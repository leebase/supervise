# Supervised execution: start here

**AI agents attempt tasks. Supervised AI employees deliver independently verified outcomes.**

`/supervise` takes one bounded assignment, selects a qualified crew, has a worker implement it, runs the project's real tests, seals the evidence, sends it to an independent reviewer, remediates genuine findings within finite limits, and closes a durable transaction as ACCEPTED only when the exact bytes have passed independent review. A worker can time out or produce an incomplete result without the operator becoming the recovery mechanism.

**Current baseline: `/supervise` v2.8.2, accepted October 7, 2026.**

~~~text
release      20261007-v2.8.2-attested-receipt-candidate
publication  sha256 2e5845bd49fcd2fffa1eab96a04b64b39e0fad868e8387dcd55e5d268a26e511
~~~

Its acceptance run delivered a real feature to a real project through the installed public entry point: one author start, one independent review that found a genuine defect, one effective repair round, a final independent acceptance, 51 passing tests, verified routing attestation, five starts fully accounted, and a transaction closed ACCEPTED. The [case study](case-study.md) tells how it got there, including the failures.

v2.8.2 is an accepted production baseline, not proof that every workload or deployment environment is production-ready. Each guide below says what was demonstrated and what remains to evaluate.

## Three audiences, three entry points

| Your seat | Start here | Then |
|---|---|---|
| **Owner / operator** running the AI employee estate | [Owner and operator guide](owner-operator-guide.md) | [Supervise command how-to](skill-how-to.md), [Supervisor employee how-to](supervisor-employee-how-to.md) |
| **Teammate** joining with no prior knowledge of the architecture | [New teammate guide](teammate-guide.md): concepts in order, an architecture diagram, a first-use walkthrough | [Supervise command how-to](skill-how-to.md) |
| **Client / market** deciding whether this matters to your organization | [Executive overview](executive-overview.md) (one page) | [Client overview](client-overview.md), [case study](case-study.md) |

## Choose your route

| Need | Entry | What persists |
|---|---|---|
| A bounded project task with a PLAN and ordinary tests | The installed `/supervise` skill and command | Original project transaction, attempts, tests, sealed evidence, review, routing attestation and cumulative accounting |
| A continuing responsibility carried by an existing employee | Owner-provisioned Supervisor Pilot assignment | Employee identity, protected admission, Board claim, original transaction, result and separate recovery evidence |

In both routes, implementation and acceptance are distinct. Passing a test is useful machine evidence. A distinct reviewer, on a different model family from the author, must assess the full assignment and the exact candidate before the supervisor can call it accepted. Publication, if needed, follows through a separately authorized owner and publisher path.

## The pattern

~~~text
PLAN admitted (tests frozen, baseline run, limits fixed)
  → Router plans a crew: author + independent reviewer + fallback
  → author implements → real project tests → evidence sealed
  → independent review on the sealed read-only view
  → blocking finding? bounded repair round (at most two) → re-review
  → routing attestation verified → transaction closed ACCEPTED
  → or: BLOCKED / UNACCEPTED with the exact reason and spent accounting
~~~

A worker failure and a reviewer timeout are different observations: the first concerns implementation; the second leaves the candidate unaccepted even when implementation and tests are complete. Both remain on the original transaction until a permitted recovery or an explicit block.

## All supervision pages

- [Owner and operator guide](owner-operator-guide.md): architecture, installed release, crew selection, attestation, sealed evidence, state inspection, diagnosis, upgrade and rollback.
- [New teammate guide](teammate-guide.md): the concepts in order, a diagram, and a complete first-use walkthrough.
- [Client overview](client-overview.md): business value, use cases, demonstrated versus planned.
- [Executive overview](executive-overview.md): a concise page to share with a prospective client.
- [Supervise command how-to](skill-how-to.md): the PLAN, the exact invocation, options, evidence and troubleshooting.
- [Supervisor Pilot how-to](supervisor-employee-how-to.md): protected provisioning, installed assignment CLI, durable recovery and closure.
- [Case study](case-study.md): what it took to move from autonomous agent execution to independently accepted delivery.
- Public repository: [github.com/leebase/supervise](https://github.com/leebase/supervise), documentation, examples and the case study under the Apache License 2.0.

**Baseline date: October 7, 2026.** Technical steps in these pages describe the installed, owner-approved surfaces at that date. They do not create authority to start a task, extend a budget, approve a result, or publish it.

---

*Production copy of this page: [https://nginx.leebasehome.com/docs/pages/supervision-overview.html](https://nginx.leebasehome.com/docs/pages/supervision-overview.html). Part of the [`/supervise` public documentation](../../README.md), Apache License 2.0.*
