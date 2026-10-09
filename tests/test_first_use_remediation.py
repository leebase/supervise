# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S6-R1: defects found by the first real end-to-end run (S6 attempt 1).

In a fresh Ubuntu VM with real OMP 18.8.5:
- `supervise run` closed the manager's stdin right after the prompt; OMP's RPC mode exits at end of input, so the run
  ended MANAGER_EXITED after one second. The manager's stdin must stay open until the run ends.
- `supervise doctor` ignored the default backend configuration, never probed the manager, and failed state/data roots
  that did not exist yet, while install/run created those roots group-readable (0775). Roots that the product creates
  are created private (0700); a root that does not exist yet is UNKNOWN in doctor (and doctor does not create it).
- `supervise doctor --probe-manager [--model M]` performs one real manager turn through OMP (SUPERVISE_OMP or `omp` on
  PATH): argv includes `-p`, `--mode json`, `--no-session`, `--no-tools`, `--no-extensions` and `--model M` when given;
  PASS only when an assistant message_end event's text contains MANAGER-OK.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest

import supervise
from supervise import installer

PACKAGE_ROOT = Path(supervise.__file__).resolve().parent.parent

FAKE = '''#!{python}
import json, os, sys, time
mode = os.environ["FAKE_MODE"]
with open(os.environ["FAKE_RECORD"], "a") as f:
    f.write(json.dumps(sys.argv) + "\\n")
def emit(value):
    sys.stdout.write(json.dumps(value) + "\\n"); sys.stdout.flush()
if mode == "rpc-eof-exits":
    sys.stdin.readline()
    emit({{"id": "supervise", "type": "response", "success": True}})
    sys.stdin.read()          # a real RPC session ends at end of input
    sys.exit(0)
if mode in ("probe-ok", "probe-wrong"):
    text = "MANAGER-OK" if mode == "probe-ok" else "something else"
    emit({{"type": "message_end", "message": {{"role": "assistant", "content": [{{"type": "text", "text": text}}]}}}})
    sys.exit(0)
'''

CONFIG = '''
[[route]]
id = "codex-gpt"
harness = "codex"
vendor = "openai"
model = "m1"
family = "openai-gpt"
roles = ["impl", "review"]

[[route]]
id = "claude-sonnet"
harness = "claude"
vendor = "anthropic"
model = "m2"
family = "anthropic-claude"
roles = ["review", "impl"]

[crew]
authors = ["codex-gpt", "claude-sonnet"]
reviewers = ["claude-sonnet", "codex-gpt"]
require_review_fallback = false
'''


class FirstUseRemediationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name).resolve()
        self.project = self.root / 'project'
        (self.project / 'tests').mkdir(parents=True)
        (self.project / 'tests' / 'test_x.py').write_text('import unittest\n')
        self.plan = self.project / 'PLAN.md'
        self.plan.write_text('# Plan\n')
        self.record = self.root / 'record.jsonl'
        fake = self.root / 'fake-omp'
        fake.write_text(FAKE.format(python=sys.executable))
        os.chmod(fake, 0o755)
        config = self.root / 'backend.toml'
        config.write_text(CONFIG)
        self.state = self.root / 'fresh' / 'state'
        self.data = self.root / 'fresh' / 'data'
        self.env = dict(os.environ, SUPERVISE_STATE=str(self.state), SUPERVISE_DATA=str(self.data),
                        SUPERVISE_CONFIG=str(config), SUPERVISE_OMP=str(fake), FAKE_RECORD=str(self.record),
                        PYTHONPATH=str(PACKAGE_ROOT))

    def tearDown(self):
        self.dir.cleanup()

    def cli(self, *args, mode='probe-ok', timeout=60):
        return subprocess.run([sys.executable, '-m', 'supervise', *args], cwd=self.project,
                              env=dict(self.env, FAKE_MODE=mode), capture_output=True, text=True, timeout=timeout)

    def calls(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def doctor(self, *args, mode='probe-ok'):
        result = self.cli('doctor', *args, mode=mode)
        return result, {check['name']: check for check in json.loads(result.stdout)['checks']}

    # run ---------------------------------------------------------------------------------------------------
    def test_manager_stdin_stays_open_for_the_whole_run(self):
        started = time.monotonic()
        result = self.cli('run', str(self.plan), '--max-seconds', '4', '--project', str(self.project),
                          mode='rpc-eof-exits')
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt['status'], 'MANAGER_TIMEOUT', receipt)
        self.assertLess(time.monotonic() - started, 30)

    def test_run_creates_a_private_state_root(self):
        self.cli('run', str(self.plan), '--max-seconds', '2', '--project', str(self.project), mode='rpc-eof-exits')
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)

    def test_begin_creates_a_private_state_root(self):
        result = self.cli('begin', str(self.plan), '--project', str(self.project))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)

    def test_install_creates_a_private_data_root(self):
        installer.install(PACKAGE_ROOT, data_root=self.data)
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.data / 'releases').stat().st_mode), 0o700)

    # doctor ------------------------------------------------------------------------------------------------
    def test_doctor_uses_the_default_backend_configuration(self):
        _, checks = self.doctor()
        for name in ('harness', 'independence'):
            self.assertNotIn('No readable', checks[name]['detail'])

    def test_missing_roots_are_unknown_and_not_created(self):
        _, checks = self.doctor()
        self.assertEqual(checks['state-root']['status'], 'UNKNOWN')
        self.assertEqual(checks['data-root']['status'], 'UNKNOWN')
        self.assertFalse(self.state.exists())
        self.assertFalse(self.data.exists())

    def test_doctor_does_not_probe_the_manager_unless_asked(self):
        _, checks = self.doctor()
        self.assertEqual(checks['manager']['status'], 'UNKNOWN')
        self.assertEqual(self.calls(), [])

    def test_manager_probe_passes_on_a_real_reply(self):
        _, checks = self.doctor('--probe-manager', '--model', 'probe-model-x')
        self.assertEqual(checks['manager']['status'], 'PASS')
        argv = self.calls()[0][1:]
        for flag in ('-p', '--no-session', '--no-tools', '--no-extensions'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index('--mode') + 1], 'json')
        self.assertEqual(argv[argv.index('--model') + 1], 'probe-model-x')

    def test_manager_probe_fails_on_a_wrong_reply(self):
        _, checks = self.doctor('--probe-manager', mode='probe-wrong')
        self.assertEqual(checks['manager']['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
