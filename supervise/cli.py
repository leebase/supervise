# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Command-line interface for portable supervise."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any

from supervise import doctor, installer, verify, transport
from supervise.backends.simple import SimpleBackend
from supervise.backends.simple import claude, codex
from supervise.controller import admission
from supervise.controller.transaction import Controller
from supervise.runner import BOOTSTRAP, run as run_invocation


def _data_root() -> Path:
    configured = os.environ.get("SUPERVISE_DATA")
    if configured:
        return Path(configured).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "supervise"
    return Path.home() / ".local" / "share" / "supervise"


def _config_path() -> Path:
    configured = os.environ.get("SUPERVISE_CONFIG")
    if configured:
        return Path(configured).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "supervise" / "backend.toml"
    return Path.home() / ".config" / "supervise" / "backend.toml"


def installed_publication_sha256() -> str:
    """Read the publication digest recorded for the selected data root."""
    path = _data_root() / "INSTALLATION.json"
    try:
        with path.open("r", encoding="utf-8") as stream:
            record = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("no valid installation record is available") from error
    if not isinstance(record, dict):
        raise ValueError("installation record is not an object")
    digest = record.get("publication_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("installation record has no valid publication_sha256")
    return digest


class _ConfiguredBackend:
    """Bind the validated simple route catalog to its stdlib harness adapters."""

    def __init__(self, config: SimpleBackend):
        self.config = config
        self._launch_routes = {}

    @property
    def require_review_fallback(self) -> bool:
        return self.config.require_review_fallback

    def identity(self):
        return self.config.identity()

    def plan_crew(self, request):
        return self.config.plan_crew(request)

    @staticmethod
    def _adapter(route):
        if route.harness == "codex":
            return codex
        if route.harness == "claude":
            return claude
        raise ValueError("unsupported configured harness")

    def check_grants(self, route, commands):
        self._adapter(route).check_grants(commands)

    def preflight_project(self, route, project):
        self._adapter(route).preflight_project(project)

    def prepare_launch(self, request):
        adapter = self._adapter(request.route)
        if adapter is codex:
            executable = shutil.which("codex")
            if not executable:
                raise ValueError("configured Codex executable is unavailable")
            home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
            spec = adapter.prepare_launch(request, executable=executable, codex_home=home)
            self._launch_routes[id(spec)] = request.route
            return spec
        executable = shutil.which("claude")
        if not executable:
            raise ValueError("configured Claude executable is unavailable")
        config_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
        spec = adapter.prepare_launch(request, executable=executable, config_dir=config_dir)
        self._launch_routes[id(spec)] = request.route
        return spec

    def interpret(self, spec, capture):
        route = self._launch_routes.get(id(spec))
        if route is None:
            raise ValueError("launch specification is not bound to a configured route")
        if route.harness == "codex":
            return codex.interpret(spec, capture, route=route)
        if route.harness == "claude":
            return claude.interpret(spec, capture, route=route)
        raise ValueError("unknown configured harness output format")


def make_controller(home: str | os.PathLike[str]) -> Controller:
    """Create a controller bound to the installed publication and user config."""
    backend = _ConfiguredBackend(SimpleBackend(_config_path()))
    return Controller(home, backend=backend,
                      publication_sha256=installed_publication_sha256())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="supervise", description="Portable supervision tools")
    commands = parser.add_subparsers(dest="command")

    begin = commands.add_parser("begin", help="create or reuse a private invocation")
    begin.add_argument("plan")
    begin.add_argument("--project")

    intent = commands.add_parser("intent", help="submit a typed intent to an invocation")
    intent.add_argument("invocation_home")
    intent.add_argument("json_intent")

    verification = commands.add_parser("verify", help="verify a completed invocation")
    verification.add_argument("invocation_home")

    health = commands.add_parser("doctor", help="check the local supervise environment")
    health.add_argument("--config", dest="config_path")
    health.add_argument("--probe-manager", action="store_true")
    health.add_argument("--model")

    install = commands.add_parser("install", help="install a supervise release")
    install.add_argument("--source", required=True)
    install.add_argument("--data-root")
    install.add_argument("--pointer")
    install.add_argument("--release")

    remove = commands.add_parser("uninstall", help="restore the prior installation pointer")
    remove.add_argument("--data-root")
    remove.add_argument("--pointer")

    run = commands.add_parser("run", help="run a supervised plan invocation")
    run.add_argument("plan")
    run.add_argument("--max-seconds", type=float, default=7200.0)
    run.add_argument("--project")
    run.add_argument("--model")
    return parser


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _data_root_argument(value: str | None) -> str:
    return str(Path(value).expanduser() if value is not None else _data_root())


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError("invalid JSON constant: " + value)


