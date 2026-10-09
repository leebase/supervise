# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S3-C: the conformance registry records which v2.8.2 scenarios the public build
implements (by real public test id) and which remain pending (by the unit that will implement them).

Recorded scope revision (Chief, 2026-10-08): the S1-3 skeleton required every scenario to be `not_implemented`; S3 has
now ported 20 of 28. This oracle supersedes those two skeleton assertions (tests/test_stub_conformance.py is amended in
the same revision to accept `implemented` and to count skips only for pending scenarios).
"""
import dataclasses
import importlib
import unittest

IMPLEMENTED = {
    "review-recovery/interrupted-review": [
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_interrupted_review"
    ],
    "review-recovery/misleading-raw-label": [
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_misleading_raw_label"
    ],
    "review-recovery/timeout-receipt": [
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_timeout_receipt"
    ],
    "review-recovery/completed-reject": [
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_completed_reject"
    ],
    "review-recovery/completed-reject-reconcile": [
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_completed_reject_reconcile"
    ],
    "crew/no-qualified-review-crew-refuses": [
        "test_controller_transaction.AttestationAndCrewTests.test_no_qualified_review_crew_refuses_before_start"
    ],
    "crew/review-recovery-retains-author": [
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_retains_author_and_never_dispatches_author"
    ],
    "classify/narrow-change-frozen": [
        "test_controller_classify.ClassifyTests.test_returns_backend_task_class_with_canonical_key",
        "test_controller_classify.ClassifyTests.test_classification_is_deterministic_and_order_insensitive"
    ],
    "classify/outside-ownership-rejected": [
        "test_controller_classify.ClassifyTests.test_ownership_outside_the_admitted_scope_is_rejected"
    ],
    "route/prestart-refusal-archived": [
        "test_controller_transaction.AttestationAndCrewTests.test_prestart_refusal_route_is_archived"
    ],
    "provenance/host-pending-refused": [
        "test_controller_admission.ProvenanceTests.test_host_written_pending_with_write_bits_is_refused"
    ],
    "provenance/pending-protected-before-dispatch": [
        "test_controller_admission.ProvenanceTests.test_pending_is_protected_before_any_dispatch"
    ],
    "budget/test-maximum-not-allowance": [
        "test_controller_budget.AllocationTests.test_test_maximum_is_not_consumed_allowance"
    ],
    "budget/admitted-worker-bound-honoured": [
        "test_controller_budget.AllocationTests.test_admitted_worker_bound_is_honoured_without_hidden_ceiling"
    ],
    "budget/expired-refuses-renewal": [
        "test_controller_budget.AllocationTests.test_expired_original_refuses_without_renewal"
    ],
    "budget/continuation-keeps-original": [
        "test_controller_budget.ContinuationTests.test_explicit_continuation_keeps_all_original_bytes"
    ],
    "budget/continuation-refuses-outer-and-worker": [
        "test_controller_budget.ContinuationTests.test_continuation_refuses_outer_limits",
        "test_controller_budget.ContinuationTests.test_continuation_refuses_worker_role"
    ],
    "budget/test-timeout-bounded-cleanup": [
        "test_controller_budget.AllocationTests.test_fit_test_run_bounds_test_time_and_cleanup"
    ],
    "budget/tampered-binding-blocks": [
        "test_controller_budget.TamperTests.test_tampered_original_binding_blocks_continuation"
    ],
    "attestation/receipt-shares-attempt-id": [
        "test_controller_transaction.AttestationAndCrewTests.test_attestation_receipt_shares_attempt_id"
    ]
}
PENDING = {
    "manager-fault/single-error-not-fatal": "S5-1",
    "manager-fault/consecutive-errors-stop": "S5-1",
    "manager-fault/stopped-nudges-stall": "S5-1",
    "manager-fault/progress-never-stall": "S5-1",
    "manager-fault/late-ledger-detected": "S5-1",
    "manager-fault/late-eighth-turn": "S5-1",
    "manager-fault/new-turn-cancels-late-ledger": "S5-1",
    "budget/rpc-wakes-authorized-expired": "S5-1"
}


class ConformanceRegistryTests(unittest.TestCase):
    def setUp(self):
        try:
            self.c = importlib.import_module('supervise.conformance')
        except ImportError as error:
            self.fail('conformance registry missing: ' + str(error))

    def registry(self):
        return {s.scenario_id: s for s in self.c.SCENARIOS}

    def test_scenario_records_carry_public_tests_and_planned_unit(self):
        names = [f.name for f in dataclasses.fields(self.c.Scenario)]
        self.assertEqual(names, ['scenario_id', 'area', 'v282_test', 'status', 'public_tests', 'planned_unit'])

    def test_implemented_scenarios_name_their_exact_public_tests(self):
        registry = self.registry()
        for scenario_id, tests in IMPLEMENTED.items():
            with self.subTest(scenario=scenario_id):
                record = registry[scenario_id]
                self.assertEqual(record.status, 'implemented')
                self.assertEqual(tuple(record.public_tests), tuple(tests))
                self.assertIsNone(record.planned_unit)

    def test_pending_scenarios_name_their_planned_unit(self):
        registry = self.registry()
        for scenario_id, unit in PENDING.items():
            with self.subTest(scenario=scenario_id):
                record = registry[scenario_id]
                self.assertEqual((record.status, tuple(record.public_tests), record.planned_unit), ('not_implemented', (), unit))
        self.assertEqual(set(registry), set(IMPLEMENTED) | set(PENDING))

    def test_conformance_suite_runs_implemented_tests_and_skips_pending(self):
        suite = self.c.conformance_suite()
        result = unittest.TestResult()
        suite.run(result)
        expected_runs = sum(len(v) for v in IMPLEMENTED.values()) + len(PENDING)
        self.assertEqual(result.testsRun, expected_runs)
        self.assertEqual(len(result.skipped), len(PENDING))
        self.assertEqual((result.failures, result.errors), ([], []))


if __name__ == '__main__':
    unittest.main()
