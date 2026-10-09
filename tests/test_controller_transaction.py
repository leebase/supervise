# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S3-3: `supervise.controller.transaction.Controller`.

Driven end to end on a temporary fixture project with the real kernel modules, the real S3-1 admission and the
deterministic StubBackend. No provider, Router or network is used.

How the fixture simulates implementation
----------------------------------------
The stub worker only prints text. `FixtureBackend` delegates every RoutingBackend method to a StubBackend and,
when the controller calls `interpret()` for an implementation attempt (i.e. after the kernel captured the author
process and before the controller finishes the start and observes tests), it writes the next queued content into
the owned file `pkg/core.py`. So "the author changed the source" happens inside the attempt, exactly where a real
worker's edits would land. `FixtureBackend` also records every `plan_crew` request and every `prepare_launch`
(route, role), which is how the oracle counts launches per route. Optional overrides inject a misbehaving
backend (non-independent crew, forged final text, a review spec without the sandbox).

Interruption seams (the implementation must look these up as module attributes at call time):
* `supervise.kernel.process.run` is wrapped by `RunRecorder`, which classifies each call (author, reviewer
  inside the review sandbox, verify.py inside the view, other) and can simulate the host dying mid-launch by
  raising `HostDied` (a BaseException, like a SIGKILLed host; it must not be accounted in except/finally).
* `supervise.kernel.journal.finish_start` / `reserve_start` are wrapped to die after a completed finish or to
  refuse a reservation.
* `supervise.controller.budget.remaining` is patched to simulate an expired elapsed window.

Status vocabulary assumed (production v2.8.2 names; see NOTES.md): REVIEW_PASSED, REVIEW_BLOCKING,
REVIEW_INCOMPLETE, RECONCILE_REQUIRED, CHECKPOINT_EXHAUSTED, ACCEPTED. Refusals raise ValueError (any subclass)
whose message contains the named fragment. The current version is `state.json["version"]`.
"""
import ast
import dataclasses
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
PY = sys.executable
HAVE_SANDBOX = shutil.which('bwrap') is not None and PY.startswith(('/usr/', '/bin/'))
PUB = 'c' * 64
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
VALUES, MORE = 'test_core.CoreTests.test_values', 'test_core.CoreTests.test_more'
ORIGINAL = 'VALUE = 1\nOTHER = 2\n'
FIX = 'VALUE = 1\nOTHER = 3\nMORE = 4\n'
PARTIAL = 'VALUE = 1\nOTHER = 3\n'
OWNED = ['pkg/core.py']
PACKET = '\n'.join(['Kind: impl', 'Owned paths: pkg/core.py', 'Declared size: 1 file, 5 changed lines',
                    'Oracle: python3 -m unittest discover -s tests', 'Domain: none', 'Review: independent reviewer',
                    '', 'Make OTHER three and add MORE = 4 in pkg/core.py.'])
GAPS = ['values', 'more']
KINDS = {'start': 'state-start', 'finish': 'state-finish', 'reconcile': 'state-reconcile',
         'outcome_closed': 'state-outcome-closed'}


class HostDied(BaseException):
    """Simulated death of the controller host (never an ordinary, catchable Exception)."""


def load(test, name):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        test.fail(name + ' is not implemented: ' + str(error))


def make_routes(b):
    def route(rid, family, roles, isolation=True):
        return b.Route(route_id=rid, harness='stub', vendor=family, model=rid, family=family,
                       roles=frozenset(roles), review_isolation=isolation, price='unknown')
    return {r.route_id: r for r in (
        route('author-a', 'alpha', {'impl'}), route('author-b', 'beta', {'impl'}),
        route('review-g', 'gamma', {'review'}), route('review-d', 'delta', {'review'}),
        route('review-a', 'alpha', {'review'}), route('review-open', 'epsilon', {'review'}, isolation=False))}


def intent(**overrides):
    value = {
        'acceptance': [{'id': 'values', 'proposition': 'OTHER is three', 'selectors': [VALUES]},
                       {'id': 'more', 'proposition': 'MORE is four', 'selectors': [MORE]}],
        'tests': {'argv': [PY, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], 'format': 'unittest',
                  'timeout_seconds': 60, 'baseline_policy': 'nonregression', 'expected_baseline_failures': [MORE, VALUES]},
        'owned_paths': list(OWNED),
        'checkpoint': {'max_starts': 8, 'max_seconds': 900, 'first_value_seconds': 300},
        'timeout_seconds': 30,
        'packet': {'text': PACKET, 'estimated_files': 1, 'estimated_lines': 5},
    }
    for key, item in overrides.items():
        value[key] = dict(value[key], **item) if isinstance(value.get(key), dict) else item
    return json.loads(json.dumps(value))


class FixtureBackend:
    """StubBackend delegate that records calls and simulates the author's source edits (see module docstring)."""

    def __init__(self, stub, project):
        self.stub, self.project = stub, project
        self.requests, self.launches, self.specs, self.edits = [], [], [], []
        self.roles = {}
        self.plan_override = None
        self.review_final_text = None
        self.review_sandbox = True

    def identity(self):
        return self.stub.identity()

    def plan_crew(self, request):
        self.requests.append(request)
        return self.plan_override(request) if self.plan_override else self.stub.plan_crew(request)

    def check_grants(self, route, commands):
        return self.stub.check_grants(route, commands)

    def preflight_project(self, route, project):
        return self.stub.preflight_project(route, project)

    def prepare_launch(self, request):
        self.launches.append((request.route.route_id, request.role))
        spec = self.stub.prepare_launch(request)
        if request.role == 'review' and not self.review_sandbox:
            spec = dataclasses.replace(spec, requires_review_sandbox=False)
        self.specs.append((request.role, tuple(spec.argv)))
        self.roles[id(spec)] = request.role
        return spec

    def interpret(self, spec, capture):
        outcome = self.stub.interpret(spec, capture)
        role = self.roles.get(id(spec), 'review' if spec.requires_review_sandbox else 'impl')
        if role == 'impl' and self.edits:
            (self.project / 'pkg' / 'core.py').write_text(self.edits.pop(0))
        if role == 'review' and self.review_final_text is not None:
            outcome = dataclasses.replace(outcome, final_text=self.review_final_text)
        return outcome

    def count(self, route=None, role=None):
        return sum(1 for r, k in self.launches if route in (None, r) and role in (None, k))


