# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S8-F: the owner-authorized final S8 remediation round.

M1: a refused `review` wrote `review-packet-N.txt` (exclusive create) before its pre-reservation refusals, so the retry
raised FileExistsError. Every refusal check now runs before the packet is written; and an orphaned
`review-packet-N.txt` left by an earlier refusal (N greater than the number of journal starts and no `route-N.json`) is
removed before writing, without touching packets or evidence of existing starts.
L3: `review` reads the live plan file; it now refuses (ValueError naming the plan, no start reserved) when the plan's
SHA-256 differs from `pending.json`'s `plan_sha256`. Offline `review-snapshot` also fails when `accepted-review.json`'s
`source_snapshot` differs from the `source_snapshot` of the reviewed author start's finish record.
L6: `supervise/portability.py` builds its private tokens without containing them as literals (the token values are
unchanged).
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import unittest

HAVE_SANDBOX = shutil.which('bwrap') is not None and sys.executable.startswith(('/usr/', '/bin/'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_controller_transaction as tx  # noqa: E402  (frozen fixture, reused read-only)
import test_review_mount_disjointness as md  # noqa: E402  (frozen fixture, reused read-only)
import test_verify as tv  # noqa: E402  (frozen fixture, reused read-only)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class ReviewRetryTests(md.ReviewMountDisjointnessTests):
    # Inherit the fixture only; disable the parent's tests in this class.
    test_mount_of_the_project_is_refused = None
    test_mount_inside_the_project_is_refused = None
    test_mount_containing_the_project_is_refused = None
    test_mount_of_the_invocation_or_state_root_is_refused = None
    test_disjoint_mount_is_accepted = None

    def test_refused_review_can_be_retried(self):
        self.begin()
        self.author(tx.FIX)
        original = self.fb.stub.prepare_launch
        self.declare(self.project)
        with self.assertRaises(ValueError):
            self.do('review')
        self.fb.stub.prepare_launch = original
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')

    def test_orphaned_packet_from_an_earlier_refusal_is_cleaned(self):
        self.begin()
        self.author(tx.FIX)
        number = len(self.starts()) + 1
        orphan = self.home / ('review-packet-%d.txt' % number)
        orphan.write_text('left behind by an earlier refused review')
        os.chmod(orphan, 0o400)
        kept = {p: digest(p) for p in self.home.glob('packet-*.txt')}
        self.assertTrue(kept)
        self.assertEqual(self.do('review')['status'], 'REVIEW_PASSED')
        self.assertEqual({p: digest(p) for p in kept}, kept)
        self.assertNotIn('left behind', orphan.read_text())

    def test_changed_plan_refuses_review_before_any_start(self):
        self.begin()
        self.author(tx.FIX)
        plan = Path(self.ctl.pending['plan_path'])
        os.chmod(plan, 0o600)
        plan.write_text(plan.read_text() + '\nA sentence added after admission.\n')
        before = len(self.starts())
        with self.assertRaises(ValueError) as caught:
            self.do('review')
        self.assertIn('plan', str(caught.exception).lower())
        self.assertEqual(len(self.starts()), before)


class AcceptedSnapshotBindingTests(tv.ClosedInvocation):
    def test_accepted_snapshot_must_be_the_reviewed_authors(self):
        events = self.journal_lines()
        start = json.loads((self.home / 'accepted-review.json').read_text())['author_start']
        finish = next(e for e in events if e.get('kind') == 'finish' and e.get('start') == start)
        artifacts = finish['attempt']['source_snapshot']['artifacts']
        name = sorted(artifacts)[0]
        artifacts[name] = '0' * 64
        self.write_journal(events)
        self.assert_failed('review-snapshot')


class PortabilityLiteralTests(unittest.TestCase):
    def test_scanner_source_has_no_private_literals(self):
        import supervise.portability as m
        text = Path(m.__file__).read_text()
        for token in m.FORBIDDEN[1:5]:
            self.assertNotIn(token, text)


if __name__ == '__main__':
    unittest.main()
