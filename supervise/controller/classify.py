# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Classify packets; this is not equivalent to production task classes."""

import re
from pathlib import PurePosixPath
from typing import Iterable

from supervise.backend import TaskClass


_SIZE_RE = re.compile(r"^(\d+)\s+files?,\s*(\d+)\s+changed\s+lines$", re.IGNORECASE)
_LANGUAGES = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".sh": "shell",
    ".sql": "sql",
    ".md": "markdown",
    ".json": "yaml-config",
    ".yaml": "yaml-config",
    ".yml": "yaml-config",
    ".toml": "yaml-config",
}


def _structural_value(lines: list[str], name: str, *, required: bool = True) -> str | None:
    prefix = name + ":"
    values = [line.split(":", 1)[1].strip() for line in lines if line.lstrip().startswith(prefix)]
    if not values:
        if required:
            raise ValueError(f"missing {name} line")
        return None
    if len(values) != 1:
        raise ValueError(f"multiple {name} lines")
    return values[0]


def _clean_path(value: object) -> tuple[str, bool]:
    raw = str(value).strip()
    if not raw or "\\" in raw or "\x00" in raw:
        raise ValueError("invalid owned path")
    is_directory = raw.endswith("/")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid owned path")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts:
        raise ValueError("invalid owned path")
    return "/".join(parts), is_directory


def _is_admitted(path: str, admitted: list[tuple[str, bool]]) -> bool:
    for allowed, is_directory in admitted:
        if is_directory:
            if path.startswith(allowed + "/"):
                return True
        elif path == allowed:
            return True
    return False


def classify(packet_text: str, owned_paths: Iterable[str], *, role: str) -> TaskClass:
    """Return a stable task class based on structural metadata and admitted files."""
    if role not in ("impl", "review"):
        raise ValueError("role must be impl or review")

    lines = str(packet_text).splitlines()
    kind = _structural_value(lines, "Kind")
    if not kind:
        raise ValueError("empty Kind line")
    owned_text = _structural_value(lines, "Owned paths")
    size_text = _structural_value(lines, "Declared size")
    size_match = _SIZE_RE.fullmatch(size_text or "")
    if size_match is None:
        raise ValueError("malformed Declared size line")
    file_count, changed_lines = (int(value) for value in size_match.groups())

    try:
        raw_admitted = [owned_paths] if isinstance(owned_paths, (str, bytes)) else list(owned_paths)
    except TypeError as error:
        raise ValueError("invalid admitted owned paths") from error
    admitted = [_clean_path(path) for path in raw_admitted]

    packet_paths = [item.strip() for item in (owned_text or "").split(",")]
    if not packet_paths or any(not item for item in packet_paths):
        raise ValueError("empty Owned paths line")

    languages = set()
    for raw_path in packet_paths:
        path, is_directory = _clean_path(raw_path)
        if is_directory:
            raise ValueError("owned path is a directory, not a file")
        if not _is_admitted(path, admitted):
            raise ValueError(f"{path} is outside frozen ownership")
        suffix = PurePosixPath(path).suffix.lower()
        language = _LANGUAGES.get(suffix)
        if language is None:
            raise ValueError(f"unknown owned-file extension: {suffix or '(none)'}")
        languages.add(language)

    if file_count <= 1 and changed_lines <= 20:
        size = "xs"
    # Frozen contract requires 6 files/100 lines to be s, overriding prose's <=3 bound.
    elif file_count <= 6 and changed_lines <= 150:
        size = "s"
    elif file_count <= 10 and changed_lines <= 600:
        size = "m"
    else:
        size = "l"

    oracle_line = _structural_value(lines, "Oracle", required=False)
    oracle = "judge" if role == "review" or not oracle_line else "deterministic"
    ordered_languages = tuple(sorted(languages))
    key = f"{role}/{oracle}/{size}/" + "+".join(ordered_languages)
    return TaskClass(role=role, oracle=oracle, size=size, languages=ordered_languages, key=key)
