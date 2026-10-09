# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S3-2: frozen test evidence (port of v2.8.2 evidence.py/unittest_runner.py)."""
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
PY = sys.executable
HAVE_BWRAP = shutil.which('bwrap') is not None
TESTS = textwrap.dedent('''
    import unittest
    from pkg import core

    class CoreTests(unittest.TestCase):
        def test_pass(self):
            self.assertEqual(core.VALUE, 1)

        def test_fail(self):
            self.assertEqual(core.OTHER, 99)

        def test_error(self):
            raise RuntimeError("boom")

        @unittest.skip("not today")
        def test_skip(self):
            pass

        def test_subtest_failure_is_not_erased(self):
            for n in (1, 2):
                with self.subTest(n=n):
                    self.assertEqual(n, 1)
''')
IDS = ['test_core.CoreTests.' + n for n in ('test_error', 'test_fail', 'test_pass', 'test_skip',
                                             'test_subtest_failure_is_not_erased')]


def load(test):
    try:
        return importlib.import_module('supervise.controller.evidence')
    except ImportError as error:
        test.fail('Controller evidence is not implemented: ' + str(error))


def tests_spec(**overrides):
    spec = {'argv': [PY, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], 'format': 'unittest', 'timeout_seconds': 60}
    spec.update(overrides)
    return spec


class Fixture(unittest.TestCase):
    def setUp(self):
        self.e = load(self)
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.project = base / 'project'
        (self.project / 'pkg').mkdir(parents=True)
        (self.project / 'tests').mkdir()
        (self.project / 'pkg' / '__init__.py').write_text('')
        (self.project / 'pkg' / 'core.py').write_text('VALUE = 1\nOTHER = 2\n')
        (self.project / 'tests' / 'test_core.py').write_text(TESTS)
        self.root = base / 'private'
        self.root.mkdir(mode=0o700)

    def tearDown(self):
        for path in Path(self.dir.name).rglob('*'):
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self.dir.cleanup()

    def freeze(self, **overrides):
        values = dict(acceptance=[{'id': 'core', 'proposition': 'core works'}], tests=tests_spec(),
                      source_scopes=['pkg'], test_scopes=['tests'], owned_paths=['pkg/core.py'])
        values.update(overrides)
        return self.e.freeze(self.project, self.root, **values)


class ValidationTests(Fixture):
    def test_supported_test_commands_only(self):
        self.e.validate_tests(tests_spec())
        self.e.validate_tests(tests_spec(argv=[PY, '-m', 'pytest', '-q'], format='pytest-junit'))
        bad = [tests_spec(argv=['python3', '-m', 'unittest']),                      # relative interpreter
               tests_spec(argv=[PY, '-m', 'pytest']),                                # format mismatch
               tests_spec(argv=[PY, 'run_tests.py', 'x']),
               tests_spec(argv=[PY, '-m', 'pytest', '--junitxml=x.xml'], format='pytest-junit'),
               tests_spec(format='nose'),
               tests_spec(timeout_seconds=0), tests_spec(timeout_seconds=3601), tests_spec(timeout_seconds=True),
               tests_spec(timeout_seconds=float('nan')), tests_spec(argv=[PY, '-m'])]
        for spec in bad:
            with self.subTest(spec=spec):
                with self.assertRaises(self.e.EvidenceError):
                    self.e.validate_tests(spec)


class RunnerTests(Fixture):
    def runner(self, mode):
        script = Path(self.e.__file__).with_name('unittest_runner.py')
        out = Path(self.dir.name) / f'{mode}.json'
        done = subprocess.run([PY, '-B', str(script), str(out), mode, 'discover', '-s', 'tests'],
                              cwd=self.project, capture_output=True, text=True, timeout=60)
        return done.returncode, json.loads(out.read_text())

    def test_collect_reports_exact_ids_without_running(self):
        code, report = self.runner('collect')
        self.assertEqual(code, 0)
        self.assertEqual(sorted(report['ids']), IDS)
        self.assertEqual(report['outcomes'], {})
        self.assertTrue(report['complete'])

    def test_run_reports_every_outcome_honestly(self):
        code, report = self.runner('run')
        self.assertEqual(code, 1)
        outcomes = {k: v['outcome'] for k, v in report['outcomes'].items()}
        self.assertEqual(outcomes, {'test_core.CoreTests.test_error': 'ERROR', 'test_core.CoreTests.test_fail': 'FAIL',
                                    'test_core.CoreTests.test_pass': 'PASS', 'test_core.CoreTests.test_skip': 'SKIP',
                                    'test_core.CoreTests.test_subtest_failure_is_not_erased': 'FAIL'})
        self.assertIn('boom', report['outcomes']['test_core.CoreTests.test_error']['diagnostic'])
        self.assertTrue(report['complete'])

    def test_import_error_is_incomplete_not_green(self):
        (self.project / 'tests' / 'test_broken.py').write_text('import does_not_exist\n')
        code, report = self.runner('collect')
        self.assertFalse(report['complete'])


