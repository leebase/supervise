# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S4-1: the `simple` reference backend's configuration and crew planning."""
import ast
import importlib
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
CONFIG = textwrap.dedent('''
    [[route]]
    id = "codex-gpt"
    harness = "codex"
    vendor = "openai"
    model = "gpt-test"
    family = "openai-gpt"
    roles = ["impl", "review"]
    effort = "high"
    price = { input_per_mtok = 1.25, output_per_mtok = 10.0 }

    [[route]]
    id = "claude-sonnet"
    harness = "claude"
    vendor = "anthropic"
    model = "sonnet-test"
    family = "anthropic-claude"
    roles = ["review", "impl"]

    [[route]]
    id = "claude-haiku"
    harness = "claude"
    vendor = "anthropic"
    model = "haiku-test"
    family = "anthropic-claude"
    roles = ["review"]
    price = { input_per_mtok = 1.0, output_per_mtok = 5.0 }

    [crew]
    authors = ["codex-gpt", "claude-sonnet"]
    reviewers = ["claude-sonnet", "claude-haiku", "codex-gpt"]
    require_review_fallback = false
''')


def load(test):
    try:
        return importlib.import_module('supervise.backends.simple')
    except ImportError as error:
        test.fail('simple backend is not implemented: ' + str(error))


class SimpleConfigTests(unittest.TestCase):
    def setUp(self):
        self.s = load(self)
        self.b = importlib.import_module('supervise.backend')
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'config.toml'
        self.path.write_text(CONFIG)

    def tearDown(self):
        self.dir.cleanup()

    def backend(self, text=None):
        if text is not None:
            self.path.write_text(text)
        return self.s.SimpleBackend(self.path)

    def task(self, role='impl'):
        return self.b.TaskClass(role=role, oracle='deterministic', size='s', languages=('python',), key=role + '/deterministic/s/python')

    def request(self, **overrides):
        values = dict(task=self.task(), prior_authors=(), failed_authors=(), failed_reviewers=(), pinned_author=None,
                      require_review_fallback=False)
        values.update(overrides)
        return self.b.CrewRequest(**values)

    def test_routes_are_loaded_exactly(self):
        identity = self.backend().identity()
        self.assertIsInstance(identity, self.b.BackendIdentity)
        self.assertEqual(identity.name, 'simple')
        routes = {r.route_id: r for r in identity.routes}
        self.assertEqual(list(routes), ['codex-gpt', 'claude-sonnet', 'claude-haiku'])
        gpt = routes['codex-gpt']
        self.assertEqual((gpt.harness, gpt.vendor, gpt.model, gpt.family, gpt.roles, gpt.review_isolation),
                         ('codex', 'openai', 'gpt-test', 'openai-gpt', frozenset({'impl', 'review'}), True))
        self.assertEqual(dict(gpt.price), {'input_per_mtok': 1.25, 'output_per_mtok': 10.0})
        self.assertEqual(routes['claude-sonnet'].price, 'unknown')         # absent price stays unknown, never zero

    def test_identity_digests_track_configuration_and_source(self):
        first = self.backend().identity()
        self.assertEqual(first, self.backend().identity())
        changed = self.backend(CONFIG.replace('gpt-test', 'gpt-other')).identity()
        self.assertNotEqual(first.config_sha256, changed.config_sha256)
        self.assertEqual(first.source_sha256, changed.source_sha256)
        self.assertEqual((len(first.config_sha256), len(first.source_sha256)), (64, 64))

    def test_crew_follows_declared_preference_and_family_independence(self):
        plan = self.backend().plan_crew(self.request())
        self.assertEqual(plan.author.route_id, 'codex-gpt')
        self.assertEqual([r.route_id for r in plan.reviewers], ['claude-sonnet', 'claude-haiku'])
        self.assertIn('codex-gpt', plan.excluded)                         # same family cannot review itself
        self.assertIn('declared preference order', plan.rationale)
        self.assertEqual(plan.cost_status, 'unavailable')                 # sonnet has no declared price

    def test_failures_prior_authors_and_pinned_author_steer_the_crew(self):
        plan = self.backend().plan_crew(self.request(failed_authors=('codex-gpt',)))
        self.assertEqual(plan.author.route_id, 'claude-sonnet')
        self.assertEqual([r.route_id for r in plan.reviewers], ['codex-gpt'])
        with self.assertRaises(self.b.CrewUnavailable):                   # every reviewer shares a prior author family
            self.backend().plan_crew(self.request(failed_authors=('codex-gpt',), prior_authors=('codex-gpt',)))
        plan = self.backend().plan_crew(self.request(task=self.task('review'), pinned_author='codex-gpt',
                                                     prior_authors=('codex-gpt',), failed_reviewers=('claude-sonnet',)))
        self.assertEqual(plan.author.route_id, 'codex-gpt')
        self.assertEqual([r.route_id for r in plan.reviewers], ['claude-haiku'])

    def test_cost_status_is_variable_only_when_every_chosen_route_is_priced(self):
        priced = CONFIG.replace('authors = ["codex-gpt", "claude-sonnet"]', 'authors = ["codex-gpt"]') \
                       .replace('reviewers = ["claude-sonnet", "claude-haiku", "codex-gpt"]', 'reviewers = ["claude-haiku"]')
        self.assertEqual(self.backend(priced).plan_crew(self.request()).cost_status, 'variable_available')

    def test_fallback_requirement_is_enforced(self):
        with self.assertRaises(self.b.CrewUnavailable):
            self.backend().plan_crew(self.request(failed_reviewers=('claude-sonnet',), require_review_fallback=True))
        strict = CONFIG.replace('require_review_fallback = false', 'require_review_fallback = true')
        with self.assertRaises(self.b.CrewUnavailable):
            self.backend(strict).plan_crew(self.request(failed_reviewers=('claude-sonnet',)))

    def test_invalid_configurations_are_refused_with_named_errors(self):
        cases = {
            'unknown harness': CONFIG.replace('harness = "codex"', 'harness = "opencode"'),
            'duplicate id': CONFIG.replace('id = "claude-haiku"', 'id = "claude-sonnet"'),
            'unknown key': CONFIG.replace('effort = "high"', 'effort = "high"\nefort = "typo"'),
            'missing model': CONFIG.replace('model = "gpt-test"\n', ''),
            'bad role': CONFIG.replace('roles = ["review"]', 'roles = ["manager"]'),
            'crew unknown route': CONFIG.replace('authors = ["codex-gpt", "claude-sonnet"]', 'authors = ["ghost"]'),
            'crew role mismatch': CONFIG.replace('authors = ["codex-gpt", "claude-sonnet"]', 'authors = ["claude-haiku"]'),
            'negative price': CONFIG.replace('input_per_mtok = 1.25', 'input_per_mtok = -1.0'),
            'no routes': '[crew]\nauthors = []\nreviewers = []\n',
            'not toml': 'this is = = not toml',
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(self.s.ConfigError):
                    self.backend(text)
        with self.assertRaises(self.s.ConfigError):
            self.s.SimpleBackend(Path(self.dir.name) / 'missing.toml')

    def test_package_is_stdlib_only(self):
        root = Path(self.s.__file__).parent
        for path in sorted(root.glob('*.py')):
            names = set()
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, ast.Import):
                    names.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names.add(node.module.split('.')[0])
            names -= {'__future__', 'supervise'}
            self.assertTrue(names <= STDLIB, (path.name, sorted(names - STDLIB)))


if __name__ == '__main__':
    unittest.main()
