# Known limitations

This document lists the known limitations of the public portable build of `/supervise` (v0.x, pre-release candidate); production v2.8.2 is Lee Harrington's private deployment (Router staffing, benchmarks, multi-account routing, capability probes), it is frozen, it is not this build, and it is not runnable outside its owner's estate.

## Test evidence

- Independent source review reduces the risk of fabricated test results, but it does not independently verify that those tests actually ran as claimed.
- Only Python tests are supported: `unittest`, or `pytest` with JUnit output. Files named on the packet's `Owned paths:` line must have one of the extensions `.py .ts .tsx .js .sh .sql .md .json .yaml .yml .toml`, and owned paths must be regular files by the time the worker finishes.
- The reviewer sees the owned source files, the worker transcript, the controller's test results and the plan. It does not see the rest of the project.

## Platform

- Linux only. Isolation uses bubblewrap and unprivileged user namespaces; on Ubuntu 23.10 and later this needs an AppArmor change (see the install guide).
- Python 3.12 or newer. The Python that runs supervise must have a path starting with `/usr/` or `/bin/`; otherwise the trusted verifier is refused and no review can start.
- Supported harnesses: Codex CLI and Claude Code as workers and reviewers; oh-my-pi (`omp`) as manager. Other harnesses need a new driver; backend plugins are trusted code and the backend interface is not OS isolation.
- The only run on record is one end-to-end run on one Ubuntu 24.04 VM with Claude Code 2.1.294, Codex CLI 0.161.0 and oh-my-pi 18.8.5. Other versions, distributions and architectures have not been tried.
- Tests exercise `supervise run` (and `runner.py`) only against a scripted stand-in for `omp`; no test exercises `supervise/omp/supervise.ts`. The real manager path was exercised by the recorded run only.

## Independence and review

- Independence needs at least two model families. With exactly two, no fallback reviewer exists (decision D-4: fallback is optional and defaults off), so a reviewer platform failure ends the run without acceptance.
- `require_review_fallback = true` needs at least three families.
- A family is a label you write in `backend.toml`. The code compares labels; it does not check that they reflect real model lineage.
- One reviewer runs per review. A fallback is used only if a later `review` intent is sent after the first reviewer failed.
- At most two repair rounds.

## Sandboxes

- The connected reviewer sandbox keeps network access (the reviewer harness must reach its provider) and has the reviewer harness's own config directory mounted writable (its login lives there). The trusted verifier has no network. Everything inside the harness's runtime and config directories is visible to the reviewer; the controller refuses those mounts when they overlap the project, the invocation directory or the state root.
- supervise does not sandbox the worker, and reviewer isolation does not extend to anything the worker runs. A Claude Code worker's granted test command executes worker-written code as your user with no sandbox; the Claude driver passes no network or filesystem restriction. This repository does not test either harness's sandbox, and the Codex CLI has no command allowlist.
- The worker's sandbox boundary is the whole project, not the owned paths. The project-confinement guard detects changes to other project files after the worker exits; it does not prevent them, ignores the top-level `.git` and `__pycache__` directories with their `.pyc` files, and does not inspect anything outside the project.
- Admission refuses a project containing harness configuration (`.codex`, `AGENTS.override.md`, `.mcp.json`, `.claude/settings*.json`) for every configured route, even a route that would not be used.

## Evidence and verification

- The credential scanner refuses only a few credential file names and `BEGIN ... PRIVATE KEY` lines. API keys, tokens and passwords in evidence are not detected.
- Worker transcripts and the manager trace are stored unredacted in the state root.
- Seals, receipts and journal hashes are unkeyed SHA-256. `verify` detects inconsistency in recorded files; it cannot show who wrote them, and a same-user process that rewrote every file consistently would not be caught. It does not re-run tests or harnesses.
- The controller records the installed publication digest but does not check that the running code matches the installed release. `installer.verify_installation` and `review_view.check_isolation` exist and are tested but nothing in the run path calls them.
- The pointer file written by `install --pointer` is not read by supervise. `uninstall` leaves releases, the installation record and all state in place.

## Operation

- A refused `review` (for example a mount-overlap refusal) can be retried after the cause is fixed; no start is reserved for a refused review.

- A repair start that ends in a platform failure strands the invocation: `repair`, `author` and `review` are then all refused. It fails closed (nothing is accepted); start over with a different plan path.

- `supervise run` ends as soon as `state.json` shows `PRECHECK_FAILED`, `CHECKPOINT_EXHAUSTED` or `RECONCILE_REQUIRED`. The invocation directory is keyed by the project path and plan path, so repeating a run of the same pair reads the same state. This is from reading `runner.py`, not from a test.
- The manager is a model. It can fail to follow its instructions; the run then ends with a `MANAGER_*` status (two consecutive failed turns, a stall, or the `--max-seconds` window of at most 7200 s).
- `doctor` checks configuration more loosely than the real parser, so a file can pass `doctor` and fail at admission. Before the first run its overall verdict can be `UNKNOWN` on a correctly configured machine, because the state root is created on first use; without `--probe-manager` it is always `UNKNOWN` or `FAIL`.
- The Codex driver always records the served model as `unknown`. Costs are `unknown` unless the harness reports one (Claude Code) or both a price and token usage are available. `effort` is not passed to Claude Code.

## Scope

- This build has no benchmarks, production statistics, multi-account routing or capability probes. Requests for `ssh`, `browser`, `external-service` and `protected-configuration` are refused as unsupported.
- `supervise/conformance.py` maps 28 scenarios ported from v2.8.2; 8 (seven manager-fault scenarios and one budget scenario) are marked `not_implemented` there.
- Production v2.8.2 remains frozen. Nothing in this document is a claim about it.
