# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Portable health checks for a supervise installation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from typing import Callable


@dataclass(frozen=True)
class Check:
    """The result of one doctor check."""

    name: str
    status: str
    detail: str


def _root_path(kind: str) -> Path:
    """Resolve a state or data root using the documented environment precedence."""
    specific = "SUPERVISE_" + kind.upper()
    configured = os.environ.get(specific)
    if configured:
        return Path(configured).expanduser()

    xdg = os.environ.get("XDG_" + kind.upper() + "_HOME")
    if xdg:
        return Path(xdg).expanduser() / "supervise"

    home = Path.home()
    if kind == "state":
        return home / ".local" / "state" / "supervise"
    return home / ".local" / "share" / "supervise"


def _root_check(name: str, kind: str) -> Check:
    try:
        root = _root_path(kind)
        if not root.exists() and not root.is_symlink():
            return Check(name, "UNKNOWN", "Root will be created private on first use.")
        if not root.is_dir():
            return Check(name, "FAIL", "Root is not a directory.")
        mode = stat.S_IMODE(root.stat().st_mode)
        if mode != 0o700:
            return Check(name, "FAIL", "Root permissions must be private (0700).")
        with tempfile.NamedTemporaryFile(prefix=".doctor-", dir=root):
            pass
    except (OSError, RuntimeError, ValueError):
        return Check(name, "FAIL", "Root must be writable and private (0700).")
    return Check(name, "PASS", "Root is writable and private (0700).")


def _python_check() -> Check:
    if sys.version_info >= (3, 12):
        return Check("python", "PASS", "Python 3.12 or newer is available.")
    return Check("python", "FAIL", "Python 3.12 or newer is required.")


def _bubblewrap_check(which: Callable[..., str | None]) -> Check:
    try:
        executable = which("bwrap")
    except Exception:
        executable = None
    if not executable:
        return Check("bubblewrap", "FAIL", "bwrap is not available on PATH.")

    command = [executable, "--unshare-user", "--unshare-pid", "--unshare-net",
               "--ro-bind", "/", "/", "--", sys.executable, "-c", "pass"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return Check("bubblewrap", "FAIL", "bwrap could not create the required namespaces.")
    if result.returncode == 0:
        return Check("bubblewrap", "PASS", "bwrap created user, pid, and network namespaces.")
    return Check("bubblewrap", "FAIL", "bwrap could not create the required namespaces.")


def _load_config(config_path: str | os.PathLike[str] | None):
    if config_path is None:
        return None
    try:
        with Path(config_path).open("rb") as source:
            config = tomllib.load(source)
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError):
        return None

    routes = config.get("route") if isinstance(config, dict) else None
    crew = config.get("crew") if isinstance(config, dict) else None
    if not isinstance(routes, list) or not isinstance(crew, dict):
        return None

    route_map = {}
    harnesses = set()
    for route in routes:
        if not isinstance(route, dict):
            return None
        route_id = route.get("id")
        family = route.get("family")
        harness = route.get("harness")
        if not isinstance(route_id, str) or not route_id or route_id in route_map:
            return None
        if not isinstance(family, str) or not family:
            return None
        # The simple backend's configured harnesses are the two supported CLIs.
        # Keeping this closed also prevents arbitrary config contents appearing in
        # diagnostic check names or messages.
        if harness not in ("codex", "claude"):
            return None
        route_map[route_id] = family
        harnesses.add(harness)

    authors = crew.get("authors")
    reviewers = crew.get("reviewers")
    if (not isinstance(authors, list) or not isinstance(reviewers, list)
            or any(not isinstance(value, str) for value in authors + reviewers)):
        return None
    return route_map, harnesses, authors, reviewers


