# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S5-V1: offline verification re-checks the sealed evidence (AT-4).

Found by tamper tests on the real ACCEPTED invocation in the S6 VM: flipping one byte in a sealed evidence chunk left
`supervise verify` at PASS (route-record and journal tampering already fail). Add a ninth check, `sealed-evidence`,
reported in every result: for every finish record carrying an `evidence_handle`, verify the store at
`<home>/evidence/<identity>` with `kernel.seal.verify`, using a binding derived independently from the journal
(mission_id = pending invocation_id; transaction_id = the unit id; worker_id/attempt_id = the reviewed author start's
route and attempt id; worker_role "impl"; source_id = attest.digest of that author's finish source_snapshot) and the
handle identity, which must equal the attempt's view_identity. A stored handle path other than that location, a
missing store, or a closure with no sealed evidence fails the check.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_verify as tv  # noqa: E402  (frozen fixture, reused read-only)


class SealedEvidenceTests(tv.ClosedInvocation):
    def chunks(self):
        return sorted((self.home / 'evidence').rglob('chunks/*'))

    def test_clean_closure_reports_sealed_evidence_ok(self):
        result = self.verify()
        checks = {c['name']: c for c in result['checks']}
        self.assertIn('sealed-evidence', checks)
        self.assertTrue(checks['sealed-evidence']['ok'], checks['sealed-evidence'])
        self.assertEqual(result['verdict'], 'PASS')

    def test_flipped_byte_in_a_sealed_chunk_fails(self):
        tv.make_writable(self.home / 'evidence')
        chunk = self.chunks()[0]
        data = chunk.read_bytes()
        chunk.write_bytes(bytes([data[0] ^ 0x01]) + data[1:])
        self.assert_failed('sealed-evidence')

    def test_missing_seal_manifest_fails(self):
        tv.make_writable(self.home / 'evidence')
        seals = sorted((self.home / 'evidence').rglob('seal.json'))
        self.assertTrue(seals)
        seals[0].unlink()
        self.assert_failed('sealed-evidence')

    def test_handle_pointing_elsewhere_fails(self):
        events = self.journal_lines()
        changed = False
        for event in events:
            handle = (event.get('attempt') or {}).get('evidence_handle')
            if event.get('kind') == 'finish' and isinstance(handle, dict):
                handle['path'] = str(Path(handle['path']).parent.parent / 'elsewhere' / handle['identity'])
                changed = True
        self.assertTrue(changed)
        self.write_journal(events)
        result = self.verify()
        failing = [c['name'] for c in result['checks'] if not c['ok']]
        self.assertIn('sealed-evidence', failing, result)


if __name__ == '__main__':
    unittest.main()
