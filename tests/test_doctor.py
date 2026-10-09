# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S5-4a: `supervise.doctor`.

API assumed: `run_checks(*, config_path=None, which=shutil.which, manager_probe=None) -> list[Check]` where
`Check` has `name`, `status` (PASS/FAIL/UNKNOWN) and `detail` attributes.

Check names assumed (see NOTES.md): `python`, `bubblewrap`, `state-root`, `data-root`, `harness` (either one
check or one per harness named `harness:<executable>`), `independence`, `manager`.

Configuration (Chief ruling): the simple backend's TOML, exactly the S4-1 format -- `[[route]]` tables with
id/harness/vendor/model/family/roles/effort/price and a `[crew]` table with authors/reviewers/
require_review_fallback. The doctor parses it itself with stdlib `tomllib` (no dependency on
supervise.backends.simple). Harness executables are the routes' `harness` values (codex, claude), looked up
with `which`. Independence is feasible when some route in crew.authors and some route in crew.reviewers have
different families.
The bubblewrap executable is located with the injected `which('bwrap')` and actually probed.
"""
import ast
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
STATUSES = {'PASS', 'FAIL', 'UNKNOWN'}
SECRET = 'sk-' + 'doctortest' + '7f3a9c2e5b1d4f6a8c0e2b4d6f8a1c3e'


def load(test, name='supervise.doctor'):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        test.fail(name + ' is not implemented: ' + str(error))


def route(rid, harness, family, roles, **extra):
    value = {'id': rid, 'harness': harness, 'vendor': family, 'model': rid + '-model', 'family': family,
             'roles': list(roles)}
    value.update(extra)
    return value


def toml_value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(toml_value(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{ ' + ', '.join(key + ' = ' + toml_value(item) for key, item in value.items()) + ' }'
    raise TypeError(value)


def toml_config(routes, authors, reviewers, fallback=False):
    """The S4-1 simple backend format: [[route]] tables and one [crew] table."""
    parts = []
    for item in routes:
        parts.append('[[route]]')
        parts.extend(key + ' = ' + toml_value(value) for key, value in item.items())
        parts.append('')
    parts.append('[crew]')
    parts.append('authors = ' + toml_value(authors))
    parts.append('reviewers = ' + toml_value(reviewers))
    parts.append('require_review_fallback = ' + toml_value(fallback))
    return '\n'.join(parts) + '\n'


def real_bwrap_probe():
    bwrap = shutil.which('bwrap')
    if bwrap is None:
        return False
    done = subprocess.run([bwrap, '--unshare-user', '--unshare-pid', '--unshare-net', '--ro-bind', '/', '/',
                           '--', sys.executable, '-c', 'pass'],
                          capture_output=True, timeout=60)
    return done.returncode == 0


class DoctorFixture(unittest.TestCase):
    def setUp(self):
        self.doctor = load(self)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.base = Path(self.dir.name).resolve()
        self.state = self.base / 'state'
        self.data = self.base / 'data'
        self.state.mkdir(mode=0o700)
        self.data.mkdir(mode=0o700)
        os.chmod(self.state, 0o700)
        os.chmod(self.data, 0o700)
        (self.base / 'home').mkdir()
        env = {'HOME': str(self.base / 'home'), 'SUPERVISE_STATE': str(self.state),
               'SUPERVISE_DATA': str(self.data), 'XDG_STATE_HOME': str(self.base / 'xdg-state'),
               'XDG_DATA_HOME': str(self.base / 'xdg-data'), 'XDG_CONFIG_HOME': str(self.base / 'xdg-config'),
               'OPENAI_API_KEY': SECRET, 'ANTHROPIC_API_KEY': SECRET, 'SUPERVISE_TEST_TOKEN': SECRET}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.asked = []

    def tearDown(self):
        for path in (self.state, self.data):
            if path.exists():
                os.chmod(path, 0o700)

    def config(self, routes, authors, reviewers, fallback=False):
        path = self.base / 'config.toml'
        text = toml_config(routes, authors, reviewers, fallback)
        tomllib.loads(text)                                          # the fixture itself is valid TOML
        path.write_text(text)
        return path

    def which_with(self, present, bwrap='real'):
        """A `which` that finds only the named harness executables (and bwrap per the argument)."""
        def which(name, *args, **kwargs):
            self.asked.append(name)
            if name == 'bwrap':
                return shutil.which('bwrap') if bwrap == 'real' else bwrap
            if name in present:
                fake = self.bin / name
                if not fake.exists():
                    fake.write_text('#!/bin/sh\nexit 0\n')
                    fake.chmod(0o755)
                return str(fake)
            return None
        return which

    def run_checks(self, **kwargs):
        checks = self.doctor.run_checks(**kwargs)
        self.assertIsInstance(checks, list)
        for check in checks:
            self.assertIn(check.status, STATUSES, check)
            self.assertTrue(isinstance(check.name, str) and check.name)
            self.assertIsInstance(check.detail, str)
            self.assertNotIn(SECRET, check.name + ' ' + check.detail + ' ' + repr(check))
        names = [c.name for c in checks]
        self.assertEqual(len(names), len(set(names)), 'check names are unique')
        return checks

    def named(self, checks, key):
        found = [c for c in checks if c.name == key or c.name.startswith(key + ':')]
        self.assertTrue(found, (key, [c.name for c in checks]))
        return found

    def one(self, checks, key):
        found = self.named(checks, key)
        self.assertEqual(len(found), 1, found)
        return found[0]


class CoverageTests(DoctorFixture):
    def test_every_documented_check_is_reported(self):
        config = self.config([route('a', 'codex', 'openai', ['impl']), route('r', 'claude', 'anthropic', ['review'])],
                             ['a'], ['r'])
        checks = self.run_checks(config_path=config, which=self.which_with({'codex', 'claude'}))
        for key in ('python', 'bubblewrap', 'state-root', 'data-root', 'harness', 'independence', 'manager'):
            with self.subTest(check=key):
                self.named(checks, key)

    def test_python_version(self):
        checks = self.run_checks(which=self.which_with(set()))
        expected = 'PASS' if sys.version_info >= (3, 12) else 'FAIL'
        self.assertEqual(self.one(checks, 'python').status, expected)


class BubblewrapTests(DoctorFixture):
    def test_missing_bubblewrap_fails(self):
        checks = self.run_checks(which=self.which_with(set(), bwrap=None))
        self.assertEqual(self.one(checks, 'bubblewrap').status, 'FAIL')
        self.assertIn('bwrap', self.asked)

    def test_bubblewrap_is_actually_probed(self):
        log = self.base / 'bwrap-args.txt'
        fake = self.bin / 'bwrap'
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "' + str(log) + '"\nexit 1\n')
        fake.chmod(0o755)
        checks = self.run_checks(which=self.which_with(set(), bwrap=str(fake)))
        self.assertEqual(self.one(checks, 'bubblewrap').status, 'FAIL', 'a present but failing bwrap is FAIL')
        self.assertTrue(log.is_file(), 'doctor executed the located bwrap')
        args = log.read_text().split('\n')
        for flag in ('--unshare-user', '--unshare-pid', '--unshare-net'):
            self.assertIn(flag, args)

    @unittest.skipUnless(shutil.which('bwrap'), 'bubblewrap is required')
    def test_real_bubblewrap_probe_matches_host(self):
        checks = self.run_checks(which=self.which_with(set()))
        self.assertEqual(self.one(checks, 'bubblewrap').status, 'PASS' if real_bwrap_probe() else 'FAIL')


class RootTests(DoctorFixture):
    def test_private_writable_roots_pass(self):
        checks = self.run_checks(which=self.which_with(set()))
        self.assertEqual(self.one(checks, 'state-root').status, 'PASS')
        self.assertEqual(self.one(checks, 'data-root').status, 'PASS')

    def test_group_or_world_accessible_roots_fail(self):
        os.chmod(self.state, 0o755)
        os.chmod(self.data, 0o750)
        checks = self.run_checks(which=self.which_with(set()))
        self.assertEqual(self.one(checks, 'state-root').status, 'FAIL')
        self.assertEqual(self.one(checks, 'data-root').status, 'FAIL')
        self.assertEqual(os.stat(self.state).st_mode & 0o777, 0o755, 'doctor reports; it does not repair')

    @unittest.skipIf(hasattr(os, 'geteuid') and os.geteuid() == 0, 'root ignores directory permissions')
    def test_unwritable_root_fails(self):
        os.chmod(self.state, 0o500)
        checks = self.run_checks(which=self.which_with(set()))
        self.assertEqual(self.one(checks, 'state-root').status, 'FAIL')

    def test_roots_follow_environment(self):
        other = self.base / 'elsewhere'
        other.mkdir(mode=0o755)
        os.chmod(other, 0o755)
        with mock.patch.dict(os.environ, {'SUPERVISE_STATE': str(other)}):
            checks = self.run_checks(which=self.which_with(set()))
        self.assertEqual(self.one(checks, 'state-root').status, 'FAIL')
        self.assertEqual(self.one(checks, 'data-root').status, 'PASS')


class HarnessTests(DoctorFixture):
    def path(self):
        return self.config([route('codex-gpt', 'codex', 'openai', ['impl', 'review'], effort='high',
                                  price={'input_per_mtok': 1.25, 'output_per_mtok': 10.0}),
                            route('claude-sonnet', 'claude', 'anthropic', ['review', 'impl'])],
                           ['codex-gpt', 'claude-sonnet'], ['claude-sonnet', 'codex-gpt'])

    def test_present_harnesses_pass(self):
        checks = self.run_checks(config_path=self.path(), which=self.which_with({'codex', 'claude'}))
        harness = self.named(checks, 'harness')
        self.assertTrue(all(c.status == 'PASS' for c in harness), harness)
        self.assertIn('codex', self.asked)
        self.assertIn('claude', self.asked)

    def test_missing_harness_fails_and_names_it(self):
        checks = self.run_checks(config_path=self.path(), which=self.which_with({'codex'}))
        failed = [c for c in self.named(checks, 'harness') if c.status == 'FAIL']
        self.assertTrue(failed)
        self.assertTrue(any('claude' in c.name + ' ' + c.detail for c in failed), failed)

    def test_config_path_accepts_a_string(self):
        checks = self.run_checks(config_path=str(self.path()), which=self.which_with({'codex', 'claude'}))
        self.assertEqual(self.one(checks, 'independence').status, 'PASS')

    def test_no_configuration_is_unknown_not_pass(self):
        checks = self.run_checks(which=self.which_with({'codex', 'claude'}))
        for key in ('harness', 'independence'):
            with self.subTest(check=key):
                self.assertTrue(all(c.status == 'UNKNOWN' for c in self.named(checks, key)))

    def test_unreadable_configuration_is_not_pass(self):
        cases = {'not toml': 'this is = = not toml\n', 'json': '{"route": []}\n', 'missing': None}
        for name, text in cases.items():
            with self.subTest(case=name):
                path = self.base / (name.replace(' ', '-') + '.toml')
                if text is not None:
                    path.write_text(text)
                checks = self.run_checks(config_path=path, which=self.which_with({'codex', 'claude'}))
                for key in ('harness', 'independence'):
                    self.assertTrue(all(c.status != 'PASS' for c in self.named(checks, key)), (name, key))

    def test_configuration_secrets_are_never_reported(self):
        text = toml_config([route('codex-gpt', 'codex', 'openai', ['impl'], model=SECRET),
                            route('claude-sonnet', 'claude', 'anthropic', ['review'])],
                           ['codex-gpt'], ['claude-sonnet'])
        path = self.base / 'secret.toml'
        path.write_text(text)
        self.run_checks(config_path=path, which=self.which_with({'claude'}))


class IndependenceTests(DoctorFixture):
    def status(self, routes, authors, reviewers):
        checks = self.run_checks(config_path=self.config(routes, authors, reviewers),
                                 which=self.which_with({'codex', 'claude'}))
        return self.one(checks, 'independence').status

    def test_distinct_author_and_reviewer_families_pass(self):
        self.assertEqual(self.status([route('a', 'codex', 'openai', ['impl']),
                                      route('r', 'claude', 'anthropic', ['review'])], ['a'], ['r']), 'PASS')

    def test_s4_reference_configuration_is_feasible(self):
        routes = [route('codex-gpt', 'codex', 'openai-gpt', ['impl', 'review'], effort='high',
                        price={'input_per_mtok': 1.25, 'output_per_mtok': 10.0}),
                  route('claude-sonnet', 'claude', 'anthropic-claude', ['review', 'impl']),
                  route('claude-haiku', 'claude', 'anthropic-claude', ['review'],
                        price={'input_per_mtok': 1.0, 'output_per_mtok': 5.0})]
        self.assertEqual(self.status(routes, ['codex-gpt', 'claude-sonnet'],
                                     ['claude-sonnet', 'claude-haiku', 'codex-gpt']), 'PASS')

    def test_single_family_is_infeasible(self):
        self.assertEqual(self.status([route('a', 'codex', 'openai', ['impl']),
                                      route('r', 'codex', 'openai', ['review'])], ['a'], ['r']), 'FAIL')

    def test_crew_lists_decide_not_the_route_catalog(self):
        routes = [route('a', 'codex', 'openai', ['impl', 'review']), route('r', 'claude', 'anthropic', ['review'])]
        self.assertEqual(self.status(routes, ['a'], ['a']), 'FAIL',
                         'an independent route outside crew.reviewers does not make the crew feasible')

    def test_reviewers_sharing_every_author_family_are_infeasible(self):
        routes = [route('a', 'codex', 'openai', ['impl']), route('r', 'claude', 'openai', ['review']),
                  route('z', 'claude', 'anthropic', ['review'])]
        self.assertEqual(self.status(routes, ['a'], ['r']), 'FAIL')

    def test_empty_crew_lists_are_infeasible(self):
        routes = [route('a', 'codex', 'openai', ['impl']), route('r', 'claude', 'anthropic', ['review'])]
        self.assertEqual(self.status(routes, [], ['r']), 'FAIL')
        self.assertEqual(self.status(routes, ['a'], []), 'FAIL')


class ManagerProbeTests(DoctorFixture):
    def test_manager_is_unknown_without_a_probe(self):
        checks = self.run_checks(which=self.which_with(set()))
        self.assertEqual(self.one(checks, 'manager').status, 'UNKNOWN')

    def test_supplied_probe_is_called(self):
        calls = []

        def probe():
            calls.append(1)
            return True
        checks = self.run_checks(which=self.which_with(set()), manager_probe=probe)
        self.assertEqual(calls, [1])
        self.assertEqual(self.one(checks, 'manager').status, 'PASS')

    def test_failing_probe_fails_without_leaking(self):
        def probe():
            raise RuntimeError('manager refused token ' + SECRET)
        checks = self.run_checks(which=self.which_with(set()), manager_probe=probe)
        self.assertEqual(self.one(checks, 'manager').status, 'FAIL')

    def test_false_probe_fails(self):
        checks = self.run_checks(which=self.which_with(set()), manager_probe=lambda: False)
        self.assertEqual(self.one(checks, 'manager').status, 'FAIL')


class StdlibTests(unittest.TestCase):
    def test_doctor_is_stdlib_only(self):
        module = load(self)
        source = Path(module.__file__).read_text(encoding='utf-8')
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names -= {'__future__', 'supervise'}
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))
        self.assertNotIn('/ho' + 'me/', source)
        self.assertIn('tomllib', names, 'the doctor parses the simple TOML configuration itself')
        self.assertNotIn('backends.simple', source, 'no dependency on the later simple backend unit')


if __name__ == '__main__':
    unittest.main()
