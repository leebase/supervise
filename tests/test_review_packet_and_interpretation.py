# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S3-L2: what the reviewer is asked, and how its output is interpreted.

Found by the S6 pre-flight with a real Claude reviewer in a fresh VM:
- The review packet was two generic lines with no plan, no acceptance criteria and a weak format instruction; the
  reviewer reviewed generically and did not end with the typed verdict, so the review was (correctly) incomplete.
  The packet must state the plan text, every acceptance id with its proposition and selectors, the owned paths, that the
  sealed evidence is under /review, and require the reply to end with exactly one final line
  `REVIEW VERDICT: ACCEPT` or `REVIEW VERDICT: REJECT`.
- The controller interpreted the review output against its sandbox-wrapped copy of the LaunchSpec, which the backend
  could not recognise ("launch specification is not bound to a configured route"), so a successful review was recorded
  as platform_env with unknown cost. The backend must interpret with the LaunchSpec object it returned from
  prepare_launch (the wrapped copy is only for launching).
"""
import shutil
import sys
from pathlib import Path
import unittest

HAVE_SANDBOX = shutil.which('bwrap') is not None and sys.executable.startswith(('/usr/', '/bin/'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_controller_transaction as tx  # noqa: E402  (frozen fixture, reused read-only)


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class ReviewPacketAndInterpretationTests(tx.TransactionFixture):
    def reviewed(self):
        self.begin()
        self.author(tx.FIX)
        returned, interpreted = [], []
        prepare, interpret = self.fb.prepare_launch, self.fb.interpret

        def prepare_launch(request):
            spec = prepare(request)
            returned.append((request.role, spec))
            return spec

        def interpret_spec(spec, capture):
            interpreted.append(spec)
            return interpret(spec, capture)
        self.fb.prepare_launch, self.fb.interpret = prepare_launch, interpret_spec
        result = self.do('review')
        packets = sorted(self.home.glob('review-packet-*.txt'))
        return result, returned, interpreted, packets[-1].read_text()

    def test_review_packet_states_the_plan_acceptance_and_verdict_format(self):
        _, _, _, packet = self.reviewed()
        plan = Path(self.ctl.pending['plan_path']).read_text()
        self.assertIn(plan.strip(), packet)
        for item in self.ctl.admitted['acceptance']:
            self.assertIn(item['id'], packet)
            self.assertIn(item['proposition'], packet)
            for selector in item['selectors']:
                self.assertIn(selector, packet)
        for path in self.ctl.admitted['owned_paths']:
            self.assertIn(path, packet)
        self.assertIn('/review', packet)
        self.assertIn('REVIEW VERDICT: ACCEPT', packet)
        self.assertIn('REVIEW VERDICT: REJECT', packet)
        self.assertIn('final line', packet.lower())

    def test_review_output_is_interpreted_with_the_backend_spec(self):
        result, returned, interpreted, _ = self.reviewed()
        review_specs = [spec for role, spec in returned if role == 'review']
        self.assertTrue(review_specs)
        self.assertTrue(interpreted)
        self.assertTrue(any(interpreted[-1] is spec for spec in review_specs),
                        'review output interpreted against a spec the backend did not return')
        self.assertEqual(result['status'], 'REVIEW_PASSED', result)


if __name__ == '__main__':
    unittest.main()
