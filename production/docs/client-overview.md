# Supervised execution for business work

**AI agents attempt tasks. Supervised AI employees deliver independently verified outcomes.**

Most organizations adopting AI agents discover the same thing: getting an agent to produce work is easy; getting it to reliably *finish* work, recover from its own failures, verify its result independently and account for what it did is hard. Supervised execution is our answer to the second problem. The one-page version is the [executive overview](executive-overview.md); the engineering story is the [case study](case-study.md).

## What it changes

| Capability | What it means for you |
|---|---|
| **Autonomous work execution** | One bounded assignment runs from admission to closure without a person re-launching or re-explaining it |
| **Qualified model selection** | The crew for each assignment is chosen from models that have passed benchmarks for that class of work and have a track record of completing it |
| **Cost-aware staffing** | Prepaid capacity is used before metered capacity; expected mission cost is part of the selection; unknown cost is reported as unknown, never as zero |
| **Independent quality assurance** | A reviewer on a different model family examines the exact result against the whole assignment, in a read-only evidence view, before anything is called accepted |
| **Automatic failure recovery** | Timeouts and genuine review findings lead to a bounded repair, not to a human restart; the number of repair rounds is fixed in advance |
| **Durable work continuity** | A process or session loss does not erase the assignment, the completed work or the accounting; the same transaction resumes |
| **Auditable evidence** | Frozen tests, baseline comparison, source hashes, attempt records, review verdicts and routing attestation are kept per transaction |
| **Controlled authority** | Workers act inside declared paths and limits; reviewers get no write or network authority; publication and deployment stay with a human-authorized owner |
| **Reduced manual supervision** | The person who assigns the work reads a verdict and an evidence trail, not a diff and a chat log |
| **Clear acceptance criteria** | Acceptance is defined before work starts and judged against the exact delivered bytes |

## Why this matters to organizations adopting AI employees

An AI employee that must be babysat is a cost center with extra steps. The value of delegation comes from being able to assign work, trust the acceptance decision, and inspect the evidence only when something needs a human. Supervised execution makes three things explicit that ordinary agent use leaves implicit: *who verified this*, *what exactly was verified*, and *what it cost*. Those are the questions an engineering manager, an auditor or a client asks anyway.

## Realistic use cases

| Use case | Status |
|---|---|
| **Software maintenance**: a bounded feature or fix in a codebase with an ordinary test suite | **Demonstrated.** The v2.8.2 acceptance run delivered a new command to a small Python project: author, independent review that found a real defect, automatic repair, final acceptance, 51 passing tests. |
| **Documentation production**: writing and revising a set of guides against frozen checks | **Demonstrated on an earlier release** (October 6, 2026): six guides produced by supervised workers with independent review, then published by a separate publisher. |
| **Data engineering**: schema changes, pipeline steps, data quality gates with executable checks | Planned. The pattern fits any work with an executable oracle; no data-engineering workload has been run through v2.8.2. |
| **Document processing**: extraction, reconciliation, structured output with verifiable rules | Planned. Same condition: the acceptance criteria must be expressible as checks the controller can run. |
| **Operational automation**: scripted operational changes with a dry-run and a verification step | Planned. Remote capabilities (for example SSH) are declared as exact, non-destructive probes and verified before admission; this path exists but is not part of the accepted baseline. |
| **Enterprise application maintenance**: bounded changes in larger codebases with longer test suites | Planned. Longer test runs and larger evidence are supported by explicit finite bounds; they have not been exercised at enterprise scale. |

## What has and has not been demonstrated

Demonstrated, on the installed system, with independent verification: a real assignment admitted; a qualified crew selected automatically; a worker implementing; an independent reviewer finding a genuine defect the tests missed; automatic remediation; final independent acceptance; five starts fully accounted; a transaction closed ACCEPTED. The release that did this, v2.8.2, is an accepted production baseline, not proof that every workload or deployment environment is production-ready.

Not demonstrated: that every workload, language, test framework, team or deployment environment is production-ready. Reviewer fallback and timeout recovery are covered by regression tests, not by a live incident in the accepted run. No customer environment has been evaluated. There are no ROI figures, customer testimonials or cost-savings claims on this page because none exist yet; a number we could not evidence would be worth less than the evidence we have.

## How an evaluation would work

A useful pilot names one representative responsibility, the materials it may use, the acceptance criteria as executable checks, the time and resource limits, and who may authorize release. We measure accepted outcomes, failed attempts, correction effort and human involvement in your environment. The right next step may be to narrow the task, or to stop.

Start with the [executive overview](executive-overview.md), the [supervision overview](overview.md) for the full map, then the [client evaluation path](https://nginx.leebasehome.com/docs/pages/prospective-client-home.html) to frame a concrete work item.

---

*Production copy of this page: [https://nginx.leebasehome.com/docs/pages/supervision-client-overview.html](https://nginx.leebasehome.com/docs/pages/supervision-client-overview.html). Part of the [`/supervise` public documentation](../../README.md), Apache License 2.0.*
