# Architecture

This document describes the architecture of the public portable build of `/supervise` (v0.x, pre-release candidate); it differs from production v2.8.2, a private, frozen deployment that cannot run outside its owner's estate, in that crew planning is done by a small TOML-driven backend behind a six-method interface instead of a private staffing router.

## Module map

All Python modules import only the standard library. Paths are under `supervise/`.

| Layer | Modules | Responsibility |
|---|---|---|
| Kernel | `kernel/process.py` | Runs one program in a new process group: absolute `argv[0]`, explicit environment and working directory, new capture files, timeout then SIGTERM/SIGKILL, returns exit code, timing and capture hashes. Reads process identity from `/proc`. |
| | `kernel/journal.py` | Locked, append-only `STATE.jsonl`: `start`, `finish`, `reconcile`, `outcome_closed`. Enforces start numbering, one accounting record per start, closure only when all starts are accounted. |
| | `kernel/seal.py`, `review_view.py`, `verifier.py` | Content-addressed evidence seals with the credential scanner; reviewer view materialization and the bubblewrap command lines; the standalone `verify.py` copied into each view. |
| | `kernel/attest.py`, `verdict.py`, `novelty.py` | Route records and receipts; verdict extraction; snapshot of owned source files and the "changed since last review" test. |
| Controller | `controller/admission.py` | `initialize` (pending record) and `admit` (validate, freeze, baseline). |
| | `controller/evidence.py`, `unittest_runner.py` | Freeze test inputs; run tests in a sandbox; parse unittest output or JUnit; compare with baseline. |
| | `controller/classify.py`, `budget.py` | Packet to task class (`role/oracle/size/languages`); elapsed window and worker time allocation. |
| | `controller/transaction.py` | `Controller.perform`: the `author`, `repair`, `review`, `reconcile`, `diagnose`, `close` actions. |
| Backend interface | `backend.py` | `RoutingBackend` protocol with six methods (`identity`, `plan_crew`, `check_grants`, `preflight_project`, `prepare_launch`, `interpret`) and its dataclasses. A backend describes and interprets; the kernel spawns, journals, seals, accounts and closes. |
| Simple backend | `backends/simple/config.py`, `codex.py`, `claude.py` | Parses `backend.toml`, plans the crew, and per harness builds the command line and reads its JSON output. `backends/stub.py` is a scripted backend for tests. |
| Runner/transport | `runner.py`, `transport.py` | Starts and watches the `omp` manager, resolves the state root, detects manager errors and stalls, writes the receipt. |
| OMP extension | `omp/supervise.ts` | Registers `/supervise` and the `supervise_continue` tool in `omp`, restricts the manager's tools, calls `python -m supervise begin` and `intent`, nudges the manager and keeps its turn ledger. |
| Installer | `installer.py` | Hash-checked, read-only releases; `INSTALLATION.json`; pointer backup and restore. |
| Doctor | `doctor.py` | Environment, root, harness, independence and manager checks. |
| Verify | `verify.py` | The ten offline checks. |
| Other | `cli.py`, `__main__.py`, `schemas.py`, `portability.py`, `conformance.py` | Command line and backend assembly; built-in record schemas and validator; a scanner for host-specific tokens (run by a test over `supervise`, `tests` and `docs`); a registry of scenarios ported from v2.8.2. |

## Processes

```
supervise run ──spawns──> omp (manager model) + omp/supervise.ts
                              │  tool call supervise_continue
                              v
                    python -m supervise intent  ──> Controller ──> codex / claude (worker)
                                                             └──> bwrap ──> codex / claude (reviewer)
```

`run` polls `state.json` and the manager's events. The manager can only read files and call `supervise_continue`; every state change happens in `python -m supervise intent`.

## Life of one invocation

The invocation directory is `<state root>/<sha256 of "<project path>\0<plan path>">`, mode 0700. Repeating the same project and plan reuses it.

1. **begin.** `/supervise <plan>` makes the extension call `supervise begin <plan> --project <cwd>`. This creates the directory, writes `pending.json` (invocation id, project, plan path, plan SHA-256, creation time; mode 0400) and `state.json` `INITIALIZED` at version 0, and returns the bootstrap prompt. The extension restricts the manager's tools and sends the prompt.
2. **admit.** The manager reads the plan and tests and sends an `admit` intent at version 0. The controller validates it, runs the baseline, freezes the contract and writes `ADMITTED` at version 1 (or `PRECHECK_FAILED` at version 0). See the security model, section 1.
3. **author.** `author` with `targets` (open acceptance ids). The controller allocates time, plans the crew (first non-failed author; reviewers of other families), then dispatches one start (below). Afterwards it stores the controller-run test results (`observation` and `comparison`) with the attempt.
4. **review.** `review` requires a finished author start, source unchanged since it, and no earlier completed review of the same source. The controller plans the crew with the author pinned, seals the evidence, materializes the view, runs the trusted verifier, writes the review packet (full plan, acceptance criteria, owned paths, verdict format), wraps the reviewer in bubblewrap and dispatches. On `ACCEPT` it marks each acceptance gap `PROVED` whose selected tests passed in the author's controller-run results, marks the `independent-review` gap `PROVED`, and writes `accepted-review.json`.
5. **repair.** After a completed `REJECT` of the current author's work, `repair` dispatches another author start with the same packet, at most twice. `author` after a completed review is refused.
6. **close.** Requires every start accounted and settled, a valid accepted review whose snapshot equals the current owned source, unchanged `tests/`, acceptable controller-run results with every acceptance gap `PROVED`, and no confinement violation. It appends `outcome_closed`, writes `closure.json` and sets `ACCEPTED`.

