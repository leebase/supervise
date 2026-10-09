# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S3-4: simplified deterministic task classification."""
import ast
import importlib
from pathlib import Path
import sys
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))


def packet(owned='pkg/core.py', size='1 file, 20 changed lines', oracle='python3 -m unittest discover -s tests'):
    lines = ['Kind: impl', f'Owned paths: {owned}', f'Declared size: {size}']
    if oracle is not None:
        lines.append(f'Oracle: {oracle}')
    lines += ['Domain: none', 'Review: independent reviewer', '', 'Implement the thing.']
    return '\n'.join(lines)


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        try:
            self.c = importlib.import_module('supervise.controller.classify')
            self.b = importlib.import_module('supervise.backend')
        except ImportError as error:
            self.fail('Task classification is not implemented: ' + str(error))

    def test_returns_backend_task_class_with_canonical_key(self):
        task = self.c.classify(packet(), ['pkg/core.py'], role='impl')
        self.assertIsInstance(task, self.b.TaskClass)
        self.assertEqual((task.role, task.oracle, task.size, task.languages), ('impl', 'deterministic', 'xs', ('python',)))
        self.assertEqual(task.key, 'impl/deterministic/xs/python')

    def test_size_bands(self):
        for size, band in (('1 file, 20 changed lines', 'xs'), ('2 files, 21 changed lines', 's'),
                           ('3 files, 150 changed lines', 's'), ('4 files, 151 changed lines', 'm'),
                           ('10 files, 600 changed lines', 'm'), ('11 files, 10 changed lines', 'l'),
                           ('1 file, 601 changed lines', 'l')):
            with self.subTest(size=size):
                self.assertEqual(self.c.classify(packet(size=size), ['pkg/'], role='impl').size, band)

    def test_languages_come_only_from_owned_file_extensions(self):
        owned = 'pkg/a.py, web/b.ts, db/c.sql, conf/d.toml, docs/e.md, run/f.sh'
        task = self.c.classify(packet(owned=owned, size='6 files, 100 changed lines'), ['pkg/', 'web/', 'db/', 'conf/', 'docs/', 'run/'], role='impl')
        self.assertEqual(task.languages, ('markdown', 'python', 'shell', 'sql', 'typescript', 'yaml-config'))
        self.assertEqual(task.key, 'impl/deterministic/s/markdown+python+shell+sql+typescript+yaml-config')
        with self.assertRaises(ValueError):
            self.c.classify(packet(owned='pkg/'), ['pkg/'], role='impl')          # a directory has no language
        with self.assertRaises(ValueError):
            self.c.classify(packet(owned='pkg/blob.xyz'), ['pkg/'], role='impl')

    def test_review_role_and_missing_oracle_are_judged(self):
        self.assertEqual(self.c.classify(packet(), ['pkg/core.py'], role='review').oracle, 'judge')
        self.assertEqual(self.c.classify(packet(), ['pkg/core.py'], role='review').key, 'review/judge/xs/python')
        self.assertEqual(self.c.classify(packet(oracle=None), ['pkg/core.py'], role='impl').oracle, 'judge')

    def test_ownership_outside_the_admitted_scope_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            self.c.classify(packet(owned='outside.py'), ['pkg/'], role='impl')
        self.assertIn('outside frozen ownership', str(caught.exception))
        with self.assertRaises(ValueError):
            self.c.classify(packet(owned='pkg/../outside.py'), ['pkg/'], role='impl')

    def test_missing_structural_lines_and_bad_role_are_rejected(self):
        with self.assertRaises(ValueError):
            self.c.classify('Implement it without structure', ['pkg/'], role='impl')
        with self.assertRaises(ValueError):
            self.c.classify(packet(size='a few files'), ['pkg/'], role='impl')
        with self.assertRaises(ValueError):
            self.c.classify(packet(), ['pkg/core.py'], role='manager')

    def test_classification_is_deterministic_and_order_insensitive(self):
        a = self.c.classify(packet(owned='pkg/a.py, pkg/b.ts', size='2 files, 30 changed lines'), ['pkg/'], role='impl')
        b = self.c.classify(packet(owned='pkg/b.ts, pkg/a.py', size='2 files, 30 changed lines'), ['pkg/'], role='impl')
        self.assertEqual(a, b)

    def test_module_documents_non_equivalence_and_is_stdlib_only(self):
        source = Path(self.c.__file__).read_text(encoding='utf-8')
        self.assertIn('not equivalent', (self.c.__doc__ or '').lower())
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names -= {'__future__', 'supervise'}
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))


if __name__ == '__main__':
    unittest.main()
