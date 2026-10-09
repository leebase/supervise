# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Locked, append-only accounting for a single unit's parent journal."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path

from supervise.schemas import validate


WORKER_ENV = "SUPERVISE_PARENT_START"
_SCHEMAS = {
    "start": "state-start", "finish": "state-finish",
    "reconcile": "state-reconcile", "outcome_closed": "state-outcome-closed",
}


class JournalError(ValueError):
    """A journal operation was refused without appending a record."""


def _require(condition, message):
    if not condition:
        raise JournalError(message)


def _number(value, *, positive=False):
    try:
        valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                 and math.isfinite(value) and (value > 0 if positive else value >= 0))
    except OverflowError:
        valid = False
    _require(valid, "expected a finite %s number" % ("positive" if positive else "nonnegative"))


def _index(value):
    _require(type(value) is int and value > 0, "expected a positive integer")


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        _require(parsed.utcoffset() is not None, "timestamp must have an offset")
        return parsed
    except (TypeError, ValueError) as error:
        raise JournalError("invalid journal timestamp") from error


def _start_arguments(event):
    _number(event.get("timeout"), positive=True)
    _require(event.get("role") in ("impl", "review"), "invalid start role")
    _require(isinstance(event.get("attempt_id"), str) and bool(event["attempt_id"]),
             "attempt_id must be a non-empty string")
    _timestamp(event["reserved_at"])


def _invalid_constant(value):
    raise JournalError("nonfinite JSON constant: " + value)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def _read(path):
    events = []
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                _require(line.endswith("\n"), "incomplete journal line")
                event = json.loads(line, parse_constant=_invalid_constant,
                                   object_pairs_hook=_object)
                _require(isinstance(event, dict), "journal line must be an object")
                events.append(event)
    except FileNotFoundError:
        return []
    except (UnicodeError, json.JSONDecodeError) as error:
        raise JournalError("invalid journal JSON") from error
    return events


def _state(events, unit_id):
    starts, accounted, closed = {}, {}, False
    for event in events:
        _require(event.get("unit_id") == unit_id, "journal belongs to another unit")
        kind = event.get("kind")
        _require(isinstance(kind, str) and kind in _SCHEMAS, "unknown journal event")
        errors = validate(_SCHEMAS[kind], event)
        _require(not errors, "invalid journal record: " + "; ".join(errors))
        _require(not closed, "event follows closed outcome")
        if kind == "start":
            _index(event["start"])
            _require(event["start"] == len(starts) + 1, "invalid start sequence")
            _require(len(starts) == len(accounted), "earlier start is unaccounted")
            _start_arguments(event)
            starts[event["start"]] = event
        elif kind in ("finish", "reconcile"):
            _index(event["start"])
            _require(event["start"] in starts, "start was never reserved")
            _require(event["start"] not in accounted, "start is already accounted")
            _require(event["accounted"] is True, "event must account the start")
            _number(event["seconds"])
            if kind == "finish":
                _timestamp(event["controller_finished_at"])
            else:
                _require(event["attempt"] is None and event["usage"] == "unknown"
                         and event["outcome"] == "unknown", "invalid reconciliation")
            accounted[event["start"]] = event
        else:
            _require(bool(starts) and len(starts) == len(accounted),
                     "closure requires every start accounted")
            _require(type(event["completed_starts"]) is int
                     and event["completed_starts"] == len(starts), "invalid closure count")
            _number(event["cumulative_seconds"])
            _require(event["cumulative_seconds"] == _total(accounted), "invalid closure total")
            _timestamp(event["observed_at"])
            closed = True
    return starts, accounted, closed


def _total(accounted):
    try:
        total = math.fsum(event["seconds"] for event in accounted.values())
    except OverflowError as error:
        raise JournalError("cumulative seconds overflow") from error
    _number(total)
    return total