class JunitTests(Fixture):
    def write(self, xml):
        path = Path(self.dir.name) / 'junit.xml'
        path.write_text(xml)
        return path

    def test_valid_junit_is_parsed(self):
        result = self.e.parse_junit(self.write(
            '<testsuites><testsuite name="s" tests="3" failures="1" errors="0" skipped="1">'
            '<testcase classname="t.C" name="a"/><testcase classname="t.C" name="b"><failure message="m">x</failure></testcase>'
            '<testcase classname="t.C" name="c"><skipped/></testcase></testsuite></testsuites>'))
        self.assertTrue(result['complete'])
        self.assertEqual({k: v['outcome'] for k, v in result['outcomes'].items()},
                         {'t.C::a': 'PASS', 't.C::b': 'FAIL', 't.C::c': 'SKIP'})

    def test_contradictory_or_malformed_junit_is_incomplete(self):
        for xml in ('<testsuite name="s" tests="1" failures="1"><testcase classname="t" name="a"/></testsuite>',
                    '<testsuite name="s" tests="2"><testcase classname="t" name="a"/><testcase classname="t" name="a"/></testsuite>',
                    '<testsuite name="s" tests="1"><testcase classname="t" name="a"><failure/><error/></testcase></testsuite>',
                    '<html/>', 'not xml'):
            with self.subTest(xml=xml):
                result = self.e.parse_junit(self.write(xml))
                self.assertFalse(result['complete'])
                self.assertEqual(result['outcomes'], {})


