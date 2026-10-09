# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Deterministic, local-only backend used for testing the routing contract."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

from supervise.backend import (
    AttemptOutcome,
    BackendIdentity,
    CrewPlan,
    CrewUnavailable,
    LaunchSpec,
    ProjectConfigRefused,
    Route,
    UnsupportedGrant,
)


_VERSION = "1.0"


def _json_value(value):
    """Convert contract values into stable, JSON-serializable data."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _json_value(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (set, frozenset)):
        converted = [_json_value(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _canonical_digest(value):
    encoded = json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _outcome_text(outcome, role):
    if role == "review" and outcome == "success":
        return "Stub review completed.\nREVIEW VERDICT: ACCEPT"
    if role == "review" and outcome == "reject":
        return "Stub review completed.\nREVIEW VERDICT: REJECT"
    if outcome == "no_verdict":
        return "Stub completed without a review verdict."
    if outcome == "crash":
        return "Stub worker crashed."
    if outcome == "platform_timeout":
        return "Stub platform timeout."
    if outcome == "reject":
        return "Stub work completed with a reject outcome."
    return "Stub work completed successfully."


class StubBackend:
    """A deterministic backend whose scripted responses never report invented costs."""

    def __init__(self, routes: Sequence[Route], script: Mapping[str, object]):
        self.routes = tuple(routes)
        self._script_config = dict(script or {})
        self._script = {}
        for route_id, configured in self._script_config.items():
            if isinstance(configured, str):
                outcomes = (configured,)
            else:
                outcomes = tuple(configured)
            self._script[str(route_id)] = outcomes
        self._positions = {route_id: 0 for route_id in self._script}

    def identity(self) -> BackendIdentity:
        return BackendIdentity(
            name="stub",
            version=_VERSION,
            config_sha256=_canonical_digest({"routes": self.routes, "script": self._script_config}),
            source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            routes=self.routes,
        )

    def plan_crew(self, request) -> CrewPlan:
        by_id = {route.route_id: route for route in self.routes}
        if request.pinned_author is not None:
            author = by_id.get(request.pinned_author)
        else:
            author = next(
                (route for route in self.routes
                 if "impl" in route.roles and route.route_id not in request.failed_authors),
                None,
            )
        if author is None:
            raise CrewUnavailable("no eligible author route is available")

        prior_families = {
            by_id[route_id].family for route_id in request.prior_authors if route_id in by_id
        }
        reviewers = tuple(
            route for route in self.routes
            if "review" in route.roles
            and route.review_isolation
            and route.route_id not in request.failed_reviewers
            and route.family != author.family
            and route.family not in prior_families
        )
        if not reviewers:
            raise CrewUnavailable("no independent, isolated reviewer route is available")
        if request.require_review_fallback and len(reviewers) < 2:
            raise CrewUnavailable("review fallback requires at least two eligible reviewers")

        selected = {author.route_id, *(route.route_id for route in reviewers)}
        excluded = {}
        for route in self.routes:
            if route.route_id in selected:
                continue
            if route.route_id == request.pinned_author:
                reason = "selected as the pinned author"
            elif "impl" in route.roles and route.route_id in request.failed_authors:
                reason = "author route is in failed_authors"
            elif "review" not in route.roles:
                reason = "route has no review role and was not selected as author"
            elif not route.review_isolation:
                reason = "review route does not support review isolation"
            elif route.route_id in request.failed_reviewers:
                reason = "review route is in failed_reviewers"
            elif route.family == author.family:
                reason = "review family matches the author family"
            elif route.family in prior_families:
                reason = "review family matches a prior author family"
            else:
                reason = "route was not selected for this crew"
            excluded[route.route_id] = reason

        rationale = (
            f"Selected author {author.route_id}; reviewers are ordered by configured route order "
            "and exclude failed, non-isolated, and non-independent routes."
        )
        inputs = {
            "task": request.task,
            "prior_authors": tuple(request.prior_authors),
            "failed_authors": tuple(request.failed_authors),
            "failed_reviewers": tuple(request.failed_reviewers),
            "pinned_author": request.pinned_author,
            "require_review_fallback": request.require_review_fallback,
        }
        return CrewPlan(
            author=author,
            reviewers=reviewers,
            rationale=rationale,
            excluded=excluded,
            cost_status="unavailable",
            inputs=inputs,
        )

    def check_grants(self, route: Route, commands: Sequence[Sequence[str]]) -> None:
        if commands and "impl" not in route.roles:
            raise UnsupportedGrant(f"route {route.route_id} does not support implementation grants")
        for command in commands:
            if not command or not isinstance(command[0], str) or not Path(command[0]).is_absolute():
                raise UnsupportedGrant("worker command program must be an absolute path")

    def preflight_project(self, route: Route, project: Path) -> None:
        if (Path(project) / ".stub-refuse").exists():
            raise ProjectConfigRefused(f"route {route.route_id} refused project configuration")

    def prepare_launch(self, request) -> LaunchSpec:
        route_id = request.route.route_id
        outcomes = self._script.get(route_id, ())
        position = self._positions.get(route_id, 0)
        outcome = outcomes[position] if position < len(outcomes) else "success"
        if position < len(outcomes):
            self._positions[route_id] = position + 1
        role = request.role
        text = _outcome_text(outcome, role)
        code = "import sys; print(" + repr(text) + ")"
        if outcome == "crash":
            code += "; sys.exit(1)"
        return LaunchSpec(
            argv=(str(Path(sys.executable).resolve()), "-c", code),
            env={},
            requires_review_sandbox=(role == "review"),
            stdout_format=f"stub:v1:{role}:{outcome}",
        )

    def interpret(self, spec: LaunchSpec, capture) -> AttemptOutcome:
        try:
            prefix, version, role, outcome = spec.stdout_format.split(":", 3)
        except (AttributeError, ValueError):
            prefix, version, role, outcome = "stub", "v1", "impl", "success"
        if prefix != "stub" or version != "v1":
            role, outcome = "impl", "success"
        failure_class = "none"
        if capture.timed_out or outcome == "platform_timeout":
            failure_class = "platform_timeout"
        elif outcome == "crash":
            failure_class = "worker"
        notes = ("The stub backend does not provide model, usage, or cost measurements.",)
        return AttemptOutcome(
            final_text=_outcome_text(outcome, role),
            served_model="unknown",
            usage="unknown",
            cost_usd="unknown",
            cost_basis="unknown",
            failure_class=failure_class,
            notes=notes,
        )
