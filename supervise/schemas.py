# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Portable schemas and validation for supervised records.

The schemas intentionally permit additional record fields: deployed supervisors
attach operational metadata beyond the stable fields described here.
"""

from copy import deepcopy
import math


NAMES = (
    "state-start",
    "state-finish",
    "state-reconcile",
    "state-outcome-closed",
    "route-record",
    "crew",
    "attempt-outcome",
    "closure",
    "publication-manifest",
)


def _field(kind, **extra):
    result = {"type": kind}
    result.update(extra)
    return result


_SCHEMAS = {
    "state-start": {
        "type": "object",
        "required": ["kind", "unit_id", "start", "route", "packet_id", "reserved_at", "execution", "decision"],
        "properties": {
            "kind": _field("string", const="start"),
            "unit_id": _field("string"),
            "start": _field("integer"),
            "route": _field("string"),
            "packet_id": _field("string"),
            "reserved_at": _field("string"),
            "execution": _field("object"),
            "decision": _field("object"),
        },
    },
    "state-finish": {
        "type": "object",
        "required": ["kind", "unit_id", "start", "accounted", "seconds", "attempt", "controller_finished_at"],
        "properties": {
            "kind": _field("string", const="finish"),
            "unit_id": _field("string"),
            "start": _field("integer"),
            "accounted": _field("boolean"),
            "seconds": _field("number", minimum=0),
            "attempt": _field("object"),
            "controller_finished_at": _field("string"),
        },
    },
    "state-reconcile": {
        "type": "object",
        "required": ["kind", "unit_id", "start", "accounted", "seconds", "attempt", "usage", "outcome", "time_basis", "evidence"],
        "properties": {
            "kind": _field("string", const="reconcile"),
            "unit_id": _field("string"),
            "start": _field("integer"),
            "accounted": _field("boolean"),
            "seconds": _field("number", minimum=0),
            "attempt": _field(["object", "null"]),
            "usage": _field("string"),
            "outcome": _field("string"),
            "time_basis": _field("string"),
            "evidence": _field("string"),
        },
    },
    "state-outcome-closed": {
        "type": "object",
        "required": ["kind", "unit_id", "completed_starts", "cumulative_seconds", "decision", "observed_at"],
        "properties": {
            "kind": _field("string", const="outcome_closed"),
            "unit_id": _field("string"),
            "completed_starts": _field("integer"),
            "cumulative_seconds": _field("number", minimum=0),
            "decision": _field("object"),
            "observed_at": _field("string"),
        },
    },
    "route-record": {
        "type": "object",
        "required": ["schema", "mission", "transaction", "role", "route", "class_key", "packet_id", "crew_path", "crew_sha256", "publication_sha256", "contract_sha256", "decision_sha256", "classification_sha256", "controller", "controller_path"],
        "properties": {
            "schema": _field("string"),
            "mission": _field("string"),
            "transaction": _field("string"),
            "role": _field("string", enum=["impl", "review"]),
            "route": _field("string"),
            "class_key": _field("string"),
            "packet_id": _field("string"),
            "crew_path": _field("string"),
            "crew_sha256": _field("string"),
            "publication_sha256": _field("string"),
            "contract_sha256": _field("string"),
            "decision_sha256": _field("string"),
            "classification_sha256": _field("string"),
            "controller": _field("string"),
            "controller_path": _field("string"),
        },
    },
    "crew": {
        "type": "object",
        "required": ["schema", "crew_sha256", "selected", "excluded", "rationale", "cost_status", "created_at"],
        "properties": {
            "schema": _field("string"),
            "crew_sha256": _field("string"),
            "selected": _field("object"),
            "excluded": {"type": "array", "items": {"type": "object"}},
            "rationale": _field("string"),
            "cost_status": _field("string"),
            "created_at": _field("string"),
        },
    },
    "attempt-outcome": {
        "type": "object",
        "required": ["attempt_id", "verdict", "failure_class", "usage", "cost", "wall_clock_ms", "routing_attestation", "captured_at"],
        "properties": {
            "attempt_id": _field("string"),
            "verdict": _field("string"),
            "failure_class": _field(["string", "null"]),
            "usage": _field("object"),
            "cost": _field("object"),
            "wall_clock_ms": _field("number", minimum=0),
            "routing_attestation": _field("object"),
            "captured_at": _field("string"),
        },
    },
    "closure": {
        "type": "object",
        "required": ["kind", "unit_id", "completed_starts", "cumulative_seconds", "decision", "observed_at"],
        "properties": {
            "kind": _field("string", const="outcome_closed"),
            "unit_id": _field("string"),
            "completed_starts": _field("integer"),
            "cumulative_seconds": _field("number", minimum=0),
            "decision": _field("object"),
            "observed_at": _field("string"),
        },
    },
    "publication-manifest": {
        "type": "object",
        "required": ["release", "version", "state", "files", "predecessor"],
        "properties": {
            "release": _field("string"),
            "version": _field("string"),
            "state": _field("string"),
            "files": _field("object"),
            "predecessor": _field(["string", "null"]),
        },
    },
}

# Provenance is pinned to the accepted v2.8.2 source inventory.  Hashes are
# copied from the frozen source table used by the acceptance contract.
DERIVATION = {
    "state-start": {"sources": [{"file": "unit_admission.py", "symbol": "reserve_start", "sha256": "0a4ea9ea59c2e98df27d0f3a4269c6703c8f3ecc55d4acc1d7f4af572598c7d5"}]},
    "state-finish": {"sources": [{"file": "unit_admission.py", "symbol": "finish_start", "sha256": "0a4ea9ea59c2e98df27d0f3a4269c6703c8f3ecc55d4acc1d7f4af572598c7d5"}]},
    "state-reconcile": {"sources": [{"file": "unit_admission.py", "symbol": "reconcile_start", "sha256": "0a4ea9ea59c2e98df27d0f3a4269c6703c8f3ecc55d4acc1d7f4af572598c7d5"}]},
    "state-outcome-closed": {"sources": [{"file": "unit_admission.py", "symbol": "close_outcome", "sha256": "0a4ea9ea59c2e98df27d0f3a4269c6703c8f3ecc55d4acc1d7f4af572598c7d5"}]},
    "route-record": {"sources": [{"file": "ordinary_supervise.py", "symbol": "retain_route", "sha256": "812c37b99f019244dee74415797a30a7f4c9f7fec88786f2974da409d55b08d7"}]},
    "crew": {"sources": [{"file": "crew.py", "symbol": "plan_crew", "sha256": "b6933a5b75c9cfc63914e9e65b3f148d02bb5aaab6f892c158755a1e196c51c5"}]},
    "attempt-outcome": {"sources": [{"file": "route_attestation.py", "symbol": "receipt", "sha256": "b613737be4060ce9573c903db8f52f9e63cb615e63c641de0225058905640a0c"}]},
    "closure": {"sources": [{"file": "unit_admission.py", "symbol": "close_outcome", "sha256": "0a4ea9ea59c2e98df27d0f3a4269c6703c8f3ecc55d4acc1d7f4af572598c7d5"}]},
    "publication-manifest": {"sources": [{"file": "publication.json", "symbol": "manifest", "sha256": "2e5845bd49fcd2fffa1eab96a04b64b39e0fad868e8387dcd55e5d268a26e511"}]},
}


def load(name):
    """Return a fresh, mutable copy of the named record schema."""
    try:
        schema = _SCHEMAS[name]
    except (KeyError, TypeError):
        raise KeyError(name) from None
    return deepcopy(schema)


_SUPPORTED_KEYWORDS = {
    "$schema", "$id", "title", "description", "type", "required", "properties",
    "items", "enum", "const", "additionalProperties", "minimum",
}
_SUPPORTED_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def check_schema(schema):
    """Check that a schema uses only the intentionally supported JSON subset."""
    def visit(node, path):
        if not isinstance(node, dict):
            raise ValueError("schema at %s must be an object" % path)
        unknown = set(node) - _SUPPORTED_KEYWORDS
        if unknown:
            keyword = sorted(unknown, key=str)[0]
            raise ValueError("unsupported schema keyword %r at %s" % (keyword, path))
        for key in ("$schema", "$id", "title", "description"):
            if key in node and not isinstance(node[key], str):
                raise ValueError("schema keyword %r must be a string" % key)
        if "type" in node:
            types = node["type"]
            if isinstance(types, str):
                types = [types]
            if not isinstance(types, list) or not types or any(
                not isinstance(item, str) or item not in _SUPPORTED_TYPES for item in types
            ):
                raise ValueError("unsupported or invalid schema type %r" % (node["type"],))
        if "required" in node and (
            not isinstance(node["required"], list)
            or any(not isinstance(field, str) for field in node["required"])
        ):
            raise ValueError("schema keyword 'required' must be a list of strings")
        if "properties" in node:
            if not isinstance(node["properties"], dict):
                raise ValueError("schema keyword 'properties' must be an object")
            for field, child in node["properties"].items():
                if not isinstance(field, str):
                    raise ValueError("property names must be strings")
                visit(child, path + ".properties." + field)
        if "items" in node:
            visit(node["items"], path + ".items")
        if "additionalProperties" in node and isinstance(node["additionalProperties"], dict):
            visit(node["additionalProperties"], path + ".additionalProperties")
        elif "additionalProperties" in node and not isinstance(node["additionalProperties"], bool):
            raise ValueError("schema keyword 'additionalProperties' must be a boolean or schema")
        if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
            raise ValueError("schema keyword 'enum' must be a non-empty list")
        if "minimum" in node and (
            isinstance(node["minimum"], bool)
            or not isinstance(node["minimum"], (int, float))
            or not math.isfinite(node["minimum"])
        ):
            raise ValueError("schema keyword 'minimum' must be a finite number")

    visit(schema, "#")
    return None


def _matches_type(value, type_name):
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "null":
        return value is None
    return False


def _json_equal(left, right):
    """Compare JSON values without Python's bool/int equivalence."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    return left == right