def _report_error(error: BaseException) -> int:
    print("supervise: " + str(error), file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(sys.stderr)
        print("supervise: a subcommand is required", file=sys.stderr)
        return 2

    if args.command == "begin":
        try:
            project = Path(args.project).expanduser().resolve() if args.project else Path.cwd().resolve()
            plan = Path(args.plan).expanduser().resolve()
            if not plan.is_file():
                raise ValueError("plan must name an existing regular file")
            state_root = transport.state_root()
            state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
            home = transport.invocation_path(state_root, project, plan)
            pending = admission.initialize(home, project=project, plan_path=plan)
            with (home / "state.json").open("r", encoding="utf-8") as stream:
                state = json.load(stream)
            result = {
                "home": str(home),
                "invocation_id": pending["invocation_id"],
                "status": state["status"],
                "version": state["version"],
                "bootstrap": BOOTSTRAP.format(
                    invocation_id=pending["invocation_id"], version=state["version"],
                    plan=str(plan), project=str(project)),
            }
            _json_print(result)
            return 0
        except (ValueError, TypeError, OSError, KeyError, json.JSONDecodeError) as error:
            return _report_error(error)

    if args.command == "intent":
        try:
            intent = json.loads(args.json_intent, object_pairs_hook=_json_object,
                                parse_constant=_bad_constant)
            if not isinstance(intent, dict):
                raise ValueError("intent JSON must be an object")
            home = Path(args.invocation_home)
            with (home / "pending.json").open("r", encoding="utf-8") as stream:
                pending = json.load(stream)
            supplied_id = intent.get("invocation_id")
            if "invocation_id" in intent and supplied_id != pending.get("invocation_id"):
                raise ValueError("invocation identity does not match pending invocation")
            if intent.get("action") == "admit":
                if supplied_id != pending.get("invocation_id"):
                    raise ValueError("invocation identity does not match pending invocation")
                with (home / "state.json").open("r", encoding="utf-8") as stream:
                    state = json.load(stream)
                expected = intent.get("expected_version")
                if type(expected) is not int or expected != state.get("version"):
                    raise ValueError("expected version does not match invocation state")
                backend = _ConfiguredBackend(SimpleBackend(_config_path()))
                result = admission.admit(home, intent, backend=backend)
            else:
                result = make_controller(home).perform(intent)
            _json_print(result)
            return 0
        except (ValueError, TypeError, OSError, KeyError, json.JSONDecodeError) as error:
            return _report_error(error)

    if args.command == "verify":
        try:
            result = verify.verify_invocation(args.invocation_home)
        except Exception as error:
            result = {"verdict": "FAIL", "checks": [], "error": error.__class__.__name__}
        _json_print(result)
        return 0 if isinstance(result, dict) and result.get("verdict") == "PASS" else 1

    if args.command == "doctor":
        try:
            config_path = args.config_path if args.config_path is not None else _config_path()
            manager_probe = (lambda: doctor.probe_manager(args.model)) if args.probe_manager else None
            checks = doctor.run_checks(config_path=config_path, manager_probe=manager_probe)
            normalized = []
            for check in checks:
                if isinstance(check, dict):
                    normalized.append(check)
                else:
                    normalized.append({"name": check.name, "status": check.status,
                                       "detail": check.detail})
            statuses = [str(check.get("status", "")).upper() for check in normalized]
            failed = any(status == "FAIL" for status in statuses)
            if failed:
                verdict = "FAIL"
            elif statuses and all(status == "PASS" for status in statuses):
                verdict = "PASS"
            else:
                verdict = "UNKNOWN"
            _json_print({"verdict": verdict, "checks": normalized})
            return 1 if failed else 0
        except Exception as error:
            _json_print({"verdict": "FAIL", "checks": [], "error": error.__class__.__name__})
            return 1

    if args.command == "install":
        try:
            result = installer.install(args.source, data_root=_data_root_argument(args.data_root),
                                       pointer_path=args.pointer, release=args.release)
            _json_print(result)
            return 0
        except (installer.InstallError, OSError, ValueError) as error:
            return _report_error(error)

    if args.command == "uninstall":
        try:
            result = installer.uninstall(_data_root_argument(args.data_root),
                                         pointer_path=args.pointer)
            _json_print(result)
            return 0
        except (installer.InstallError, OSError, ValueError) as error:
            return _report_error(error)

    if args.command == "run":
        seconds = args.max_seconds
        if not math.isfinite(seconds) or seconds <= 0 or seconds > 7200:
            return _report_error(ValueError("--max-seconds must be finite, positive, and at most 7200"))
        project = Path(args.project).expanduser().resolve() if args.project else Path.cwd().resolve()
        try:
            return run_invocation(args.plan, project=project, max_seconds=seconds, model=args.model)
        except (ValueError, TypeError, OSError) as error:
            return _report_error(error)

    parser.error("unknown subcommand")
    return 2
