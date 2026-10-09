# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S1-3: deterministic stub backend and conformance skeleton."""
import ast
import dataclasses
import importlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
OUTCOMES = ('success', 'reject', 'platform_timeout', 'no_verdict', 'crash')
CI = 'test_crew_integration.py::test_actual_review_reconcile_settle_advances_and_preserves_author_for_fallback'
# Every v2.8.2 regression scenario the public build must port, by its production test id.
SCENARIOS = {
    'review-recovery/interrupted-review': CI + '[False]',
    'review-recovery/misleading-raw-label': CI + '[True]',
    'review-recovery/timeout-receipt': CI + '[timeout_receipt]',
    'review-recovery/completed-reject': CI + '[completed_reject]',
    'review-recovery/completed-reject-reconcile': CI + '[completed_reject_reconcile]',
    'crew/no-qualified-review-crew-refuses': 'test_crew_integration.py::test_no_qualified_review_crew_refuses_before_native_start',
    'crew/review-recovery-retains-author': 'test_crew_integration.py::test_review_recovery_uses_retained_author_class_and_never_dispatches_author',
    'classify/narrow-change-frozen': 'test_crew_integration.py::test_structural_task_classification_freezes_narrow_config_change',
    'classify/outside-ownership-rejected': 'test_crew_integration.py::test_structural_change_outside_admitted_ownership_rejected',
    'route/prestart-refusal-archived': 'test_crew_integration.py::test_retained_prestart_refusal_archived_without_new_transaction',
    'manager-fault/single-error-not-fatal': 'test_command_manager_fault.py::test_single_manager_error_is_not_fatal',
    'manager-fault/consecutive-errors-stop': 'test_command_manager_fault.py::test_consecutive_manager_errors_stop_the_wait',
    'manager-fault/stopped-nudges-stall': 'test_command_manager_fault.py::test_stopped_nudges_become_a_stall_after_grace',
    'manager-fault/progress-never-stall': 'test_command_manager_fault.py::test_progressing_manager_is_never_a_stall',
    'manager-fault/late-ledger-detected': 'test_command_manager_fault.py::test_ledger_written_after_agent_end_is_still_detected',
    'manager-fault/late-eighth-turn': 'test_command_manager_fault.py::test_eighth_turn_boundary_written_late_is_detected',
    'manager-fault/new-turn-cancels-late-ledger': 'test_command_manager_fault.py::test_late_ledger_is_ignored_once_a_new_turn_started',
    'provenance/host-pending-refused': 'test_pending_provenance.py::test_host_written_pending_is_refused_by_provenance_rule',
    'provenance/pending-protected-before-dispatch': 'test_pending_provenance.py::test_initialize_protects_pending_before_any_dispatch',
    'budget/test-maximum-not-allowance': 'test_budget.py::test_test_maximum_is_not_consumed_allowance',
    'budget/admitted-worker-bound-honoured': 'test_budget.py::test_admitted_worker_bound_is_honored_without_hidden_ceiling',
    'budget/expired-refuses-renewal': 'test_budget.py::test_expired_original_refuses_without_renewal',
    'budget/continuation-keeps-original': 'test_budget.py::test_explicit_continuation_keeps_all_original_bytes',
    'budget/continuation-refuses-outer-and-worker': 'test_budget.py::test_continuation_refuses_outer_limits_and_worker_role',
    'budget/test-timeout-bounded-cleanup': 'test_budget.py::test_actual_test_timeout_and_bounded_cleanup',
    'budget/tampered-binding-blocks': 'test_budget.py::test_tampered_original_binding_blocks_continuation',
    'budget/rpc-wakes-authorized-expired': 'test_budget.py::test_normal_rpc_wakes_authorized_expired_state_without_reset',
    'attestation/receipt-shares-attempt-id': 'test_route_receipt_attempt_id.py::test_ordinary_run_receipt_carries_the_record_attempt_id',
}


def load(test, name):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        test.fail(name + ' is not implemented: ' + str(error))


def routes(b):
    def route(rid, family, roles, isolation=True):
        return b.Route(route_id=rid, harness='stub', vendor=family, model=rid, family=family,
                       roles=frozenset(roles), review_isolation=isolation, price='unknown')
    return (route('author-a', 'alpha', {'impl'}), route('author-b', 'beta', {'impl'}),
            route('review-g', 'gamma', {'review'}), route('review-d', 'delta', {'review'}),
            route('review-a', 'alpha', {'review'}), route('review-open', 'epsilon', {'review'}, isolation=False))


def task(b, role='impl'):
    return b.TaskClass(role=role, oracle='deterministic', size='s', languages=('python',), key=role + '/deterministic/s/python')


def request(b, **overrides):
    values = dict(task=task(b), prior_authors=(), failed_authors=(), failed_reviewers=(), pinned_author=None,
                  require_review_fallback=True)
    values.update(overrides)
    return b.CrewRequest(**values)


