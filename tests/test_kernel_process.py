# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S2-2: bounded process execution and identity."""
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
PY = sys.executable


def proc(test):
    try:
        return importlib.import_module('supervise.kernel.process')
    except ImportError as error:
        test.fail('Kernel process execution is not implemented: ' + str(error))


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f'/proc/{pid}/stat')
    return not (stat.exists() and stat.read_text().split(') ')[-1].startswith('Z'))


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.p = proc(self)
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def run_child(self, code, *, env=None, timeout=20.0, grace=1.0, on_spawn=None, name='run'):
        return self.p.run((PY, '-c', code), env=env or {}, cwd=self.root, timeout=timeout, grace=grace,
                          stdout_path=self.root / (name + '.out'), stderr_path=self.root / (name + '.err'),
                          on_spawn=on_spawn)

    def test_capture_matches_backend_contract_with_hashes(self):
        backend = importlib.import_module('supervise.backend')
        capture = self.run_child('import sys; print("hello"); print("oops", file=sys.stderr); sys.exit(3)')
        self.assertIsInstance(capture, backend.ProcessCapture)
        self.assertEqual(capture.exit_code, 3)
        self.assertFalse(capture.timed_out)
        self.assertEqual(capture.stdout_path.read_bytes(), b'hello\n')
        self.assertEqual(capture.stderr_path.read_bytes(), b'oops\n')
        self.assertEqual(capture.stdout_sha256, hashlib.sha256(b'hello\n').hexdigest())
        self.assertEqual(capture.stderr_sha256, hashlib.sha256(b'oops\n').hexdigest())
        self.assertGreaterEqual(capture.seconds, 0)

    def test_environment_is_exactly_the_allowlist(self):
        os.environ['SUPERVISE_AMBIENT_SECRET'] = 'leak'
        try:
            capture = self.run_child('import os, json; print(json.dumps(dict(os.environ)))', env={'ONLY': 'this'})
        finally:
            os.environ.pop('SUPERVISE_AMBIENT_SECRET')
        seen = json.loads(capture.stdout_path.read_text())
        self.assertEqual({k: v for k, v in seen.items() if k not in {'LC_CTYPE'}}, {'ONLY': 'this'})

    def test_relative_program_and_bad_bounds_are_refused(self):
        for kwargs in ({'argv': ('python3', '-c', 'pass')}, {'timeout': 0}, {'timeout': float('inf')},
                       {'timeout': float('nan')}, {'grace': -1.0}):
            with self.subTest(kwargs=kwargs):
                values = dict(argv=(PY, '-c', 'pass'), env={}, cwd=self.root, timeout=5.0, grace=1.0,
                              stdout_path=self.root / 'x.out', stderr_path=self.root / 'x.err', on_spawn=None)
                values.update(kwargs)
                with self.assertRaises(self.p.ExecRefused):
                    self.p.run(**values)
                self.assertFalse((self.root / 'x.out').exists())

    def test_existing_capture_files_are_never_overwritten(self):
        (self.root / 'run.out').write_text('previous')
        with self.assertRaises(self.p.ExecRefused):
            self.run_child('print(1)')
        self.assertEqual((self.root / 'run.out').read_text(), 'previous')

    def test_timeout_terminates_the_whole_process_group(self):
        pidfile = self.root / 'grandchild.pid'
        code = ('import subprocess, sys, time\n'
                f'g = subprocess.Popen([{PY!r}, "-c", "import time; time.sleep(60)"])\n'
                f'open({str(pidfile)!r}, "w").write(str(g.pid))\n'
                'time.sleep(60)\n')
        started = time.monotonic()
        capture = self.run_child(code, timeout=2.0, grace=1.0)
        elapsed = time.monotonic() - started
        self.assertTrue(capture.timed_out)
        self.assertIsNone(capture.exit_code)
        self.assertLess(elapsed, 15)
        self.assertGreaterEqual(capture.seconds, 2.0)
        grandchild = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(grandchild), 'grandchild survived the group kill')

    def test_sigterm_ignoring_child_is_killed_after_grace(self):
        code = 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print("ready", flush=True); time.sleep(60)'
        started = time.monotonic()
        capture = self.run_child(code, timeout=1.5, grace=1.0)
        self.assertTrue(capture.timed_out)
        self.assertLess(time.monotonic() - started, 12)

    def test_stdin_is_the_given_file_or_empty(self):
        packet = self.root / 'packet.md'
        packet.write_text('the task packet')
        capture = self.p.run((PY, '-c', 'import sys; print(sys.stdin.read())'), env={}, cwd=self.root, timeout=20.0,
                             grace=1.0, stdout_path=self.root / 'in.out', stderr_path=self.root / 'in.err', on_spawn=None,
                             stdin_path=packet)
        self.assertEqual(capture.stdout_path.read_text(), 'the task packet\n')
        capture = self.run_child('import sys; print(repr(sys.stdin.read()))', name='empty')
        self.assertEqual(capture.stdout_path.read_text(), "''\n")

    def test_spawn_identity_is_reported_before_completion(self):
        seen = []
        capture = self.run_child('import os; print(os.getpid())', on_spawn=seen.append)
        self.assertEqual(len(seen), 1)
        identity = seen[0]
        self.assertEqual(identity['pid'], int(capture.stdout_path.read_text()))
        self.assertIsInstance(identity['start_time'], int)
        self.assertEqual(identity['boot'], self.p.boot_id())
        self.assertEqual(set(identity), {'pid', 'start_time', 'boot'})

    def test_process_identity_reads_proc(self):
        identity = self.p.process_identity(os.getpid())
        fields = Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()
        self.assertEqual(identity['start_time'], int(fields[19]))
        self.assertEqual(identity['pid'], os.getpid())
        self.assertIsNone(self.p.process_identity(2 ** 22 + 12345))

    def test_process_module_is_stdlib_only(self):
        names = set()
        for node in ast.walk(ast.parse(Path(self.p.__file__).read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names -= {'__future__', 'supervise'}
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))


if __name__ == '__main__':
    unittest.main()
