# Supervision: owner and operator guide

Assign the outcome and its limits once. The supervisor handles normal worker and reviewer failures within the admitted allowance, preserving completed work and accounting. You intervene when the next action needs a new decision, grant, scope change or resource authority. Repeatedly asking the owner to restart a routine failed worker defeats the purpose of supervision.

This guide is written so the system can be maintained without reconstructing its development history. Every command below was checked against the installed v2.8.2 release on October 7, 2026; commands marked *read-only* change nothing.

## 1. Architecture and installed release

`/supervise` is five cooperating parts:

| Part | What it is | Where it lives |
|---|---|---|
| **Manager** | An ordinary OMP session (the `omp` coding agent) running the `supervise` extension. It never implements the work; it only submits typed `supervise_continue` actions: `admit`, `author`, `repair`, `review`, `diagnose`, `reconcile`, `close`. | `~/.omp/agent/extensions/supervise.ts` (pointer) |
| **Owner / controller** | The Python controller that validates and freezes admission, runs the project's tests, allocates worker time, seals evidence, verifies attestation and writes every state record. The manager cannot bypass it. | `ordinary_supervise.py`, `evidence.py`, `budget.py`, `migration.py` in the release directory |
| **Transport** | `command.py`: the public entry that starts the manager, watches it for errors or stalls, enforces the host window and writes a transport receipt. | release directory |
| **Router** | `lee-llm-router`: plans the crew, checks eligibility and independence, dispatches each start to a native harness, records the attempt and the route receipt. | pinned Router files, hash-verified against the publication |
| **Native harnesses** | Claude Code, Codex, Pi, OpenCode: the processes that actually run a model under the user's own subscriptions, in a governed workspace-write sandbox rooted at the project with network denied by default. Declared owned paths are registered, watched for effective-work evidence and snapshotted by the controller, with the frozen `tests/` observed separately; those byte checks cover the declared scopes and the tests, not every file in the workspace, so a change elsewhere relies on the project's tests and the independent review to be caught. Owned paths are not an operating-system write boundary. | installed CLIs |

Releases are immutable directories under `$HOME/.local/lib/supervise/releases/`. Each carries `publication.json`, which pins the hash of every release file, every Router file the release depends on, the attempt-record schema and the runtime dependency modules. The installed release is:

~~~text
20261007-v2.8.2-attested-receipt-candidate
publication.json sha256 2e5845bd49fcd2fffa1eab96a04b64b39e0fad868e8387dcd55e5d268a26e511
~~~

Verify the installed identity (*read-only*):

~~~bash
RELEASE="$HOME/.local/lib/supervise/releases/20261007-v2.8.2-attested-receipt-candidate"
sha256sum "$RELEASE/publication.json"
python3 - "$RELEASE" <<'PY'
import hashlib, json, sys
from pathlib import Path
release = Path(sys.argv[1])
pub = json.loads((release / "publication.json").read_text())
bad = 0
for name, expected in pub["files"].items():
    actual = hashlib.sha256((release / name).read_bytes()).hexdigest()
    bad += actual != expected
    print(("OK      " if actual == expected else "MISMATCH"), name)
print("release:", pub["release"], "mismatches:", bad)
PY
grep -o 'releases/[^/"]*' "$HOME/.omp/agent/extensions/supervise.ts"
grep -o 'releases/[^/"]*' "$HOME/.codex/skills/supervise/SKILL.md"
~~~

All three lines must name the same release. The Router side is checked the same way from `publication.json`'s `router_files`, `router_dependencies` and `router_schema_files` against the live Router source tree; the installer records the result in its `INSTALLATION.json`. The controller also refuses to dispatch when the live Router bytes differ from the accepted publication ("Installed Router source differs from accepted publication"), which is the pin gate doing its job, not a regression.

## 2. Skill versus Supervisor employee

| | `/supervise` skill and command | Supervisor Pilot employee |
|---|---|---|
| Unit of work | One bounded PLAN in one project | A continuing responsibility with a durable employee identity |
| Authority source | The invoking session and the PLAN's limits | Owner-prepared protected assignment, Board claim and grants |
| Persists | One invocation directory per project+PLAN | Assignment, counters, result and Board record across processes |
| Use when | Software change with ordinary tests, documentation production, bounded maintenance | Work that must survive sessions and be managed through the employee lifecycle |
| How-to | [Supervise command](skill-how-to.md) | [Supervisor Pilot](supervisor-employee-how-to.md) |

Neither creates business authority. Publication and deployment belong to a separately authorized owner.

