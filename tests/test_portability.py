# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S1-4: the portability scan and its repository gate."""
import ast
import contextlib
import dataclasses
import importlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
# Forbidden tokens are assembled from parts so this oracle never flags itself.
TOKENS = ('/ho' + 'me/', 'lee' + '_llm_router', 'lee' + '-llm-router', 'leebase' + 'home',
          'chief' + '-of-staff', '192' + '.168.', '/usr/bin/' + 'python3')
STDLIB = set(getattr(sys, 'stdlib_module_names', ()))


def scanner(test):
    try:
        return importlib.import_module('supervise.portability')
    except ImportError as error:
        test.fail('Portability scan is not implemented: ' + str(error))


def tree(files):
    directory = tempfile.TemporaryDirectory()
    root = Path(directory.name)
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding='utf-8')
    return directory, root


class ScanBehaviourTests(unittest.TestCase):
    def test_every_forbidden_token_is_reported_with_relative_path_and_line(self):
        m = scanner(self)
        self.assertEqual(tuple(m.FORBIDDEN), TOKENS)
        body = ''.join(f'line {i}\nvalue = "{token}x"\n' for i, token in enumerate(TOKENS))
        handle, root = tree({'pkg/mod.py': body})
        with handle:
            findings = m.scan(root)
        self.assertEqual([(f.path, f.line, f.token) for f in findings],
                         [('pkg/mod.py', 2 * i + 2, token) for i, token in enumerate(TOKENS)])

    def test_findings_are_frozen_and_sorted(self):
        m = scanner(self)
        self.assertTrue(dataclasses.is_dataclass(m.Finding))
        self.assertTrue(m.Finding.__dataclass_params__.frozen)
        self.assertEqual([f.name for f in dataclasses.fields(m.Finding)], ['path', 'line', 'token'])
        handle, root = tree({'b.py': TOKENS[0] + '\n', 'a.py': 'ok\n' + TOKENS[1] + ' ' + TOKENS[0] + '\n'})
        with handle:
            findings = m.scan(root)
        self.assertEqual([(f.path, f.line, f.token) for f in findings],
                         [('a.py', 2, TOKENS[0]), ('a.py', 2, TOKENS[1]), ('b.py', 1, TOKENS[0])])

    def test_development_and_cache_directories_are_excluded(self):
        m = scanner(self)
        files = {name + '/x.py': TOKENS[0] for name in ('.git', '.private', '__pycache__', '.venv')}
        files['nested/__pycache__/y.py'] = TOKENS[0]
        handle, root = tree(files)
        with handle:
            self.assertEqual(m.scan(root), [])

    def test_pragma_line_is_skipped_and_only_that_line(self):
        m = scanner(self)
        handle, root = tree({'m.py': f'A = "{TOKENS[2]}"  # portability: allow\nB = "{TOKENS[2]}"\n'})
        with handle:
            self.assertEqual([(f.path, f.line) for f in m.scan(root)], [('m.py', 2)])

    def test_binary_and_undecodable_files_are_skipped(self):
        m = scanner(self)
        handle, root = tree({'blob.bin': b'\xff\xfe\x00' + TOKENS[0].encode(), 'ok.txt': 'clean\n'})
        with handle:
            self.assertEqual(m.scan(root), [])

    def test_review_sandbox_mount_point_is_not_a_host_path(self):
        m = scanner(self)
        handle, root = tree({'r.py': 'VERIFY = "/opt/supervise/verify.py"\n'})
        with handle:
            self.assertEqual(m.scan(root), [])

    def test_command_line_reports_findings_and_exit_status(self):
        m = scanner(self)
        handle, root = tree({'bad.py': 'x\n' + TOKENS[4] + '\n'})
        with handle:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = m.main([str(root)])
            self.assertEqual(code, 1)
            self.assertIn('bad.py:2: ' + TOKENS[4], out.getvalue())
        handle, root = tree({'good.py': 'fine\n'})
        with handle:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(m.main([str(root)]), 0)

    def test_scanner_uses_only_the_standard_library(self):
        m = scanner(self)
        source = Path(m.__file__).read_text(encoding='utf-8')
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names.discard('__future__')
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))


class RepositoryGateTests(unittest.TestCase):
    def test_shipped_tree_is_portable(self):
        m = scanner(self)
        findings = []
        for name in ('supervise', 'tests', 'manager', 'examples', 'docs'):
            if (ROOT / name).is_dir():
                findings += [(name + '/' + f.path, f.line, f.token) for f in m.scan(ROOT / name)]
        self.assertEqual(findings, [])


if __name__ == '__main__':
    unittest.main()
