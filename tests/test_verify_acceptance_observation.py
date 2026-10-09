# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S8-R1: two offline-verification defects found by the S8 review.

M3: no offline check read the controller-run test observation, so `supervise verify` PASS did not establish that the
frozen acceptance tests passed, even by the controller's own record. Add a tenth check, `acceptance-observation`,
reported in every result: using `accepted-review.json`'s `author_start`, the finish record of that author start must
carry an `observation` with `complete` true, empty `missing_frozen_ids`, `unexpected_test_ids` and
`changed_frozen_test_inputs`, and, for every selector of every record in `acceptance.json`, at least one observed test id
equal to the selector or starting with the selector followed by "." and every such matching id's outcome `PASS`.
M1: the controller archives an orphaned pre-start route record as `route-refused-prestart-<32 hex>.json`; the
`route-*.json` enumeration treated it as a malformed route record, so a valid closure after crash recovery could never
verify. Such archived records are excluded from route enumeration; any other unexpected `route-*.json` name still fails.
"""
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_verify as tv  # noqa: E402  (frozen fixture, reused read-only)


class AcceptanceObservationTests(tv.ClosedInvocation):
    def author_finish(self, events):
        start = json.loads((self.home / 'accepted-review.json').read_text())['author_start']
        return next(e for e in events if e.get('kind') == 'finish' and e.get('start') == start)

    def selector_ids(self):
        acceptance = json.loads((self.home / 'acceptance.json').read_text())
        return [s for item in acceptance for s in item['selectors']]

    def mutate_observation(self, change):
        events = self.journal_lines()
        change(self.author_finish(events)['attempt']['observation'])
        self.write_journal(events)

    def test_clean_closure_reports_acceptance_observation_ok(self):
        result = self.verify()
        checks = {c['name']: c for c in result['checks']}
        self.assertIn('acceptance-observation', checks)
        self.assertTrue(checks['acceptance-observation']['ok'], checks['acceptance-observation'])
        self.assertEqual(result['verdict'], 'PASS')

    def test_failed_acceptance_test_fails(self):
        selector = self.selector_ids()[0]

        def change(observation):
            for test_id, record in observation['outcomes'].items():
                if test_id == selector or test_id.startswith(selector + '.'):
                    record['outcome'] = 'FAIL'
        self.mutate_observation(change)
        self.assert_failed('acceptance-observation')

    def test_incomplete_observation_fails(self):
        self.mutate_observation(lambda observation: observation.__setitem__('complete', False))
        self.assert_failed('acceptance-observation')

    def test_missing_acceptance_ids_fail(self):
        selector = self.selector_ids()[0]

        def change(observation):
            for test_id in [t for t in observation['outcomes'] if t == selector or t.startswith(selector + '.')]:
                del observation['outcomes'][test_id]
        self.mutate_observation(change)
        self.assert_failed('acceptance-observation')

    def test_archived_prestart_route_record_is_ignored(self):
        os.chmod(self.home, 0o700)   # only the directory: records and seals stay read-only
        source = self.home / 'route-1.json'
        (self.home / ('route-refused-prestart-' + 'a' * 32 + '.json')).write_bytes(source.read_bytes())
        result = self.verify()
        self.assertEqual(result['verdict'], 'PASS', result)

    def test_other_unexpected_route_names_still_fail(self):
        os.chmod(self.home, 0o700)   # only the directory: records and seals stay read-only
        (self.home / 'route-x.json').write_text('{}')
        self.assert_failed('crew-files')


if __name__ == '__main__':
    unittest.main()
