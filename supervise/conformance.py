# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Public test registry and pending skeleton for the v2.8.2 regression port."""

from dataclasses import dataclass
from pathlib import Path
import sys
import unittest


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    area: str
    v282_test: str
    status: str
    public_tests: tuple[str, ...]
    planned_unit: str | None


_V282_SCENARIOS = {
    "review-recovery/interrupted-review": "test_crew_integration.py::test_actual_review_reconcile_settle_advances_and_preserves_author_for_fallback[False]",
    "review-recovery/misleading-raw-label": "test_crew_integration.py::test_actual_review_reconcile_settle_advances_and_preserves_author_for_fallback[True]",
    "review-recovery/timeout-receipt": "test_crew_integration.py::test_actual_review_reconcile_settle_advances_and_preserves_author_for_fallback[timeout_receipt]",
    "review-recovery/completed-reject": "test_crew_integration.py::test_actual_review_reconcile_settle_advances_and_preserves_author_for_fallback[completed_reject]",
    "review-recovery/completed-reject-reconcile": "test_crew_integration.py::test_actual_review_reconcile_settle_advances_and_preserves_author_for_fallback[completed_reject_reconcile]",
    "crew/no-qualified-review-crew-refuses": "test_crew_integration.py::test_no_qualified_review_crew_refuses_before_native_start",
    "crew/review-recovery-retains-author": "test_crew_integration.py::test_review_recovery_uses_retained_author_class_and_never_dispatches_author",
    "classify/narrow-change-frozen": "test_crew_integration.py::test_structural_task_classification_freezes_narrow_config_change",
    "classify/outside-ownership-rejected": "test_crew_integration.py::test_structural_change_outside_admitted_ownership_rejected",
    "route/prestart-refusal-archived": "test_crew_integration.py::test_retained_prestart_refusal_archived_without_new_transaction",
    "manager-fault/single-error-not-fatal": "test_command_manager_fault.py::test_single_manager_error_is_not_fatal",
    "manager-fault/consecutive-errors-stop": "test_command_manager_fault.py::test_consecutive_manager_errors_stop_the_wait",
    "manager-fault/stopped-nudges-stall": "test_command_manager_fault.py::test_stopped_nudges_become_a_stall_after_grace",
    "manager-fault/progress-never-stall": "test_command_manager_fault.py::test_progressing_manager_is_never_a_stall",
    "manager-fault/late-ledger-detected": "test_command_manager_fault.py::test_ledger_written_after_agent_end_is_still_detected",
    "manager-fault/late-eighth-turn": "test_command_manager_fault.py::test_eighth_turn_boundary_written_late_is_detected",
    "manager-fault/new-turn-cancels-late-ledger": "test_command_manager_fault.py::test_late_ledger_is_ignored_once_a_new_turn_started",
    "provenance/host-pending-refused": "test_pending_provenance.py::test_host_written_pending_is_refused_by_provenance_rule",
    "provenance/pending-protected-before-dispatch": "test_pending_provenance.py::test_initialize_protects_pending_before_any_dispatch",
    "budget/test-maximum-not-allowance": "test_budget.py::test_test_maximum_is_not_consumed_allowance",
    "budget/admitted-worker-bound-honoured": "test_budget.py::test_admitted_worker_bound_is_honored_without_hidden_ceiling",
    "budget/expired-refuses-renewal": "test_budget.py::test_expired_original_refuses_without_renewal",
    "budget/continuation-keeps-original": "test_budget.py::test_explicit_continuation_keeps_all_original_bytes",
    "budget/continuation-refuses-outer-and-worker": "test_budget.py::test_continuation_refuses_outer_limits_and_worker_role",
    "budget/test-timeout-bounded-cleanup": "test_budget.py::test_actual_test_timeout_and_bounded_cleanup",
    "budget/tampered-binding-blocks": "test_budget.py::test_tampered_original_binding_blocks_continuation",
    "budget/rpc-wakes-authorized-expired": "test_budget.py::test_normal_rpc_wakes_authorized_expired_state_without_reset",
    "attestation/receipt-shares-attempt-id": "test_route_receipt_attempt_id.py::test_ordinary_run_receipt_carries_the_record_attempt_id",
}