class RunRecorder:
    """Wraps supervise.kernel.process.run; classifies calls; an optional hook may replace or interrupt one."""

    def __init__(self, real, backend):
        self.real, self.backend = real, backend
        self.calls, self.hook = [], None

    def kind(self, argv):
        if any(a.endswith('verify.py') for a in argv):
            return 'verify'
        for role, spec in reversed(self.backend.specs):
            if argv == spec:
                return 'author' if role == 'impl' else 'reviewer-unsandboxed'
            if len(argv) > len(spec) and argv[-len(spec):] == spec:
                return 'reviewer' if role == 'review' else 'author'
        return 'other'

    def __call__(self, argv, *args, **kwargs):
        argv = tuple(str(a) for a in argv)
        kind = self.kind(argv)
        self.calls.append({'kind': kind, 'argv': argv, 'timeout': kwargs.get('timeout')})
        if self.hook is not None:
            replaced = self.hook(kind, argv, kwargs)
            if replaced is not None:
                return replaced
        return self.real(argv, *args, **kwargs)

    def kinds(self):
        return [c['kind'] for c in self.calls if c['kind'] != 'other']


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap and a /usr or /bin interpreter are required for the review view')
class TransactionFixture(unittest.TestCase):
    def setUp(self):
        self.admission = load(self, 'supervise.controller.admission')
        self.t = load(self, 'supervise.controller.transaction')
        self.budget = load(self, 'supervise.controller.budget')
        self.classify = load(self, 'supervise.controller.classify')
        self.b = load(self, 'supervise.backend')
        self.stub = load(self, 'supervise.backends.stub')
        self.journal = load(self, 'supervise.kernel.journal')
        self.process = load(self, 'supervise.kernel.process')
        self.attest = load(self, 'supervise.kernel.attest')
        self.novelty = load(self, 'supervise.kernel.novelty')
        self.schemas = load(self, 'supervise.schemas')
        self.routes = make_routes(self.b)
        self.dir = tempfile.TemporaryDirectory()
        self.base = Path(self.dir.name)
        (self.base / 'private').mkdir(mode=0o700)
        self.children = []
        self.serial = 0
        os.environ.pop(self.journal.WORKER_ENV, None)

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
                child.wait(5)
        for path in Path(self.dir.name).rglob('*'):
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self.dir.cleanup()

    # -- fixture -------------------------------------------------------------------------------------------
    def begin(self, script=None, route_ids=None, **intent_overrides):
        """Fresh project + invocation home, admitted with a StubBackend; returns the Controller."""
        self.serial += 1
        self.project = self.base / f'project-{self.serial}'
        (self.project / 'pkg').mkdir(parents=True)
        (self.project / 'tests').mkdir()
        (self.project / 'pkg' / '__init__.py').write_text('')
        (self.project / 'pkg' / 'core.py').write_text(ORIGINAL)
        (self.project / 'tests' / 'test_core.py').write_text(TESTS_PY)
        plan = self.project / 'PLAN.md'
        plan.write_text('# Plan\nMake OTHER three and MORE four.\n')
        self.home = self.base / 'private' / f'inv-{self.serial}'
        routes = tuple(self.routes[r] for r in (route_ids or self.routes))
        self.fb = FixtureBackend(self.stub.StubBackend(routes, script or {}), self.project)
        self.admission.initialize(self.home, project=self.project, plan_path=plan)
        admitted = self.admission.admit(self.home, intent(**intent_overrides), backend=self.fb)
        self.assertEqual(admitted['status'], 'ADMITTED')
        self.fb.requests.clear(); self.fb.launches.clear(); self.fb.specs.clear()
        self.recorder = RunRecorder(self.process.run, self.fb)
        patcher = mock.patch.object(self.process, 'run', self.recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.restart()
        return self.ctl

    def restart(self):
        """A new controller process on the same home (used after a simulated host death)."""
        self.ctl = self.t.Controller(self.home, backend=self.fb, publication_sha256=PUB)
        return self.ctl

    def version(self):
        return json.loads((self.home / 'state.json').read_text())['version']

    def state(self):
        return json.loads((self.home / 'state.json').read_text())

    def decision(self):
        return json.loads((self.home / 'decision.json').read_text())

    def do(self, action, **extra):
        return self.ctl.perform(dict({'action': action, 'expected_version': self.version()}, **extra))

    def author(self, content=FIX, targets=GAPS, action='author'):
        if content is not None:
            self.fb.edits.append(content)
        return self.do(action, targets=list(targets))

    def repair(self, content, targets=GAPS):
        return self.author(content, targets, action='repair')

    def events(self):
        return self.journal.read_events(self.home / 'STATE.jsonl') if (self.home / 'STATE.jsonl').exists() else []

    def starts(self):
        return [e for e in self.events() if e['kind'] == 'start']

    def accounting(self, number):
        return [e for e in self.events() if e['kind'] in ('finish', 'reconcile') and e['start'] == number]

    def refused(self, fragment, action, **extra):
        before = (len(self.starts()), list(self.fb.launches))
        with self.assertRaises(ValueError) as caught:
            self.do(action, **extra)
        if fragment:
            fragments = (fragment,) if isinstance(fragment, str) else tuple(fragment)
            message = str(caught.exception).lower()
            self.assertTrue(any(f.lower() in message for f in fragments), (fragments, message))
        self.assertEqual((len(self.starts()), list(self.fb.launches)), before, 'refused before any start')
        return caught.exception

    def interrupt(self, kind, label=None):
        """Arm the recorder: the next `kind` launch spawns a live child, reports it, then the host dies."""
        def hook(seen, argv, kwargs):
            if seen != kind:
                return None
            child = subprocess.Popen([PY, '-c', 'import time; time.sleep(60)'])
            self.children.append(child)
            if kwargs.get('on_spawn') is not None:
                kwargs['on_spawn'](self.process.process_identity(child.pid))
            if label is not None:
                out = Path(kwargs['stdout_path'])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(label)
            self.recorder.hook = None
            raise HostDied('CONTROL host died during ' + kind)
        self.recorder.hook = hook

    def stop_children(self):
        for child in self.children:
            if child.poll() is None:
                child.send_signal(signal.SIGKILL)
                child.wait(5)

    def assert_recovery_retains_author(self, failed_reviewer):
        """The recovery review planned with the pinned author, the failed reviewer excluded, no author re-run."""
        recovery = self.fb.requests[-1]
        self.assertEqual(recovery.pinned_author, 'author-a')
        self.assertIn('author-a', recovery.prior_authors)
        self.assertIn(failed_reviewer, recovery.failed_reviewers)
        self.assertFalse(recovery.require_review_fallback)
        author_task = self.fb.requests[0].task
        self.assertEqual((recovery.task.size, recovery.task.languages), (author_task.size, author_task.languages))
        starts = self.starts()
        self.assertNotEqual(starts[-1]['route'], failed_reviewer)
        self.assertEqual(starts[-1]['route'], 'review-d')
        self.assertEqual(self.fb.count(role='impl'), 1, 'reviewer recovery never re-runs the author')
        self.assertEqual(sum(1 for s in starts if s['route'] == 'author-a'), 1)
        self.assertEqual(sum(1 for c in self.recorder.calls if c['kind'] == 'author'), 1)


class AttestationAndCrewTests(TransactionFixture):
    def test_attestation_receipt_shares_attempt_id(self):
        """attestation/receipt-shares-attempt-id"""
        self.begin()
        self.author(FIX)
        start, = self.starts()
        finish, = self.accounting(1)
        self.assertEqual(finish['kind'], 'finish')
        attempt = finish['attempt']
        self.assertTrue(isinstance(start['attempt_id'], str) and start['attempt_id'])
        self.assertEqual(attempt['attempt_id'], start['attempt_id'])
        self.assertEqual(attempt['routing_attestation']['attempt_id'], attempt['attempt_id'])
        self.assertEqual(self.attest.verify_receipt(self.home, start, finish)['attempt_id'], start['attempt_id'])
        self.assertEqual(self.schemas.validate('attempt-outcome', attempt), [])
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')
        second = self.starts()[1]
        self.assertNotEqual(second['attempt_id'], start['attempt_id'])
        self.attest.verify_receipt(self.home, second, self.accounting(2)[0])

    def test_route_and_crew_records_are_sealed_and_bound(self):
        self.begin()
        self.author(FIX)
        route_path = self.home / 'route-1.json'
        record = json.loads(route_path.read_text())
        self.assertFalse(route_path.stat().st_mode & 0o222)
        self.assertEqual(self.schemas.validate('route-record', record), [])
        self.assertEqual((record['role'], record['route'], record['publication_sha256']), ('impl', 'author-a', PUB))
        self.assertEqual(record['contract_sha256'], hashlib.sha256((self.home / 'contract.json').read_bytes()).hexdigest())
        self.assertEqual(record['transaction'], self.decision()['unit_id'])
        crews = sorted(self.home.glob('crew-1-*.json'))
        self.assertEqual(len(crews), 1)
        crew = json.loads(crews[0].read_text())
        self.assertFalse(crews[0].stat().st_mode & 0o222)
        self.assertEqual(self.schemas.validate('crew', crew), [])
        digest = self.attest.digest({k: v for k, v in crew.items() if k != 'crew_sha256'})
        self.assertEqual(crew['crew_sha256'], digest)
        self.assertEqual(crews[0].name, f'crew-1-{digest}.json')
        self.assertEqual(record['crew_sha256'], digest)

    def test_author_crew_request_uses_frozen_classification_and_fallback(self):
        self.begin()
        self.author(FIX)
        request = self.fb.requests[0]
        self.assertEqual(request.task, self.classify.classify(PACKET, OWNED, role='impl'))
        self.assertEqual((request.prior_authors, request.failed_authors, request.failed_reviewers), ((), (), ()))
        self.assertIsNone(request.pinned_author)
        self.assertTrue(request.require_review_fallback)
        author_calls = [c for c in self.recorder.calls if c['kind'] == 'author']
        self.assertEqual(len(author_calls), 1)
        self.assertTrue(0 < author_calls[0]['timeout'] <= 30, 'worker timeout comes from budget.worker_allocation')

    def test_no_qualified_review_crew_refuses_before_start(self):
        """crew/no-qualified-review-crew-refuses"""
        self.begin(route_ids=('author-a', 'author-b', 'review-g', 'review-a', 'review-open'))
        self.refused(None, 'author', targets=GAPS)
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.fb.launches, [])
        self.assertFalse((self.home / 'route-1.json').exists())
        self.assertEqual(self.recorder.kinds(), [])

    def test_independence_violation_refused_before_any_start(self):
        self.begin()
        r = self.routes

        def crew(author, *reviewers):
            return lambda request: self.b.CrewPlan(author=r[author], reviewers=tuple(r[x] for x in reviewers),
                                                   rationale='CONTROL', excluded={}, cost_status='unavailable', inputs={})
        cases = {'same family as author': crew('author-a', 'review-a', 'review-g'),
                 'reviewer without review isolation': crew('author-a', 'review-open', 'review-g'),
                 'reviewer without review role': crew('author-a', 'author-b', 'review-g')}
        for name, plan in cases.items():
            with self.subTest(case=name):
                self.fb.plan_override = plan
                self.refused(('independen', 'family', 'isolation', 'role'), 'author', targets=GAPS)
        self.fb.plan_override = None
        self.author(FIX)
        self.assertEqual(len(self.starts()), 1)
        self.fb.plan_override = crew('author-b', 'review-g', 'review-a')       # alpha is a prior author family
        self.refused(('independen', 'family'), 'author', targets=GAPS)
        self.assertEqual(len(self.starts()), 1)

    def test_author_platform_timeout_steers_next_crew_away(self):
        self.begin(script={'author-a': ('platform_timeout',)})
        self.author(None)
        self.author(FIX)
        request = self.fb.requests[-1]
        self.assertIn('author-a', request.failed_authors)
        self.assertIn('author-a', request.prior_authors)
        self.assertEqual([s['route'] for s in self.starts()], ['author-a', 'author-b'])

    def test_author_worker_failure_is_not_a_platform_failure(self):
        self.begin(script={'author-a': ('crash',)})
        self.author(None)
        self.author(FIX)
        self.assertEqual(self.fb.requests[-1].failed_authors, ())
        self.assertEqual([s['route'] for s in self.starts()], ['author-a', 'author-a'])

    def test_prestart_refusal_route_is_archived(self):
        """route/prestart-refusal-archived: the host dies after retaining route-1.json, before the reservation."""
        self.begin()
        real = self.journal.reserve_start
        unit = self.decision()['unit_id']

        def die_before_reservation(*args, **kwargs):
            raise HostDied('CONTROL host died before the reservation')
        with mock.patch.object(self.journal, 'reserve_start', die_before_reservation):
            with self.assertRaises(HostDied):
                self.author(None)
        self.assertEqual(self.starts(), [])
        route = self.home / 'route-1.json'
        self.assertTrue(route.is_file(), 'route-N.json is retained before the reservation')
        old = route.read_bytes()
        self.assertIs(self.journal.reserve_start, real)
        self.restart()
        if self.author(FIX).get('status') == 'RECONCILE_REQUIRED':    # an explicit reconcile is also acceptable
            self.do('reconcile')
            self.author(None)
        archives = sorted(self.home.glob('route-refused-prestart-*.json'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), old)
        start, = self.starts()
        self.attest.verify_receipt(self.home, start, self.accounting(1)[0])
        self.assertEqual(self.decision()['unit_id'], unit, 'no new transaction')
        self.assertEqual(sum(1 for c in self.recorder.calls if c['kind'] == 'author'), 1)


