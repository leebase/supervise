# Building an AI Supervisor That Actually Finishes the Job

*What it took to move from autonomous agent execution to independently accepted delivery.*

This is an engineering case study, not a success story. It reconstructs, from our own records, how a system called `/supervise` went from an agent that could write code to a supervisor that reliably completes bounded work, recovers from its own failures, has the result independently verified, and accounts for every start. It took about eight days of calendar time, eighteen installed releases in forty-eight hours at the end, and sixteen distinct classes of failure. Every number below is labelled as measured, reported or incomplete, and the limits of the final verification are stated at the end.

We spent days discovering that making AI agents write code is relatively easy. Making them reliably complete work, recover from failures, independently verify results, and account for their actions is considerably harder. We have the evidence for that, and we think the lessons are useful even if you never use our software.

## The original ambition

We run a small estate of AI employees. The ask was simple to state: assign a bounded piece of work and have a supervisor manage implementation, failures, independent review and delivery, so that the human who assigned it does not become the recovery mechanism. The owner's instruction, verbatim, when the final push began: "make the installed `/supervise` system reliably deliver useful work through autonomous execution, recovery, independent review and accepted closure."

The pieces already existed. A staffing router could choose a model and harness for a class of work from benchmarks, production statistics and price. Native coding harnesses (Claude Code, Codex, Pi, OpenCode) could run under prepaid subscriptions. A first `/supervise` was a slash command wrapped around a six-command router loop: staff, run, classify the failure, pick the next action. It was accepted for rollout on September 30, 2026.

Within a day the owner reported that "the new /supervise got stuck in the same way, the model ignored the instructions, and apologized afterward." That sentence is the whole problem. A supervisor that is itself a model following prose instructions is just another agent, with the same failure modes, now one level up.

## What went wrong

Between October 1 and October 7 we recorded sixteen classes of failure. We are not going to catalogue every failed attempt; the private record does that. These are the classes, roughly in the order the system had to grow through them, and what each one taught us.

**1. Incorrect project or test context.** The supervisor froze test selectors that did not match the real test ids, inherited the parent repository's source root inside an isolated folder, rejected a harmless `-B` interpreter flag, and once submitted three different selector shapes before one validated. Lesson: *admission must be executed, not described.* The owner now runs the complete baseline and validates every selector before anything is frozen.

**2. Immutable malformed admission.** Early versions froze the admission before validating it. A bad selector or a wrong source root became an immutable transaction that could only be abandoned, with zero starts and a dead record. The owner's ruling: "Admission configuration must become immutable ONLY AFTER it has been validated as executable." The fix (v2.4) introduced a *correctable candidate*: discover, validate, run a real preflight, then canonicalize, hash and freeze. A follow-on defect, where a pre-admission diagnose corrupted the candidate itself, took another round (v2.4.2).

**3. Hidden execution timeouts.** Three of them. A hardcoded 30-second test clamp sat inside the controller with no public override, so an explicit 420-second test bound was silently reduced to 30. A 300-second worker ceiling, introduced in v2.3.1 and present in every release until v2.8.1, truncated capable authors whose production median was 110 to 620 seconds; the one real product mission under ordinary supervision lost three of eight starts to it. And the public transport watched only the state file, so a manager that errored on every turn was reported only as a timeout after the full host window, up to two hours of silence per attempt. The owner's ruling on the first: "Do not solve this by removing time limits. Solve it by making the time limit we intentionally selected actually work."

**4. Broken manager transport.** The manager runs as an ordinary session of our coding agent. On October 6 the global default model was changed to a newer model that the agent's transport does not support under a subscription account. Every manager turn errored before admission. Three separate desks hit it independently before anyone connected the symptom to the configuration. The native CLI ran that model fine, so the Router side was unaffected and the failure was invisible from there. Lesson: *a supervisor has its own dependencies, and a configuration change can kill it silently.* The transport now stops after two consecutive manager errors and names the fault in its receipt.