## 3. Assigning work

Write a PLAN in the project that owns the source (objective, owned and forbidden paths, exact test command, acceptance criteria, stop condition) and run, from that project:

~~~text
/supervise ./PLAN.md
~~~

or, outside an OMP session:

~~~bash
python3 -B "$HOME/.local/lib/supervise/releases/20261007-v2.8.2-attested-receipt-candidate/command.py" ./PLAN.md --max-seconds 3600
~~~

Admission first builds a *correctable candidate*: the owner resolves the project and source roots, validates the exact test argv and every acceptance selector, runs the complete baseline, validates any declared capability probe, and only then canonicalizes, hashes and freezes the admission. A bad candidate stays at version 0 (`PRECHECK_FAILED`, zero starts) and can be diagnosed and corrected; a frozen admission is immutable for the life of the transaction. The full PLAN shape and options are in the [command how-to](skill-how-to.md).

## 4. How the Router chooses a crew

Before the first dispatch the controller asks the Router to plan a whole crew, not just the next worker: an **author**, an **independent reviewer** and a **fallback reviewer**. The plan is written to `crew-N-<sha256>.json` (sealed) in the invocation directory with the rationale, the excluded candidates and their reasons. The selection, for the accepted run, read:

| Role | Class key | Chosen route | Why |
|---|---|---|---|
| Author | `impl/deterministic/none/s/python` | GPT-6 Luna, xhigh, Pi harness, OpenAI subscription | Qualified (comparable quality band), native proof present, zero marginal price on a prepaid subscription, "least expected variable mission cost" |
| Reviewer | `review/judge/none/s/python` | Claude Sonnet 5.5, high, Claude Code, Anthropic subscription | Qualified evidence-capable reviewer on a different model family from the author |
| Fallback | same class | Claude Sonnet 5.5, medium | Reserved so a reviewer failure has an eligible same-role replacement |

Exclusions are explicit: every Claude author candidate was excluded with "no qualified independent evidence-capable reviewer" (the only evidence-capable reviewer harness is Claude, and author and reviewer may not share a family); several OpenAI routes were excluded with "no qualified benchmark/current native proof". Inspect a crew file's `selected`, `rationale` and `excluded` keys to see exactly why a route won or lost. The plan is never pinned by the PLAN or the manager; explicit model, harness or grant requirements in the PLAN remain binding.

## 5. How benchmarks and live statistics influence staffing

Each candidate route carries three kinds of evidence in the crew file:

- **Qualification** (`qualified`, `qualification_basis`, `evidence.level`): whether the route has passed qualified benchmarks for the class, and at what evidence level. Routes without a qualified benchmark *or* a current native proof are excluded.
- **Execution statistics** (`execution_statistics`: attempts, completed, completion probability, median seconds, platform failures) taken from the production attempt ledger; quality is kept separate from completion.
- **Pricing** (`pricing`: marginal and replacement price per token, subscription badge): prepaid subscription headroom counts as zero marginal cost; metered routes are priced from the rate table.

`benchmark_freshness` records that the source hashes and the independent admission were re-verified for this decision; stale or substituted benchmark sources are excluded. Known limitation: a zero-marginal route can win a tiny task even when its production median runtime is long (Luna xhigh finished the accepted run in 63 s against a 692 s median); the planner does not yet weigh the admitted worker bound against a route's median runtime.

## 6. Independent review

The reviewer never sees the author's working tree. The controller seals the implementation evidence (source diff, test output, native session capture, attempt record) and projects a **read-only, kernel-isolated view** to a distinct harness. The reviewer gets `Read`, `Grep`, `Glob` and the trusted integrity verifier; other shell commands and credential paths are denied. Missing, substituted or tampered raw evidence prevents acceptance. The reviewer must assess the *entire* PLAN, not just the test names, and returns a typed verdict: `judge_pass` or `judge_fail` with findings. A review that produces no verdict (timeout, role violation, invalid output) is retained as an incomplete review, charged, and never counted as either acceptance or rejection.

## 7. Worker and reviewer recovery