class VersionAndTargetTests(TransactionFixture):
    def tree(self):
        return {str(p.relative_to(self.home)): (p.read_bytes() if p.is_file() else None, p.stat().st_mode & 0o777)
                for p in sorted(self.home.rglob('*')) if not p.name.endswith('.lock')}

    def test_stale_expected_version_is_refused_with_nothing_written(self):
        self.begin()
        current = self.version()
        for stale in (current - 1, current + 1, str(current)):
            with self.subTest(stale=stale):
                before = self.tree()
                with self.assertRaises(ValueError):
                    self.ctl.perform({'action': 'author', 'expected_version': stale, 'targets': GAPS})
                self.assertEqual(self.tree(), before)
                self.assertEqual(self.fb.launches, [])
        self.author(FIX)
        self.assertGreater(self.version(), current, 'every write bumps the version')
        before = self.tree()
        with self.assertRaises(ValueError):
            self.ctl.perform({'action': 'review', 'expected_version': current})
        self.assertEqual(self.tree(), before)

    def test_author_targets_must_be_open_gap_ids(self):
        self.begin()
        self.refused('dispatch must advance an open parent gap', 'author', targets=['not-a-gap'])
        self.refused('dispatch must advance an open parent gap', 'author', targets=['values', 'not-a-gap'])
        self.assertFalse((self.home / 'route-1.json').exists())

    def test_review_requires_a_finished_author_start(self):
        self.begin()
        self.refused('author', 'review')


