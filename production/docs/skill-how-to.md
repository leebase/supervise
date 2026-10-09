# Use the installed supervise command

The `/supervise` skill delivers one bounded project PLAN through a qualified crew: an author worker, the project's real tests, sealed evidence, a distinct independent reviewer, bounded repair and an attested, durable closure. Start in the ordinary project that owns the source, with its instructions and test runner available. The installed evidence backend supports ordinary Python `unittest` and `pytest` with JUnit output. Confirm the project's actual test command before assigning work; this is not a universal adapter for every language or harness.

Installed release: `20261007-v2.8.2-attested-receipt-candidate` (publication SHA-256 `2e5845bd…`). Commands below were checked against it on October 7, 2026.

## Prepare an assignable PLAN

Name one objective, exact owned paths, forbidden paths, the expected artifact, required evidence, the exact ordinary test command and acceptance criteria. Include a finite stop condition and any owner-specified model, harness, grants or outer limits. Give workers the context needed to implement within scope, and tell them to stop and report an exact conflict if an API, dependency or repository premise differs from the frozen specification. A reviewer must assess the entire brief, not merely the test names.

The admission shape the manager submits from your PLAN:

- **Exact test argv** (for example `/usr/bin/python3 -m unittest discover -s tests -v`), format `unittest` or `pytest`. Tests must be expressible as literal worker command grants: wildcard patterns such as `-p test_*.py`, parentheses and newlines in the argv are rejected before freeze.
- **`tests.timeout_seconds`**: defaults to 30; explicit finite values up to 3600 are honored exactly; a test that needs more than the remaining elapsed envelope is refused, never silently shortened.
- **`baseline_policy`**: `all-green` (default) or `nonregression` for scoped work with a completed red baseline. Under `nonregression`, selected acceptance tests must pass, unrelated retained failures must remain unchanged or be repaired, and new or regressed failures block acceptance.
- **Acceptance propositions** with test selectors; every selector is validated against the real baseline before freeze.
- **`owned_paths`**; optional `project_root` / `source_root` when discovery differs.
- **`required_capabilities`**: only non-destructive exact probes (an SSH probe must be native `/usr/bin/ssh` with `BatchMode=yes`, finite `ConnectTimeout` and `StrictHostKeyChecking=yes`). Local Python work declares none.
- **Worker `timeout_seconds`** and an internal economical **checkpoint** (`max_starts`, `max_seconds`, `first_value_seconds`). These are management estimates; the manager must not attribute them to the user or override explicit user limits.

The PLAN used by the accepted run (sanitized):

~~~text
Objective: a fail-closed `scan` CLI gate in this project.
Owned: scrap_suno/ (expected: cli.py modified, scan_gate.py added; ~120 lines).
Forbidden: tests/, existing commands, anything outside the package; no network, no browser, no credentials.
Tests: /usr/bin/python3 -m unittest discover -s tests -v   (unittest; timeout 30 s; baseline_policy nonregression)
Expected baseline: six scan-gate tests FAIL, the other 45 PASS.
Acceptance: scan-gate-cli (exit 2 with named reasons unless both status files report ok; exit 3 when
            the gate passes; listed in --help) and scan-gate-library (evaluate_scan_gate returns allowed + reasons).
Worker timeout 900 s; checkpoint 6 starts / 3000 s (internal economical limits, not a user budget).
Definition of Done: all 51 tests pass, change confined to scrap_suno/, an independent reviewer accepts
            the full plan on sealed evidence with zero blocking High/Medium; the manager closes ACCEPTED.
~~~

## Invoke

The installed normal entry, inside an OMP session, is exactly:

~~~text
/supervise ./PLAN.md
~~~

From a shell (the Codex skill uses this same transport):

~~~bash
python3 -B "$HOME/.local/lib/supervise/releases/20261007-v2.8.2-attested-receipt-candidate/command.py" ./PLAN.md
~~~

Options, as printed by `command.py --help`:

| Option | Meaning |
|---|---|
| `--max-seconds N` | Finite host window for the manager transport; default 1800, at most 7200. This is not the test timeout and adds no native allowance. |
| `--authorize-continuation SOURCE` with `--continuation-seconds N` | Operator-only explicit direction source for a same-objective host continuation after the window expires; adds **zero** native worker or reviewer budget. |
| `--authorize-migration SOURCE` | Operator-only, audited migration of an original TableApp invocation. Not part of ordinary use. |

Run it from the plan's ordinary project directory. The transport starts an installed OMP manager, which admits the PLAN, asks the Router for a crew, dispatches the author, runs the ordinary tests on the produced bytes, seals the evidence, dispatches the independent reviewer, repairs genuine review findings (at most two reviewer-directed rounds; a round may contain more than one start), and closes. The transport stops early on two consecutive manager error turns (`MANAGER_ERROR`) or when the manager stalls with the unit open (`MANAGER_STALLED`), so a dead manager no longer costs the whole window.

