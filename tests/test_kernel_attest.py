# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S2-5: route attestation, receipts, verdict and novelty."""
import ast
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
SHA = 'a' * 64


def load(test, name):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        test.fail(name + ' is not implemented: ' + str(error))


class AttestationTests(unittest.TestCase):
    def setUp(self):
        self.a = load(self, 'supervise.kernel.attest')
        self.dir = tempfile.TemporaryDirectory()
        self.home = Path(self.dir.name) / 'inv'
        self.home.mkdir(mode=0o700)
        crew = {'schema': 'supervised-crew/1', 'selected': {'author': 'route-a', 'reviewer': 'route-b'}}
        self.crew_sha = self.a.digest(crew)
        self.crew_path = self.home / f'crew-1-{self.crew_sha}.json'
        self.crew_path.write_text(json.dumps(dict(crew, crew_sha256=self.crew_sha)))
        os.chmod(self.crew_path, 0o444)
        self.decision = {'completed_starts': 0, 'dispatch': {'phase': 'author'}}
        self.record = self.a.route_record(
            mission='m-1', transaction='u-1', role='impl', route_id='route-a', class_key='impl/deterministic/s/python',
            packet_id='sha256:' + SHA, crew_path=self.crew_path, crew_sha256=self.crew_sha, publication_sha256=SHA,
            contract_sha256=SHA, decision=self.decision, classification_sha256=SHA,
            controller={'pid': 10, 'start_time': 20, 'boot': 'b'}, controller_path='supervise/controller/run.py')
        self.path = self.a.write_route_record(self.home, 1, self.record)
        self.execution = {'pid': 11, 'start_time': 21, 'boot': 'b'}
        self.start = {'kind': 'start', 'unit_id': 'u-1', 'start': 1, 'route': 'route-a', 'packet_id': 'sha256:' + SHA,
                      'decision': self.decision, 'execution': self.execution}

    def tearDown(self):
        for path in Path(self.dir.name).rglob('*'):
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self.dir.cleanup()

    def finish(self, attempt_id='att-1', **receipt_overrides):
        receipt = self.a.receipt(self.path, execution=self.execution, start=1, attempt_id=attempt_id)
        receipt.update(receipt_overrides)
        return {'kind': 'finish', 'unit_id': 'u-1', 'start': 1,
                'attempt': {'attempt_id': attempt_id, 'routing_attestation': receipt}}

    def test_digest_is_canonical_json_sha256(self):
        value = {'b': [1, 2], 'a': 'é'}
        expected = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                                  .encode('utf-8')).hexdigest()
        self.assertEqual(self.a.digest(value), expected)
        self.assertEqual(self.a.digest({'a': 'é', 'b': [1, 2]}), expected)

    def test_route_record_matches_the_schema_and_binds_the_decision(self):
        schemas = importlib.import_module('supervise.schemas')
        self.assertEqual(schemas.validate('route-record', self.record), [])
        self.assertEqual(self.record['decision_sha256'], self.a.digest(self.decision))
        self.assertEqual(self.record['route'], 'route-a')
        self.assertEqual(self.path, self.home / 'route-1.json')
        self.assertFalse(self.path.stat().st_mode & 0o222)
        with self.assertRaises(self.a.AttestationError):
            self.a.write_route_record(self.home, 1, self.record)       # never overwritten

    def test_receipt_shares_one_attempt_id_and_is_self_hashed(self):
        receipt = self.a.receipt(self.path, execution=self.execution, start=1, attempt_id='att-1')
        self.assertEqual(receipt['attempt_id'], 'att-1')
        self.assertEqual(receipt['receipt_sha256'], self.a.digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'}))
        self.assertEqual(receipt['sha256'], self.a.digest(json.loads(self.path.read_text())))
        for value in (None, ''):
            with self.assertRaises(self.a.AttestationError):
                self.a.receipt(self.path, execution=self.execution, start=1, attempt_id=value)

    def test_verify_receipt_accepts_the_genuine_chain(self):
        result = self.a.verify_receipt(self.home, self.start, self.finish())
        self.assertEqual(result['attempt_id'], 'att-1')

    def test_verify_receipt_names_every_substitution(self):
        def resigned(**changes):
            def mutate(finish):
                receipt = finish['attempt']['routing_attestation']
                receipt.update(changes)
                receipt['receipt_sha256'] = self.a.digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})
            return mutate

        def counterfeit(finish):
            finish['attempt']['routing_attestation']['attempt_id'] = 'att-x'      # hash no longer matches

        cases = {
            'counterfeit hash': counterfeit,
            'record attempt id': lambda f: f['attempt'].update(attempt_id='att-2'),
            'route substitution': resigned(route_id='route-z'),
            'execution identity': resigned(execution={'pid': 99, 'start_time': 1, 'boot': 'b'}),
            'start number': resigned(start=2),
            'foreign transaction': resigned(transaction='u-9'),
            'record path': resigned(path=str(self.home / 'route-2.json')),
            'missing receipt': lambda f: f['attempt'].pop('routing_attestation'),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                finish = self.finish()
                mutate(finish)
                with self.assertRaises(self.a.AttestationError):
                    self.a.verify_receipt(self.home, self.start, finish)

    def test_verify_receipt_detects_changed_reservation_crew_or_decision(self):
        start = copy.deepcopy(self.start); start['decision'] = {'completed_starts': 9}
        with self.assertRaises(self.a.AttestationError):
            self.a.verify_receipt(self.home, start, self.finish())
        start = copy.deepcopy(self.start); start['route'] = 'route-b'
        with self.assertRaises(self.a.AttestationError):
            self.a.verify_receipt(self.home, start, self.finish())
        finish = self.finish()
        os.chmod(self.crew_path, 0o600); self.crew_path.write_text('{}'); os.chmod(self.crew_path, 0o444)
        with self.assertRaises(self.a.AttestationError):
            self.a.verify_receipt(self.home, self.start, finish)

    def test_writable_route_record_is_not_trusted(self):
        finish = self.finish()
        os.chmod(self.path, 0o644)
        with self.assertRaises(self.a.AttestationError):
            self.a.verify_receipt(self.home, self.start, finish)


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.v = load(self, 'supervise.kernel.verdict')

    def test_last_marker_wins_and_must_be_in_captured_stdout(self):
        accept, reject = 'REVIEW VERDICT: ACCEPT', 'REVIEW VERDICT: REJECT'
        self.assertEqual(self.v.parse_verdict('x\n' + accept, (accept + '"}').encode()), 'accept')
        self.assertEqual(self.v.parse_verdict(f'I considered {accept} but\n{reject}', (accept + reject).encode()), 'reject')
        self.assertEqual(self.v.parse_verdict(f'{reject} then {accept}', (reject + accept).encode()), 'accept')
        self.assertIsNone(self.v.parse_verdict('no marker', b'no marker'))
        self.assertIsNone(self.v.parse_verdict(accept, b'stdout never contained it'))
        self.assertIsNone(self.v.parse_verdict(accept, reject.encode()))


class NoveltyTests(unittest.TestCase):
    def setUp(self):
        self.n = load(self, 'supervise.kernel.novelty')
        self.dir = tempfile.TemporaryDirectory()
        self.project = Path(self.dir.name)
        (self.project / 'pkg').mkdir()
        (self.project / 'pkg' / 'a.py').write_text('A = 1\n')
        (self.project / 'pkg' / '__pycache__').mkdir()
        (self.project / 'pkg' / '__pycache__' / 'a.pyc').write_bytes(b'x')
        (self.project / 'other.py').write_text('B = 2\n')

    def tearDown(self):
        self.dir.cleanup()

    def test_snapshot_covers_owned_sources_only(self):
        snap = self.n.snapshot(self.project, ['pkg/'])
        self.assertEqual(snap['status'], 'VALID')
        self.assertEqual(snap['artifacts'], {'pkg/a.py': hashlib.sha256(b'A = 1\n').hexdigest()})

    def test_single_owned_file_is_supported(self):
        snap = self.n.snapshot(self.project, ['pkg/a.py'])
        self.assertEqual(snap['artifacts'], {'pkg/a.py': hashlib.sha256(b'A = 1\n').hexdigest()})

    def test_unchanged_evidence_earns_no_new_review(self):
        before = self.n.snapshot(self.project, ['pkg/'])
        self.assertFalse(self.n.review_allowed(before, self.n.snapshot(self.project, ['pkg/'])))
        (self.project / 'other.py').write_text('B = 3\n')
        self.assertFalse(self.n.review_allowed(before, self.n.snapshot(self.project, ['pkg/'])))
        (self.project / 'pkg' / 'a.py').write_text('A = 2\n')
        self.assertTrue(self.n.review_allowed(before, self.n.snapshot(self.project, ['pkg/'])))
        self.assertTrue(self.n.review_allowed(None, before))

    def test_unsafe_owned_paths_are_refused(self):
        for owned in (['../escape'], ['/abs']):
            with self.assertRaises(ValueError):
                self.n.snapshot(self.project, owned)


class StdlibTests(unittest.TestCase):
    def test_modules_are_stdlib_only(self):
        for name in ('supervise.kernel.attest', 'supervise.kernel.verdict', 'supervise.kernel.novelty'):
            module = load(self, name)
            names = set()
            for node in ast.walk(ast.parse(Path(module.__file__).read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names.add(node.module.split('.')[0])
            names -= {'__future__', 'supervise'}
            self.assertTrue(names <= STDLIB, (name, sorted(names - STDLIB)))


if __name__ == '__main__':
    unittest.main()
