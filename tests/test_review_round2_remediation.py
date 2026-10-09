# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for the S4 final-round and S5 sprint review findings (confirmed by the Chief).

- S4 Medium: the project-confinement inventory skipped every non-regular entry, so a worker could add a symlink outside
  the owned paths (for example a module the tests import, pointing outside the project) without detection.
- S5 Medium: `supervise doctor` printed an overall verdict of PASS when checks were UNKNOWN (only FAIL lowered it),
  reporting unchecked prerequisites as passed. Exit codes are unchanged (0 when nothing failed).
"""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock

HAVE_SANDBOX = shutil.which('bwrap') is not None and sys.executable.startswith(('/usr/', '/bin/'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_controller_transaction as tx  # noqa: E402  (frozen S3-3 fixture, reused read-only)


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class ConfinementEntryTests(tx.TransactionFixture):
    def make_entry_during_author(self, create):
        original = self.fb.interpret

        def interpret(spec, capture, *args, **kwargs):
            result = original(spec, capture, *args, **kwargs)
            if not spec.requires_review_sandbox:
                create(self.project)
            return result
        self.fb.interpret = interpret

    def test_new_symlink_outside_owned_paths_is_a_violation(self):
        self.begin()
        outside = self.base / 'elsewhere.py'
        outside.write_text('HELPER = 1\n')
        self.make_entry_during_author(lambda project: (project / 'pkg' / 'helper.py').symlink_to(outside))
        self.author(tx.FIX)
        self.refused('outside owned', 'review')

    def test_replacing_a_file_with_a_symlink_is_a_violation(self):
        self.begin()
        def swap(project):
            target = project / 'pkg' / '__init__.py'
            target.unlink()
            target.symlink_to(project / 'pkg' / 'core.py')
        self.make_entry_during_author(swap)
        self.author(tx.FIX)
        self.refused('outside owned', 'review')

    def test_new_directory_symlink_is_a_violation(self):
        self.begin()
        self.make_entry_during_author(lambda project: (project / 'linked').symlink_to(self.base))
        self.author(tx.FIX)
        self.refused('outside owned', 'review')


class DoctorVerdictTests(unittest.TestCase):
    def setUp(self):
        self.cli = importlib.import_module('supervise.cli')
        self.doctor = importlib.import_module('supervise.doctor')

    def run_doctor(self, *statuses):
        checks = [self.doctor.Check('c%d' % i, s, 'detail') for i, s in enumerate(statuses)]
        out = io.StringIO()
        with mock.patch.object(self.doctor, 'run_checks', lambda **kw: checks), contextlib.redirect_stdout(out):
            code = self.cli.main(['doctor'])
        return code, json.loads(out.getvalue())

    def test_unknown_checks_are_never_reported_as_pass(self):
        code, report = self.run_doctor('PASS', 'UNKNOWN')
        self.assertEqual(code, 0)
        self.assertEqual(report['verdict'], 'UNKNOWN')

    def test_all_pass_and_any_fail(self):
        self.assertEqual(self.run_doctor('PASS', 'PASS')[1]['verdict'], 'PASS')
        code, report = self.run_doctor('PASS', 'UNKNOWN', 'FAIL')
        self.assertEqual((code, report['verdict']), (1, 'FAIL'))


if __name__ == '__main__':
    unittest.main()
