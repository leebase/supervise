# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S3-1: invocation home and frozen admission.

Module under test: `supervise.controller.admission` (CONTROLLER-DESIGN.md, "S3-1 Admission").

Public API exercised (beyond the design note, see NOTES.md):
* `initialize(home, *, project, plan_path, now=None) -> dict` returns the pending document it wrote
  (invocation_id, cwd, plan_path, plan_sha256, created_at); a second call never changes pending.json.
* `admit(home, intent, *, backend) -> dict` returns a dict whose `status` is 'ADMITTED'; every refusal raises
  `AdmissionRefused` (a ValueError) whose message contains the fragment named in each test below.
* `state.json` in the home carries `{"status", "version", ...}`; after a refusal before freeze its status is
  'PRECHECK_FAILED' and its JSON contains a diagnostic naming the refusal.
Ports of v2.8.2 conformance scenarios: provenance/host-pending-refused, provenance/pending-protected-before-dispatch.
"""
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import textwrap
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
PY = sys.executable
HAVE_BWRAP = shutil.which('bwrap') is not None
TESTS_PY = textwrap.dedent('''
    import unittest
    from pkg import core


    class CoreTests(unittest.TestCase):
        def test_value(self):
            self.assertEqual(core.VALUE, 1)

        def test_values(self):
            self.assertEqual(getattr(core, 'OTHER', None), 3)

        def test_more(self):
            self.assertEqual(getattr(core, 'MORE', None), 4)
''')
GREEN_TESTS_PY = 'import unittest\nfrom pkg import core\n\n\nclass CoreTests(unittest.TestCase):\n' \
                 '    def test_value(self):\n        self.assertEqual(core.VALUE, 1)\n'
VALUES, MORE, VALUE = ('test_core.CoreTests.test_values', 'test_core.CoreTests.test_more',
                       'test_core.CoreTests.test_value')
PACKET = '\n'.join(['Kind: impl', 'Owned paths: pkg/core.py', 'Declared size: 1 file, 5 changed lines',
                    'Oracle: python3 -m unittest discover -s tests', 'Domain: none', 'Review: independent reviewer',
                    '', 'Make OTHER three and add MORE = 4 in pkg/core.py.'])
PINNED = ('pending.json', 'acceptance.json', 'contract.json', 'binding.json', 'elapsed-checkpoint.json')


def load(test, name):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        test.fail(name + ' is not implemented: ' + str(error))


def routes(b):
    def route(rid, family, roles, isolation=True):
        return b.Route(route_id=rid, harness='stub', vendor=family, model=rid, family=family,
                       roles=frozenset(roles), review_isolation=isolation, price='unknown')
    return (route('author-a', 'alpha', {'impl'}), route('author-b', 'beta', {'impl'}),
            route('review-g', 'gamma', {'review'}), route('review-d', 'delta', {'review'}))


def intent(**overrides):
    value = {
        'acceptance': [{'id': 'values', 'proposition': 'OTHER is three', 'selectors': [VALUES]},
                       {'id': 'more', 'proposition': 'MORE is four', 'selectors': [MORE]}],
        'tests': {'argv': [PY, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], 'format': 'unittest',
                  'timeout_seconds': 60, 'baseline_policy': 'nonregression',
                  'expected_baseline_failures': [MORE, VALUES]},
        'owned_paths': ['pkg/core.py'],
        'checkpoint': {'max_starts': 8, 'max_seconds': 900, 'first_value_seconds': 300},
        'timeout_seconds': 30,
        'packet': {'text': PACKET, 'estimated_files': 1, 'estimated_lines': 5},
    }
    for key, item in overrides.items():
        if key in ('tests', 'checkpoint', 'packet'):
            value[key] = dict(value[key], **item)
        else:
            value[key] = item
    return json.loads(json.dumps(value))


def tree(root):
    """Every entry below root with its bytes and mode, ignoring advisory lock files."""
    entries = {}
    for path in sorted(Path(root).rglob('*')):
        if path.name.endswith('.lock'):
            continue
        data = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        entries[str(path.relative_to(root))] = (data, stat.S_IMODE(path.lstat().st_mode))
    return entries


class HomeFixture(unittest.TestCase):
    def setUp(self):
        self.a = load(self, 'supervise.controller.admission')
        self.b = load(self, 'supervise.backend')
        self.stub = load(self, 'supervise.backends.stub')
        self.dir = tempfile.TemporaryDirectory()
        self.base = Path(self.dir.name)
        self.project = self.base / 'project'
        (self.project / 'pkg').mkdir(parents=True)
        (self.project / 'tests').mkdir()
        (self.project / 'pkg' / '__init__.py').write_text('')
        (self.project / 'pkg' / 'core.py').write_text('VALUE = 1\nOTHER = 2\n')
        (self.project / 'tests' / 'test_core.py').write_text(TESTS_PY)
        self.plan = self.project / 'PLAN.md'
        self.plan.write_text('# Plan\nMake OTHER three and MORE four.\n')
        self.home = self.base / 'private' / 'inv'
        (self.base / 'private').mkdir(mode=0o700)
        self.backend = self.stub.StubBackend(routes(self.b), {})

    def tearDown(self):
        for path in Path(self.dir.name).rglob('*'):
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self.dir.cleanup()

    def initialize(self):
        return self.a.initialize(self.home, project=self.project, plan_path=self.plan)

    def admit(self, value=None, backend=None):
        return self.a.admit(self.home, value if value is not None else intent(), backend=backend or self.backend)

    def state(self):
        path = self.home / 'state.json'
        self.assertTrue(path.is_file(), 'state.json missing from the invocation home')
        return json.loads(path.read_text())

    def starts(self):
        path = self.home / 'STATE.jsonl'
        if not path.exists():
            return []
        return [e for e in map(json.loads, path.read_text().splitlines()) if e.get('kind') == 'start']

    def refused(self, fragment, value=None, backend=None):
        """Admission must refuse, name the condition, leave PRECHECK_FAILED with a diagnostic and no start."""
        with self.assertRaises(self.a.AdmissionRefused) as caught:
            self.admit(value, backend)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertIn(fragment.lower(), str(caught.exception).lower())
        self.assertEqual(self.starts(), [], 'a refusal must leave zero journal starts')
        state = self.state()
        self.assertEqual(state['status'], 'PRECHECK_FAILED')
        self.assertIn(fragment.lower(), json.dumps(state).lower(), 'diagnostic must name the refusal')
        return caught.exception


class InitializeTests(HomeFixture):
    def test_initialize_creates_private_home_and_read_only_pending_once(self):
        pending = self.initialize()
        path = self.home / 'pending.json'
        self.assertEqual(stat.S_IMODE(self.home.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)
        on_disk = json.loads(path.read_text())
        self.assertEqual(on_disk, pending)
        self.assertTrue({'invocation_id', 'cwd', 'plan_path', 'plan_sha256', 'created_at'} <= set(on_disk))
        self.assertEqual(Path(on_disk['cwd']).resolve(), self.project.resolve())
        self.assertEqual(Path(on_disk['plan_path']).resolve(), self.plan.resolve())
        self.assertEqual(on_disk['plan_sha256'], hashlib.sha256(self.plan.read_bytes()).hexdigest())
        self.assertTrue(isinstance(on_disk['invocation_id'], str) and on_disk['invocation_id'])
        before = path.read_bytes()
        try:
            again = self.initialize()
        except ValueError:
            again = None
        if again is not None:
            self.assertEqual(again, pending)
        self.assertEqual(path.read_bytes(), before, 'pending.json is created once and never rewritten')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o400)

    def test_home_inside_project_is_refused(self):
        inside = self.project / 'supervise-home'
        with self.assertRaises(ValueError):
            self.a.initialize(inside, project=self.project, plan_path=self.plan)
            self.a.admit(inside, intent(), backend=self.backend)
        self.assertFalse((inside / 'contract.json').exists())


class ProvenanceTests(HomeFixture):
    """Ports of provenance/host-pending-refused and provenance/pending-protected-before-dispatch."""

    def host_written(self):
        path = self.home / 'pending.json'
        data = path.read_text()
        os.chmod(path, 0o600)
        path.unlink()
        with path.open('x') as stream:
            stream.write(data)
        os.chmod(path, 0o600)
        return path

    def test_pending_is_protected_before_any_dispatch(self):
        self.initialize()
        self.assertFalse((self.home / 'pending.json').stat().st_mode & 0o222)
        self.assertEqual(self.starts(), [])

    def test_host_written_pending_with_write_bits_is_refused(self):
        self.initialize()
        self.host_written()
        self.refused('pending')
        self.assertFalse((self.home / 'contract.json').exists())

    def test_pending_made_writable_is_refused(self):
        self.initialize()
        os.chmod(self.home / 'pending.json', 0o644)
        self.refused('pending')

    def test_plan_changed_after_initialize_is_refused(self):
        self.initialize()
        self.plan.write_text('# Plan\nSomething else entirely.\n')
        self.refused('plan')


@unittest.skipUnless(HAVE_BWRAP, 'bubblewrap unavailable (admission may run the baseline before these checks)')
class EarlyRefusalTests(HomeFixture):
    def setUp(self):
        super().setUp()
        self.initialize()

    def test_unsupported_capability_kinds_are_refused_in_this_build(self):
        self.assertEqual(tuple(self.a.UNSUPPORTED), ('ssh', 'browser', 'external-service', 'protected-configuration'))
        for kind in self.a.UNSUPPORTED:
            with self.subTest(kind=kind):
                capability = {'id': 'cap-' + kind, 'kind': kind, 'argv': ['/usr/bin/true'], 'timeout_seconds': 5}
                self.refused('unsupported in this build', intent(required_capabilities=[capability]))

    def test_owned_paths_must_be_relative_inside_project_and_not_tests(self):
        for owned in (['../outside.py'], ['/etc/hostname'], ['tests/test_core.py'], ['tests'], ['pkg/../../x.py']):
            with self.subTest(owned=owned):
                self.refused('owned', intent(owned_paths=owned))

    def test_tests_directory_is_required(self):
        shutil.rmtree(self.project / 'tests')
        self.refused('tests')

    def test_unsupported_test_command_is_refused(self):
        for tests in ({'format': 'nose'}, {'timeout_seconds': 0}, {'argv': [PY, 'run_tests.py']}):
            with self.subTest(tests=tests):
                with self.assertRaises(self.a.AdmissionRefused):
                    self.admit(intent(tests=tests))
                self.assertEqual(self.starts(), [])

    def test_reserved_independent_review_id_is_refused(self):
        acceptance = intent()['acceptance'] + [{'id': 'independent-review', 'proposition': 'x', 'selectors': [VALUE]}]
        self.refused('independent-review', intent(acceptance=acceptance))

    def test_timeouts_must_fit_the_checkpoint(self):
        self.refused('checkpoint', intent(timeout_seconds=1200))
        self.refused('checkpoint', intent(tests={'timeout_seconds': 950}))

    def test_packet_needs_structural_lines(self):
        self.refused('packet', intent(packet={'text': 'Please just fix it.'}))

    def test_packet_ownership_must_be_within_owned_paths(self):
        text = PACKET.replace('Owned paths: pkg/core.py', 'Owned paths: pkg/other.py')
        self.refused('ownership', intent(packet={'text': text}))

    def test_backend_project_preflight_refusal_is_named(self):
        (self.project / '.stub-refuse').write_text('refuse')
        self.refused('preflight')

    def test_oracle_command_grant_refusal_is_named(self):
        backend = self.backend
        b = self.b

        class NoGrants:
            def __getattr__(self, name):
                return getattr(backend, name)

            def check_grants(self, route, commands):
                raise b.UnsupportedGrant('CONTROL grant refusal')

        self.refused('grant', backend=NoGrants())


@unittest.skipUnless(HAVE_BWRAP, 'bubblewrap unavailable')
class BaselineTests(HomeFixture):
    def setUp(self):
        super().setUp()
        self.initialize()

    def test_unresolvable_selector_is_refused(self):
        acceptance = [{'id': 'values', 'proposition': 'x', 'selectors': ['test_core.CoreTests.test_nothing_here']}]
        self.refused('selector', intent(acceptance=acceptance))

    def test_empty_selectors_are_refused(self):
        self.refused('selector', intent(acceptance=[{'id': 'values', 'proposition': 'x', 'selectors': []}]))

    def test_nonregression_baseline_must_match_expected_failures_exactly(self):
        for expected in ([], [VALUES], [MORE, VALUES, VALUE]):
            with self.subTest(expected=expected):
                self.refused('baseline', intent(tests={'expected_baseline_failures': expected}))

    def test_all_green_refuses_a_red_baseline(self):
        self.refused('baseline', intent(tests={'baseline_policy': 'all-green', 'expected_baseline_failures': []}))
        self.refused('baseline', intent(tests={'baseline_policy': 'all-green',
                                               'expected_baseline_failures': [MORE, VALUES]}))

    def test_all_green_admits_a_green_baseline(self):
        (self.project / 'tests' / 'test_core.py').write_text(GREEN_TESTS_PY)
        result = self.admit(intent(acceptance=[{'id': 'value', 'proposition': 'VALUE is one', 'selectors': [VALUE]}],
                                   tests={'baseline_policy': 'all-green', 'expected_baseline_failures': []}))
        self.assertEqual(result['status'], 'ADMITTED')

    def test_refused_admission_can_be_corrected_before_freeze(self):
        self.refused('baseline', intent(tests={'expected_baseline_failures': []}))
        self.assertFalse((self.home / 'contract.json').exists())
        self.assertEqual(self.admit()['status'], 'ADMITTED')


@unittest.skipUnless(HAVE_BWRAP, 'bubblewrap unavailable')
class FreezeTests(HomeFixture):
    def setUp(self):
        super().setUp()
        self.initialize()
        self.result = self.admit()

    def test_successful_freeze_writes_read_only_contract_and_open_gaps(self):
        self.assertEqual(self.result['status'], 'ADMITTED')
        for name in PINNED:
            with self.subTest(name=name):
                path = self.home / name
                self.assertTrue(path.is_file(), name)
                self.assertFalse(path.stat().st_mode & 0o222, name + ' must be read-only')
        self.assertTrue((self.home / 'decision.json').is_file())
        binding = json.loads((self.home / 'binding.json').read_text())
        self.assertEqual(binding['sha256'], hashlib.sha256((self.home / 'contract.json').read_bytes()).hexdigest())
        contract = json.loads((self.home / 'contract.json').read_text())
        self.assertTrue(os.path.isabs(contract['tests']['argv'][0]))
        self.assertTrue({VALUE, VALUES, MORE} <= set(contract['frozen_ids']))
        checkpoint = json.loads((self.home / 'elapsed-checkpoint.json').read_text())
        self.assertEqual(checkpoint['max_seconds'], 900)
        self.assertIsInstance(checkpoint['opened_at'], (int, float))
        decision = json.loads((self.home / 'decision.json').read_text())
        self.assertTrue(isinstance(decision['unit_id'], str) and decision['unit_id'])
        gaps = {g['id']: g['state'] for g in decision['gaps']}
        self.assertEqual(gaps, {'values': 'OPEN', 'more': 'OPEN', 'independent-review': 'OPEN'})
        self.assertEqual(len(decision['gaps']), 3)
        state = self.state()
        self.assertEqual(state['status'], 'ADMITTED')
        self.assertIsInstance(state['version'], int)
        self.assertEqual(self.starts(), [], 'admission never starts a worker')

    def test_relative_interpreter_is_resolved_to_absolute(self):
        other = self.base / 'private' / 'inv2'
        self.a.initialize(other, project=self.project, plan_path=self.plan)
        value = intent(tests={'argv': ['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-v']})
        self.assertEqual(self.a.admit(other, value, backend=self.backend)['status'], 'ADMITTED')
        argv0 = json.loads((other / 'contract.json').read_text())['tests']['argv'][0]
        self.assertTrue(os.path.isabs(argv0))
        self.assertTrue(Path(argv0).exists())

    def test_identical_readmit_is_idempotent(self):
        before = {name: (self.home / name).read_bytes() for name in PINNED + ('decision.json',)}
        self.assertEqual(self.admit()['status'], 'ADMITTED')
        after = {name: (self.home / name).read_bytes() for name in PINNED + ('decision.json',)}
        self.assertEqual(before, after)

    def test_changed_readmit_is_refused_as_immutable(self):
        before = {name: (self.home / name).read_bytes() for name in PINNED + ('decision.json',)}
        with self.assertRaises(self.a.AdmissionRefused) as caught:
            self.admit(intent(timeout_seconds=45))
        self.assertIn('immutable', str(caught.exception).lower())
        after = {name: (self.home / name).read_bytes() for name in PINNED + ('decision.json',)}
        self.assertEqual(before, after)
        self.assertEqual(self.starts(), [])


class StdlibTests(unittest.TestCase):
    def test_admission_module_is_stdlib_only(self):
        module = load(self, 'supervise.controller.admission')
        names = set()
        for node in ast.walk(ast.parse(Path(module.__file__).read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names -= {'__future__', 'supervise'}
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))


if __name__ == '__main__':
    unittest.main()
