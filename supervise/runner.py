# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Drive a packaged OMP manager for one portable invocation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid
import select

from supervise import transport, verify
from supervise.controller import admission


BOOTSTRAP = (
    "Portable /supervise invocation {invocation_id}, version {version}. Plan: {plan}. Project: {project}. You are the manager. "
    "Read the plan and the project tests with your read-only tools, then submit every decision through the supervise_continue tool, "
    "passing one JSON intent object as intent_json. Do not implement project files yourself and do not call model providers directly. "
    """(1) Admit at version 0: {{"action":"admit","invocation_id":"{invocation_id}","expected_version":0,"acceptance":[{{"id":"<gap id>","proposition":"<what must hold>","selectors":["<test id exactly as the test command reports it>"]}}],"tests":{{"argv":["python3","-m","unittest","discover","-s","tests"],"format":"unittest","timeout_seconds":120,"baseline_policy":"nonregression","expected_baseline_failures":["<every test id that fails before the work>"]}},"owned_paths":["<relative file the worker may change; never under tests/>"],"packet":{{"text":"Kind: impl\\nOwned paths: <comma-separated owned paths>\\nDeclared size: <n> files, <m> changed lines\\nOracle: <the test command>\\nDomain: none\\nReview: independent reviewer\\n\\n<the complete task for the worker>"}},"checkpoint":{{"max_starts":6,"max_seconds":3600,"first_value_seconds":1800}},"timeout_seconds":900}}. """
    "A refusal leaves version 0: read its message, correct the intent and admit again. (2) After admission send "
    '{{"action":"author","invocation_id":...,"expected_version":<current version>,"targets":["<open acceptance id>",...]}} and inspect the returned result. '
    '(3) Send {{"action":"review",...,"targets":[]}} for independent review; after a REJECT send '
    '{{"action":"repair",...,"targets":[...]}} and review again (at most two repairs). (4) Send '
    '{{"action":"close",...,"targets":[]}} once the review accepted. {{"action":"diagnose"}} reads state without changing it; '
    '{{"action":"reconcile"}} settles an interrupted start. Every intent carries invocation_id and the expected_version from the latest result. '
    "Stop when the status is ACCEPTED, CHECKPOINT_EXHAUSTED or RECONCILE_REQUIRED and report it."
)


