# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for the S2 sprint-review remediation round 1 (findings M2, M3 and verdict Low).

Independent S2 sprint review (DeepSeek, router-run in execution/S2/sprint-review/REVIEW.md) confirmed by the Chief:
- M2: the trusted in-view verifier printed VERIFIED for a view with an injected extra entry.
- M3: a descendant of a normally exiting worker survived `process.run` (process group not reaped on normal exit).
- Low: `parse_verdict` accepted `REVIEW VERDICT: ACCEPTED` as an ACCEPT marker (no word boundary).
"""
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

BINDING = {'mission_id': 'm-1', 'transaction_id': 'u-1', 'worker_id': 'route-a', 'worker_role': 'impl',
           'attempt_id': 'att-1', 'source_id': 'a' * 64}


def alive(pid):
    stat = Path(f'/proc/{pid}/stat')
    if not stat.exists():
        return False
    return not stat.read_text().rsplit(')', 1)[1].split()[0].startswith('Z')


class VerifierEntryTests(unittest.TestCase):
    def setUp(self):
        self.s = importlib.import_module('supervise.kernel.seal')
        self.v = importlib.import_module('supervise.kernel.review_view')
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        (root / 'store').mkdir(mode=0o700)
        (root / 'views').mkdir(mode=0o700)
        src = root / 'stdout.txt'
        src.write_bytes(b'hello\n')
        handle = self.s.seal(root / 'store', binding=BINDING, files={'stdout.txt': src}, pins={})
        view = Path(self.v.materialize(handle, expected_binding=BINDING, expected_identity=handle['identity'],
                                       reviewer_id='r', view_root=root / 'views')['path'])
        self.copy = root / 'copy'
        shutil.copytree(view, self.copy)
        for path in [self.copy, *self.copy.rglob('*')]:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)

    def tearDown(self):
        for path in Path(self.dir.name).rglob('*'):
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self.dir.cleanup()

    def verify(self):
        return subprocess.run([sys.executable, '-I', '-B', str(self.copy / 'verify.py'), str(self.copy)],
                              capture_output=True, text=True, timeout=30)

    def test_untouched_copy_still_verifies(self):
        done = self.verify()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertTrue(done.stdout.startswith('VERIFIED '))

    def test_injected_extra_file_fails(self):
        (self.copy / 'injected.txt').write_text('extra')
        done = self.verify()
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue(done.stdout.strip().startswith('FAILED'), done.stdout)

    def test_injected_extra_directory_or_link_fails(self):
        (self.copy / 'extra-dir').mkdir()
        self.assertNotEqual(self.verify().returncode, 0)
        shutil.rmtree(self.copy / 'extra-dir')
        (self.copy / 'link.txt').symlink_to(self.copy / 'stdout.txt')
        self.assertNotEqual(self.verify().returncode, 0)


class ProcessReapTests(unittest.TestCase):
    def setUp(self):
        self.p = importlib.import_module('supervise.kernel.process')
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_descendants_are_reaped_after_a_normal_exit(self):
        pidfile = self.root / 'descendant.pid'
        code = ('import subprocess, sys\n'
                'g = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
                f'open({str(pidfile)!r}, "w").write(str(g.pid))\n'
                'print("done")\n')
        capture = self.p.run((sys.executable, '-c', code), env={}, cwd=self.root, timeout=30.0, grace=1.0,
                             stdout_path=self.root / 'o', stderr_path=self.root / 'e', on_spawn=None)
        self.assertEqual(capture.exit_code, 0)
        self.assertFalse(capture.timed_out)
        descendant = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while alive(descendant) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(descendant), 'descendant outlived the supervised command')

    def test_capture_hash_covers_output_written_by_descendants_before_reaping(self):
        code = ('import subprocess, sys\n'
                'subprocess.Popen([sys.executable, "-c", "import sys, time; time.sleep(0.5); print(\\"late\\", flush=True)"])\n'
                'print("early", flush=True)\n')
        capture = self.p.run((sys.executable, '-c', code), env={}, cwd=self.root, timeout=30.0, grace=2.0,
                             stdout_path=self.root / 'o2', stderr_path=self.root / 'e2', on_spawn=None)
        data = capture.stdout_path.read_bytes()
        import hashlib
        self.assertEqual(capture.stdout_sha256, hashlib.sha256(data).hexdigest())
        time.sleep(1.0)
        self.assertEqual(capture.stdout_path.read_bytes(), data, 'capture changed after run() returned')


class VerdictBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.v = importlib.import_module('supervise.kernel.verdict')

    def test_marker_requires_a_word_boundary(self):
        self.assertIsNone(self.v.parse_verdict('REVIEW VERDICT: ACCEPTED', b'REVIEW VERDICT: ACCEPTED'))
        self.assertIsNone(self.v.parse_verdict('REVIEW VERDICT: REJECTION', b'REVIEW VERDICT: REJECTION'))
        self.assertEqual(self.v.parse_verdict('ok\nREVIEW VERDICT: ACCEPT', b'REVIEW VERDICT: ACCEPT"}'), 'accept')
        self.assertEqual(self.v.parse_verdict('REVIEW VERDICT: REJECT.', b'REVIEW VERDICT: REJECT.'), 'reject')


if __name__ == '__main__':
    unittest.main()
