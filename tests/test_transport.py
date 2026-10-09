# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S5-1: the public transport (port of v2.8.2 command.py ManagerWatch etc.).

Ports the seven manager-fault conformance scenarios by name, plus state-root resolution and the finite host window.
"""
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))


def load(test):
    try:
        return importlib.import_module('supervise.transport')
    except ImportError as error:
        test.fail('Public transport is not implemented: ' + str(error))


def error_turn(message):
    return {'type': 'turn_end', 'message': {'stopReason': 'error', 'errorMessage': message}}


class ManagerFaultTests(unittest.TestCase):
    def setUp(self):
        self.t = load(self)
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name)
        self.now = [100.0]

    def tearDown(self):
        self.dir.cleanup()

    def watch(self):
        return self.t.ManagerWatch(self.home, clock=lambda: self.now[0])

    def ledger(self, **value):
        (self.home / 'manager-turns.json').write_text(json.dumps(value))

    def test_constants(self):
        self.assertEqual(self.t.MANAGER_ERROR_TURNS, 2)
        self.assertEqual(self.t.MANAGER_STALL_GRACE_SECONDS, 20.0)

    def test_manager_fault_single_error_not_fatal(self):
        watch = self.watch()
        self.assertIsNone(watch.observe(error_turn('model unsupported')))
        self.assertIsNone(watch.observe({'type': 'turn_end', 'message': {'stopReason': 'stop'}}))
        self.assertIsNone(watch.observe(error_turn('model unsupported')))

    def test_manager_fault_consecutive_errors_stop(self):
        watch = self.watch()
        message = "The 'gpt-x' model is not supported when using Codex with a ChatGPT account."
        self.assertIsNone(watch.observe(error_turn(message)))
        self.assertEqual(watch.observe(error_turn(message)), ('MANAGER_ERROR', message))

    def test_manager_fault_stopped_nudges_stall(self):
        watch = self.watch()
        self.ledger(turns=3, version=0, idle=2)
        self.assertIsNone(watch.observe({'type': 'agent_end'}))
        self.assertIsNone(watch.stalled())
        self.now[0] += self.t.MANAGER_STALL_GRACE_SECONDS
        status, detail = watch.stalled()
        self.assertEqual(status, 'MANAGER_STALLED')
        self.assertIn('"idle": 2', detail)

    def test_manager_fault_progress_never_stall(self):
        watch = self.watch()
        self.ledger(turns=2, version=1, idle=0)
        watch.observe({'type': 'agent_end'})
        self.now[0] += 1000
        self.assertIsNone(watch.stalled())
        self.ledger(turns=3, version=1, idle=2)
        watch.observe({'type': 'agent_end'})
        watch.observe({'type': 'turn_start'})
        self.now[0] += 1000
        self.assertIsNone(watch.stalled())

    def test_manager_fault_late_ledger_detected(self):
        watch = self.watch()
        self.ledger(turns=2, version=1, idle=1)
        watch.observe({'type': 'agent_end'})
        self.assertIsNone(watch.stalled())
        self.ledger(turns=3, version=1, idle=2)
        self.assertIsNone(watch.stalled())                      # the first poll that sees exhaustion arms the grace
        self.now[0] += self.t.MANAGER_STALL_GRACE_SECONDS - 1
        self.assertIsNone(watch.stalled())
        self.now[0] += 1
        self.assertEqual(watch.stalled()[0], 'MANAGER_STALLED')

    def test_manager_fault_late_eighth_turn(self):
        watch = self.watch()
        self.ledger(turns=7, version=4, idle=0)
        watch.observe({'type': 'agent_end'})
        self.ledger(turns=8, version=5, idle=0)
        self.now[0] += self.t.MANAGER_STALL_GRACE_SECONDS + 1
        self.assertIsNone(watch.stalled())
        self.now[0] += self.t.MANAGER_STALL_GRACE_SECONDS
        self.assertEqual(watch.stalled()[0], 'MANAGER_STALLED')

    def test_manager_fault_new_turn_cancels_late_ledger(self):
        watch = self.watch()
        self.ledger(turns=2, version=1, idle=1)
        watch.observe({'type': 'agent_end'})
        watch.observe({'type': 'turn_start'})
        self.ledger(turns=3, version=1, idle=2)
        self.now[0] += 1000
        self.assertIsNone(watch.stalled())

    def test_unreadable_ledger_never_stalls(self):
        watch = self.watch()
        (self.home / 'manager-turns.json').write_text('not json')
        watch.observe({'type': 'agent_end'})
        self.now[0] += 1000
        self.assertIsNone(watch.stalled())


class StateRootTests(unittest.TestCase):
    def setUp(self):
        self.t = load(self)
        self.saved = {k: os.environ.get(k) for k in ('SUPERVISE_STATE', 'XDG_STATE_HOME', 'HOME')}

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_state_root_precedence(self):
        os.environ['HOME'] = '/tmp/h'
        os.environ.pop('SUPERVISE_STATE', None); os.environ.pop('XDG_STATE_HOME', None)
        self.assertEqual(self.t.state_root(), Path('/tmp/h/.local/state/supervise'))
        os.environ['XDG_STATE_HOME'] = '/tmp/x'
        self.assertEqual(self.t.state_root(), Path('/tmp/x/supervise'))
        os.environ['SUPERVISE_STATE'] = '/tmp/s'
        self.assertEqual(self.t.state_root(), Path('/tmp/s'))
        os.environ['SUPERVISE_STATE'] = 'relative'
        with self.assertRaises(ValueError):
            self.t.state_root()

    def test_invocation_identity_is_derived_from_project_and_plan(self):
        root = Path('/tmp/s')
        expected = root / hashlib.sha256(('/p' + '\0' + '/p/PLAN.md').encode()).hexdigest()
        self.assertEqual(self.t.invocation_path(root, Path('/p'), Path('/p/PLAN.md')), expected)

    def test_host_window_is_finite_and_bounded(self):
        for good in (1, 1800, 7200):
            self.t.validate_window(good)
        for bad in (0, -1, 7201, float('inf'), float('nan'), True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.t.validate_window(bad)

    def test_receipt_shape(self):
        receipt = self.t.make_receipt(status='ACCEPTED', admission_phase='ADMITTED', native_starts=2,
                                      invocation=Path('/tmp/s/i'), began_at=1.0, ended_at=3.5, manager_fault=None)
        self.assertEqual(receipt['delivery'], 'DELIVERED')
        self.assertEqual(self.t.make_receipt(status='MANAGER_STALLED', admission_phase='ADMITTED', native_starts=0,
                                             invocation=Path('/tmp/s/i'), began_at=1.0, ended_at=3.5,
                                             manager_fault='stalled')['delivery'], 'UNDELIVERED')
        self.assertEqual(set(receipt) >= {'status', 'admission_phase', 'native_starts', 'delivery', 'invocation',
                                          'began_at', 'ended_at', 'manager_fault'}, True)

    def test_transport_is_stdlib_only_and_has_no_private_paths(self):
        source = Path(self.t.__file__).read_text(encoding='utf-8')
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names -= {'__future__', 'supervise'}
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))
        self.assertNotIn('.omp/', source)


if __name__ == '__main__':
    unittest.main()
