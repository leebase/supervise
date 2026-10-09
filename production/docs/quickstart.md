# Quickstart

This quickstart assumes an estate where `/supervise` v2.8.2 is already installed (see [installation and configuration](install-and-configuration.md): the release is not installable from this repository). It compresses the [teammate guide](teammate-guide.md)'s walkthrough into the steps you actually type.

## 1. Confirm the installed identity (read-only)

```bash
RELEASE="$HOME/.local/lib/supervise/releases/20261007-v2.8.2-attested-receipt-candidate"
sha256sum "$RELEASE/publication.json"
# expect 2e5845bd49fcd2fffa1eab96a04b64b39e0fad868e8387dcd55e5d268a26e511
grep -o 'releases/[^/"]*' "$HOME/.omp/agent/extensions/supervise.ts"
python3 -B "$RELEASE/command.py" --help
```

## 2. Write the failing tests, then the PLAN

In the project that owns the source, add the acceptance tests to the ordinary test suite so they fail at baseline, then write `PLAN.md`:

```text
Objective: <one bounded outcome>.
Owned paths: <package or files the worker may change>.
Forbidden: tests/, unrelated paths, deployment, credentials, network.
Tests: /usr/bin/python3 -m unittest discover -s tests -v   (unittest; timeout 30 s; baseline_policy nonregression)
Expected baseline: <N> tests FAIL (the new acceptance tests), the rest PASS.
Acceptance: <proposition> (selectors: <TestCase class or test id>) ...
Worker timeout 900 s; checkpoint 6 starts / 3000 s (internal economical limits, not a user budget).
Definition of Done: all tests pass, change confined to owned paths, an independent reviewer
            accepts the full plan on sealed evidence with zero blocking High/Medium; close ACCEPTED.
Stop if an API, dependency or repository premise differs from this specification; report the conflict.
```

Keep the test argv literal (no `-p test_*.py` wildcards, no parentheses or newlines); the controller must express it as an exact worker command grant.

## 3. Invoke

Inside an OMP session, from the project directory:

```text
/supervise ./PLAN.md
```

From a shell (the Codex skill uses this transport):

```bash
python3 -B "$HOME/.local/lib/supervise/releases/20261007-v2.8.2-attested-receipt-candidate/command.py" ./PLAN.md --max-seconds 3600
```

## 4. Watch

The transport prints a JSON receipt when it finishes. While it runs, the invocation directory under `$HOME/.local/state/omp/supervise/` fills with `STATE.jsonl` (one `start` and one `finish` record per native start), `crew-N-<sha256>.json` (who was chosen and why), `route-N.json` (the attestation), and the native stream for each start. The read-only locator in the [owner guide](owner-operator-guide.md) prints a summary.

## 5. Read the result

| You see | Meaning |
|---|---|
| receipt `status: ACCEPTED`, `delivery: DELIVERED`, `manager_returncode: 143` | Success. 143 is the transport terminating the idle manager after acceptance. |
| `state.json` ACCEPTED; `accepted-review.json` and `closure.json` present; `STATE.jsonl` ends `outcome_closed` | Independently accepted, durable closure. |
| `judge_fail` in a review finish record | A genuine finding; the supervisor runs a reviewer-directed repair round (at most two rounds per admission; a round may contain more than one start). |
| `PRECHECK_FAILED` at version 0 | Your PLAN or project setup did not validate; nothing was spent. |
| `MANAGER_ERROR` | The manager model is not usable by the harness transport; fix the model configuration and re-invoke the same PLAN. |
| `OWNER_REFUSED` / `RECONCILE_REQUIRED` / `CHECKPOINT_EXHAUSTED` | Blocked or unaccepted; the reason is in `state.json` and the receipt. See [troubleshooting](troubleshooting.md). |

## 6. Do not

Re-run under a new PLAN name to get fresh starts; edit state; implement the assignment yourself while the manager is open; treat a green test run as acceptance.

The accepted run of exactly this flow is in [examples/scrap-suno-scan-gate](../examples/scrap-suno-scan-gate/README.md).

---

*Part of the [`/supervise` public documentation](../../README.md), Apache License 2.0. Production documentation: https://nginx.leebasehome.com/docs/pages/supervision-teammate-guide.html*
