# /supervise

**AI agents attempt tasks. Supervised AI employees deliver independently verified outcomes.**

`/supervise` is an AI supervisor for bounded software work. It admits one assignment with its tests frozen, selects a qualified crew (author, independent reviewer, fallback) from benchmarks, production statistics and price, has a worker implement, runs the project's real tests, seals the evidence, sends it to a reviewer on a different model family in a read-only view, repairs genuine findings within a fixed number of rounds, verifies routing attestation, and closes a durable transaction as **ACCEPTED** only when the exact delivered bytes have passed independent review. When the limits run out it returns **BLOCKED** with the reason and the spent accounting, not a hopeful summary.

Current release: **v2.8.2** (`20261007-v2.8.2-attested-receipt-candidate`, publication SHA-256 `2e5845bd49fcd2fffa1eab96a04b64b39e0fad868e8387dcd55e5d268a26e511`), accepted October 7, 2026 after its acceptance run delivered a real feature to a real codebase: author, independent review that found a genuine defect the 51 passing tests had missed, automatic repair, final independent acceptance, five starts fully attested and accounted.

## What this repository is, and is not

This is a **documentation-first** repository: the product documentation, a candid engineering case study, the security model, known limitations, and a sanitized example of the accepted run. It is published under the Apache License 2.0.

It is **not** an installable package. The v2.8.2 implementation depends on private infrastructure: a private staffing router, an operator-specific coding-agent harness and extension, and operator-local paths and state. The source has been audited for redistribution and the audit concluded it cannot run independently of that infrastructure, so it is not included here. [Installation and configuration](docs/install-and-configuration.md) says exactly what exists, what a source release would need, and what you can do with this repository today. Please do not read this repository as a turnkey open-source product.

## Start here

| If you are | Read |
|---|---|
| Deciding whether this matters to your organization | [Executive overview](docs/executive-overview.md), then the [client overview](docs/client-overview.md) |
| An engineer who wants to understand how it works and how it was proven | [Case study: Building an AI Supervisor That Actually Finishes the Job](docs/case-study.md) |
| Joining a team that uses it | [Teammate guide](docs/teammate-guide.md) (concepts, architecture diagram, first-use walkthrough), then the [quickstart](docs/quickstart.md) |
| Operating an installation | [Owner and operator guide](docs/owner-operator-guide.md), [skill how-to](docs/skill-how-to.md), [Supervisor employee how-to](docs/supervisor-employee-how-to.md), [troubleshooting](docs/troubleshooting.md) |
| Evaluating the trust model | [Security model](docs/security-model.md), [known limitations](docs/known-limitations.md) |

## Documentation map

- [Product overview](docs/overview.md)
- [Architecture](docs/architecture.md)
- [Installation and configuration](docs/install-and-configuration.md)
- [Quickstart](docs/quickstart.md)
- [Skill how-to](docs/skill-how-to.md)
- [Supervisor employee how-to](docs/supervisor-employee-how-to.md)
- [Examples](examples/README.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Case study](docs/case-study.md)
- [Security model](docs/security-model.md)
- [Known limitations](docs/known-limitations.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)

The production documentation site is at **https://nginx.leebasehome.com/docs/** (entry: [Supervised execution: start here](https://nginx.leebasehome.com/docs/pages/supervision-overview.html)). Each page in `docs/` links to its production copy; the production pages also carry PDF editions.

## How we proved it works

The acceptance run (October 7, 2026) on the installed release, through the public entry point:

| Start | Role | Route | Seconds | Outcome |
|---|---|---|---|---|
| 1 | author | GPT-6 Luna, xhigh, Pi harness | 63.392 | pass; six acceptance tests green, zero regressions |
| 2 | review | Claude Sonnet 5.5, high, Claude Code | 108.044 | blocking finding (an early return hid a second gate's failure) |
| 3 | repair | GPT-6.1 Sol, high, Codex | 33.988 | no source change; recorded as not advanced |
| 4 | repair | GPT-6.1 Sol, high, Codex | 58.31 | pass |
| 5 | review | Claude Sonnet 5.5, high, Claude Code | 127.841 | accepted |

Total 391.575 s, equal to the closure record; every route record names the publication hash; the final attestation carries its attempt id. Independent verification afterwards confirmed 51/51 tests on the live source, source and test hashes against the baseline, the attestation binding and the accounting (six manual CLI scenarios are reported in the launch brief only; the retained verification note does not record them and no transcript was kept, so that count is reported, not traceable). It did **not** recompute the aggregate source hash or the seal chunks; the [case study](docs/case-study.md) explains the difference between accepted operational closure and exhaustive security assurance.

v2.8.2 is an accepted production baseline. It is not proof that every workload or deployment environment is production-ready.

## License

Copyright 2026 Lee Harrington. Licensed under the [Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE).
