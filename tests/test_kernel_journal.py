# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S2-1: the append-only parent journal (STATE.jsonl)."""
import ast
import importlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
UNIT = 'ordinary-supervise-unit-1'


def journal(test):
    try:
        return importlib.import_module('supervise.kernel.journal')
    except ImportError as error:
        test.fail('Kernel journal is not implemented: ' + str(error))


def schemas():
    return importlib.import_module('supervise.schemas')


def reserve(j, path, *, attempt='att-1', role='impl', timeout=60.0, max_starts=4, unit=UNIT, route='route-a'):
    return j.reserve_start(path, unit, route=route, packet_id='sha256:' + 'a' * 64, role=role, timeout=timeout,
                           attempt_id=attempt, execution={'pid': 1, 'start_time': 2, 'boot': 'b'},
                           decision={'dispatch': {'phase': 'author' if role == 'impl' else 'review'}},
                           max_starts=max_starts)


def finish(j, path, start, *, attempt='att-1', seconds=12.5, unit=UNIT):
    return j.finish_start(path, unit, start, attempt_id=attempt, seconds=seconds,
                          attempt={'attempt_id': attempt, 'verdict': 'pass'})


def _race(path, attempt, queue):
    import importlib as il
    j = il.import_module('supervise.kernel.journal')
    try:
        queue.put(('ok', reserve(j, Path(path), attempt=attempt, max_starts=50)))
    except j.JournalError as error:
        queue.put(('refused', str(error)))


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.j = journal(self)
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'STATE.jsonl'
        os.environ.pop(self.j.WORKER_ENV, None)

    def tearDown(self):
        os.environ.pop(self.j.WORKER_ENV, None)
        self.dir.cleanup()

    def bytes(self):
        return self.path.read_bytes() if self.path.exists() else b''

    def refused(self, call, *args, **kwargs):
        before = self.bytes()
        with self.assertRaises(self.j.JournalError):
            call(*args, **kwargs)
        self.assertEqual(self.bytes(), before, 'a refused operation must append nothing')

    def test_reserve_finish_close_happy_path_writes_schema_valid_records(self):
        self.assertEqual(reserve(self.j, self.path), 1)
        finish(self.j, self.path, 1)
        self.assertEqual(reserve(self.j, self.path, attempt='att-2', role='review'), 2)
        finish(self.j, self.path, 2, attempt='att-2', seconds=7.5)
        closed = self.j.close_outcome(self.path, UNIT, decision={'assessments': []})
        events = self.j.read_events(self.path)
        self.assertEqual([e['kind'] for e in events], ['start', 'finish', 'start', 'finish', 'outcome_closed'])
        self.assertEqual(closed['completed_starts'], 2)
        self.assertTrue(math.isclose(closed['cumulative_seconds'], 20.0))
        kinds = {'start': 'state-start', 'finish': 'state-finish', 'outcome_closed': 'state-outcome-closed'}
        for event in events:
            self.assertEqual(event['unit_id'], UNIT)
            self.assertEqual(schemas().validate(kinds[event['kind']], event), [], event)
        self.assertEqual(self.j.summary(self.path, UNIT),
                         {'starts': 2, 'accounted': 2, 'cumulative_seconds': 20.0, 'closed': True})
        for line in self.path.read_text().splitlines():
            self.assertIsInstance(json.loads(line), dict)

    def test_unfinished_reservation_blocks_another_start(self):
        reserve(self.j, self.path)
        self.refused(reserve, self.j, self.path, attempt='att-2')

    def test_closed_parent_admits_no_start_finish_or_second_close(self):
        reserve(self.j, self.path); finish(self.j, self.path, 1)
        self.j.close_outcome(self.path, UNIT, decision={})
        self.refused(reserve, self.j, self.path, attempt='att-2')
        self.refused(self.j.close_outcome, self.path, UNIT, decision={})

    def test_close_requires_every_start_accounted_and_at_least_one(self):
        self.refused(self.j.close_outcome, self.path, UNIT, decision={})
        reserve(self.j, self.path)
        self.refused(self.j.close_outcome, self.path, UNIT, decision={})

    def test_worker_bound_caller_is_refused_everywhere(self):
        reserve(self.j, self.path)
        os.environ[self.j.WORKER_ENV] = '1'
        self.refused(finish, self.j, self.path, 1)
        self.refused(self.j.reconcile_start, self.path, UNIT, 1, evidence='x')
        self.refused(self.j.close_outcome, self.path, UNIT, decision={})
        os.environ.pop(self.j.WORKER_ENV)
        finish(self.j, self.path, 1)
        os.environ[self.j.WORKER_ENV] = '1'
        self.refused(reserve, self.j, self.path, attempt='att-2')

    def test_start_limit_is_enforced(self):
        for n in (1, 2):
            reserve(self.j, self.path, attempt=f'a{n}', max_starts=2); finish(self.j, self.path, n, attempt=f'a{n}')
        self.refused(reserve, self.j, self.path, attempt='a3', max_starts=2)

    def test_invalid_arguments_are_refused(self):
        for bad in ({'timeout': 0}, {'timeout': -1.0}, {'timeout': float('inf')}, {'timeout': float('nan')},
                    {'role': 'manager'}, {'attempt': ''}):
            with self.subTest(bad=bad):
                self.refused(reserve, self.j, self.path, **bad)
        reserve(self.j, self.path)
        self.refused(finish, self.j, self.path, 1, seconds=-1.0)
        self.refused(finish, self.j, self.path, 1, seconds=float('nan'))
        self.refused(finish, self.j, self.path, 2)                       # never reserved
        self.refused(finish, self.j, self.path, 1, attempt='other')      # attempt id must match the reservation

    def test_finish_and_reconcile_account_each_start_once(self):
        reserve(self.j, self.path); finish(self.j, self.path, 1)
        self.refused(finish, self.j, self.path, 1)
        self.refused(self.j.reconcile_start, self.path, UNIT, 1, evidence='late')

    def test_reconcile_accounts_a_stopped_start_conservatively(self):
        reserve(self.j, self.path)
        event = self.j.reconcile_start(self.path, UNIT, 1, evidence='process gone')
        self.assertEqual(event['kind'], 'reconcile')
        self.assertTrue(event['accounted'])
        self.assertIsNone(event['attempt'])
        self.assertEqual((event['usage'], event['outcome']), ('unknown', 'unknown'))
        self.assertGreaterEqual(event['seconds'], 0)
        self.assertEqual(schemas().validate('state-reconcile', event), [])
        self.assertEqual(reserve(self.j, self.path, attempt='att-2'), 2)

    def test_foreign_unit_and_tampered_journal_fail_closed(self):
        reserve(self.j, self.path)
        self.refused(finish, self.j, self.path, 1, unit='another-unit')
        with self.path.open('a') as stream:
            stream.write('not json\n')
        self.refused(finish, self.j, self.path, 1)
        self.path.write_text(json.dumps([1, 2]) + '\n')
        self.refused(reserve, self.j, self.path)

    def test_existing_lines_are_never_rewritten(self):
        reserve(self.j, self.path)
        first = self.bytes()
        finish(self.j, self.path, 1)
        self.assertTrue(self.bytes().startswith(first))

    def test_concurrent_reservations_are_serialized_by_the_lock(self):
        ctx = multiprocessing.get_context('spawn')
        queue = ctx.Queue()
        workers = [ctx.Process(target=_race, args=(str(self.path), f'race-{n}', queue)) for n in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(60)
        results = [queue.get(timeout=5) for _ in workers]
        self.assertEqual(sum(1 for kind, _ in results if kind == 'ok'), 1, results)
        events = self.j.read_events(self.path)
        self.assertEqual([e['kind'] for e in events], ['start'])

    def test_journal_module_is_stdlib_only(self):
        names = set()
        for node in ast.walk(ast.parse(Path(self.j.__file__).read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names -= {'__future__', 'supervise'}
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))


if __name__ == '__main__':
    unittest.main()
