# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S3 sprint-review remediation round 1.

Independent S3 review part B (DeepSeek), confirmed by the Chief:
- Medium: the frozen-test sandbox (`evidence.observe`) passed the controller's whole environment to the code under test,
  shared the host network and exposed the user's home directory read-only, so worker code run by the tests could read
  controller secrets and exfiltrate them.
- Medium (part A): after a REJECT, calling `author` again instead of `repair` bypassed the two-round repair bound.
- Integration (non-blocking in the review, blocking for the Chief): admission stores `acceptance.json` as a list while
  `budget` required an object; the transaction worked around it with a "legacy" path that refuses every operator
  continuation, so the supported continuation mechanism could never be used on a real admission.
"""
import importlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

HAVE_SANDBOX = shutil.which('bwrap') is not None and sys.executable.startswith(('/usr/', '/bin/'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_controller_transaction as tx  # noqa: E402  (frozen S3-3 fixture, reused read-only)


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class BudgetAdmissionShapeTests(tx.TransactionFixture):
    def test_budget_reads_a_real_admission(self):
        self.begin()
        try:
            left = self.budget.remaining(self.home)
            allocation = self.budget.worker_allocation(self.home, self.ctl.admitted, [], {})
        except self.budget.BudgetError as error:
            self.fail('budget cannot read a real admission: ' + str(error))
        self.assertGreater(left, 0)
        self.assertGreater(allocation, 0)

    def test_controller_has_no_legacy_budget_path(self):
        source = Path(self.t.__file__).read_text()
        self.assertNotIn('legacy acceptance rows', source)
        self.assertNotIn('_is_legacy_budget_format_error', source)

    def test_operator_continuation_resumes_an_expired_transaction(self):
        self.begin()
        later = time.time() + 5000
        with mock.patch('time.time', return_value=later):
            self.assertEqual(self.author(None)['status'], 'CHECKPOINT_EXHAUSTED')
            self.assertEqual(self.starts(), [])
            authority = self.base / 'operator-direction.txt'
            authority.write_text('Operator directs continuation of the same objective.')
            try:
                self.budget.authorize(self.home, authority, 900)
                left = self.budget.remaining(self.home)
            except self.budget.BudgetError as error:
                self.fail('operator continuation refused on a real admission: ' + str(error))
            self.assertGreater(left, 0)
            result = self.author(tx.FIX)
        self.assertNotEqual(result['status'], 'CHECKPOINT_EXHAUSTED')
        self.assertEqual(len(self.starts()), 1)


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class RepairBoundTests(tx.TransactionFixture):
    def test_author_is_refused_after_a_completed_review(self):
        self.begin(script={'review-g': ('reject', 'reject', 'reject')})
        self.author(tx.FIX)
        self.assertEqual(self.do('review')['status'], 'REVIEW_BLOCKING')
        self.fb.edits.append(tx.FIX + '# another attempt\n')
        self.refused(('repair', 'review'), 'author', targets=list(tx.GAPS))

    def test_author_retry_after_a_worker_failure_is_still_allowed(self):
        self.begin(script={'author-a': ('crash',)})
        first = self.author(None)
        self.assertNotEqual(first['status'], 'CHECKPOINT_EXHAUSTED')
        second = self.author(tx.FIX)
        self.assertEqual(len([s for s in self.starts() if s['route'].startswith('author')]), 2, second)


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class TestSandboxTests(unittest.TestCase):
    PROBE = textwrap.dedent('''
        import json, os, socket, unittest
        from pathlib import Path

        class Probe(unittest.TestCase):
            def test_probe(self):
                seen = {"env_secret": os.environ.get("SUPERVISE_SECRET_PROBE")}
                try:
                    seen["home_secret"] = Path(SECRET_PATH).read_text()
                except OSError:
                    seen["home_secret"] = None
                s = socket.socket(); s.settimeout(2)
                try:
                    s.connect(("1.1.1.1", 53)); seen["net"] = True
                except OSError:
                    seen["net"] = False
                print("PROBE " + json.dumps(seen))
    ''')

    def setUp(self):
        self.e = importlib.import_module('supervise.controller.evidence')
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.home = base / 'home'
        self.project = self.home / 'projects' / 'demo'        # project lives under the user's home
        (self.project / 'pkg').mkdir(parents=True)
        (self.project / 'tests').mkdir()
        (self.project / 'pkg' / '__init__.py').write_text('')
        
        self.secret = self.home / '.provider' / 'auth.json'
        self.secret.parent.mkdir(parents=True)
        self.secret.write_text('TOP-SECRET-TOKEN')
        (self.project / 'tests' / 'test_probe.py').write_text(self.PROBE.replace('SECRET_PATH', repr(str(self.secret))))
        self.root = self.home / '.local' / 'state' / 'supervise' / 'inv'   # private evidence root also under home
        self.root.mkdir(parents=True, mode=0o700)
        self.saved = {k: os.environ.get(k) for k in ('HOME', 'SUPERVISE_SECRET_PROBE')}
        os.environ.update(HOME=str(self.home), SUPERVISE_SECRET_PROBE='LEAKED')

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for path in Path(self.dir.name).rglob('*'):
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self.dir.cleanup()

    def observe(self):
        tests = {'argv': [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], 'format': 'unittest',
                 'timeout_seconds': 60}
        binding = self.e.freeze(self.project, self.root, acceptance=[{'id': 'probe', 'proposition': 'probe'}], tests=tests,
                                source_scopes=['pkg'], test_scopes=['tests'], owned_paths=['pkg/__init__.py'])
        contract = json.loads(Path(binding['path']).read_text())
        out = self.root / 'obs'
        out.mkdir(mode=0o700)
        observation = self.e.observe(contract, out)
        text = Path(observation['process']['stdout']['path']).read_text() + Path(observation['process']['stderr']['path']).read_text()
        line = next(l for l in text.splitlines() if l.startswith('PROBE '))
        return observation, json.loads(line[len('PROBE '):])

    def test_code_under_test_sees_no_secrets_network_or_home(self):
        observation, seen = self.observe()
        self.assertEqual(observation['verdict'], 'PASS')            # the project under the home is still testable
        self.assertEqual(seen, {'env_secret': None, 'home_secret': None, 'net': False})


if __name__ == '__main__':
    unittest.main()
