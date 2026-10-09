# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Claude Code CLI adapter for the simple backend."""

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
class ClaudeRoute(Route):
    """Route metadata carrying the optional configured effort value."""

    effort: str | None = None


def make_route(route_id, *, model, family, roles, effort, price):
    """Create an isolated Anthropic Claude Code route."""
    return ClaudeRoute(
        route_id=route_id,
        harness="claude",
        vendor="anthropic",
        model=model,
        family=family,
        roles=frozenset(roles),
        review_isolation=True,
        price=price,
        effort=effort,
    )


def prepare_launch(request: LaunchRequest, *, executable, config_dir) -> LaunchSpec:
    """Describe a restricted Claude invocation without starting a process."""
    route = request.route
    is_review = request.role == "review"
    executable_path = Path(os.fspath(executable)).resolve(strict=False)
    config_path = Path(os.fspath(config_dir))

    mounts: tuple[Mount, ...] = ()
    if is_review:
        runtime_path = executable_path.parent.resolve(strict=False)
        runtime_target = str(runtime_path)
        home_path_resolved = Path.home().resolve(strict=False)
        safe_runtime = (
            runtime_path != Path(runtime_path.anchor)
            and runtime_path != home_path_resolved
            and runtime_path not in home_path_resolved.parents
        )
        config_target = _SANDBOX_HOME + "/.claude"
        argv_executable = os.fspath(executable_path)
        home_value = _SANDBOX_HOME
        config_value = config_target
        if safe_runtime and Path("/review") not in (
            runtime_path, *runtime_path.parents
        ):
            mounts = (
                Mount(source=runtime_target, target=runtime_target, writable=False),
            )
        mounts += (
            Mount(
                source=str(config_path.absolute()),
                target=config_target,
                writable=True,
            ),
        )
    else:
        argv_executable = os.fspath(executable_path)
        home_value = str(config_path.absolute())
        config_value = str(config_path.absolute())

    argv = [
        argv_executable,
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        route.model,
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--setting-sources",
        "",
        "--disable-slash-commands",
    ]
    if is_review:
        argv.extend(("--tools", "Read,Grep,Glob"))
    else:
        argv.extend(("--permission-mode", "acceptEdits"))
        grants = ["Bash(" + " ".join(command) + ")" for command in request.worker_commands]
        if grants:
            argv.append("--allowedTools")
            argv.extend(grants)

    return LaunchSpec(
        argv=tuple(argv),
        env={"HOME": home_value, "PATH": os.defpath, "CLAUDE_CONFIG_DIR": config_value},
        requires_review_sandbox=is_review,
        stdout_format="claude-json",
        mounts=mounts,
    )


def interpret(
    spec: LaunchSpec, capture: ProcessCapture, *, route: Route
) -> AttemptOutcome:
    """Interpret Claude's recorded stream without fabricating absent facts."""
    notes: list[str] = []
    final_text = ""
    served_model = "unknown"
    usage: dict[str, int] | str = "unknown"
    reported_cost: float | None = None
    saw_error = False

    try:
        output = capture.stdout_path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        output = ""
        notes.append(f"Could not read Claude output: {error.__class__.__name__}.")

    usage_fields = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )
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

        event_type = event.get("type")
        if event_type == "error":
            saw_error = True
        if event_type == "system":
            model = event.get("model")
            if isinstance(model, str) and model:
                served_model = model
        if event_type == "result":
            result = event.get("result")
            final_text = result if isinstance(result, str) else ""
            if event.get("is_error") is True:
                saw_error = True

            raw_cost = event.get("total_cost_usd")
            if (
                isinstance(raw_cost, (int, float))
                and not isinstance(raw_cost, bool)
                and math.isfinite(raw_cost)
                and raw_cost >= 0
            ):
                reported_cost = float(raw_cost)
            else:
                reported_cost = None

            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                parsed_usage = {
                    key: value
                    for key in usage_fields
                    if isinstance((value := raw_usage.get(key)), int)
                    and not isinstance(value, bool)
                    and value >= 0
                }
                usage = parsed_usage if parsed_usage else "unknown"
            else:
                usage = "unknown"

    if not final_text:
        notes.append("Claude did not report final result text.")
    if served_model == "unknown":
        notes.append("Claude did not report a served model.")
    if usage == "unknown":
        notes.append("Claude did not report usable result usage.")

    cost: float | str = "unknown"
    cost_basis = "unknown"
    if reported_cost is not None:
        cost = reported_cost
        cost_basis = "harness_reported"
    else:
        price = getattr(route, "price", "unknown")
        if isinstance(price, Mapping) and isinstance(usage, dict):
            input_price = price.get("input_per_mtok")
            output_price = price.get("output_per_mtok")
            valid_prices = (
                isinstance(input_price, (int, float))
                and not isinstance(input_price, bool)
                and math.isfinite(input_price)
                and input_price >= 0
                and isinstance(output_price, (int, float))
                and not isinstance(output_price, bool)
                and math.isfinite(output_price)
                and output_price >= 0
            )
            required_usage = {"input_tokens", "output_tokens"}
            valid_usage = required_usage.issubset(usage)
            cache_keys = ("cache_creation_input_tokens", "cache_read_input_tokens")
            valid_cache = all(
                key not in usage or (isinstance(usage[key], int) and usage[key] >= 0)
                for key in cache_keys
            )
            if valid_prices and valid_usage and valid_cache:
                input_tokens = usage["input_tokens"] + sum(
                    usage.get(key, 0) for key in cache_keys
                )
                cost = (
                    input_tokens * input_price + usage["output_tokens"] * output_price
                ) / 1_000_000
                cost_basis = "list_price_estimate"
    if cost == "unknown":
        notes.append("Cost is unknown because reported cost and usable declared prices are unavailable.")

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
        served_model=served_model,
        usage=usage,
        cost_usd=cost,
        cost_basis=cost_basis,
        failure_class=failure,
        notes=tuple(notes),
    )


def check_grants(commands: Sequence[Sequence[str]]) -> None:
    """Accept only absolute, literal commands that can be expressed as Bash rules."""
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
    """Refuse project-level Claude settings and MCP configuration files."""
    root = Path(project)
    if (root / ".mcp.json").exists():
        raise ProjectConfigRefused("project Claude harness configuration is not permitted")
    settings_dir = root / ".claude"
    if settings_dir.is_dir():
        try:
            if any(path.is_file() for path in settings_dir.glob("settings*.json")):
                raise ProjectConfigRefused("project Claude harness configuration is not permitted")
        except OSError as error:
            raise ProjectConfigRefused("project configuration could not be checked") from error

    try:
        for path in root.rglob(".mcp.json"):
            if path.exists():
                raise ProjectConfigRefused("project Claude harness configuration is not permitted")
        for directory in root.rglob(".claude"):
            if directory.is_dir() and any(
                path.is_file() for path in directory.glob("settings*.json")
            ):
                raise ProjectConfigRefused("project Claude harness configuration is not permitted")
    except OSError as error:
        raise ProjectConfigRefused("project configuration could not be checked") from error
