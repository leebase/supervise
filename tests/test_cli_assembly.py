# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S5-A: assemble the public CLI into a runnable product.

Gap found before S6: `supervise run` only validated the plan, nothing created an invocation, and `supervise intent`
could not admit (the Controller requires a frozen contract). This oracle fixes the contract for:
  supervise begin PLAN [--project DIR]            create/reuse the invocation; print {home, invocation_id, status,
                                                   version, bootstrap}
  supervise intent HOME JSON  (action=admit)       route to admission.admit after checking invocation_id/version
  supervise run PLAN --max-seconds N [--project DIR] [--model M]
                                                   drive an OMP manager in RPC mode with only the packaged
                                                   extension, watch it, and write a receipt
`run` reports ACCEPTED only when the invocation is ACCEPTED, the command was acknowledged, the plan is unchanged
and offline verification passes; an ACCEPTED state that does not verify is UNVERIFIED. OMP is located through
SUPERVISE_OMP when set, otherwise `omp` on PATH. A fake OMP stands in for the manager here; S6 runs the real one.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

import supervise
from supervise import transport

PACKAGE_ROOT = Path(supervise.__file__).resolve().parent.parent

CONFIG = '''
[[route]]
id = "codex-gpt"
harness = "codex"
vendor = "openai"
model = "gpt-6.1-sol"
family = "openai-gpt"
roles = ["impl", "review"]

[[route]]
id = "claude-sonnet"
harness = "claude"
vendor = "anthropic"
model = "claude-sonnet-5-5"
family = "anthropic-claude"
roles = ["review", "impl"]

[crew]
authors = ["codex-gpt", "claude-sonnet"]
reviewers = ["claude-sonnet", "codex-gpt"]
require_review_fallback = false
'''

FAKE_OMP = '''#!{python}
import json, os, subprocess, sys, time
record = {{"argv": sys.argv, "pid": os.getpid(),
           "env": {{k: os.environ.get(k) for k in ("SUPERVISE_PYTHON", "PYTHONPATH")}}}}
line = sys.stdin.readline()
record["prompt"] = json.loads(line)
mode = os.environ["FAKE_MODE"]
with open(os.environ["FAKE_RECORD"], "w") as f:
    json.dump(record, f)
def emit(value):
    sys.stdout.write(json.dumps(value) + "\\n"); sys.stdout.flush()
if mode == "refuse":
    emit({{"id": "supervise", "type": "response", "success": False, "error": "nope"}})
    time.sleep(60)
emit({{"id": "supervise", "type": "response", "success": True}})
if mode == "errors":
    for _ in range(2):
        emit({{"type": "turn_start"}})
        emit({{"type": "turn_end", "message": {{"stopReason": "error", "errorMessage": "boom-manager"}}}})
    time.sleep(60)
if mode == "accept-unverified":
    plan = json.loads(record["prompt"]["message"][len("/supervise "):])
    out = subprocess.run([os.environ["SUPERVISE_PYTHON"], "-m", "supervise", "begin", plan, "--project", os.getcwd()],
                         capture_output=True, text=True, check=True)
    home = json.loads(out.stdout)["home"]
    with open(os.path.join(home, "state.json"), "w") as f:
        json.dump({{"status": "ACCEPTED", "version": 9}}, f)
time.sleep(120)
'''


class CliAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name).resolve()
        self.state = self.root / 'state'
        self.project = self.root / 'project'
        (self.project / 'tests').mkdir(parents=True)
        (self.project / 'pkg.py').write_text('VALUE = 1\n')
        (self.project / 'tests' / 'test_pkg.py').write_text(
            'import unittest\nimport pkg\n\nclass T(unittest.TestCase):\n    def test_value(self):\n'
            '        self.assertEqual(pkg.VALUE, 2)\n')
        self.plan = self.project / 'PLAN.md'
        self.plan.write_text('# Plan\n\nMake VALUE equal 2.\n')
        config = self.root / 'backend.toml'
        config.write_text(CONFIG)
        self.record = self.root / 'record.json'
        fake = self.root / 'fake-omp'
        fake.write_text(FAKE_OMP.format(python=sys.executable))
        os.chmod(fake, 0o755)
        self.env = dict(os.environ, SUPERVISE_STATE=str(self.state), SUPERVISE_CONFIG=str(config),
                        SUPERVISE_DATA=str(self.root / 'data'), SUPERVISE_OMP=str(fake),
                        FAKE_RECORD=str(self.record), PYTHONPATH=str(PACKAGE_ROOT))

    def tearDown(self):
        self.dir.cleanup()

    def cli(self, *args, mode='refuse', timeout=60):
        env = dict(self.env, FAKE_MODE=mode)
        return subprocess.run([sys.executable, '-m', 'supervise', *args], cwd=self.project, env=env,
                              capture_output=True, text=True, timeout=timeout)

    def begin(self):
        result = self.cli('begin', str(self.plan), '--project', str(self.project))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    # begin -------------------------------------------------------------------------------------------------
    def test_begin_creates_a_private_invocation_with_a_bootstrap(self):
        data = self.begin()
        expected = transport.invocation_path(self.state, self.project, self.plan)
        self.assertEqual(Path(data['home']), expected)
        self.assertEqual(stat.S_IMODE(expected.stat().st_mode), 0o700)
        pending = json.loads((expected / 'pending.json').read_text())
        self.assertEqual(data['invocation_id'], pending['invocation_id'])
        self.assertEqual(pending['cwd'], str(self.project))
        self.assertEqual((data['status'], data['version']), ('INITIALIZED', 0))
        for word in (data['invocation_id'], 'supervise_continue', 'admit', 'author', 'review', 'close',
                     str(self.plan)):
            self.assertIn(word, data['bootstrap'])

    def test_begin_is_idempotent(self):
        self.assertEqual(self.begin()['invocation_id'], self.begin()['invocation_id'])

    def test_begin_refuses_a_missing_plan(self):
        result = self.cli('begin', str(self.project / 'missing.md'), '--project', str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.state.exists() and any(self.state.iterdir()))

    # intent admit -----------------------------------------------------------------------------------------
    def admit(self, **changes):
        data = self.begin()
        intent = {'action': 'admit', 'invocation_id': data['invocation_id'], 'expected_version': 0}
        intent.update(changes)
        return data, self.cli('intent', data['home'], json.dumps(intent))

    def test_admit_is_routed_to_admission(self):
        data, result = self.admit()
        self.assertEqual(result.returncode, 2)
        self.assertIn('owned paths', result.stderr)
        self.assertFalse((Path(data['home']) / 'contract.json').exists())

    def test_admit_checks_the_invocation_identity(self):
        data, result = self.admit(invocation_id='someone-else')
        self.assertEqual(result.returncode, 2)
        self.assertIn('invocation', result.stderr)
        self.assertNotIn('owned paths', result.stderr)

    def test_admit_checks_the_expected_version(self):
        data, result = self.admit(expected_version=3)
        self.assertEqual(result.returncode, 2)
        self.assertIn('version', result.stderr)
        self.assertNotIn('owned paths', result.stderr)

    # run ---------------------------------------------------------------------------------------------------
    def run_cli(self, mode, seconds='30', *extra):
        started = time.monotonic()
        result = self.cli('run', str(self.plan), '--max-seconds', seconds, '--project', str(self.project), *extra,
                          mode=mode, timeout=90)
        elapsed = time.monotonic() - started
        receipt = json.loads(result.stdout)
        return result, receipt, elapsed

    def assert_retained(self, receipt):
        trace = Path(receipt['trace'])
        self.assertTrue(str(trace).startswith(str(self.state / 'transports')))
        self.assertEqual(json.loads((trace / 'receipt.json').read_text()), receipt)
        self.assertTrue((trace / 'rpc.jsonl').read_bytes().startswith(b'{"id": "supervise"'))

    def test_run_launches_only_the_packaged_extension_and_sends_the_plan(self):
        result, receipt, _ = self.run_cli('refuse', '30', '--model', 'manager-model-x')
        record = json.loads(self.record.read_text())
        argv = record['argv'][1:]
        for flag in ('--no-ui', '--no-extensions'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index('--mode') + 1], 'rpc')
        self.assertEqual(argv[argv.index('--model') + 1], 'manager-model-x')
        self.assertEqual(float(argv[argv.index('--max-time') + 1]), 30.0)
        extension = Path(argv[argv.index('-e') + 1])
        self.assertEqual(extension, PACKAGE_ROOT / 'supervise' / 'omp' / 'supervise.ts')
        self.assertEqual(record['prompt']['type'], 'prompt')
        self.assertEqual(record['prompt']['id'], 'supervise')
        self.assertEqual(record['prompt']['message'], '/supervise ' + json.dumps(str(self.plan)))
        self.assertEqual(record['env']['SUPERVISE_PYTHON'], sys.executable)
        self.assertEqual(record['env']['PYTHONPATH'].split(os.pathsep)[0], str(PACKAGE_ROOT))

    def test_refused_command_is_reported(self):
        result, receipt, elapsed = self.run_cli('refuse')
        self.assertEqual(result.returncode, 2)
        self.assertEqual((receipt['status'], receipt['delivery']), ('COMMAND_REFUSED', 'UNDELIVERED'))
        self.assertLess(elapsed, 30)
        self.assert_retained(receipt)

    def test_manager_errors_stop_the_run(self):
        result, receipt, elapsed = self.run_cli('errors')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(receipt['status'], 'MANAGER_ERROR')
        self.assertIn('boom-manager', receipt['manager_fault'])
        self.assertLess(elapsed, 30)

    def test_window_is_finite_and_the_manager_is_stopped(self):
        result, receipt, elapsed = self.run_cli('hang', '3')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(receipt['status'], 'MANAGER_TIMEOUT')
        self.assertLess(elapsed, 30)
        pid = json.loads(self.record.read_text())['pid']
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assert_retained(receipt)

    def test_accepted_state_without_verifiable_closure_is_not_delivered(self):
        result, receipt, elapsed = self.run_cli('accept-unverified')
        self.assertEqual(result.returncode, 2)
        self.assertEqual((receipt['status'], receipt['delivery']), ('UNVERIFIED', 'UNDELIVERED'))
        self.assertEqual(Path(receipt['invocation']), transport.invocation_path(self.state, self.project, self.plan))
        self.assertLess(elapsed, 30)

    def test_run_refuses_an_unbounded_window(self):
        result = self.cli('run', str(self.plan), '--max-seconds', 'inf', '--project', str(self.project))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.record.exists())


if __name__ == '__main__':
    unittest.main()