def _read_json(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _plan_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _omp_executable():
    configured = os.environ.get("SUPERVISE_OMP")
    candidate = configured if configured else "omp"
    if os.path.dirname(candidate):
        resolved = shutil.which(candidate)
        if resolved is None:
            path = Path(candidate).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                resolved = str(path.resolve())
    else:
        resolved = shutil.which(candidate)
    if not resolved:
        raise FileNotFoundError("OMP manager was not found")
    return resolved


def _state(home):
    try:
        value = _read_json(Path(home) / "state.json")
    except (OSError, UnicodeError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _native_starts(home):
    count = 0
    try:
        with (Path(home) / "STATE.jsonl").open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(event, dict) and event.get("kind") == "start":
                    count += 1
    except OSError:
        pass
    return count


def _terminate_group(process):
    """Stop the manager's session and reap its leader."""
    if process is None:
        return
    try:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
    except (OSError, ValueError):
        pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    deadline = time.monotonic() + 5.0
    while True:
        try:
            process.wait(timeout=0.05)
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            break
        try:
            os.killpg(process.pid, 0)
            group_exists = True
        except ProcessLookupError:
            group_exists = False
        except PermissionError:
            group_exists = True
        except OSError:
            group_exists = False
        if not group_exists:
            break
        if time.monotonic() >= deadline:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            break
        time.sleep(0.05)
    try:
        process.wait()
    except OSError:
        pass


def run(plan, *, project, max_seconds, model=None):
    """Run the manager, persist a trace receipt, print it, and return its exit code."""
    seconds = transport.validate_window(max_seconds)
    project_path = Path(project).expanduser().resolve()
    plan_path = Path(plan).expanduser().resolve()
    if not project_path.is_dir():
        raise ValueError("project must be an existing directory")
    if not plan_path.is_file():
        raise ValueError("plan must name an existing regular file")

    state_root = transport.state_root()
    state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    home = transport.invocation_path(state_root, project_path, plan_path)
    pending = admission.initialize(home, project=project_path, plan_path=plan_path)
    omp = _omp_executable()

    trace_root = transport.state_root() / "transports"
    trace_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    trace = trace_root / str(uuid.uuid4())
    trace.mkdir(mode=0o700)
    os.chmod(trace, 0o700)
    rpc_path = trace / "rpc.jsonl"
    stderr_path = trace / "stderr.txt"
    receipt_path = trace / "receipt.json"

    extension = Path(__file__).resolve().parent / "omp" / "supervise.ts"
    argv = [omp, "--mode", "rpc", "--no-ui", "--no-extensions", "-e", str(extension),
            "--max-time", str(seconds)]
    if model is not None:
        argv.extend(["--model", str(model)])
    env = os.environ.copy()
    env["SUPERVISE_PYTHON"] = sys.executable
    env["SUPERVISE_STATE"] = str(transport.state_root())
    package_root = str(Path(__file__).resolve().parent.parent)
    prior_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = package_root + (os.pathsep + prior_pythonpath if prior_pythonpath else "")

    now_wall = time.time()
    began = time.monotonic()
    deadline = began + float(seconds)
    status = None
    manager_fault = None
    acknowledged = False
    verification = None
    manager = transport.ManagerWatch(home)
    proc = None
    stdout_eof = False
    buffer = bytearray()

    try:
        with rpc_path.open("wb") as rpc, stderr_path.open("wb") as stderr:
            os.chmod(rpc_path, 0o600)
            os.chmod(stderr_path, 0o600)
            proc = subprocess.Popen(argv, cwd=project_path, env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=stderr, start_new_session=True)
            prompt = {"id": "supervise", "type": "prompt",
                      "message": "/supervise " + json.dumps(str(plan_path))}
            try:
                proc.stdin.write((json.dumps(prompt, ensure_ascii=False) + "\n").encode("utf-8"))
                proc.stdin.flush()
            except BrokenPipeError:
                pass
            fd = proc.stdout.fileno()
            os.set_blocking(fd, False)

            while status is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status = "MANAGER_TIMEOUT"
                    break
                try:
                    readable, _, _ = select.select([fd], [], [], min(0.1, remaining))
                except (OSError, ValueError):
                    readable = []
                if readable:
                    try:
                        chunk = os.read(fd, 65536)
                    except BlockingIOError:
                        chunk = None
                    except OSError:
                        chunk = b""
                    if chunk is not None:
                        if chunk:
                            rpc.write(chunk)
                            rpc.flush()
                            buffer.extend(chunk)
                            while b"\n" in buffer:
                                raw, _, rest = buffer.partition(b"\n")
                                buffer = bytearray(rest)
                                try:
                                    event = json.loads(raw.decode("utf-8"))
                                except (UnicodeError, ValueError, TypeError):
                                    continue
                                fault = manager.observe(event)
                                if (isinstance(event, dict) and event.get("id") == "supervise"
                                        and event.get("type") == "response"):
                                    if event.get("success") is False:
                                        status = "COMMAND_REFUSED"
                                        error = event.get("error")
                                        manager_fault = str(error) if error is not None else "command refused"
                                        break
                                    if event.get("success") is True:
                                        acknowledged = True
                                if fault is not None:
                                    status = fault[0]
                                    manager_fault = str(fault[1])
                                    break
                        else:
                            stdout_eof = True
                            try:
                                os.close(fd)
                            except OSError:
                                pass
                if status is not None:
                    break

                stalled = manager.stalled()
                if stalled is not None:
                    status, detail = stalled
                    manager_fault = str(detail)
                    break

                state = _state(home)
                current_status = state.get("status")
                if current_status in ("CHECKPOINT_EXHAUSTED", "RECONCILE_REQUIRED", "PRECHECK_FAILED"):
                    status = current_status
                    break
                if current_status == "ACCEPTED" and acknowledged:
                    try:
                        live_digest = _plan_digest(plan_path)
                    except OSError:
                        live_digest = None
                    if (live_digest == pending.get("plan_sha256")
                            and live_digest == _plan_digest_from_pending(home)):
                        verification = verify.verify_invocation(home)
                        status = "ACCEPTED" if verification.get("verdict") == "PASS" else "UNVERIFIED"
                    else:
                        verification = {"verdict": "FAIL", "checks": [],
                                        "error": "plan digest does not match pending admission"}
                        status = "UNVERIFIED"
                    break

                return_code = proc.poll()
                if return_code is not None and stdout_eof:
                    # A final non-newline response is still useful evidence.
                    if buffer:
                        try:
                            event = json.loads(bytes(buffer).decode("utf-8"))
                        except (UnicodeError, ValueError, TypeError):
                            event = None
                        if isinstance(event, dict):
                            fault = manager.observe(event)
                            if event.get("id") == "supervise" and event.get("type") == "response":
                                if event.get("success") is False:
                                    status = "COMMAND_REFUSED"
                                    manager_fault = str(event.get("error", "command refused"))
                                elif event.get("success") is True:
                                    acknowledged = True
                            if fault is not None:
                                status, detail = fault
                                manager_fault = str(detail)
                    if status is None:
                        state = _state(home)
                        current_status = state.get("status")
                        if current_status in ("CHECKPOINT_EXHAUSTED", "RECONCILE_REQUIRED", "PRECHECK_FAILED"):
                            status = current_status
                        elif current_status == "ACCEPTED" and acknowledged:
                            try:
                                digest = _plan_digest(plan_path)
                            except OSError:
                                digest = None
                            if digest == pending.get("plan_sha256") and digest == _plan_digest_from_pending(home):
                                verification = verify.verify_invocation(home)
                                status = "ACCEPTED" if verification.get("verdict") == "PASS" else "UNVERIFIED"
                            else:
                                verification = {"verdict": "FAIL", "checks": [], "error": "plan digest mismatch"}
                                status = "UNVERIFIED"
                        else:
                            status = "MANAGER_EXITED"
                    break
    except BaseException:
        _terminate_group(proc)
        raise
    finally:
        _terminate_group(proc)

    if verification is None and status in ("ACCEPTED", "UNVERIFIED"):
        verification = verify.verify_invocation(home)
    ended_wall = time.time()
    admission_phase = "ADMITTED" if (Path(home) / "contract.json").exists() else "NOT_ADMITTED"
    receipt = transport.make_receipt(
        status=status or "MANAGER_EXITED", admission_phase=admission_phase,
        native_starts=_native_starts(home), invocation=home,
        began_at=now_wall, ended_at=ended_wall, manager_fault=manager_fault,
    )
    receipt.update({"trace": str(trace), "argv": argv, "cwd": str(project_path),
                    "plan": str(plan_path),
                    "verify_verdict": verification.get("verdict") if verification else None,
                    "verification": verification})
    serialized = json.dumps(receipt, ensure_ascii=False, allow_nan=False)
    with receipt_path.open("w", encoding="utf-8") as stream:
        stream.write(serialized + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(receipt_path, 0o600)
    print(serialized)
    return 0 if receipt["status"] == "ACCEPTED" else 2


def _plan_digest_from_pending(home):
    try:
        pending = _read_json(Path(home) / "pending.json")
        return pending.get("plan_sha256") if isinstance(pending, dict) else None
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
