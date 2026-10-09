# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Finite, read-only Python-test evidence for controller decisions.

Evidence produced here is an internal observation, not an owner receipt.
"""

import ast
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
_OUTCOMES = {"PASS", "FAIL", "ERROR", "SKIP", "XFAIL", "UNKNOWN"}


class EvidenceError(ValueError):
    """Invalid, unsafe, or incomplete test-evidence input."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _inside(parent, child):
    return child == parent or parent in child.parents


def _absolute(path):
    return Path(os.path.abspath(os.fspath(path)))


def _private(path, project):
    path = _absolute(path)
    if path.resolve() != path or _inside(project, path) or _inside(path, project):
        raise EvidenceError("Evidence must be outside the project, with no symlink ancestors")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise EvidenceError("Cannot create private evidence directory") from exc
    if path.is_symlink() or not path.is_dir() or path.stat().st_mode & 0o777 != 0o700:
        raise EvidenceError("Evidence directory must have private mode 0700")
    return path


def _scope(project, scope):
    if not isinstance(scope, str) or not scope or "\\" in scope or "\x00" in scope:
        raise EvidenceError("Invalid project scope")
    relative = PurePosixPath(scope)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise EvidenceError("Project scope must be relative and stay inside the project")
    path = project.joinpath(*relative.parts)
    if path.resolve() != path or not _inside(project, path):
        raise EvidenceError("Project scope escapes the project or uses a symlink")
    return path


def _scopes(project, scopes):
    if not isinstance(scopes, (list, tuple)):
        raise EvidenceError("Project scopes must be a sequence")
    normalized = []
    for scope in scopes:
        _scope(project, scope)
        if scope not in normalized:
            normalized.append(scope)
    return normalized


def snapshot(project, scopes):
    """Hash a stable inventory of the files covered by relative project scopes."""
    project = _absolute(project)
    if project.resolve() != project or not project.is_dir():
        raise EvidenceError("Project must be a real directory")
    scopes = _scopes(project, scopes)
    files = {}
    for scope in scopes:
        path = _scope(project, scope)
        if not path.exists():
            files[scope] = {"missing": True}
            continue
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for file in candidates:
            if "__pycache__" in file.parts:
                continue
            if file.is_symlink():
                raise EvidenceError("Symlink in source inventory")
            if not file.is_file():
                continue
            data = file.read_bytes()
            item = {"byte_sha256": sha(data)}
            if file.suffix == ".py":
                try:
                    normalized = ast.dump(ast.parse(data), include_attributes=False)
                    item.update(semantic_sha256=sha(normalized.encode()),
                                normalized_python_ast=normalized)
                except (SyntaxError, ValueError, UnicodeError) as exc:
                    item.update(semantic_sha256=None, parse_error=str(exc))
            else:
                item["semantic_sha256"] = item["byte_sha256"]
            files[str(file.relative_to(project))] = item
    return {
        "files": files,
        "byte_sha256": sha(encoded(files)),
        "semantic_sha256": sha(encoded({
            key: value.get("semantic_sha256", "MISSING") for key, value in files.items()
        })),
    }


def validate_tests(tests):
    """Validate the deliberately small set of supported Python test commands."""
    try:
        if not isinstance(tests, dict):
            raise EvidenceError("Test specification must be an object")
        argv = tests["argv"]
        test_format = tests["format"]
        timeout = tests["timeout_seconds"]
        if (not isinstance(argv, list) or len(argv) < 3
                or not all(isinstance(item, str) and item for item in argv)):
            raise EvidenceError("Ordinary Python test argv required")
        expected = {"unittest": "unittest", "pytest-junit": "pytest"}.get(test_format)
        if expected is None or argv[1:3] != ["-m", expected]:
            raise EvidenceError("Supported argv: python -m unittest / python -m pytest")
        interpreter = Path(argv[0])
        if not interpreter.is_absolute() or not interpreter.is_file():
            raise EvidenceError("Freeze an absolute Python interpreter")
        if any(arg.startswith(("--junitxml", "--junit-xml")) for arg in argv):
            raise EvidenceError("JUnit output path is controlled by the evidence runner")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 3600):
            raise EvidenceError("Finite test timeout required")
    except (KeyError, TypeError, OverflowError) as exc:
        raise EvidenceError("Malformed test specification") from exc