| Event | What the controller does |
|---|---|
| Author times out or fails tests before any review | Retains the attempt, its diff, elapsed time and failure class; the manager dispatches a further `author` start carrying the observed gap. `repair` is not admissible before an independent review has completed (the controller refuses it) |
| Reviewer returns a blocking finding | `review_outcome: blocking` is recorded; a reviewer-directed `repair` round is admitted; re-review is required on the new bytes. A round may contain more than one repair start; the round counter increments only when a repair follows a review, and at most two rounds are admitted (the review after round two is the final review) |
| Repair produces no source change | The start is accounted and marked not advanced (this happened once in the accepted run); the next repair carries that fact in its packet |
| Reviewer times out or returns no verdict | Candidate stays UNACCEPTED; the reserved fallback reviewer may be used; unchanged evidence is never re-reviewed without a changed action |
| No eligible independent same-role fallback | `OWNER_REFUSED`; the mission is BLOCKED on a routing fact, not on work quality |
| Manager loses the session | Resume by re-invoking the same PLAN from the same project; unknown starts must be reconciled before anything new is dispatched |

Reviewer fallback and timeout recovery are covered by the regression suite on v2.8.2 (five failure shapes); they were **not** exercised live in the accepted run.

## 8. Routing attestation

Every start is attested *before* it executes and verified *after*. `route-N.json` is the reservation: mission, transaction, class key, crew hash, publication hash, controller path and process identity. The Router's attempt record and route receipt must carry the same `attempt_id`; `verify_start_outcome` checks the binding when the start finishes and stamps `verified_success_reason`. Until v2.8.2 the receipt carried `attempt_id: None`, so every record read `supervisor_route_unattested` and the final settlement refused; v2.8.2 corrected the Router so the id is resolved once per run. In the accepted run the final review attestation is `router-run-4a2f89468f31414c96248bed5b5407fe`, and every route file names publication `2e5845bd…`.

## 9. Sealed evidence

Evidence that crosses a role boundary is sealed: each `crew-N-<sha256>.json` has a `.seal.json` pin; the review handoff is content-addressed in chunks of at most 2 MiB (logical files up to 128 MiB, bundles up to 256 MiB) since v2.7.3, after multi-megabyte contracts had been refused by the Router's 4 MiB file bound. Seals and descriptor pins survive restart and reviewer recovery. A seal mismatch is a hard stop.

## 10. Inspecting mission state

Run this *read-only* locator from the project where the original command ran, with the exact unchanged PLAN path (the invocation directory is the SHA-256 of `project path + NUL + plan path`):

~~~bash
PLAN_PATH=./PLAN.md python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
project = Path.cwd().resolve()
plan = Path(os.environ["PLAN_PATH"]).resolve(strict=True)
root = Path.home() / ".local/state/omp/supervise"
invocation = root / hashlib.sha256((str(project) + "\0" + str(plan)).encode()).hexdigest()
print("invocation:", invocation)
for name in ("pending.json", "state.json", "decision.json", "last-review.json",
             "accepted-review.json", "closure.json", "current-observation.json", "STATE.jsonl"):
    path = invocation / name
    print(f"{name}: {'present' if path.is_file() else 'absent'}")
state = invocation / "state.json"
if state.is_file():
    print("status:", json.loads(state.read_text()).get("status"))
journal = invocation / "STATE.jsonl"
if journal.is_file():
    for line in journal.read_text().splitlines():
        r = json.loads(line)
        if r.get("kind") == "finish":
            a = r["attempt"]
            print(f"start {r['start']}: {a['route']['model']}/{a['route']['effort']} "
                  f"{r['seconds']} s verdict={a.get('verdict')}")
        elif r.get("kind") == "outcome_closed":
            print("outcome_closed")
for receipt in sorted((root / "transports").glob("*/receipt.json")):
    record = json.loads(receipt.read_text())
    if record.get("invocation") == str(invocation):
        print("transport receipt:", receipt, record.get("status"), record.get("manager_returncode"))
PY
~~~

What each file means:

| File | Meaning |
|---|---|
| `pending.json` | The original project, PLAN, invocation id and component hash; protected (mode 0400) before any dispatch |
| `state.json` | Current `status` and `version`; `next` is the manager's card |
| `STATE.jsonl` | Append-only journal: `start` / `finish` records per native start (route, seconds, attempt record with usage and cost) and the terminal `outcome_closed` |
| `decision.json` | Per-start assessments (`advanced`, `denied`, `review_outcome`), the checkpoint and the dispatch method |
| `crew-N-<sha256>.json` (+ `.seal.json`) | The planned crew for start N (content-addressed by its own hash) with rationale and exclusions |
| `route-N.json` | The pre-execution attestation for start N |
| `router-N-*/router.stdout` | The native harness stream for start N; the final `result` record carries `attempt_id`, `verdict`, `usage` |
| `last-review.json` / `accepted-review.json` | The latest review and the accepting review, with their routing attestation |
| `closure.json` | Completed starts, cumulative seconds and the final decision |
| `transports/<trace>/receipt.json` | The transport window: status, admission phase, native starts, manager return code |

