# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Offline verification of a completed supervise invocation."""

import json
import math
import re
from pathlib import Path

from supervise import schemas
from supervise.kernel import attest, seal


_CHECK_NAMES = (
    "journal-schema",
    "starts-accounted",
    "closure",
    "receipts",
    "crew-files",
    "accepted-review",
    "review-independence",
    "review-snapshot",
    "sealed-evidence",
    "acceptance-observation",
)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("invalid JSON constant: " + value)


def _load(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=_object, parse_constant=_constant)


def _same_json(left, right):
    """Compare JSON values without treating booleans as integers."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json(a, b) for a, b in zip(left, right)
        )
    return left == right


def _read_journal(path):
    """Return parsed records and a schema result, retaining valid lines on errors."""
    events = []
    errors = []
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        return events, False, "cannot read STATE.jsonl: " + str(error)

    lines = data.splitlines(keepends=True)
    if not lines:
        errors.append("journal is empty")
    for number, raw in enumerate(lines, 1):
        if not raw.endswith(b"\n"):
            errors.append("line %d is incomplete" % number)
        try:
            text = raw.decode("utf-8")
            event = json.loads(text, object_pairs_hook=_object, parse_constant=_constant)
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            errors.append("line %d is not valid JSON: %s" % (number, error))
            events.append(None)
            continue
        if not isinstance(event, dict):
            errors.append("line %d is not an object" % number)
            events.append(None)
            continue
        kind = event.get("kind")
        schema_name = {
            "start": "state-start",
            "finish": "state-finish",
            "reconcile": "state-reconcile",
            "outcome_closed": "state-outcome-closed",
        }.get(kind)
        if schema_name is None:
            errors.append("line %d has an unknown event kind" % number)
        else:
            problems = schemas.validate(schema_name, event)
            if problems:
                errors.append("line %d: %s" % (number, "; ".join(problems)))
        events.append(event)
    return events, not errors, "; ".join(errors) if errors else "all journal records are schema-valid"


def _accounting(events):
    starts = {}
    counts = {}
    errors = []
    if any(event is None for event in events):
        errors.append("journal contains an unreadable record")
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        number = event.get("start")
        if kind == "start":
            if type(number) is not int or number <= 0:
                errors.append("start has an invalid number")
                continue
            if number in starts:
                errors.append("start %d is reserved more than once" % number)
            starts.setdefault(number, event)
        elif kind in ("finish", "reconcile"):
            if type(number) is not int or number <= 0:
                errors.append("accounting record has an invalid start")
                continue
            counts[number] = counts.get(number, 0) + 1
            if number not in starts:
                errors.append("start %d has no reservation" % number)
    for number in starts:
        if counts.get(number, 0) != 1:
            errors.append("start %d is accounted %d times" % (number, counts.get(number, 0)))
    if not starts:
        errors.append("there are no starts")
    return starts, counts, not errors, "; ".join(errors) if errors else "every start is accounted exactly once"


def _read_routes_and_crews(home):
    """Load expected route records and sealed crew records without changing files."""
    routes, crews, errors = {}, {}, []
    for path in sorted(home.glob("route-*.json")):
        if re.fullmatch(r"route-refused-prestart-[0-9a-f]{32}\.json", path.name):
            continue
        match = re.fullmatch(r"route-(\d+)\.json", path.name)
        if not match:
            errors.append("invalid route record filename: " + path.name)
            continue
        number = int(match.group(1))
        try:
            value = _load(path)
            if not isinstance(value, dict):
                raise ValueError("record is not an object")
            routes[number] = value
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            errors.append("cannot read %s: %s" % (path.name, error))

    for path in sorted(home.glob("crew-*.json")):
        match = re.fullmatch(r"crew-(\d+)-([0-9a-f]{64})\.json", path.name)
        if not match:
            errors.append("invalid crew filename: " + path.name)
            continue
        try:
            value = _load(path)
            if not isinstance(value, dict):
                raise ValueError("record is not an object")
            claim = value.get("crew_sha256")
            body = dict(value)
            body.pop("crew_sha256", None)
            actual = attest.digest(body)
            if claim != actual or match.group(2) != actual:
                errors.append("crew digest does not match filename: " + path.name)
            problems = schemas.validate("crew", value)
            if problems:
                errors.append("invalid crew schema in %s: %s" % (path.name, "; ".join(problems)))
            crews[str(path.resolve())] = value
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            errors.append("cannot verify %s: %s" % (path.name, error))

    # Route records bind to crew files by absolute path and digest. Include those
    # references in this check so removing a sealed crew is visible here too.
    for record in routes.values():
        crew_path = record.get("crew_path")
        crew_hash = record.get("crew_sha256")
        if not isinstance(crew_path, str) or not isinstance(crew_hash, str):
            errors.append("route record has no crew binding")
            continue
        try:
            candidate = Path(crew_path)
            crew = _load(candidate)
            body = dict(crew) if isinstance(crew, dict) else {}
            claim = body.pop("crew_sha256", None)
            if claim != crew_hash or attest.digest(body) != crew_hash:
                errors.append("route crew binding is invalid")
            crews[str(candidate.resolve())] = crew
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            errors.append("route crew file is unavailable: " + str(error))
    return routes, crews, not errors, "; ".join(errors) if errors else "all named crew files match their sealed digests"


def _crew_for_route(home, routes, number):
    record = routes.get(number)
    if not isinstance(record, dict):
        raise ValueError("missing route record for start %d" % number)
    path = record.get("crew_path")
    if not isinstance(path, str):
        raise ValueError("route has no crew path")
    crew = _load(path)
    if not isinstance(crew, dict):
        raise ValueError("crew record is not an object")
    if Path(path).resolve().parent != home.resolve():
        raise ValueError("crew record is outside the invocation")
    body = dict(crew)
    claim = body.pop("crew_sha256", None)
    if claim != record.get("crew_sha256") or attest.digest(body) != claim:
        raise ValueError("crew record does not match its route")
    return record, crew


def _accepted_review(home):
    try:
        value = _load(home / "accepted-review.json")
        if not isinstance(value, dict):
            raise ValueError("accepted review is not an object")
        return value, None
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        return None, str(error)


def _sealed_evidence(home, events):
    """Recheck each evidence store against independently derived journal bindings."""
    errors, checked = [], 0
    try:
        pending = _load(home / "pending.json")
        mission = pending["invocation_id"]
    except (OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
        return False, "cannot read evidence mission: " + str(error)
    for event in events:
        if not isinstance(event, dict) or event.get("kind") != "finish":
            continue
        attempt = event.get("attempt")
        if not isinstance(attempt, dict) or attempt.get("evidence_handle") is None:
            continue
        checked += 1
        try:
            handle = attempt["evidence_handle"]
            if not isinstance(handle, dict):
                raise ValueError("evidence handle is not an object")
            identity = handle.get("identity")
            if (not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None
                    or identity != attempt.get("view_identity")):
                raise ValueError("evidence identity differs from the review view")
            location = home / "evidence" / identity
            if handle.get("path") != str(location) or location.resolve() != location:
                raise ValueError("evidence handle points outside its invocation store")
            author_number = attempt.get("author_start")
            if type(author_number) is not int:
                raise ValueError("reviewed author start is invalid")
            authors = [item for item in events if isinstance(item, dict)
                       and item.get("kind") == "start" and item.get("start") == author_number
                       and item.get("role") == "impl"]
            finishes = [item for item in events if isinstance(item, dict)
                        and item.get("kind") == "finish" and item.get("start") == author_number]
            if len(authors) != 1 or len(finishes) != 1:
                raise ValueError("evidence has no unique completed author start")
            author, finish = authors[0], finishes[0]
            source = finish["attempt"]["source_snapshot"]
            if (not isinstance(source, dict) or event.get("unit_id") != author.get("unit_id")
                    or finish.get("unit_id") != author.get("unit_id")):
                raise ValueError("evidence author source or unit binding is invalid")
            binding = {"mission_id": mission, "transaction_id": author["unit_id"],
                       "worker_id": author["route"], "worker_role": "impl",
                       "attempt_id": author["attempt_id"], "source_id": attest.digest(source)}
            seal.verify(handle, expected_binding=binding, expected_identity=identity)
        except (OSError, UnicodeError, KeyError, TypeError, ValueError, RuntimeError) as error:
            errors.append("start %s sealed evidence failed: %s" % (event.get("start"), error))
    if not checked:
        errors.append("invocation has no sealed evidence")
    return not errors, "; ".join(errors) if errors else "all sealed evidence verifies against its journal bindings"


def _acceptance_observation(home, events):
    """Check the accepted author's recorded tests against the raw selectors."""
    try:
        accepted, error = _accepted_review(home)
        if accepted is None:
            raise ValueError("accepted review is unavailable: " + str(error))
        number = accepted.get("author_start")
        if type(number) is not int or number <= 0:
            raise ValueError("accepted author start is invalid")
        finishes = [event for event in events if isinstance(event, dict)
                    and event.get("kind") == "finish" and type(event.get("start")) is int
                    and event.get("start") == number]
        if len(finishes) != 1:
            raise ValueError("accepted author has no unique finish record")
        observation = finishes[0]["attempt"]["observation"]
        if not isinstance(observation, dict) or observation.get("complete") is not True:
            raise ValueError("accepted author test observation is incomplete")
        for field in ("missing_frozen_ids", "unexpected_test_ids", "changed_frozen_test_inputs"):
            if observation.get(field) != []:
                raise ValueError("accepted author test observation has invalid " + field)
        outcomes = observation.get("outcomes")
        if not isinstance(outcomes, dict):
            raise ValueError("accepted author test outcomes are unavailable")
        acceptance = _load(home / "acceptance.json")
        if not isinstance(acceptance, list) or not acceptance:
            raise ValueError("acceptance records are unavailable")
        for item in acceptance:
            selectors = item.get("selectors") if isinstance(item, dict) else None
            if not isinstance(selectors, list) or not selectors:
                raise ValueError("acceptance selectors are unavailable")
            for selector in selectors:
                if not isinstance(selector, str) or not selector:
                    raise ValueError("acceptance selector is invalid")
                matches = [record for test_id, record in outcomes.items()
                           if test_id == selector or test_id.startswith(selector + ".")]
                if not matches:
                    raise ValueError("acceptance selector has no observed tests: " + selector)
                if any(not isinstance(record, dict) or record.get("outcome") != "PASS"
                       for record in matches):
                    raise ValueError("acceptance selector has non-passing tests: " + selector)
    except (OSError, UnicodeError, KeyError, TypeError, ValueError) as error:
        return False, "acceptance observation failed: " + str(error)
    return True, "accepted author observation is complete and every acceptance selector passed"