**5. Worker capability propagation.** Declared capabilities (an SSH grant, a network allowance) reached the admission record but not the sandboxed worker. One release's fix broke the Router's declared Python 3.10 support with an unconditional import and was withdrawn the same afternoon (v2.5.0 and v2.5.1, rolled back to v2.4.2). Lesson: *capability bootstrap probes are not worker proof.* The worker must repeat the probe inside its own boundary before it may mutate anything remote.

**6. SSH sandbox restrictions.** The governed Codex worker runs with network denied by default. A worker that needed SSH could reach the host through an exactly granted probe but not through ordinary direct SSH ("socket Operation not permitted"). The resolution was a destination-scoped native proxy owned by one worker process and revoked with its bounded shutdown; the worker's own seven read-only probes then succeeded while the default remained denied.

**7. Browser execution restrictions.** One product line needs a real browser inside the worker to prove a web application works. The sandbox had to admit a headless browser probe while continuing to deny unrelated network, including the cloud metadata address. That shipped as a dedicated routing release (v2.7.1) with auth and browser preflights passing.

**8. Evidence-size limits.** The Router reads bounded files (4 MiB). A frozen documentation contract was 4,930,820 bytes; a product observation was about 5.5 MB; a package review needed a 5,084,107-byte source diff. Each one was refused at a different point. (A crew file with the full evidence inputs embedded is about 16 MB; those are written and sealed successfully, "fine for now, not free".) v2.7.3 introduced content-addressed sealed evidence in 2 MiB chunks (logical files up to 128 MiB, bundles up to 256 MiB). This class produced the deadlock described in the next section.

**9. Cross-role evidence access.** The independent reviewer could not read the author's raw session output outside its workspace, and when it could, it could also write. v2.6.0 gave the reviewer a read-only, kernel-isolated view of sealed evidence: write, chmod and unlink fail with EROFS, unrelated paths and credentials are invisible, and a hardlink-alias mutation the independent static reviewer discovered was closed and re-verified by repeating the exploit.

**10. Reviewer-role confusion.** A reviewer "exited 0 after authoring REPAIR.md with no verdict". A review role inherited the implementation's SSH arguments through legacy grants. A manager misread the accountable parent claim as a conflict and tried to launch a nested supervisor. Lesson: *roles are projections of authority, not labels in a prompt.* The reviewer now receives read-only tools and no worker grants; the author may not invoke the manager's tool; a repair that reaches for the wrong tool is recorded as a start that did not advance (that exact thing happened once in the accepted run).

**11. Invalid verdict parsing.** A reviewer that timed out was classified as a rejection because the captured tool source contained the quoted word REJECT. A completed-review guard then refused a legitimate re-review because it believed a review had completed. Lesson: *a verdict is a typed outcome, not a substring.* Only `judge_pass` and `judge_fail` complete a review; timeout, role violation and invalid output retain evidence and charge the start but permit the remaining authorized recovery.

**12. Concurrent publication ownership.** Two sessions worked on the installed system at once. One installed a reviewed release while another's invocation was open; the open one correctly refused with a publication-hash mismatch. A stale claim on the shared publication path blocked a release until it was reconciled through the claim mechanism with its history preserved. Lesson: *installation is a shared resource with an owner; the pin gate that stopped the second session was right.*

**13. Reviewer fallback eligibility.** Our only evidence-capable reviewer harness is one model family. When a worker from that family authored, the rule that author and reviewer may not share a family left "No eligible independent same-role fallback", and two otherwise successful missions were refused at review. v2.8.0 moved crew planning before the first dispatch: author, reviewer and fallback are chosen together, and an author with no independent reviewer is excluded up front.

**14. Missing routing attestation.** Two defects. The host wrote the pending record with mode 0600 while the attestation check required no write bits, so v2.8.0 could never launch an author (the tests had built the fixture at 0444, so it never surfaced). And the Router stamped `attempt_id: None` into every route receipt because the id was resolved only when a privileged executor was present, so every receipt in the history read `supervisor_route_unattested`, and the stricter v2.8.1 settlement refused a run in which a real author and a real reviewer had both executed. v2.8.2 resolved the id once per run. These were the last two releases.