The Claude Code and OpenCode `/supervise` commands are the older router shim (a six-command `staff` / `run` / `classify-failure` / `next-action` loop). They are a distinct entry and are not the installed v2.8.2 transport.

## Read results

The transport prints and writes a receipt (`receipt.json` under `transports/<trace>/` in the state root `~/.local/state/omp/supervise/`): `status`, `admission_phase`, `native_starts`, `delivery`, `manager_returncode`, `manager_fault`. The invocation directory (the state root plus the SHA-256 of `project path + NUL + plan path`) holds `state.json`, `STATE.jsonl`, `decision.json`, `crew-N-<sha256>.json`, `route-N.json`, `router-N-*/`, `last-review.json`, `accepted-review.json`, `closure.json` and `current-observation.json`. The read-only locator and the meaning of each file are in the [owner guide](owner-operator-guide.md) §10.

**DONE** means independently accepted current bytes and original closure: `state.json` ACCEPTED, `accepted-review.json` present with a routing attestation, `closure.json` present, `STATE.jsonl` ending `outcome_closed`. **UNACCEPTED** means a candidate may pass tests while review or closure is missing. **BLOCKED** names the exact authority, capability or exhausted allowance that prevents the next action. A manager return code of 143 after ACCEPTED is the normal success exit (the transport terminates the idle manager).

## Resume safely

An interrupted process does not authorize a fresh assignment. From the same project, invoke the same PLAN; the controller reconciles any unknown live start or cleanup first and keeps the original binding, failed attempts, reviewer history and accounting. Renaming the PLAN, copying it, changing workers or asking an unchanged reviewer again does not create progress or renew authority. If the real plan conflicts with an installed API or authority, report that exact conflict instead of inventing a different architecture.

## Example 1: the accepted run (real, sanitized)

Author (GPT-6 Luna on Pi) implemented the gate in 63 s; all six acceptance tests went green with zero regressions. The independent reviewer (Claude Sonnet 5.5) rejected: an early return on the first status-file error hid the second gate's failing status, a case no test covered. The first repair start produced no source change and was recorded as not advanced; the second repair (GPT-6.1 Sol on Codex) fixed it; the final review accepted. Five starts, 391.575 s, every receipt attested, transaction closed ACCEPTED. The test pass alone would have left it UNACCEPTED.

## Example 2: worker failure and autonomous recovery (illustrative)

A worker times out after a useful edit. The supervisor retains that attempt's identity, source diff, elapsed time and failure class, then checks whether the remaining admitted allowance permits a further author start (before any review has completed, `repair` is not admissible; recovery is more authoring). It supplies the observed gap, reruns the ordinary checks and requests independent review of the final bytes. No routine restart decision reaches the owner. If allowance is exhausted, the supervisor returns BLOCKED with the original transaction and spent accounting.

## Example 3: complete implementation, reviewer timeout (illustrative)

The worker finishes and the real tests pass, but the independent reviewer times out without a verdict. The supervisor keeps the tested candidate and marks acceptance incomplete. Within the same admitted allowance, it reconciles the failed review and starts the reserved fallback reviewer on the exact candidate and full PLAN. A genuine accepting verdict can close the original transaction; a second incomplete review remains UNACCEPTED or BLOCKED under the applicable bound. Examples 2 and 3 are teaching scenarios covered by regression tests, not additional acceptance runs.

## Troubleshooting

- A PLAN refused at admission (`PRECHECK_FAILED`, version 0) is not an implemented result and spent nothing; use `diagnose`, correct, re-admit.
- `MANAGER_ERROR`: the OMP manager model is rejected by the transport; fix the OMP model configuration (global `~/.omp/agent/config.yml` or project `.omp/config.yml`) and re-invoke.
- A missing ordinary test runner is a project setup or specification conflict, not permission to fabricate a green observation.
- A timeout or missing verdict cannot be relabeled as a rejection of candidate quality or as acceptance.
- `OWNER_REFUSED` with "No eligible independent same-role fallback remains": author and reviewer families collided; a routing fact, not a quality verdict.
- "Installed Router source differs from accepted publication": the pin gate; restore the pinned bytes or install a reviewed release.
- An exhausted native allowance needs applicable owner authority. The command cannot self-grant more worker or reviewer starts.

Return to the [supervision overview](overview.md), use the [owner guide](owner-operator-guide.md) for diagnosis and intervention decisions, or read the [case study](case-study.md) for the failures behind each of these rules.

---

*Production copy of this page: [https://nginx.leebasehome.com/docs/pages/supervise-how-to.html](https://nginx.leebasehome.com/docs/pages/supervise-how-to.html). Part of the [`/supervise` public documentation](../../README.md), Apache License 2.0.*
