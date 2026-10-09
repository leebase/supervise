# Known limitations

Stated as of v2.8.2, October 7, 2026.

## Scope of the accepted baseline

- v2.8.2 is an accepted production baseline on **one real workload**: a dependency-free Python project with an ordinary `unittest` suite. It is not proof that every workload, language, test framework, team or deployment environment is production-ready.
- The evidence backend supports ordinary Python `unittest` and `pytest` with JUnit output. It is not a universal adapter for every language or harness.
- Reviewer fallback and worker-timeout recovery are covered by the regression suite (five failure shapes); they were **not** exercised live in the acceptance run.
- No customer environment has been evaluated. There are no ROI figures, testimonials or cost-savings claims because none exist.

## Distribution

- The implementation is not distributed here. It depends on a private staffing router, a specific coding-agent harness and extension, and operator-local paths. See [installation and configuration](install-and-configuration.md) for exactly what a source release would need.
- The Claude Code and OpenCode `/supervise` commands on the originating estate are an older router shim, not the v2.8.2 transport.

## Staffing

- A route with zero marginal price (prepaid subscription) can win a tiny task even when its production median runtime is long; the planner does not yet weigh the admitted worker bound against a route's median runtime. This is a policy question, not a hidden defect.
- Subscription channels report cost basis *unavailable*; list-equivalent figures are estimates from public prices; actual subscription expenditure is unknown unless separately recorded.
- Only one harness family currently provides an evidence-capable independent reviewer, which constrains which families may author.

## Evidence and verification

- Each dispatch writes a large crew file (about 16 MB, full evidence inputs embedded) and seals it; acceptable today, not free.
- The Router reads bounded files (4 MiB); oversized frozen contracts are refused at admission. Chunked sealed evidence (since v2.7.3) covers the review handoff, not oversized admission inputs.
- The independent verification of the acceptance run did not recompute the aggregate source byte hash or the seal chunks, and the reviewer did not run the verifier script. See the [security model](security-model.md).

## Platform

- Python 3.12 is the tested interpreter; Python 3.10 coverage is compile-plus-pure-tests only.
- The manager depends on the coding-agent harness supporting the configured model; an unsupported model name makes every manager turn fail (now detected within two turns, but still a configuration dependency).
- Browser-dependent proofs require a headless browser inside the governed sandbox and are specific to one product line.

---

*Part of the [`/supervise` public documentation](../../README.md), Apache License 2.0.*