**15. Incorrect authority checks.** A manager's `diagnose` was refused by the owner after a dispatch refusal; an installed Router file that differed from the accepted publication refused a start; and, instructively, the engineering session's own harness refused to run the reviewed installer, so a human had to run it. Lesson: *authority checks that fire on legitimate paths are findings; authority checks that stop a wrong path are the product.*

**16. Failure to reach ordinary author dispatch.** The underlying pattern behind most of the above. The acceptance workloads were themselves infrastructure fixtures: multi-megabyte evidence contracts and 60 to 120 second windows. Each run died on its own harness before the ordinary path was exercised. In our own words at the time: "governance grew, execution never ran." v2.8.0, with every governance feature in place, had never closed a mission; its only production run exhausted a plan-imposed 120-second checkpoint before any start.

## The breakthrough

By October 7 we had a deadlock. The supervisor's evidence-size limit (class 8) needed fixing. The natural move, in a system that insists that all work goes through supervision, was to assign the repair to `/supervise`. The assignment was refused by the very limit it was meant to fix: `NOVELTY_INVALID: file exceeds bounded snapshot size`. Zero starts. We could keep asking broken machinery to repair itself, or we could stop.

The owner authorized something narrower: "dedicated isolated maintenance outside broken /supervise admission solely [for the] evidence limitation, preserving independent review/publication/ownership/integrity. Supersedes skill normal-path restriction for this repair only." A dedicated maintenance author worked in an isolated copy; a genuinely independent reviewer, on a different vendor, reviewed exact bytes; an installer that only accepts a reviewed manifest published the result; and a fresh ordinary proof was staged *before* installation. That produced v2.7.3 in an afternoon, with a High finding (a hardlink alias that allowed mutation of sealed evidence) caught by the independent reviewer and closed before publication.

Why did this work without abandoning safety? Because nothing about the safety came from `/supervise` supervising itself. The safety came from four properties that the bootstrap path kept: an isolated author who cannot touch the installed bytes; an independent reviewer on exact bytes; an installer that refuses anything unreviewed; and a real proof run before anyone calls it done. The governance deadlock was an artifact of insisting on a particular *mechanism* rather than on those properties. The record notes the boundary explicitly: a governed maintenance recovery path independent of the broken component was demonstrated, with no permanent generic bypass.

The same shape produced v2.8.0 (coordinated crews) that evening. For the last mile the engineering session acted directly as the owner of the repair, under the same four properties, and did one more thing differently: it chose a *real, useful, dependency-free workload* with no secrets and failing tests written first, instead of another infrastructure fixture. The three runs that followed each surfaced a defect that the fixtures had hidden (classes 3, 4, 14), and each was fixed, independently reviewed and installed before the next run.

## The final proof

Run 4 ran on the installed v2.8.2 release through the public entry point on the evening of October 7, 2026, against a frozen baseline of a small Python project (51 tests, six acceptance tests red). What the record shows:

| Step | Evidence |
|---|---|
| Real assignment admitted | Candidate validated and frozen; baseline 45 pass / 6 fail as expected; 900-second workers; checkpoint 6 starts / 3000 s, recorded as an internal economical limit, not a user budget |
| Qualified crew selected | Author GPT-6 Luna (xhigh, Pi, prepaid subscription), reviewer Claude Sonnet 5.5 (high), fallback Sonnet 5.5 (medium); rationale "least expected variable mission cost"; every Claude author excluded for lack of an independent reviewer; several OpenAI routes excluded for lack of a qualified benchmark or native proof |
| Luna authored | Start 1, 63.392 s: new `scan_gate.py`, `scan` subcommand wired; all six acceptance tests green, zero regressions, frozen tests untouched |
| Sonnet independently reviewed | Start 2, 108.044 s, on the sealed read-only view: `judge_fail`, one genuine finding: an early return on the first status-file error hid the second gate's failing status |
| Remediation | Start 3 (GPT-6.1 Sol, 33.988 s) produced no source change and was recorded as not advanced; start 4 (Sol, 58.31 s) removed the early return and aggregated the reasons |
| Final independent acceptance | Start 5, Sonnet 5.5, 127.841 s: `judge_pass` |
| 51 tests passed | Verified again independently after closure on the live source |
| Six manual CLI scenarios passed | Independently executed and verified by the Chief of Staff during the Run 4 verification, against the live accepted source with scratch status files outside the repository: `--help` lists `scan`; both gates ok exits 3 ("scan gate passed; live scanning is not implemented yet"); five refusal combinations (failing auth + failing drift, missing or non-JSON auth file + failing drift, failing auth + missing drift, ok auth + non-JSON drift) exit 2 naming every failing gate or file, with drift refusals quoting the recommended action. The sanitized transcript is summarized in the public repository's example |
| Routing attestation verified | Every route file names the publication hash; the final attestation's attempt id is present in both the receipt and the attempt record; the compact-JSON hash of the route file matches its seal |
| Five starts accounted | 63.392 + 108.044 + 33.988 + 58.31 + 127.841 = 391.575 s, equal to `closure.cumulative_seconds` |
| Durable closure | `STATE.jsonl` holds eleven records (five start/finish pairs and `outcome_closed`); `state.json` reads ACCEPTED |

**The exit-143 lifecycle.** The transport receipt records `manager_returncode: 143`. That is the transport, not a crash: once the state is ACCEPTED, acknowledged and verified as current, the transport sends SIGTERM to the idle manager rather than letting it sit until the host window expires. The receipt's `terminal_readback: false` is a pre-run flag that is false on any fresh run. Neither affects closure, which is established by the owner's records, not by the manager's exit code. We mention it because it is exactly the kind of detail that, unexplained, makes a reviewer distrust a result.

## Quantifying the journey

We distinguish measured values (read from records), reported estimates (figures our tooling computes from list prices), incomplete accounting (totals we cannot sum from the records we have) and actual subscription expenditure (which is unknown unless recorded).

| Quantity | Value | Basis |
|---|---|---|
| Calendar time | September 30 (first rollout accepted) to October 7, 2026 (v2.8.2 accepted); the installed-release line ran from October 5, 20:54 to October 7, 20:01 local time | Measured from decisions and release directories |
| Installed releases | 18 (v2 through v2.8.2, including two withdrawn: v2.5.0 and v2.5.1) | Measured (release directories) |
| Invocations in the local store | 48 with a state record; 6 closed ACCEPTED, including Run 4 | Measured on October 7 |
| Significant failure classes | 16 | Counted from the record; the sixteen above |
| Worker and reviewer starts, selected missions | Evidence handoff: 13 starts / 1,865 s; worker capability: 12 starts / 1,668 s, three repair rounds; bootstrap proofs: 4 author starts / 648 s; routing: 7 starts; final four runs: 0, 0, 2, 5 starts | Measured per mission; **no overall total** (incomplete accounting) |
| Production attempt ledger | 2,234 attempts; capable authors complete ordinary work in 110 to 620 s median | Measured from the Router ledger when the 300 s ceiling was diagnosed |
| Run 4 model usage | Author: 32,942 in / 4,370 out / 118,784 cached tokens; repairs: 220,432 and 440,054 input with 1,155 and 1,876 output; reviews: 10,707 and 11,877 output with 523,311 and 1,037,805 cached input | Measured (provider-reported) |
| Run 4 list-equivalent cost | $0.282 for the three OpenAI-subscription starts; the two Anthropic-subscription reviews report cost basis *unavailable*; manager usage not included | Reported estimate from list prices; marginal cost on prepaid subscriptions is $0 |
| Runs 2 and 3 list-equivalent cost | Manager $0.23 and $0.30; run 3 author $0.008, reviewer $0.43 | Reported estimate |
| Actual subscription expenditure | Unknown. Our usage mapping records cost basis *unavailable* for subscription channels; we do not synthesize spend | Not recorded |
| Tests and acceptance milestones | Run 4: 51/51; v2.8.2 broad regression 427 passed / 7 failed with the same seven known ids as the accepted baseline; targeted Router regression 18 passed; two installer review rounds and one Router delta review, all ending ACCEPT 0 High / 0 Medium | Measured |

