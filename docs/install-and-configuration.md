# Installation and configuration

This document covers the public portable build of `/supervise` (v0.x, pre-release candidate); it differs from production v2.8.2, a private, frozen deployment that cannot run outside its owner's estate, in that everything below can be done from this repository on an ordinary Linux machine.

## 1. Prerequisites

**Platform.** Linux only. Python 3.12 or newer (`doctor` fails below 3.12; the installed release records `python_requires: ">=3.12"`). Bubblewrap (`bwrap`) with unprivileged user namespaces.

**Interpreter location.** The Python that runs supervise must have a path beginning with `/usr/` or `/bin/` (for example the system `python3`). The review step runs the trusted verifier with `sys.executable` inside the sandbox, and the sandbox refuses any other prefix; a review then cannot start. An interpreter whose path starts anywhere else (for example a user-local Python or a virtualenv under your home directory) does not qualify; the check looks only at the path prefix.

**Packages used in the recorded run** (Ubuntu 24.04): `bubblewrap git curl ca-certificates python3`. The code imports only the Python standard library.

**Ubuntu AppArmor note.** On Ubuntu 23.10 and later, AppArmor restricts unprivileged user namespaces, so `bwrap` fails until it is allowed. The recorded run added this profile at `/etc/apparmor.d/bwrap` and reloaded it:

```
abi <abi/4.0>,
include <tunables/global>
/usr/bin/bwrap flags=(unconfined) {
  userns,
}
```

```bash
sudo apparmor_parser -r /etc/apparmor.d/bwrap
```

This profile leaves `bwrap` unconfined by AppArmor. It is a system policy change that you decide on; `supervise doctor` tests whether `bwrap` can create the namespaces it needs.

**Harnesses.** Install each yourself, with the commands used in the recorded run:

| Harness | Role | Install | Interactive login |
|---|---|---|---|
| Claude Code 2.1.294 | worker/reviewer | `curl -fsSL https://claude.ai/install.sh \| bash` | `claude auth login --claudeai` |
| Codex CLI 0.161.0 | worker/reviewer | the official `codex-package-x86_64-unknown-linux-musl.tar.gz` release from github.com/openai/codex; extract it and put its contents on `PATH` together (the bare `codex` binary alone lacks its `codex-code-mode-host` companion) | `codex login --device-auth` |
| oh-my-pi 18.8.5 (`omp`) | manager | `curl -fsSL https://omp.sh/install \| sh -s -- --binary` | `omp login` (choose OpenAI Codex) |

Log in yourself, inside the machine that will run supervise. `omp` keeps its own credential store and does not reuse the Codex CLI login. supervise never copies logins (see [security-model.md](security-model.md)). Those versions are the ones in the recorded run; no other versions have been tested.

## 2. Installing supervise

```bash
git clone <repository-url> ~/candidate
PYTHONPATH=~/candidate python3 -m supervise install --source ~/candidate
```

`install` requires `--source` to contain a `supervise/` package. It copies every file under `supervise/` (skipping `__pycache__` and `.pyc`), checks each copied file's SHA-256, writes `publication.json` (release name, version `"1"`, state `installed`, per-file hashes, predecessor, `python_requires`), makes the release directory read-only, and writes `INSTALLATION.json` in the data root. Only the package is installed, not `tests/` or `docs/`.

It prints JSON: `release`, `publication_sha256`, `pointer_path`, `pointer_backup`, `path` (the release directory, `<data root>/releases/<name>`) and `verdict: PASS`. If a step fails it attempts to remove the new release and restore the previous record and pointer.

**PYTHONPATH.** supervise is not installed into any Python environment. Run it from a release by pointing `PYTHONPATH` at the release directory:

```bash
export PYTHONPATH=<release path>
python3 -m supervise doctor --probe-manager --model <omp model>
```