class ReviewTests(TransactionFixture):
    def test_happy_path_review_accept_and_close(self):
        self.begin()
        self.author(FIX)
        reviewed = self.do('review')
        self.assertEqual(reviewed['status'], 'REVIEW_PASSED')
        accepted = json.loads((self.home / 'accepted-review.json').read_text())
        self.assertEqual(accepted['source_snapshot'], self.novelty.snapshot(self.project, OWNED))
        kinds = self.recorder.kinds()
        self.assertEqual(kinds, ['author', 'verify', 'reviewer', 'verify'],
                         'verify.py runs inside the view before and after the sandboxed reviewer')
        reviewer = next(c['argv'] for c in self.recorder.calls if c['kind'] == 'reviewer')
        self.assertTrue(reviewer[0].endswith('bwrap'))
        self.assertIn('/review', reviewer)
        closed = self.do('close')
        self.assertEqual(closed['status'], 'ACCEPTED')
        events = self.events()
        self.assertEqual([e['kind'] for e in events], ['start', 'finish', 'start', 'finish', 'outcome_closed'])
        for event in events:
            self.assertEqual(self.schemas.validate(KINDS[event['kind']], event), [], event['kind'])
        closure = json.loads((self.home / 'closure.json').read_text())
        self.assertEqual(self.schemas.validate('closure', closure), [])
        self.assertEqual((closure['kind'], closure['completed_starts']), ('outcome_closed', 2))
        self.assertFalse((self.home / 'closure.json').stat().st_mode & 0o222)
        self.assertEqual({g['id']: g['state'] for g in self.decision()['gaps']},
                         {'values': 'PROVED', 'more': 'PROVED', 'independent-review': 'PROVED'})
        self.assertEqual(self.state()['status'], 'ACCEPTED')
        try:
            self.author(None)
        except ValueError:
            pass
        self.assertEqual(len(self.starts()), 2, 'a closed transaction admits no start')
        self.assertEqual(self.fb.count(), 2)

    def test_post_review_verifier_failure_never_accepts(self):
        self.begin()
        self.author(FIX)
        seen = []

        def forge_second_verify(kind, argv, kwargs):
            if kind == 'verify':
                seen.append(argv)
                if len(seen) == 2:
                    return self.recorder.real((PY, '-c', "print('FAILED forged view')"), **kwargs)
            return None
        self.recorder.hook = forge_second_verify
        try:
            result = self.do('review')
        except ValueError:
            result = {'status': 'REFUSED'}
        self.assertNotEqual(result['status'], 'REVIEW_PASSED')
        self.assertFalse((self.home / 'accepted-review.json').exists())

    def test_pre_review_verifier_failure_never_launches_reviewer(self):
        self.begin()
        self.author(FIX)

        def forge_first_verify(kind, argv, kwargs):
            if kind == 'verify':
                return self.recorder.real((PY, '-c', "print('FAILED forged view')"), **kwargs)
            return None
        self.recorder.hook = forge_first_verify
        try:
            result = self.do('review')
        except ValueError:
            result = {'status': 'REFUSED'}
        self.assertNotEqual(result['status'], 'REVIEW_PASSED')
        self.assertNotIn('reviewer', self.recorder.kinds())
        self.assertFalse((self.home / 'accepted-review.json').exists())

    def test_reviewer_verdict_must_appear_in_captured_stdout(self):
        self.begin(script={'review-g': ('no_verdict',)})
        self.author(FIX)
        self.fb.review_final_text = 'Looks fine to me.\nREVIEW VERDICT: ACCEPT'   # never printed to stdout
        self.assertEqual(self.do('review')['status'], 'REVIEW_INCOMPLETE')
        self.assertFalse((self.home / 'accepted-review.json').exists())
        self.fb.review_final_text = None
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')
        self.assert_recovery_retains_author('review-g')

    def test_review_spec_without_sandbox_is_refused(self):
        self.begin()
        self.author(FIX)
        self.fb.review_sandbox = False
        with self.assertRaises(ValueError):
            self.do('review')
        self.assertNotIn('reviewer-unsandboxed', [c['kind'] for c in self.recorder.calls])
        self.assertNotIn('reviewer', self.recorder.kinds())
        self.assertFalse((self.home / 'accepted-review.json').exists())

    def test_unchanged_evidence_earns_no_rereview(self):
        self.begin(script={'review-g': ('reject',)})
        self.author(FIX)
        self.assertEqual(self.do('review')['status'], 'REVIEW_BLOCKING')
        self.refused('unchanged', 'review')
        self.assertEqual(self.fb.count(role='review'), 1)
        self.repair(FIX + '# repaired\n')
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')
        self.refused(('unchanged', 'novelty', 'accepted'), 'review')
        self.assertEqual(self.fb.count(role='review'), 2)

    def test_repair_only_after_reject_and_at_most_two_rounds(self):
        self.begin(script={'review-g': ('reject', 'reject', 'reject')})
        self.author(FIX)
        self.refused('reject', 'repair', targets=GAPS)
        self.assertEqual(self.do('review')['status'], 'REVIEW_BLOCKING')
        self.repair(FIX + '# repair 1\n')
        self.assertEqual(self.do('review')['status'], 'REVIEW_BLOCKING')
        self.repair(FIX + '# repair 2\n')
        self.assertEqual(self.do('review')['status'], 'REVIEW_BLOCKING')
        self.fb.edits.append(FIX + '# repair 3\n')
        self.refused('round', 'repair', targets=GAPS)
        self.assertEqual(len(self.starts()), 6)
        self.assertEqual([s['route'] for s in self.starts()],
                         ['author-a', 'review-g', 'author-a', 'review-g', 'author-a', 'review-g'])


