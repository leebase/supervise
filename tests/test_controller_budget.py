# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle: `supervise.controller.budget` (ports v2.8.2 budget.py / worker_allocation).

Public API fixed by this oracle (consistent with CONTROLLER-DESIGN.md; see NOTES.md):

    PINS = ('pending.json', 'acceptance.json', 'elapsed-checkpoint.json', 'contract.json', 'binding.json')
    class BudgetError(ValueError)
    sha(path) -> str                       # sha256 hex of the exact file bytes
    remaining(home) -> float               # seconds left in the newest valid elapsed window (negative when
                                           # expired); verifies the whole continuation chain on every call and
                                           # raises BudgetError on any tampering. Never writes.
    worker_allocation(home, admitted, starts, finishes, *, test_timeout_reserve=5.0) -> float
        admitted = the admitted intent (timeout_seconds, tests.timeout_seconds, checkpoint.max_starts/max_seconds);
        starts = journal start events; finishes = {start number: finish/reconcile event}.
        0.0 when len(starts) >= checkpoint.max_starts, otherwise
        max(0, min(timeout_seconds,                                  # admitted worker bound, no hidden ceiling
                   checkpoint.max_seconds - sum(accounted seconds),  # accounted native seconds
                   remaining(home) - (tests.timeout_seconds + test_timeout_reserve)))  # elapsed window
    fit_test_run(home, timeout_seconds, *, cleanup_seconds=5.0) -> float
        returns timeout_seconds when remaining(home) >= timeout_seconds + cleanup_seconds, else BudgetError
        containing 'cannot fit'.
    authorize(home, source, seconds) -> dict
        Operator-only same-objective continuation of an EXPIRED elapsed window. Refuses (BudgetError) when
        os.environ[supervise.kernel.journal.WORKER_ENV] is set ('worker'), acceptance.json carries non-empty
        `user_limits` ('outer limit'), seconds is not a finite number in (0, 7200], the home is not private
        ('private'), source is not a regular non-symlink file outside pending['cwd'] ('outside'), the current window is
        still active ('active window'), or any journal start is unaccounted ('unaccounted').
        Writes `continuation-NNNN-authority.txt` (exact source bytes) and `continuation-NNNN.json`, both 0444,
        NNNN = 0001, 0002, ... and returns the record:
        {kind: 'same_objective_continuation', number, previous_sha256 (sha of the previous continuation file or
         None), invocation_id, original_pins {PINS name: sha}, authority_source, authority_snapshot,
         authority_sha256, opened_at, max_seconds, starts, accounted_seconds,
         additional_starts: 0, additional_seconds: 0}
        It adds elapsed time only: never starts, never native seconds, never rewrites any original file.

