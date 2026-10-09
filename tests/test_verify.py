# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S5-4b: `supervise.verify.verify_invocation`.

A real closed ACCEPTED invocation is produced once per class through the S3-1 admission, the S3-3 Controller
and a StubBackend delegate that writes the author's fix (the fixture approach of
tests/test_controller_transaction.py). Receipts and route records carry absolute paths into the invocation
home, so each test restores the pristine closed home *at the same path* before tampering with it.

Check names assumed (see NOTES.md); every result lists all of them, each `{"name", "ok", "detail"}`:
  journal-schema       every STATE.jsonl line parses and is schema-valid
  starts-accounted     every start is accounted exactly once (finish or reconcile)
  closure              an outcome_closed record exists and closure.json equals it
  receipts             every finish's routing receipt verifies (kernel.attest.verify_receipt)
  crew-files           every crew-N-<sha>.json hashes to its name
  accepted-review      accepted-review.json exists and binds to a completed accepting review start
  review-independence  the accepted reviewer's family (from the sealed crew records) differs from every author
  review-snapshot      the accepted review's source snapshot equals the closure-time snapshot
Checks are evaluated independently from the raw files, so one tampering does not hide another check.
"""
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import traceback
import unittest
from unittest import mock

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
PY = sys.executable
HAVE_SANDBOX = shutil.which('bwrap') is not None and PY.startswith(('/usr/', '/bin/'))
PUB = 'd' * 64
CHECKS = ('journal-schema', 'starts-accounted', 'closure', 'receipts', 'crew-files', 'accepted-review',
          'review-independence', 'review-snapshot')
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
ORIGINAL = 'VALUE = 1\nOTHER = 2\n'
FIX = 'VALUE = 1\nOTHER = 3\n'
OWNED = ['pkg/core.py']


def field(name, value):
    return name + ': ' + value


PACKET = '\n'.join([field('Kind', 'impl'), field('Owned paths', 'pkg/core.py'),
                    field('Declared size', '1 file, 2 changed lines'),
                    field('Oracle', 'python3 -m unittest discover -s tests'), field('Domain', 'none'),
                    field('Review', 'independent reviewer'), '', 'Make OTHER three in pkg/core.py.'])


def load(test, name='supervise.verify'):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        test.fail(name + ' is not implemented: ' + str(error))


def intent():
    return {
        'acceptance': [{'id': 'values', 'proposition': 'OTHER is three', 'selectors': [VALUES]}],
        'tests': {'argv': [PY, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], 'format': 'unittest',
                  'timeout_seconds': 60, 'baseline_policy': 'nonregression', 'expected_baseline_failures': [VALUES]},
        'owned_paths': list(OWNED),
        'checkpoint': {'max_starts': 6, 'max_seconds': 900, 'first_value_seconds': 300},
        'timeout_seconds': 30,
        'packet': {'text': PACKET, 'estimated_files': 1, 'estimated_lines': 2},
    }


class FixtureBackend:
    """StubBackend delegate; writes the fix into the owned file when an implementation attempt is interpreted."""

    def __init__(self, stub, project):
        self.stub, self.project, self.roles = stub, project, {}

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
        return spec

    def interpret(self, spec, capture):
        outcome = self.stub.interpret(spec, capture)
        if self.roles.get(id(spec), 'review' if spec.requires_review_sandbox else 'impl') == 'impl':
            (self.project / 'pkg' / 'core.py').write_text(FIX)
        return outcome


def make_writable(root):
    root = Path(root)
    if root.exists():
        for path in [root, *root.rglob('*')]:
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)


def snapshot_tree(root):
    root = Path(root)
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() and not p.is_symlink() else None,
                                       stat.S_IMODE(p.lstat().st_mode))
            for p in sorted(root.rglob('*'))}


def restore(source, target):
    make_writable(target)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, symlinks=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap and a /usr or /bin interpreter are required for the review view')
class ClosedInvocation(unittest.TestCase):
    """Builds one closed ACCEPTED invocation per class (plus a reviewed-but-not-closed copy)."""

    @classmethod
    def setUpClass(cls):
        cls.build_error = None
        cls.dir = tempfile.TemporaryDirectory()
        cls.base = Path(cls.dir.name).resolve()
        cls.env = mock.patch.dict(os.environ, {
            'HOME': str(cls.base / 'h'), 'SUPERVISE_STATE': str(cls.base / 'state'),
            'SUPERVISE_DATA': str(cls.base / 'data'), 'XDG_STATE_HOME': str(cls.base / 'xs'),
            'XDG_DATA_HOME': str(cls.base / 'xd'), 'XDG_CONFIG_HOME': str(cls.base / 'xc')})
        cls.env.start()
        try:
            cls.build()
        except Exception:
            cls.build_error = traceback.format_exc()

    @classmethod
    def build(cls):
        admission = importlib.import_module('supervise.controller.admission')
        transaction = importlib.import_module('supervise.controller.transaction')
        backend = importlib.import_module('supervise.backend')
        stub = importlib.import_module('supervise.backends.stub')
        journal = importlib.import_module('supervise.kernel.journal')
        os.environ.pop(journal.WORKER_ENV, None)
        (cls.base / 'h').mkdir()

        def route(rid, family, roles):
            return backend.Route(route_id=rid, harness='stub', vendor=family, model=rid, family=family,
                                 roles=frozenset(roles), review_isolation=True, price='unknown')
        routes = (route('author-a', 'alpha', {'impl'}), route('review-g', 'gamma', {'review'}),
                  route('review-d', 'delta', {'review'}))
        cls.project = cls.base / 'project'
        (cls.project / 'pkg').mkdir(parents=True)
        (cls.project / 'tests').mkdir()
        (cls.project / 'pkg' / '__init__.py').write_text('')
        (cls.project / 'pkg' / 'core.py').write_text(ORIGINAL)
        (cls.project / 'tests' / 'test_core.py').write_text(TESTS_PY)
        plan = cls.project / 'PLAN.md'
        plan.write_text('# Plan\nMake OTHER three.\n')
        (cls.base / 'private').mkdir(mode=0o700)
        cls.home = cls.base / 'private' / 'inv'
        fb = FixtureBackend(stub.StubBackend(routes, {}), cls.project)
        admission.initialize(cls.home, project=cls.project, plan_path=plan)
        admitted = admission.admit(cls.home, intent(), backend=fb)
        assert admitted['status'] == 'ADMITTED', admitted
        ctl = transaction.Controller(cls.home, backend=fb, publication_sha256=PUB)

        def do(action, **extra):
            version = json.loads((cls.home / 'state.json').read_text())['version']
            return ctl.perform(dict({'action': action, 'expected_version': version}, **extra))
        assert do('author', targets=['values'])['status'] == 'AUTHOR_COMPLETE'
        assert do('review')['status'] == 'REVIEW_PASSED'
        cls.reviewed = cls.base / 'pristine-reviewed'
        shutil.copytree(cls.home, cls.reviewed, symlinks=True)
        assert do('close')['status'] == 'ACCEPTED'
        cls.pristine = cls.base / 'pristine-closed'
        shutil.copytree(cls.home, cls.pristine, symlinks=True)

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        make_writable(cls.base)
        cls.dir.cleanup()

    def setUp(self):
        if self.build_error:
            self.fail('could not produce a closed invocation through the Controller:\n' + self.build_error)
        self.v = load(self)
        restore(self.pristine, self.home)

    # -- tampering helpers -------------------------------------------------------------------------------------
    def journal_lines(self):
        return [json.loads(line) for line in (self.home / 'STATE.jsonl').read_text().splitlines()]

    def write_journal(self, events):
        data = ''.join(json.dumps(event, allow_nan=False, separators=(',', ':')) + '\n' for event in events)
        path = self.home / 'STATE.jsonl'
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, 0o600)
        path.write_text(data)
        os.chmod(path, mode)

    def rewrite(self, path, value, canonical=False):
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, 0o600)
        if canonical:
            path.write_bytes(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())
        else:
            path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
        os.chmod(path, mode)

    def read(self, name):
        return json.loads((self.home / name).read_text())

    def verify(self, home=None):
        result = self.v.verify_invocation(home or self.home)
        json.dumps(result)
        self.assertIn(result['verdict'], ('PASS', 'FAIL'))
        names = [check['name'] for check in result['checks']]
        for check in result['checks']:
            self.assertEqual(set(check) >= {'name', 'ok', 'detail'}, True, check)
            self.assertIsInstance(check['ok'], bool)
        for name in CHECKS:
            self.assertIn(name, names)
        if any(not check['ok'] for check in result['checks']):
            self.assertEqual(result['verdict'], 'FAIL', 'a failing check fails the verdict')
        return result

    def assert_failed(self, check_name):
        result = self.verify()
        self.assertEqual(result['verdict'], 'FAIL', result)
        failing = [check['name'] for check in result['checks'] if not check['ok']]
        self.assertIn(check_name, failing, result)
        return result


class PassTests(ClosedInvocation):
    def test_closed_accepted_invocation_passes(self):
        result = self.verify()
        self.assertEqual(result['verdict'], 'PASS', result)
        self.assertTrue(all(check['ok'] for check in result['checks']), result)

    def test_string_home_is_accepted(self):
        self.assertEqual(self.v.verify_invocation(str(self.home))['verdict'], 'PASS')

    def test_verification_is_read_only_and_reruns_nothing(self):
        process = importlib.import_module('supervise.kernel.process')
        before = (snapshot_tree(self.home), snapshot_tree(self.project))

        def forbidden(*args, **kwargs):
            raise AssertionError('verify must not launch anything')
        with mock.patch.object(subprocess, 'Popen', forbidden), mock.patch.object(subprocess, 'run', forbidden), \
                mock.patch.object(process, 'run', forbidden), mock.patch.object(os, 'fork', forbidden):
            self.assertEqual(self.verify()['verdict'], 'PASS')
        self.assertEqual((snapshot_tree(self.home), snapshot_tree(self.project)), before)

    def test_tampered_home_verification_is_read_only(self):
        path = self.home / 'closure.json'
        closure = self.read('closure.json')
        closure['completed_starts'] += 1
        self.rewrite(path, closure)
        before = snapshot_tree(self.home)
        self.assertEqual(self.verify()['verdict'], 'FAIL')
        self.assertEqual(snapshot_tree(self.home), before)

    def test_reviewed_but_unclosed_invocation_fails_closure(self):
        restore(self.reviewed, self.home)
        self.assert_failed('closure')

    def test_empty_directory_fails(self):
        empty = self.base / 'empty'
        empty.mkdir(exist_ok=True)
        self.assertEqual(self.v.verify_invocation(empty)['verdict'], 'FAIL')


class JournalTamperTests(ClosedInvocation):
    def test_schema_invalid_journal_line(self):
        events = self.journal_lines()
        events[0]['packet_id'] = 12345
        self.write_journal(events)
        self.assert_failed('journal-schema')

    def test_unparseable_journal_line(self):
        path = self.home / 'STATE.jsonl'
        os.chmod(path, 0o600)
        with path.open('a') as stream:
            stream.write('{"kind": "start", truncated\n')
        self.assert_failed('journal-schema')

    def test_edited_start_route(self):
        events = self.journal_lines()
        start = next(e for e in events if e['kind'] == 'start' and e['start'] == 1)
        start['route'] = 'author-b'
        self.write_journal(events)
        self.assert_failed('receipts')

    def test_extra_start(self):
        events = self.journal_lines()
        extra = dict(next(e for e in events if e['kind'] == 'start' and e['start'] == 2))
        extra['start'] = 3
        extra['attempt_id'] = 'extra-attempt'
        events.insert(len(events) - 1, extra)
        self.write_journal(events)
        self.assert_failed('starts-accounted')

    def test_start_accounted_twice(self):
        events = self.journal_lines()
        finish = next(e for e in events if e['kind'] == 'finish' and e['start'] == 1)
        events.insert(len(events) - 1, dict(finish))
        self.write_journal(events)
        self.assert_failed('starts-accounted')


class ClosureTamperTests(ClosedInvocation):
    def test_closure_file_differs_from_record(self):
        closure = self.read('closure.json')
        closure['completed_starts'] += 1
        self.rewrite(self.home / 'closure.json', closure)
        self.assert_failed('closure')

    def test_outcome_closed_record_removed(self):
        events = [e for e in self.journal_lines() if e['kind'] != 'outcome_closed']
        self.write_journal(events)
        self.assert_failed('closure')

    def test_closure_file_removed(self):
        path = self.home / 'closure.json'
        os.chmod(path, 0o600)
        path.unlink()
        self.assert_failed('closure')


class CrewAndRouteTamperTests(ClosedInvocation):
    def test_crew_file_byte_flip(self):
        crew = sorted(self.home.glob('crew-1-*.json'))[0]
        data = crew.read_bytes()
        self.assertIn(b'Selected', data)
        os.chmod(crew, 0o600)
        crew.write_bytes(data.replace(b'Selected', b'Selectee', 1))
        os.chmod(crew, 0o444)
        self.assert_failed('crew-files')

    def test_route_record_change(self):
        path = self.home / 'route-1.json'
        record = self.read('route-1.json')
        record['class_key'] = record['class_key'] + '-forged'
        self.rewrite(path, record, canonical=True)
        self.assert_failed('receipts')

    def test_receipt_substitution(self):
        events = self.journal_lines()
        first = next(e for e in events if e['kind'] == 'finish' and e['start'] == 1)
        second = next(e for e in events if e['kind'] == 'finish' and e['start'] == 2)
        second['attempt']['routing_attestation'] = json.loads(json.dumps(first['attempt']['routing_attestation']))
        self.write_journal(events)
        self.assert_failed('receipts')


class AcceptedReviewTamperTests(ClosedInvocation):
    def test_accepted_review_removed(self):
        path = self.home / 'accepted-review.json'
        os.chmod(path, 0o600)
        path.unlink()
        self.assert_failed('accepted-review')

    def test_accepted_review_bound_to_a_different_snapshot(self):
        accepted = self.read('accepted-review.json')
        snapshot = accepted['source_snapshot']
        self.assertIsInstance(snapshot, dict)
        artifacts = snapshot['artifacts']
        name = sorted(artifacts)[0]
        artifacts[name] = 'f' * 64 if artifacts[name] != 'f' * 64 else 'e' * 64
        self.rewrite(self.home / 'accepted-review.json', accepted)
        self.assert_failed('review-snapshot')

    def test_accepted_reviewer_is_the_author(self):
        accepted = self.read('accepted-review.json')
        accepted['reviewer'] = 'author-a'
        self.rewrite(self.home / 'accepted-review.json', accepted)
        result = self.verify()
        self.assertEqual(result['verdict'], 'FAIL')
        failing = {c['name'] for c in result['checks'] if not c['ok']}
        self.assertTrue(failing & {'accepted-review', 'review-independence'}, result)

    def test_consistent_forgery_of_reviewer_family(self):
        """Every crew record re-hashed and re-bound so receipts verify; only independence is violated."""
        reviewer = self.read('accepted-review.json')['reviewer']
        events = self.journal_lines()
        author_family = None
        renamed = {}
        for crew_path in sorted(self.home.glob('crew-*.json')):
            crew = json.loads(crew_path.read_text())
            author_family = author_family or crew['selected']['author']['family']
            entries = [crew['selected']['author'], *crew['selected']['reviewers']]
            for entry in entries:
                if entry['route_id'] == reviewer:
                    entry['family'] = crew['selected']['author']['family']
            body = {k: v for k, v in crew.items() if k != 'crew_sha256'}
            crew['crew_sha256'] = digest(body)
            number = crew_path.name.split('-')[1]
            target = self.home / ('crew-%s-%s.json' % (number, crew['crew_sha256']))
            os.chmod(crew_path, 0o600)
            crew_path.unlink()
            target.write_bytes(json.dumps(crew, sort_keys=True, separators=(',', ':'),
                                          ensure_ascii=False).encode())
            os.chmod(target, 0o444)
            renamed[str(crew_path)] = (str(target), crew['crew_sha256'])
        for event in events:
            if event['kind'] != 'finish':
                continue
            route_path = self.home / ('route-%d.json' % event['start'])
            record = json.loads(route_path.read_text())
            if record['crew_path'] in renamed:
                record['crew_path'], record['crew_sha256'] = renamed[record['crew_path']]
                self.rewrite(route_path, record, canonical=True)
            attestation = event['attempt']['routing_attestation']
            attestation['crew_sha256'] = record['crew_sha256']
            attestation['sha256'] = digest(record)
            attestation['receipt_sha256'] = digest({k: v for k, v in attestation.items() if k != 'receipt_sha256'})
        self.write_journal(events)
        attest = importlib.import_module('supervise.kernel.attest')
        starts = {e['start']: e for e in events if e['kind'] == 'start'}
        for event in events:
            if event['kind'] == 'finish':
                attest.verify_receipt(self.home, starts[event['start']], event)   # the forgery is consistent
        result = self.assert_failed('review-independence')
        for name in ('receipts', 'crew-files'):
            self.assertTrue(next(c for c in result['checks'] if c['name'] == name)['ok'], (name, result))


class StdlibTests(unittest.TestCase):
    def test_verify_is_stdlib_only(self):
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


if __name__ == '__main__':
    unittest.main()
