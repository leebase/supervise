# supervise

This repository is the public portable build of `/supervise` (v0.x, pre-release candidate); unlike production v2.8.2, which is a private, frozen deployment that cannot run outside its owner's estate, this build is a standalone source release that replaces production's private staffing router with a TOML route catalog.

## What it is

`supervise` takes one plan file for one bounded software task and tries to deliver it with independent review:

1. A manager model (an oh-my-pi session) reads the plan and the project's tests and submits typed intents to Python code.
2. The code admits the task and freezes its tests, runs a baseline, and has a worker (Codex CLI or Claude Code) change only declared files.
3. The code runs the project's tests itself, seals the evidence, and has a reviewer from a different model family read it in a sandboxed, read-only view.
4. After a rejection, the worker may repair (at most two repair rounds). The invocation closes `ACCEPTED` only after an accepting review.
5. `supervise verify` re-checks a finished invocation offline from its recorded files.

## What the code enforces, and what it does not

Enforced (details in [security-model.md](docs/security-model.md)):

- Admission is frozen: tests, acceptance selectors, owned paths and the checkpoint cannot change after admission.
- Author and reviewer must come from different configured model families.
- The reviewer sees sealed, content-hashed evidence in a bubblewrap sandbox with a read-only evidence view. Only the reviewer and the trusted verifier run in supervise's sandbox; the worker does not.
- Every worker start is reserved in an append-only journal before it runs and accounted exactly once.
- `close` is refused unless the accepted review matches the current owned-source snapshot and the acceptance tests pass in a controller-run observation.
- `supervise run` reports `ACCEPTED` only if offline verification also passes.

Not guaranteed (details in [known-limitations.md](docs/known-limitations.md)):

- Independent source review reduces the risk of fabricated test results, but it does not independently verify that those tests actually ran as claimed.
- **Worker-executed code is not sandboxed by supervise.** The worker runs under its own harness. A Claude Code worker runs its granted test command, which executes the project's tests and the code the worker just wrote, with your user's full permissions and no sandbox. Reviewer isolation does not extend to anything the worker runs. Writes outside owned paths are detected afterwards, not prevented.
- The reviewer sandbox keeps network access and has the reviewer harness's config directory mounted writable.
- Receipts and seals are unkeyed hashes. They detect inconsistency; they do not prove who wrote the files.
- Linux only. Nothing here is a performance or cost claim.

## Prerequisites

- Linux with bubblewrap (`bwrap`) and unprivileged user namespaces; Python 3.12 or newer, run from a path under `/usr/` or `/bin/` (the system `python3`).
- Codex CLI and Claude Code (workers and reviewers) and oh-my-pi `omp` (manager), each installed and logged in by you.
- At least two model families in `backend.toml`.

See [install-and-configuration.md](docs/install-and-configuration.md) for exact steps, including the Ubuntu AppArmor note.

## Quickstart

This sequence is the one recorded in the run described below. Write `~/.config/supervise/backend.toml` first (schema in the install guide).

```bash
git clone <repository-url> ~/candidate
PYTHONPATH=~/candidate python3 -m supervise install --source ~/candidate
```

`install` prints JSON with `path` (an immutable release directory under `~/.local/share/supervise/releases/`), `publication_sha256` and `verdict: PASS`. Use that path from here on:

```bash
export PYTHONPATH=<release path>
python3 -m supervise doctor --probe-manager --model openai-codex/gpt-6.1-sol
cd <your project>
python3 -m supervise run PLAN.md --max-seconds 5400 --model openai-codex/gpt-6.1-sol
python3 -m supervise verify <invocation path from the receipt>
```

`doctor` can report an overall `UNKNOWN` before the first run, because the state root does not exist until first use. `run` prints a JSON receipt whose `invocation` field is the path that `verify` takes. `--model` selects the manager model in `omp`; the string above is the one used in the recorded run.

## What has been shown

One end-to-end run on a fresh Ubuntu 24.04 cloud VM, using an unprivileged user and no access to the author's machine (Claude Code 2.1.294, Codex CLI 0.161.0, oh-my-pi 18.8.5). The receipt was `ACCEPTED` / `DELIVERED` in 147 s with 2 native starts (Codex author 82 s; Claude reviewer 14 s, ACCEPT), and offline `verify` passed all 9 checks it had at the time (a tenth, `acceptance-observation`, was added later; that run's recorded observation shows all 8 acceptance tests passing). Changing one byte of a sealed chunk, a route record or the journal each made `verify` fail with a named check. This is one run, not a benchmark.

## Production deployment documentation

The documentation, case study and example of the private production deployment (v2.8.x), which this public build is
derived from but is not, are kept unchanged in [production/docs/](production/docs/README.md) and [production/examples/](production/examples/README.md).
Start with the [case study](production/docs/case-study.md).

## Documentation

- [Installation and configuration](docs/install-and-configuration.md)
- [Security model](docs/security-model.md)
- [Known limitations](docs/known-limitations.md)
- [Architecture](docs/architecture.md)

## License

Apache-2.0. Copyright 2026 Lee Harrington. See [LICENSE](LICENSE), [NOTICE](NOTICE) and [THIRD_PARTY.md](THIRD_PARTY.md).
