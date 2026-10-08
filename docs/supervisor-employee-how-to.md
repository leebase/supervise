# Use a durable Supervisor Pilot assignment

Supervisor Pilot is an existing employee with a continuing identity. Choose its durable assignment path when the work, Board ownership, evidence and recovery must survive host processes or sessions. The direct [supervise command](skill-how-to.md) serves a bounded project plan; the employee's completed supervised gaps reference the same invocation evidence (`closure.json`, `accepted-review.json`, `STATE.jsonl`) that the command produces, currently from the installed v2.8.2 release. Neither route creates its own business authority; the employee assignment needs protected owner provisioning before its installed runtime can act.

## Before a first assignment

The accountable owner supplies the task and acceptance criteria. The Factory owns the existing employee identity and grants; the Board records and claims organizational work; the Host binds that claim to a protected assignment configuration and admission receipt; the installed Runtime verifies those inputs before running. This is the **Factory → Board → Host → Runtime** path. A name in an assignment, digest, or model response is not authentication. Local ownership and permissions plus Chief supervision form the trust boundary; hashes bind bytes and reveal changes.

The owner prepares the immutable configuration, authorization, current Board claim and receipt under protected owner paths. The installed intake checks exact assignment, authority, source identity and live claim. The employee cannot produce those files, add a grant by editing instance.json, or treat queue admission as an LLM run. The task request should state the objective, exact scope, ordinary tests, reviewer acceptance, finite work allowance, stop condition and the recipient of the result.

### Illustrative first work request and frozen criteria

For a **new, separately authorized** assignment in an owner-selected project, an illustrative request is: “Add `--limit N` to the existing `tools/list_jobs.py` command. With a non-negative integer, print only the first N jobs in the existing order; with an invalid value, exit 2 with a useful error; without the option, preserve current output.” Freeze the acceptance criteria before admission: change only `tools/list_jobs.py` and `tests/test_list_jobs.py`; verify `--limit 0` prints no job rows, `--limit 2` prints the first two in order, negative and non-integer values exit 2, and omitting the option preserves prior output; pass the agreed ordinary `python3 -m unittest discover -s tests -p test_list_jobs.py -v` command; have an independent reviewer inspect the full request and exact tested source; stop and report an API or repository conflict instead of changing the design silently. This is an example request, not a claim that this project or profile is provisioned. It does not replay the accepted CSV assignment.

The owner must supply the new assignment ID, current Board task and lease, protected receipt/config/grants, authorized project and frozen test command. Copy finite `max_calls`, `max_turns`, `max_worker_starts` and `max_elapsed_seconds` values from that protected profile; choose `FINITE_STEPS` as a positive count within its remaining allowance and `HOST_SELECTED_MODEL` from its permitted host selection. These are labelled placeholders for **owner-granted values**, not CLI switches or example permission. The accepted CSV profile's reviewer limits below do not grant these values to a new assignment.

## Installed CLI and first walkthrough

The installed assignment_intake.py help supports **check, init, status, run, event, recover, acceptance**. Its required flags are --owner-root, --assignments-root, --runtime-root and --receipt-name; --model and --steps are available for execution. The installed assignment_service.py requires those four binding flags plus --model and --steps, and supports --acceptance. There is no universal supervisor assign command.

The following is a command template, **not** a permission grant. Every value must come from the authorized assignment profile and current owner receipt. The owner chooses a finite positive step count and host-selected model. Do not point these variables at an old accepted assignment to create new work.

~~~bash
OWNER_ROOT="<protected owner root from authorized profile>"
ASSIGNMENTS_ROOT="<authorized assignments root>"
RUNTIME_ROOT="<installed assignment runtime selected by owner>"
RECEIPT_NAME="<protected receipt filename from owner>"
HOST_SELECTED_MODEL="<model selected by authorized host>"
FINITE_STEPS="<positive finite steps in assignment grant>"

python3 -B "$RUNTIME_ROOT/assignment_intake.py" check \
  --owner-root "$OWNER_ROOT" --assignments-root "$ASSIGNMENTS_ROOT" \
  --runtime-root "$RUNTIME_ROOT" --receipt-name "$RECEIPT_NAME"
python3 -B "$RUNTIME_ROOT/assignment_intake.py" init \
  --owner-root "$OWNER_ROOT" --assignments-root "$ASSIGNMENTS_ROOT" \
  --runtime-root "$RUNTIME_ROOT" --receipt-name "$RECEIPT_NAME"
python3 -B "$RUNTIME_ROOT/assignment_service.py" \
  --owner-root "$OWNER_ROOT" --assignments-root "$ASSIGNMENTS_ROOT" \
  --runtime-root "$RUNTIME_ROOT" --receipt-name "$RECEIPT_NAME" \
  --model "$HOST_SELECTED_MODEL" --steps "$FINITE_STEPS"
python3 -B "$RUNTIME_ROOT/assignment_intake.py" status \
  --owner-root "$OWNER_ROOT" --assignments-root "$ASSIGNMENTS_ROOT" \
  --runtime-root "$RUNTIME_ROOT" --receipt-name "$RECEIPT_NAME"
~~~