class ReviewRecoveryTests(TransactionFixture):
    """Ports of review-recovery/* and crew/review-recovery-retains-author."""

    def interrupted_review(self, label):
        self.begin()
        self.author(FIX)
        self.interrupt('reviewer', label)
        with self.assertRaises(HostDied):
            self.do('review')
        self.restart()
        starts = self.starts()
        self.assertEqual(len(starts), 2)
        self.assertEqual(self.accounting(2), [], 'the interrupted review start is unaccounted')
        launches = list(self.fb.launches)
        self.assertEqual(self.do('review')['status'], 'RECONCILE_REQUIRED')
        self.assertEqual(self.do('author', targets=GAPS)['status'], 'RECONCILE_REQUIRED')
        self.assertEqual(self.fb.launches, launches, 'no redispatch before reconciliation')
        with self.assertRaises(ValueError) as caught:
            self.do('reconcile')
        self.assertIn('live', str(caught.exception).lower())
        self.assertEqual(self.accounting(2), [])
        self.stop_children()
        result = self.do('reconcile')
        self.assertEqual(result['status'], 'REVIEW_INCOMPLETE')
        event, = self.accounting(2)
        self.assertEqual((event['kind'], event['accounted'], event['attempt']), ('reconcile', True, None))
        self.assertEqual(self.schemas.validate('state-reconcile', event), [])
        self.assertFalse((self.home / 'accepted-review.json').exists())
        self.refused('reject', 'repair', targets=GAPS)
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')
        self.assert_recovery_retains_author('review-g')
        self.assertEqual(self.do('close')['status'], 'ACCEPTED')

    def test_review_recovery_interrupted_review(self):
        """review-recovery/interrupted-review"""
        self.interrupted_review(None)

    def test_review_recovery_misleading_raw_label(self):
        """review-recovery/misleading-raw-label: a raw REJECT without a completed attempt directs nothing."""
        self.interrupted_review('REVIEW VERDICT: REJECT\n')

    def test_review_recovery_timeout_receipt(self):
        """review-recovery/timeout-receipt"""
        self.begin(script={'review-g': ('platform_timeout',)})
        self.author(FIX)
        self.assertEqual(self.do('review')['status'], 'REVIEW_INCOMPLETE')
        start = self.starts()[1]
        finish, = self.accounting(2)
        self.assertEqual(finish['kind'], 'finish')
        self.assertEqual(finish['attempt']['failure_class'], 'platform_timeout')
        self.assertEqual(finish['attempt']['routing_attestation']['attempt_id'], finish['attempt']['attempt_id'])
        self.attest.verify_receipt(self.home, start, finish)
        self.assertFalse((self.home / 'accepted-review.json').exists())
        self.refused('reject', 'repair', targets=GAPS)
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')
        self.assert_recovery_retains_author('review-g')

    def test_review_recovery_completed_reject(self):
        """review-recovery/completed-reject: a completed REJECT is a valid review, repair follows."""
        self.begin(script={'review-g': ('reject',)})
        self.author(FIX)
        self.assertEqual(self.do('review')['status'], 'REVIEW_BLOCKING')
        start = self.starts()[1]
        finish, = self.accounting(2)
        self.assertEqual(finish['kind'], 'finish')
        self.attest.verify_receipt(self.home, start, finish)
        self.assertFalse((self.home / 'accepted-review.json').exists())
        self.repair(FIX + '# repaired\n')
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')
        self.assertTrue(all('review-g' not in r.failed_reviewers for r in self.fb.requests),
                        'a completed rejection is not a reviewer failure')
        self.assertEqual([s['route'] for s in self.starts()], ['author-a', 'review-g', 'author-a', 'review-g'])
        self.assertEqual(self.do('close')['status'], 'ACCEPTED')

    def test_review_recovery_completed_reject_reconcile(self):
        """review-recovery/completed-reject-reconcile: host dies after the completed finish, before settling."""
        self.begin(script={'review-g': ('reject',)})
        self.author(FIX)
        real = self.journal.finish_start

        def die_after_review_finish(path, unit_id, start, *args, **kwargs):
            result = real(path, unit_id, start, *args, **kwargs)
            if start == 2:
                raise HostDied('CONTROL interruption after completed review receipt')
            return result
        with mock.patch.object(self.journal, 'finish_start', die_after_review_finish):
            with self.assertRaises(HostDied):
                self.do('review')
        self.restart()
        finish, = self.accounting(2)
        self.assertEqual(finish['kind'], 'finish')
        launches = list(self.fb.launches)
        self.assertEqual(self.do('review')['status'], 'RECONCILE_REQUIRED')
        self.assertEqual(self.fb.launches, launches)
        self.assertEqual(self.do('reconcile')['status'], 'REVIEW_BLOCKING')
        self.assertEqual([e['kind'] for e in self.accounting(2)], ['finish'], 'settled from the retained finish')
        self.assertEqual(self.fb.count(role='review'), 1)
        self.repair(FIX + '# repaired\n')
        self.assertEqual([s['route'] for s in self.starts()], ['author-a', 'review-g', 'author-a'])

    def test_review_recovery_retains_author_and_never_dispatches_author(self):
        """crew/review-recovery-retains-author"""
        self.begin(script={'review-g': ('crash',)})
        self.author(FIX)
        self.assertEqual(self.do('review')['status'], 'REVIEW_INCOMPLETE')
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')
        self.assert_recovery_retains_author('review-g')
        self.assertEqual(self.do('close')['status'], 'ACCEPTED')


