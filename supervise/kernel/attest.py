# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Route-record attestation and receipt verification."""

import hashlib
import json
import os
import stat
from pathlib import Path
from collections.abc import Mapping


class AttestationError(ValueError):
    """Raised when an attestation chain is missing or inconsistent."""


def digest(value):
    """Return the SHA-256 digest of canonical UTF-8 JSON."""
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _controller_value(controller):
    # The portable route-record schema stores this field as a string, while
    # callers may provide structured controller identity data.
    if isinstance(controller, str):
        return controller
    return json.dumps(controller, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def route_record(*, mission, transaction, role, route_id, class_key, packet_id,
                 crew_path, crew_sha256, publication_sha256, contract_sha256,
                 decision, classification_sha256, controller, controller_path):
    """Build the stable route record used by a reservation."""
    return {
        "schema": "route-record/1",
        "mission": mission,
        "transaction": transaction,
        "role": role,
        "route": route_id,
        "class_key": class_key,
        "packet_id": packet_id,
        "crew_path": str(crew_path),
        "crew_sha256": crew_sha256,
        "publication_sha256": publication_sha256,
        "contract_sha256": contract_sha256,
        "decision_sha256": digest(decision),
        "classification_sha256": classification_sha256,
        "controller": _controller_value(controller),
        "controller_path": controller_path,
    }


def write_route_record(home, number, record):
    """Create a read-only route record without replacing an existing record."""
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        raise AttestationError("route number is invalid")
    path = Path(home) / ("route-%s.json" % number)
    try:
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(encoded)
                stream.flush()
            os.chmod(path, 0o444)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except FileExistsError as error:
        raise AttestationError("route record already exists") from error
    except (OSError, TypeError, ValueError) as error:
        raise AttestationError("could not write route record") from error
    return path


def _require(condition, check):
    if not condition:
        raise AttestationError("attestation check failed: " + check)


def _mapping(value, check):
    _require(isinstance(value, Mapping), check)
    return value


def _read_json_record(path, check):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise AttestationError("attestation check failed: " + check) from error
    return _mapping(value, check)


def receipt(path, *, execution, start, attempt_id):
    """Create a self-hashed receipt bound to a route record and attempt."""
    _require(isinstance(attempt_id, str) and bool(attempt_id), "attempt id")
    record = _read_json_record(path, "route record")
    values = {
        "mission": record.get("mission"),
        "transaction": record.get("transaction"),
        "role": record.get("role"),
        "route_id": record.get("route"),
        "class_key": record.get("class_key"),
        "packet_id": record.get("packet_id"),
        "publication_sha256": record.get("publication_sha256"),
        "crew_sha256": record.get("crew_sha256"),
        "path": str(path),
        "sha256": digest(record),
        "execution": execution,
        "start": start,
        "attempt_id": attempt_id,
    }
    values["receipt_sha256"] = digest(values)
    return values


def verify_receipt(home, start_event, finish_event):
    """Verify receipt, route record, reservation bindings, and crew content."""
    start_event = _mapping(start_event, "start event")
    finish_event = _mapping(finish_event, "finish event")
    attempt = _mapping(finish_event.get("attempt"), "attempt")
    attestation = _mapping(attempt.get("routing_attestation"), "routing attestation")

    claimed_receipt_hash = attestation.get("receipt_sha256")
    receipt_body = {key: value for key, value in attestation.items() if key != "receipt_sha256"}
    _require(isinstance(claimed_receipt_hash, str) and claimed_receipt_hash == digest(receipt_body),
             "receipt self-hash")

    start_number = start_event.get("start")
    _require(isinstance(start_number, int) and not isinstance(start_number, bool), "start number")
    expected_path = Path(home) / ("route-%s.json" % start_number)
    supplied_path = attestation.get("path")
    _require(isinstance(supplied_path, str), "record path")
    try:
        _require(Path(supplied_path) == expected_path, "record path")
        info = os.lstat(expected_path)
    except (OSError, TypeError, ValueError) as error:
        raise AttestationError("attestation check failed: record path") from error
    _require(stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode), "route record type")
    _require(not (info.st_mode & 0o222), "route record read-only")
    record = _read_json_record(expected_path, "route record")
    _require(attestation.get("sha256") == digest(record), "route record digest")

    fields = (
        ("mission", "mission"),
        ("transaction", "transaction"),
        ("role", "role"),
        ("route_id", "route"),
        ("class_key", "class_key"),
        ("packet_id", "packet_id"),
        ("publication_sha256", "publication_sha256"),
        ("crew_sha256", "crew_sha256"),
    )
    for receipt_field, record_field in fields:
        _require(attestation.get(receipt_field) == record.get(record_field),
                 "record field " + receipt_field)

    _require(attestation.get("transaction") == start_event.get("unit_id"), "transaction")
    try:
        expected_decision_hash = digest(start_event["decision"])
    except (KeyError, TypeError, ValueError) as error:
        raise AttestationError("attestation check failed: decision") from error
    _require(record.get("decision_sha256") == expected_decision_hash, "decision")
    _require(attestation.get("route_id") == start_event.get("route"), "route")
    _require(attestation.get("packet_id") == start_event.get("packet_id"), "packet id")
    _require(attestation.get("start") == start_number, "start")
    _require(attestation.get("execution") == start_event.get("execution"), "execution")
    _require(attestation.get("attempt_id") == attempt.get("attempt_id"), "attempt id")

    crew_path = record.get("crew_path")
    _require(isinstance(crew_path, str) and bool(crew_path), "crew path")
    crew = _read_json_record(crew_path, "crew file")
    crew_body = dict(crew)
    crew_claim = crew_body.pop("crew_sha256", None)
    crew_hash = digest(crew_body)
    _require(crew_claim == record.get("crew_sha256") == crew_hash, "crew hash")
    return dict(attestation)
