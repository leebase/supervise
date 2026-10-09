# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Kernel-mediated author, review, reconciliation, and closure transactions."""

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import uuid
from datetime import datetime, timezone

from supervise import backend as backend_api, schemas
from supervise.controller import budget, classify, evidence
from supervise.kernel import attest, journal, novelty, process, review_view, seal, verdict


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                        ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _read(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _write(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        os.chmod(path, mode)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _store_text(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, mode)


def _timestamp():
    return datetime.now(timezone.utc).isoformat()


def _digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _route_data(route):
    return {
        "route_id": route.route_id,
        "harness": route.harness,
        "vendor": route.vendor,
        "model": route.model,
        "family": route.family,
        "roles": sorted(route.roles),
        "review_isolation": bool(route.review_isolation),
        "price": route.price if isinstance(route.price, str) else dict(route.price),
    }


def _task_data(task):
    return {"role": task.role, "oracle": task.oracle, "size": task.size,
            "languages": list(task.languages), "key": task.key}


def _attempt_event(events, start):
    for event in events:
        if event.get("start") == start and event.get("kind") == "finish":
            return event
    return None


def _is_inside_process(identity):
    if not isinstance(identity, dict):
        return False
    pid = identity.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    current = process.process_identity(pid)
    if current is not None:
        return current == identity
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # An identity-less process is treated as live rather than risk duplicate work.
    return True


class Controller:
    """One admitted invocation, with the append-only journal as start authority."""

    def __init__(self, home, *, backend, publication_sha256, test_timeout_reserve=5.0):
        self.home = Path(home)
        self.backend = backend
        self.publication_sha256 = publication_sha256
        self.test_timeout_reserve = test_timeout_reserve
        self.pending = _read(self.home / "pending.json")
        self.contract = _read(self.home / "contract.json")
        self.admitted = self.contract["admission"]
        self.project = Path(self.pending["cwd"])
        self.unit_id = _read(self.home / "decision.json")["unit_id"]
        # Keep the bytes observed at controller construction so an in-flight
        # controller also notices replacement of any budget authority file.
        self._budget_pin_snapshot = {
            name: (self.home / name).read_bytes() for name in budget.PINS
        }

    def _events(self):
        return journal.read_events(self.home / "STATE.jsonl")

    def _parts(self, events=None):
        events = self._events() if events is None else events
        starts = {item["start"]: item for item in events if item.get("kind") == "start"}
        accounted = {item["start"]: item for item in events
                     if item.get("kind") in ("finish", "reconcile")}
        unfinished = [number for number in sorted(starts) if number not in accounted]
        unsettled = [number for number in sorted(starts)
                     if number in accounted and not self._is_settled(number)]
        return events, starts, accounted, unfinished, unsettled

    def _is_settled(self, number):
        decision = _read(self.home / "decision.json")
        settled = decision.get("settled_starts", [])
        return number in settled

    def _commit(self, status=None, *, decision_updates=None, state_updates=None):
        state = _read(self.home / "state.json")
        decision = _read(self.home / "decision.json")
        if decision_updates:
            decision.update(decision_updates)
        version = state.get("version", 0) + 1
        decision["version"] = version
        if status is not None:
            state["status"] = status
        state.update(state_updates or {})
        state["version"] = version
        _write(self.home / "decision.json", decision, 0o400)
        _write(self.home / "state.json", state, 0o600)
        return state

    def _check_version(self, intent):
        state = _read(self.home / "state.json")
        expected = intent.get("expected_version")
        if type(expected) is not int or expected != state.get("version"):
            raise ValueError("stale expected_version; current state version differs")
        return state

    def _status_result(self, status, **extra):
        state = _read(self.home / "state.json")
        result = {"status": status, "version": state.get("version")}
        result.update(extra)
        return result

    def _json_contract_hash(self):
        return _digest_bytes((self.home / "contract.json").read_bytes())

    def _gaps(self):
        return _read(self.home / "decision.json").get("gaps", [])

    def _targets(self, intent):
        targets = intent.get("targets")
        if not isinstance(targets, list) or not targets or any(not isinstance(x, str) for x in targets):
            raise ValueError("dispatch must advance an open parent gap")
        open_ids = {row.get("id") for row in self._gaps()
                    if row.get("state") == "OPEN" and row.get("id") != "independent-review"}
        if len(set(targets)) != len(targets) or not set(targets) <= open_ids:
            raise ValueError("dispatch must advance an open parent gap")
        return tuple(targets)

    def _crew_history(self, starts, accounted):
        prior, failed_authors, failed_reviewers = [], [], []
        for number, start in sorted(starts.items()):
            route = start.get("route")
            if start.get("role") == "impl" and route not in prior:
                prior.append(route)
            if number not in accounted:
                continue
            finish = accounted[number]
            attempt = finish.get("attempt") if finish.get("kind") == "finish" else None
            if not isinstance(attempt, dict):
                continue
            failure = attempt.get("failure_class")
            if start.get("role") == "impl" and failure in ("platform_timeout", "platform_env"):
                if route not in failed_authors:
                    failed_authors.append(route)
            if start.get("role") == "review" and failure in ("platform_timeout", "platform_env", "worker"):
                if route not in failed_reviewers:
                    failed_reviewers.append(route)
        data = _read(self.home / "decision.json")
        for route in data.get("failed_authors", []):
            if route not in failed_authors:
                failed_authors.append(route)
        for route in data.get("failed_reviewers", []):
            if route not in failed_reviewers:
                failed_reviewers.append(route)
        return tuple(prior), tuple(failed_authors), tuple(failed_reviewers)

    def _task(self):
        frozen = self.contract["worker_task_class"]
        return backend_api.TaskClass(role=frozen["role"], oracle=frozen["oracle"],
                                     size=frozen["size"], languages=tuple(frozen["languages"]),
                                     key=frozen["key"])

    def _plan(self, *, starts, accounted, pinned_author=None, require_fallback=False):
        prior, failed_authors, failed_reviewers = self._crew_history(starts, accounted)
        task = self._task()
        if pinned_author is None:
            task = classify.classify(self.admitted["packet"]["text"],
                                     self.admitted["owned_paths"], role="impl")
            require_fallback = getattr(self.backend, "require_review_fallback", True)
            if not isinstance(require_fallback, bool):
                require_fallback = True
        request = backend_api.CrewRequest(
            task=task,
            prior_authors=prior,
            failed_authors=failed_authors,
            failed_reviewers=failed_reviewers,
            pinned_author=pinned_author,
            require_review_fallback=require_fallback,
        )
        try:
            plan = self.backend.plan_crew(request)
        except Exception as error:
            raise ValueError("no qualified independent review crew: " + str(error)) from error
        self._verify_plan(plan, prior, pinned_author)
        if require_fallback and len(plan.reviewers) < 2:
            raise ValueError("crew has no independent fallback reviewer")
        return plan, request

    @staticmethod
    def _verify_plan(plan, _prior_authors, pinned_author):
        author = getattr(plan, "author", None)
        reviewers = tuple(getattr(plan, "reviewers", ()))
        if author is None or "impl" not in getattr(author, "roles", ()):
            raise ValueError("crew author role is invalid")
        if pinned_author is not None and author.route_id != pinned_author:
            raise ValueError("review recovery must retain its pinned author")
        if not reviewers:
            raise ValueError("crew has no independent reviewer")
        families = set()
        for reviewer in reviewers:
            if "review" not in getattr(reviewer, "roles", ()):
                raise ValueError("reviewer role is invalid")
            if not getattr(reviewer, "review_isolation", False):
                raise ValueError("reviewer isolation is required")
            if reviewer.family == author.family:
                raise ValueError("reviewer family must be independent from author")
            if reviewer.family in families:
                raise ValueError("reviewer families must be independent")
            families.add(reviewer.family)

    def _verify_prior_families(self, plan, starts):
        try:
            routes = {route.route_id: route for route in self.backend.identity().routes}
        except Exception as error:
            raise ValueError("crew independence cannot be checked against backend identity") from error
        planned = (plan.author, *tuple(plan.reviewers))
        for route in planned:
            configured = routes.get(getattr(route, "route_id", None))
            if configured is None or configured != route:
                raise ValueError("crew route is not part of the configured backend identity")
        prior_ids = {item.get("route") for item in starts.values()
                     if item.get("role") == "impl"}
        if not prior_ids <= set(routes):
            raise ValueError("prior author family cannot be verified")
        families = {routes[route_id].family for route_id in prior_ids}
        for reviewer in plan.reviewers:
            if reviewer.family in families:
                raise ValueError("reviewer family must differ from every prior author family")

    def _archive_orphan_route(self, number, events):
        path = self.home / ("route-%d.json" % number)
        if not path.exists():
            return
        if any(item.get("kind") == "start" and item.get("start") == number for item in events):
            raise ValueError("route record already belongs to a reserved start")
        data = path.read_bytes()
        archive = self.home / ("route-refused-prestart-" + uuid.uuid4().hex + ".json")
        _store_text(archive, data, 0o444)
        path.chmod(0o600)
        path.unlink()
        for artifact in (self.home / ("packet-%d.txt" % number),
                         self.home / ("review-packet-%d.txt" % number)):
            if artifact.exists():
                artifact.chmod(0o600)
                artifact.unlink()

    def _write_crew(self, number, plan, request):
        record = {
            "schema": "crew/1",
            "selected": {"author": _route_data(plan.author),
                         "reviewers": [_route_data(route) for route in plan.reviewers]},
            "excluded": [{"route_id": str(key), "reason": str(value)}
                         for key, value in sorted(dict(plan.excluded).items())],
            "rationale": str(plan.rationale),
            "cost_status": str(plan.cost_status),
            "created_at": _timestamp(),
            "request": {"task": _task_data(request.task),
                        "prior_authors": list(request.prior_authors),
                        "failed_authors": list(request.failed_authors),
                        "failed_reviewers": list(request.failed_reviewers),
                        "pinned_author": request.pinned_author,
                        "require_review_fallback": request.require_review_fallback},
        }
        record["crew_sha256"] = attest.digest(record)
        path = self.home / ("crew-%d-%s.json" % (number, record["crew_sha256"]))
        _store_text(path, _json_bytes(record).rstrip(b"\n"), 0o444)
        errors = schemas.validate("crew", record)
        if errors:
            raise ValueError("invalid crew record: " + "; ".join(errors))
        return record, path

    def _route_record(self, number, role, route, task, crew, crew_path, packet_id, decision):
        record = attest.route_record(
            mission=self.pending["invocation_id"], transaction=self.unit_id, role=role,
            route_id=route.route_id, class_key=task.key, packet_id=packet_id,
            crew_path=crew_path, crew_sha256=crew["crew_sha256"],
            publication_sha256=self.publication_sha256,
            contract_sha256=self._json_contract_hash(), decision=decision,
            classification_sha256=attest.digest(_task_data(task)),
            controller="supervise.controller.transaction", controller_path=str(Path(__file__).resolve()),
        )
        return attest.write_route_record(self.home, number, record)

    def _verify_budget_authority(self):
        """Validate the pinned admission and its contract binding before allocation."""
        try:
            home_info = self.home.lstat()
        except OSError as error:
            raise ValueError("budget invocation home is unavailable") from error
        if (not stat.S_ISDIR(home_info.st_mode) or stat.S_IMODE(home_info.st_mode) != 0o700):
            raise ValueError("budget invocation home must be private")

        raw, info = {}, {}
        for name in budget.PINS:
            path = self.home / name
            try:
                item = path.lstat()
            except OSError as error:
                raise ValueError("missing pinned budget artifact: " + name) from error
            if not stat.S_ISREG(item.st_mode) or item.st_mode & 0o222:
                raise ValueError("pinned budget artifact is writable or not regular: " + name)
            try:
                data = path.read_bytes()
            except OSError as error:
                raise ValueError("cannot read pinned budget artifact: " + name) from error
            if data != self._budget_pin_snapshot.get(name):
                raise ValueError("tampered pinned budget artifact: " + name)
            raw[name], info[name] = data, item

        # Admission writes binding.json last among the pinned files.  This
        # catches a checkpoint (including opened_at) replaced after admission,
        # even though that timestamp has no separate JSON digest field.
        binding_time = info["binding.json"].st_ctime_ns
        if any(info[name].st_ctime_ns > binding_time
               for name in budget.PINS if name != "binding.json"):
            raise ValueError("pinned budget artifact changed after admission")

        def decode(name):
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate field")
                    result[key] = value
                return result

            def reject_constant(value):
                raise ValueError("non-finite JSON value")

            try:
                return json.loads(raw[name].decode("utf-8"), object_pairs_hook=unique_object,
                                  parse_constant=reject_constant)
            except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
                raise ValueError("invalid pinned budget artifact: " + name) from error

        pending = decode("pending.json")
        acceptance = decode("acceptance.json")
        checkpoint_file = decode("elapsed-checkpoint.json")
        contract = decode("contract.json")
        binding = decode("binding.json")
        if not all(isinstance(value, dict) for value in (pending, checkpoint_file, contract, binding)):
            raise ValueError("pinned budget object has an invalid format")
        if set(binding) != {"sha256"} or binding.get("sha256") != _digest_bytes(raw["contract.json"]):
            raise ValueError("contract binding mismatch")
        if contract != self.contract or pending != self.pending:
            raise ValueError("pinned admission artifacts changed")
        if acceptance != contract.get("acceptance"):
            raise ValueError("acceptance artifact does not match the admitted contract")
        if not isinstance(acceptance, list):
            raise ValueError("admitted acceptance rows must be a list")

        admitted = contract.get("admission")
        if not isinstance(admitted, dict):
            raise ValueError("admitted contract is missing its intent")
        if (admitted.get("user_limits") or contract.get("user_limits")
                or (isinstance(acceptance, dict) and acceptance.get("user_limits"))):
            raise ValueError("outer user_limits prevent worker allocation")
        if (admitted.get("acceptance") != acceptance
                or admitted.get("checkpoint") != contract.get("checkpoint")
                or admitted.get("tests") != contract.get("tests")):
            raise ValueError("admission fields do not match the frozen contract")
        if (pending.get("cwd") != contract.get("project")
                or str(self.home.resolve()) != contract.get("private_root")):
            raise ValueError("pending invocation does not match the frozen contract")

        checkpoint = self.admitted.get("checkpoint")
        expected_fields = {"opened_at", "max_seconds", "max_starts", "first_value_seconds"}
        if not isinstance(checkpoint, dict) or set(checkpoint_file) != expected_fields:
            raise ValueError("elapsed checkpoint has an unsafe format")
        opened_at = checkpoint_file.get("opened_at")
        if (isinstance(opened_at, bool) or not isinstance(opened_at, (int, float))
                or not math.isfinite(opened_at)):
            raise ValueError("elapsed checkpoint opened_at is invalid")
        for name in ("max_seconds", "max_starts", "first_value_seconds"):
            actual, expected = checkpoint_file.get(name), checkpoint.get(name)
            if (isinstance(actual, bool) or not isinstance(actual, (int, float))
                    or isinstance(expected, bool) or not isinstance(expected, (int, float))
                    or not math.isfinite(actual) or not math.isfinite(expected) or actual != expected):
                raise ValueError("elapsed checkpoint does not match admitted " + name)

        return float(opened_at), float(checkpoint_file["max_seconds"])

    def _budget(self, events, accounted):
        self._verify_budget_authority()
        starts = [item for item in events if item.get("kind") == "start"]
        left = budget.remaining(self.home)
        test_seconds = (self.admitted["tests"]["timeout_seconds"]
                        + self.test_timeout_reserve)
        if left < test_seconds:
            return 0.0
        finishes = {number: value for number, value in accounted.items()}
        return budget.worker_allocation(
            self.home, self.admitted, starts, finishes,
            test_timeout_reserve=self.test_timeout_reserve)

    def _checkpoint_result(self):
        current = _read(self.home / "state.json")
        if current.get("status") != "CHECKPOINT_EXHAUSTED":
            self._commit("CHECKPOINT_EXHAUSTED")
        return self._status_result("CHECKPOINT_EXHAUSTED")

    def _observe_tests(self, start_number):
        root = self.home / "test-runs" / ("run-%d-%s" % (start_number, uuid.uuid4().hex))
        root.mkdir(mode=0o700, parents=True)
        observation_contract = dict(self.contract)
        # The frozen source baseline is an admission pin, not a requirement that
        # implementation source remain unchanged during later test observations.
        observation_contract["source_baseline"] = None
        observation = evidence.observe(observation_contract, root)
        baseline = self.contract.get("baseline_run", {})
        tests = self.admitted["tests"]
        required_map = self.contract.get("acceptance_test_ids", {})
        required = sorted({test_id for values in required_map.values() for test_id in values})
        comparison = evidence.compare(baseline, observation, tests["baseline_policy"], required)
        return observation, comparison

    def _advance_gaps(self, decision, observation):
        outcomes = observation.get("outcomes", {}) if isinstance(observation, dict) else {}
        complete = observation.get("complete") is True if isinstance(observation, dict) else False
        mapping = self.contract.get("acceptance_test_ids", {})
        for row in decision.get("gaps", []):
            if row.get("id") == "independent-review":
                continue
            ids = mapping.get(row.get("id"), [])
            proved = bool(complete and ids and all(
                isinstance(outcomes.get(test_id), dict)
                and outcomes[test_id].get("outcome") == "PASS" for test_id in ids))
            row["state"] = "PROVED" if proved else "OPEN"

    def _attempt_directory(self, number, attempt_id):
        path = self.home / "attempts" / ("start-%d-%s" % (number, attempt_id))
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        return path

    def _unowned_snapshot(self):
        """Inventory unowned project entries without following symlinks or owned trees.

        Regular files are recorded by permission bits and content digest, symbolic
        links by permission bits and target text (never followed), directories by
        their permission bits, and every other entry type by permission bits and
        file type, so any added, removed, retyped or mode-only changed entry is a
        confinement change.  Only the project's own top-level ``.git`` directory is
        ignored in full; a nested directory with that name is inventoried like any
        other.  Inside a ``__pycache__`` directory only bytecode files (``*.pyc``)
        are ignored, so anything else that hides there is still recorded.
        """
        owned = tuple(Path(path) for path in self.admitted["owned_paths"])
        snapshot = {}

        def unowned(relative):
            return not any(relative == item or item in relative.parents for item in owned)

        def record(path):
            key = path.relative_to(self.project).as_posix()
            mode = path.lstat().st_mode
            bits = "mode-%o" % stat.S_IMODE(mode)
            if stat.S_ISLNK(mode):
                snapshot[key] = "link:%s:%s" % (bits, os.readlink(path))
            elif stat.S_ISDIR(mode):
                snapshot[key] = "dir:" + bits
            elif stat.S_ISREG(mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                snapshot[key] = "file:%s:%s" % (bits, digest.hexdigest())
            else:
                kind = {stat.S_IFIFO: "fifo", stat.S_IFSOCK: "socket",
                        stat.S_IFCHR: "character device", stat.S_IFBLK: "block device"}
                snapshot[key] = "type:%s:%s" % (
                    bits, kind.get(stat.S_IFMT(mode), "mode-%o" % stat.S_IFMT(mode)))

        def walk_error(error):
            raise error

        for directory, dirnames, filenames in os.walk(
                self.project, followlinks=False, onerror=walk_error):
            root = Path(directory)
            top = root == self.project
            descended = []
            for name in sorted(dirnames):
                path = root / name
                relative = path.relative_to(self.project)
                if not unowned(relative):
                    continue
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    record(path)
                    continue
                if top and name == ".git":
                    continue  # only the project's own .git tree may be ignored
                if name == "__pycache__":
                    descended.append(name)  # bytecode cache: entered, never recorded
                    continue
                record(path)
                descended.append(name)
            dirnames[:] = descended
            for name in sorted(filenames):
                path = root / name
                relative = path.relative_to(self.project)
                if not unowned(relative):
                    continue
                if "__pycache__" in relative.parts and name.endswith(".pyc"):
                    continue  # bytecode inside a bytecode cache is not evidence
                record(path)
        return snapshot

    def _check_project_confinement(self, number):
        before_path = self.home / ("confinement-%d.json" % number)
        if not before_path.is_file():
            return  # Older invocations have no retained confinement baseline.
        before = _read(before_path)
        snapshot_error = None
        try:
            after = self._unowned_snapshot()
            changed = sorted(path for path in before.keys() | after.keys()
                             if before.get(path) != after.get(path))
        except OSError as error:
            # An unreadable post-run inventory cannot establish confinement.
            changed = []
            snapshot_error = str(error)
        if not changed and snapshot_error is None:
            return
        decision = _read(self.home / "decision.json")
        violation = dict(decision.get("project_confinement_violation", {}))
        violation["paths"] = sorted(set(violation.get("paths", [])) | set(changed))
        violation["starts"] = sorted(set(violation.get("starts", [])) | {number})
        violation["reason"] = "worker changed files outside owned paths"
        if snapshot_error is not None:
            violation["snapshot_error"] = snapshot_error
        self._commit(decision_updates={"project_confinement_violation": violation})

    def _refuse_project_violation(self):
        violation = _read(self.home / "decision.json").get("project_confinement_violation")
        if violation:
            raise ValueError("transaction changed files outside owned paths: "
                             + ", ".join(violation.get("paths", [])))

    def _dispatch(self, *, role, route, plan, request, task, packet_text, author_start=None,
                  source_snapshot=None, evidence_handle=None, view=None, spec=None,
                  interpretation_spec=None, allocation=None, events, starts, accounted):
        number = len([item for item in events if item.get("kind") == "start"]) + 1
        timeout = allocation
        if timeout is None:
            timeout = self._budget(events, accounted)
        latest_allocation = self._budget(events, accounted)
        if latest_allocation <= 0:
            return self._checkpoint_result()
        timeout = min(timeout, latest_allocation)
        self._commit(_read(self.home / "state.json").get("status"))
        self._archive_orphan_route(number, events)
        attempt_id = str(uuid.uuid4())
        crew, crew_path = self._write_crew(number, plan, request)
        packet = self.home / ("packet-%d.txt" % number)
        packet_data = packet_text.encode("utf-8")
        _store_text(packet, packet_data, 0o400)
        packet_id = _digest_bytes(packet_data)
        decision_snapshot = _read(self.home / "decision.json")
        route_path = self._route_record(number, role, route, task, crew, crew_path,
                                        packet_id, decision_snapshot)
        if spec is None:
            owned = tuple(self.project / path for path in self.admitted["owned_paths"])
            launch_request = backend_api.LaunchRequest(
                route=route, role=role, packet_path=packet, workdir=self.project,
                owned_paths=owned, timeout_seconds=timeout,
                worker_commands=(tuple(self.admitted["tests"]["argv"]),) if role == "impl" else (),
                review_view=Path(view["path"]) if view else None,
            )
            spec = self.backend.prepare_launch(launch_request)
        if interpretation_spec is None:
            interpretation_spec = spec
        if role == "review" and not spec.requires_review_sandbox:
            raise ValueError("review LaunchSpec requires review sandbox isolation")
        identity = self.backend.identity()
        execution = {"backend": identity.name, "role": role}
        if role == "impl":
            _write(self.home / ("confinement-%d.json" % number),
                   self._unowned_snapshot(), 0o400)
        start_number = journal.reserve_start(
            self.home / "STATE.jsonl", self.unit_id, route=route.route_id,
            packet_id=packet_id, role=role, timeout=timeout, attempt_id=attempt_id,
            execution=execution, decision=decision_snapshot,
            max_starts=self.admitted["checkpoint"]["max_starts"],
        )
        start_event = next(item for item in self._events()
                           if item.get("kind") == "start" and item.get("start") == start_number)
        attempt_dir = self._attempt_directory(start_number, attempt_id)
        stdout_path, stderr_path = attempt_dir / "stdout.txt", attempt_dir / "stderr.txt"

        def spawned(process_identity):
            if process_identity is not None:
                _write(self.home / ("execution-%d.json" % start_number), process_identity, 0o600)

        started = time.monotonic()
        try:
            capture = process.run(
                spec.argv, env=dict(spec.env), cwd=self.project, timeout=timeout, grace=1.0,
                stdin_path=packet, stdout_path=stdout_path, stderr_path=stderr_path,
                on_spawn=spawned,
            )
            interpreted = self.backend.interpret(interpretation_spec, capture)
        except Exception as error:
            elapsed = max(0.0, time.monotonic() - started)
            capture = None
            interpreted = backend_api.AttemptOutcome(
                final_text="", served_model="unknown", usage="unknown", cost_usd="unknown",
                cost_basis="unknown", failure_class="platform_env", notes=(str(error),))
        finally:
            if role == "impl":
                self._check_project_confinement(start_number)
        # BaseException deliberately passes through unaccounted for reconciliation.
        seconds = capture.seconds if capture is not None else elapsed
        stdout_bytes = stdout_path.read_bytes() if stdout_path.exists() else b""
        stderr_bytes = stderr_path.read_bytes() if stderr_path.exists() else b""
        failure = interpreted.failure_class
        if capture is not None and capture.timed_out:
            failure = "platform_timeout"
        if failure not in ("none", "platform_env", "platform_timeout", "worker"):
            failure = "worker"
        parsed = None
        post_verify = self._run_verifier(view) if role == "review" and view is not None else None
        if (role == "review" and post_verify is True and capture is not None
                and failure == "none" and capture.exit_code == 0):
            parsed = verdict.parse_verdict(interpreted.final_text, stdout_bytes)
        if role == "review" and parsed is None and failure == "none":
            failure = "worker"
        if role == "review":
            result_verdict = parsed or "unknown"
            attempt_author = author_start
            source = source_snapshot
            observation = None
            comparison = None
        else:
            result_verdict = "completed" if capture is not None and capture.exit_code == 0 else "unknown"
            attempt_author = None
            source = novelty.snapshot(self.project, self.admitted["owned_paths"])
            observation, comparison = self._observe_tests(start_number)
        attempt = {
            "attempt_id": attempt_id,
            "verdict": result_verdict,
            "failure_class": failure,
            "usage": (dict(interpreted.usage) if isinstance(interpreted.usage, dict)
                      else {"reported": "unknown"}),
            "cost": {"usd": interpreted.cost_usd, "basis": interpreted.cost_basis},
            "wall_clock_ms": max(0.0, seconds * 1000.0),
            "routing_attestation": attest.receipt(
                route_path, execution=start_event["execution"], start=start_number,
                attempt_id=attempt_id),
            "captured_at": _timestamp(),
            "final_text": interpreted.final_text,
            "notes": list(interpreted.notes),
            "source_snapshot": source,
            "author_start": attempt_author,
            "evidence_handle": evidence_handle,
            "view_identity": view.get("identity") if view else None,
            "verifier_ok": post_verify if role == "review" else None,
            "observation": observation,
            "comparison": comparison,
        }
        journal.finish_start(self.home / "STATE.jsonl", self.unit_id, start_number,
                             attempt_id=attempt_id, seconds=seconds, attempt=attempt)
        events = self._events()
        self._settle_finished(start_number, role, route.route_id, attempt)
        return {"start": start_number, "attempt": attempt, "capture": capture,
                "observation": observation, "comparison": comparison}

    def _settle_finished(self, number, role, route, attempt):
        events = self._events()
        start_event = next((item for item in events
                            if item.get("kind") == "start" and item.get("start") == number), None)
        finish_event = next((item for item in events
                             if item.get("kind") == "finish" and item.get("start") == number), None)
        if start_event is None or finish_event is None:
            raise ValueError("finished transaction start is missing its journal evidence")
        retained_attempt = finish_event.get("attempt")
        if retained_attempt != attempt:
            raise ValueError("in-memory attempt differs from retained finish")
        errors = schemas.validate("attempt-outcome", retained_attempt)
        if errors:
            raise ValueError("invalid attempt outcome: " + "; ".join(errors))
        attest.verify_receipt(self.home, start_event, finish_event)
        attempt = retained_attempt
        decision = _read(self.home / "decision.json")
        settled = list(decision.get("settled_starts", []))
        if number in settled:
            return
        settled.append(number)
        updates = {"settled_starts": sorted(settled)}
        status = _read(self.home / "state.json").get("status", "ADMITTED")
        if role == "impl":
            observation = attempt.get("observation") or {}
            last_author = {
                "start": number, "route": route, "attempt": attempt,
                "source_snapshot": attempt.get("source_snapshot"),
                "observation": observation, "comparison": attempt.get("comparison"),
            }
            updates["last_author"] = last_author
            updates["current_author_start"] = number
            status = "AUTHOR_COMPLETE"
            if attempt.get("failure_class") in ("platform_timeout", "platform_env"):
                failed = list(decision.get("failed_authors", []))
                if route not in failed:
                    failed.append(route)
                updates["failed_authors"] = failed
        else:
            author_start = attempt.get("author_start")
            last_review = {"start": number, "author_start": author_start,
                           "route": route, "verdict": attempt.get("verdict", "unknown"),
                           "source_snapshot": attempt.get("source_snapshot"),
                           "findings": attempt.get("final_text", ""),
                           "failure_class": attempt.get("failure_class"),
                           "verifier_ok": attempt.get("verifier_ok")}
            updates["last_review"] = last_review
            good = attempt.get("failure_class") == "none" and attempt.get("verifier_ok") is True
            if good and attempt.get("verdict") in ("accept", "reject"):
                updates["last_completed_review_source"] = attempt.get("source_snapshot")
                updates["last_completed_review"] = last_review
                if attempt["verdict"] == "accept":
                    status = "REVIEW_PASSED"
                    self._advance_gaps(decision, (decision.get("last_author") or {}).get("observation", {}))
                    for row in decision.get("gaps", []):
                        if row.get("id") == "independent-review":
                            row["state"] = "PROVED"
                    accepted = {"kind": "accepted_review", "unit_id": self.unit_id,
                                "source_snapshot": attempt.get("source_snapshot"),
                                "author_start": author_start, "review_start": number,
                                "reviewer": route, "attempt_id": attempt.get("attempt_id"),
                                "evidence_identity": attempt.get("view_identity"),
                                "accepted_at": _timestamp()}
                    _write(self.home / "accepted-review.json", accepted, 0o400)
                    updates["accepted_review"] = accepted
                else:
                    status = "REVIEW_BLOCKING"
                    rejected = list(decision.get("reject_reviews", []))
                    rejected.append(number)
                    updates["reject_reviews"] = rejected
                    updates["review_findings"] = attempt.get("final_text", "")
            else:
                status = "REVIEW_INCOMPLETE"
                failed = list(decision.get("failed_reviewers", []))
                if route not in failed:
                    failed.append(route)
                updates["failed_reviewers"] = failed
                updates["review_recovery_author"] = author_start
        updates["gaps"] = decision.get("gaps", [])
        self._commit(status, decision_updates=updates)

    def _store_source_files(self, directory):
        source_dir = directory / "source"
        source_dir.mkdir(mode=0o700)
        files = {}
        for owned in self.admitted["owned_paths"]:
            source = self.project / owned
            if not source.is_file() or source.is_symlink():
                raise ValueError("owned source evidence must be a regular file")
            target = source_dir / owned
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            data = source.read_bytes()
            target.write_bytes(data)
            os.chmod(target, 0o600)
            files["source/" + owned] = target
        return files

    def _author_seal(self, author):
        attempt = author["attempt"]
        start = author["start"]
        event = next(item for item in self._events()
                     if item.get("kind") == "start" and item.get("start") == start)
        finish = _attempt_event(self._events(), start)
        if finish is None or finish.get("kind") != "finish":
            raise ValueError("review requires a finished author start")
        store = self.home / "evidence"
        store.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(store, 0o700)
        directory = store / ("author-%d-%s" % (start, uuid.uuid4().hex))
        directory.mkdir(mode=0o700)
        work = directory / "files"
        work.mkdir(mode=0o700)
        files = {}
        metadata = {"start": event, "finish": finish, "attempt": attempt}
        for name, content in (("attempt.json", _json_bytes(metadata)),
                              ("source-snapshot.json", _json_bytes(author["source_snapshot"])),
                              ("tests.json", _json_bytes(author.get("observation", {})))):
            path = work / name
            path.write_bytes(content)
            os.chmod(path, 0o600)
            files[name] = path
        attempt_directory = self.home / "attempts"
        # Captures are located below the unique attempt directory named by the receipt.
        capture_candidates = sorted(attempt_directory.glob("start-%d-*" % start))
        if not capture_candidates:
            raise ValueError("author stdout and stderr evidence are unavailable")
        for name in ("stdout.txt", "stderr.txt"):
            path = capture_candidates[-1] / name
            if not path.is_file():
                raise ValueError("author stdout and stderr evidence are unavailable")
            files[name] = path
        files.update(self._store_source_files(work))
        source_id = attest.digest(author["source_snapshot"])
        binding = {"mission_id": self.pending["invocation_id"], "transaction_id": self.unit_id,
                   "worker_id": event["route"], "worker_role": "impl",
                   "attempt_id": event["attempt_id"], "source_id": source_id}
        handle = seal.seal(store, binding=binding, files=files, pins={})
        return handle, binding

    def _run_verifier(self, view):
        path = Path(view["path"])
        capture_dir = self.home / "attempts" / ("verify-" + uuid.uuid4().hex)
        capture_dir.mkdir(mode=0o700)
        stdout_path, stderr_path = capture_dir / "stdout.txt", capture_dir / "stderr.txt"
        argv = review_view.verifier_argv(path, [sys.executable, "verify.py"],
                                         expected_identity=view["identity"])
        try:
            capture = process.run(argv, env={}, cwd=path, timeout=30.0, grace=1.0,
                                  stdout_path=stdout_path, stderr_path=stderr_path, on_spawn=None)
            output = stdout_path.read_bytes().decode("utf-8", "replace").strip()
            return bool(capture.exit_code == 0 and output == "VERIFIED " + view["identity"])
        except Exception:
            return False

    def _review(self, events, starts, accounted):
        plan_bytes = Path(self.pending["plan_path"]).read_bytes()
        if _digest_bytes(plan_bytes) != self.pending["plan_sha256"]:
            raise ValueError("plan SHA-256 differs from the admitted plan")
        plan_text = plan_bytes.decode("utf-8")
        decision = _read(self.home / "decision.json")
        author = decision.get("last_author")
        if not isinstance(author, dict):
            raise ValueError("review requires a finished author start")
        author_start = author.get("start")
        if author_start not in starts or author_start not in accounted or accounted[author_start].get("kind") != "finish":
            raise ValueError("review requires a finished author start")
        author_finish = _attempt_event(events, author_start)
        if author_finish is None:
            raise ValueError("review requires a finished author start")
        current_source = novelty.snapshot(self.project, self.admitted["owned_paths"])
        last_completed = decision.get("last_completed_review_source")
        if last_completed is not None and not novelty.review_allowed(last_completed, current_source):
            raise ValueError("unchanged source evidence earns no new review")
        if author.get("source_snapshot") != current_source:
            raise ValueError("source changed since the finished author attempt")
        pinned = starts[author_start]["route"]
        allocation = self._budget(events, accounted)
        if allocation <= 0:
            return self._checkpoint_result()
        plan, request = self._plan(starts=starts, accounted=accounted,
                                   pinned_author=pinned, require_fallback=False)
        self._verify_prior_families(plan, starts)
        reviewer = plan.reviewers[0]
        self._commit(_read(self.home / "state.json").get("status"))
        # Evidence is frozen before any reviewer start is reserved.
        handle, binding = self._author_seal(author)
        view_root = self.home / "views"
        view_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        view = review_view.materialize(handle, expected_binding=binding,
                                       expected_identity=handle["identity"],
                                       reviewer_id=reviewer.route_id, view_root=view_root)
        number = len([item for item in events if item.get("kind") == "start"]) + 1
        packet = ("You are an independent, read-only reviewer of this transaction.\n"
                  "The sealed evidence is under /review: changed source is under\n"
                  "/review/source, with the worker transcript in stdout.txt and stderr.txt\n"
                  "and test results in tests.json alongside it under /review.\n\n"
                  "Full plan:\n" + plan_text + "\n\nAcceptance criteria:\n")
        for item in self.admitted["acceptance"]:
            packet += "- " + item["id"] + ": " + item["proposition"] + "\n"
            packet += "  Selectors: " + ", ".join(item["selectors"]) + "\n"
        packet += "\nOwned paths:\n"
        for path in self.admitted["owned_paths"]:
            packet += "- " + path + "\n"
        packet += ("\nReview the whole plan, including requirements without direct test coverage.\n"
                   "Put any findings above the verdict. Your reply must end with exactly\n"
                   "one final line, either:\n"
                   "REVIEW VERDICT: ACCEPT\n"
                   "or:\n"
                   "REVIEW VERDICT: REJECT\n"
                   "Do not place any text after that final line.\n")
        packet_path = self.home / ("review-packet-%d.txt" % number)
        launch_request = backend_api.LaunchRequest(
            route=reviewer, role="review", packet_path=packet_path, workdir=self.project,
            owned_paths=tuple(self.project / path for path in self.admitted["owned_paths"]),
            timeout_seconds=allocation, worker_commands=(), review_view=Path(view["path"]),
        )
        spec = self.backend.prepare_launch(launch_request)
        protected_roots = (
            ("project", self.project.resolve()),
            ("invocation", self.home.resolve()),
            ("state root", self.home.parent.resolve()),
        )
        for path in review_view.SYSTEM_BINDS:
            if not os.path.exists(path):
                continue
            source = Path(path).resolve()
            for name, root in protected_roots:
                if source == root or source in root.parents:
                    raise ValueError(
                        "review system bind source %s exposes %s directory %s" % (source, name, root)
                    )
        for mount in spec.mounts:
            source = Path(mount.source).resolve()
            for name, root in protected_roots:
                if source == root or source in root.parents or root in source.parents:
                    raise ValueError(
                        "review mount source %s overlaps %s directory %s" % (source, name, root)
                    )
        if not self._run_verifier(view):
            raise ValueError("pre-review verifier did not print the required VERIFIED identity")
        # The sandbox wrapper is for execution; interpretation needs the backend's object.
        interpretation_spec = spec
        if not spec.requires_review_sandbox:
            raise ValueError("review LaunchSpec requires sandbox isolation")
        reviewer_home = "/run/reviewer-home"
        configured_home = spec.env.get("HOME")
        if isinstance(configured_home, str) and os.path.isabs(configured_home):
            try:
                candidate = Path(configured_home).resolve(strict=False)
                restricted = (self.home.resolve(), Path.home().resolve(),
                              Path("/review").resolve(strict=False))
                blocked = candidate == Path(candidate.anchor) or any(
                    candidate == root or root in candidate.parents for root in restricted
                )
            except (OSError, RuntimeError, ValueError):
                blocked = True
            if not blocked:
                reviewer_home = configured_home
        reviewer_mounts = tuple(
            (Path(mount.source), mount.target, mount.writable) for mount in spec.mounts
        )
        reviewer_env = dict(spec.env)
        reviewer_env["HOME"] = reviewer_home
        spec = type(spec)(
            argv=tuple(review_view.reviewer_argv(
                Path(view["path"]), spec.argv, expected_identity=view["identity"],
                mounts=reviewer_mounts, home=reviewer_home)),
            env=reviewer_env, requires_review_sandbox=spec.requires_review_sandbox,
            stdout_format=spec.stdout_format, mounts=spec.mounts,
        )
        # Only packets without a reserved start or route are safe to replace.
        start_count = sum(item.get("kind") == "start" for item in events)
        if number > start_count and not (self.home / ("route-%d.json" % number)).exists():
            packet_path.unlink(missing_ok=True)
        _store_text(packet_path, packet.encode("utf-8"), 0o400)
        attempt_route = reviewer
        result = self._dispatch(
            role="review", route=attempt_route, plan=plan, request=request,
            task=request.task, packet_text=packet, author_start=author_start,
            source_snapshot=current_source, evidence_handle=handle, view=view,
            spec=spec, interpretation_spec=interpretation_spec, allocation=allocation,
            events=events, starts=starts, accounted=accounted,
        )
        return self._status_result(_read(self.home / "state.json").get("status"),
                                   start=result["start"])

    def _live_identity(self, number):
        path = self.home / ("execution-%d.json" % number)
        if not path.is_file():
            return None
        try:
            return _read(path)
        except (OSError, ValueError):
            return None

    def _reconcile(self, events, starts, accounted, unfinished, unsettled):
        if unfinished:
            number = unfinished[0]
            identity = self._live_identity(number)
            if identity is None:
                raise ValueError("cannot reconcile without a trustworthy process identity; process may be live")
            if _is_inside_process(identity):
                raise ValueError("cannot reconcile while the worker process is live")
            event = starts[number]
            if event.get("role") == "impl":
                self._check_project_confinement(number)
            journal.reconcile_start(self.home / "STATE.jsonl", self.unit_id, number,
                                    evidence="reserved worker process is no longer live")
            updates = {"settled_starts": sorted(set(
                _read(self.home / "decision.json").get("settled_starts", []) + [number]))}
            status = "REVIEW_INCOMPLETE"
            if event.get("role") == "review":
                decision = _read(self.home / "decision.json")
                failed = list(decision.get("failed_reviewers", []))
                if event["route"] not in failed:
                    failed.append(event["route"])
                updates["failed_reviewers"] = failed
                author = decision.get("current_author_start")
                updates["review_recovery_author"] = author
            else:
                status = "AUTHOR_INCOMPLETE"
            self._commit(status, decision_updates=updates)
            return self._status_result(status, start=number)
        if unsettled:
            number = unsettled[0]
            start = starts[number]
            retained = accounted[number]
            if retained.get("kind") == "reconcile":
                decision = _read(self.home / "decision.json")
                settled = sorted(set(decision.get("settled_starts", []) + [number]))
                updates = {"settled_starts": settled}
                status = "AUTHOR_INCOMPLETE"
                if start.get("role") == "review":
                    failed = list(decision.get("failed_reviewers", []))
                    if start["route"] not in failed:
                        failed.append(start["route"])
                    updates["failed_reviewers"] = failed
                    updates["review_recovery_author"] = decision.get("current_author_start")
                    status = "REVIEW_INCOMPLETE"
                self._commit(status, decision_updates=updates)
            else:
                attempt = retained.get("attempt")
                if not isinstance(attempt, dict):
                    raise ValueError("finished start is missing its retained attempt")
                self._settle_finished(number, start["role"], start["route"], attempt)
                status = _read(self.home / "state.json").get("status")
            return self._status_result(status, start=number)
        return self._status_result(_read(self.home / "state.json").get("status"))

    def _tests_acceptable(self, observation):
        if not isinstance(observation, dict) or observation.get("complete") is not True:
            return False, "observation incomplete"
        outcomes = observation.get("outcomes", {})
        baseline = self.contract.get("baseline_run", {}).get("outcomes", {})
        ids = observation.get("test_ids", [])
        if not isinstance(outcomes, dict) or set(ids) != set(outcomes) or set(ids) != set(baseline):
            return False, "test ID vectors differ"
        required = {test_id for values in self.contract.get("acceptance_test_ids", {}).values()
                    for test_id in values}
        if not required or any(outcomes.get(test_id, {}).get("outcome") != "PASS"
                               for test_id in required):
            return False, "acceptance tests are not green"
        if self.admitted["tests"]["baseline_policy"] == "all-green":
            return (bool(outcomes) and all(item.get("outcome") == "PASS"
                                           for item in outcomes.values()), "all-green policy")
        for test_id in ids:
            before = baseline[test_id].get("outcome")
            after = outcomes[test_id].get("outcome")
            if before == "PASS" and after != "PASS":
                return False, "test regression: " + test_id
            if after != "PASS" and before != after:
                return False, "changed pre-existing test outcome: " + test_id
        return True, "acceptable"

    def _close(self, events, starts, accounted, unfinished, unsettled):
        if unfinished or unsettled or len(accounted) != len(starts):
            raise ValueError("close requires every start accounted; unaccounted or RECONCILE_REQUIRED")
        decision = _read(self.home / "decision.json")
        accepted = decision.get("accepted_review")
        accepted_path = self.home / "accepted-review.json"
        if not isinstance(accepted, dict) or not accepted_path.is_file():
            raise ValueError("close requires an accepted review")
        try:
            if _read(accepted_path) != accepted:
                raise ValueError("accepted review record does not match controller state")
        except (OSError, ValueError, TypeError) as error:
            raise ValueError("close requires a valid accepted review") from error
        current = novelty.snapshot(self.project, self.admitted["owned_paths"])
        if accepted.get("source_snapshot") != current:
            raise ValueError("accepted review source snapshot is stale")
        author = decision.get("last_author")
        observation = author.get("observation", {}) if isinstance(author, dict) else {}
        current_test_inputs = evidence.snapshot(self.project, self.contract["test_scopes"])
        if current_test_inputs.get("files") != self.contract.get("frozen_test_inputs"):
            raise ValueError("current tests changed after admission")
        tests_ok, tests_reason = self._tests_acceptable(observation)
        if not tests_ok:
            raise ValueError("current tests are not acceptable: " + tests_reason)
        open_gaps = [row.get("id") for row in decision.get("gaps", [])
                     if row.get("id") != "independent-review" and row.get("state") != "PROVED"]
        if open_gaps:
            raise ValueError("acceptance gaps are not PROVED: " + ", ".join(open_gaps))
        if any(row.get("id") == "independent-review" and row.get("state") != "PROVED"
               for row in decision.get("gaps", [])):
            raise ValueError("independent review gap is not PROVED")
        self._commit(_read(self.home / "state.json").get("status"))
        close_decision = _read(self.home / "decision.json")
        close_decision["decision"] = "ACCEPTED"
        event = journal.close_outcome(self.home / "STATE.jsonl", self.unit_id,
                                      decision=close_decision)
        closure = {key: event[key] for key in
                   ("kind", "unit_id", "completed_starts", "cumulative_seconds",
                    "decision", "observed_at")}
        errors = schemas.validate("closure", closure)
        if errors:
            raise ValueError("invalid closure: " + "; ".join(errors))
        _write(self.home / "closure.json", closure, 0o444)
        committed = dict(decision)
        committed["decision"] = "ACCEPTED"
        self._commit("ACCEPTED", decision_updates=committed)
        return self._status_result("ACCEPTED", closure=closure)

    def perform(self, intent):
        if not isinstance(intent, dict):
            raise ValueError("intent must be an object")
        self._check_version(intent)
        action = intent.get("action")
        if action not in {"author", "repair", "review", "reconcile", "diagnose", "close"}:
            raise ValueError("unknown transaction action")
        if action in ("review", "close"):
            self._refuse_project_violation()
        events, starts, accounted, unfinished, unsettled = self._parts()
        if action == "diagnose":
            return {"status": _read(self.home / "state.json").get("status"),
                    "version": _read(self.home / "state.json").get("version"),
                    "starts": len(starts), "accounted": len(accounted),
                    "unaccounted": unfinished, "unsettled": unsettled,
                    "gaps": self._gaps()}
        if action == "reconcile":
            return self._reconcile(events, starts, accounted, unfinished, unsettled)
        if unfinished or unsettled:
            self._commit("RECONCILE_REQUIRED")
            return self._status_result("RECONCILE_REQUIRED")
        if action == "close":
            return self._close(events, starts, accounted, unfinished, unsettled)
        if action == "review":
            return self._review(events, starts, accounted)
        if action in ("author", "repair"):
            decision = _read(self.home / "decision.json")
            completed_review = decision.get("last_completed_review")
            if action == "author" and isinstance(completed_review, dict):
                raise ValueError("implementation after a completed review must use repair")
            targets = self._targets(intent)
            if action == "repair":
                rejected = decision.get("last_completed_review")
                current_author = decision.get("last_author")
                if (not isinstance(rejected, dict) or rejected.get("verdict") != "reject"
                        or not isinstance(current_author, dict)
                        or rejected.get("author_start") != current_author.get("start")):
                    raise ValueError("repair is allowed only after a completed REJECT review")
                rounds = decision.get("repair_rounds", 0)
                if rounds >= 2:
                    raise ValueError("repair round limit exhausted")
            allocation = self._budget(events, accounted)
            if allocation <= 0:
                return self._checkpoint_result()
            plan, request = self._plan(starts=starts, accounted=accounted,
                                       pinned_author=None, require_fallback=False)
            self._verify_prior_families(plan, starts)
            task = request.task
            role = "impl"
            packet_text = self.admitted["packet"]["text"]
            if action == "repair":
                current_rounds = decision.get("repair_rounds", 0) + 1
                self._commit(_read(self.home / "state.json").get("status"),
                             decision_updates={"repair_rounds": current_rounds,
                                               "repair_targets": list(targets)})
                result = self._dispatch(role=role, route=plan.author, plan=plan,
                                        request=request, task=task, packet_text=packet_text,
                                        allocation=allocation, events=events,
                                        starts=starts, accounted=accounted)
                if isinstance(result, dict) and result.get("status") == "CHECKPOINT_EXHAUSTED":
                    return result
                return self._status_result(_read(self.home / "state.json").get("status"),
                                           start=result["start"])
            result = self._dispatch(role=role, route=plan.author, plan=plan,
                                    request=request, task=task, packet_text=packet_text,
                                    allocation=allocation, events=events,
                                    starts=starts, accounted=accounted)
            return self._status_result(_read(self.home / "state.json").get("status"),
                                       start=result["start"] if "start" in result else None)
        raise ValueError("unsupported transaction action")