`supervise run` prepends its own package root to `PYTHONPATH` for the manager and its tool calls, and passes its own interpreter as `SUPERVISE_PYTHON`. The controller-driven intents (`author`, `repair`, `review`, `reconcile`, `diagnose`, `close`) read `INSTALLATION.json` in the data root and record its `publication_sha256` in each route record; they fail without it. A `supervise run` therefore needs an installation in the data root that it will use.

**Pointer file (optional).** `--pointer FILE` writes `release = <absolute release path>` into FILE and saves FILE's previous content. Nothing in supervise reads that file; it is for your own tooling. `--release NAME` sets the release name (a single path component; the default is a UTC timestamp plus 12 hex characters). `install` refuses an existing release name, refuses a data root inside the source tree, and, when the existing pointer file names a release path of the form `…/supervise/releases/<name>` under a different data root, refuses to replace it; other pointer contents are not recognised.

## 3. backend.toml

The route catalog is read from `$SUPERVISE_CONFIG`, else `$XDG_CONFIG_HOME/supervise/backend.toml`, else `~/.config/supervise/backend.toml`. The parser rejects unknown keys at every level. Only `[[route]]` and `[crew]` are allowed at the top level.

**`[[route]]`** (one or more; each is a table):

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | non-empty string | yes | Unique route id, referenced by `[crew]`. |
| `harness` | string: `"codex"` or `"claude"` | yes | Which driver launches it. |
| `vendor` | non-empty string | yes | Recorded in crew files. Not used to choose routes. |
| `model` | non-empty string | yes | Passed to the harness (`-m` for Codex, `--model` for Claude Code). |
| `family` | non-empty string | yes | Independence key. An author and its reviewer must have different families. |
| `roles` | non-empty array; each `"impl"` or `"review"` | yes | What the route may do. |
| `effort` | string | no | Codex: sent as `model_reasoning_effort` (default `"medium"`). Claude: accepted but not passed to the Claude Code command line. |
| `price` | table | no | Without it, price is `"unknown"`. |

**`price`** has exactly two required fields, `input_per_mtok` and `output_per_mtok`: numbers (integer or float, not boolean), finite and at least 0. The code multiplies token counts by them and divides by 1,000,000 to record `cost_usd` with basis `list_price_estimate`. Claude Code's own reported cost is used instead when present (`harness_reported`); with neither, cost is `"unknown"`.

**`[crew]`** (required):

| Field | Type | Required | Meaning |
|---|---|---|---|
| `authors` | array of route ids | yes | Preference order. Each route must have role `impl`. |
| `reviewers` | array of route ids | yes | Preference order. Each route must have role `review`. |
| `require_review_fallback` | boolean | no, default `false` | If `true`, authoring is refused unless at least two eligible reviewers of different families remain, which needs at least three families. |

The author is the first `authors` entry not already failed. Reviewers are the `reviewers` entries that did not fail and whose family differs from the author's and from every earlier author's. One reviewer is used per review; a later `review` intent uses the next eligible one after a failure.

The configuration used in the recorded run had this shape (model ids left as placeholders; use ids that each harness accepts):

```toml
[[route]]
id = "codex-gpt"
harness = "codex"
vendor = "openai"
model = "<model id for codex -m>"
family = "openai-gpt"
roles = ["impl", "review"]
effort = "medium"

[[route]]
id = "claude-sonnet"
harness = "claude"
vendor = "anthropic"
model = "<model id for claude --model>"
family = "anthropic-claude"
roles = ["review", "impl"]

[crew]
authors = ["codex-gpt", "claude-sonnet"]
reviewers = ["claude-sonnet", "codex-gpt"]
require_review_fallback = false
```

`doctor` reads this file more loosely than the real parser. A file can pass `doctor` and still be refused at admission.

## 4. Environment variables

