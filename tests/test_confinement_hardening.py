# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for SC-2: project-confinement guard hardening.

Residual findings of the independent final review of the S4/S5 remediation delta (confirmed by the Chief):
- `.git` and `__pycache__` were skipped by name at every depth, so changes could hide in a nested directory with those
  names. Only the project's top-level `.git` directory and bytecode-only `__pycache__` contents may be ignored.
- Permission bits were not recorded, so a mode-only change (e.g. making a non-owned script executable) was invisible.
"""
import os
from pathlib import Path
import shutil
import stat
import sys
import unittest

HAVE_SANDBOX = shutil.which('bwrap') is not None and sys.executable.startswith(('/usr/', '/bin/'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_controller_transaction as tx  # noqa: E402  (frozen S3-3 fixture, reused read-only)


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class ConfinementHardeningTests(tx.TransactionFixture):
    def during_author(self, action):
        original = self.fb.interpret

        def interpret(spec, capture, *args, **kwargs):
            result = original(spec, capture, *args, **kwargs)
            if not spec.requires_review_sandbox:
                action(self.project)
            return result
        self.fb.interpret = interpret

    def test_nested_dot_git_directory_is_not_a_hiding_place(self):
        self.begin()
        def hide(project):
            nested = project / 'pkg' / '.git'
            nested.mkdir()
            (nested / 'payload.py').write_text('X = 1\n')
        self.during_author(hide)
        self.author(tx.FIX)
        self.refused('outside owned', 'review')

    def test_source_in_a_pycache_directory_is_not_ignored(self):
        self.begin()
        def hide(project):
            cache = project / 'pkg' / '__pycache__'
            cache.mkdir(exist_ok=True)
            (cache / 'shadow.py').write_text('X = 1\n')
        self.during_author(hide)
        self.author(tx.FIX)
        self.refused('outside owned', 'review')

    def test_mode_only_change_is_a_violation(self):
        self.begin()
        script = self.project / 'tools.sh'
        script.write_text('#!/bin/sh\necho ok\n')
        os.chmod(script, 0o644)
        self.restart()
        self.during_author(lambda project: os.chmod(project / 'tools.sh', 0o755))
        self.author(tx.FIX)
        self.refused('outside owned', 'review')

    def test_top_level_git_and_bytecode_caches_stay_ignored(self):
        self.begin()
        (self.project / '.git').mkdir(exist_ok=True)
        def benign(project):
            (project / '.git' / 'index').write_bytes(b'changed index')
            cache = project / 'pkg' / '__pycache__'
            cache.mkdir(exist_ok=True)
            (cache / 'core.cpython-312.pyc').write_bytes(b'bytecode')
        self.during_author(benign)
        self.author(tx.FIX)
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')


if __name__ == '__main__':
    unittest.main()
