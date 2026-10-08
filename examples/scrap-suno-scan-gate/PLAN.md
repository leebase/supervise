# scrap-suno: fail-closed `scan` gate (sanitized copy of the accepted PLAN)

Ordinary implementation assignment in THIS project (project_root and source_root are this directory). Deliver the project's documented next action: a `scan` CLI command that refuses to start a live scan unless the auth and drift preflight checks both passed. The acceptance tests in `tests/test_scan_gate.py` are the contract and already exist; they fail at baseline.

## Required behavior (the frozen tests in `tests/test_scan_gate.py` are the contract)

1. New module `scrap_suno/scan_gate.py` with `evaluate_scan_gate(auth, drift)` returning an object with `allowed: bool` and `reasons: tuple[str, ...]`. Inputs are the JSON mappings written by `auth-check --json` and `drift-check --json`. A gate passes only when its mapping has `status == "ok"`; every failing gate contributes a reason that quotes its status (and, for drift, its recommended action).
2. New `scan` subcommand in `scrap_suno/cli.py` with required `--auth-status PATH` and `--drift-status PATH`. Missing or unreadable status files are gate refusals that name the path. Any refusal prints `scrap-suno: error: scan gate refused: ...` (one line per reason is fine) to stderr and returns exit 2. When the gate passes, the command exits 3 with a message that the live scan is not implemented yet. `--help` lists `scan`.
3. Do not change `tests/`, existing commands, or files outside `scrap_suno/`. No network, no browser, no credentials; this project stores no session material. Keep the dependency-free stdlib style of the package.

## Admission (the manager submits exactly this shape)

- Exact test argv: `/usr/bin/python3 -m unittest discover -s tests -v`; format `unittest`; `tests.timeout_seconds` 30; `baseline_policy` `nonregression`.
- Expected baseline outcomes (all six are FAIL before implementation; the other 45 tests pass): `test_scan_gate.ScanGateCliTests.test_help_lists_scan_command`, `...test_scan_refuses_when_status_files_are_missing`, `...test_scan_refuses_when_auth_is_not_ok`, `...test_scan_refuses_when_drift_is_not_ok`, `...test_scan_gate_passes_but_live_scan_is_not_implemented`, `test_scan_gate.ScanGateUnitTests.test_evaluate_scan_gate_reports_every_failing_gate`.
- Acceptance: `scan-gate-cli` (proposition: the `scan` command refuses with exit 2 and named reasons unless both status files report ok, exits 3 when the gate passes, and appears in `--help`; selectors `test_scan_gate.ScanGateCliTests`) and `scan-gate-library` (proposition: `scrap_suno.scan_gate.evaluate_scan_gate` reports every failing gate; selectors `test_scan_gate.ScanGateUnitTests`).
- `owned_paths` EXACTLY `["scrap_suno/"]`. No `required_capabilities` (local Python only).
- Worker `timeout_seconds` 900; `checkpoint.max_starts` 6; `checkpoint.max_seconds` 3000; `checkpoint.first_value_seconds` 600. These are internal economical limits, not a user budget.
- `packet.estimated_files` 2, `packet.estimated_lines` 120. `packet.text` MUST begin with these structural lines, then the implementation instructions from "Required behavior" above:

```text
Kind: impl
Owned paths: scrap_suno/cli.py, scrap_suno/scan_gate.py
Declared size: 2 files, 120 changed lines
Oracle: /usr/bin/python3 -m unittest discover -s tests -v
Domain: none
Review: independent reviewer
```

## Definition of Done

Outcome: all 51 frozen tests pass (the six scan-gate tests newly green, no regression), the change is confined to `scrap_suno/`, and an independent reviewer accepts the full plan against the sealed evidence with zero blocking High/Medium findings; the manager closes the transaction ACCEPTED. Accountable owner: the manager. Acceptance authority: the independent reviewer on the exact tested bytes. Stop if an API, dependency or repository premise differs from this specification; report the exact conflict.