`diagnose` reads state. `reconcile` settles a start whose worker is no longer live (it needs a recorded process identity and charges the whole time since reservation) or a finished start not yet settled. If a start is unaccounted or unsettled, any other action sets `RECONCILE_REQUIRED`.

**One dispatch** (author or review):

1. Check the pinned budget files; compute the allocation: the smaller of the admitted worker timeout, the checkpoint seconds not yet used, and the window left minus the test timeout and a 5 s reserve. If it is zero or less, or the admitted `max_starts` is reached, set `CHECKPOINT_EXHAUSTED`.
2. Write `crew-N-<sha256>.json`, `packet-N.txt` and `route-N.json`.
3. Call `prepare_launch` for the argv, the environment allowlist and any mounts.
4. For an author: write `confinement-N.json`.
5. Append the `start` record to the journal, before launching anything.
6. Run the process with the packet on stdin; capture to `attempts/start-N-<attempt id>/`; write `execution-N.json` at spawn.
7. Call `interpret` for final text, model, usage, cost and failure class (`none`, `platform_env`, `platform_timeout`, `worker`).
8. Author: compare confinement; run the tests in the sandbox. Review: re-run the trusted verifier; parse the verdict.
9. Build the attempt with its routing receipt; append the `finish` record.
10. Settle: verify the receipt, update `decision.json` and `state.json`.

## State machine

`state.json` has a `status` and an integer `version`. Every commit raises the version; an intent with a stale `expected_version` is refused. Statuses written by the code:

| Status | Set when |
|---|---|
| `INITIALIZED` | `begin` created the invocation (version 0). |
| `PRECHECK_FAILED` | `admit` was refused before freezing; the message is in `state.json` (version 0). |
| `ADMITTED` | `admit` succeeded (version 1). |
| `AUTHOR_COMPLETE` | An author or repair start finished, even if the worker failed (the failure class is recorded). |
| `REVIEW_PASSED` | A review completed with a passing verifier and `ACCEPT`. |
| `REVIEW_BLOCKING` | A review completed with `REJECT`. |
| `REVIEW_INCOMPLETE` | A review failed, had no valid verdict or failed the verifier, or a review start was reconciled. The reviewer is excluded from later reviews. |
| `AUTHOR_INCOMPLETE` | An author start was reconciled instead of finishing. |
| `RECONCILE_REQUIRED` | A start is unaccounted or unsettled and another action was tried. |
| `CHECKPOINT_EXHAUSTED` | No time or starts remain. |
| `ACCEPTED` | `close` succeeded. |

`run` ends on `ACCEPTED` (after offline verification), `CHECKPOINT_EXHAUSTED`, `RECONCILE_REQUIRED` and `PRECHECK_FAILED`. The receipt can also end `UNVERIFIED`, `MANAGER_TIMEOUT`, `COMMAND_REFUSED`, `MANAGER_ERROR` (two consecutive error turns), `MANAGER_STALLED` (no progress after the turn ledger shows 2 idle turns or 8 turns, plus 20 s) or `MANAGER_EXITED`.

## Where each record is written

In the invocation directory unless stated.

| Record | Writer | Notes |
|---|---|---|
| `pending.json` | `begin` | 0400; plan digest, project, plan path. |
| `state.json`, `decision.json` | admission, then each commit | `decision.json` holds gaps, last author/review, repair count, failed routes, confinement violation. |
| `contract.json`, `acceptance.json`, `elapsed-checkpoint.json`, `binding.json` | `admit` | 0400; pinned. `baseline/`, `baseline-run/`, `baseline-observation.json` are baseline evidence. |
| `STATE.jsonl` (+ `.lock`) | journal | `start`, `finish`, `reconcile`, `outcome_closed`; fsynced. |
| `crew-N-<sha256>.json`, `route-N.json` | dispatch | 0444; before the `start` record. |
| `packet-N.txt`, `review-packet-N.txt` | dispatch, review | 0400. |
| `confinement-N.json` | author dispatch | Inventory of unowned project entries. |
| `execution-N.json` | at process spawn | Process identity for reconcile. |
| `attempts/start-N-<id>/stdout.txt`, `stderr.txt`; `attempts/verify-<id>/` | dispatch, verifier | Raw captures. |
| `test-runs/run-N-<id>/` | after each author start | Controller-run test output. |
| `evidence/<identity>/` | review | Seal: `seal.json` and `chunks/`. `evidence/author-N-<id>/` holds the files that were sealed. |
| `views/<identity>--<reviewer>/` | review | Reviewer's read-only view. |
| `accepted-review.json` | settle on `ACCEPT` | 0400. |
| `closure.json` | `close` | 0444; equals the `outcome_closed` record. |
| `manager-turns.json` | OMP extension | `turns`, `version`, `idle`; read by the stall watch. |
| `<state root>/transports/<uuid>/rpc.jsonl`, `stderr.txt`, `receipt.json` | `run` | Manager trace and the receipt printed by `run`. |
| `<data root>/releases/<name>/`, `INSTALLATION.json`, `UNINSTALLATION.json` | `install`, `uninstall` | See the install guide. |