## 11. Accepted delivery versus partial progress

| Signal | Accepted delivery | Partial progress |
|---|---|---|
| `state.json.status` | `ACCEPTED` | `ADMITTED`, `OWNER_REFUSED`, `RECONCILE_REQUIRED`, `CHECKPOINT_EXHAUSTED` … |
| `STATE.jsonl` | ends with `outcome_closed` | ends with a `finish` or `start` |
| `accepted-review.json` | present, `outcome: passed`, attestation attempt id present | absent, or `last-review.json` shows `blocking` |
| `closure.json.cumulative_seconds` | equals the sum of every `finish.seconds` | n/a |
| Transport receipt | `status ACCEPTED`, `delivery DELIVERED` | `INCOMPLETE`, `RECONCILE_REQUIRED`, `MANAGER_*` |

A green test run is necessary, never sufficient. **DONE** means independently accepted current bytes and original closure. **UNACCEPTED** means tests may pass but review or closure is missing. **BLOCKED** names the exact authority, capability or exhausted allowance.

The manager's return code is normally **143** on success: once the state is ACCEPTED, acknowledged and `accepted_current`, the transport sends SIGTERM to the idle manager rather than waiting for the host window. `terminal_readback` in the receipt is a pre-run flag and is `false` on a fresh run. Neither invalidates closure.

## 12. Diagnosing blocked missions

| Observation | Cause | Permitted response |
|---|---|---|
| `PRE_ADMISSION` / `PRECHECK_FAILED`, zero starts | Candidate did not validate (wrong test argv, selector not found, source root mismatch, oversized frozen input) | `diagnose` at version 0, correct the PLAN or project setup, re-admit; nothing was spent |
| `MANAGER_ERROR` in the transport receipt | The manager model is unusable in the OMP transport (for example a model name the Codex transport rejects) | Fix the OMP model configuration (global `~/.omp/agent/config.yml` or project `.omp/config.yml`); re-invoke the same PLAN |
| `MANAGER_STALLED` | The manager stopped acting with the unit open (nudge budget exhausted) | Inspect `state.json.next`; re-invoke; if it recurs, the PLAN is probably ambiguous |
| `MANAGER_TIMEOUT` | Host window expired | Re-invoke with a larger finite `--max-seconds`; no native allowance is added |
| `OWNER_REFUSED: No eligible independent same-role fallback remains` | Author and reviewer families collided (Claude author leaves no Claude reviewer) | Routing fact; v2.8.0+ refuses Claude authors up front; otherwise constrain the PLAN's author |
| `NOVELTY_INVALID` / contract exceeds bounded snapshot size | Frozen inputs or observation exceed the Router file bound | Reduce the admitted inputs; evidence chunking (v2.7.3) covers sealed handoff, not oversized contracts |
| `Route provenance requires protected immutable regular file` | `pending.json` not protected (pre-v2.8.1) | Upgrade; v2.8.1 protects it on every owner entry |
| `Native receipt record attribution/integrity mismatch` at settlement | Receipt `attempt_id` missing (pre-v2.8.2) | Upgrade; the invocation stays `RECONCILE_REQUIRED` as history |
| `Installed Router source differs from accepted publication` | Someone changed a pinned Router file after installation | Restore the pinned bytes or install a reviewed release; this gate is correct |
| `OWNER_OUTCOME_UNKNOWN` | A start's outcome was not observed (crash, kill) | `reconcile` first; never redispatch an unknown start |

## 13. Publication and concurrent ownership

Only reviewed bytes are installed. The release procedure is: freeze a reviewed manifest of every file (`FINAL-REVIEW-MANIFEST.json`), obtain an independent review with zero High/Medium findings on exactly those bytes, then run the guarded installer with absolute paths. The installer refuses if the live tree does not match the manifest preconditions, publishes the release directory read-only, backs up every pointer and Router file it will touch (`publication-backup*/RESTORE.json`), rewrites the pointers, and writes `INSTALLATION.json`. If any pointer restore fails during a failed publication, the release is retained and the error lists the unrestored pointers.

Concurrent instances coordinate through desk claims: installation and publication paths are claimed before a release is installed, and a live claim held by another instance is a stop, not something to work around. A stale claim (heartbeat older than the convention) may be superseded through the claim command, which keeps the audit trail. The command transport also writes per-start claims under the operator's claim root so two managers cannot dispatch the same start.

