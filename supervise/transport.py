# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Portable process-manager transport helpers."""

import hashlib
import json
import math
import os
from pathlib import Path
import time


MANAGER_ERROR_TURNS = 2
MANAGER_STALL_GRACE_SECONDS = 20.0


class ManagerWatch:
    """Track manager errors and detect a manager that has stopped progressing.

    A completed agent turn means a manager turn is expected.  While waiting,
    the manager's small ledger is polled afresh so changes made after the
    event are not missed.  Ledger failures are deliberately treated as
    inconclusive rather than as evidence of a stall.
    """

    def __init__(self, home, *, clock=time.monotonic):
        self.home = Path(home)
        self._clock = clock
        self._error_count = 0
        self._awaiting_turn = False
        self._last_ledger = None
        self._stall_since = None

    @staticmethod
    def _error_message(event):
        message = event.get("message")
        if not isinstance(message, dict):
            return "manager turn failed"
        detail = message.get("errorMessage")
        return detail if isinstance(detail, str) else "manager turn failed"

    def _read_ledger(self):
        try:
            with (self.home / "manager-turns.json").open("r", encoding="utf-8") as stream:
                ledger = json.load(stream)
        except (OSError, UnicodeError, ValueError, TypeError):
            return None
        if not isinstance(ledger, dict):
            return None
        values = (ledger.get("turns"), ledger.get("version"), ledger.get("idle"))
        if any(type(value) is not int or value < 0 for value in values):
            return None
        return tuple(values)

    @staticmethod
    def _is_stalled_state(ledger):
        turns, _version, idle = ledger
        return idle >= 2 or turns >= 8

    def observe(self, event):
        """Observe one manager event; return a fault tuple when fatal."""
        if not isinstance(event, dict):
            return None
        event_type = event.get("type")
        if event_type == "turn_start":
            self._awaiting_turn = False
            self._last_ledger = None
            self._stall_since = None
            return None

        if event_type == "turn_end":
            message = event.get("message")
            is_error = isinstance(message, dict) and message.get("stopReason") == "error"
            if is_error:
                self._error_count += 1
                if self._error_count >= MANAGER_ERROR_TURNS:
                    return "MANAGER_ERROR", self._error_message(event)
            else:
                self._error_count = 0
            self._awaiting_turn = False
            self._last_ledger = None
            self._stall_since = None
            return None

        if event_type == "agent_end":
            self._awaiting_turn = True
            ledger = self._read_ledger()
            self._last_ledger = ledger
            self._stall_since = (
                self._clock()
                if ledger is not None and self._is_stalled_state(ledger)
                else None
            )
        return None

    def stalled(self):
        """Return a manager-stall fault after the grace interval, if warranted."""
        if not self._awaiting_turn:
            return None

        ledger = self._read_ledger()
        if ledger is None:
            self._last_ledger = None
            self._stall_since = None
            return None

        now = self._clock()
        previous = self._last_ledger
        self._last_ledger = ledger
        if not self._is_stalled_state(ledger):
            self._stall_since = None
            return None

        # A manager version change is progress.  If it coincides with an
        # exhausted/idle state, allow a complete grace period from that update.
        if previous is not None and previous[1] != ledger[1]:
            self._stall_since = now
        elif self._stall_since is None:
            self._stall_since = now

        if now - self._stall_since >= MANAGER_STALL_GRACE_SECONDS:
            return "MANAGER_STALLED", json.dumps(
                {"turns": ledger[0], "version": ledger[1], "idle": ledger[2]},
                sort_keys=True,
            )
        return None


def state_root():
    """Resolve the user-configurable state directory."""
    configured = os.environ.get("SUPERVISE_STATE")
    if configured:
        root = Path(configured)
        if not root.is_absolute():
            raise ValueError("SUPERVISE_STATE must be an absolute path")
        return root

    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "supervise"
    return Path.home() / ".local" / "state" / "supervise"


def invocation_path(root, project, plan):
    """Return a stable per-project/per-plan invocation directory."""
    identity = os.fspath(project) + "\0" + os.fspath(plan)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return Path(root) / digest


def validate_window(seconds):
    """Validate a finite host timeout in the supported one-hour-scale range."""
    if isinstance(seconds, bool):
        raise ValueError("window must be between 1 and 7200 seconds")
    try:
        valid = math.isfinite(seconds) and 1 <= seconds <= 7200
    except (TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        raise ValueError("window must be between 1 and 7200 seconds")
    return seconds


def make_receipt(*, status, admission_phase, native_starts, invocation,
                 began_at, ended_at, manager_fault):
    """Build the public invocation delivery receipt."""
    return {
        "status": status,
        "admission_phase": admission_phase,
        "native_starts": native_starts,
        "delivery": "DELIVERED" if status == "ACCEPTED" else "UNDELIVERED",
        "invocation": os.fspath(invocation),
        "began_at": began_at,
        "ended_at": ended_at,
        "manager_fault": manager_fault,
    }
