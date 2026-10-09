# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S1-2: record schemas, stdlib validator, derivation ledger."""
import ast
import copy
import importlib
import json
from pathlib import Path
import sys
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
SHA = 'a' * 64
# Required fields, derived field-by-field from the accepted v2.8.2 records.
REQUIRED = {
    'state-start': {'kind', 'unit_id', 'start', 'route', 'packet_id', 'reserved_at', 'execution', 'decision'},
    'state-finish': {'kind', 'unit_id', 'start', 'accounted', 'seconds', 'attempt', 'controller_finished_at'},
    'state-reconcile': {'kind', 'unit_id', 'start', 'accounted', 'seconds', 'attempt', 'usage', 'outcome', 'time_basis', 'evidence'},
    'state-outcome-closed': {'kind', 'unit_id', 'completed_starts', 'cumulative_seconds', 'decision', 'observed_at'},
    'route-record': {'schema', 'mission', 'transaction', 'role', 'route', 'class_key', 'packet_id', 'crew_path', 'crew_sha256',
                     'publication_sha256', 'contract_sha256', 'decision_sha256', 'classification_sha256', 'controller', 'controller_path'},
    'crew': {'schema', 'crew_sha256', 'selected', 'excluded', 'rationale', 'cost_status', 'created_at'},
    'attempt-outcome': {'attempt_id', 'verdict', 'failure_class', 'usage', 'cost', 'wall_clock_ms', 'routing_attestation', 'captured_at'},
    'closure': {'kind', 'unit_id', 'completed_starts', 'cumulative_seconds', 'decision', 'observed_at'},
    'publication-manifest': {'release', 'version', 'state', 'files', 'predecessor'},
}
SOURCES = {  # v2.8.2 file -> accepted sha256
    'ordinary_supervise.py': '812c37b99f019244dee74415797a30a7f4c9f7fec88786f2974da409d55b08d7',
    'unit_admission.py': '0a4ea9ea59c2e98df27d0f3a4269c6703c8f3ecc55d4acc1d7f4af572598c7d5',
    'route_attestation.py': 'b613737be4060ce9573c903db8f52f9e63cb615e63c641de0225058905640a0c',
    'crew.py': 'b6933a5b75c9cfc63914e9e65b3f148d02bb5aaab6f892c158755a1e196c51c5',
    'publication.json': '2e5845bd49fcd2fffa1eab96a04b64b39e0fad868e8387dcd55e5d268a26e511',
}
ATTEMPT = {'attempt_id': 'router-run-0001', 'verdict': 'pass', 'failure_class': None, 'usage': {'input_tokens': 10},
           'cost': {'basis': ['unavailable']}, 'wall_clock_ms': 63392, 'captured_at': '2026-10-08T01:00:00Z',
           'routing_attestation': {'attempt_id': 'router-run-0001', 'route_id': 'route-a', 'role': 'impl'}}
SAMPLES = {  # sanitized: v2.8.2 field names, synthetic values
    'state-start': {'kind': 'start', 'unit_id': 'u-1', 'start': 1, 'route': 'route-a', 'packet_id': 'sha256:' + SHA,
                    'reserved_at': '2026-10-08T01:00:00Z', 'execution': {'pid': 7}, 'decision': {'dispatch': {'phase': 'author'}}},
    'state-finish': {'kind': 'finish', 'unit_id': 'u-1', 'start': 1, 'accounted': True, 'seconds': 63.4, 'attempt': ATTEMPT,
                     'controller_finished_at': '2026-10-08T01:01:03Z'},
    'state-reconcile': {'kind': 'reconcile', 'unit_id': 'u-1', 'start': 2, 'accounted': True, 'seconds': 95.4, 'attempt': None,
                        'usage': 'unknown', 'outcome': 'unknown', 'time_basis': 'conservative upper bound', 'evidence': 'stopped'},
    'state-outcome-closed': {'kind': 'outcome_closed', 'unit_id': 'u-1', 'completed_starts': 5, 'cumulative_seconds': 391.575,
                             'decision': {'assessments': []}, 'observed_at': '2026-10-08T01:10:00Z'},
    'route-record': {'schema': 'route-record/1', 'mission': 'm-1', 'transaction': 'u-1', 'role': 'review', 'route': 'route-b',
                     'class_key': 'review/judge/none/s/python', 'packet_id': 'sha256:' + SHA, 'crew_path': 'crew-2.json',
                     'crew_sha256': SHA, 'publication_sha256': SHA, 'contract_sha256': SHA, 'decision_sha256': SHA,
                     'classification_sha256': SHA, 'controller': 'supervise', 'controller_path': 'controller.py'},
    'crew': {'schema': 'supervised-crew/1', 'crew_sha256': SHA, 'selected': {'author': 'route-a', 'reviewer': 'route-b'},
             'excluded': [{'route': 'route-c', 'reason': 'unproven'}], 'rationale': 'least expected cost', 'cost_status': 'unknown',
             'created_at': '2026-10-08T01:00:00Z'},
    'attempt-outcome': ATTEMPT,
    'closure': {'kind': 'outcome_closed', 'unit_id': 'u-1', 'completed_starts': 3, 'cumulative_seconds': 187.8,
                'decision': {}, 'observed_at': '2026-10-08T02:58:00Z'},
    'publication-manifest': {'release': 'supervise-0.1.0', 'version': '0.1.0', 'state': 'candidate',
                             'files': {'supervise/backend.py': SHA}, 'predecessor': None},
}
TYPE_VIOLATIONS = [  # (schema, field, wrong value)
    ('state-finish', 'accounted', 'yes'), ('state-finish', 'seconds', '63'), ('state-start', 'start', 1.5),
    ('state-outcome-closed', 'completed_starts', '5'), ('route-record', 'role', 'author'),
    ('crew', 'excluded', 'none'), ('attempt-outcome', 'routing_attestation', 'x'), ('publication-manifest', 'files', []),
]