class FreezeObserveTests(Fixture):
    def test_freeze_records_an_immutable_contract_and_baseline(self):
        binding = self.freeze()
        contract_path = Path(binding['path'])
        self.assertEqual(contract_path, self.root / 'contract.json')
        self.assertEqual(binding['sha256'], hashlib.sha256(contract_path.read_bytes()).hexdigest())
        contract = json.loads(contract_path.read_text())
        self.assertEqual(sorted(contract['frozen_ids']), IDS)
        self.assertTrue(contract['baseline_collection_valid'])
        self.assertEqual(set(contract['frozen_test_inputs']), {'tests/test_core.py'})
        self.assertEqual(contract['independent_review'], 'OPEN')
        self.assertTrue((self.root / 'baseline-observation.json').is_file())
        with self.assertRaises((self.e.EvidenceError, FileExistsError)):
            self.freeze()                                    # never overwritten

    def test_freeze_refuses_unsafe_scopes_and_ids(self):
        for overrides in ({'owned_paths': ['../outside.py']},
                          {'acceptance': [{'id': 'a', 'proposition': 'x'}, {'id': 'a', 'proposition': 'y'}]},
                          {'acceptance': []}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(self.e.EvidenceError):
                    self.freeze(**overrides)
        inside = self.project / 'private'
        inside.mkdir(mode=0o700)
        with self.assertRaises(self.e.EvidenceError):
            self.e.freeze(self.project, inside, acceptance=[{'id': 'a', 'proposition': 'x'}], tests=tests_spec(),
                          source_scopes=['pkg'], test_scopes=['tests'], owned_paths=['pkg/core.py'])

    @unittest.skipUnless(HAVE_BWRAP, 'bubblewrap unavailable')
    def test_observe_reports_failures_as_fail(self):
        contract = json.loads(Path(self.freeze()['path']).read_text())
        out = self.root / 'obs-1'
        out.mkdir(mode=0o700)
        observation = self.e.observe(contract, out)
        self.assertTrue(observation['complete'])
        self.assertEqual(observation['verdict'], 'FAIL')
        self.assertEqual(sorted(observation['test_ids']), IDS)
        self.assertEqual(observation['changed_frozen_test_inputs'], [])
        self.assertEqual(observation['missing_frozen_ids'], [])
        for name in ('stdout', 'stderr'):
            record = observation['process'][name]
            self.assertEqual(record['sha256'], hashlib.sha256(Path(record['path']).read_bytes()).hexdigest())

    @unittest.skipUnless(HAVE_BWRAP, 'bubblewrap unavailable')
    def test_observe_passes_only_when_everything_passes(self):
        (self.project / 'tests' / 'test_core.py').write_text(
            'import unittest\nfrom pkg import core\n\nclass CoreTests(unittest.TestCase):\n'
            '    def test_pass(self):\n        self.assertEqual(core.VALUE, 1)\n')
        contract = json.loads(Path(self.freeze()['path']).read_text())
        out = self.root / 'obs-pass'
        out.mkdir(mode=0o700)
        self.assertEqual(self.e.observe(contract, out)['verdict'], 'PASS')

    @unittest.skipUnless(HAVE_BWRAP, 'bubblewrap unavailable')
    def test_tests_cannot_modify_source_and_tampering_is_unknown(self):
        (self.project / 'tests' / 'test_core.py').write_text(
            'import unittest, pathlib\n\nclass T(unittest.TestCase):\n'
            '    def test_write(self):\n'
            '        try:\n            pathlib.Path("pkg/core.py").write_text("VALUE = 2\\n")\n'
            '        except OSError:\n            pass\n')
        contract = json.loads(Path(self.freeze()['path']).read_text())
        out = self.root / 'obs-w'
        out.mkdir(mode=0o700)
        self.e.observe(contract, out)
        self.assertEqual((self.project / 'pkg' / 'core.py').read_text(), 'VALUE = 1\nOTHER = 2\n')
        (self.project / 'tests' / 'test_core.py').write_text('import unittest\n')       # frozen test changed
        out2 = self.root / 'obs-t'
        out2.mkdir(mode=0o700)
        observation = self.e.observe(contract, out2)
        self.assertEqual(observation['verdict'], 'UNKNOWN')
        self.assertFalse(observation['complete'])
        self.assertEqual(observation['changed_frozen_test_inputs'], ['tests/test_core.py'])

    @unittest.skipUnless(HAVE_BWRAP, 'bubblewrap unavailable')
    def test_timeout_is_unknown(self):
        (self.project / 'tests' / 'test_core.py').write_text(
            'import time, unittest\n\nclass T(unittest.TestCase):\n    def test_slow(self):\n        time.sleep(30)\n')
        contract = json.loads(Path(self.freeze(tests=tests_spec(timeout_seconds=20))['path']).read_text())
        contract['tests']['timeout_seconds'] = 2
        out = self.root / 'obs-slow'
        out.mkdir(mode=0o700)
        observation = self.e.observe(contract, out)
        self.assertTrue(observation['process']['timeout'])
        self.assertEqual(observation['verdict'], 'UNKNOWN')


class CompareTests(Fixture):
    def obs(self, **outcomes):
        return {'complete': True, 'test_ids': sorted(outcomes),
                'outcomes': {k: {'outcome': v, 'diagnostic': ''} for k, v in outcomes.items()}}

    def test_nonregression_policy(self):
        base = self.obs(a='FAIL', b='PASS', c='FAIL')
        result = self.e.compare(base, self.obs(a='PASS', b='PASS', c='FAIL'), 'nonregression', required=['a'])
        self.assertEqual((result['repaired'], result['pre_existing'], result['regressed'], result['new_failures']),
                         (['a'], ['c'], [], []))
        self.assertTrue(result['acceptable'])
        self.assertFalse(self.e.compare(base, self.obs(a='PASS', b='FAIL', c='FAIL'), 'nonregression', required=['a'])['acceptable'])
        self.assertFalse(self.e.compare(base, self.obs(a='FAIL', b='PASS', c='FAIL'), 'nonregression', required=['a'])['acceptable'])
        self.assertFalse(self.e.compare(base, self.obs(a='PASS', b='PASS', c='FAIL', d='ERROR'), 'nonregression',
                                        required=['a'])['acceptable'])

    def test_all_green_policy_and_incomplete_observations(self):
        base = self.obs(a='FAIL', b='PASS')
        self.assertTrue(self.e.compare(base, self.obs(a='PASS', b='PASS'), 'all-green', required=['a'])['acceptable'])
        self.assertFalse(self.e.compare(base, self.obs(a='PASS', b='SKIP'), 'all-green', required=['a'])['acceptable'])
        incomplete = dict(self.obs(a='PASS', b='PASS'), complete=False)
        self.assertFalse(self.e.compare(base, incomplete, 'all-green', required=['a'])['acceptable'])
        with self.assertRaises(self.e.EvidenceError):
            self.e.compare(base, self.obs(a='PASS', b='PASS'), 'lenient', required=['a'])


class StdlibTests(Fixture):
    def test_modules_are_stdlib_only(self):
        for path in (Path(self.e.__file__), Path(self.e.__file__).with_name('unittest_runner.py')):
            names = set()
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names.add(node.module.split('.')[0])
            names -= {'__future__', 'supervise'}
            self.assertTrue(names <= STDLIB, (path.name, sorted(names - STDLIB)))


if __name__ == '__main__':
    unittest.main()
