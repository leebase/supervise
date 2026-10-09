# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Private invocation admission and immutable evidence-contract creation."""

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import time
import uuid

from supervise.controller import classify, evidence


UNSUPPORTED = ("ssh", "browser", "external-service", "protected-configuration")


class AdmissionRefused(ValueError):
    """The submitted assignment cannot safely be admitted."""


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _absolute(path):
    return Path(os.path.abspath(os.fspath(path)))


def _within(parent, child):
    return child == parent or parent in child.parents


def _timestamp(value=None):
    if value is None:
        value = time.time()
    elif callable(value):
        value = value()
    if hasattr(value, "timestamp") and callable(value.timestamp):
        value = value.timestamp()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("timestamp must be finite")
    return value


def _store(path, value, mode=0o600, *, exclusive=False):
    path = Path(path)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, mode)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _read(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _state(home, status, version=0, **extra):
    value = {"status": status, "version": version}
    value.update(extra)
    _store(Path(home) / "state.json", value, 0o600)
    return value


def _project_home(project, home):
    project = _absolute(project)
    home = _absolute(home)
    if project.resolve() != project or not project.is_dir():
        raise ValueError("project must be a real directory")
    if home.resolve() != home or _within(project, home) or _within(home, project):
        raise ValueError("home must be private and outside the project")
    return project, home


def initialize(home, *, project, plan_path, now=None) -> dict:
    """Create a private invocation directory and write its pending record once."""
    project, home = _project_home(project, home)
    plan = _absolute(plan_path)
    if plan.resolve() != plan or not plan.is_file():
        raise ValueError("plan_path must name a regular, non-symlink file")

    existed = home.exists()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    if home.is_symlink() or not home.is_dir():
        raise ValueError("home must be a real private directory")
    if not existed:
        os.chmod(home, 0o700)
    if stat.S_IMODE(home.stat().st_mode) != 0o700:
        raise ValueError("home must have private mode 0700")

    pending_path = home / "pending.json"
    if pending_path.is_symlink():
        raise ValueError("pending record must not be a symlink")
    if pending_path.exists():
        return _read(pending_path)

    pending = {
        "invocation_id": str(uuid.uuid4()),
        "cwd": str(project),
        "plan_path": str(plan),
        "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
        "created_at": _timestamp(now),
    }
    try:
        _store(pending_path, pending, 0o400, exclusive=True)
    except FileExistsError:
        return _read(pending_path)
    if not (home / "state.json").exists():
        _state(home, "INITIALIZED", 0)
    return pending


def _resolve_interpreter(value, base=None):
    if not isinstance(value, str) or not value:
        raise ValueError("tests interpreter is missing")
    candidate = Path(value)
    if candidate.is_absolute():
        # Do not rewrite an absolute interpreter argument, even if it names a
        # symlink or uses a non-canonical but valid absolute spelling.
        absolute = value
    elif "/" in value or "\\" in value:
        absolute = os.path.abspath((base or Path.cwd()) / candidate)
    else:
        found = shutil.which(value)
        if found is None:
            raise ValueError("tests interpreter cannot be resolved")
        absolute = os.path.abspath(found)
    # Keep symlink paths (including virtualenv and system interpreters) intact;
    # these checks follow the path only to confirm it names an executable.
    executable = Path(absolute)
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("tests interpreter cannot be resolved to an executable")
    return absolute


def _normalized_intent(intent, project):
    if not isinstance(intent, dict):
        raise ValueError("intent must be an object")
    try:
        value = json.loads(json.dumps(intent, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("intent must contain finite JSON values") from exc
    tests = value.get("tests")
    if isinstance(tests, dict):
        argv = tests.get("argv")
        if isinstance(argv, list) and argv and isinstance(argv[0], str):
            try:
                argv[0] = _resolve_interpreter(argv[0], project)
            except ValueError:
                # Preserve malformed inputs for a stable named refusal during validation.
                pass
    return value


def _intent_digest(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _path_list(project, owned_paths):
    if not isinstance(owned_paths, list) or not owned_paths:
        raise ValueError("owned paths must be a non-empty list")
    seen = set()
    normalized = []
    for item in owned_paths:
        if not isinstance(item, str) or not item or "\\" in item or "\x00" in item:
            raise ValueError("owned paths must be relative paths inside the project")
        relative = PurePosixPath(item)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError("owned paths must be relative paths inside the project")
        if relative.parts[0] == "tests":
            raise ValueError("owned paths must not include tests/")
        path = project.joinpath(*relative.parts)
        if path.resolve() != path or not _within(project, path):
            raise ValueError("owned paths must stay inside the project without symlinks")
        normalized_name = relative.as_posix()
        if normalized_name in seen:
            raise ValueError("owned paths must be unique")
        seen.add(normalized_name)
        normalized.append(normalized_name)
    return normalized


def _acceptance(items):
    if not isinstance(items, list) or not items:
        raise ValueError("acceptance must be a non-empty list")
    identifiers = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("acceptance records must be objects")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            raise ValueError("acceptance IDs must be non-empty strings")
        if identifier == "independent-review":
            raise ValueError("independent-review is a reserved acceptance ID")
        if identifier in identifiers:
            raise ValueError("acceptance IDs must be unique")
        identifiers.add(identifier)
        if not isinstance(item.get("proposition"), str) or not item["proposition"].strip():
            raise ValueError("acceptance proposition is required")
        selectors = item.get("selectors")
        if not isinstance(selectors, list) or not selectors or any(
                not isinstance(selector, str) or not selector.strip() for selector in selectors):
            raise ValueError("selector list must be non-empty")
    return identifiers


def _tests(value, project):
    if not isinstance(value, dict):
        raise ValueError("tests specification is required")
    tests = dict(value)
    argv = tests.get("argv")
    if not isinstance(argv, list) or not argv:
        raise ValueError("tests argv is required")
    argv = list(argv)
    argv[0] = _resolve_interpreter(argv[0], project)
    tests["argv"] = argv
    try:
        evidence.validate_tests(tests)
    except (evidence.EvidenceError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("tests: " + str(exc)) from exc
    if tests.get("baseline_policy") not in ("nonregression", "all-green"):
        raise ValueError("baseline policy must be nonregression or all-green")
    expected = tests.get("expected_baseline_failures")
    if not isinstance(expected, list) or any(not isinstance(item, str) or not item for item in expected):
        raise ValueError("baseline expected failures must be a list of test IDs")
    if len(set(expected)) != len(expected):
        raise ValueError("baseline expected failures must be unique")
    tests["expected_baseline_failures"] = sorted(expected)
    return tests


def _checkpoint(value, tests, worker_timeout):
    if not isinstance(value, dict):
        raise ValueError("checkpoint must be an object")
    starts = value.get("max_starts")
    maximum = value.get("max_seconds")
    first = value.get("first_value_seconds")
    if isinstance(starts, bool) or not isinstance(starts, int) or starts <= 0:
        raise ValueError("checkpoint max_starts must be a positive integer")
    for label, number in (("max_seconds", maximum), ("first_value_seconds", first),
                          ("worker timeout", worker_timeout)):
        if (isinstance(number, bool) or not isinstance(number, (int, float))
                or not math.isfinite(number) or number <= 0):
            raise ValueError("checkpoint has an invalid " + label)
    test_timeout = tests.get("timeout_seconds")
    if test_timeout + worker_timeout > maximum:
        raise ValueError("timeouts do not fit the checkpoint")
    return {"max_starts": starts, "max_seconds": maximum,
            "first_value_seconds": first}


def _packet(value, owned_paths):
    if not isinstance(value, dict) or not isinstance(value.get("text"), str):
        raise ValueError("packet text is required")
    text = value["text"]
    labels = ("Kind", "Owned paths", "Declared size", "Oracle", "Domain", "Review")
    lines = text.splitlines()
    if len(lines) < len(labels) or any(
            not lines[index].startswith(label + ":") for index, label in enumerate(labels)):
        raise ValueError("packet must begin with its structural lines")
    try:
        task = classify.classify(text, owned_paths, role="impl")
    except (TypeError, ValueError) as exc:
        if "outside frozen ownership" in str(exc):
            raise ValueError("packet ownership is outside owned paths: " + str(exc)) from exc
        raise ValueError("packet: " + str(exc)) from exc
    return task


def _capabilities(value):
    capabilities = value.get("required_capabilities", [])
    if not isinstance(capabilities, list):
        raise ValueError("required capabilities must be a list")
    unsupported = {kind.casefold() for kind in UNSUPPORTED}
    for capability in capabilities:
        if not isinstance(capability, dict):
            raise ValueError("required capability entries must be objects")
        kind = capability.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise ValueError("required capability kind must be a non-empty string")
        normalized_kind = kind.strip().casefold()
        if normalized_kind in unsupported:
            raise ValueError("unsupported in this build: " + normalized_kind)


def _selectors(acceptance, collected_ids):
    collected = set(collected_ids)
    resolved = {}
    for item in acceptance:
        selected = set()
        for selector in item["selectors"]:
            matches = {test_id for test_id in collected
                       if test_id == selector or test_id.startswith(selector + ".")}
            if not matches:
                raise ValueError("selector does not resolve to a collected test ID: " + selector)
            selected.update(matches)
        if not selected:
            raise ValueError("selector list resolves to no collected test IDs")
        resolved[item["id"]] = sorted(selected)
    return resolved


def _backend_preflight(backend, project, tests):
    try:
        identity = backend.identity()
        routes = tuple(identity.routes)
    except Exception as exc:
        raise ValueError("preflight could not inspect configured routes: " + str(exc)) from exc
    if not routes:
        raise ValueError("preflight found no configured routes")
    for route in routes:
        try:
            backend.preflight_project(route, project)
        except Exception as exc:
            raise ValueError("preflight refused route " + str(getattr(route, "route_id", "unknown"))
                             + ": " + str(exc)) from exc
    commands = [tests["argv"]]
    author_routes = [route for route in routes if "impl" in getattr(route, "roles", ())]
    if not author_routes:
        raise ValueError("grant check found no implementation route")
    for route in author_routes:
        try:
            backend.check_grants(route, commands)
        except Exception as exc:
            raise ValueError("grant refused oracle command for route "
                             + str(getattr(route, "route_id", "unknown")) + ": " + str(exc)) from exc
    return identity


def _cleanup_freeze(home):
    home = Path(home)
    for name in ("contract.json", "baseline-observation.json", "baseline-run-observation.json",
                 "binding.json", "acceptance.json", "elapsed-checkpoint.json", "decision.json"):
        try:
            (home / name).unlink()
        except FileNotFoundError:
            pass
    for name in ("baseline", "baseline-run"):
        path = home / name
        if path.exists() and path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)


def _refuse(home, message):
    try:
        _state(home, "PRECHECK_FAILED", 0, diagnostic=str(message))
    except OSError:
        pass
    return AdmissionRefused(str(message))


def admit(home, intent, *, backend) -> dict:
    """Validate an assignment, run its baseline and freeze the admission contract."""
    home = _absolute(home)
    normalized = None
    try:
        pending_path = home / "pending.json"
        pending = _read(pending_path)
        cwd = Path(pending["cwd"])
        normalized = _normalized_intent(intent, cwd)
        digest = _intent_digest(normalized)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _refuse(home, "pending admission record is invalid: " + str(exc)) from exc

    contract_path = home / "contract.json"
    if contract_path.exists():
        try:
            frozen = _read(contract_path)
            if frozen.get("admission_intent_sha256") == digest:
                state = _read(home / "state.json")
                return {"status": "ADMITTED", "version": state.get("version", 1),
                        "contract": str(contract_path), "sha256": hashlib.sha256(
                            contract_path.read_bytes()).hexdigest()}
        except (OSError, ValueError, TypeError):
            pass
        raise AdmissionRefused("admission is immutable once frozen")

    try:
        if home.is_symlink() or not home.is_dir() or stat.S_IMODE(home.stat().st_mode) != 0o700:
            raise ValueError("private home must have mode 0700")
        if pending_path.is_symlink():
            raise ValueError("pending record must not be a symlink")
        pending_mode = stat.S_IMODE(pending_path.stat().st_mode)
        if pending_mode != 0o400:
            raise ValueError("pending record must be protected read-only")
        if not isinstance(pending, dict) or not all(key in pending for key in
                ("invocation_id", "cwd", "plan_path", "plan_sha256", "created_at")):
            raise ValueError("pending record is malformed")
        project = _absolute(pending["cwd"])
        project, checked_home = _project_home(project, home)
        if checked_home != home:
            raise ValueError("pending project/home provenance is invalid")
        plan = Path(pending["plan_path"])
        if plan.resolve() != plan or not plan.is_file():
            raise ValueError("plan is missing or unsafe")
        if hashlib.sha256(plan.read_bytes()).hexdigest() != pending["plan_sha256"]:
            raise ValueError("plan changed after initialize")

        _capabilities(normalized)
        owned_paths = _path_list(project, normalized.get("owned_paths"))
        tests_dir = project / "tests"
        if tests_dir.is_symlink() or not tests_dir.is_dir():
            raise ValueError("tests directory is required")
        acceptance = normalized.get("acceptance")
        _acceptance(acceptance)
        tests = _tests(normalized.get("tests"), project)
        checkpoint = _checkpoint(normalized.get("checkpoint"), tests,
                                 normalized.get("timeout_seconds"))
        task = _packet(normalized.get("packet"), owned_paths)

        identity = _backend_preflight(backend, project, tests)
        # The command supplied to grants is the exact, resolved oracle command.
        normalized["tests"] = tests
        normalized["owned_paths"] = owned_paths
        normalized["checkpoint"] = checkpoint

        try:
            evidence.freeze(project, home, acceptance=acceptance, tests=tests,
                            source_scopes=owned_paths, test_scopes=["tests"],
                            owned_paths=owned_paths)
            frozen = _read(contract_path)
            if frozen.get("baseline_collection_valid") is not True:
                raise ValueError("baseline collection was incomplete")
            accepted_ids = _selectors(acceptance, frozen.get("frozen_ids", []))
            baseline_dir = home / "baseline-run"
            baseline_dir.mkdir(mode=0o700)
            observation = evidence.observe(frozen, baseline_dir)
            if observation.get("complete") is not True:
                raise ValueError("baseline observation was incomplete")
            outcomes = observation.get("outcomes", {})
            observed_failures = sorted(test_id for test_id, record in outcomes.items()
                                       if record.get("outcome") in ("FAIL", "ERROR"))
            expected_failures = tests["expected_baseline_failures"]
            if tests["baseline_policy"] == "nonregression":
                if observed_failures != expected_failures:
                    raise ValueError("baseline failures do not exactly match expected failures")
                if any(record.get("outcome") not in ("PASS", "FAIL", "ERROR")
                       for record in outcomes.values()):
                    raise ValueError("baseline contains a non-green unsupported outcome")
            else:
                if expected_failures or any(record.get("outcome") != "PASS"
                                            for record in outcomes.values()):
                    raise ValueError("baseline must be fully green for all-green policy")

            frozen["admission_intent_sha256"] = digest
            frozen["admission"] = normalized
            frozen["checkpoint"] = checkpoint
            frozen["worker_task_class"] = {
                "role": task.role, "oracle": task.oracle, "size": task.size,
                "languages": list(task.languages), "key": task.key,
            }
            frozen["acceptance_test_ids"] = accepted_ids
            frozen["baseline_run"] = observation
            os.chmod(contract_path, 0o600)
            _store(contract_path, frozen, 0o400)

            _store(home / "acceptance.json", acceptance, 0o400, exclusive=True)
            opened_at = time.time()
            _store(home / "elapsed-checkpoint.json", {
                "opened_at": opened_at,
                "max_seconds": checkpoint["max_seconds"],
                "max_starts": checkpoint["max_starts"],
                "first_value_seconds": checkpoint["first_value_seconds"],
            }, 0o400, exclusive=True)
            binding = {"sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest()}
            _store(home / "binding.json", binding, 0o400, exclusive=True)
            gaps = [{"id": item["id"], "state": "OPEN", "proposition": item["proposition"]}
                    for item in acceptance]
            gaps.append({"id": "independent-review", "state": "OPEN"})
            decision = {"decision": "OPEN", "version": 1, "unit_id": str(uuid.uuid4()),
                        "gaps": gaps}
            _store(home / "decision.json", decision, 0o400, exclusive=True)
            state = _state(home, "ADMITTED", 1, unit_id=decision["unit_id"],
                           decision="OPEN", admission_intent_sha256=digest)
            return {"status": "ADMITTED", "version": state["version"],
                    "contract": str(contract_path), "sha256": binding["sha256"],
                    "unit_id": decision["unit_id"]}
        except Exception as exc:
            _cleanup_freeze(home)
            raise ValueError("baseline or selector admission failed: " + str(exc)) from exc
    except AdmissionRefused:
        raise
    except Exception as exc:
        diagnostic = str(exc)
        if isinstance(exc, ValueError):
            diagnostic = str(exc)
        raise _refuse(home, diagnostic) from exc