## Limitations

The final independent verification confirmed: 51 of 51 tests on the live source; six manual CLI scenarios executed by hand against the live accepted source with the expected exit codes and messages observed; source and test hashes against the pre-run baseline (only `cli.py` modified, `scan_gate.py` added, tests byte-identical); the attestation binding (the attempt id present and the route file's seal matching its compact-sorted-JSON hash); five starts summing to the closure total; the eleven-record state chain; and the exit-143 lifecycle. It did **not** recompute the aggregate source byte hash or the review-handoff seal chunks, and the reviewer did not run the evidence verifier script itself.

That is the difference between *accepted operational closure* and *exhaustive security assurance*. Operational closure means the transaction's own records are internally consistent, independently sampled, and the delivered bytes do what the assignment required. Exhaustive assurance would mean every seal and every hash in the chain was recomputed from raw bytes by a party with no stake in the result. We have the first. We claim only the first. v2.8.2 is an accepted production baseline on one real workload; it is not proof that every workload or deployment environment is production-ready. Reviewer fallback and worker-timeout recovery are covered by regression tests and were not exercised live in Run 4.

## Lessons learned

**Bootstrap recovery.** A system that must supervise its own repair will eventually deadlock on the defect it is trying to fix. Keep a governed maintenance path that is independent of the broken component and holds the same four properties: isolated author, independent reviewer on exact bytes, reviewed-only installer, real proof before "done". Make it narrow and explicitly authorized, not a standing bypass.

**Avoiding governance deadlocks.** Insist on properties, not mechanisms. Every time we insisted that "all work goes through `/supervise`" we got either a deadlock or an infrastructure fixture pretending to be a workload. "Governance grew, execution never ran" is the failure to watch for.

**Designing for useful completion.** Test the supervisor on real, dependency-free, useful work with failing tests written first. Fixtures hide exactly the defects that matter: hidden ceilings, unusable managers, unattested receipts. Three real runs found three real defects that forty-odd fixture runs had not.

**Separating worker and reviewer authority.** The reviewer gets a read-only, isolated view and no worker grants; the author cannot call the manager's tools; a verdict is a typed outcome. Every one of classes 9, 10, 11 and 13 was a leak across that boundary.

**Evidence provenance.** Attest before execution, verify after, pin the publication hash into every route record, and refuse settlement when the ids do not match. The refusal that blocked run 3 was painful and correct; it is what made run 4's acceptance mean something.

**Cost-aware crew selection.** Plan the whole crew before the first dispatch, from qualified benchmarks, production completion statistics and marginal price, with the independence rule applied up front. Record the exclusions and their reasons. Be honest that a zero-marginal route can win a tiny task even when its median runtime is long; that is a policy question, not a bug to hide.

**Preserving safety without preventing progress.** Time limits that silently clamp are not safety; they are hidden failure. Make the limit the operator chose actually work, make its exhaustion visible, and never let a limit be removed to make a test pass.

**Measuring accepted outcomes rather than agent activity.** Starts, tokens and green test runs are activity. ACCEPTED closures with verified attestation are outcomes. For most of the week the first number grew and the second stayed at a handful. The only metric that told the truth was the one we could not fake.

---

The public home of `/supervise` is [github.com/leebase/supervise](https://github.com/leebase/supervise) (documentation, examples and this case study, Apache License 2.0). The detailed technical evidence behind this narrative is retained privately. Start with the [supervision overview](overview.md) for the audience map, the [owner guide](owner-operator-guide.md) to operate the system, or the [executive overview](executive-overview.md) for the one-page version.

---

*Production copy of this page: [https://nginx.leebasehome.com/docs/pages/supervision-case-study.html](https://nginx.leebasehome.com/docs/pages/supervision-case-study.html). Part of the [`/supervise` public documentation](../README.md), Apache License 2.0.*
