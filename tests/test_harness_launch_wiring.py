# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S3-L1: the controller must wire real harness launches (found by the S6
pre-flight in a fresh VM, invisible to stub-backend tests).

- Workers read their packet on stdin (Codex `exec ... -`, Claude `-p`), but the controller launched them with no stdin:
  Codex exited "No prompt provided via stdin". The controller passes the stored packet file as stdin to every start.
- The review sandbox ignored the LaunchSpec's declared mounts (S4-0 contract) and used a host path inside the
  invocation home as the reviewer's HOME, so a real reviewer had neither its runtime nor its login: "reviewer executable
  is outside its runtime mounts". The controller passes the spec's mounts to `review_view.reviewer_argv` and uses the
  spec's HOME when it is an absolute container path, otherwise a fixed container path; never a host path.
"""
import dataclasses
import shutil
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

HAVE_SANDBOX = shutil.which('bwrap') is not None and sys.executable.startswith(('/usr/', '/bin/'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_controller_transaction as tx  # noqa: E402  (frozen fixture, reused read-only)

from supervise import backend as backend_api  # noqa: E402
from supervise.controller import transaction as T  # noqa: E402
from supervise.kernel import process, review_view  # noqa: E402


def records(mounts):
    out = []
    for m in mounts:
        if hasattr(m, 'target'):
            out.append((str(m.source), m.target, bool(m.writable)))
        else:
            out.append((str(m[0]), m[1], bool(m[2])))
    return out


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class HarnessLaunchWiringTests(tx.TransactionFixture):
    def test_worker_receives_its_packet_on_stdin(self):
        self.begin()
        with mock.patch.object(T.process, 'run', wraps=process.run) as run:
            self.author(tx.FIX)
        stdin = run.call_args_list[0].kwargs.get('stdin_path')
        self.assertIsNotNone(stdin, 'worker launched without its packet on stdin')
        self.assertEqual(Path(stdin).read_text(), self.ctl.admitted['packet']['text'])

    def declare_review_runtime(self, env_home):
        runtime = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, runtime, True)
        original = self.fb.stub.prepare_launch

        def prepare(request):
            spec = original(request)
            if request.role == 'review':
                env = dict(spec.env)
                if env_home is not None:
                    env['HOME'] = env_home
                spec = dataclasses.replace(spec, env=env, mounts=(
                    backend_api.Mount(source=runtime, target='/opt/harness-runtime', writable=False),))
            return spec
        self.fb.stub.prepare_launch = prepare
        return runtime

    def review_call(self):
        with mock.patch.object(T.review_view, 'reviewer_argv', wraps=review_view.reviewer_argv) as built, \
                mock.patch.object(T.process, 'run', wraps=process.run) as run:
            result = self.do('review')
        return result, built.call_args.kwargs, run

    def test_review_sandbox_gets_the_declared_mounts_and_home(self):
        self.begin()
        self.author(tx.FIX)
        runtime = self.declare_review_runtime('/sandbox/home')
        result, kwargs, run = self.review_call()
        self.assertIn((runtime, '/opt/harness-runtime', False), records(kwargs['mounts']))
        self.assertEqual(kwargs['home'], '/sandbox/home')
        self.assertEqual(result['status'], 'REVIEW_PASSED', result)
        review_runs = [c for c in run.call_args_list if c.kwargs.get('stdin_path') is not None]
        self.assertTrue(review_runs, 'reviewer launched without its packet on stdin')

    def test_review_home_is_never_a_host_path(self):
        self.begin()
        self.author(tx.FIX)
        self.declare_review_runtime(None)
        _, kwargs, _ = self.review_call()
        home = kwargs['home']
        self.assertTrue(home.startswith('/'))
        self.assertFalse(home.startswith(str(self.home)), home)
        self.assertFalse(home.startswith(str(Path.home())), home)


if __name__ == '__main__':
    unittest.main()
