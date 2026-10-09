# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Public describe-and-interpret adapter contract (Apache-2.0).

The six backend methods have these responsibilities:

* identity: describe the backend, configuration/source digests, and route catalog.
* plan_crew: propose an author and ordered reviewers using the supplied request;
  raise CrewUnavailable when no eligible crew is available.
* check_grants: check whether a route supports the literal worker commands;
  raise UnsupportedGrant for commands the route cannot support.
* preflight_project: check project configuration for compatibility with a route;
  raise ProjectConfigRefused when that configuration must be refused.
* prepare_launch: describe argv, the complete environment allowlist, review sandbox
  requirements, and the stdout format, without launching a process.
* interpret: interpret captured output into a worker message, reported model,
  usage, cost, failure classification, and notes, preserving missing model,
  usage, and cost as "unknown" with a note rather than inventing zero values.

The kernel verifies crew independence and review eligibility, validates launch
specifications, supplies review isolation, launches and captures processes, and
parses verdicts against captured stdout. It alone writes the journal, receipts,
and seals and controls accounting and closure. ProcessCapture facts are produced
by the kernel; interpretation cannot change exit status, timing, or hashes.
There are no journal, receipt, seal, close, spawn, or accounting methods here.
Plugins are trusted code: this Protocol is an interface, not OS isolation.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence

Role = Literal["impl", "review"]
Unknown = Literal["unknown"]


@dataclass(frozen=True)
class Route:
    route_id: str            # stable, user-chosen
    harness: str             # e.g. "codex", "claude"
    vendor: str              # e.g. "openai", "anthropic"
    model: str               # requested model id
    family: str              # independence key; author and reviewer must differ
    roles: frozenset[Role]
    review_isolation: bool   # harness can run inside the kernel's read-only review view
    price: Mapping[str, float] | Unknown   # per-Mtok list prices, or "unknown"


@dataclass(frozen=True)
class BackendIdentity:
    name: str
    version: str
    config_sha256: str       # canonical digest of the effective configuration
    source_sha256: str       # digest of the backend's own module bytes
    routes: tuple[Route, ...]


@dataclass(frozen=True)
class TaskClass:             # computed by the kernel, never by the backend
    role: Role
    oracle: str
    size: str
    languages: tuple[str, ...]
    key: str


@dataclass(frozen=True)
class CrewRequest:
    task: TaskClass
    prior_authors: tuple[str, ...]          # route ids already used as author/repair
    failed_authors: tuple[str, ...]         # platform_env / platform_timeout this transaction
    failed_reviewers: tuple[str, ...]
    pinned_author: str | None               # set when planning a review/recovery
    require_review_fallback: bool           # True at author time (v2.8.2 behaviour)


@dataclass(frozen=True)
class CrewPlan:
    author: Route
    reviewers: tuple[Route, ...]            # ordered; [0] primary, rest fallback
    rationale: str
    excluded: Mapping[str, str]             # route_id -> reason
    cost_status: Literal["variable_available", "unavailable"]
    inputs: Mapping[str, object]            # what the decision used (sealed by kernel)


@dataclass(frozen=True)
class LaunchRequest:
    route: Route
    role: Role
    packet_path: Path
    workdir: Path
    owned_paths: tuple[Path, ...]
    timeout_seconds: float                  # finite, kernel-allocated
    worker_commands: tuple[tuple[str, ...], ...]   # literal grants (impl only)
    review_view: Path | None                # read-only evidence mount (review only)


@dataclass(frozen=True)
class Mount:
    source: str
    target: str
    writable: bool


@dataclass(frozen=True)
class LaunchSpec:
    """Launch description; mounts name harness runtime/auth paths a review sandbox needs.

    The kernel validates mounts: sources are absolute and exist; targets are absolute,
    are not `/`, and are not inside `/review`.
    """
    argv: tuple[str, ...]
    env: Mapping[str, str]                  # env is the complete allowlist
    requires_review_sandbox: bool
    stdout_format: str                      # opaque tag the backend's interpret() understands
    mounts: tuple[Mount, ...] = ()


@dataclass(frozen=True)
class ProcessCapture:                       # produced by the kernel, read-only to backend
    exit_code: int | None
    timed_out: bool
    seconds: float
    stdout_path: Path
    stderr_path: Path
    stdout_sha256: str
    stderr_sha256: str


@dataclass(frozen=True)
class AttemptOutcome:
    final_text: str                         # worker's final message (verdict parsed by kernel)
    served_model: str | Unknown
    usage: Mapping[str, int] | Unknown      # input/output/cached tokens when reported
    cost_usd: float | Unknown
    cost_basis: Literal["harness_reported", "list_price_estimate", "unknown"]
    failure_class: Literal["none", "platform_env", "platform_timeout", "worker"]
    notes: tuple[str, ...]


class UnsupportedGrant(ValueError):
    ...


class ProjectConfigRefused(ValueError):
    ...


class CrewUnavailable(ValueError):
    ...


class RoutingBackend(Protocol):
    def identity(self) -> BackendIdentity: ...

    def plan_crew(self, request: CrewRequest) -> CrewPlan: ...  # raise CrewUnavailable

    def check_grants(
        self, route: Route, commands: Sequence[Sequence[str]]
    ) -> None: ...  # raise UnsupportedGrant

    def preflight_project(self, route: Route, project: Path) -> None: ...  # raise ProjectConfigRefused

    def prepare_launch(self, request: LaunchRequest) -> LaunchSpec: ...

    def interpret(self, spec: LaunchSpec, capture: ProcessCapture) -> AttemptOutcome: ...