_IMPLEMENTED = {
    "review-recovery/interrupted-review": (
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_interrupted_review",
    ),
    "review-recovery/misleading-raw-label": (
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_misleading_raw_label",
    ),
    "review-recovery/timeout-receipt": (
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_timeout_receipt",
    ),
    "review-recovery/completed-reject": (
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_completed_reject",
    ),
    "review-recovery/completed-reject-reconcile": (
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_completed_reject_reconcile",
    ),
    "crew/no-qualified-review-crew-refuses": (
        "test_controller_transaction.AttestationAndCrewTests.test_no_qualified_review_crew_refuses_before_start",
    ),
    "crew/review-recovery-retains-author": (
        "test_controller_transaction.ReviewRecoveryTests.test_review_recovery_retains_author_and_never_dispatches_author",
    ),
    "classify/narrow-change-frozen": (
        "test_controller_classify.ClassifyTests.test_returns_backend_task_class_with_canonical_key",
        "test_controller_classify.ClassifyTests.test_classification_is_deterministic_and_order_insensitive",
    ),
    "classify/outside-ownership-rejected": (
        "test_controller_classify.ClassifyTests.test_ownership_outside_the_admitted_scope_is_rejected",
    ),
    "route/prestart-refusal-archived": (
        "test_controller_transaction.AttestationAndCrewTests.test_prestart_refusal_route_is_archived",
    ),
    "provenance/host-pending-refused": (
        "test_controller_admission.ProvenanceTests.test_host_written_pending_with_write_bits_is_refused",
    ),
    "provenance/pending-protected-before-dispatch": (
        "test_controller_admission.ProvenanceTests.test_pending_is_protected_before_any_dispatch",
    ),
    "budget/test-maximum-not-allowance": (
        "test_controller_budget.AllocationTests.test_test_maximum_is_not_consumed_allowance",
    ),
    "budget/admitted-worker-bound-honoured": (
        "test_controller_budget.AllocationTests.test_admitted_worker_bound_is_honoured_without_hidden_ceiling",
    ),
    "budget/expired-refuses-renewal": (
        "test_controller_budget.AllocationTests.test_expired_original_refuses_without_renewal",
    ),
    "budget/continuation-keeps-original": (
        "test_controller_budget.ContinuationTests.test_explicit_continuation_keeps_all_original_bytes",
    ),
    "budget/continuation-refuses-outer-and-worker": (
        "test_controller_budget.ContinuationTests.test_continuation_refuses_outer_limits",
        "test_controller_budget.ContinuationTests.test_continuation_refuses_worker_role",
    ),
    "budget/test-timeout-bounded-cleanup": (
        "test_controller_budget.AllocationTests.test_fit_test_run_bounds_test_time_and_cleanup",
    ),
    "budget/tampered-binding-blocks": (
        "test_controller_budget.TamperTests.test_tampered_original_binding_blocks_continuation",
    ),
    "attestation/receipt-shares-attempt-id": (
        "test_controller_transaction.AttestationAndCrewTests.test_attestation_receipt_shares_attempt_id",
    ),
}

_PENDING = {
    "manager-fault/single-error-not-fatal": "S5-1",
    "manager-fault/consecutive-errors-stop": "S5-1",
    "manager-fault/stopped-nudges-stall": "S5-1",
    "manager-fault/progress-never-stall": "S5-1",
    "manager-fault/late-ledger-detected": "S5-1",
    "manager-fault/late-eighth-turn": "S5-1",
    "manager-fault/new-turn-cancels-late-ledger": "S5-1",
    "budget/rpc-wakes-authorized-expired": "S5-1",
}

SCENARIOS = tuple(
    Scenario(
        scenario_id=scenario_id,
        area=scenario_id.split("/", 1)[0],
        v282_test=v282_test,
        status="implemented" if scenario_id in _IMPLEMENTED else "not_implemented",
        public_tests=_IMPLEMENTED.get(scenario_id, ()),
        planned_unit=None if scenario_id in _IMPLEMENTED else _PENDING[scenario_id],
    )
    for scenario_id, v282_test in _V282_SCENARIOS.items()
)


def skeleton_suite():
    """Return one explicitly skipped placeholder for each pending scenario."""
    suite = unittest.TestSuite()
    for index, scenario in enumerate(SCENARIOS):
        if scenario.status != "not_implemented":
            continue

        def skipped_test(self, _scenario=scenario):
            self.skipTest(f"{_scenario.scenario_id} not yet implemented")

        case = type(
            f"ScenarioSkeleton{index}",
            (unittest.TestCase,),
            {"run_scenario": skipped_test},
        )
        suite.addTest(case("run_scenario"))
    return suite


def conformance_suite():
    """Load implemented public tests and include pending scenario placeholders."""
    suite = unittest.TestSuite()
    tests_path = str(Path(__file__).resolve().parent.parent / "tests")
    added_path = tests_path not in sys.path
    if added_path:
        sys.path.insert(0, tests_path)
    try:
        for scenario in SCENARIOS:
            if scenario.status == "implemented":
                for test_id in scenario.public_tests:
                    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(test_id))
    finally:
        if added_path:
            sys.path.remove(tests_path)
    suite.addTests(skeleton_suite())
    return suite
