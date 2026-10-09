# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S2-L1: the connected reviewer sandbox can resolve provider hostnames.

Live in a fresh Ubuntu VM, an authenticated Claude reviewer started inside the review sandbox and failed with
"Can't reach the API server (EAI_AGAIN)": the sandbox binds only /usr, /bin, /lib and /lib64, so it has no resolver
configuration. The reviewer sandbox (provider connectivity by design) additionally binds, read-only and only when they
exist: /etc/resolv.conf (bound from its resolved real path, because it is commonly a symlink into /run), /etc/hosts,
/etc/nsswitch.conf and the CA certificate directory /etc/ssl. It never binds /etc itself or any other /etc file. The
network-isolated verifier sandbox gets none of these.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_kernel_review_view as rv  # noqa: E402  (frozen fixture, reused read-only)

NAME_FILES = ('/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf')


def triples(argv):
    return [argv[k:k + 3] for k in range(len(argv) - 2)]


def bind_targets(argv):
    return [argv[k + 2] for k in range(len(argv) - 2) if argv[k] in ('--bind', '--ro-bind', '--bind-try', '--ro-bind-try')]


class ReviewerNameResolutionTests(unittest.TestCase):
    setUp = rv.ReviewViewTests.setUp
    tearDown = rv.ReviewViewTests.tearDown
    view = rv.ReviewViewTests.view

    def reviewer(self, command=('/usr/bin/true',)):
        view = Path(self.view()['path'])
        home = Path(self.dir.name) / 'reviewer-home'
        home.mkdir(mode=0o700, exist_ok=True)
        return self.v.reviewer_argv(view, list(command), expected_identity=self.handle['identity'],
                                    mounts=[(home, '/run/reviewer-home', True)], home='/run/reviewer-home')

    def test_reviewer_binds_resolver_files_read_only(self):
        argv = self.reviewer()
        for path in NAME_FILES:
            if os.path.exists(path):
                self.assertIn(['--ro-bind', os.path.realpath(path), path], triples(argv), path)
        if os.path.isdir('/etc/ssl'):
            self.assertIn(['--ro-bind', os.path.realpath('/etc/ssl'), '/etc/ssl'], triples(argv))

    def test_reviewer_never_binds_etc_wholesale_or_other_etc_files(self):
        allowed = set(NAME_FILES) | {'/etc/ssl'}
        for target in bind_targets(self.reviewer()):
            if target == '/etc' or target.startswith('/etc/'):
                self.assertIn(target, allowed)

    def test_verifier_gets_no_etc_files(self):
        view = Path(self.view()['path'])
        argv = self.v.verifier_argv(view, [rv.SYS_PY, '-c', 'pass'], expected_identity=self.handle['identity'])
        self.assertFalse([t for t in bind_targets(argv) if t == '/etc' or t.startswith('/etc/')])

    @unittest.skipUnless(rv.HAVE_BWRAP and os.path.exists('/etc/hosts'), 'bubblewrap or /etc/hosts unavailable')
    def test_reviewer_child_sees_the_resolver_configuration(self):
        probe = ('import json\nfrom pathlib import Path\n'
                 'print(json.dumps({p: Path(p).read_text() for p in ("/etc/hosts",) + tuple(x for x in '
                 + repr(NAME_FILES[:1]) + ' if Path(x).exists())}))\n')
        done = subprocess.run(self.reviewer((rv.SYS_PY, '-c', probe)), capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        seen = json.loads(done.stdout)
        self.assertEqual(seen['/etc/hosts'], Path('/etc/hosts').read_text())
        if os.path.exists('/etc/resolv.conf'):
            self.assertEqual(seen.get('/etc/resolv.conf'), Path('/etc/resolv.conf').read_text())


if __name__ == '__main__':
    unittest.main()
