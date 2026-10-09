# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S7-R2: reviewer mounts must be disjoint from the project and the state.

Round-2 documentation review (residual H1): mount sources are refused only when they are `/`, the home directory or its
ancestors. A CODEX_HOME / CLAUDE_CONFIG_DIR (mounted writable) or a harness install directory (mounted read-only) that
contains, or lies inside, the project or the state root would expose unsealed project files or writable invocation
evidence to the network-connected reviewer. Before reserving a review start, the controller refuses (ValueError naming
the overlap) any LaunchSpec mount whose resolved source equals, contains or lies inside the project directory, the
invocation directory or the state root (the invocation directory's parent). No review start is reserved.
"""
import dataclasses
import shutil
import sys
import tempfile
from pathlib import Path
import unittest

HAVE_SANDBOX = shutil.which('bwrap') is not None and sys.executable.startswith(('/usr/', '/bin/'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_controller_transaction as tx  # noqa: E402  (frozen fixture, reused read-only)

from supervise import backend as backend_api  # noqa: E402


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class ReviewMountDisjointnessTests(tx.TransactionFixture):
    def declare(self, source, writable=False):
        original = self.fb.stub.prepare_launch

        def prepare(request):
            spec = original(request)
            if request.role == 'review':
                spec = dataclasses.replace(spec, mounts=(
                    backend_api.Mount(source=str(source), target='/opt/harness-runtime', writable=writable),))
            return spec
        self.fb.stub.prepare_launch = prepare

    def review_starts(self):
        return [c for c in self.recorder.calls if c['kind'] != 'author']

    def refused_review(self, choose, writable=False):
        self.begin()
        self.author(tx.FIX)
        self.declare(choose(), writable)
        before = len(self.starts())
        with self.assertRaises(ValueError) as caught:
            self.do('review')
        self.assertEqual(len(self.starts()), before, 'a review start was reserved')
        self.assertEqual(self.review_starts(), [])
        return str(caught.exception)

    def test_mount_of_the_project_is_refused(self):
        self.refused_review(lambda: self.project)

    def test_mount_inside_the_project_is_refused(self):
        self.refused_review(lambda: self.project / 'pkg')

    def test_mount_containing_the_project_is_refused(self):
        self.refused_review(lambda: self.project.parent, writable=True)

    def test_mount_of_the_invocation_or_state_root_is_refused(self):
        self.refused_review(lambda: self.home.parent, writable=True)

    def test_disjoint_mount_is_accepted(self):
        self.begin()
        self.author(tx.FIX)
        runtime = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, runtime, True)
        self.declare(runtime)
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')


if __name__ == '__main__':
    unittest.main()
