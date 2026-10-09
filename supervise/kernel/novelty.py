# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Content snapshots for owned source evidence."""

import hashlib
import os
from pathlib import Path, PureWindowsPath
import stat


def _owned_path(value):
    try:
        raw = os.fspath(value)
    except TypeError as error:
        raise ValueError("owned path must be path-like") from error
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("owned path must be a non-empty relative path")
    path = Path(raw)
    windows_path = PureWindowsPath(raw)
    components = raw.replace("\\", "/").split("/")
    if path.is_absolute() or windows_path.is_absolute() or windows_path.drive or ".." in components:
        raise ValueError("unsafe owned path")
    return path


def _file_sha256(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def snapshot(project, owned_paths):
    """Hash only regular files reached through the declared relative paths."""
    root = Path(project)
    artifacts = {}
    for owned in owned_paths:
        relative = _owned_path(owned)
        target = root / relative
        try:
            info = os.lstat(target)
        except OSError as error:
            raise ValueError("owned path is unavailable") from error
        prefix = relative.as_posix().rstrip("/")
        if not prefix:
            raise ValueError("owned path must name a file or directory")
        if "__pycache__" in relative.parts:
            continue
        if stat.S_ISREG(info.st_mode):
            if "__pycache__" not in relative.parts:
                artifacts[prefix] = _file_sha256(target)
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("owned path is not a regular file or directory")

        for directory, dirnames, filenames in os.walk(target, followlinks=False):
            dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
            for filename in sorted(filenames):
                file_path = Path(directory) / filename
                try:
                    file_info = os.lstat(file_path)
                except OSError as error:
                    raise ValueError("owned file is unavailable") from error
                if not stat.S_ISREG(file_info.st_mode):
                    continue
                relpath = file_path.relative_to(root).as_posix()
                artifacts[relpath] = _file_sha256(file_path)
    return {"status": "VALID", "artifacts": dict(sorted(artifacts.items()))}


def review_allowed(previous, current):
    """A review is warranted for the first snapshot or changed owned evidence."""
    if previous is None:
        return True
    previous_artifacts = previous.get("artifacts") if isinstance(previous, dict) else None
    current_artifacts = current.get("artifacts") if isinstance(current, dict) else None
    return previous_artifacts != current_artifacts