## 14. Model usage and accounting

Each `finish` record in `STATE.jsonl` carries the attempt's `usage` (`basis: provider_reported`, input/output/cached/reasoning tokens from the native harness) and `cost` (`basis: ["list", "marginal"]` with `usd_list` and `usd_marginal`, or `basis: ["unavailable"]` when the subscription channel reports no price). `closure.json.cumulative_seconds` must equal the sum of the five (or N) finishes. For the accepted run: OpenAI-subscription starts $0.282 list-equivalent at $0 marginal; Anthropic-subscription review starts cost basis unavailable; manager usage is reported by OMP separately and is not in the native accounting. Unknown cost is unknown, never zero.

## 15. Upgrading safely

1. Build the candidate in an isolated directory; never edit the installed release (it is read-only by design).
2. Run the targeted regressions and one broad regression against the recorded baseline; identical known failure ids, no new failures.
3. Freeze the manifest; obtain independent review (different vendor from the author); remediate at most twice; final review with zero High/Medium.
4. Run the guarded installer with absolute `--manifest` and `--review` paths from a shell that is allowed to run it. Expected result: new release directory, `publication.json` hash as reviewed, exactly the listed Router mutations, pointers rewritten, `INSTALLATION.json` written, predecessor preserved.
5. Update the Codex skill copy (`~/.codex/skills/supervise/SKILL.md`), which is outside the installer's pointer list, so it names the new `command.py`.
6. Verify with the identity check in §1, then run one real bounded mission through the public entry before declaring the upgrade accepted. A regression-clean install that has never closed a mission is not accepted.

## 16. Rolling back

The installer's rollback record lists every file it replaced with its backup bytes and original hash. Restoring is a file copy; nothing else is required:

~~~bash
# RESTORE_DIR is the publication-backup directory written by the installer you are reverting
RESTORE_DIR="<publication-backup directory from INSTALLATION.json rollback_record>"
python3 - "$RESTORE_DIR" <<'PY'
import hashlib, json, shutil, sys
from pathlib import Path
backup = Path(sys.argv[1])
record = json.loads((backup / "RESTORE.json").read_text())
for target, info in record.items():
    data = (backup / info["backup"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == info["sha256"], target
    shutil.copyfile(backup / info["backup"], target)
    Path(target).chmod(info["mode"])
    print("restored", target)
PY
~~~

For v2.8.2 the record restores two OMP extension pointers, the chief skill and the Router's `doctor.py` (to its v2.8.1 bytes). The predecessor release directory is preserved by the installer, so after restoring the pointers the previous release is live again. Also revert the Codex skill copy by hand. Then verify against the *predecessor*, not against §1's v2.8.2 values: run the §1 check with `RELEASE` set to the predecessor directory named in `INSTALLATION.json` (`predecessor`) and compare `publication.json`'s hash with `predecessor_publication_sha256` from the same record; the two pointer greps must now name the predecessor. For the v2.8.2 record the predecessor is:

~~~text
predecessor  20261007-v2.8.1-production-dispatch-candidate
publication  sha256 c7ef937cdc1b1d6c349cccf4ffb3bc439dc45d28ad91b2b324c0a571b3206f73
~~~
 Roll back the Router and the pointers together; a pointer on one release with Router bytes of another trips the pin gate.

## 17. When human intervention is actually required

Leave ordinary worker timeout, reviewer timeout, focused repair, test execution and status reconciliation to the supervisor while the admitted allowance remains. Intervene for:

- a new scope or a changed acceptance criterion;
- missing protected owner or Board authority, or a true contradictory requirement;
- exhausted allowance (the command cannot self-grant starts or seconds);
- an unavailable independent route (no eligible reviewer family);
- a changed installed identity (pin gate refusal) or a live claim held by another instance;
- a consequential release decision: publication, deployment, external communication.

A genuine blocking review finding after exhaustion needs explicit bounded remediation authority and a new independent review. Unknown state must be reconciled; neither a cached verdict nor a status sentence may be invented. Publication and deployment belong to their separately authorized owner: a supervised candidate can be accepted without being live.

Start from the [supervision overview](overview.md) for the audience map, or the [case study](case-study.md) for why each of these controls exists.

---

*Production copy of this page: [https://nginx.leebasehome.com/docs/pages/supervision-owner-guide.html](https://nginx.leebasehome.com/docs/pages/supervision-owner-guide.html). Part of the [`/supervise` public documentation](../README.md), Apache License 2.0.*
