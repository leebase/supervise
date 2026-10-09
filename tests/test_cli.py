# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S5-5: `supervise.cli.main(argv) -> int` and `python -m supervise`.

Exit codes assumed (see NOTES.md): 0 success / PASS / no doctor FAIL; 1 verify FAIL or doctor FAIL; 2 usage
error, refusal (Controller ValueError, InstallError) or invalid intent JSON. argparse may raise SystemExit for
usage errors; the oracle treats SystemExit(code) like a returned code.

Seams assumed (module attributes looked up at call time):
* `supervise.cli.make_controller(home)` builds the Controller for `intent` (patched here to use a StubBackend).
  Its publication_sha256 comes from INSTALLATION.json under the data root (Chief ruling), read through
  `supervise.cli.installed_publication_sha256()`, which raises ValueError when no installation is recorded.
* `supervise.verify.verify_invocation`, `supervise.doctor.run_checks`, `supervise.installer.install` and
  `supervise.installer.uninstall` are called through their modules, so patching the module attribute works.

Install flags assumed: `install --source DIR [--data-root DIR] [--pointer PATH] [--release NAME]` and
`uninstall [--data-root DIR] [--pointer PATH]`; without `--data-root` the data root is $SUPERVISE_DATA, else
$XDG_DATA_HOME/supervise, else ~/.local/share/supervise.
"""
import ast
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
PY = sys.executable
HAVE_SANDBOX = shutil.which('bwrap') is not None and PY.startswith(('/usr/', '/bin/'))
PUB = 'e' * 64
SECRET = 'sk-' + 'clitest' + '0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e'
SUBCOMMANDS = ('intent', 'verify', 'doctor', 'install', 'uninstall', 'run')
TESTS_PY = textwrap.dedent('''
    import unittest
    from pkg import core


    class CoreTests(unittest.TestCase):
        def test_value(self):
            self.assertEqual(core.VALUE, 1)

        def test_values(self):
            self.assertEqual(getattr(core, 'OTHER', None), 3)
''')
VALUES = 'test_core.CoreTests.test_values'
FIX = 'VALUE = 1\nOTHER = 3\n'


def field(name, value):
    return name + ': ' + value


PACKET = '\n'.join([field('Kind', 'impl'), field('Owned paths', 'pkg/core.py'),
                    field('Declared size', '1 file, 2 changed lines'),
                    field('Oracle', 'python3 -m unittest discover -s tests'), field('Domain', 'none'),
                    field('Review', 'independent reviewer'), '', 'Make OTHER three in pkg/core.py.'])


def load(test, name='supervise.cli'):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        test.fail(name + ' is not implemented: ' + str(error))


class FixtureBackend:
    def __init__(self, stub, project):
        self.stub, self.project, self.roles, self.launches = stub, project, {}, []

    def identity(self):
        return self.stub.identity()

    def plan_crew(self, request):
        return self.stub.plan_crew(request)

    def check_grants(self, route, commands):
        return self.stub.check_grants(route, commands)

    def preflight_project(self, route, project):
        return self.stub.preflight_project(route, project)

    def prepare_launch(self, request):
        spec = self.stub.prepare_launch(request)
        self.roles[id(spec)] = request.role
        self.launches.append((request.route.route_id, request.role))
        return spec

    def interpret(self, spec, capture):
        outcome = self.stub.interpret(spec, capture)
        if self.roles.get(id(spec)) == 'impl':
            (self.project / 'pkg' / 'core.py').write_text(FIX)
        return outcome


class CliFixture(unittest.TestCase):
    def setUp(self):
        self.cli = load(self)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup)
        self.base = Path(self.dir.name).resolve()
        for name in ('h', 'state', 'data'):
            (self.base / name).mkdir(mode=0o700)
            os.chmod(self.base / name, 0o700)
        self.env = {'HOME': str(self.base / 'h'), 'SUPERVISE_STATE': str(self.base / 'state'),
                    'SUPERVISE_DATA': str(self.base / 'data'), 'XDG_STATE_HOME': str(self.base / 'xs'),
                    'XDG_DATA_HOME': str(self.base / 'xd'), 'XDG_CONFIG_HOME': str(self.base / 'xc')}
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cleanup(self):
        for path in [self.base, *self.base.rglob('*')]:
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self.dir.cleanup()

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = self.cli.main(list(argv))
            except SystemExit as exit_:
                code = exit_.code if isinstance(exit_.code, int) else (0 if exit_.code is None else 2)
        self.assertIsInstance(code, int)
        return code, out.getvalue(), err.getvalue()

    def json_out(self, out):
        try:
            return json.loads(out)
        except ValueError:
            self.fail('stdout is not one JSON document: ' + out[:500])


class DispatchTests(CliFixture):
    def test_no_subcommand_is_a_usage_error(self):
        self.assertEqual(self.main()[0], 2)

    def test_unknown_subcommand_is_a_usage_error(self):
        self.assertEqual(self.main('frobnicate')[0], 2)

    def test_help_lists_every_subcommand(self):
        code, out, err = self.main('--help')
        self.assertEqual(code, 0)
        for name in SUBCOMMANDS:
            self.assertIn(name, out + err)

    def test_python_dash_m_supervise_help(self):
        package_parent = Path(importlib.import_module('supervise').__file__).resolve().parent.parent
        env = dict(os.environ, PYTHONPATH=str(package_parent), PYTHONDONTWRITEBYTECODE='1')
        done = subprocess.run([PY, '-m', 'supervise', '--help'], cwd=str(self.base), env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        for name in SUBCOMMANDS:
            self.assertIn(name, done.stdout)


class VerifyCommandTests(CliFixture):
    def setUp(self):
        super().setUp()
        self.verify = load(self, 'supervise.verify')

    def test_pass_exits_zero_and_prints_json(self):
        seen = []

        def passing(home):
            seen.append(Path(home))
            return {'verdict': 'PASS', 'checks': [{'name': 'closure', 'ok': True, 'detail': 'ok'}]}
        home = self.base / 'inv'
        home.mkdir()
        with mock.patch.object(self.verify, 'verify_invocation', passing):
            code, out, _ = self.main('verify', str(home))
        self.assertEqual(code, 0)
        self.assertEqual(seen, [home])
        self.assertEqual(self.json_out(out)['verdict'], 'PASS')

    def test_fail_exits_one(self):
        failing = {'verdict': 'FAIL', 'checks': [{'name': 'receipts', 'ok': False, 'detail': 'route record'}]}
        with mock.patch.object(self.verify, 'verify_invocation', lambda home: failing):
            code, out, _ = self.main('verify', str(self.base))
        self.assertEqual(code, 1)
        self.assertEqual(self.json_out(out)['verdict'], 'FAIL')

    def test_real_verify_of_an_empty_home_fails(self):
        empty = self.base / 'empty'
        empty.mkdir()
        code, out, _ = self.main('verify', str(empty))
        self.assertEqual(code, 1)
        self.assertEqual(self.json_out(out)['verdict'], 'FAIL')

    def test_missing_argument_is_usage_error(self):
        self.assertEqual(self.main('verify')[0], 2)


class DoctorCommandTests(CliFixture):
    def setUp(self):
        super().setUp()
        self.doctor = load(self, 'supervise.doctor')

    def checks(self, *statuses):
        return [self.doctor.Check('c%d' % i, status, 'detail') for i, status in enumerate(statuses)]

    def test_exit_zero_without_fail(self):
        with mock.patch.object(self.doctor, 'run_checks', lambda **kw: self.checks('PASS', 'UNKNOWN')):
            self.assertEqual(self.main('doctor')[0], 0)

    def test_nonzero_with_fail(self):
        with mock.patch.object(self.doctor, 'run_checks', lambda **kw: self.checks('PASS', 'FAIL')):
            code, out, err = self.main('doctor')
        self.assertNotEqual(code, 0)
        self.assertIn('FAIL', out + err)

    def test_config_is_forwarded(self):
        seen = []

        def recording(**kwargs):
            seen.append(kwargs.get('config_path'))
            return self.checks('PASS')
        config = self.base / 'backend.json'
        config.write_text('{"routes": []}')
        with mock.patch.object(self.doctor, 'run_checks', recording):
            self.assertEqual(self.main('doctor', '--config', str(config))[0], 0)
        self.assertEqual([str(item) for item in seen], [str(config)])

    def test_real_doctor_never_prints_secrets(self):
        with mock.patch.dict(os.environ, {'OPENAI_API_KEY': SECRET, 'ANTHROPIC_API_KEY': SECRET}):
            code, out, err = self.main('doctor')
        self.assertIn(code, (0, 1))
        self.assertNotIn(SECRET, out + err)
        for name in ('python', 'bubblewrap', 'manager'):
            self.assertIn(name, out + err)


class InstallCommandTests(CliFixture):
    def setUp(self):
        super().setUp()
        self.installer = load(self, 'supervise.installer')
        self.calls = []

    def recording(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {'release': 'r1', 'publication_sha256': 'a' * 64}

    def installed_with(self):
        (args, kwargs), = self.calls
        source = args[0] if args else kwargs['source_root']
        return Path(source), Path(kwargs['data_root']), kwargs.get('pointer_path'), kwargs.get('release')

    def test_install_dispatches_with_flags(self):
        source, data, pointer = self.base / 'src', self.base / 'd' / 'supervise', self.base / 'pointer'
        source.mkdir()
        with mock.patch.object(self.installer, 'install', self.recording):
            code, out, _ = self.main('install', '--source', str(source), '--data-root', str(data),
                                     '--pointer', str(pointer), '--release', 'r1')
        self.assertEqual(code, 0)
        got = self.installed_with()
        self.assertEqual((got[0], got[1], str(got[2]), got[3]), (source, data, str(pointer), 'r1'))
        self.assertEqual(self.json_out(out)['release'], 'r1')

    def test_install_default_data_root_precedence(self):
        source = self.base / 'src'
        source.mkdir()
        cases = (({}, self.base / 'data'),
                 ({'SUPERVISE_DATA': None}, self.base / 'xd' / 'supervise'),
                 ({'SUPERVISE_DATA': None, 'XDG_DATA_HOME': None}, self.base / 'h' / '.local' / 'share' / 'supervise'))
        for overrides, expected in cases:
            with self.subTest(expected=str(expected)):
                self.calls.clear()
                env = dict(os.environ)
                for key, value in overrides.items():
                    env.pop(key, None)
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.object(self.installer, 'install', self.recording):
                    self.assertEqual(self.main('install', '--source', str(source))[0], 0)
                self.assertEqual(self.installed_with()[1], expected)

    def test_install_refusal_exits_two(self):
        def refusing(*args, **kwargs):
            raise self.installer.InstallError('CONTROL release already exists')
        source = self.base / 'src'
        source.mkdir()
        with mock.patch.object(self.installer, 'install', refusing):
            code, out, err = self.main('install', '--source', str(source))
        self.assertEqual(code, 2)
        self.assertIn('release already exists', out + err)
        self.assertNotIn('Traceback', out + err)

    def test_uninstall_dispatches(self):
        data, pointer = self.base / 'd' / 'supervise', self.base / 'pointer'
        with mock.patch.object(self.installer, 'uninstall', self.recording):
            code, _, _ = self.main('uninstall', '--data-root', str(data), '--pointer', str(pointer))
        self.assertEqual(code, 0)
        (args, kwargs), = self.calls
        self.assertEqual(Path(args[0] if args else kwargs['data_root']), data)
        self.assertEqual(str(kwargs.get('pointer_path')), str(pointer))


class InstallationBindingTests(CliFixture):
    def test_publication_sha256_comes_from_installation_record(self):
        record = {'release': 'r1', 'publication_sha256': 'ab' * 32}
        (self.base / 'data' / 'INSTALLATION.json').write_text(json.dumps(record))
        self.assertEqual(self.cli.installed_publication_sha256(), 'ab' * 32)

    def test_follows_the_data_root_environment(self):
        other = self.base / 'xd' / 'supervise'
        other.mkdir(parents=True)
        (other / 'INSTALLATION.json').write_text(json.dumps({'release': 'r2', 'publication_sha256': 'cd' * 32}))
        env = dict(os.environ)
        env.pop('SUPERVISE_DATA')
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(self.cli.installed_publication_sha256(), 'cd' * 32)

    def test_missing_installation_is_refused(self):
        with self.assertRaises(ValueError):
            self.cli.installed_publication_sha256()


class RunArgumentTests(CliFixture):
    def forbid_launch(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('run must validate arguments before launching anything')
        stack = contextlib.ExitStack()
        for target, name in ((subprocess, 'Popen'), (subprocess, 'run'), (os, 'execv'), (os, 'execvp'),
                             (os, 'execvpe'), (os, 'posix_spawn'), (os, 'fork')):
            stack.enter_context(mock.patch.object(target, name, forbidden))
        return stack

    def test_window_must_be_finite_and_at_most_two_hours(self):
        plan = self.base / 'PLAN.md'
        plan.write_text('# Plan\n')
        for bad in ('0', '-1', '7201', 'inf', 'nan', 'abc', '1e9'):
            with self.subTest(max_seconds=bad):
                with self.forbid_launch():
                    code, out, err = self.main('run', str(plan), '--max-seconds', bad)
                self.assertEqual(code, 2, out + err)

    def test_missing_plan_is_refused(self):
        with self.forbid_launch():
            code, _, _ = self.main('run', str(self.base / 'absent.md'), '--max-seconds', '600')
        self.assertEqual(code, 2)

    def test_plan_argument_is_required(self):
        with self.forbid_launch():
            self.assertEqual(self.main('run')[0], 2)


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap and a /usr or /bin interpreter are required for admission')
class IntentCommandTests(CliFixture):
    def setUp(self):
        super().setUp()
        admission = load(self, 'supervise.controller.admission')
        self.transaction = load(self, 'supervise.controller.transaction')
        backend = load(self, 'supervise.backend')
        stub = load(self, 'supervise.backends.stub')
        journal = load(self, 'supervise.kernel.journal')
        os.environ.pop(journal.WORKER_ENV, None)

        def route(rid, family, roles):
            return backend.Route(route_id=rid, harness='stub', vendor=family, model=rid, family=family,
                                 roles=frozenset(roles), review_isolation=True, price='unknown')
        self.project = self.base / 'project'
        (self.project / 'pkg').mkdir(parents=True)
        (self.project / 'tests').mkdir()
        (self.project / 'pkg' / '__init__.py').write_text('')
        (self.project / 'pkg' / 'core.py').write_text('VALUE = 1\nOTHER = 2\n')
        (self.project / 'tests' / 'test_core.py').write_text(TESTS_PY)
        plan = self.project / 'PLAN.md'
        plan.write_text('# Plan\nMake OTHER three.\n')
        (self.base / 'private').mkdir(mode=0o700)
        self.home = self.base / 'private' / 'inv'
        routes = (route('author-a', 'alpha', {'impl'}), route('review-g', 'gamma', {'review'}),
                  route('review-d', 'delta', {'review'}))
        self.fb = FixtureBackend(stub.StubBackend(routes, {}), self.project)
        admission.initialize(self.home, project=self.project, plan_path=plan)
        intent = {
            'acceptance': [{'id': 'values', 'proposition': 'OTHER is three', 'selectors': [VALUES]}],
            'tests': {'argv': [PY, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], 'format': 'unittest',
                      'timeout_seconds': 60, 'baseline_policy': 'nonregression',
                      'expected_baseline_failures': [VALUES]},
            'owned_paths': ['pkg/core.py'],
            'checkpoint': {'max_starts': 6, 'max_seconds': 900, 'first_value_seconds': 300},
            'timeout_seconds': 30,
            'packet': {'text': PACKET, 'estimated_files': 1, 'estimated_lines': 2},
        }
        self.assertEqual(admission.admit(self.home, intent, backend=self.fb)['status'], 'ADMITTED')
        self.made = []

        def make_controller(home):
            self.made.append(Path(home))
            return self.transaction.Controller(Path(home), backend=self.fb, publication_sha256=PUB)
        patcher = mock.patch.object(self.cli, 'make_controller', make_controller)
        patcher.start()
        self.addCleanup(patcher.stop)

    def version(self):
        return json.loads((self.home / 'state.json').read_text())['version']

    def direct(self, intent):
        return self.transaction.Controller(self.home, backend=self.fb, publication_sha256=PUB).perform(intent)

    def test_diagnose_round_trip_matches_the_controller(self):
        intent = {'action': 'diagnose', 'expected_version': self.version()}
        code, out, _ = self.main('intent', str(self.home), json.dumps(intent))
        self.assertEqual(code, 0)
        self.assertEqual(self.made, [self.home])
        self.assertEqual(self.json_out(out), json.loads(json.dumps(self.direct(intent))))

    def test_author_intent_reaches_perform(self):
        code, out, _ = self.main('intent', str(self.home),
                                 json.dumps({'action': 'author', 'expected_version': self.version(),
                                             'targets': ['values']}))
        self.assertEqual(code, 0)
        self.assertEqual(self.json_out(out)['status'], 'AUTHOR_COMPLETE')
        self.assertEqual(self.fb.launches, [('author-a', 'impl')])

    def test_refused_intent_exits_two_without_writing(self):
        before = self.version()
        code, out, err = self.main('intent', str(self.home), json.dumps({'action': 'author',
                                                                         'expected_version': before + 5,
                                                                         'targets': ['values']}))
        self.assertEqual(code, 2)
        self.assertIn('stale', (out + err).lower())
        self.assertEqual(self.version(), before)
        self.assertEqual(self.fb.launches, [])

    def test_invalid_intent_json_exits_two(self):
        for text in ('{not json', '[1, 2]', '"author"', ''):
            with self.subTest(text=text):
                code, out, err = self.main('intent', str(self.home), text)
                self.assertEqual(code, 2)
                self.assertNotIn('Traceback', out + err)
        self.assertEqual(self.fb.launches, [])


class StdlibTests(unittest.TestCase):
    def test_cli_and_main_are_stdlib_only(self):
        module = load(self)
        main_module = Path(module.__file__).with_name('__main__.py')
        self.assertTrue(main_module.is_file(), 'python -m supervise needs supervise/__main__.py')
        for path in (Path(module.__file__), main_module):
            source = path.read_text(encoding='utf-8')
            names = set()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names.add(node.module.split('.')[0])
            names -= {'__future__', 'supervise'}
            self.assertTrue(names <= STDLIB, (path.name, sorted(names - STDLIB)))
            self.assertNotIn('/ho' + 'me/', source)


if __name__ == '__main__':
    unittest.main()