class StubBackendTests(unittest.TestCase):
    def setUp(self):
        self.b = load(self, 'supervise.backend')
        self.stub = load(self, 'supervise.backends.stub')
        self.routes = routes(self.b)

    def make(self, script=None):
        return self.stub.StubBackend(self.routes, script or {})

    def test_identity_is_deterministic_and_pins_source_and_config(self):
        first, second = self.make(), self.make()
        self.assertIsInstance(first.identity(), self.b.BackendIdentity)
        self.assertEqual(first.identity(), second.identity())
        self.assertEqual(first.identity().routes, self.routes)
        self.assertEqual(first.identity().name, 'stub')
        changed = self.make({'author-a': ('reject',)})
        self.assertNotEqual(first.identity().config_sha256, changed.identity().config_sha256)
        self.assertEqual(first.identity().source_sha256, changed.identity().source_sha256)
        self.assertEqual(len(first.identity().config_sha256), 64)

    def test_crew_is_independent_ordered_and_explains_exclusions(self):
        plan = self.make().plan_crew(request(self.b))
        self.assertIsInstance(plan, self.b.CrewPlan)
        self.assertEqual(plan.author.route_id, 'author-a')
        self.assertEqual([r.route_id for r in plan.reviewers], ['review-g', 'review-d'])
        self.assertIn('review-a', plan.excluded)        # same family as the author
        self.assertIn('review-open', plan.excluded)     # cannot run in the review view
        self.assertEqual(plan.cost_status, 'unavailable')
        self.assertTrue(plan.rationale)

    def test_failures_and_prior_authors_steer_the_crew(self):
        plan = self.make().plan_crew(request(self.b, failed_authors=('author-a',), prior_authors=('author-a',)))
        self.assertEqual(plan.author.route_id, 'author-b')
        self.assertNotIn('review-a', [r.route_id for r in plan.reviewers])
        plan = self.make().plan_crew(request(self.b, failed_reviewers=('review-g',), require_review_fallback=False))
        self.assertEqual([r.route_id for r in plan.reviewers], ['review-d'])

    def test_pinned_author_is_kept_for_review_recovery(self):
        plan = self.make().plan_crew(request(self.b, task=task(self.b, 'review'), pinned_author='author-b',
                                             prior_authors=('author-b',), require_review_fallback=False))
        self.assertEqual(plan.author.route_id, 'author-b')
        self.assertTrue(all(r.family != 'beta' for r in plan.reviewers))

    def test_unsatisfiable_crew_raises_named_refusal(self):
        with self.assertRaises(self.b.CrewUnavailable):
            self.make().plan_crew(request(self.b, failed_reviewers=('review-g',)))   # fallback required, one left
        with self.assertRaises(self.b.CrewUnavailable):
            self.make().plan_crew(request(self.b, failed_authors=('author-a', 'author-b')))

    def test_grants_and_project_preflight_refuse_with_named_errors(self):
        backend = self.make()
        backend.check_grants(self.routes[0], [['/bin/true']])
        with self.assertRaises(self.b.UnsupportedGrant):
            backend.check_grants(self.routes[0], [['true']])           # not an absolute program
        with self.assertRaises(self.b.UnsupportedGrant):
            backend.check_grants(self.routes[2], [['/bin/true']])      # review route has no implementation grants
        with tempfile.TemporaryDirectory() as directory:
            backend.preflight_project(self.routes[0], Path(directory))
            (Path(directory) / '.stub-refuse').write_text('x')
            with self.assertRaises(self.b.ProjectConfigRefused):
                backend.preflight_project(self.routes[0], Path(directory))

    def run_scripted(self, outcome, role):
        route = self.routes[0] if role == 'impl' else self.routes[2]
        backend = self.make({route.route_id: (outcome,)})
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            spec = backend.prepare_launch(self.b.LaunchRequest(
                route=route, role=role, packet_path=work / 'packet.md', workdir=work, owned_paths=(),
                timeout_seconds=30.0, worker_commands=(), review_view=work if role == 'review' else None))
            self.assertIsInstance(spec, self.b.LaunchSpec)
            self.assertEqual(spec.requires_review_sandbox, role == 'review')
            self.assertTrue(Path(spec.argv[0]).is_absolute())
            done = subprocess.run(list(spec.argv), env=dict(spec.env), capture_output=True, timeout=30)
            out, err = work / 'out', work / 'err'
            out.write_bytes(done.stdout); err.write_bytes(done.stderr)
            capture = self.b.ProcessCapture(exit_code=done.returncode, timed_out=False, seconds=0.1, stdout_path=out,
                                            stderr_path=err, stdout_sha256='0' * 64, stderr_sha256='0' * 64)
            return backend.interpret(spec, capture), done.stdout.decode()

    def test_every_scripted_outcome_is_deterministic_and_honest(self):
        for role in ('impl', 'review'):
            for outcome in OUTCOMES:
                with self.subTest(role=role, outcome=outcome):
                    result, stdout = self.run_scripted(outcome, role)
                    self.assertIsInstance(result, self.b.AttemptOutcome)
                    self.assertEqual((result.served_model, result.usage, result.cost_usd, result.cost_basis),
                                     ('unknown', 'unknown', 'unknown', 'unknown'))
                    expected_failure = {'platform_timeout': 'platform_timeout', 'crash': 'worker'}.get(outcome, 'none')
                    self.assertEqual(result.failure_class, expected_failure)
                    if role == 'review' and outcome == 'success':
                        self.assertTrue(result.final_text.endswith('REVIEW VERDICT: ACCEPT'))
                        self.assertIn('REVIEW VERDICT: ACCEPT', stdout)
                    if role == 'review' and outcome == 'reject':
                        self.assertTrue(result.final_text.endswith('REVIEW VERDICT: REJECT'))
                        self.assertIn('REVIEW VERDICT: REJECT', stdout)
                    if outcome == 'no_verdict':
                        self.assertNotIn('REVIEW VERDICT', result.final_text)

    def test_script_is_consumed_in_order_then_defaults_to_success(self):
        route = self.routes[0]
        backend = self.make({route.route_id: ('reject', 'crash')})
        seen = []
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for _ in range(3):
                spec = backend.prepare_launch(self.b.LaunchRequest(
                    route=route, role='impl', packet_path=work / 'p', workdir=work, owned_paths=(),
                    timeout_seconds=30.0, worker_commands=(), review_view=None))
                capture = self.b.ProcessCapture(exit_code=0, timed_out=False, seconds=0.1, stdout_path=work / 'o',
                                                stderr_path=work / 'e', stdout_sha256='0' * 64, stderr_sha256='0' * 64)
                (work / 'o').write_text(''); (work / 'e').write_text('')
                seen.append(backend.interpret(spec, capture).failure_class)
        self.assertEqual(seen, ['none', 'worker', 'none'])

    def test_kernel_timeout_overrides_any_script(self):
        route = self.routes[0]
        backend = self.make({route.route_id: ('success',)})
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            spec = backend.prepare_launch(self.b.LaunchRequest(
                route=route, role='impl', packet_path=work / 'p', workdir=work, owned_paths=(),
                timeout_seconds=1.0, worker_commands=(), review_view=None))
            (work / 'o').write_text(''); (work / 'e').write_text('')
            capture = self.b.ProcessCapture(exit_code=None, timed_out=True, seconds=1.0, stdout_path=work / 'o',
                                            stderr_path=work / 'e', stdout_sha256='0' * 64, stderr_sha256='0' * 64)
            self.assertEqual(backend.interpret(spec, capture).failure_class, 'platform_timeout')

    def test_stub_is_a_routing_backend_and_stdlib_only(self):
        backend = self.make()
        for name in ('identity', 'plan_crew', 'check_grants', 'preflight_project', 'prepare_launch', 'interpret'):
            self.assertTrue(callable(getattr(backend, name)), name)
        assert_stdlib(self, self.stub)


