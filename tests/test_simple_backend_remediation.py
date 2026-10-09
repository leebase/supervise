# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S4 sprint-review remediation round 1 (simple backend).

Findings (independent S4 review, DeepSeek; confirmed by the Chief):
- `SimpleBackend` raised NotImplementedError for check_grants / preflight_project / prepare_launch / interpret, so it was
  not a working RoutingBackend; only the module-level drivers were.
- Configured `effort` was dropped (config built a base Route; drivers always used a default).
- Medium: the Codex driver accepted literal command grants it cannot enforce (Codex CLI has no command allowlist). The
  driver must say so explicitly; the controller's project-confinement guard (separate unit) closes the consequence.
"""
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import textwrap
import unittest

CONFIG = textwrap.dedent('''
    [[route]]
    id = "codex-gpt"
    harness = "codex"
    vendor = "openai"
    model = "gpt-test"
    family = "openai-gpt"
    roles = ["impl", "review"]
    effort = "high"

    [[route]]
    id = "claude-sonnet"
    harness = "claude"
    vendor = "anthropic"
    model = "sonnet-test"
    family = "anthropic-claude"
    roles = ["review", "impl"]

    [crew]
    authors = ["codex-gpt", "claude-sonnet"]
    reviewers = ["claude-sonnet", "codex-gpt"]
    require_review_fallback = false
''')
CLAUDE_STREAM = [
    {'type': 'system', 'subtype': 'init', 'model': 'claude-test-5'},
    {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'ok\nREVIEW VERDICT: ACCEPT',
     'total_cost_usd': 0.12, 'usage': {'input_tokens': 5, 'output_tokens': 7}},
]


class SimpleBackendDelegationTests(unittest.TestCase):
    def setUp(self):
        try:
            self.s = importlib.import_module('supervise.backends.simple')
            self.b = importlib.import_module('supervise.backend')
            self.codex = importlib.import_module('supervise.backends.simple.codex')
        except ImportError as error:
            self.fail('simple backend missing: ' + str(error))
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / 'config.toml').write_text(CONFIG)
        for name in ('codex-home', 'claude-config', 'work'):
            (self.root / name).mkdir()
        try:
            self.backend = self.s.SimpleBackend(
                self.root / 'config.toml',
                executables={'codex': '/opt/tools/codex', 'claude': '/opt/tools/claude'},
                codex_home=self.root / 'codex-home', claude_config_dir=self.root / 'claude-config')
        except TypeError as error:
            self.fail('SimpleBackend does not accept executables/codex_home/claude_config_dir: ' + str(error))
        self.routes = {r.route_id: r for r in self.backend.identity().routes}

    def tearDown(self):
        self.dir.cleanup()

    def request(self, route_id, role):
        return self.b.LaunchRequest(route=self.routes[route_id], role=role, packet_path=self.root / 'packet.md',
                                    workdir=self.root / 'work' if role == 'impl' else Path('/review'),
                                    owned_paths=(Path('pkg/core.py'),), timeout_seconds=300.0,
                                    worker_commands=((('/usr/bin/' + 'python3'), '-m', 'unittest'),) if role == 'impl' else (),
                                    review_view=self.root if role == 'review' else None)

    def test_prepare_launch_delegates_by_harness_and_carries_effort(self):
        try:
            codex_spec = self.backend.prepare_launch(self.request('codex-gpt', 'impl'))
            claude_spec = self.backend.prepare_launch(self.request('claude-sonnet', 'review'))
        except NotImplementedError as error:
            self.fail('prepare_launch not delegated: ' + str(error))
        self.assertEqual(list(codex_spec.argv[:2]), ['/opt/tools/codex', 'exec'])
        self.assertIn('model_reasoning_effort="high"', codex_spec.argv)
        self.assertEqual(claude_spec.argv[0], '/opt/tools/claude')
        self.assertTrue(claude_spec.requires_review_sandbox)

    def test_interpret_check_grants_and_preflight_delegate(self):
        spec = self.backend.prepare_launch(self.request('claude-sonnet', 'review'))
        out, err = self.root / 'o.jsonl', self.root / 'e.txt'
        out.write_text(''.join(json.dumps(x) + '\n' for x in CLAUDE_STREAM)); err.write_text('')
        capture = self.b.ProcessCapture(exit_code=0, timed_out=False, seconds=1.0, stdout_path=out, stderr_path=err,
                                        stdout_sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
                                        stderr_sha256=hashlib.sha256(b'').hexdigest())
        try:
            outcome = self.backend.interpret(spec, capture)
        except NotImplementedError as error:
            self.fail('interpret not delegated: ' + str(error))
        self.assertEqual((outcome.served_model, outcome.cost_basis), ('claude-test-5', 'harness_reported'))
        with self.assertRaises(self.b.UnsupportedGrant):
            self.backend.check_grants(self.routes['claude-sonnet'], [['python3', '-m', 'unittest']])
        (self.root / 'work' / '.mcp.json').write_text('{}')
        with self.assertRaises(self.b.ProjectConfigRefused):
            self.backend.preflight_project(self.routes['claude-sonnet'], self.root / 'work')
        self.backend.preflight_project(self.routes['codex-gpt'], self.root / 'work')       # codex ignores .mcp.json
        (self.root / 'work' / 'AGENTS.override.md').write_text('x')
        with self.assertRaises(self.b.ProjectConfigRefused):
            self.backend.preflight_project(self.routes['codex-gpt'], self.root / 'work')

    def test_codex_driver_states_its_grants_are_sandbox_enforced(self):
        doc = ' '.join(((self.codex.__doc__ or '') + ' ' + (self.codex.check_grants.__doc__ or '')).split()).lower()
        self.assertIn('sandbox', doc)
        self.assertIn('allowlist', doc)


if __name__ == '__main__':
    unittest.main()