def parse_junit(path):
    """Parse a JUnit report, marking unsupported or contradictory data incomplete."""
    try:
        root = ET.parse(path).getroot()
        if root.tag not in ("testsuites", "testsuite"):
            raise ValueError("Not a JUnit suite")
        suites = list(root.iter("testsuite"))
        if not suites:
            raise ValueError("No suite metadata")
        outcomes = {}
        complete = True
        totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        for suite in suites:
            cases = suite.findall("testcase")
            complete &= int(suite.attrib["tests"]) == len(cases)
            counts = {"tests": len(cases), "failures": 0, "errors": 0, "skipped": 0}
            for case in cases:
                name, classname = case.attrib["name"], case.attrib.get("classname", "")
                test_id = classname + "::" + name
                if not name or test_id in outcomes:
                    raise ValueError("Missing or duplicate JUnit test ID")
                tags = {child.tag for child in case}
                if tags - {"failure", "error", "skipped", "system-out", "system-err", "properties"}:
                    raise ValueError("Unsupported JUnit outcome")
                result_tags = [child.tag for child in case
                               if child.tag in {"failure", "error", "skipped"}]
                if len(result_tags) > 1:
                    raise ValueError("Contradictory or duplicate JUnit case outcomes")
                status = ("ERROR" if "error" in tags else
                          "FAIL" if "failure" in tags else
                          "SKIP" if "skipped" in tags else "PASS")
                for tag, counter in (("failure", "failures"), ("error", "errors"),
                                     ("skipped", "skipped")):
                    counts[counter] += int(tag in tags)
                outcomes[test_id] = {
                    "outcome": status,
                    "diagnostic": "\n".join(
                        child.attrib.get("message", "") + "\n" + (child.text or "")
                        for child in case if child.tag in {"failure", "error", "skipped"}
                    ),
                }
            for counter, count in counts.items():
                if counter in suite.attrib and int(suite.attrib[counter]) != count:
                    raise ValueError("Contradictory JUnit suite counter: " + counter)
                totals[counter] += count
        if root.tag == "testsuites":
            for counter, count in totals.items():
                if counter in root.attrib and int(root.attrib[counter]) != count:
                    raise ValueError("Contradictory JUnit aggregate counter: " + counter)
        return {"ids": sorted(outcomes), "outcomes": outcomes, "complete": complete}
    except (OSError, ET.ParseError, KeyError, ValueError, TypeError, OverflowError) as exc:
        return {"ids": [], "outcomes": {}, "complete": False, "error": str(exc)}