@contextmanager
def _locked(path, unit_id=None, *, writing=False):
    path = Path(path)
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if writing:
                _require(WORKER_ENV not in os.environ, "workers cannot write the parent journal")
            events = _read(path)
            if unit_id is None and events:
                unit_id = events[0].get("unit_id")
            _require(isinstance(unit_id, str) or (unit_id is None and not events),
                     "unit_id must be a string")
            state = _state(events, unit_id)
            yield path, events, state
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _append(path, events, event):
    # Validate the complete transition and serialize before opening the journal.
    _state(events + [event], event["unit_id"])
    try:
        data = (json.dumps(event, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise JournalError("record is not JSON serializable") from error
    with path.open("ab") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return event


def read_events(path):
    """Read validated events while holding the journal's exclusive lock."""
    with _locked(path) as (_, events, _):
        return events


def summary(path, unit_id):
    """Return reservation counts and cumulative accounted time for a unit."""
    with _locked(path, unit_id) as (_, _, (starts, accounted, closed)):
        return {"starts": len(starts), "accounted": len(accounted),
                "cumulative_seconds": _total(accounted), "closed": closed}


def reserve_start(path, unit_id, *, route, packet_id, role, timeout, attempt_id,
                  execution, decision, max_starts):
    """Reserve the next start before launching its worker."""
    with _locked(path, unit_id, writing=True) as (path, events, (starts, accounted, closed)):
        _require(not closed, "unit is closed")
        _require(len(starts) == len(accounted), "earlier start is unaccounted")
        _index(max_starts)
        number = len(starts) + 1
        _require(number <= max_starts, "start limit exceeded")
        _append(path, events, {"kind": "start", "unit_id": unit_id, "start": number,
                "route": route, "packet_id": packet_id, "role": role, "timeout": timeout,
                "attempt_id": attempt_id, "reserved_at": datetime.now(timezone.utc).isoformat(),
                "execution": execution, "decision": decision})
        return number


def _unfinished(state, start):
    starts, accounted, closed = state
    _require(not closed, "unit is closed")
    _index(start)
    _require(start in starts, "start was never reserved")
    _require(start not in accounted, "start is already accounted")
    return starts[start]


def finish_start(path, unit_id, start, *, attempt_id, seconds, attempt):
    """Account the controller's measured runtime exactly once."""
    with _locked(path, unit_id, writing=True) as (path, events, state):
        reservation = _unfinished(state, start)
        _require(attempt_id == reservation["attempt_id"], "attempt_id differs from reservation")
        _number(seconds)
        return _append(path, events, {"kind": "finish", "unit_id": unit_id, "start": start,
                       "accounted": True, "seconds": seconds, "attempt": attempt,
                       "controller_finished_at": datetime.now(timezone.utc).isoformat()})


def reconcile_start(path, unit_id, start, *, evidence):
    """Account a stopped worker using the entire interval since reservation."""
    with _locked(path, unit_id, writing=True) as (path, events, state):
        reservation = _unfinished(state, start)
        now = datetime.now(timezone.utc)
        seconds = (now - _timestamp(reservation["reserved_at"])).total_seconds()
        _number(seconds)  # Refuse clock rollback rather than undercounting time.
        return _append(path, events, {"kind": "reconcile", "unit_id": unit_id, "start": start,
                       "accounted": True, "seconds": seconds, "attempt": None,
                       "usage": "unknown", "outcome": "unknown",
                       "time_basis": "reserved_at_to_reconciled_at",
                       "reconciled_at": now.isoformat(), "evidence": evidence})


def close_outcome(path, unit_id, *, decision):
    """Close a unit only after all of its starts have been accounted."""
    with _locked(path, unit_id, writing=True) as (path, events, (starts, accounted, closed)):
        _require(not closed, "unit is already closed")
        _require(bool(starts) and len(starts) == len(accounted),
                 "closure requires at least one start and every start accounted")
        return _append(path, events, {"kind": "outcome_closed", "unit_id": unit_id,
                       "completed_starts": len(starts), "cumulative_seconds": _total(accounted),
                       "decision": decision, "observed_at": datetime.now(timezone.utc).isoformat()})
