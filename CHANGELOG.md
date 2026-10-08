# Changelog

Release names are the installed release directory names. Dates are local time (US Central) of the release directory; publication hashes are the SHA-256 of the release's `publication.json`.

## v2.8.2 — 2026-10-07 20:01 — `20261007-v2.8.2-attested-receipt-candidate`

Publication `2e5845bd49fcd2fffa1eab96a04b64b39e0fad868e8387dcd55e5d268a26e511`. Accepted production baseline.

- Router: route receipts carry the real `attempt_id` for every run (one file changed), so routing attestation verifies and ordinary closure settles. Previously every receipt read `supervisor_route_unattested` and settlement refused after a real author and reviewer had executed.
- Regression: 427 passed / 7 failed with the same seven known ids as the accepted baseline; targeted Router regression 18 passed. Independent review ACCEPT 0 High / 0 Medium, plus a delta review of the post-review publication metadata fix (ACCEPT 0/0/0).
- Acceptance run: five starts, 391.575 s, transaction closed ACCEPTED (see the case study).

## v2.8.1 — 2026-10-07 18:24 — `20261007-v2.8.1-production-dispatch-candidate`

Publication `c7ef937cdc1b1d6c349cccf4ffb3bc439dc45d28ad91b2b324c0a571b3206f73`.

- `pending.json` is protected (mode 0400) on every owner entry before any dispatch; v2.8.0 could never launch an author because the host wrote it 0600 and the attestation check required no write bits.
- Admitted worker timeout is honored, bounded only by the native checkpoint and the retained window; the hidden 300 s worker ceiling present since v2.3.1 is removed.
- Transport (`command.py`) stops after two consecutive manager error turns (`MANAGER_ERROR`) or a stalled manager with the unit open (`MANAGER_STALLED`); receipts gain `manager_fault`.
- Reviewed installer: pointers recorded before replacement; exclusive release directory creation; every pointer restore attempted on failure.

## v2.8.0 — 2026-10-07 17:07 — `20261007-v2.8.0-coordinated-crew-candidate`

- Coordinated crew planning before the first dispatch: author, independent reviewer and fallback chosen together from qualification metadata, production statistics and price; authors with no independent reviewer family are excluded up front.

## v2.7.3 — 2026-10-07 14:30 — `20261007-v2.7.3-bootstrap-evidence-candidate`

- Content-addressed sealed evidence in 2 MiB chunks (logical files up to 128 MiB, bundles up to 256 MiB), after multi-megabyte contracts and observations were refused by the Router's 4 MiB file bound. Produced through an authorized, independently reviewed maintenance path outside the broken admission (see the case study, "The breakthrough"); a High finding (hardlink alias allowing mutation of sealed evidence) was closed before publication.

## v2.7.1 / v2.7.2 — 2026-10-07 10:44 / 11:28

- TableApp routing with auth and browser preflights; frozen legacy producer binding.

## v2.6.0 — 2026-10-06 19:23 — `20261006-v2.6.0-evidence`

- Sealed cross-role evidence handoff: the reviewer receives a read-only, kernel-isolated view of the author's evidence (write, chmod, unlink fail with EROFS; unrelated paths and credentials invisible).

## v2.5.0 / v2.5.1 — 2026-10-06 15:30 / 15:51 — withdrawn

- Worker capability projection and reviewer-role separation; withdrawn the same afternoon (an unconditional import broke the Router's declared Python 3.10 support) and rolled back to v2.4.2.

## v2.4 / v2.4.1 / v2.4.2 — 2026-10-06 12:48 to 14:25

- Correctable candidate admission: discover, validate, real preflight, then canonicalize, hash and freeze; explicit finite test timeouts up to 3600 s honored instead of a hardcoded 30 s clamp. v2.4.2 stops a pre-admission diagnose from corrupting the candidate.

## v2 to v2.3.1 — 2026-10-05 20:54 to 22:23

- First installed-release line of the ordinary supervisor: normal-entry bridge, interpreter resolution, budget and frozen-test repairs, terminal readback. v2.3.1 was the release that produced the first six supervision guides on October 6, 2026.

## Before v2 — 2026-09-30 to 2026-10-05

- `/supervise` was a slash command around a six-command router loop (staff, run, classify-failure, next-action). Accepted for rollout on September 30, 2026; reported stuck the next day. The Claude Code and OpenCode commands still carry this shim as a separate entry.
