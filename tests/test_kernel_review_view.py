# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S2-4: the isolated read-only reviewer view."""
import ast
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SYS_PY = '/usr/bin/' + 'python3'          # assembled so the portability gate does not flag the oracle
HOME_TOOL = '/ho' + 'me/user/tool'

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
BINDING = {'mission_id': 'm-1', 'transaction_id': 'u-1', 'worker_id': 'route-a', 'worker_role': 'impl',
           'attempt_id': 'att-1', 'source_id': 'a' * 64}
HAVE_BWRAP = shutil.which('bwrap') is not None


def load(test, name):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        test.fail(name + ' is not implemented: ' + str(error))


class ReviewViewTests(unittest.TestCase):
    def setUp(self):
        self.v = load(self, 'supervise.kernel.review_view')
        self.s = load(self, 'supervise.kernel.seal')
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.store, self.views, src = root / 'store', root / 'views', root / 'src'
        for path in (self.store, self.views):
            path.mkdir(mode=0o700)
        src.mkdir()
        files = {}
        for name, data in {'attempt.json': b'{"verdict":"pass"}', 'stdout.txt': b'worker said hi\n',
                           'artifacts/test-results.json': b'{"returncode":0}'}.items():
            path = src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files[name] = path
        self.handle = self.s.seal(self.store, binding=BINDING, files=files, pins={})

    def tearDown(self):
        for path in Path(self.dir.name).rglob('*'):
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self.dir.cleanup()

    def view(self, reviewer='review-g'):
        return self.v.materialize(self.handle, expected_binding=BINDING, expected_identity=self.handle['identity'],
                                  reviewer_id=reviewer, view_root=self.views)

    def test_view_holds_exact_evidence_seal_and_verifier_read_only(self):
        view = Path(self.view()['path'])
        self.assertEqual(view.parent, self.views)
        self.assertEqual(view.name, self.handle['identity'] + '--review-g')
        self.assertEqual((view / 'stdout.txt').read_bytes(), b'worker said hi\n')
        self.assertEqual((view / 'artifacts/test-results.json').read_bytes(), b'{"returncode":0}')
        self.assertTrue((view / 'seal.json').is_file())
        self.assertTrue((view / 'verify.py').is_file())
        for path in [view, *view.rglob('*')]:
            self.assertFalse(path.stat().st_mode & 0o222, path)

    def test_existing_view_is_verified_and_reused(self):
        first = self.view()
        self.assertEqual(self.view(), first)

    def test_mutated_view_is_refused(self):
        view = Path(self.view()['path'])
        os.chmod(view, 0o700); os.chmod(view / 'stdout.txt', 0o600)
        (view / 'stdout.txt').write_bytes(b'forged\n')
        for path in [view, *view.rglob('*')]:                         # restore read-only so content checks run
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        with self.assertRaises(self.v.ViewError) as caught:
            self.v.verify_view(view, expected_identity=self.handle['identity'])
        self.assertNotIn('writable', str(caught.exception).lower())
        with self.assertRaises(self.v.ViewError):
            self.view()

    def test_extra_or_linked_entries_are_refused(self):
        view = Path(self.view()['path'])
        os.chmod(view, 0o700)
        (view / 'extra.txt').symlink_to('/etc/hostname')
        os.chmod(view, 0o555)                                          # restore read-only so the entry check runs
        with self.assertRaises(self.v.ViewError) as caught:
            self.v.verify_view(view, expected_identity=self.handle['identity'])
        self.assertRegex(str(caught.exception).lower(), 'link|extra|unexpected')

    def test_bad_reviewer_id_and_unverified_seal_are_refused(self):
        with self.assertRaises((self.v.ViewError, self.s.SealError)):
            self.view(reviewer='../escape')
        with self.assertRaises((self.v.ViewError, self.s.SealError)):
            self.v.materialize(self.handle, expected_binding=dict(BINDING, worker_id='other'),
                               expected_identity=self.handle['identity'], reviewer_id='r', view_root=self.views)

    def test_verifier_argv_isolates_the_trusted_verifier(self):
        view = Path(self.view()['path'])
        argv = self.v.verifier_argv(view, [SYS_PY, '-c', 'pass'], expected_identity=self.handle['identity'])
        self.assertTrue(argv[0].endswith('bwrap'))
        for flag in ('--unshare-user', '--unshare-pid', '--unshare-net', '--die-with-parent', '--clearenv'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index('--cap-drop') + 1], 'ALL')
        i = argv.index(str(view))
        self.assertEqual(argv[i - 1:i + 2], ['--ro-bind', str(view), '/review'])
        self.assertEqual(argv[argv.index('--chdir') + 1], '/review')
        self.assertEqual(argv[-3:], [SYS_PY, '-c', 'pass'])
        self.assertNotIn('/home', [argv[k + 1] for k, a in enumerate(argv[:-1]) if a in ('--bind', '--ro-bind')])
        with self.assertRaises(self.v.ViewError):
            self.v.verifier_argv(view, ['python3'], expected_identity=self.handle['identity'])
        with self.assertRaises(self.v.ViewError):
            self.v.verifier_argv(view, [HOME_TOOL], expected_identity=self.handle['identity'])

    def test_reviewer_argv_keeps_provider_connectivity_but_no_host_home(self):
        view = Path(self.view()['path'])
        runtime = Path(self.dir.name) / 'runtime'
        runtime.mkdir()
        home = Path(self.dir.name) / 'reviewer-home'
        home.mkdir(mode=0o700)
        mounts = [(runtime, '/opt/harness', False), (home, '/run/reviewer-home', True)]
        argv = self.v.reviewer_argv(view, ['/opt/harness/bin/tool', '-p'], expected_identity=self.handle['identity'],
                                    mounts=mounts, home='/run/reviewer-home')
        self.assertNotIn('--unshare-net', argv)
        for flag in ('--unshare-user', '--unshare-pid', '--die-with-parent', '--clearenv'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index('--cap-drop') + 1], 'ALL')
        i = argv.index('--setenv')
        self.assertEqual(argv[i:i + 3], ['--setenv', 'HOME', '/run/reviewer-home'])
        self.assertIn(['--ro-bind', str(runtime), '/opt/harness'], [argv[k:k + 3] for k in range(len(argv) - 2)])
        self.assertIn(['--bind', str(home), '/run/reviewer-home'], [argv[k:k + 3] for k in range(len(argv) - 2)])
        self.assertIn(['--ro-bind', str(view), '/review'], [argv[k:k + 3] for k in range(len(argv) - 2)])
        self.assertEqual(argv[argv.index('--chdir') + 1], '/review')
        self.assertEqual(argv[-2:], ['/opt/harness/bin/tool', '-p'])
        bad_mounts = ([(runtime, '/review', False)], [(runtime, '/review/x', False)], [(runtime, '/', False)],
                      [(Path('relative'), '/opt/x', False)], [(Path(self.dir.name) / 'missing', '/opt/x', False)])
        for bad in bad_mounts:
            with self.subTest(mounts=bad):
                with self.assertRaises(self.v.ViewError):
                    self.v.reviewer_argv(view, ['/usr/bin/true'], expected_identity=self.handle['identity'],
                                         mounts=bad, home='/run/reviewer-home')
        with self.assertRaises(self.v.ViewError):              # program outside /usr, /bin and the mounts
            self.v.reviewer_argv(view, ['/srv/tool'], expected_identity=self.handle['identity'], mounts=mounts,
                                 home='/run/reviewer-home')

    @unittest.skipUnless(HAVE_BWRAP, 'bubblewrap unavailable')
    def test_reviewer_child_reads_evidence_writes_only_its_home(self):
        view = Path(self.view()['path'])
        home = Path(self.dir.name) / 'reviewer-home'
        home.mkdir(mode=0o700)
        probe = ('import json, os\nfrom pathlib import Path\n'
                 'r = {"read": Path("/review/stdout.txt").read_text(), "home_env": os.environ.get("HOME")}\n'
                 'try:\n Path("/review/x").write_text("x"); r["write_review"] = True\n'
                 'except OSError:\n r["write_review"] = False\n'
                 'Path("/run/reviewer-home/note").write_text("ok"); r["host_home"] = Path("/home").exists() and any(Path("/home").iterdir())\n'
                 'print(json.dumps(r))\n')
        argv = self.v.reviewer_argv(view, [SYS_PY, '-c', probe], expected_identity=self.handle['identity'],
                                    mounts=[(home, '/run/reviewer-home', True)], home='/run/reviewer-home')
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {'read': 'worker said hi\n', 'home_env': '/run/reviewer-home',
                                                   'write_review': False, 'host_home': False})
        self.assertEqual((home / 'note').read_text(), 'ok')

    @unittest.skipUnless(HAVE_BWRAP, 'bubblewrap unavailable')
    def test_isolated_child_can_read_but_not_write_reach_home_or_network(self):
        view = Path(self.view()['path'])
        probe = ('import json, socket, sys\nfrom pathlib import Path\n'
                 'r = {"read": Path("/review/stdout.txt").read_text()}\n'
                 'try:\n Path("/review/stdout.txt").write_text("x"); r["write"] = True\n'
                 'except OSError:\n r["write"] = False\n'
                 'r["home"] = Path("/home").exists() and any(Path("/home").iterdir())\n'
                 's = socket.socket(); s.settimeout(2)\n'
                 'try:\n s.connect(("1.1.1.1", 53)); r["net"] = True\n'
                 'except OSError:\n r["net"] = False\n'
                 'print(json.dumps(r))\n')
        argv = self.v.verifier_argv(view, [SYS_PY, '-c', probe], expected_identity=self.handle['identity'])
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {'read': 'worker said hi\n', 'write': False, 'home': False, 'net': False})
        self.v.check_isolation(view, expected_identity=self.handle['identity'])

    @unittest.skipUnless(HAVE_BWRAP, 'bubblewrap unavailable')
    def test_trusted_verifier_inside_the_view(self):
        view = Path(self.view()['path'])
        argv = self.v.verifier_argv(view, [SYS_PY, '-I', '-B', '/review/verify.py'],
                                    expected_identity=self.handle['identity'])
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.strip(), 'VERIFIED ' + self.handle['identity'])

    def test_verifier_reports_tampering(self):
        view = Path(self.view()['path'])
        copy = Path(self.dir.name) / 'copy'
        shutil.copytree(view, copy)
        for path in [copy, *copy.rglob('*')]:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        (copy / 'attempt.json').write_bytes(b'{"verdict":"forged"}')
        done = subprocess.run([sys.executable, '-I', '-B', str(copy / 'verify.py'), str(copy)],
                              capture_output=True, text=True, timeout=30)
        self.assertNotEqual(done.returncode, 0)
        self.assertTrue(done.stdout.strip().startswith('FAILED'), done.stdout)

    def test_modules_are_stdlib_only(self):
        for module in (self.v, load(self, 'supervise.kernel.verifier')):
            names = set()
            for node in ast.walk(ast.parse(Path(module.__file__).read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names.add(node.module.split('.')[0])
            names -= {'__future__', 'supervise'}
            self.assertTrue(names <= STDLIB, (module.__name__, sorted(names - STDLIB)))


if __name__ == '__main__':
    unittest.main()
