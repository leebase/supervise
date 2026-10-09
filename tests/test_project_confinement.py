# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle: project confinement guard in the controller.

Found while confirming the S4 review's Codex finding: harness command grants cannot always be enforced (Codex has no
command allowlist), and the controller only watched owned paths and frozen tests. A worker that edited any other project
file (for example a non-owned module the tests import) could make the tests pass undetected. The controller must snapshot
every project file outside the owned paths (ignoring __pycache__ and .git) before each author/repair start and compare it
after the start finishes; any difference is recorded durably and blocks review and closure of that transaction.
"""
import shutil
from pathlib import Path
import sys
import unittest

HAVE_SANDBOX = shutil.which('bwrap') is not None and sys.executable.startswith(('/usr/', '/bin/'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_controller_transaction as tx  # noqa: E402  (frozen S3-3 fixture, reused read-only)


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class ProjectConfinementTests(tx.TransactionFixture):
    def violate_next_author(self, relative='pkg/__init__.py', content='HELPER = "rigged"\n'):
        original = self.fb.interpret

        def interpret(spec, capture, *args, **kwargs):
            result = original(spec, capture, *args, **kwargs)
            if not spec.requires_review_sandbox:
                (self.project / relative).write_text(content)
            return result
        self.fb.interpret = interpret

    def test_change_outside_owned_paths_blocks_review_and_close(self):
        self.begin()
        self.violate_next_author()
        self.author(tx.FIX)
        self.refused('outside owned', 'review')
        self.refused('outside owned', 'close')

    def test_violation_survives_a_controller_restart(self):
        self.begin()
        self.violate_next_author(relative='extra_module.py', content='X = 1\n')
        self.author(tx.FIX)
        self.restart()
        self.refused('outside owned', 'review')

    def test_owned_changes_and_caches_are_not_violations(self):
        self.begin()
        cache = self.project / 'pkg' / '__pycache__'
        cache.mkdir(exist_ok=True)
        (cache / 'core.cpython.pyc').write_bytes(b'cache')
        self.author(tx.FIX)
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')


if __name__ == '__main__':
    unittest.main()
