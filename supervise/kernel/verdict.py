# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Verdict extraction from the final response and captured output."""

import re


_ACCEPT = "REVIEW VERDICT: ACCEPT"
_REJECT = "REVIEW VERDICT: REJECT"
_MARKER = re.compile(r"REVIEW VERDICT: (ACCEPT|REJECT)(?!\w)")


def parse_verdict(final_text, stdout_bytes):
    """Return the last final-line verdict only when it ends the line and is captured."""
    if not isinstance(final_text, str) or not isinstance(stdout_bytes, (bytes, bytearray)):
        return None
    final_line = next((line for line in reversed(final_text.splitlines()) if line.strip()), None)
    if final_line is None:
        return None
    matches = list(_MARKER.finditer(final_line))
    if not matches:
        return None
    match = matches[-1]
    if re.fullmatch(r"[\W\s]*", final_line[match.end():]) is None:
        return None
    selected = match.group(1)
    marker = _ACCEPT if selected == "ACCEPT" else _REJECT
    result = "accept" if selected == "ACCEPT" else "reject"
    try:
        captured = bytes(stdout_bytes)
        marker_bytes = marker.encode("utf-8")
    except (TypeError, UnicodeError):
        return None
    return result if marker_bytes in captured else None
