# Security model

This page describes the trust boundaries of `/supervise` v2.8.2 as designed and as exercised in its acceptance run. It is a statement of design and of what was verified, not a certification.

## Principals

| Principal | Authority | Enforced by |
|---|---|---|
| Operator | Writes the PLAN; owns publication and deployment decisions; provides subscriptions | Human |
| Manager (a model) | Submits typed actions; may read; may not implement, call providers, call the Router, or write control documents | The extension exposes only `supervise_continue` plus read tools to the manager; the controller validates every intent against the admitted contract |
| Owner / controller (code) | The only writer of state; freezes admission; runs tests; allocates time; seals evidence; verifies attestation | Trusted code, hash-pinned by the release publication |
| Router (code) | Crew planning, eligibility, dispatch, attempt records, receipts | Trusted code, hash-pinned; the controller refuses dispatch when live bytes differ from the publication |
| Author (a model in a sandbox) | Implements within declared owned paths; runs the frozen tests; no network by default; no ambient user configuration | Native harness sandbox (workspace-write rooted at the project, network denied); mutable project harness configuration layers refused at launch. Owned paths are a declared scope: the controller registers them, watches them for effective-work evidence and snapshots them (with the frozen `tests/` observed separately); those byte checks do not cover every file in the workspace, so a change elsewhere relies on tests and independent review to be caught. Owned paths are not an operating-system write boundary |
| Reviewer (a model in a sandbox) | Reads a sealed, read-only, kernel-isolated evidence view with `Read`, `Grep`, `Glob` and the trusted verifier; no write, no network, no worker grants | Controller-pinned descriptor; EROFS on write/chmod/unlink; unrelated paths and credentials invisible |

## Properties the design enforces

1. **Immutable admission after validation.** Nothing is frozen until the test argv, selectors, baseline and probes have been executed; once frozen, the contract cannot change for the life of the transaction.
2. **Independence.** Author and reviewer never share a model family; the crew is planned with that rule before the first dispatch; an author with no eligible independent reviewer is excluded.
3. **Evidence provenance.** Every start is reserved before execution and verified after; the attempt record and the route receipt must carry the same attempt id; every route record names the publication hash; sealed evidence is content-addressed and a seal mismatch is a hard stop.
4. **Bounded authority.** Finite host window, finite test timeout, finite checkpoint (starts and seconds), at most two reviewer-directed repair rounds; exhaustion is visible, never silently extended; re-invoking the same PLAN resumes the same transaction and accounting. Renaming the PLAN creates a new invocation and is prohibited by policy; the system does not link renamed plans mechanically.
5. **Fail closed.** Unknown outcomes must be reconciled before anything new runs; a review without a typed verdict is incomplete, not a pass or a fail; a capability that cannot be verified in the worker's own boundary fails admission.
6. **Reviewed-only installation.** Only bytes an independent reviewer saw are installed; the installer backs up what it replaces and records a rollback; the controller refuses to run against a drifted Router.
7. **Declared remote capability.** Remote access (SSH) is admitted only as an exact, non-destructive native probe with batch mode and strict host key checking, routed through a destination-scoped proxy owned by one worker process and revoked with it; reviewers never inherit it.

## What the acceptance run verified

Independent verification of the October 7, 2026 run confirmed: 51 of 51 tests on the live source; only the two owned files changed and the frozen tests byte-identical; the attestation binding (attempt id present in both records, route file seal matching its compact-JSON hash); five starts summing to the closure total; the eleven-record state chain; the transport's exit-143 lifecycle. The crew, exclusions, and read-only reviewer tooling were read directly from the sealed records. Six manual CLI scenarios are reported in the launch brief only; the retained verification note does not record them and no transcript was kept, so that count is reported rather than traceable.

## What it did not verify

The aggregate source byte hash and the review-handoff seal chunks were not recomputed by the independent verifier, and the reviewer did not run the evidence verifier script itself. Reviewer fallback and worker-timeout recovery were exercised by regression tests, not live. No external penetration test, no third-party audit, and no evaluation in a customer environment has been performed. The sandbox properties above were exercised by the project's own regression suites and by the reviewer's confirmation of EROFS on the sealed view; they are not a claim about every host configuration.

## Reporting

If you believe you have found a defect in this model, open an issue describing the observation; please do not include credentials, hostnames or evidence from a private installation.

---

*Part of the [`/supervise` public documentation](../README.md), Apache License 2.0.*
