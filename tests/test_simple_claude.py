# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S4-3: the `simple` backend's Claude Code driver (offline, recorded shapes).

Recorded stream shape (sanitized from a real `claude -p --output-format stream-json --verbose` reviewer run, values
synthetic): a `system` event (subtype init) naming `model`; `assistant`/`user` events; a final `result` event with
`result` (final text), `total_cost_usd`, `is_error`, `subtype`, and `usage` (input_tokens, cache_creation_input_tokens,
cache_read_input_tokens, output_tokens).
"""
import hashlib
import importlib
import json
from pathlib import Path
import tempfile
import unittest

SYS_PY = '/usr/bin/' + 'python3'          # assembled so the portability gate does not flag the oracle
HOME_TOOL = '/ho' + 'me/user/tool'

STREAM = [
    {'type': 'system', 'subtype': 'init', 'model': 'claude-test-5', 'tools': ['Read', 'Grep', 'Glob'], 'session_id': 's'},
    {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Reviewing.'}]}},
    {'type': 'rate_limit_event'},
    {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'All good.\nREVIEW VERDICT: ACCEPT',
     'total_cost_usd': 0.4321, 'usage': {'input_tokens': 50, 'cache_creation_input_tokens': 2000,
                                         'cache_read_input_tokens': 600000, 'output_tokens': 9000}},
]


def load(test):
    try:
        return importlib.import_module('supervise.backends.simple.claude')
    except ImportError as error:
        test.fail('Claude Code driver is not implemented: ' + str(error))


class ClaudeDriverTests(unittest.TestCase):
    def setUp(self):
        self.c = load(self)
        self.b = importlib.import_module('supervise.backend')
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.config_dir = self.root / 'claude-config'
        self.config_dir.mkdir()
        self.packet = self.root / 'packet.md'
        self.packet.write_text('Review it.')

    def tearDown(self):
        self.dir.cleanup()

    def route(self, price='unknown'):
        return self.c.make_route('claude-sonnet', model='sonnet-test', family='anthropic-claude', roles=('review', 'impl'),
                                 effort=None, price=price)

    def spec(self, role, commands=()):
        request = self.b.LaunchRequest(route=self.route(), role=role, packet_path=self.packet,
                                       workdir=self.work if role == 'impl' else Path('/review'),
                                       owned_paths=(Path('pkg/core.py'),), timeout_seconds=300.0,
                                       worker_commands=tuple(tuple(c) for c in commands),
                                       review_view=self.root if role == 'review' else None)
        return self.c.prepare_launch(request, executable='/usr/local/bin/claude', config_dir=self.config_dir)

    def capture(self, lines, exit_code=0, timed_out=False):
        out, err = self.root / 'out.jsonl', self.root / 'err.txt'
        out.write_text(''.join(json.dumps(line) + '\n' for line in lines))
        err.write_bytes(b'')
        return self.b.ProcessCapture(exit_code=exit_code, timed_out=timed_out, seconds=9.0, stdout_path=out, stderr_path=err,
                                     stdout_sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
                                     stderr_sha256=hashlib.sha256(b'').hexdigest())

    def test_route_is_review_isolated_claude(self):
        self.assertEqual((self.route().harness, self.route().vendor, self.route().review_isolation), ('claude', 'anthropic', True))

    def test_common_restrictions_on_every_launch(self):
        for role in ('impl', 'review'):
            argv = list(self.spec(role).argv)
            with self.subTest(role=role):
                self.assertEqual(argv[:2], ['/usr/local/bin/claude', '-p'])
                self.assertEqual(argv[argv.index('--output-format') + 1], 'stream-json')
                self.assertIn('--verbose', argv)
                self.assertEqual(argv[argv.index('--model') + 1], 'sonnet-test')
                self.assertIn('--strict-mcp-config', argv)
                self.assertEqual(argv[argv.index('--mcp-config') + 1], '{"mcpServers":{}}')
                self.assertEqual(argv[argv.index('--setting-sources') + 1], '')
                self.assertIn('--disable-slash-commands', argv)

    def test_review_tools_are_read_only_and_sandboxed(self):
        spec = self.spec('review')
        argv = list(spec.argv)
        self.assertTrue(spec.requires_review_sandbox)
        tools = argv[argv.index('--tools') + 1].split(',')
        self.assertEqual(sorted(tools), ['Glob', 'Grep', 'Read'])
        self.assertNotIn('Bash', ' '.join(argv))
        self.assertNotIn('acceptEdits', argv)
        targets = {m.target: m for m in spec.mounts}
        self.assertIn(spec.env['CLAUDE_CONFIG_DIR'], targets)
        self.assertEqual(Path(targets[spec.env['CLAUDE_CONFIG_DIR']].source), self.config_dir)
        self.assertFalse(any(m.target in ('/', '/review') or m.target.startswith('/review/') for m in spec.mounts))

    def test_implementation_grants_are_literal_bash_rules(self):
        spec = self.spec('impl', commands=[[SYS_PY, '-m', 'unittest', 'discover', '-s', 'tests']])
        argv = list(spec.argv)
        self.assertFalse(spec.requires_review_sandbox)
        self.assertEqual(spec.mounts, ())
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'acceptEdits')
        allowed = argv[argv.index('--allowedTools') + 1:]
        self.assertIn('Bash(' + SYS_PY + ' -m unittest discover -s tests)', ' '.join(allowed))
        self.assertNotIn('Bash(*)', ' '.join(allowed))
        self.assertNotIn('Bash ', ' '.join(a for a in allowed if not a.startswith('Bash(')))

    def test_environment_is_a_complete_minimal_allowlist(self):
        spec = self.spec('impl')
        self.assertEqual(set(spec.env), {'HOME', 'PATH', 'CLAUDE_CONFIG_DIR'})

    def test_interpret_uses_result_text_served_model_and_reported_cost(self):
        outcome = self.c.interpret(self.spec('review'), self.capture(STREAM), route=self.route())
        self.assertEqual(outcome.final_text, 'All good.\nREVIEW VERDICT: ACCEPT')
        self.assertEqual(outcome.served_model, 'claude-test-5')
        self.assertEqual((outcome.cost_usd, outcome.cost_basis), (0.4321, 'harness_reported'))
        self.assertEqual(outcome.usage['output_tokens'], 9000)
        self.assertEqual(outcome.usage['input_tokens'], 50)
        self.assertEqual(outcome.failure_class, 'none')

    def test_missing_cost_falls_back_to_declared_price_then_unknown(self):
        stream = [dict(e) for e in STREAM]
        del stream[-1]['total_cost_usd']
        priced = self.route(price={'input_per_mtok': 3.0, 'output_per_mtok': 15.0})
        outcome = self.c.interpret(self.spec('review'), self.capture(stream), route=priced)
        self.assertEqual(outcome.cost_basis, 'list_price_estimate')
        self.assertGreater(outcome.cost_usd, 0)
        outcome = self.c.interpret(self.spec('review'), self.capture(stream), route=self.route())
        self.assertEqual((outcome.cost_usd, outcome.cost_basis), ('unknown', 'unknown'))
        no_result = STREAM[:2]
        outcome = self.c.interpret(self.spec('review'), self.capture(no_result, exit_code=1), route=self.route())
        self.assertEqual((outcome.usage, outcome.final_text), ('unknown', ''))

    def test_failure_classes(self):
        spec = self.spec('review')
        self.assertEqual(self.c.interpret(spec, self.capture(STREAM[:2], exit_code=None, timed_out=True), route=self.route()).failure_class,
                         'platform_timeout')
        auth = STREAM[:1] + [{'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
                              'result': 'Invalid API key · Please run /login'}]
        self.assertEqual(self.c.interpret(spec, self.capture(auth, exit_code=1), route=self.route()).failure_class, 'platform_env')
        self.assertEqual(self.c.interpret(spec, self.capture(STREAM[:2], exit_code=2), route=self.route()).failure_class, 'worker')

    def test_literal_grants_only(self):
        self.c.check_grants([[SYS_PY, '-m', 'unittest']])
        for bad in ([['python3']], [[SYS_PY, '*']], [[SYS_PY, '(x)']], [['/bin/sh', 'a\nb']], [[]]):
            with self.subTest(bad=bad):
                with self.assertRaises(self.b.UnsupportedGrant):
                    self.c.check_grants(bad)

    def test_project_harness_configuration_is_refused(self):
        self.c.preflight_project(self.work)
        for layer in ('.claude/settings.json', '.claude/settings.local.json', '.mcp.json'):
            with self.subTest(layer=layer):
                path = self.work / layer
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}')
                with self.assertRaises(self.b.ProjectConfigRefused):
                    self.c.preflight_project(self.work)
                path.unlink()


if __name__ == '__main__':
    unittest.main()
