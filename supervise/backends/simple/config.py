# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Validated configuration and crew planning for the simple backend."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tomllib
import weakref
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from supervise.backend import (
    AttemptOutcome,
    BackendIdentity,
    CrewPlan,
    CrewRequest,
    CrewUnavailable,
    LaunchRequest,
    LaunchSpec,
    ProcessCapture,
    Route,
)

from . import claude, codex


_DRIVERS = {"codex": codex, "claude": claude}


class ConfigError(ValueError):
    """The simple backend configuration is missing or invalid."""


_ROUTE_REQUIRED = {"id", "harness", "vendor", "model", "family", "roles"}
_ROUTE_OPTIONAL = {"effort", "price"}
_PRICE_FIELDS = {"input_per_mtok", "output_per_mtok"}
_CREW_FIELDS = {"authors", "reviewers", "require_review_fallback"}
_ROOT_FIELDS = {"route", "crew"}


def _fail(where: str, message: str) -> None:
    raise ConfigError(f"{where}: {message}")


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(where, "must be a non-empty string")
    return value


def _parse_route(raw: Any, index: int) -> tuple[Route, dict[str, Any]]:
    where = f"route[{index}]"
    if not isinstance(raw, dict):
        _fail(where, "must be a table")
    unknown = set(raw) - _ROUTE_REQUIRED - _ROUTE_OPTIONAL
    if unknown:
        _fail(where, f"unknown key(s): {', '.join(sorted(unknown))}")
    missing = _ROUTE_REQUIRED - set(raw)
    if missing:
        _fail(where, f"missing required field(s): {', '.join(sorted(missing))}")

    route_id = _string(raw["id"], f"{where}.id")
    harness = _string(raw["harness"], f"{where}.harness")
    if harness not in {"codex", "claude"}:
        _fail(f"{where}.harness", "must be 'codex' or 'claude'")
    vendor = _string(raw["vendor"], f"{where}.vendor")
    model = _string(raw["model"], f"{where}.model")
    family = _string(raw["family"], f"{where}.family")

    roles_raw = raw["roles"]
    if not isinstance(roles_raw, list) or not roles_raw:
        _fail(f"{where}.roles", "must be a non-empty array of roles")
    if any(not isinstance(role, str) or role not in {"impl", "review"} for role in roles_raw):
        _fail(f"{where}.roles", "roles must be a subset of 'impl' and 'review'")
    roles = frozenset(roles_raw)
    if not roles:
        _fail(f"{where}.roles", "must contain at least one role")

    if "effort" in raw and not isinstance(raw["effort"], str):
        _fail(f"{where}.effort", "must be a string")

    price: Mapping[str, float] | str = "unknown"
    if "price" in raw:
        price_raw = raw["price"]
        if not isinstance(price_raw, dict):
            _fail(f"{where}.price", "must be a table")
        unknown_price = set(price_raw) - _PRICE_FIELDS
        missing_price = _PRICE_FIELDS - set(price_raw)
        if unknown_price:
            _fail(f"{where}.price", f"unknown key(s): {', '.join(sorted(unknown_price))}")
        if missing_price:
            _fail(f"{where}.price", f"missing required field(s): {', '.join(sorted(missing_price))}")
        normalized: dict[str, float] = {}
        for key in sorted(_PRICE_FIELDS):
            amount = price_raw[key]
            if isinstance(amount, bool) or not isinstance(amount, (int, float)):
                _fail(f"{where}.price.{key}", "must be a non-negative number")
            if not math.isfinite(amount) or amount < 0:
                _fail(f"{where}.price.{key}", "must be a finite non-negative number")
            normalized[key] = float(amount)
        price = normalized

    route = _DRIVERS[harness].make_route(
        route_id,
        model=model,
        family=family,
        roles=roles,
        effort=raw.get("effort", "medium" if harness == "codex" else None),
        price=price,
    )
    return replace(route, vendor=vendor), raw


