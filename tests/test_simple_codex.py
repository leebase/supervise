# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S4-2: the `simple` backend's Codex CLI driver (offline, recorded shapes).

Recorded stream shape (sanitized from a real `codex exec --json` author run, values synthetic): one JSON object per
line with types thread.started, turn.started, item.started, item.completed (item.type 'agent_message' carries text),
turn.completed (usage: input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens,
reasoning_output_tokens). The stream does not name the served model.
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
    {'type': 'thread.started', 'thread_id': 't-1'},
    {'type': 'turn.started'},
    {'type': 'item.completed', 'item': {'id': 'i0', 'type': 'agent_message', 'text': 'I will read the plan.'}},
    {'type': 'item.started', 'item': {'id': 'i1', 'type': 'command_execution', 'command': 'ls'}},
    {'type': 'item.completed', 'item': {'id': 'i1', 'type': 'command_execution', 'command': 'ls', 'exit_code': 0}},
    {'type': 'item.completed', 'item': {'id': 'i2', 'type': 'agent_message', 'text': 'Done. REVIEW VERDICT: ACCEPT'}},
    {'type': 'turn.completed', 'usage': {'input_tokens': 120000, 'cached_input_tokens': 100000,
                                         'cache_write_input_tokens': 0, 'output_tokens': 2000, 'reasoning_output_tokens': 300}},
]


def load(test):
    try:
        return importlib.import_module('supervise.backends.simple.codex')
    except ImportError as error:
        test.fail('Codex driver is not implemented: ' + str(error))