| Variable | Read by | Effect |
|---|---|---|
| `SUPERVISE_STATE` | `begin`, `run`, `doctor` | State root. Must be an absolute path for `begin`/`run`. Else `$XDG_STATE_HOME/supervise`, else `~/.local/state/supervise`. |
| `SUPERVISE_DATA` | `install`, `uninstall`, `intent` (all but `admit`), `doctor` | Data root (releases, `INSTALLATION.json`). Else `$XDG_DATA_HOME/supervise`, else `~/.local/share/supervise`. |
| `SUPERVISE_CONFIG` | `intent`, `doctor` | Path of `backend.toml`. Else `$XDG_CONFIG_HOME/supervise/backend.toml`, else `~/.config/supervise/backend.toml`. |
| `SUPERVISE_OMP` | `run`, `doctor --probe-manager` | The `omp` executable (name on `PATH` or a path). Default `omp`. |
| `XDG_STATE_HOME`, `XDG_DATA_HOME`, `XDG_CONFIG_HOME` | as above | Fallbacks used when the `SUPERVISE_*` variable is unset. |
| `CODEX_HOME` | worker/reviewer launch | Codex config directory. Default `~/.codex`. |
| `CLAUDE_CONFIG_DIR` | worker/reviewer launch | Claude Code config directory. Default `~/.claude`. |
| `PYTHONPATH` | you | Selects the release (section 2). |

Set by supervise itself: `SUPERVISE_PYTHON` and `SUPERVISE_STATE` for the manager process. The journal refuses to write in a process that has `SUPERVISE_PARENT_START` set; nothing in this build sets it. Also read: `PATH` (to find `codex`, `claude`, `bwrap`), and `PATH`, `LANG`, `LC_ALL` (passed to the test process).

Defaults: state `~/.local/state/supervise`, data `~/.local/share/supervise`, config `~/.config/supervise/backend.toml`. supervise creates a missing state or data root with mode 0700; it does not change the mode of a directory that already exists, and it neither creates nor checks the permissions of the config file. `doctor` reports an existing root as `FAIL` unless its mode is 0700.

## 5. Commands

Invoke as `python3 -m supervise <command>`. Results are one JSON object on stdout. Errors print `supervise: <message>` on stderr and exit 2. With no subcommand, usage is printed and the exit code is 2.

| Command | Options | What it does |
|---|---|---|
| `install` | `--source SOURCE` (required), `--data-root DATA_ROOT`, `--pointer POINTER`, `--release RELEASE` | Section 2. Data root defaults to the data root above. |
| `uninstall` | `--data-root DATA_ROOT`, `--pointer POINTER` | Restores the pointer file saved by the latest install (or deletes it if none existed) and writes `UNINSTALLATION.json`. `--pointer` must match the installation record. |
| `doctor` | `--config CONFIG_PATH`, `--probe-manager`, `--model MODEL` | Runs the checks below. Exit 1 on `FAIL`, else 0. |
| `run` | `plan`, `--max-seconds MAX_SECONDS`, `--project PROJECT`, `--model MODEL` | Runs the manager for one plan and prints a receipt. |
| `verify` | `invocation_home` | Offline verification of a finished invocation. Exit 0 on `PASS`, else 1. |
| `begin` | `plan`, `--project PROJECT` | Creates or reuses the invocation directory; prints `home`, `invocation_id`, `status`, `version`, `bootstrap`. |
| `intent` | `invocation_home`, `json_intent` | Submits one JSON intent object. The manager does this through its tool; you can do it by hand. |

**`doctor`** reports `python`, `bubblewrap` (it starts `bwrap` with user, pid and network namespaces), `state-root`, `data-root` (each must be a writable directory with mode 0700, or `UNKNOWN` if not yet created), `harness` (the configured `codex`/`claude` executables are on `PATH`), `independence` (some author/reviewer pair has distinct families), and `manager`. The overall verdict is `FAIL` if any check fails, `PASS` only if all pass, else `UNKNOWN`. Without `--probe-manager`, `manager` is `UNKNOWN`, so the overall verdict cannot be `PASS`. With it, `doctor` runs one silent `omp -p --mode json --no-session --no-tools --no-extensions [--model MODEL]` turn (180 s limit) and requires an assistant reply containing `MANAGER-OK`. `--model` has no effect without `--probe-manager`. Before the first run the state root does not exist, so a correctly configured machine can still report `UNKNOWN`.