def verify_invocation(home):
    """Check a closed invocation using only its persisted evidence.

    The function deliberately reports every check even when earlier evidence is
    unreadable, so a damaged artifact cannot suppress an unrelated finding.
    """
    try:
        root = Path(home)
        if not root.is_dir():
            raise ValueError("invocation home is not a directory")
        root = root.resolve()
    except (OSError, TypeError, ValueError) as error:
        detail = "invalid invocation home: " + str(error)
        checks = [{"name": name, "ok": False, "detail": detail} for name in _CHECK_NAMES]
        return {"verdict": "FAIL", "checks": checks}

    events, journal_ok, journal_detail = _read_journal(root / "STATE.jsonl")
    starts, accounting_counts, accounted_ok, accounting_detail = _accounting(events)
    closed_events = [event for event in events if isinstance(event, dict)
                     and event.get("kind") == "outcome_closed"]
    try:
        closure_file = _load(root / "closure.json")
        closure_file_error = None
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        closure_file, closure_file_error = None, str(error)
    closure_event = closed_events[0] if len(closed_events) == 1 else None
    accounted_events = [event for event in events if isinstance(event, dict)
                        and event.get("kind") in ("finish", "reconcile")]
    try:
        expected_total = math.fsum(event["seconds"] for event in accounted_events)
        closure_consistent = (
            isinstance(closure_event, dict)
            and closure_event.get("completed_starts") == len(starts)
            and len(accounted_events) == len(starts)
            and closure_event.get("cumulative_seconds") == expected_total
            and events and events[-1] is closure_event
        )
    except (KeyError, TypeError, OverflowError, ValueError):
        closure_consistent = False
    closure_ok = (
        closure_consistent
        and isinstance(closure_file, dict)
        and not schemas.validate("closure", closure_file)
        and _same_json(closure_file, closure_event)
    )
    if closure_ok:
        closure_detail = "closure.json equals the unique, accounting-consistent outcome_closed record"
    elif closure_file_error:
        closure_detail = "closure.json is unavailable or invalid: " + closure_file_error
    else:
        closure_detail = "closure.json does not equal exactly one valid outcome_closed record"

    routes, crews, crews_ok, crews_detail = _read_routes_and_crews(root)
    receipts_ok = True
    receipt_errors = []
    for event in events:
        if not isinstance(event, dict) or event.get("kind") != "finish":
            continue
        number = event.get("start")
        matches = [item for item in events if isinstance(item, dict)
                   and item.get("kind") == "start" and item.get("start") == number]
        if len(matches) != 1:
            receipts_ok = False
            receipt_errors.append("finish has no unique matching start")
            continue
        try:
            attest.verify_receipt(root, matches[0], event)
            route_record = routes.get(number)
            if (not isinstance(route_record, dict)
                    or route_record.get("role") != matches[0].get("role")):
                raise ValueError("route role differs from reserved start")
        except Exception as error:
            receipts_ok = False
            receipt_errors.append("start %s receipt failed: %s" % (number, error))
    receipts_detail = "; ".join(receipt_errors) if receipt_errors else "every finish receipt verifies"

    accepted, accepted_error = _accepted_review(root)
    accepted_ok = False
    accepted_detail = "accepted review is missing or invalid"
    accepted_start = None
    accepted_finish = None
    if accepted is not None:
        review_number = accepted.get("review_start")
        author_number = accepted.get("author_start")
        if type(review_number) is int and type(author_number) is int:
            candidates = [event for event in events if isinstance(event, dict)
                          and event.get("kind") == "start" and event.get("start") == review_number]
            finishes = [event for event in events if isinstance(event, dict)
                        and event.get("kind") == "finish" and event.get("start") == review_number]
            author_starts = [event for event in events if isinstance(event, dict)
                             and event.get("kind") == "start" and event.get("start") == author_number
                             and event.get("role") == "impl"]
            if len(candidates) == len(finishes) == len(author_starts) == 1:
                accepted_start, accepted_finish = candidates[0], finishes[0]
                attempt = accepted_finish.get("attempt")
                if isinstance(attempt, dict):
                    close_decision = (closed_events[0].get("decision")
                                      if len(closed_events) == 1 else None)
                    close_accepted = (close_decision.get("accepted_review")
                                      if isinstance(close_decision, dict) else None)
                    accepted_ok = (
                        (close_accepted is None or _same_json(accepted, close_accepted))
                        and accepted.get("kind") == "accepted_review"
                        and accepted.get("unit_id") == accepted_start.get("unit_id")
                        and accepted_start.get("role") == "review"
                        and accepted_start.get("route") == accepted.get("reviewer")
                        and accepted_start.get("attempt_id") == accepted.get("attempt_id")
                        and attempt.get("attempt_id") == accepted.get("attempt_id")
                        and attempt.get("verdict") == "accept"
                        and attempt.get("failure_class") == "none"
                        and attempt.get("verifier_ok") is True
                        and attempt.get("author_start") == author_number
                        and attempt.get("source_snapshot") == accepted.get("source_snapshot")
                        and attempt.get("view_identity") == accepted.get("evidence_identity")
                        and isinstance(accepted.get("reviewer"), str)
                        and isinstance(accepted.get("source_snapshot"), dict)
                    )
                    if accepted_ok:
                        try:
                            route_record, crew = _crew_for_route(root, routes, review_number)
                            selected = crew.get("selected", {})
                            reviewer_routes = selected.get("reviewers", []) if isinstance(selected, dict) else []
                            accepted_ok = (
                                route_record.get("role") == "review"
                                and route_record.get("route") == accepted.get("reviewer")
                                and any(isinstance(item, dict)
                                        and item.get("route_id") == accepted.get("reviewer")
                                        for item in reviewer_routes)
                            )
                        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                            accepted_ok = False
        accepted_detail = ("accepted review binds to a completed accepting review start"
                           if accepted_ok else "accepted review does not bind to a completed accepting review start")
    elif accepted_error:
        accepted_detail = "accepted-review.json is unavailable or invalid: " + accepted_error

    independence_errors = []
    reviewer_family = None
    author_families = []
    if accepted_start is None:
        independence_errors.append("accepted review start cannot be identified")
    else:
        try:
            _, reviewer_crew = _crew_for_route(root, routes, accepted_start["start"])
            selected = reviewer_crew.get("selected", {})
            selected_reviewers = selected.get("reviewers", []) if isinstance(selected, dict) else []
            reviewer_id = accepted.get("reviewer") if accepted else None
            candidates = [item for item in selected_reviewers if isinstance(item, dict)
                          and item.get("route_id") == reviewer_id]
            if len(candidates) != 1 or not isinstance(candidates[0].get("family"), str):
                independence_errors.append("reviewer family is not sealed by its crew")
            else:
                reviewer_family = candidates[0]["family"]
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            independence_errors.append("reviewer crew cannot be verified: " + str(error))

    for number, start in starts.items():
        if start.get("role") != "impl":
            continue
        try:
            record, crew = _crew_for_route(root, routes, number)
            selected = crew.get("selected", {})
            author = selected.get("author") if isinstance(selected, dict) else None
            if (record.get("role") != "impl" or record.get("route") != start.get("route")
                    or not isinstance(author, dict)
                    or author.get("route_id") != start.get("route")
                    or not isinstance(author.get("family"), str)):
                raise ValueError("author family is not bound by its sealed crew")
            author_families.append(author["family"])
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            independence_errors.append("author start %d cannot be verified: %s" % (number, error))
    if reviewer_family is not None:
        if not author_families:
            independence_errors.append("there are no verifiable author families")
        elif any(reviewer_family == family for family in author_families):
            independence_errors.append("reviewer family matches an author family")
    independence_ok = not independence_errors
    independence_detail = "; ".join(independence_errors) if independence_errors else \
        "reviewer family differs from every author family"

    snapshot_ok = False
    snapshot_detail = "closure-time snapshot is unavailable"
    if accepted is not None and len(closed_events) == 1:
        close_decision = closed_events[0].get("decision")
        if isinstance(close_decision, dict):
            close_review = close_decision.get("accepted_review")
            last_snapshot = close_decision.get("last_completed_review_source")
            accepted_snapshot = accepted.get("source_snapshot")
            author_finishes = [event for event in events if isinstance(event, dict)
                               and event.get("kind") == "finish"
                               and event.get("start") == accepted.get("author_start")]
            author_attempt = (author_finishes[0].get("attempt")
                              if len(author_finishes) == 1 else None)
            snapshot_ok = (
                isinstance(close_review, dict)
                and _same_json(close_review.get("source_snapshot"), accepted_snapshot)
                and _same_json(last_snapshot, accepted_snapshot)
                and isinstance(author_attempt, dict)
                and isinstance(accepted_snapshot, dict)
                and _same_json(author_attempt.get("source_snapshot"), accepted_snapshot)
            )
            snapshot_detail = ("accepted review matches the author and closure-time source snapshots"
                               if snapshot_ok else "accepted review differs from the author or closure-time source snapshot")

    sealed_ok, sealed_detail = _sealed_evidence(root, events)
    observation_ok, observation_detail = _acceptance_observation(root, events)
    checks = [
        {"name": "journal-schema", "ok": journal_ok, "detail": journal_detail},
        {"name": "starts-accounted", "ok": accounted_ok, "detail": accounting_detail},
        {"name": "closure", "ok": closure_ok, "detail": closure_detail},
        {"name": "receipts", "ok": receipts_ok, "detail": receipts_detail},
        {"name": "crew-files", "ok": crews_ok, "detail": crews_detail},
        {"name": "accepted-review", "ok": accepted_ok, "detail": accepted_detail},
        {"name": "review-independence", "ok": independence_ok, "detail": independence_detail},
        {"name": "review-snapshot", "ok": snapshot_ok, "detail": snapshot_detail},
        {"name": "sealed-evidence", "ok": sealed_ok, "detail": sealed_detail},
        {"name": "acceptance-observation", "ok": observation_ok, "detail": observation_detail},
    ]
    return {"verdict": "PASS" if all(check["ok"] for check in checks) else "FAIL",
            "checks": checks}