class CloseTests(TransactionFixture):
    def close_refused(self, *fragments):
        """Close must not succeed; its refusal (ValueError or non-ACCEPTED status) names one of the fragments."""
        try:
            result = self.do('close')
            self.assertNotEqual(result.get('status'), 'ACCEPTED')
            message = json.dumps(result, default=str)
        except ValueError as error:
            message = str(error)
        self.assertTrue(any(f.lower() in message.lower() for f in fragments), (fragments, message))
        self.assertFalse((self.home / 'closure.json').exists())
        self.assertNotIn('outcome_closed', [e['kind'] for e in self.events()])

    def test_close_refused_without_accepted_review_or_with_stale_review(self):
        self.begin()
        self.close_refused('accepted review', 'no start')
        self.author(FIX)
        self.close_refused('accepted review')
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')
        (self.project / 'pkg' / 'core.py').write_text(FIX + '# drift after review\n')
        self.close_refused('source snapshot')
        (self.project / 'pkg' / 'core.py').write_text(FIX)
        self.assertEqual(self.do('close')['status'], 'ACCEPTED')

    def test_close_refused_when_tests_or_gaps_are_not_acceptable(self):
        self.begin()
        self.author(PARTIAL)
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')     # the stub reviewer accepts anything
        self.close_refused('tests', 'more')
        self.assertTrue(all(g['state'] == 'OPEN' for g in self.decision()['gaps'] if g['id'] == 'more'))

    def test_close_refused_with_unaccounted_start(self):
        self.begin()
        self.interrupt('author')
        with self.assertRaises(HostDied):
            self.author(FIX)
        self.restart()
        self.close_refused('unaccounted', 'RECONCILE_REQUIRED')