Ports: budget/test-maximum-not-allowance, budget/admitted-worker-bound-honoured, budget/expired-refuses-renewal,
budget/continuation-keeps-original, budget/continuation-refuses-outer-and-worker, budget/test-timeout-bounded-cleanup
(fit check), budget/tampered-binding-blocks, budget/rpc-wakes-authorized-expired (allocation wakes after
authorization without resetting counters; the transport/RPC half is outside the controller).
"""
import ast
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
PINS = ('pending.json', 'acceptance.json', 'elapsed-checkpoint.json', 'contract.json', 'binding.json')
UNIT = 'supervise-inv-1'


def load(test, name):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        test.fail(name + ' is not implemented: ' + str(error))


def admitted(*, timeout=30, tests_timeout=30, max_seconds=900, max_starts=3):
    return {'timeout_seconds': timeout, 'tests': {'timeout_seconds': tests_timeout},
            'checkpoint': {'max_seconds': max_seconds, 'max_starts': max_starts, 'first_value_seconds': max_seconds}}


class BudgetFixture(unittest.TestCase):
    def setUp(self):
        self.m = load(self, 'supervise.controller.budget')
        self.j = load(self, 'supervise.kernel.journal')
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.project = base / 'project'
        self.project.mkdir()
        plan = self.project / 'PLAN.md'
        plan.write_text('CONTROL objective\n')
        self.home = base / 'private' / 'inv'
        self.home.mkdir(mode=0o700, parents=True)
        self.write('pending.json', {'invocation_id': 'inv-1', 'cwd': str(self.project), 'plan_path': str(plan),
                                    'plan_sha256': hashlib.sha256(plan.read_bytes()).hexdigest(),
                                    'created_at': '2026-10-08T00:00:00+00:00'}, mode=0o400)
        self.write('acceptance.json', {'intent': admitted(), 'user_limits': {}})
        self.write('contract.json', {'CONTROL': 'unchanged'})
        self.write('binding.json', {'CONTROL': 'unchanged'})
        self.window(opened_at=time.time(), max_seconds=900)
        self.source = base / 'operator-direction.txt'
        self.source.write_text('Operator explicitly directs same-objective continuation.\n')
        os.environ.pop(self.j.WORKER_ENV, None)

    def tearDown(self):
        os.environ.pop(self.j.WORKER_ENV, None)
        for path in Path(self.dir.name).rglob('*'):
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self.dir.cleanup()

    def write(self, name, value, mode=0o444):
        path = self.home / name
        if path.exists():
            os.chmod(path, 0o600)
        path.write_text(json.dumps(value))
        os.chmod(path, mode)
        return path

    def window(self, *, opened_at, max_seconds):
        self.write('elapsed-checkpoint.json', {'opened_at': opened_at, 'max_seconds': max_seconds})

    def expire(self):
        self.window(opened_at=time.time() - 121, max_seconds=120)

    def pins(self):
        return {name: hashlib.sha256((self.home / name).read_bytes()).hexdigest() for name in PINS}

    def files(self):
        return sorted(p.name for p in self.home.iterdir() if not p.name.endswith('.lock'))

    def reserve(self, n=1):
        return self.j.reserve_start(self.home / 'STATE.jsonl', UNIT, route='author-a', packet_id='sha256:' + 'a' * 64,
                                    role='impl', timeout=30.0, attempt_id=f'att-{n}',
                                    execution={'pid': 1, 'start_time': 2, 'boot': 'b'},
                                    decision={'dispatch': {'phase': 'author'}}, max_starts=8)

    def finish(self, n=1, seconds=12.5):
        self.j.finish_start(self.home / 'STATE.jsonl', UNIT, n, attempt_id=f'att-{n}', seconds=seconds,
                            attempt={'attempt_id': f'att-{n}', 'verdict': 'pass'})

    def refused(self, fragment, call, *args, **kwargs):
        before = (self.pins(), self.files())
        with self.assertRaises(self.m.BudgetError) as caught:
            call(*args, **kwargs)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertIn(fragment.lower(), str(caught.exception).lower())
        self.assertEqual((self.pins(), self.files()), before, 'a refusal writes nothing')


class AllocationTests(BudgetFixture):
    def test_published_pins_and_sha(self):
        self.assertEqual(tuple(self.m.PINS), PINS)
        self.assertTrue(issubclass(self.m.BudgetError, ValueError))
        path = self.home / 'pending.json'
        self.assertEqual(self.m.sha(path), hashlib.sha256(path.read_bytes()).hexdigest())

    def test_test_maximum_is_not_consumed_allowance(self):
        """budget/test-maximum-not-allowance"""
        self.window(opened_at=time.time(), max_seconds=900)
        value = admitted(timeout=1200, tests_timeout=1200, max_seconds=900, max_starts=3)
        self.assertEqual(self.m.worker_allocation(self.home, value, [], {}), 0)   # cannot fit; no silent clamp
        value = admitted(timeout=1200, tests_timeout=30, max_seconds=900, max_starts=3)
        allocation = self.m.worker_allocation(self.home, value, [], {})
        self.assertTrue(860 < allocation <= 865, allocation)                   # 900 - (30 + 5)
        self.assertEqual(self.m.worker_allocation(self.home, value, [{}, {}, {}], {}), 0)
        self.assertEqual(self.m.worker_allocation(self.home, value, [{}], {1: {'accounted': True, 'seconds': 880}}), 20)
        # A start whose accounting is unknown consumes no recorded seconds here, but still counts as a start.
        self.assertEqual(self.m.worker_allocation(self.home, admitted(timeout=10, max_starts=1), [{}], {}), 0)

    def test_test_time_reserve_is_explicit(self):
        value = admitted(timeout=1200, tests_timeout=30, max_seconds=900, max_starts=3)
        allocation = self.m.worker_allocation(self.home, value, [], {}, test_timeout_reserve=100.0)
        self.assertTrue(765 < allocation <= 770, allocation)                   # 900 - (30 + 100)

    def test_admitted_worker_bound_is_honoured_without_hidden_ceiling(self):
        """budget/admitted-worker-bound-honoured"""
        self.window(opened_at=time.time(), max_seconds=3000)
        value = admitted(timeout=900, tests_timeout=30, max_seconds=3000, max_starts=6)
        self.assertTrue(899 < self.m.worker_allocation(self.home, value, [], {}) <= 900)
        value['timeout_seconds'] = 120
        self.assertTrue(119 < self.m.worker_allocation(self.home, value, [], {}) <= 120)
        value['timeout_seconds'] = 3600
        self.assertEqual(self.m.worker_allocation(self.home, value, [{}], {1: {'accounted': True, 'seconds': 2500}}), 500)
        self.window(opened_at=time.time() - 2700, max_seconds=3000)
        allocation = self.m.worker_allocation(self.home, value, [], {})
        self.assertTrue(260 < allocation <= 265, allocation)                   # 300 - (30 + 5)

    def test_expired_original_refuses_without_renewal(self):
        """budget/expired-refuses-renewal"""
        self.expire()
        before = (self.pins(), self.files())
        self.assertLessEqual(self.m.remaining(self.home), 0)
        self.assertEqual(self.m.worker_allocation(self.home, admitted(), [], {}), 0)
        self.assertLessEqual(self.m.remaining(self.home), 0, 'reading the window must not renew it')
        self.assertEqual((self.pins(), self.files()), before)

    def test_fit_test_run_bounds_test_time_and_cleanup(self):
        """budget/test-timeout-bounded-cleanup (fit half; the bounded run itself is evidence.observe's oracle)"""
        with mock.patch.object(self.m, 'remaining', lambda home: 5.2):
            with self.assertRaises(self.m.BudgetError) as caught:
                self.m.fit_test_run(self.home, 1200)
            self.assertIn('cannot fit', str(caught.exception).lower())
            self.assertEqual(self.m.fit_test_run(self.home, 0.2), 0.2)
            with self.assertRaises(self.m.BudgetError):
                self.m.fit_test_run(self.home, 0.2, cleanup_seconds=5.1)


class ContinuationTests(BudgetFixture):
    def test_explicit_continuation_keeps_all_original_bytes(self):
        """budget/continuation-keeps-original"""
        self.expire()
        pins = self.pins()
        record = self.m.authorize(self.home, self.source, 60)
        self.assertEqual(record['original_pins'], pins)
        self.assertEqual(record['kind'], 'same_objective_continuation')
        self.assertEqual(record['invocation_id'], 'inv-1')
        self.assertIsNone(record['previous_sha256'])
        self.assertEqual((record['additional_starts'], record['additional_seconds']), (0, 0))
        self.assertEqual(record['max_seconds'], 60)
        self.assertTrue(58 < self.m.remaining(self.home) <= 60)
        self.assertEqual(self.pins(), pins, 'every original pinned file stays byte-identical')
        path = self.home / 'continuation-0001.json'
        snapshot = self.home / 'continuation-0001-authority.txt'
        self.assertEqual(json.loads(path.read_text()), record)
        self.assertEqual(snapshot.read_bytes(), self.source.read_bytes())
        self.assertEqual(record['authority_sha256'], hashlib.sha256(self.source.read_bytes()).hexdigest())
        for item in (path, snapshot):
            self.assertFalse(item.stat().st_mode & 0o222, item.name)
        self.refused('active window', self.m.authorize, self.home, self.source, 60)

    def test_continuation_retains_counters_and_adds_no_starts(self):
        """budget/rpc-wakes-authorized-expired: allocation wakes, counters are never reset."""
        self.reserve(1)
        self.finish(1, seconds=100.0)
        self.expire()
        value = admitted(timeout=30, tests_timeout=10, max_seconds=900, max_starts=2)
        starts = [{'start': 1}]
        finishes = {1: {'accounted': True, 'seconds': 100.0}}
        self.assertEqual(self.m.worker_allocation(self.home, value, starts, finishes), 0)
        record = self.m.authorize(self.home, self.source, 120)
        self.assertEqual(record['starts'], 1)
        self.assertTrue(math.isclose(record['accounted_seconds'], 100.0))
        self.assertTrue(29 < self.m.worker_allocation(self.home, value, starts, finishes) <= 30)
        self.assertEqual(self.m.worker_allocation(self.home, value, starts * 2, finishes), 0, 'no extra starts')
        self.assertEqual(self.m.worker_allocation(self.home, value, starts, {1: {'accounted': True, 'seconds': 890.0}}),
                         10, 'no extra native seconds')

    def test_continuation_refuses_worker_role(self):
        """budget/continuation-refuses-outer-and-worker (worker half)"""
        self.expire()
        with mock.patch.dict(os.environ, {self.j.WORKER_ENV: '1'}):
            self.refused('worker', self.m.authorize, self.home, self.source, 60)

    def test_continuation_refuses_outer_limits(self):
        """budget/continuation-refuses-outer-and-worker (outer-limit half)"""
        self.expire()
        self.write('acceptance.json', {'intent': admitted(), 'user_limits': {'seconds': 10}})
        self.refused('outer limit', self.m.authorize, self.home, self.source, 60)

    def test_authority_source_must_be_outside_the_project(self):
        self.expire()
        inside = self.project / 'direction.txt'
        inside.write_text('a worker could write this\n')
        self.refused('outside', self.m.authorize, self.home, inside, 60)
        link = Path(self.dir.name) / 'link.txt'
        link.symlink_to(self.source)
        with self.assertRaises(self.m.BudgetError):
            self.m.authorize(self.home, link, 60)
        with self.assertRaises(self.m.BudgetError):
            self.m.authorize(self.home, Path(self.dir.name) / 'missing.txt', 60)

    def test_window_must_be_finite_and_bounded(self):
        self.expire()
        for seconds in (0, -1, float('nan'), float('inf'), 7201, True):
            with self.subTest(seconds=seconds):
                with self.assertRaises(self.m.BudgetError):
                    self.m.authorize(self.home, self.source, seconds)
        self.assertFalse((self.home / 'continuation-0001.json').exists())

    def test_home_must_be_private(self):
        self.expire()
        os.chmod(self.home, 0o755)
        try:
            with self.assertRaises(self.m.BudgetError) as caught:
                self.m.authorize(self.home, self.source, 60)
            self.assertIn('private', str(caught.exception).lower())
        finally:
            os.chmod(self.home, 0o700)

    def test_unaccounted_start_blocks_continuation(self):
        self.reserve(1)
        self.expire()
        self.refused('unaccounted', self.m.authorize, self.home, self.source, 60)
        self.finish(1)
        self.assertEqual(self.m.authorize(self.home, self.source, 60)['starts'], 1)

    def test_continuations_chain_immutably(self):
        self.expire()
        first = self.m.authorize(self.home, self.source, 0.3)
        time.sleep(0.5)
        self.assertLessEqual(self.m.remaining(self.home), 0)
        first_bytes = (self.home / 'continuation-0001.json').read_bytes()
        second = self.m.authorize(self.home, self.source, 60)
        self.assertEqual(second['previous_sha256'], hashlib.sha256(first_bytes).hexdigest())
        self.assertEqual(second['original_pins'], first['original_pins'])
        self.assertTrue((self.home / 'continuation-0002.json').is_file())
        self.assertTrue((self.home / 'continuation-0002-authority.txt').is_file())
        self.assertEqual((self.home / 'continuation-0001.json').read_bytes(), first_bytes)
        self.assertTrue(58 < self.m.remaining(self.home) <= 60)

    def test_broken_chain_is_refused(self):
        self.expire()
        self.m.authorize(self.home, self.source, 0.3)
        time.sleep(0.5)
        self.m.authorize(self.home, self.source, 60)
        first = self.home / 'continuation-0001.json'
        record = json.loads(first.read_text())
        os.chmod(first, 0o600)
        first.write_text(json.dumps(record, indent=1))           # same meaning, different bytes
        os.chmod(first, 0o444)
        with self.assertRaises(self.m.BudgetError):
            self.m.remaining(self.home)

    def test_sequence_gap_is_refused(self):
        self.expire()
        self.m.authorize(self.home, self.source, 0.3)
        time.sleep(0.5)
        self.m.authorize(self.home, self.source, 60)
        os.rename(self.home / 'continuation-0002.json', self.home / 'continuation-0003.json')
        with self.assertRaises(self.m.BudgetError):
            self.m.remaining(self.home)


class TamperTests(BudgetFixture):
    def setUp(self):
        super().setUp()
        self.expire()
        self.m.authorize(self.home, self.source, 60)

    def test_tampered_original_binding_blocks_continuation(self):
        """budget/tampered-binding-blocks"""
        data = json.loads((self.home / 'elapsed-checkpoint.json').read_text())
        data['opened_at'] += 1
        self.write('elapsed-checkpoint.json', data)
        with self.assertRaises(self.m.BudgetError):
            self.m.remaining(self.home)
        with self.assertRaises(self.m.BudgetError):
            self.m.worker_allocation(self.home, admitted(), [], {})

    def test_every_pinned_file_is_bound(self):
        for name in ('pending.json', 'acceptance.json', 'contract.json', 'binding.json'):
            with self.subTest(name=name):
                path = self.home / name
                original, mode = path.read_bytes(), path.stat().st_mode & 0o777
                os.chmod(path, 0o600)
                path.write_bytes(original + b' ')
                os.chmod(path, mode)
                try:
                    with self.assertRaises(self.m.BudgetError):
                        self.m.remaining(self.home)
                finally:
                    os.chmod(path, 0o600)
                    path.write_bytes(original)
                    os.chmod(path, mode)
        self.assertTrue(0 < self.m.remaining(self.home) <= 60)

    def test_changed_authority_snapshot_or_writable_record_is_refused(self):
        snapshot = self.home / 'continuation-0001-authority.txt'
        os.chmod(snapshot, 0o600)
        snapshot.write_text('forged direction\n')
        os.chmod(snapshot, 0o444)
        with self.assertRaises(self.m.BudgetError):
            self.m.remaining(self.home)
        snapshot_ok = self.source.read_bytes()
        os.chmod(snapshot, 0o600)
        snapshot.write_bytes(snapshot_ok)
        os.chmod(snapshot, 0o444)
        self.assertTrue(0 < self.m.remaining(self.home) <= 60)
        os.chmod(self.home / 'continuation-0001.json', 0o644)
        with self.assertRaises(self.m.BudgetError):
            self.m.remaining(self.home)


class StdlibTests(unittest.TestCase):
    def test_budget_module_is_stdlib_only(self):
        module = load(self, 'supervise.controller.budget')
        names = set()
        for node in ast.walk(ast.parse(Path(module.__file__).read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names -= {'__future__', 'supervise'}
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))


if __name__ == '__main__':
    unittest.main()
