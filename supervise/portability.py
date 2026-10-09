# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Scan UTF-8 source trees for private, host-specific portability tokens."""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path


FORBIDDEN = (
    "/ho" + "me/",
    "lee" + "_llm_router",
    "lee" + "-llm-router",
    "leebase" + "home",
    "chief" + "-of-staff",
    "192" + ".168.",
    "/usr/bin/" + "python3",
)

_EXCLUDED = frozenset({".git", ".private", "__pycache__", ".venv"})


@dataclass(frozen=True)
class Finding:
    """One forbidden token on a line, with a root-relative POSIX path."""

    path: str
    line: int
    token: str


def scan(root: str | os.PathLike[str]) -> list[Finding]:
    """Return sorted findings, excluding development directories and binary files."""
    root = Path(root)
    findings = []
    for directory, directories, filenames in os.walk(root):
        if Path(directory).name in _EXCLUDED:
            directories.clear()
            continue
        directories[:] = [name for name in directories if name not in _EXCLUDED]
        for filename in filenames:
            path = Path(directory) / filename
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if "\x00" in content:
                continue
            relative = path.relative_to(root).as_posix()
            for line_number, line in enumerate(content.splitlines(), start=1):
                if "portability: allow" in line:
                    continue
                for token in FORBIDDEN:
                    if token in line:
                        findings.append(Finding(relative, line_number, token))
    return sorted(findings, key=lambda finding: (finding.path, finding.line, finding.token))


def main(argv: list[str] | None = None) -> int:
    """Print findings for each requested root and return a failing status if found."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    args = parser.parse_args(argv)
    status = 0
    for root in args.roots:
        for finding in scan(root):
            print(f"{finding.path}:{finding.line}: {finding.token}")
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