def _command(contract, directory, collect=False):
    argv = contract["tests"]["argv"]
    output = directory / ("structured.json" if contract["tests"]["format"] == "unittest"
                          else "junit.xml")
    if contract["tests"]["format"] == "unittest":
        scratch, _private_home = _scratch_paths(directory)
        runner = scratch / "unittest_runner.py"
        with runner.open("xb") as stream:
            stream.write((HERE / "unittest_runner.py").read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        runner.chmod(0o444)
        command = [argv[0], "-B", str(runner), str(output),
                   "collect" if collect else "run", *argv[3:]]
        return command, output
    if collect:
        raise EvidenceError("JUnit freeze requires an actual baseline run")
    return [*argv, "-o", "junit_family=xunit2", "--junitxml=" + str(output)], output


def _producer_identity():
    return {name: sha((HERE / name).read_bytes())
            for name in ("evidence.py", "unittest_runner.py")}


def _scratch_paths(directory):
    scratch = directory / "scratch"
    private_home = directory / "scratch-home"
    for path in (scratch, private_home):
        path.mkdir(mode=0o700, exist_ok=True)
        if (path.is_symlink() or not path.is_dir()
                or path.stat().st_mode & 0o777 != 0o700):
            raise EvidenceError("Unsafe verification scratch directory")
    if any(private_home.iterdir()):
        raise EvidenceError("Verification scratch home must be empty")
    return scratch, private_home


def _verification_argv(argv, project, directory, source_fds):
    """Mask the host home and expose only the project and this run's evidence."""
    project = _absolute(project)
    directory = _absolute(directory)
    evidence_parent = directory.parent
    if (project.resolve() != project or directory.resolve() != directory
            or _inside(project, directory)):
        raise EvidenceError("Unsafe verification mount paths")
    try:
        home = Path.home().resolve(strict=True)
    except OSError as exc:
        raise EvidenceError("Invoking home is unavailable") from exc
    if (not home.is_dir() or home == Path(home.anchor)
            or any(path == home or _inside(path, home) for path in (project, evidence_parent))):
        raise EvidenceError("Unsafe verification home mount")

    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise OSError("Bubblewrap is unavailable")
    _scratch_paths(directory)
    command = [bwrap, "--unshare-user", "--unshare-pid", "--unshare-net",
               "--die-with-parent", "--cap-drop", "ALL", "--ro-bind", "/", "/",
               "--tmpfs", str(home)]

    targets = (project, evidence_parent, directory)
    created = set()
    for target in sorted((path for path in targets if _inside(home, path)),
                         key=lambda path: len(path.parts)):
        current = home
        for component in target.relative_to(home).parts:
            current = current / component
            if current not in created:
                command.extend(("--dir", str(current)))
                created.add(current)

    bind_items = [(project, source_fds[0]), (evidence_parent, source_fds[1])]
    bind_items.sort(key=lambda item: len(item[0].parts))
    for target, descriptor in bind_items:
        command.extend(("--ro-bind", "/proc/self/fd/" + str(descriptor), str(target)))
    # Bubblewrap needs these descriptors while resolving the bind sources. Do not
    # rely on its own close-on-exec policy: close every extra descriptor in a
    # bootstrap process after setup and before the test interpreter is exec'd.
    launcher = (
        "import os,sys\n"
        "for _name in os.listdir('/proc/self/fd'):\n"
        " _fd=int(_name)\n"
        " if _fd > 2:\n"
        "  try: os.close(_fd)\n"
        "  except OSError: pass\n"
        "if any(os.path.exists('/proc/self/fd/'+_name) for _name in os.listdir('/proc/self/fd') if int(_name)>2): raise SystemExit(126)\n"
        "os.execv(sys.argv[1],sys.argv[1:])\n"
    )
    payload = [argv[0], "-c", launcher, *argv]
    command.extend(("--bind", "/proc/self/fd/" + str(source_fds[2]), str(directory),
                    "--proc", "/proc", "--dev", "/dev", "--", *payload))
    return command


def _test_environment(directory):
    scratch, private_home = _scratch_paths(directory)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(private_home),
        "TMPDIR": str(scratch),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(scratch / "bytecode"),
    }
    for name in ("LANG", "LC_ALL"):
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def _execute(argv, cwd, timeout, directory):
    start = time.monotonic()
    timed_out = False
    error = None
    stdout_path, stderr_path = directory / "stdout.txt", directory / "stderr.txt"
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        source_fds = []
        try:
            for path in (cwd, directory.parent, directory):
                source_fds.append(os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)))
            process = subprocess.Popen(
                _verification_argv(argv, cwd, directory, source_fds), cwd=cwd,
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                start_new_session=True, pass_fds=tuple(source_fds),
                env=_test_environment(directory),
            )
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                try:
                    returncode = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    returncode, error = None, "Test cleanup outcome unknown"
        except OSError as exc:
            returncode, error = None, str(exc)
        finally:
            for descriptor in source_fds:
                os.close(descriptor)
    return {
        "argv": argv,
        "returncode": returncode,
        "timeout": timed_out,
        "error": error,
        "requested_timeout_seconds": timeout,
        "effective_timeout_seconds": timeout,
        "cleanup_bound_seconds": 5,
        "seconds": time.monotonic() - start,
        "stdout": {"path": str(stdout_path), "sha256": sha(stdout_path.read_bytes())},
        "stderr": {"path": str(stderr_path), "sha256": sha(stderr_path.read_bytes())},
    }


