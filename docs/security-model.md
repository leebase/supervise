# Security model

This document describes the trust boundaries of the public portable build of `/supervise` (v0.x, pre-release candidate); it differs from production v2.8.2, a private, frozen deployment that cannot run outside its owner's estate, in that these boundaries are implemented by the code in this repository (a TOML route catalog, bubblewrap and the two harness CLIs) rather than by a private router.

It states what the code does. It is not a certification and no external audit has been done.

## Assumptions

Trusted: the supervise code, running as your user; `bwrap`; the harness programs you installed. External: the model providers. Not defended against: a hostile operator, a hostile plan or hostile project tests, or another process running as the same user, which can change any file supervise writes.

## Parties

| Party | May do | Enforced by |
|---|---|---|
| Manager (a model in `omp`) | Read files; submit intents through `supervise_continue`. | The OMP extension limits the active tools to those of `read`, `grep`, `glob`, `find`, `ls` that were active, plus `supervise_continue`, and restores them at `ACCEPTED`, `CHECKPOINT_EXHAUSTED` or `RECONCILE_REQUIRED`. The controller validates every intent. |
| Controller (Python) | Admit, run tests, allocate time, seal evidence, write state. | Ordinary file permissions. (The journal refuses writes in a process that has `SUPERVISE_PARENT_START` set, but nothing in this build sets that variable, so it is not an enforcement boundary.) |
| Worker (Codex CLI or Claude Code) | Change files; run the granted test command. | The harness, plus the confinement guard (section 3). |
| Reviewer (Codex CLI or Claude Code) | Read a sealed view. | Bubblewrap (section 5) and the harness's read-only options. |

## 1. Admission freezing

`admit` checks the plan, owned paths, test command, acceptance selectors, checkpoint and packet header, refuses unsupported capabilities (`ssh`, `browser`, `external-service`, `protected-configuration`), checks the project for harness configuration (section 2), and refuses if any configured author route cannot accept the test command as a literal grant. It then freezes the test inputs, runs a baseline in the test sandbox (section 7), and compares the observed failures with `expected_baseline_failures`.