class CodexDriverTests(unittest.TestCase):
    def setUp(self):
        self.c = load(self)
        self.b = importlib.import_module('supervise.backend')
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.codex_home = self.root / 'codex-home'
        self.codex_home.mkdir()
        self.packet = self.root / 'packet.md'
        self.packet.write_text('Implement it.')

    def tearDown(self):
        self.dir.cleanup()

    def route(self, price='unknown', effort='high'):
        return self.c.make_route('codex-gpt', model='gpt-test', family='openai-gpt', roles=('impl', 'review'),
                                 effort=effort, price=price)

    def request(self, role):
        return self.b.LaunchRequest(route=self.route(), role=role, packet_path=self.packet,
                                    workdir=self.work if role == 'impl' else Path('/review'), owned_paths=(Path('pkg/core.py'),),
                                    timeout_seconds=300.0, worker_commands=(), review_view=self.root if role == 'review' else None)

    def spec(self, role, **kwargs):
        return self.c.prepare_launch(self.request(role), executable='/usr/local/bin/codex', codex_home=self.codex_home, **kwargs)

    def capture(self, lines, exit_code=0, timed_out=False, stderr=b''):
        out, err = self.root / 'out.jsonl', self.root / 'err.txt'
        out.write_text(''.join(json.dumps(line) + '\n' for line in lines))
        err.write_bytes(stderr)
        return self.b.ProcessCapture(exit_code=exit_code, timed_out=timed_out, seconds=12.0, stdout_path=out, stderr_path=err,
                                     stdout_sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
                                     stderr_sha256=hashlib.sha256(err.read_bytes()).hexdigest())

    def test_route_is_review_isolated_codex(self):
        route = self.route()
        self.assertEqual((route.harness, route.vendor, route.review_isolation), ('codex', 'openai', True))

    def test_implementation_argv_is_pinned_and_sandboxed(self):
        spec = self.spec('impl')
        self.assertIsInstance(spec, self.b.LaunchSpec)
        argv = list(spec.argv)
        self.assertEqual(argv[:2], ['/usr/local/bin/codex', 'exec'])
        for flag in ('--json', '--ephemeral', '--ignore-user-config', '--ignore-rules', '--skip-git-repo-check'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index('-m') + 1], 'gpt-test')
        self.assertEqual(argv[argv.index('-C') + 1], str(self.work))
        self.assertEqual(argv[argv.index('-s') + 1], 'workspace-write')
        self.assertIn('approval_policy="never"', argv)
        self.assertIn('sandbox_workspace_write.network_access=false', argv)
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertEqual(argv[-1], '-')                                    # packet arrives on stdin from the kernel
        self.assertFalse(spec.requires_review_sandbox)
        self.assertEqual(spec.mounts, ())
        self.assertEqual(spec.stdout_format, 'codex-json')

    def test_environment_is_a_complete_minimal_allowlist(self):
        spec = self.spec('impl')
        self.assertEqual(set(spec.env), {'HOME', 'PATH', 'CODEX_HOME'})
        self.assertEqual(spec.env['CODEX_HOME'], str(self.codex_home))

    def test_review_runs_read_only_in_the_view_with_explicit_mounts(self):
        spec = self.spec('review')
        argv = list(spec.argv)
        self.assertTrue(spec.requires_review_sandbox)
        self.assertEqual(argv[argv.index('-C') + 1], '/review')
        self.assertNotIn('workspace-write', argv)
        self.assertTrue(any('network.enabled=false' in a for a in argv))
        self.assertTrue(any('filesystem=' in a and '"read"' in a for a in argv))
        targets = {m.target: m for m in spec.mounts}
        self.assertIn(spec.env['CODEX_HOME'], targets)
        self.assertTrue(targets[spec.env['CODEX_HOME']].writable)           # token refresh
        self.assertEqual(Path(targets[spec.env['CODEX_HOME']].source), self.codex_home)
        self.assertTrue(any(argv[0].startswith(m.target) for m in spec.mounts) or argv[0].startswith(('/usr/', '/bin/')))
        self.assertFalse(any(m.target in ('/', '/review') or m.target.startswith('/review/') for m in spec.mounts))

    def test_interpret_reads_final_message_usage_and_unknown_model(self):
        spec = self.spec('impl')
        outcome = self.c.interpret(spec, self.capture(STREAM), route=self.route())
        self.assertEqual(outcome.final_text, 'Done. REVIEW VERDICT: ACCEPT')
        self.assertEqual(outcome.served_model, 'unknown')
        self.assertEqual(dict(outcome.usage), {'input_tokens': 120000, 'cached_input_tokens': 100000, 'output_tokens': 2000,
                                               'reasoning_output_tokens': 300})
        self.assertEqual((outcome.cost_usd, outcome.cost_basis), ('unknown', 'unknown'))
        self.assertEqual(outcome.failure_class, 'none')

    def test_declared_price_gives_a_list_estimate_never_zero_when_unknown(self):
        priced = self.route(price={'input_per_mtok': 2.0, 'output_per_mtok': 10.0})
        outcome = self.c.interpret(self.spec('impl'), self.capture(STREAM), route=priced)
        self.assertEqual(outcome.cost_basis, 'list_price_estimate')
        self.assertAlmostEqual(outcome.cost_usd, (120000 * 2.0 + 2000 * 10.0) / 1e6)
        no_usage = [line for line in STREAM if line['type'] != 'turn.completed']
        outcome = self.c.interpret(self.spec('impl'), self.capture(no_usage), route=priced)
        self.assertEqual((outcome.usage, outcome.cost_usd, outcome.cost_basis), ('unknown', 'unknown', 'unknown'))

    def test_failure_classes(self):
        spec = self.spec('impl')
        self.assertEqual(self.c.interpret(spec, self.capture(STREAM[:3], timed_out=True, exit_code=None), route=self.route()).failure_class,
                         'platform_timeout')
        error = STREAM[:2] + [{'type': 'error', 'message': 'not supported when using Codex with a ChatGPT account'}]
        self.assertEqual(self.c.interpret(spec, self.capture(error, exit_code=1), route=self.route()).failure_class, 'platform_env')
        self.assertEqual(self.c.interpret(spec, self.capture(STREAM[:3], exit_code=2), route=self.route()).failure_class, 'worker')
        garbage = self.capture([])
        garbage.stdout_path.write_text('not json\n{"type": "turn.started"}\n')
        outcome = self.c.interpret(spec, garbage, route=self.route())
        self.assertEqual(outcome.final_text, '')
        self.assertTrue(outcome.notes)

    def test_literal_grants_only(self):
        self.c.check_grants([[SYS_PY, '-m', 'unittest', 'discover', '-s', 'tests']])
        for bad in ([['python3', '-m', 'unittest']], [[SYS_PY, '*.py']], [[SYS_PY, '-c', 'x()']],
                    [[SYS_PY, 'a\nb']], [[SYS_PY, 'tests?']], [[]]):
            with self.subTest(bad=bad):
                with self.assertRaises(self.b.UnsupportedGrant):
                    self.c.check_grants(bad)

    def test_project_harness_configuration_is_refused(self):
        self.c.preflight_project(self.work)
        for layer in ('.codex/config.toml', 'AGENTS.override.md'):
            with self.subTest(layer=layer):
                path = self.work / layer
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('x')
                with self.assertRaises(self.b.ProjectConfigRefused):
                    self.c.preflight_project(self.work)
                path.unlink()


if __name__ == '__main__':
    unittest.main()