def _observe(contract, directory, collect=False):
    try:
        project = _absolute(contract["project"])
        directory = _absolute(directory)
        if project.resolve() != project or not project.is_dir():
            raise EvidenceError("Invalid project directory")
        directory = _private(directory, project)
        before = snapshot(project, contract["source_scopes"])
        test_before = snapshot(project, contract["test_scopes"])
        argv, output = _command(contract, directory, collect)
        if output.exists():
            raise EvidenceError("Structured output already exists")
        process = _execute(argv, project, contract["tests"]["timeout_seconds"], directory)
    except (KeyError, TypeError, OSError, EvidenceError) as exc:
        # A run that cannot be safely started is represented as UNKNOWN evidence.
        fallback = _absolute(directory)
        if not fallback.is_dir():
            raise EvidenceError("Observation directory must exist") from exc
        return {
            "schema": "component-observation/1-not-owner-receipt",
            "process": {"returncode": None, "timeout": False, "error": str(exc)},
            "complete": False, "verdict": "UNKNOWN", "test_ids": [], "outcomes": {},
            "missing_frozen_ids": list(contract.get("frozen_ids", [])),
            "changed_frozen_test_inputs": [], "structured_path": None,
            "structured_sha256": None, "structured_error": str(exc),
        }

    structured = {}
    try:
        if contract["tests"]["format"] == "unittest":
            structured = json.loads(output.read_text(encoding="utf-8"))
        else:
            structured = parse_junit(output)
        ids, outcomes = structured["ids"], structured["outcomes"]
        if (not isinstance(ids, list) or not all(isinstance(item, str) for item in ids)
                or len(set(ids)) != len(ids) or not isinstance(outcomes, dict)):
            raise ValueError("Malformed test vector")
        if not collect and set(ids) != set(outcomes):
            raise ValueError("Incomplete outcomes")
        if any(not isinstance(value, dict) or value.get("outcome") not in _OUTCOMES
               for value in outcomes.values()):
            raise ValueError("Invalid test outcome")
        complete = structured.get("complete") is True
    except (OSError, ValueError, KeyError, TypeError) as exc:
        ids, outcomes, complete = [], {}, False
        structured = {"error": str(exc)}

    after = snapshot(project, contract["source_scopes"])
    test_after = snapshot(project, contract["test_scopes"])
    frozen_ids = contract.get("frozen_ids", [])
    missing = sorted(set(frozen_ids) - set(ids))
    unexpected = sorted(set(ids) - set(frozen_ids)) if frozen_ids else []
    frozen_inputs = contract.get("frozen_test_inputs", {})
    changed_tests = sorted(
        key for key in set(frozen_inputs) | set(test_before["files"])
        if test_before["files"].get(key) != frozen_inputs.get(key)
    )
    source_baseline = contract.get("source_baseline")
    producer_valid = contract.get("producer") == _producer_identity()
    try:
        interpreter_valid = contract.get("interpreter_sha256") == sha(
            Path(contract["tests"]["argv"][0]).read_bytes())
    except (KeyError, OSError, TypeError, IndexError):
        interpreter_valid = False
    complete = bool(complete and ids and not process.get("timeout")
                    and process.get("error") is None)
    complete &= before["byte_sha256"] == after["byte_sha256"] and test_before == test_after
    complete &= not missing and not unexpected and not changed_tests
    complete &= producer_valid and interpreter_valid
    if source_baseline is not None:
        complete &= before["byte_sha256"] == source_baseline.get("byte_sha256")
    if not complete:
        verdict = "UNKNOWN"
    elif any(value["outcome"] in {"FAIL", "ERROR"} for value in outcomes.values()):
        verdict = "FAIL"
    elif any(value["outcome"] != "PASS" for value in outcomes.values()) or process["returncode"] != 0:
        verdict = "UNKNOWN"
    else:
        verdict = "PASS"
    return {
        "schema": "component-observation/1-not-owner-receipt",
        "process": process,
        "complete": bool(complete),
        "verdict": verdict,
        "test_ids": sorted(ids),
        "outcomes": outcomes,
        "missing_frozen_ids": missing,
        "unexpected_test_ids": unexpected,
        "changed_frozen_test_inputs": changed_tests,
        "source_before": before,
        "source_after": after,
        "test_inputs_before": test_before,
        "test_inputs_after": test_after,
        "structured_path": str(output),
        "structured_sha256": sha(output.read_bytes()) if output.exists() else None,
        "structured_error": structured.get("error"),
    }


def observe(contract, directory):
    """Run exactly the frozen test command in a read-only bubblewrap namespace."""
    return _observe(contract, directory)