class CheckpointTests(TransactionFixture):
    def test_start_limit_exhaustion_refuses_without_resetting_counters(self):
        self.begin(script={'review-g': ('reject',)}, checkpoint={'max_starts': 2})
        self.author(FIX)
        self.assertEqual(self.do('review')['status'], 'REVIEW_BLOCKING')
        elapsed = (self.home / 'elapsed-checkpoint.json').read_bytes()
        unit = self.decision()['unit_id']
        summary = self.journal.summary(self.home / 'STATE.jsonl', unit)
        for _ in range(2):
            self.fb.edits[:] = [FIX + '# repaired\n']
            self.assertEqual(self.repair(None)['status'], 'CHECKPOINT_EXHAUSTED')
            self.assertEqual(self.state()['status'], 'CHECKPOINT_EXHAUSTED')
            self.assertEqual(self.journal.summary(self.home / 'STATE.jsonl', unit), summary)
            self.assertEqual(self.fb.count(), 2)
        self.assertEqual((self.home / 'elapsed-checkpoint.json').read_bytes(), elapsed)
        self.assertEqual({g['id']: g['state'] for g in self.decision()['gaps']},
                         {'values': 'OPEN', 'more': 'OPEN', 'independent-review': 'OPEN'})

    def test_expired_elapsed_window_refuses_without_renewal(self):
        self.begin()
        pins = {name: self.budget.sha(self.home / name) for name in self.budget.PINS}
        with mock.patch.object(self.budget, 'remaining', lambda *args, **kwargs: -1.0):
            self.assertEqual(self.author(None)['status'], 'CHECKPOINT_EXHAUSTED')
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.fb.launches, [])
        self.assertEqual(pins, {name: self.budget.sha(self.home / name) for name in self.budget.PINS})


class StdlibTests(unittest.TestCase):
    def test_transaction_module_is_stdlib_only(self):
        module = load(self, 'supervise.controller.transaction')
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