On success it writes `contract.json` (read-only mode 0400) with the project, test command, interpreter SHA-256, hashes of everything under `tests/` except `__pycache__` paths, the source baseline, frozen test ids, the normalized intent and its digest. It also writes `acceptance.json`, `elapsed-checkpoint.json`, `binding.json` (the contract's digest) and `decision.json`, and `pending.json` holds the plan's SHA-256. `review` refuses, before reserving a start, if the plan file no longer matches the SHA-256 recorded in `pending.json`. A second `admit` with the same intent returns the same result; any other intent is refused ("admission is immutable once frozen"). A refused `admit` writes `PRECHECK_FAILED` at version 0 and removes the freeze files.

Before each allocation the controller compares the five pinned files (`pending`, `acceptance`, `elapsed-checkpoint`, `contract`, `binding`) with the bytes it read at construction, requires them read-only, and checks that none changed after `binding.json`. `close` refuses if `tests/` no longer matches the frozen hashes, and `run` reports `UNVERIFIED` instead of `ACCEPTED` if the plan file's digest changed. These are detection checks: mode bits do not stop a process running as your user.

## 2. Worker harness sandboxing, per driver

supervise does not wrap the worker in bubblewrap. It starts the harness as its own process group with stdin from the packet file, a working directory equal to the project, and an explicit environment of only `HOME`, `PATH` and the harness config variable. A timeout sends SIGTERM to the group and SIGKILL after one second. Sandboxing is whatever the harness provides for the options below.

| | Codex driver | Claude driver |
|---|---|---|
| Options sent | `exec --json --ephemeral --ignore-user-config --ignore-rules -s workspace-write -C <project>`, `approval_policy="never"`, `sandbox_workspace_write.network_access=false` | `-p --output-format stream-json --strict-mcp-config` with an empty MCP config, `--setting-sources ""`, `--disable-slash-commands`, `--permission-mode acceptEdits`, `--allowedTools Bash(<test command>)` |
| Environment | `HOME` and `CODEX_HOME` = the Codex config directory; `PATH` | `HOME` and `CLAUDE_CONFIG_DIR` = the Claude config directory; `PATH` |
| Relied on | Codex's workspace-write sandbox: writes confined to the project, network off | Claude Code's permission system; supervise passes no network or filesystem restriction |
| Project preflight | refuses any `.codex` or `AGENTS.override.md` in the project tree | refuses any `.mcp.json` or `.claude/settings*.json` in the project tree |

The sandbox boundary for the worker is the project directory, not the owned paths. Worker grants are checked for an absolute executable and no `* ? ( )`, newline or NUL, but the Codex CLI has no command allowlist, so for Codex the grant is validated and not enforced. This repository does not test either harness's sandbox.

**Worker-executed code runs outside supervise's sandbox.** The Claude driver grants the worker the frozen test command; running it executes the project's tests and whatever code the worker has written, as your user, with no network or filesystem restriction from supervise. A Codex worker's commands run inside Codex's own workspace-write sandbox. Only the reviewer and the trusted verifier are isolated by supervise's bubblewrap sandbox, and the controller's own test runs are sandboxed (section 7); none of that applies to code the worker runs.

## 3. Project-confinement guard

Before each worker start the controller records an inventory of every project entry outside the owned paths: regular files by mode and SHA-256, symlinks by mode and target text (not followed), directories by mode, other entry types by type. It ignores the project's top-level `.git`, and `__pycache__` directories together with the `.pyc` files in them. After the worker exits (and when reconciling an unfinished worker start) it compares inventories. Any added, removed, retyped or mode-changed entry, or an unreadable inventory, is recorded as `project_confinement_violation` in `decision.json`, after which `review` and `close` are refused. This detects writes inside the project; it does not prevent them and does not look outside the project.

## 4. Sealed evidence

Before a review, the controller seals: `attempt.json`, `source-snapshot.json`, `tests.json`, the worker's `stdout.txt` and `stderr.txt`, and a copy of each owned file under `source/`. The seal is bound to the invocation id, unit id, worker route, role `impl`, attempt id and a digest of the source snapshot.

Files are split into 2 MiB chunks stored under their SHA-256 names (mode 0400); `seal.json` is canonical JSON, its SHA-256 is the seal's identity and directory name, and the directories are read-only. Limits: 128 MiB per file, 256 MiB in total. Verification recomputes everything and fails on a changed binding, size, digest, writable entry, or missing or extra chunk.

**Credential scanner.** Sealing refuses these file names (compared case-insensitively on the last path component): `auth.json`, `.credentials.json`, `credentials.json`, `id_rsa`, `id_ed25519`, and names ending `.pem` or `.key`. It also scans every file's bytes, when sealing and again when verifying, for a line containing `-----BEGIN ` followed on that line by `PRIVATE KEY` (case-insensitive) and refuses to seal if found. It looks for nothing else: API keys, tokens and passwords are not detected.

## 5. Reviewer sandbox

The view `views/<identity>--<reviewer>` is built from the verified seal: files mode 0400, directories 0500, plus `seal.json` and `verify.py` (a copy of `kernel/verifier.py`). It is re-checked for extra, writable, linked or altered entries and for a `verify.py` that differs from the trusted source.

The reviewer harness runs under `bwrap --unshare-user --unshare-pid --die-with-parent --cap-drop ALL --clearenv` with:

- read-only: `/usr`, `/bin`, `/lib`, `/lib64` (those that exist), the view at `/review` (working directory), the directory holding the harness executable (for Codex, the official package root instead, when the executable sits in that root's `bin/`; a root is recognised by `codex-package.json`, or by `codex-path/` and `codex-resources/`), and `/etc/resolv.conf`, `/etc/hosts`, `/etc/nsswitch.conf`, `/etc/ssl` (those that exist);
- writable: the harness's own config directory (`CODEX_HOME` or `CLAUDE_CONFIG_DIR`), mounted under the sandbox home directory `/sandbox/home` as `.codex` or `.claude`; `/tmp` as a tmpfs;
- `/proc` and `/dev`. Your home directory itself, the project and the state root are not mounted; only the paths above are. A mount whose source is `/`, your home directory or one of its ancestors is refused, so a harness installed directly in your home directory cannot be used as a reviewer. Before a review start is reserved, the controller also refuses any mount whose source equals, contains or lies inside the project, the invocation directory or the state root, so `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and harness install directories must be kept apart from your projects and from the state root. It likewise refuses when the project, the invocation directory or the state root lies inside one of the built-in read-only system binds (`/usr`, `/bin`, `/lib`, `/lib64`). Everything inside a permitted mount is visible to the reviewer.

**The network is on**: the sandbox does not unshare the network namespace, because the reviewer harness must reach its provider. **The environment is cleared**: only `HOME` (`/sandbox/home`) is set inside. Mount sources must exist, targets must be absolute and may not be `/`, `/review`, anything under it, or under `/etc`. The Codex reviewer also gets `-s read-only` and options requesting read-only filesystem and no tool network; the Claude reviewer gets `--tools Read,Grep,Glob`. This repository does not test those harness options. Anything inside the sandbox can read the mounted login and use the network.

A verdict counts only if the final non-empty line of the harness's final message ends with a `REVIEW VERDICT: ACCEPT` or `REVIEW VERDICT: REJECT` marker (the last marker on that line counts, and only punctuation or whitespace may follow it), that marker also appears in the captured stdout, the harness exited 0 without a platform error, and the trusted verifier passes again after the review. Otherwise the review is incomplete, not a rejection.

## 6. Trusted verifier

`verify.py` runs in the view before and after the review under the same bubblewrap setup with `--unshare-net` (**network off**), an empty environment, a 30 s limit, and an interpreter that must start with `/usr/` or `/bin/`. It checks that `seal.json` is canonical, every listed file's SHA-256 and size match, and the tree contains nothing else, no symlinks and nothing unexpected. It must print `VERIFIED <identity>`. A probe, `check_isolation`, exists in the code and is exercised by tests; the run path does not call it.

## 7. Controller-run tests

The controller, not the worker, runs the frozen test command for baseline and after each author start, in bubblewrap with user, pid and **network off**, read-only root, the invoking user's home directory masked with a tmpfs, the project bound read-only, and only the run's evidence directory writable. The environment is `PATH`, `LANG`/`LC_ALL` if set, a private empty `HOME`, `TMPDIR` and bytecode settings. A result counts as complete only if the test ids match the frozen ones, the `tests/` hashes, interpreter hash and runner hashes match the contract, the source files did not change during the run, and the run did not time out.

## 8. Attestation receipts

Each start gets a read-only `route-N.json` (mode 0444, created exclusively) naming the invocation, unit, role, route, class key, packet digest, crew file and digest, publication digest, contract digest, decision digest and controller path. The crew file `crew-N-<sha256>.json` (read-only) records the selected author and reviewers and each exclusion with its reason. The finish record carries a receipt that binds the route record's digest, start number, execution and attempt id with a self-hash. The controller verifies it when a start settles; `verify` verifies it again offline. All digests are SHA-256 over canonical JSON with no key, so they show consistency, not authorship.

## 9. Offline verification

`supervise verify <invocation>` works from the files recorded in the invocation directory and runs all ten checks, even after one fails, and reports `PASS` only if all pass:

1. `journal-schema`: every `STATE.jsonl` line is complete, valid, free of duplicate keys and non-finite numbers, and matches its record schema.
2. `starts-accounted`: starts are unique and each is finished or reconciled exactly once; there is at least one.
3. `closure`: exactly one `outcome_closed`, last in the journal, with the right start count and total seconds, equal to `closure.json`.
4. `receipts`: every finish receipt verifies against its route record, start and crew file, and the route's role matches.
5. `crew-files`: each crew file's digest matches its name and schema, and each route record's crew binding matches.
6. `accepted-review`: `accepted-review.json` binds to a finished review start that accepted, with failure class `none` and a passing verifier, by a reviewer in that start's sealed crew.
7. `review-independence`: the accepting reviewer's sealed family differs from every author's sealed family.
8. `review-snapshot`: the accepted source snapshot equals the closure-time snapshot and the source snapshot of the reviewed author start.
9. `sealed-evidence`: every evidence seal referenced by a finish record is re-verified against bindings rebuilt from the journal (chunks, digests, extras, credential scan); at least one must exist.
10. `acceptance-observation`: the accepted author's recorded controller-run test observation is complete, has no missing, unexpected or changed frozen tests, and every acceptance selector matches at least one test, all `PASS`.

Output is `{"verdict", "checks":[{"name","ok","detail"}]}`; exit 0 on `PASS`, else 1. `verify` does not re-run tests, does not re-run the harnesses, and does not check that the running code matches an installed release.

## 10. Credential handling

Logins stay in each harness's own configuration (`~/.codex` or `CODEX_HOME`; `~/.claude` or `CLAUDE_CONFIG_DIR`; `omp`'s own store). No code in this repository reads, copies or writes credential files. Workers are started with the config directory path in their environment and use their login in place. Reviewers get the same directory bind-mounted writable into the sandbox. `omp` is started with a copy of the caller's whole environment plus three variables. `doctor` omits command output, config contents and exception text. Worker stdout and stderr, and the manager trace (`rpc.jsonl`, `stderr.txt`, mode 0600), are stored unredacted in the state root.

## 11. In-process test reports

> Independent source review reduces the risk of fabricated test results, but it does not independently verify that those tests actually ran as claimed.

The test reports are written by code that runs in the same process as the project's code under test (the `unittest` adapter in `controller/unittest_runner.py`, or pytest's JUnit output), so project code could in principle produce a report that does not reflect what ran. The frozen-input and hash checks above do not detect that.
