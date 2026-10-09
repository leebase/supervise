# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Materialize and run reviewers against an isolated, immutable evidence view."""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from . import seal


SYSTEM_BINDS = ("/usr", "/bin", "/lib", "/lib64")


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_BINDING_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_BINDING_FIELDS = {
    "mission_id", "transaction_id", "worker_id", "worker_role", "attempt_id", "source_id"
}


class ViewError(ValueError):
    """A reviewer view is invalid or cannot be safely isolated."""


def _fail(message):
    raise ViewError(message)


def _read_regular(path):
    """Read a regular file without following the final path component."""
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            _fail("view entry is not a regular file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                _fail("view entry is not a regular file")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = -1
                return stream.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except ViewError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise ViewError("view entry is unavailable") from error


def _safe_parts(name):
    if not isinstance(name, str) or not name or "\x00" in name or name.startswith("/"):
        _fail("invalid sealed filename")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        _fail("invalid sealed filename")
    return parts


def _manifest(raw, expected_identity):
    if not isinstance(expected_identity, str) or _HEX.fullmatch(expected_identity) is None:
        _fail("invalid expected identity")
    if hashlib.sha256(raw).hexdigest() != expected_identity:
        _fail("view seal identity mismatch")
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ViewError("invalid view seal") from error
    if not isinstance(record, dict) or json.dumps(
            record, sort_keys=True, separators=(",", ":")).encode("utf-8") != raw:
        _fail("view seal is not canonical")
    if set(record) != {"schema", "binding", "files"}:
        _fail("invalid view seal fields")
    if type(record["schema"]) is not int or record["schema"] != 1:
        _fail("unsupported view seal schema")
    binding = record["binding"]
    if not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS:
        _fail("invalid view seal binding")
    if any(not isinstance(value, str) or _BINDING_ID.fullmatch(value) is None
           for value in binding.values()):
        _fail("invalid view seal binding")
    files = record["files"]
    if not isinstance(files, dict):
        _fail("invalid view file table")
    for name, entry in files.items():
        _safe_parts(name)
        if not isinstance(entry, dict) or set(entry) != {"size", "sha256", "chunks"}:
            _fail("invalid view file record")
        size = entry["size"]
        digest = entry["sha256"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            _fail("invalid view file size")
        if not isinstance(digest, str) or _HEX.fullmatch(digest) is None:
            _fail("invalid view file digest")
        if not isinstance(entry["chunks"], list):
            _fail("invalid view chunk record")
    return files


def _verifier_source():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verifier.py")
    try:
        return _read_regular(path)
    except (OSError, ViewError) as error:
        raise ViewError("trusted verifier is unavailable") from error


def _check_tree(root, files):
    expected_files = set(files) | {"seal.json", "verify.py"}
    expected_dirs = set()
    for name in expected_files:
        parts = _safe_parts(name)
        for index in range(1, len(parts)):
            expected_dirs.add("/".join(parts[:index]))
    if expected_dirs & expected_files:
        _fail("conflicting sealed paths")

    actual_files = set()
    actual_dirs = set()
    pending = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise ViewError("view directory is unavailable") from error
        for entry in entries:
            relative = entry.name if not prefix else prefix + "/" + entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ViewError("view entry is unavailable") from error
            if stat.S_ISLNK(info.st_mode) or info.st_mode & 0o222:
                _fail("view contains a writable or linked entry")
            if stat.S_ISDIR(info.st_mode):
                actual_dirs.add(relative)
                pending.append((entry.path, relative))
            elif stat.S_ISREG(info.st_mode):
                actual_files.add(relative)
            else:
                _fail("view contains an unsupported entry")
    if actual_files != expected_files or actual_dirs != expected_dirs:
        _fail("view entries do not match its seal")

    for name, entry in files.items():
        content = _read_regular(os.path.join(root, *name.split("/")))
        if len(content) != entry["size"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
            _fail("sealed evidence changed")
    if _read_regular(os.path.join(root, "verify.py")) != _verifier_source():
        _fail("trusted verifier changed")


def verify_view(view, *, expected_identity):
    """Refuse any view with altered, missing, extra, writable, or linked entries."""
    try:
        root = os.path.abspath(os.fspath(view))
        info = os.lstat(root)
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o222:
            _fail("view root is not a read-only directory")
        raw = _read_regular(os.path.join(root, "seal.json"))
        files = _manifest(raw, expected_identity)
        _check_tree(root, files)
        return None
    except ViewError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise ViewError("invalid reviewer view") from error


def _remove_tree(path):
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
        return
    os.chmod(path, 0o700)
    for name in os.listdir(path):
        _remove_tree(os.path.join(path, name))
    os.rmdir(path)


def materialize(handle, *, expected_binding, expected_identity, reviewer_id, view_root):
    """Verify a seal and atomically materialize its immutable reviewer view."""
    try:
        # Verification is deliberately first: untrusted metadata is not used before this.
        verified = seal.verify(handle, expected_binding=expected_binding,
                               expected_identity=expected_identity)
    except Exception as error:
        raise ViewError("evidence seal verification failed") from error

    if not isinstance(reviewer_id, str) or _BINDING_ID.fullmatch(reviewer_id) is None:
        _fail("invalid reviewer id")
    try:
        root = os.path.abspath(os.fspath(view_root))
        root_info = os.lstat(root)
    except (OSError, TypeError, ValueError) as error:
        raise ViewError("view root is unavailable") from error
    if not stat.S_ISDIR(root_info.st_mode) or root_info.st_mode & 0o077:
        _fail("view root must be a private directory")

    identity = handle["identity"]
    destination = os.path.join(root, identity + "--" + reviewer_id)
    result = {"path": destination, "identity": identity}
    if os.path.lexists(destination):
        verify_view(destination, expected_identity=identity)
        return result

    try:
        raw_seal = _read_regular(os.path.join(os.path.abspath(os.fspath(handle["path"])), "seal.json"))
        files = verified["files"]
        verifier = _verifier_source()
        staging = tempfile.mkdtemp(prefix=".review-", dir=root)
    except (KeyError, OSError, TypeError, ValueError, ViewError) as error:
        raise ViewError("could not prepare reviewer view") from error

    try:
        for name in sorted(files):
            parts = _safe_parts(name)
            target = os.path.join(staging, *parts)
            os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
            content = seal.read(handle, name)
            with open(target, "xb") as output:
                output.write(content)
            os.chmod(target, 0o400)
        for name, content in (("seal.json", raw_seal), ("verify.py", verifier)):
            target = os.path.join(staging, name)
            with open(target, "xb") as output:
                output.write(content)
            os.chmod(target, 0o400)

        directories = []
        for current, names, _ in os.walk(staging):
            directories.append(current)
        for directory in reversed(directories):
            os.chmod(directory, 0o500)
        try:
            os.rename(staging, destination)
            staging = None
        except OSError:
            if not os.path.lexists(destination):
                raise
            verify_view(destination, expected_identity=identity)
            _remove_tree(staging)
            staging = None
        verify_view(destination, expected_identity=identity)
        return result
    except ViewError:
        raise
    except Exception as error:
        raise ViewError("could not materialize reviewer view") from error
    finally:
        if staging is not None and os.path.lexists(staging):
            try:
                _remove_tree(staging)
            except OSError:
                pass


def _command(command):
    if not isinstance(command, (list, tuple)) or not command:
        _fail("command must be a non-empty argument sequence")
    result = list(command)
    if any(not isinstance(arg, str) or "\x00" in arg for arg in result):
        _fail("command arguments must be strings")
    return result


def _base_argv(view, command, expected_identity, *, network, require_system_command=True):
    verify_view(view, expected_identity=expected_identity)
    command = _command(command)
    executable = command[0]
    if require_system_command and not executable.startswith(("/usr/", "/bin/")):
        _fail("command executable is outside the trusted runtime")
    bwrap = shutil.which("bwrap")
    if not bwrap:
        _fail("bubblewrap is unavailable")
    bwrap = os.path.abspath(bwrap)
    args = [bwrap, "--unshare-user", "--unshare-pid"]
    if network:
        args.append("--unshare-net")
    args.extend(["--die-with-parent", "--cap-drop", "ALL", "--clearenv"])
    for path in SYSTEM_BINDS:
        if os.path.exists(path):
            args.extend(["--ro-bind", path, path])
    args.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                 "--ro-bind", os.path.abspath(os.fspath(view)), "/review",
                 "--chdir", "/review", "--"])
    return args, command


def verifier_argv(view, command, *, expected_identity):
    """Build the sandbox command for trusted verifier code and isolated probes."""
    args, command = _base_argv(view, command, expected_identity, network=True)
    return args + command


def _container_path(path):
    """Normalize a container path after collapsing equivalent leading slashes."""
    return os.path.normpath("/" + path.lstrip("/"))


def _resolved_mount_source(source):
    try:
        resolved = Path(source).resolve(strict=False)
        home_path = Path.home().resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise ViewError("invalid reviewer mount source") from error
    if (
        resolved == Path(resolved.anchor)
        or resolved == home_path
        or resolved in home_path.parents
    ):
        _fail("reviewer mount source exposes the host home")
    return os.fspath(resolved)


def _mount_record(mount):
    if not isinstance(mount, (list, tuple)) or len(mount) != 3:
        _fail("invalid reviewer mount")
    source, target, writable = mount
    try:
        source = os.fspath(source)
    except TypeError as error:
        raise ViewError("invalid reviewer mount source") from error
    if not isinstance(source, str) or not os.path.isabs(source) or not os.path.exists(source):
        _fail("reviewer mount source must be an existing absolute path")
    source = _resolved_mount_source(source)
    if not isinstance(target, str) or "\x00" in target or not os.path.isabs(target):
        _fail("reviewer mount target must be absolute")
    pieces = target.split("/")
    if ".." in pieces:
        _fail("invalid reviewer mount target")
    normalized = _container_path(target)
    if normalized == "/" or normalized == "/review" or normalized.startswith("/review/"):
        _fail("reviewer mounts cannot overlap the evidence view")
    if not isinstance(writable, bool):
        _fail("invalid reviewer mount access mode")
    return os.path.abspath(source), normalized, writable


def reviewer_argv(view, command, *, expected_identity, mounts, home):
    """Build the connected reviewer sandbox with declared and resolver runtime mounts."""
    args, command = _base_argv(view, command, expected_identity, network=False,
                               require_system_command=False)
    try:
        records = [_mount_record(mount) for mount in mounts]
    except TypeError as error:
        raise ViewError("invalid reviewer mounts") from error
    if any(target == "/etc" or target.startswith("/etc/") for _, target, _ in records):
        _fail("reviewer mounts cannot expose other system configuration")
    for path in ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/ssl"):
        if os.path.exists(path):
            records.append((_resolved_mount_source(os.path.realpath(path)), path, False))
    if not isinstance(home, str) or "\x00" in home or not os.path.isabs(home):
        _fail("reviewer home must be an absolute container path")
    home_path = _container_path(home)
    if home_path == "/" or home_path == "/review" or home_path.startswith("/review/"):
        _fail("reviewer home cannot overlap the evidence view")

    executable = os.path.normpath(command[0])
    trusted = executable.startswith(("/usr/", "/bin/"))
    for _, target, _ in records:
        if executable == target or executable.startswith(target.rstrip("/") + "/"):
            trusted = True
    if not trusted:
        _fail("reviewer executable is outside its runtime mounts")

    insertion = args.index("--")
    args[insertion:insertion] = ["--setenv", "HOME", home_path]
    for source, target, writable in records:
        args[insertion + 3:insertion + 3] = ["--bind" if writable else "--ro-bind", source, target]
        insertion += 3
    return args + command


def check_isolation(view, *, expected_identity):
    """Run fail-closed probes for read-only evidence, hidden host home, and no network."""
    probe = (
        "import json, os, socket\n"
        "from pathlib import Path\n"
        "r = {}\n"
        "try:\n"
        " r['read'] = bool(Path('/review/seal.json').read_bytes())\n"
        "except OSError:\n"
        " r['read'] = False\n"
        "try:\n"
        " fd = os.open('/review/seal.json', os.O_WRONLY | os.O_APPEND)\n"
        " os.close(fd)\n"
        " r['write'] = True\n"
        "except OSError:\n"
        " r['write'] = False\n"
        "h = Path('/' + 'ho' + 'me')\n"
        "try:\n"
        " r['home'] = h.exists() and any(h.iterdir())\n"
        "except OSError:\n"
        " r['home'] = False\n"
        "s = socket.socket()\n"
        "s.settimeout(1)\n"
        "try:\n"
        " s.connect(('1.1.1.1', 53))\n"
        " r['net'] = True\n"
        "except OSError:\n"
        " r['net'] = False\n"
        "finally:\n"
        " s.close()\n"
        "print(json.dumps(r, sort_keys=True))\n"
    )
    try:
        argv = verifier_argv(view, [sys.executable, "-I", "-B", "-c", probe],
                             expected_identity=expected_identity)
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=False)
        result = json.loads(completed.stdout)
    except Exception as error:
        raise ViewError("reviewer isolation probe failed") from error
    if (completed.returncode != 0 or result != {"home": False, "net": False,
                                                  "read": True, "write": False}):
        _fail("reviewer isolation probe did not satisfy the contract")
