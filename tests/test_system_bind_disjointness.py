# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S8-H1: the built-in system binds are part of the reviewer mount check.

Final S7 documentation review (High): the reviewer sandbox always binds `/usr`, `/bin`, `/lib` and `/lib64` read-only,
but the controller's overlap check (S7-R2) covered only the LaunchSpec mounts, so a project or state root under, e.g.,
`/usr/local` was visible to the network-connected reviewer. Fix:
- `supervise.kernel.review_view.SYSTEM_BINDS` is the single tuple of built-in read-only bind paths; `reviewer_argv` and
  `verifier_argv` bind exactly the existing entries of it (patching it changes what they bind);
- before reserving a review start, the controller refuses (ValueError) when the project, the invocation directory or
  the state root equals or lies inside the resolved real path of any existing SYSTEM_BINDS entry, in addition to the
  existing check of the LaunchSpec mounts. No review start is reserved.
"""
import shutil
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

HAVE_SANDBOX = shutil.which('bwrap') is not None and sys.executable.startswith(('/usr/', '/bin/'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_controller_transaction as tx  # noqa: E402  (frozen fixture, reused read-only)

from supervise.kernel import review_view  # noqa: E402


class SystemBindConstantTests(unittest.TestCase):
    def test_constant_lists_the_built_in_binds(self):
        self.assertEqual(tuple(review_view.SYSTEM_BINDS), ('/usr', '/bin', '/lib', '/lib64'))


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class SystemBindDisjointnessTests(tx.TransactionFixture):
    def review_starts(self):
        return [c for c in self.recorder.calls if c['kind'] != 'author']

    def test_project_under_a_system_bind_is_refused(self):
        self.begin()
        self.author(tx.FIX)
        patched = tuple(review_view.SYSTEM_BINDS) + (str(self.project.parent),)
        before = len(self.starts())
        with mock.patch.object(review_view, 'SYSTEM_BINDS', patched):
            with self.assertRaises(ValueError):
                self.do('review')
        self.assertEqual(len(self.starts()), before)
        self.assertEqual(self.review_starts(), [])

    def test_state_under_a_system_bind_is_refused(self):
        self.begin()
        self.author(tx.FIX)
        patched = tuple(review_view.SYSTEM_BINDS) + (str(self.home.parent),)
        with mock.patch.object(review_view, 'SYSTEM_BINDS', patched):
            with self.assertRaises(ValueError):
                self.do('review')
        self.assertEqual(self.review_starts(), [])

    def test_patched_binds_reach_the_sandbox_command(self):
        extra = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, extra, True)
        self.begin()
        self.author(tx.FIX)
        patched = tuple(review_view.SYSTEM_BINDS) + (str(extra),)
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')
        view = Path(sorted(self.home.glob('views/*'))[-1])
        with mock.patch.object(review_view, 'SYSTEM_BINDS', patched):
            identity = view.name.split('--')[0]
            command = review_view.reviewer_argv(view, ['/usr/bin/true'], expected_identity=identity, mounts=[],
                                                home='/run/reviewer-home')
        self.assertIn(['--ro-bind', str(extra), str(extra)], [command[k:k + 3] for k in range(len(command) - 2)])


if __name__ == '__main__':
    unittest.main()
