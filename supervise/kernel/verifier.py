# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Standalone integrity verifier for a materialized reviewer view."""

import hashlib
import json
import os
import re
import stat
import sys


_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _regular_file(path):
    """Read a regular file without following a final-component symlink."""
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("not a regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _safe_name(name):
    if not isinstance(name, str) or not name or "\x00" in name or name.startswith("/"):
        raise ValueError("invalid evidence name")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("invalid evidence name")
    return parts


def verify(view):
    view = os.path.abspath(os.fspath(view))
    root_info = os.lstat(view)
    if not stat.S_ISDIR(root_info.st_mode):
        raise ValueError("view is not a directory")
    raw = _regular_file(os.path.join(view, "seal.json"))
    record = json.loads(raw.decode("utf-8"))
    if not isinstance(record, dict) or _canonical(record) != raw:
        raise ValueError("seal is not canonical")
    identity = hashlib.sha256(raw).hexdigest()
    files = record.get("files")
    if (record.get("schema") != 1 or isinstance(record.get("schema"), bool) or
            not isinstance(files, dict)):
        raise ValueError("invalid seal record")

    allowed_files = {"seal.json", "verify.py"}
    allowed_dirs = set()
    for name, entry in files.items():
        parts = _safe_name(name)
        allowed_files.add(name)
        for count in range(1, len(parts)):
            allowed_dirs.add("/".join(parts[:count]))
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise ValueError("invalid file record")
        expected = entry["sha256"]
        if _HEX.fullmatch(expected) is None:
            raise ValueError("invalid file digest")
        current = view
        for part in parts[:-1]:
            current = os.path.join(current, part)
            info = os.lstat(current)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("invalid evidence directory")
        content = _regular_file(os.path.join(current, parts[-1]))
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("evidence digest mismatch")
        size = entry.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size != len(content):
            raise ValueError("evidence size mismatch")

    pending = [(view, "")]
    while pending:
        directory, prefix = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                relative = prefix + entry.name
                if prefix:
                    relative = prefix + "/" + entry.name
                info = os.lstat(entry.path)
                if stat.S_ISLNK(info.st_mode):
                    raise ValueError("symbolic links are not allowed")
                if stat.S_ISDIR(info.st_mode):
                    if relative not in allowed_dirs:
                        raise ValueError("unexpected evidence directory")
                    pending.append((entry.path, relative))
                elif stat.S_ISREG(info.st_mode):
                    if relative not in allowed_files:
                        raise ValueError("unexpected evidence file")
                else:
                    raise ValueError("unexpected evidence entry")
    return identity


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    try:
        if len(arguments) > 1:
            raise ValueError("expected at most one view path")
        view = arguments[0] if arguments else os.path.dirname(os.path.abspath(__file__))
        identity = verify(view)
        print("VERIFIED " + identity)
        return 0
    except Exception as error:
        message = str(error).replace("\n", " ").replace("\r", " ")
        print("FAILED " + (message or "verification failed"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
