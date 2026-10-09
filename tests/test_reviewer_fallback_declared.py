# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S6-R2: reviewer fallback is the backend's declared policy (decision D-4).

D-4 made the author-time fallback reviewer optional, default off in the simple backend's configuration, without
changing assurance. The v2.8.2 conformance scenario "crew/no-qualified-review-crew-refuses" must keep passing, so:
- a backend that declares `require_review_fallback` (bool attribute) sets the policy the controller requests;
- a backend that declares nothing (the stub conformance backend) keeps production parity: fallback required;
- the simple backend and the CLI's configured backend declare their configured value (default False);
- with or without fallback, a crew with no independent, isolated reviewer is refused before the author runs.
(Supersedes the S6-R1b oracle, whose stub-default premise contradicted the frozen conformance scenario.)
"""
import shutil
import sys
import tempfile
from pathlib import Path
import unittest

HAVE_SANDBOX = shutil.which('bwrap') is not None and sys.executable.startswith(('/usr/', '/bin/'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_controller_transaction as tx  # noqa: E402  (frozen fixture, reused read-only)

CONFIG = '''
[[route]]
id = "codex-gpt"
harness = "codex"
vendor = "openai"
model = "m1"
family = "openai-gpt"
roles = ["impl", "review"]

[[route]]
id = "claude-sonnet"
harness = "claude"
vendor = "anthropic"
model = "m2"
family = "anthropic-claude"
roles = ["review", "impl"]

[crew]
authors = ["codex-gpt", "claude-sonnet"]
reviewers = ["claude-sonnet", "codex-gpt"]
require_review_fallback = {value}
'''


@unittest.skipUnless(HAVE_SANDBOX, 'bubblewrap unavailable')
class DeclaredFallbackPolicyTests(tx.TransactionFixture):
    def authors_run(self):
        return len([c for c in self.recorder.calls if c['kind'] == 'author'])

    def test_undeclared_backend_keeps_production_parity(self):
        self.begin(route_ids=['author-a', 'review-g'])
        with self.assertRaises(ValueError):
            self.do('author', targets=list(tx.GAPS))
        self.assertTrue(self.fb.requests[0].require_review_fallback)
        self.assertEqual(self.authors_run(), 0)

    def test_declared_optional_fallback_allows_one_independent_reviewer(self):
        self.begin(route_ids=['author-a', 'review-g'])
        self.fb.require_review_fallback = False
        self.author(tx.FIX)
        self.assertFalse(self.fb.requests[0].require_review_fallback)
        self.assertEqual(self.authors_run(), 1)

    def test_declared_required_fallback_is_requested(self):
        self.begin()
        self.fb.require_review_fallback = True
        self.author(tx.FIX)
        self.assertTrue(self.fb.requests[0].require_review_fallback)

    def test_no_independent_reviewer_refuses_before_the_author(self):
        self.begin(route_ids=['author-a', 'review-a'])
        self.fb.require_review_fallback = False
        with self.assertRaises(ValueError):
            self.do('author', targets=list(tx.GAPS))
        self.assertEqual(self.authors_run(), 0)


class SimpleBackendDeclaresPolicyTests(unittest.TestCase):
    def config(self, value):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'backend.toml'
        path.write_text(CONFIG.format(value=value))
        return path

    def test_simple_backend_declares_its_configured_policy(self):
        from supervise.backends.simple import SimpleBackend
        self.assertIs(SimpleBackend(self.config('false')).require_review_fallback, False)
        self.assertIs(SimpleBackend(self.config('true')).require_review_fallback, True)

    def test_cli_backend_declares_the_same_policy(self):
        from supervise import cli
        from supervise.backends.simple import SimpleBackend
        for value, expected in (('false', False), ('true', True)):
            configured = cli._ConfiguredBackend(SimpleBackend(self.config(value)))
            self.assertIs(configured.require_review_fallback, expected)


if __name__ == '__main__':
    unittest.main()
