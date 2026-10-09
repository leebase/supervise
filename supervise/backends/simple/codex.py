# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Codex CLI adapter for the simple backend.

The Codex CLI has no command allowlist. Literal grants are validated, but
enforcement is the workspace sandbox: writes are confined to the workdir and
network access is disabled. The controller's project-confinement guard detects
writes outside the owned paths.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from supervise.backend import (
    AttemptOutcome,
    LaunchRequest,
    LaunchSpec,
    Mount,
    ProcessCapture,
    ProjectConfigRefused,
    Route,
    UnsupportedGrant,
)


_SANDBOX_HOME = "/sandbox/home"


@dataclass(frozen=True)
class CodexRoute(Route):
    """Route metadata with the Codex-specific reasoning setting."""

    effort: str = "medium"


def make_route(route_id, *, model, family, roles, effort, price):
    """Create an isolated OpenAI Codex route."""
    return CodexRoute(
        route_id=route_id,
        harness="codex",
        vendor="openai",
        model=model,
        family=family,
        roles=frozenset(roles),
        review_isolation=True,
        price=price,
        effort=effort,
    )


def prepare_launch(request: LaunchRequest, *, executable, codex_home) -> LaunchSpec:
    """Describe a pinned Codex invocation without starting a process."""
    route = request.route
    role = request.role
    executable_path = Path(os.fspath(executable)).resolve(strict=False)
    original_executable = os.fspath(executable_path)
    home_path = Path(os.fspath(codex_home))
    is_review = role == "review"

    mounts: tuple[Mount, ...] = ()
    codex_home_value = str(home_path)
    home_value = str(home_path)
    path_value = os.defpath
    if is_review:
        runtime_path = executable_path.parent
        if runtime_path.name == "bin":
            package_root = runtime_path.parent
            if (
                (package_root / "codex-package.json").is_file()
                or (
                    (package_root / "codex-path").is_dir()
                    and (package_root / "codex-resources").is_dir()
                )
            ):
                runtime_path = package_root
        runtime_path = runtime_path.resolve(strict=False)
        runtime_source = str(runtime_path)
        home_path_resolved = Path.home().resolve(strict=False)
        safe_runtime = (
            runtime_path != Path(runtime_path.anchor)
            and runtime_path != home_path_resolved
            and runtime_path not in home_path_resolved.parents
        )
        codex_home_value = _SANDBOX_HOME + "/.codex"
        home_value = _SANDBOX_HOME
        argv_executable = original_executable
        if safe_runtime and Path("/review") not in (
            runtime_path, *runtime_path.parents
        ):
            mounts = (
                Mount(source=runtime_source, target=runtime_source, writable=False),
            )
        mounts += (
            Mount(source=str(home_path.absolute()), target=codex_home_value, writable=True),
        )
        working_directory = "/review"
        sandbox = "read-only"
    else:
        argv_executable = original_executable
        path_value = str(executable_path.parent) + os.pathsep + os.defpath
        working_directory = str(request.workdir)
        sandbox = "workspace-write"

    effort = getattr(route, "effort", "medium")
    argv = [
        argv_executable,
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "-m",
        route.model,
        "-C",
        working_directory,
        "-s",
        sandbox,
        "-c",
        'approval_policy="never"',
    ]
    if is_review:
        argv.extend(("-c", 'sandbox_permissions.filesystem="read"'))
        argv.extend(("-c", "sandbox_permissions.network.enabled=false"))
    else:
        argv.extend(("-c", "sandbox_workspace_write.network_access=false"))
    argv.extend(("-c", f'model_reasoning_effort="{effort}"', "-"))

    return LaunchSpec(
        argv=tuple(argv),
        env={"HOME": home_value, "PATH": path_value, "CODEX_HOME": codex_home_value},
        requires_review_sandbox=is_review,
        stdout_format="codex-json",
        mounts=mounts,
    )