def _validate_value(schema, value, path, errors):
    types = schema.get("type")
    if types is not None:
        allowed = [types] if isinstance(types, str) else types
        if not any(_matches_type(value, type_name) for type_name in allowed):
            expected = " or ".join(allowed)
            errors.append("field '%s': expected %s" % (path, expected))
            return

    if "const" in schema and not _json_equal(value, schema["const"]):
        errors.append("field '%s': expected constant %r" % (path, schema["const"]))
    if "enum" in schema and not any(_json_equal(value, choice) for choice in schema["enum"]):
        errors.append("field '%s': value is not in the allowed enum" % path)
    if "minimum" in schema and isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema["minimum"]:
            errors.append("field '%s': expected value >= %s" % (path, schema["minimum"]))

    if isinstance(value, dict):
        for field in schema.get("required", []):
            if field not in value:
                errors.append("missing required field '%s'" % field)
        properties = schema.get("properties", {})
        for field, child_schema in properties.items():
            if field in value:
                _validate_value(child_schema, value[field], path + "." + field if path else field, errors)
        additional = schema.get("additionalProperties", True)
        if isinstance(additional, dict):
            for field in value.keys() - properties.keys():
                _validate_value(additional, value[field], path + "." + str(field), errors)
        elif additional is False:
            for field in value.keys() - properties.keys():
                errors.append("field '%s': additional property is not allowed" % (path + "." + str(field)))

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate_value(schema["items"], item, "%s[%d]" % (path, index), errors)


def validate(name, document):
    """Return validation errors for a document matching the named schema."""
    schema = load(name)
    errors = []
    if not isinstance(document, dict):
        return ["document: expected object"]
    _validate_value(schema, document, "", errors)
    return errors
