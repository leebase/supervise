# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for the approved S1-1 revised Python public contract."""
import ast
import dataclasses
import importlib
import inspect
from pathlib import Path
import unittest


def backend(test):
    try:
        return importlib.import_module('supervise.backend')
    except ImportError as error:
        test.fail('Public backend contract is not implemented: ' + str(error))


class BackendContractTests(unittest.TestCase):
    def test_exact_record_fields(self):
        m = backend(self)
        expected = {
            'Route': 'route_id harness vendor model family roles review_isolation price',
            'BackendIdentity': 'name version config_sha256 source_sha256 routes',
            'TaskClass': 'role oracle size languages key',
            'CrewRequest': 'task prior_authors failed_authors failed_reviewers pinned_author require_review_fallback',
            'CrewPlan': 'author reviewers rationale excluded cost_status inputs',
            'LaunchRequest': 'route role packet_path workdir owned_paths timeout_seconds worker_commands review_view',
            'LaunchSpec': 'argv env requires_review_sandbox stdout_format mounts',   # S4-0 recorded scope revision
            'ProcessCapture': 'exit_code timed_out seconds stdout_path stderr_path stdout_sha256 stderr_sha256',
            'AttemptOutcome': 'final_text served_model usage cost_usd cost_basis failure_class notes',
        }
        for name, fields in expected.items():
            with self.subTest(name=name):
                record = getattr(m, name, None)
                self.assertIsNotNone(record, name)
                self.assertTrue(dataclasses.is_dataclass(record), name)
                self.assertEqual([f.name for f in dataclasses.fields(record)], fields.split())
                self.assertTrue(record.__dataclass_params__.frozen)

    def test_unknown_usage_cost_and_model_remain_explicit(self):
        m = backend(self)
        outcome = m.AttemptOutcome(final_text='', served_model='unknown', usage='unknown',
                                   cost_usd='unknown', cost_basis='unknown',
                                   failure_class='platform_timeout', notes=('capture absent',))
        self.assertEqual(outcome.cost_usd, 'unknown')
        self.assertEqual(outcome.usage, 'unknown')
        self.assertEqual(outcome.served_model, 'unknown')
        self.assertEqual(outcome.failure_class, 'platform_timeout')

    def test_exact_six_describe_interpret_methods(self):
        m = backend(self)
        protocol = m.RoutingBackend
        self.assertTrue(getattr(protocol, '_is_protocol', False))
        expected = {
            'identity': ('self',),
            'plan_crew': ('self', 'request'),
            'check_grants': ('self', 'route', 'commands'),
            'preflight_project': ('self', 'route', 'project'),
            'prepare_launch': ('self', 'request'),
            'interpret': ('self', 'spec', 'capture'),
        }
        public = {n for n, v in vars(protocol).items() if not n.startswith('_') and callable(v)}
        self.assertEqual(public, set(expected))
        for name, parameters in expected.items():
            self.assertEqual(tuple(inspect.signature(getattr(protocol, name)).parameters), parameters)

    def test_named_refusal_exceptions(self):
        m = backend(self)
        for name in ('UnsupportedGrant', 'ProjectConfigRefused', 'CrewUnavailable'):
            exception = getattr(m, name, None)
            self.assertIsNotNone(exception, name)
            self.assertTrue(issubclass(exception, ValueError), name)


class FrozenValuesTests(unittest.TestCase):
    def test_backend_cannot_modify_capture_facts_through_record_assignment(self):
        m = backend(self)
        capture = m.ProcessCapture(exit_code=1, timed_out=True, seconds=4.2,
                                   stdout_path=Path('stdout'), stderr_path=Path('stderr'),
                                   stdout_sha256='a'*64, stderr_sha256='b'*64)
        for name, value in [('exit_code', 0), ('timed_out', False), ('seconds', 0.0),
                            ('stdout_sha256', 'c'*64)]:
            with self.subTest(name=name):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(capture, name, value)

    def test_route_and_literal_launch_values_are_preserved(self):
        m = backend(self)
        route = m.Route(route_id='author', harness='codex', vendor='openai', model='example',
                        family='openai', roles=frozenset({'impl'}), review_isolation=False,
                        price='unknown')
        request = m.LaunchRequest(route=route, role='impl', packet_path=Path('packet.md'),
                                  workdir=Path('work'), owned_paths=(Path('module.py'),),
                                  timeout_seconds=60, worker_commands=(('python3', '-m', 'unittest'),),
                                  review_view=None)
        spec = m.LaunchSpec(argv=('/usr/bin/tool', 'literal $() argument'),
                            env={'ONLY': 'allowed'}, requires_review_sandbox=False,
                            stdout_format='fixture')
        self.assertEqual(request.worker_commands, (('python3', '-m', 'unittest'),))
        self.assertEqual(spec.argv[1], 'literal $() argument')
        self.assertEqual(dict(spec.env), {'ONLY': 'allowed'})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            route.family = 'anthropic'


class DescribeOnlyTests(unittest.TestCase):
    def test_protocol_contains_no_execution_or_state_writer_implementation(self):
        m = backend(self)
        tree = ast.parse(Path(m.__file__).read_text())
        protocol = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'RoutingBackend')
        for node in protocol.body:
            if isinstance(node, ast.FunctionDef):
                for statement in node.body:
                    self.assertTrue(isinstance(statement, ast.Expr) and
                                    isinstance(statement.value, ast.Constant) and
                                    (statement.value.value is Ellipsis or isinstance(statement.value.value, str)),
                                    node.name + ' executes code in a describe-only protocol')

    def test_stdlib_contract_without_private_imports(self):
        m = backend(self)
        tree = ast.parse(Path(m.__file__).read_text())
        approved = {'__future__', 'dataclasses', 'typing', 'pathlib', 'types', 'collections'}
        for node in ast.walk(tree):
            names = [x.name for x in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                self.assertIn((name or '').split('.')[0], approved)

    def test_consumption_mapping_and_kernel_authority_documented(self):
        m = backend(self)
        text = (m.__doc__ or '').lower()
        self.assertTrue(text, 'module method derivation/authority documentation is required')
        for name in ('identity', 'plan_crew', 'check_grants', 'preflight_project',
                     'prepare_launch', 'interpret', 'journal', 'accounting', 'independence'):
            self.assertIn(name, text)


if __name__ == '__main__':
    unittest.main()