**`run`** needs `--max-seconds` between 1 and 7200 (default 7200); it bounds the manager session. It starts `omp --mode rpc --no-ui --no-extensions -e <package>/omp/supervise.ts --max-time <seconds> [--model MODEL]` in the project directory (default: current directory) and sends `/supervise <plan>`. It exits 0 only when the receipt status is `ACCEPTED`, else 2. Receipt `status` is one of `ACCEPTED`, `UNVERIFIED`, `PRECHECK_FAILED`, `CHECKPOINT_EXHAUSTED`, `RECONCILE_REQUIRED`, `MANAGER_TIMEOUT`, `COMMAND_REFUSED`, `MANAGER_ERROR`, `MANAGER_STALLED`, `MANAGER_EXITED`; `delivery` is `DELIVERED` only for `ACCEPTED`. The receipt and the manager trace are written under `<state root>/transports/<uuid>/`.

**`intent` actions:** `admit`, `author`, `repair`, `review`, `reconcile`, `diagnose`, `close`. Every intent needs `invocation_id` (for `admit`) and an integer `expected_version` equal to the current state version. An `admit` intent has `acceptance` (list of `{id, proposition, selectors}`; `independent-review` is reserved), `tests` (`argv`, `format`, `timeout_seconds`, `baseline_policy`, `expected_baseline_failures`), `owned_paths`, `packet` (`text`), `checkpoint` (`max_starts`, `max_seconds`, `first_value_seconds`) and `timeout_seconds` (worker limit). `tests.timeout_seconds + timeout_seconds` must not exceed `checkpoint.max_seconds`. `author` and `repair` take `targets`: a non-empty list of open acceptance ids.

## 6. Project requirements

- A `tests/` directory (not a symlink) at the project root. The test command is frozen; `tests/` is hashed at admission and must be unchanged at close.
- Test command: `tests.format` is `unittest` or `pytest-junit`. `argv` is `[python, "-m", "unittest", ...]` or `[python, "-m", "pytest", ...]` (at least three items). A bare interpreter name is resolved on `PATH` to an absolute path. `--junitxml` arguments are refused; supervise adds `-o junit_family=xunit2 --junitxml=<path>` for pytest. `timeout_seconds` must be above 0 and at most 3600. Arguments must be literal: no `*`, `?`, parentheses or newlines.
- Collection must be complete (no loader errors, unique ids). Baseline policy is `nonregression` (the observed failing ids must equal `expected_baseline_failures` exactly) or `all-green`. Each acceptance selector must equal a collected test id or be a prefix of one followed by `.`.
- Owned paths: relative, unique, inside the project, no symlinks, not under `tests/`. They must be regular files by the time the worker finishes. Each comma-separated path on the packet's `Owned paths:` line must exactly equal one of them and have one of the extensions `.py .ts .tsx .js .sh .sql .md .json .yaml .yml .toml`. The packet text must start with the lines `Kind:`, `Owned paths:`, `Declared size:` (`N files, M changed lines`), `Oracle:`, `Domain:`, `Review:`, in that order.
- No project-level harness configuration. Admission checks every configured route, whether or not it will be used. With a Codex route: no `.codex` directory and no `AGENTS.override.md` anywhere in the project tree. With a Claude route: no `.mcp.json` and no `.claude/settings*.json` anywhere in the tree.
- The plan is a regular file. The project and the invocation directory (`<state root>/<sha256>`) must not contain one another. Required capabilities `ssh`, `browser`, `external-service` and `protected-configuration` are refused as unsupported.

## 7. Uninstall

`supervise uninstall` only restores the saved pointer file and writes `UNINSTALLATION.json`. It leaves release directories, `INSTALLATION.json` and all state in place. To remove everything, delete the data root, state root and config yourself. Release directories have their write bits removed, so restore them first (for example `chmod -R u+w <data root>`) before deleting.
