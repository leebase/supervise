# Supervision: a new teammate's guide

You are an experienced developer. You know how to launch Claude Code, Codex or another coding agent and get a plausible diff back. This guide explains why we do not stop there, and walks you through your first supervised assignment end to end.

## What is `/supervise`, and why does it exist?

Launching a coding agent gives you an *attempt*. Sometimes the attempt is excellent. Sometimes it times out after making useful changes, or passes the tests it wrote and misses the ones it did not, or reports "done" with a confident paragraph and no evidence. When that happens, the person who launched it becomes the recovery mechanism: re-read the diff, re-run the tests, re-launch, re-explain.

`/supervise` turns an attempt into a **transaction**. One bounded assignment is admitted with its tests frozen; a worker implements it; the real tests run; the evidence is sealed; a different model independently reviews the whole assignment; genuine findings get a bounded repair; and the transaction closes as ACCEPTED only when the exact bytes passed review. Every start, timeout and verdict stays on the record. If the allowance runs out, you get BLOCKED with the exact reason, not a cheerful summary.

How it differs from just launching an agent:

| Launching an agent | Supervised assignment |
|---|---|
| One model, one session, one opinion | Qualified crew: author, independent reviewer, fallback |
| "Tests pass" as reported by the author | Tests run by the controller on frozen inputs; baseline compared |
| You read the diff | A reviewer on a different model family reads sealed evidence in a read-only view |
| Retry by hand | Bounded, finding-directed repair with cumulative accounting |
| Done when it says so | Done when the transaction closes ACCEPTED with attestation |

## The concepts, in order

1. **Assignment.** A PLAN file in the project: one objective, owned paths, forbidden paths, the exact test command, acceptance criteria, a stop condition.
2. **Supervisor.** The manager session that admits the PLAN and submits typed actions (`admit`, `author`, `repair`, `review`, `close`). It never edits project files itself.
3. **Author.** The worker that implements or repairs, inside its owned paths, with a finite time bound.
4. **Reviewer.** A different model family that judges the exact candidate against the complete assignment and returns a typed verdict.
5. **Crew selection.** The Router plans author, reviewer and fallback together from qualified benchmarks, production statistics and price, with the independence rule enforced before anyone runs.
6. **Independent acceptance.** The only way a transaction becomes ACCEPTED. A green test suite is evidence, not acceptance.
7. **Evidence.** Frozen test inputs, the baseline run, source hashes before and after, native session captures, attempt records, review verdicts. Everything a verdict rests on is on disk and hashed.
8. **Recovery.** A worker timeout or test failure before review leads to a further author start with the observed gap; a blocking review finding leads to a reviewer-directed repair round (at most two per admission); a failed review may use the reserved fallback reviewer. Never a silent restart.
9. **Transaction.** One invocation directory keyed by project and PLAN path, with an append-only journal (`STATE.jsonl`) and cumulative accounting. Re-invoking the same PLAN from the same project resumes that transaction. Renaming or copying the PLAN creates a *new* invocation; it is prohibited by policy because it orphans the original accounting, not because the system links the two.
10. **Routing attestation.** Each start is reserved before execution and verified after; the receipt must carry the same attempt id as the attempt record.
11. **Durable closure.** `closure.json` and `accepted-review.json` exist, the journal ends with `outcome_closed`, and the cumulative seconds equal the sum of every start.

## Architecture

~~~mermaid
flowchart TD
    You["You: PLAN.md in the project"] --> Transport["command.py transport: host window, manager watch, receipt"]
    Transport --> Manager["Manager (OMP session) submits typed actions"]
    Manager --> Owner["Owner/controller: admission, frozen tests, allocation, seals, attestation, state"]
    Owner --> Router["Router: crew plan, eligibility, dispatch, attempt record, route receipt"]
    Router --> Author["Author on a native harness (Pi, Codex, ...)"]
    Author --> Tests["Real project tests on frozen inputs"]
    Tests --> Seal["Sealed evidence"]
    Seal --> Reviewer["Independent reviewer, different model family, read-only view"]
    Reviewer -->|blocking finding| Repair["Bounded repair round (max 2)"]
    Repair --> Tests
    Reviewer -->|accepted| Close["Transaction closed ACCEPTED with attestation"]
    Owner --> State["Invocation directory: STATE.jsonl, crew/route files, closure.json"]
~~~

## First-use walkthrough

The example is the real acceptance run of v2.8.2 on October 7, 2026: a small, dependency-free Python project (a scraping utility) whose documented next step was a `scan` command that must refuse to run unless two earlier preflight checks (`auth-check` and `drift-check`) both reported ok. Identifiers, hosts and paths are omitted; the shape is exact.

### 1. Prerequisites

- An OMP session (the `omp` coding agent) with the `supervise` extension installed, or a shell for the Codex transport.
- The installed release `20261007-v2.8.2-attested-receipt-candidate` and the staffing router it pins.
- Native harnesses with your own subscriptions (the accepted run used Pi, Codex and Claude Code).
- A project with an ordinary Python test suite (`unittest` or `pytest` with JUnit output). Confirm the real test command first.

### 2. Installation and configuration

You do not install anything for a first assignment on an existing estate. Verify the installed identity instead (the one-line checks are in the [owner guide](owner-operator-guide.md) §1). If your project needs a specific manager model, put it in the project's `.omp/config.yml`; the accepted run pinned the manager to an OpenAI model at high effort.

### 3. Creating an assignment

Write the failing tests first, then the PLAN. The accepted PLAN said, in substance:

~~~text
Objective: a fail-closed `scan` CLI gate.
Owned paths: scrap_suno/ (two files expected: cli.py, scan_gate.py; about 120 lines).
Forbidden: tests/, existing commands, anything outside the package; no network, no browser, no credentials.
Tests: /usr/bin/python3 -m unittest discover -s tests -v   (format unittest; timeout 30 s)
Baseline policy: nonregression. Expected baseline: six scan-gate tests FAIL, the other 45 PASS.
Acceptance: `scan` refuses with exit 2 and named reasons unless both status files report ok;
            exits 3 when the gate passes; appears in --help;
            scrap_suno.scan_gate.evaluate_scan_gate(auth, drift) returns allowed + reasons.
Limits: worker 900 s; checkpoint 6 starts / 3000 s (internal, economical; not a user budget).
Definition of Done: all 51 tests pass, change confined to scrap_suno/, independent reviewer accepts
            the full plan on sealed evidence with zero blocking High/Medium; transaction closes ACCEPTED.
~~~

### 4. Invoking `/supervise`

From the project directory:

~~~text
/supervise ./PLAN.md
~~~

or, from a shell:

~~~bash
python3 -B "$HOME/.local/lib/supervise/releases/20261007-v2.8.2-attested-receipt-candidate/command.py" ./PLAN.md --max-seconds 3600
~~~

The manager admitted the PLAN (tests frozen, baseline run: 45 pass / 6 fail as expected) and the Router planned the crew: author GPT-6 Luna on Pi, reviewer Claude Sonnet 5.5 on Claude Code, fallback Sonnet 5.5 medium.

### 5. Inspecting progress

While it runs, the invocation directory fills up. The journal tells the story:

~~~text
start 1  author  gpt-6-luna/xhigh      63.4 s  verdict=pass        (6 tests repaired, 0 regressions)
start 2  review  claude-sonnet-5-5/high 108.0 s verdict=judge_fail  (blocking finding)
start 3  repair  gpt-6.1-sol/high       34.0 s  verdict=pass        (no source change; not advanced)
start 4  repair  gpt-6.1-sol/high       58.3 s  verdict=pass
start 5  review  claude-sonnet-5-5/high 127.8 s verdict=judge_pass
outcome_closed   5 starts, 391.575 s
~~~

Use the read-only locator in the [owner guide](owner-operator-guide.md) §10 to print this for your own run.

### 6. Understanding review findings

The first reviewer rejected the candidate even though all 51 tests passed. Its finding: the `scan` command returned early when the first status file was missing or unreadable, which hid the second gate's failing status and its recommended action. No test covered that interaction. That is exactly the kind of defect a self-reported "all green" hides, and exactly why the reviewer reads the whole PLAN rather than the test names.

### 7. Observing automatic remediation

The manager dispatched a finding-directed repair. The first repair worker produced no source change (it went looking for a manager-only tool instead of editing the file); the controller recorded that start as not advanced and the second repair packet said so explicitly: "The previous repair employee produced no source change ... do not repeat. Independent reviewer found ... Remove that early return; aggregate missing/unreadable status file path errors with any failing loaded gate reasons." The second repair fixed it. No human touched the code. Both repair starts belong to the same reviewer-directed round (the records show `repair_round: 1` for both); the counter increments only when a repair follows a new review, and at most two rounds are admitted, after which the next review is the final one.

### 8. Inspecting the accepted result

- `state.json`: `status: ACCEPTED`.
- `accepted-review.json`: `outcome: passed`, reviewer route, routing attestation with the attempt id.
- `closure.json`: `completed_starts: 5`, `cumulative_seconds: 391.575`, which equals the sum of the five finishes.
- The transport receipt: `status ACCEPTED`, `delivery DELIVERED`, `native_starts 5`, `manager_returncode 143` (the transport terminates the idle manager after acceptance; this is the normal success exit).
- Source: only `cli.py` modified and `scan_gate.py` added; the frozen tests byte-identical to baseline.

### 9. Troubleshooting common failures

| You see | It means | Do |
|---|---|---|
| `PRECHECK_FAILED` at version 0, zero starts | Your test command, a selector or the source root did not validate | Fix the PLAN or project; nothing was spent |
| `MANAGER_ERROR` in the receipt | The manager model is not usable by the OMP transport | Change the OMP model configuration and re-invoke the same PLAN |
| `judge_fail` with findings | Genuine review rejection | Let the supervisor run the repair round; read the finding |
| `OWNER_REFUSED` naming no eligible independent same-role fallback | Routing cannot provide an independent reviewer for that author family | Routing fact, not code quality; see the owner guide |
| `RECONCILE_REQUIRED` | A start's outcome is unknown or attestation failed at settlement | Re-invoke the same PLAN; the controller reconciles before anything new runs |
| Return code 143 with `ACCEPTED` | Normal success exit | Nothing |

## What you should never do

Do not re-run the PLAN under a new name to "get fresh starts"; do not edit protected state; do not forge a verdict; do not implement the assignment yourself while the manager is open; do not treat a green test run as acceptance. The transaction is the point.

**Next:** the [command how-to](skill-how-to.md) for the full PLAN and option reference, the [owner guide](owner-operator-guide.md) for diagnosis and state, the [case study](case-study.md) for the sixteen ways this failed before it worked, and the [supervision overview](overview.md) for the audience map.

---

*Production copy of this page: [https://nginx.leebasehome.com/docs/pages/supervision-teammate-guide.html](https://nginx.leebasehome.com/docs/pages/supervision-teammate-guide.html). Part of the [`/supervise` public documentation](../../README.md), Apache License 2.0.*