class SimpleBackend:
    """Load a route catalog and delegate launches to its harness drivers."""

    def __init__(
        self,
        config_path: str | Path,
        *,
        executables: Mapping[str, str | Path] | None = None,
        codex_home: str | Path | None = None,
        claude_config_dir: str | Path | None = None,
    ):
        self.config_path = Path(config_path)
        try:
            with self.config_path.open("rb") as stream:
                parsed = tomllib.load(stream)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            raise ConfigError(f"cannot read configuration: {error}") from error

        if not isinstance(parsed, dict):
            _fail("configuration", "must be a TOML table")
        unknown_root = set(parsed) - _ROOT_FIELDS
        if unknown_root:
            _fail("configuration", f"unknown key(s): {', '.join(sorted(unknown_root))}")
        raw_routes = parsed.get("route")
        if not isinstance(raw_routes, list) or not raw_routes:
            _fail("route", "at least one route table is required")

        routes: list[Route] = []
        route_raw_by_id: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(raw_routes):
            route, route_raw = _parse_route(raw, index)
            if route.route_id in route_raw_by_id:
                _fail(f"route[{index}].id", f"duplicate route id {route.route_id!r}")
            routes.append(route)
            route_raw_by_id[route.route_id] = route_raw

        raw_crew = parsed.get("crew")
        if not isinstance(raw_crew, dict):
            _fail("crew", "a [crew] table is required")
        unknown_crew = set(raw_crew) - _CREW_FIELDS
        if unknown_crew:
            _fail("crew", f"unknown key(s): {', '.join(sorted(unknown_crew))}")
        if "authors" not in raw_crew or "reviewers" not in raw_crew:
            missing = {"authors", "reviewers"} - set(raw_crew)
            _fail("crew", f"missing required field(s): {', '.join(sorted(missing))}")
        authors = self._crew_list(raw_crew["authors"], "crew.authors", route_raw_by_id, "impl")
        reviewers = self._crew_list(raw_crew["reviewers"], "crew.reviewers", route_raw_by_id, "review")
        fallback = raw_crew.get("require_review_fallback", False)
        if not isinstance(fallback, bool):
            _fail("crew.require_review_fallback", "must be a boolean")

        self.routes = tuple(routes)
        self._routes_by_id = {route.route_id: route for route in routes}
        self._authors = authors
        self._reviewers = reviewers
        self._require_review_fallback = fallback
        try:
            canonical = json.dumps(
                parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ConfigError(f"configuration cannot be canonicalized: {error}") from error
        self._config_sha256 = hashlib.sha256(canonical).hexdigest()
        programs = (
            {harness: shutil.which(harness) for harness in _DRIVERS}
            if executables is None else dict(executables)
        )
        self._executables: dict[str, Path | None] = {}
        for harness in _DRIVERS:
            program = programs.get(harness)
            path = Path(program) if program is not None else None
            if path is not None and not path.is_absolute():
                if executables is not None:
                    raise ConfigError(f"{harness} executable must be an absolute path")
                path = path.absolute()
            self._executables[harness] = path
        self.codex_home = Path(
            codex_home if codex_home is not None
            else os.environ.get("CODEX_HOME", Path.home() / ".codex")
        ).expanduser().absolute()
        self.claude_config_dir = Path(
            claude_config_dir if claude_config_dir is not None
            else os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")
        ).expanduser().absolute()
        # LaunchSpec has no route field; keep the association for each live spec.
        self._launch_routes: dict[int, tuple[weakref.ReferenceType[LaunchSpec], Route]] = {}

    @property
    def require_review_fallback(self) -> bool:
        return self._require_review_fallback

    @staticmethod
    def _crew_list(
        value: Any, where: str, route_raw_by_id: Mapping[str, Mapping[str, Any]], role: str
    ) -> tuple[str, ...]:
        if not isinstance(value, list):
            _fail(where, "must be an array of route ids")
        result: list[str] = []
        for index, route_id in enumerate(value):
            if not isinstance(route_id, str):
                _fail(f"{where}[{index}]", "must be a route id string")
            route_raw = route_raw_by_id.get(route_id)
            if route_raw is None:
                _fail(f"{where}[{index}]", f"unknown route id {route_id!r}")
            if role not in route_raw["roles"]:
                _fail(f"{where}[{index}]", f"route {route_id!r} does not have the {role!r} role")
            result.append(route_id)
        return tuple(result)

    def identity(self) -> BackendIdentity:
        source = hashlib.sha256()
        for module in sorted(Path(__file__).parent.glob("*.py"), key=lambda item: item.name):
            source.update(module.name.encode("utf-8"))
            source.update(b"\0")
            source.update(module.read_bytes())
            source.update(b"\0")
        return BackendIdentity(
            name="simple",
            version="1",
            config_sha256=self._config_sha256,
            source_sha256=source.hexdigest(),
            routes=self.routes,
        )

    def plan_crew(self, request: CrewRequest) -> CrewPlan:
        if request.pinned_author is not None:
            author_id = request.pinned_author
        else:
            author_id = next(
                (route_id for route_id in self._authors if route_id not in request.failed_authors), None
            )
        author = self._routes_by_id.get(author_id) if author_id is not None else None
        if author is None or "impl" not in author.roles:
            raise CrewUnavailable("no eligible author is available")

        prior_families = {
            self._routes_by_id[route_id].family
            for route_id in request.prior_authors
            if route_id in self._routes_by_id
        }
        blocked_families = prior_families | {author.family}
        chosen_reviewers: list[Route] = []
        excluded: dict[str, str] = {}
        for route_id in self._reviewers:
            route = self._routes_by_id[route_id]
            if route_id in request.failed_reviewers:
                excluded[route_id] = "reviewer previously failed"
            elif route.family in blocked_families:
                if route.family == author.family:
                    excluded[route_id] = "reviewer shares the author's family"
                else:
                    excluded[route_id] = "reviewer shares a prior author's family"
            else:
                chosen_reviewers.append(route)

        selected_review_ids = {route.route_id for route in chosen_reviewers}
        for route in self.routes:
            if route.route_id in selected_review_ids:
                continue
            if route.route_id in excluded:
                continue
            if route.route_id not in self._reviewers:
                excluded[route.route_id] = "not in the declared reviewer preference order"
            elif route.route_id == author.route_id:
                excluded[route.route_id] = "author route cannot review its own work"
            else:
                excluded[route.route_id] = "not selected from the declared reviewer preference order"

        if not chosen_reviewers:
            raise CrewUnavailable("no independent reviewer is available")
        fallback_required = request.require_review_fallback or self._require_review_fallback
        if fallback_required and len(chosen_reviewers) < 2:
            raise CrewUnavailable("a review fallback is required but fewer than two reviewers remain")

        priced = [author, *chosen_reviewers]
        cost_status = "variable_available" if all(route.price != "unknown" for route in priced) else "unavailable"
        rationale = "Crew selected in declared preference order with family-independent reviewers."
        inputs: dict[str, object] = {
            "authors": self._authors,
            "reviewers": self._reviewers,
            "prior_authors": tuple(request.prior_authors),
            "failed_authors": tuple(request.failed_authors),
            "failed_reviewers": tuple(request.failed_reviewers),
            "pinned_author": request.pinned_author,
            "require_review_fallback": fallback_required,
        }
        return CrewPlan(
            author=author,
            reviewers=tuple(chosen_reviewers),
            rationale=rationale,
            excluded=excluded,
            cost_status=cost_status,
            inputs=inputs,
        )

    def check_grants(self, route: Route, commands: Sequence[Sequence[str]]) -> None:
        self._driver(route).check_grants(commands)

    def preflight_project(self, route: Route, project: Path) -> None:
        self._driver(route).preflight_project(project)

    def prepare_launch(self, request: LaunchRequest) -> LaunchSpec:
        driver = self._driver(request.route)
        executable = self._executables[request.route.harness]
        if executable is None:
            raise FileNotFoundError(f"{request.route.harness} executable is unavailable")
        if request.route.harness == "codex":
            spec = driver.prepare_launch(
                request, executable=executable, codex_home=self.codex_home
            )
        else:
            spec = driver.prepare_launch(
                request, executable=executable, config_dir=self.claude_config_dir
            )
        key = id(spec)
        self._launch_routes[key] = (
            weakref.ref(spec, lambda ref: self._launch_routes.pop(key, None)), request.route
        )
        return spec

    def interpret(self, spec: LaunchSpec, capture: ProcessCapture) -> AttemptOutcome:
        association = self._launch_routes.get(id(spec))
        if association is None or association[0]() is not spec:
            raise ValueError("launch specification was not prepared by this backend")
        route = association[1]
        return self._driver(route).interpret(spec, capture, route=route)

    @staticmethod
    def _driver(route: Route):
        try:
            return _DRIVERS[route.harness]
        except KeyError as error:
            raise ValueError(f"unsupported harness: {route.harness!r}") from error
