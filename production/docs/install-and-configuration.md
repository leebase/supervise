# Installation and configuration

This page is deliberately plain about what is installable today.

## What you can install from this repository

Nothing executable. This repository contains documentation, a case study, a security model, limitations and a sanitized example. The v2.8.2 implementation is **not** included.

## Why

Before publication the release was audited for redistribution. Every controller file imports a private staffing router; the coding-agent extension hardcodes operator-local repository and journal paths; the migration and controller modules hardcode absolute paths to the router and to prior releases; the browser probes depend on a private web-application project and on third-party packages whose licensing was not audited; and the extension depends on a specific coding-agent harness. The code therefore cannot operate independently of the operator's private infrastructure. The owner's instruction for that case was to publish documentation, the sanitized case study and safe examples first, and to say so plainly rather than present an incomplete package as installable. That is what this repository does.

## What exists on an installed estate (for context)

On the estate where v2.8.2 is accepted, the installation consists of:

- an immutable release directory `$HOME/.local/lib/supervise/releases/20261007-v2.8.2-attested-receipt-candidate/` holding 14 pinned files plus `publication.json` (15 in all): the public transport `command.py`, the Python controller (`ordinary_supervise.py`, `evidence.py`, `budget.py`, `migration.py`), the coding-agent extension (`supervise.ts`, `ordinary.ts`, `native.ts`, `owner.ts`), a driver and test runner, three TableApp probes, and the publication manifest (SHA-256 `2e5845bd49fcd2fffa1eab96a04b64b39e0fad868e8387dcd55e5d268a26e511`);
- a staffing router whose nine supervision files, seventeen dependency modules and attempt-record schema are hash-pinned by that publication;
- two extension pointers (global and per-project) that export the release's `supervise.ts` into the coding-agent harness, and skill files for the harnesses that invoke `command.py`;
- the harness itself (OMP 18.4.x, Node 22), Python 3.12 (3.10 compile-only coverage), and the native CLIs (Claude Code, Codex, Pi, OpenCode) with the operator's own subscriptions;
- a per-project `.omp/config.yml` when the manager model must be pinned (the acceptance run pinned an OpenAI model at high effort).

Configuration surfaces an operator uses: the PLAN (per assignment), the project's `.omp/config.yml` (manager model), the global coding-agent model configuration, and the Router catalog, benchmark evidence and availability snapshots (private).

## What a source release would need

1. Replace every hardcoded operator path (`native.ts` claim and journal roots, `migration.py` router and prior-release paths, `ordinary_supervise.py` retained-release path) with configuration.
2. Publish, or provide a documented interface to, the staffing router the controller imports (`lee_llm_router.staffing.*`, `lee_llm_router.doctor`), including its catalog, eligibility, dispatch and attestation modules, or stub them behind a documented adapter.
3. Separate the TableApp browser probes (Playwright, Chromium, a Snowflake connector) into an optional component with their own licensing review.
4. Verify the license compatibility of the coding-agent harness dependency (`@oh-my-pi/pi-coding-agent`) and any other third-party package the extension imports, and add the required notices.
5. Package a reviewed-only installer and the rollback procedure for a foreign host, with the identity checks in the [owner guide](owner-operator-guide.md) runnable from a fresh checkout.
6. Run the acceptance proof (a real bounded assignment through the public entry, closed ACCEPTED) on a host with no access to the author's estate, and publish that run as the example.

Until those are done, "installable" would be a false claim.

## What you can do with this repository today

- Understand the model: admission, crew, independent review, bounded repair, attestation, durable closure. The [teammate guide](teammate-guide.md) and [architecture](architecture.md) are written for that.
- Reuse the PLAN discipline and the acceptance-versus-activity distinction in your own agent workflows; the [case study](case-study.md) lists the sixteen failure classes you are likely to meet.
- Evaluate whether a pilot on an installed estate fits a responsibility you have; the [client overview](client-overview.md) says what has and has not been demonstrated.

---

*Part of the [`/supervise` public documentation](../../README.md), Apache License 2.0. Production documentation: https://nginx.leebasehome.com/docs/pages/supervision-owner-guide.html*
