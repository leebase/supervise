# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Linux process execution and identity primitives for the supervision kernel."""

import hashlib
import math
import os
from pathlib import Path
import signal
import subprocess
import time

from supervise.backend import ProcessCapture


class ExecRefused(ValueError):
    """Raised when a process launch violates the kernel's execution bounds."""


def boot_id() -> str | None:
    """Return this boot's Linux kernel identifier, if it is available."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return None


def process_identity(pid: int) -> dict | None:
    """Read a process's stable-within-boot identity from procfs."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_bytes()
        # comm is parenthesized and may itself contain ')' or non-ASCII bytes.
        fields = stat.rsplit(b")", 1)[1].split()
        start_time = int(fields[19])  # field 22; fields begin at field 3
    except (OSError, IndexError, ValueError):
        return None
    return {"pid": int(pid), "start_time": start_time, "boot": boot_id()}


def _number(value, name: str, *, positive: bool) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ExecRefused(f"{name} must be a finite number") from error
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        bound = "finite and greater than zero" if positive else "finite and non-negative"
        raise ExecRefused(f"{name} must be {bound}")
    return number


def _digest(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_group(process_group: int, grace: float) -> None:
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while grace and _group_exists(process_group):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))
    if _group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run(
    argv,
    *,
    env,
    cwd,
    timeout,
    grace,
    stdout_path,
    stderr_path,
    on_spawn,
    stdin_path=None,
) -> ProcessCapture:
    """Run argv in a fresh process group and return immutable capture facts."""
    try:
        program = os.fspath(argv[0])
    except (IndexError, TypeError, AttributeError) as error:
        raise ExecRefused("argv must begin with an absolute program path") from error
    if not os.path.isabs(program):
        raise ExecRefused("argv[0] must be an absolute path")
    if env is None:
        raise ExecRefused("env must be an explicit environment mapping")
    if cwd is None:
        raise ExecRefused("cwd must be explicit")

    timeout = _number(timeout, "timeout", positive=True)
    grace = _number(grace, "grace", positive=False)

    stdout_path = Path(stdout_path)
    stderr_path = Path(stderr_path)
    if os.path.lexists(stdout_path) or os.path.lexists(stderr_path):
        raise ExecRefused("capture paths must not already exist")
    if os.path.abspath(stdout_path) == os.path.abspath(stderr_path):
        raise ExecRefused("stdout and stderr paths must be different")

    # Open input before creating capture files so a bad stdin path leaves no captures.
    input_stream = open(stdin_path, "rb") if stdin_path is not None else subprocess.DEVNULL
    stdout_stream = None
    stderr_stream = None
    try:
        try:
            stdout_stream = open(stdout_path, "xb")
            stderr_stream = open(stderr_path, "xb")
        except BaseException as error:
            if stdout_stream is not None:
                stdout_stream.close()
                stdout_stream = None
                try:
                    os.unlink(stdout_path)
                except FileNotFoundError:
                    pass
            if isinstance(error, FileExistsError):
                raise ExecRefused("capture paths must not already exist") from error
            raise

        started = time.monotonic()
        try:
            process = subprocess.Popen(
                argv,
                stdin=input_stream,
                stdout=stdout_stream,
                stderr=stderr_stream,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        except BaseException:
            # Do not leave partial captures behind when launch itself fails.
            stdout_stream.close()
            stderr_stream.close()
            stdout_stream = stderr_stream = None
            for path in (stdout_path, stderr_path):
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
            raise

        try:
            if on_spawn is not None:
                on_spawn(process_identity(process.pid))

            timed_out = False
            try:
                exit_code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                # The process leader may exit before its descendants. Give the whole
                # group the full grace interval, then kill any members still remaining.
                if grace:
                    time.sleep(grace)
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                exit_code = None

            if not timed_out:
                _terminate_group(process.pid, grace)
                process.wait()

            elapsed = time.monotonic() - started
        except BaseException:
            # Any failure after spawn must not strand this process group. Keep the
            # triggering exception even if best-effort cleanup itself fails.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except BaseException:
                pass
            try:
                process.wait()
            except BaseException:
                pass
            raise
    finally:
        if stdout_stream is not None:
            stdout_stream.close()
        if stderr_stream is not None:
            stderr_stream.close()
        if stdin_path is not None and input_stream is not subprocess.DEVNULL:
            input_stream.close()

    return ProcessCapture(
        exit_code=exit_code,
        timed_out=timed_out,
        seconds=elapsed,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stdout_sha256=_digest(stdout_path),
        stderr_sha256=_digest(stderr_path),
    )