The sequence is: request the bounded work; obtain the protected owner receipt/config/grants and live Board claim; run check and init; run the service under that admitted profile; inspect status, source and ordinary tests; require full independent acceptance and closure; then inspect the durable result.json and corresponding Board result. The service writes/publishes a result only for a completed bound outcome. Retrieve actual attempt IDs, test observations, independent review and original transaction evidence from the assigned runtime and its protected evidence stores. Publication or deployment of the candidate is a separate authorized action.

For readback, retain the JSON printed by `assignment_service.py`, including its Board publication response, and run the `status` command above against the **same** receipt. The following read-only lookup uses that receipt's `instance_home` to find the actual durable result; an absent file is an incomplete handoff, not a PASS. Compare its `status`, gaps and counters with intake status, the tested source and independent review/closure evidence, then check the corresponding Board result through the owner's existing Board readback. In a completed supervised gap, inspect the retained `accepted_supervision` proof's invocation, closure hash, independent review hash and source byte hash against the original invocation's `closure.json`, `accepted-review.json`, `current-observation.json` and `STATE.jsonl`. The service publishes to the Board only after `COMPLETE`; do not infer Board publication from a green test alone.

~~~bash
python3 - "$OWNER_ROOT" "$RECEIPT_NAME" <<'PY'
import json
import sys
from pathlib import Path

receipt = json.loads((Path(sys.argv[1]) / sys.argv[2]).read_text())
result_path = Path(receipt["instance_home"]) / "result.json"
print("assignment:", receipt["assignment_id"])
print("result:", result_path)
if result_path.is_file():
    result = json.loads(result_path.read_text())
    print(json.dumps({key: result.get(key) for key in
                      ("employee_id", "status", "gaps", "counters")}, indent=2))
else:
    print("result absent: acceptance or publication is incomplete")
PY
~~~

## Process loss and ordinary recovery

A lost process does not erase the assignment or grant another one. Use status against the same protected binding, reconcile unknown live work, then use the installed recover action or host-managed service continuation under the original assignment as directed by its authorized profile. The original assignment ID, Board task, native unit, predecessors, tests, calls, turns and counters persist. Do not edit SQLite, replace the config, reset counters or create a duplicate assignment to get more budget. A genuine cached terminal verdict may be replayed idempotently; an unknown operation must be reconciled before it can become a result.

Routine worker failure is the supervisor's bounded recovery work. A completed implementation whose independent reviewer times out is different: source and tests may be preserved while the result remains unaccepted. For the accepted CSV profile, separately authorized reviewer recovery allowed **two reviewer starts total, 900 active seconds total, and 420 seconds per attempt**. These are profile-specific finite limits, not automatic allowance for every future task.

That reviewer lane requires an owner-prepared protected acceptance-recovery.json tied to the original assignment, authority and current Board claim, the exact completed source bytes, the exhausted original transaction and failed review predecessor. It adds no implementation turns or counters. Only then may the installed intake acceptance action or service --acceptance path run against the original binding. A genuine independent verdict on the exact candidate can close it; a timeout leaves it unaccepted. If review finds a real blocking source defect, obtain explicit bounded remediation authority and independent re-review rather than using the acceptance-only lane to edit implementation.

After the owner has supplied and verified `"$OWNER_ROOT/acceptance-recovery.json"` as protected acceptance authority, the installed service template for **acceptance-only** recovery is:

~~~bash
python3 -B "$RUNTIME_ROOT/assignment_service.py" \
  --owner-root "$OWNER_ROOT" --assignments-root "$ASSIGNMENTS_ROOT" \
  --runtime-root "$RUNTIME_ROOT" --receipt-name "$RECEIPT_NAME" \
  --model "$HOST_SELECTED_MODEL" --steps "$FINITE_STEPS" --acceptance
~~~

Use the original receipt and assignment; the required model and finite-steps arguments satisfy the service interface and do not create a new grant. Recheck `status`, `result.json`, original native evidence and the Board response afterward. If cleanup or closure is unknown, reconcile it on the original assignment. Only a retained genuine independent verdict may be replayed idempotently; never fabricate PASS or reset work counters.

## A blocked or interrupted result

Report BLOCKED with the exact missing authority, source conflict, unavailable independent route or exhausted allowance and the original transaction identity. Do not fabricate PASS from an unknown state, a green test suite or a plausible reviewer summary. Preserve result.json, Board evidence and all prior failed attempts. An accepted assignment is a candidate handoff. For **this documentation product**, Document Publisher owns the authorized documentation build, deployment and live checks; other software tasks follow their own separately authorized release owner.

This page describes an installed, accepted substantial assignment path first demonstrated on October 6, 2026 and re-verified against the installed intake and service CLIs (`--help`) on October 7, 2026. It does not claim that every employee profile or project language is provisioned. Return to the [supervision overview](overview.md) for the shared model, or the [owner guide](owner-operator-guide.md) for the skill-versus-employee comparison.

---

*Production copy of this page: [https://nginx.leebasehome.com/docs/pages/supervisor-pilot-how-to.html](https://nginx.leebasehome.com/docs/pages/supervisor-pilot-how-to.html). Part of the [`/supervise` public documentation](../README.md), Apache License 2.0.*