def _exclusive(path, value):
    path = Path(path)
    with path.open("xb") as stream:
        stream.write(encoded(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)


def _validate_acceptance(acceptance):
    if not isinstance(acceptance, list) or not acceptance:
        raise EvidenceError("Non-empty acceptance records are required")
    seen = set()
    for item in acceptance:
        if not isinstance(item, dict):
            raise EvidenceError("Acceptance record must be an object")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen:
            raise EvidenceError("Unique non-empty acceptance IDs are required")
        seen.add(identifier)


def _validate_owned(project, root, owned_paths):
    if not isinstance(owned_paths, list) or not owned_paths:
        raise EvidenceError("Owned paths are required")
    seen = set()
    for item in owned_paths:
        if (not isinstance(item, str) or not item or "\\" in item or "\x00" in item
                or PurePosixPath(item).is_absolute() or ".." in PurePosixPath(item).parts):
            raise EvidenceError("Invalid owned path")
        path = _scope(project, item)
        if path in seen or _inside(path.resolve(), root) or _inside(root, path.resolve()):
            raise EvidenceError("Worker scope intersects protected paths")
        seen.add(path)


def freeze(project, root, *, acceptance, tests, source_scopes, test_scopes, owned_paths):
    """Freeze test inputs and retain an exclusive baseline collection observation."""
    try:
        project = _absolute(project).resolve()
        if not project.is_dir():
            raise EvidenceError("Project must be a directory")
        root = _private(root, project)
        validate_tests(tests)
        _validate_acceptance(acceptance)
        source_scopes = _scopes(project, source_scopes)
        test_scopes = _scopes(project, test_scopes)
        _validate_owned(project, root, owned_paths)
        if (root / "contract.json").exists() or (root / "baseline-observation.json").exists():
            raise EvidenceError("Frozen evidence is immutable and already exists")
        baseline_dir = root / "baseline"
        baseline_dir.mkdir(mode=0o700)
        if baseline_dir.stat().st_mode & 0o777 != 0o700:
            raise EvidenceError("Baseline directory must have private mode 0700")
        contract = {
            "run_identity": str(uuid.uuid4()),
            "project": str(project),
            "private_root": str(root),
            "acceptance": acceptance,
            "tests": tests,
            "source_scopes": source_scopes,
            "test_scopes": test_scopes,
            "owned_paths": owned_paths,
            "producer": _producer_identity(),
            "producer_root": str(HERE),
            "interpreter_sha256": sha(Path(tests["argv"][0]).read_bytes()),
            "frozen_test_inputs": snapshot(project, test_scopes)["files"],
            "source_baseline": snapshot(project, source_scopes),
            "independent_review": "OPEN",
        }
        baseline = _observe(contract, baseline_dir, collect=tests["format"] == "unittest")
        contract["frozen_ids"] = baseline["test_ids"]
        contract["baseline_collection_valid"] = baseline["complete"]
        _exclusive(root / "baseline-observation.json", baseline)
        _exclusive(root / "contract.json", contract)
        path = root / "contract.json"
        return {"path": str(path), "sha256": sha(path.read_bytes())}
    except EvidenceError:
        raise
    except (KeyError, TypeError, OSError, ValueError) as exc:
        raise EvidenceError(str(exc)) from exc


def _observation_vector(observation):
    if not isinstance(observation, dict):
        return {}, False
    outcomes = observation.get("outcomes")
    ids = observation.get("test_ids")
    valid = (observation.get("complete") is True and isinstance(outcomes, dict)
             and isinstance(ids, list) and all(isinstance(item, str) for item in ids)
             and len(set(ids)) == len(ids) and set(ids) == set(outcomes))
    if isinstance(outcomes, dict):
        valid = valid and all(isinstance(item, dict) and item.get("outcome") in _OUTCOMES
                              for item in outcomes.values())
    else:
        outcomes = {}
    return outcomes, bool(valid)


def compare(baseline, observation, policy, required):
    """Compare complete outcome vectors under nonregression or all-green policy."""
    if policy not in {"nonregression", "all-green"}:
        raise EvidenceError("Unsupported comparison policy")
    if not isinstance(required, (list, tuple, set, frozenset)) or not all(
            isinstance(item, str) and item for item in required):
        raise EvidenceError("Required test IDs must be a collection of non-empty strings")
    baseline_outcomes, baseline_complete = _observation_vector(baseline)
    current_outcomes, current_complete = _observation_vector(observation)
    old = {key: value.get("outcome") for key, value in baseline_outcomes.items()
           if isinstance(value, dict)}
    current = {key: value.get("outcome") for key, value in current_outcomes.items()
               if isinstance(value, dict)}
    ids = set(old) | set(current)
    repaired = sorted(key for key in ids
                      if old.get(key) not in (None, "PASS") and current.get(key) == "PASS")
    pre_existing = sorted(key for key in ids
                          if old.get(key) not in (None, "PASS") and current.get(key) == old.get(key))
    regressed = sorted(key for key in ids
                       if old.get(key) == "PASS" and current.get(key) != "PASS")
    new_failures = sorted(key for key in ids
                          if current.get(key) in {"FAIL", "ERROR"}
                          and old.get(key) != current.get(key))
    required = set(required)
    required_green = bool(required) and all(current.get(key) == "PASS" for key in required)
    same_ids = set(old) == set(current)
    valid = baseline_complete and current_complete and same_ids
    if policy == "all-green":
        acceptable = valid and bool(current) and all(value == "PASS" for value in current.values())
        acceptable &= not required or all(current.get(key) == "PASS" for key in required)
    else:
        unchanged_preexisting = all(
            value == "PASS" or old.get(key) == value for key, value in current.items()
        )
        acceptable = (valid and required_green and not regressed and not new_failures
                      and unchanged_preexisting)
    return {
        "repaired": repaired,
        "pre_existing": pre_existing,
        "regressed": regressed,
        "new_failures": new_failures,
        "acceptable": bool(acceptable),
    }