class ConformanceSkeletonTests(unittest.TestCase):
    def setUp(self):
        self.c = load(self, 'supervise.conformance')

    def test_registry_names_every_v282_scenario_exactly(self):
        registry = {s.scenario_id: s for s in self.c.SCENARIOS}
        self.assertEqual(len(registry), len(self.c.SCENARIOS), 'duplicate scenario ids')
        self.assertEqual(set(registry), set(SCENARIOS))
        for scenario_id, source in SCENARIOS.items():
            self.assertEqual(registry[scenario_id].v282_test, source)
        self.assertTrue(dataclasses.is_dataclass(self.c.Scenario) and self.c.Scenario.__dataclass_params__.frozen)

    def test_no_scenario_claims_to_pass_before_it_is_implemented(self):
        for scenario in self.c.SCENARIOS:
            with self.subTest(scenario=scenario.scenario_id):
                self.assertIn(scenario.status, {'not_implemented', 'implemented'})   # S3-C scope revision
                if scenario.status == 'not_implemented':
                    self.assertEqual(tuple(scenario.public_tests), ())
                self.assertIn(scenario.area, {'review-recovery', 'crew', 'classify', 'route', 'manager-fault',
                                              'provenance', 'budget', 'attestation'})
                self.assertEqual(scenario.scenario_id.split('/')[0], scenario.area)

    def test_skeleton_suite_skips_rather_than_passes(self):
        suite = self.c.skeleton_suite()                                        # pending scenarios only (S3-C)
        result = unittest.TestResult()
        suite.run(result)
        pending = [s for s in self.c.SCENARIOS if s.status == 'not_implemented']
        self.assertEqual(result.testsRun, len(pending))
        self.assertEqual(len(result.skipped), len(pending))
        self.assertEqual((result.failures, result.errors), ([], []))
        self.assertTrue(all('not yet implemented' in reason for _, reason in result.skipped))

    def test_conformance_module_is_stdlib_only(self):
        assert_stdlib(self, self.c)


def assert_stdlib(test, module):
    names = set()
    for node in ast.walk(ast.parse(Path(module.__file__).read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add(node.module.split('.')[0])
    names -= {'__future__', 'supervise'}
    test.assertTrue(names <= STDLIB, sorted(names - STDLIB))


if __name__ == '__main__':
    unittest.main()
