# Supervised AI delivery: executive overview

**AI agents attempt tasks. Supervised AI employees deliver independently verified outcomes.**

## The problem

Coding agents produce work quickly. Left to themselves they also time out, over-report, pass the tests they wrote and miss the ones they did not, and leave a person to decide whether the result is real. That person becomes the recovery mechanism, and the promised leverage disappears.

## What supervised execution does

One bounded assignment is admitted with its tests frozen. A crew is selected from models qualified for that class of work. A worker implements. The real tests run. The evidence is sealed and reviewed by an independent model on a different family, in a read-only view. Genuine findings get a fixed number of repair rounds. The transaction closes as **accepted** only when the exact delivered result has passed independent review, with every attempt and its cost on the record. If the limits run out, the result is **blocked** with the reason, not a hopeful summary.

## What has been proven

On October 7, 2026 the installed system (`/supervise` v2.8.2) delivered a real feature to a real codebase through its public entry point:

- a qualified crew was selected automatically;
- the worker implemented the change and all 51 project tests passed;
- the independent reviewer rejected it anyway, for a genuine defect the tests did not cover;
- the system repaired it without human involvement and passed final independent review;
- five model starts, about 6.5 minutes of cumulative worker-and-reviewer time, fully attested and accounted.

This is an accepted production baseline. It is not proof that every workload or environment is production-ready, and we do not publish ROI figures or customer results we cannot evidence.

## Why it matters

Delegation only pays when you can trust the acceptance decision. Supervised execution makes *who verified this*, *what exactly was verified* and *what it cost* explicit for every assignment, which is what engineering leadership, audit and clients ask for anyway.

## Where it applies

Demonstrated today: software maintenance with an ordinary test suite; documentation production with frozen checks. Planned, same pattern, not yet run: data engineering, document processing, operational automation, enterprise application maintenance, wherever acceptance can be expressed as checks the system can execute.

## Next step

A pilot is one representative responsibility, executable acceptance criteria, explicit limits, and a named person who authorizes release. We measure accepted outcomes, failed attempts, correction effort and human involvement in your environment, and we tell you if the honest answer is to narrow the scope or stop.

Read the [client overview](client-overview.md) for use cases, the [case study](case-study.md) for how we got here, and the [supervision overview](overview.md) for the full documentation map.

---

*Production copy of this page: [https://nginx.leebasehome.com/docs/pages/supervision-executive-overview.html](https://nginx.leebasehome.com/docs/pages/supervision-executive-overview.html). Part of the [`/supervise` public documentation](../README.md), Apache License 2.0.*