def schemas(test):
    try:
        return importlib.import_module('supervise.schemas')
    except ImportError as error:
        test.fail('Record schemas are not implemented: ' + str(error))


class SchemaShapeTests(unittest.TestCase):
    def test_every_record_schema_is_published_with_derived_required_fields(self):
        m = schemas(self)
        self.assertEqual(tuple(sorted(m.NAMES)), tuple(sorted(REQUIRED)))
        for name, required in REQUIRED.items():
            with self.subTest(name=name):
                schema = m.load(name)
                self.assertEqual(schema.get('type'), 'object')
                self.assertEqual(set(schema.get('required', ())), required)
                self.assertTrue(required <= set(schema.get('properties', {})))
                self.assertEqual(json.loads(json.dumps(schema)), schema)      # JSON-serializable
                schema['required'] = []                                         # load() hands out copies
                self.assertEqual(set(m.load(name)['required']), required)

    def test_state_records_pin_their_kind(self):
        m = schemas(self)
        for name, kind in [('state-start', 'start'), ('state-finish', 'finish'), ('state-reconcile', 'reconcile'),
                           ('state-outcome-closed', 'outcome_closed'), ('closure', 'outcome_closed')]:
            with self.subTest(name=name):
                bad = dict(SAMPLES[name], kind='other')
                self.assertTrue(any('kind' in e for e in m.validate(name, bad)))


class ValidatorTests(unittest.TestCase):
    def test_sanitized_samples_validate(self):
        m = schemas(self)
        for name, sample in SAMPLES.items():
            with self.subTest(name=name):
                self.assertEqual(m.validate(name, copy.deepcopy(sample)), [])

    def test_each_missing_required_field_is_named(self):
        m = schemas(self)
        for name, required in REQUIRED.items():
            for field in sorted(required):
                with self.subTest(name=name, field=field):
                    sample = copy.deepcopy(SAMPLES[name])
                    del sample[field]
                    errors = m.validate(name, sample)
                    self.assertTrue(errors)
                    self.assertTrue(any(field in e for e in errors), errors)

    def test_type_violations_are_reported(self):
        m = schemas(self)
        for name, field, value in TYPE_VIOLATIONS:
            with self.subTest(name=name, field=field):
                sample = copy.deepcopy(SAMPLES[name])
                sample[field] = value
                errors = m.validate(name, sample)
                self.assertTrue(any(field in e for e in errors), errors)

    def test_null_attempt_is_allowed_only_for_reconcile(self):
        m = schemas(self)
        self.assertEqual(m.validate('state-reconcile', dict(SAMPLES['state-reconcile'], attempt=None)), [])
        self.assertTrue(m.validate('state-finish', dict(SAMPLES['state-finish'], attempt=None)))

    def test_non_object_document_is_rejected(self):
        m = schemas(self)
        self.assertTrue(m.validate('closure', []))

    def test_unsupported_schema_keywords_fail_closed(self):
        m = schemas(self)
        with self.assertRaises(ValueError):
            m.check_schema({'type': 'object', 'patternProperties': {}})
        with self.assertRaises(ValueError):
            m.check_schema({'type': 'nonsense'})
        for name in REQUIRED:
            m.check_schema(m.load(name))

    def test_unknown_schema_name_is_refused(self):
        m = schemas(self)
        with self.assertRaises(KeyError):
            m.load('not-a-record')


class DerivationLedgerTests(unittest.TestCase):
    def test_every_schema_names_its_v282_source_and_exact_hash(self):
        m = schemas(self)
        ledger = m.DERIVATION
        self.assertEqual(json.loads(json.dumps(ledger)), ledger)
        self.assertEqual(set(ledger), set(REQUIRED))
        for name, entry in ledger.items():
            with self.subTest(name=name):
                self.assertTrue(entry['sources'])
                for source in entry['sources']:
                    file = source['file']
                    self.assertIn(file, SOURCES)
                    self.assertEqual(source['sha256'], SOURCES[file])
                    self.assertTrue(source['symbol'].strip())

    def test_package_uses_only_the_standard_library(self):
        m = schemas(self)
        names = set()
        for node in ast.walk(ast.parse(Path(m.__file__).read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names.discard('__future__')
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))


if __name__ == '__main__':
    unittest.main()