def _harness_checks(parsed, which: Callable[..., str | None]) -> list[Check]:
    if parsed is None:
        return [Check("harness", "UNKNOWN", "No readable, valid backend configuration was supplied.")]
    _, harnesses, _, _ = parsed
    if not harnesses:
        return [Check("harness", "UNKNOWN", "No configured harness executable can be checked.")]

    missing = []
    for executable in sorted(harnesses):
        try:
            found = which(executable)
        except Exception:
            found = None
        if not found:
            missing.append(executable)
    if missing:
        return [Check("harness", "FAIL", "Harness executable(s) unavailable: " + ", ".join(missing) + ".")]
    return [Check("harness", "PASS", "Configured harness executables are available on PATH.")]


def _independence_check(parsed) -> Check:
    if parsed is None:
        return Check("independence", "UNKNOWN", "No readable, valid backend configuration was supplied.")
    route_map, _, authors, reviewers = parsed
    feasible = any(
        author in route_map and reviewer in route_map
        and route_map[author] != route_map[reviewer]
        for author in authors for reviewer in reviewers
    )
    if feasible:
        return Check("independence", "PASS", "Configured author and reviewer crews can use distinct families.")
    return Check("independence", "FAIL", "No configured author/reviewer pair has distinct families.")


def _manager_executable() -> str:
    configured = os.environ.get("SUPERVISE_OMP")
    candidate = configured if configured else "omp"
    resolved = shutil.which(candidate)
    if resolved is None and os.path.dirname(candidate):
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            resolved = str(path.resolve())
    if not resolved:
        raise FileNotFoundError("OMP manager was not found")
    return resolved


def _assistant_manager_reply(event) -> bool:
    if not isinstance(event, dict) or event.get("type") != "message_end":
        return False
    message = event.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    content = message.get("content")
    if isinstance(content, str):
        return "MANAGER-OK" in content
    if isinstance(content, list):
        return any(isinstance(item, dict) and item.get("type") == "text"
                   and isinstance(item.get("text"), str) and "MANAGER-OK" in item["text"]
                   for item in content)
    text = message.get("text")
    return isinstance(text, str) and "MANAGER-OK" in text


def probe_manager(model: str | None = None) -> bool:
    """Run one silent OMP JSON turn and accept only an assistant success reply."""
    try:
        argv = [_manager_executable(), "-p", "--mode", "json", "--no-session",
                "--no-tools", "--no-extensions"]
        if model is not None:
            argv.extend(["--model", str(model)])
        argv.append("Reply with exactly: MANAGER-OK")
        result = subprocess.run(argv, capture_output=True, text=True, timeout=180, check=False)
        if result.returncode != 0:
            return False
        output = result.stdout
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return False

    parsed = []
    try:
        parsed.append(json.loads(output))
    except (ValueError, TypeError):
        for line in output.splitlines():
            try:
                parsed.append(json.loads(line))
            except (ValueError, TypeError):
                continue
    for value in parsed:
        events = value if isinstance(value, list) else [value]
        for event in events:
            if _assistant_manager_reply(event):
                return True
    return False


def _manager_check(manager_probe: Callable[[], object] | None) -> Check:
    if manager_probe is None:
        return Check("manager", "UNKNOWN", "No manager probe was supplied.")
    try:
        succeeded = bool(manager_probe())
    except Exception:
        succeeded = False
    if succeeded:
        return Check("manager", "PASS", "The supplied manager probe succeeded.")
    return Check("manager", "FAIL", "The supplied manager probe failed.")


def run_checks(*, config_path: str | os.PathLike[str] | None = None,
               which: Callable[..., str | None] = shutil.which,
               manager_probe: Callable[[], object] | None = None) -> list[Check]:
    """Run portable environment, roots, and configured-backend checks.

    Diagnostics intentionally avoid command output, config contents, and exception
    text because these may contain credentials.
    """
    try:
        parsed = _load_config(config_path)
    except Exception:
        # Injected path-like objects should not prevent the rest of the checks.
        parsed = None

    checks = [
        _python_check(),
        _bubblewrap_check(which),
        _root_check("state-root", "state"),
        _root_check("data-root", "data"),
        *_harness_checks(parsed, which),
        _independence_check(parsed),
        _manager_check(manager_probe),
    ]
    return checks