def interpret(spec: LaunchSpec, capture: ProcessCapture, *, route: Route) -> AttemptOutcome:
    """Interpret Codex's JSONL event stream without inventing missing facts."""
    notes: list[str] = []
    final_text = ""
    usage: dict[str, int] | str = "unknown"
    saw_error = False

    try:
        output = capture.stdout_path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        output = ""
        notes.append(f"Could not read Codex output: {error.__class__.__name__}.")

    for line_number, line in enumerate(output.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            notes.append(f"Ignored malformed JSONL event on line {line_number}.")
            continue
        if not isinstance(event, dict):
            notes.append(f"Ignored non-object JSONL event on line {line_number}.")
            continue
        if event.get("type") == "error":
            saw_error = True
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    final_text = text
        if event.get("type") == "turn.completed":
            reported = event.get("usage")
            if isinstance(reported, dict):
                parsed = {}
                for key in (
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                ):
                    value = reported.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        parsed[key] = value
                usage = parsed if parsed else "unknown"
            else:
                usage = "unknown"

    if not final_text:
        notes.append("Codex did not report a final agent message.")
    if usage == "unknown":
        notes.append("Codex did not report usable turn usage.")

    cost: float | str = "unknown"
    cost_basis = "unknown"
    price = getattr(route, "price", "unknown")
    if (
        isinstance(price, Mapping)
        and isinstance(usage, dict)
        and "input_tokens" in usage
        and "output_tokens" in usage
    ):
        input_price = price.get("input_per_mtok")
        output_price = price.get("output_per_mtok")
        if (
            isinstance(input_price, (int, float))
            and not isinstance(input_price, bool)
            and isinstance(output_price, (int, float))
            and not isinstance(output_price, bool)
            and math.isfinite(input_price)
            and math.isfinite(output_price)
            and input_price >= 0
            and output_price >= 0
        ):
            cost = (usage["input_tokens"] * input_price + usage["output_tokens"] * output_price) / 1_000_000
            cost_basis = "list_price_estimate"
    if cost == "unknown":
        notes.append("Cost is unknown because complete usage and declared prices are unavailable.")

    if capture.timed_out:
        failure = "platform_timeout"
    elif saw_error:
        failure = "platform_env"
    elif capture.exit_code is not None and capture.exit_code != 0:
        failure = "worker"
    else:
        failure = "none"

    return AttemptOutcome(
        final_text=final_text,
        served_model="unknown",
        usage=usage,
        cost_usd=cost,
        cost_basis=cost_basis,
        failure_class=failure,
        notes=tuple(notes),
    )


def check_grants(commands: Sequence[Sequence[str]]) -> None:
    """Validate literal grants with an absolute executable path.

    The Codex CLI has no command allowlist. Enforcement is the workspace sandbox
    (writes confined to the workdir, network disabled); the controller's
    project-confinement guard detects writes outside the owned paths.
    """
    forbidden = ("*", "?", "(", ")", "\n", "\r", "\x00")
    try:
        for command in commands:
            if not isinstance(command, (list, tuple)) or not command:
                raise UnsupportedGrant("worker grants must be non-empty argument lists")
            if any(not isinstance(argument, str) for argument in command):
                raise UnsupportedGrant("worker grant arguments must be strings")
            if not os.path.isabs(command[0]):
                raise UnsupportedGrant("worker grant executable must be an absolute path")
            if any(any(marker in argument for marker in forbidden) for argument in command):
                raise UnsupportedGrant("worker grants must be literal arguments")
    except TypeError as error:
        raise UnsupportedGrant("worker grants must be a sequence of commands") from error


def preflight_project(project: str | os.PathLike[str]) -> None:
    """Refuse project-level Codex harness configuration and overrides."""
    root = Path(project)
    if (root / ".codex").exists() or (root / "AGENTS.override.md").exists():
        raise ProjectConfigRefused("project Codex harness configuration is not permitted")
    try:
        for path in root.rglob("AGENTS.override.md"):
            if path.exists():
                raise ProjectConfigRefused("project Codex harness configuration is not permitted")
        for path in root.rglob(".codex"):
            if path.exists():
                raise ProjectConfigRefused("project Codex harness configuration is not permitted")
    except OSError as error:
        raise ProjectConfigRefused("project configuration could not be checked") from error
