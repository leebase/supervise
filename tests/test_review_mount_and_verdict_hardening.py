# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S7-R1: two code defects found by the cross-family documentation review.

H1 (security): the Codex review launch mounted the parent of any directory named `bin`, so `~/bin/codex` exposed the
whole home directory (read-only) to the connected reviewer sandbox. Fix, defence in depth:
- the Codex driver mounts the parent of `bin` only when it is the official Codex package root (it holds
  `codex-package.json`, or both `codex-path/` and `codex-resources/`); otherwise only the executable's own directory;
- neither driver adds a runtime mount whose source is the user's home directory or one of its ancestors;
- `review_view.reviewer_argv` refuses any mount whose resolved source is `/`, the user's home directory (Path.home())
  or an ancestor of it.
M3 (false acceptance): `parse_verdict` accepted a verdict marker anywhere in the text. It now takes the final non-empty
line of the final text; the last marker on that line must be followed by nothing but punctuation, emphasis or
whitespace (so the frozen rule "the last marker wins" still holds on that line); the marker must still appear in the
captured stdout. (Amended by the Chief after S7-R1: "exactly one marker" contradicted the frozen S2 verdict test.)
"""
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class VerdictAnchoringTests(unittest.TestCase):
    def setUp(self):
        self.v = importlib.import_module('supervise.kernel.verdict')

    def parse(self, text):
        return self.v.parse_verdict(text, text.encode('utf-8'))

    def test_final_line_verdicts(self):
        self.assertEqual(self.parse('Findings: none.\nREVIEW VERDICT: ACCEPT'), 'accept')
        self.assertEqual(self.parse('Two blocking issues.\n\n**REVIEW VERDICT: REJECT**\n'), 'reject')
        self.assertEqual(self.parse('Done. REVIEW VERDICT: ACCEPT'), 'accept')
        self.assertEqual(self.parse('REVIEW VERDICT: REJECT.'), 'reject')

    def test_marker_not_on_the_final_line_is_ignored(self):
        self.assertIsNone(self.parse('The packet says to end with REVIEW VERDICT: ACCEPT.\nThe work is wrong.'))
        self.assertIsNone(self.parse('REVIEW VERDICT: ACCEPT\nActually, there is a bug in line 3.'))

    def test_last_marker_on_the_final_line_must_end_it(self):
        self.assertEqual(self.parse('Return REVIEW VERDICT: ACCEPT or REVIEW VERDICT: REJECT'), 'reject')
        self.assertEqual(self.parse('REVIEW VERDICT: REJECT then REVIEW VERDICT: ACCEPT'), 'accept')
        self.assertIsNone(self.parse('I would give REVIEW VERDICT: ACCEPT if the tests passed'))
        self.assertIsNone(self.parse('REVIEW VERDICT: REJECT, but REVIEW VERDICT: ACCEPT after fixes'))

    def test_marker_must_still_be_captured_on_stdout(self):
        self.assertIsNone(self.v.parse_verdict('ok\nREVIEW VERDICT: ACCEPT', b'nothing here'))


class ReviewMountHardeningTests(unittest.TestCase):
    def setUp(self):
        self.b = importlib.import_module('supervise.backend')
        self.codex = importlib.import_module('supervise.backends.simple.codex')
        self.claude = importlib.import_module('supervise.backends.simple.claude')
        self.rv = importlib.import_module('supervise.kernel.review_view')
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name).resolve()
        self.home = self.root / 'home' / 'user'
        (self.home / 'bin').mkdir(parents=True)
        (self.home / '.codex').mkdir()
        (self.home / '.claude').mkdir()
        self.packet = self.root / 'packet.md'
        self.packet.write_text('Review it.')
        patcher = mock.patch.dict(os.environ, {'HOME': str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.dir.cleanup()

    def tool(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\n')
        os.chmod(path, 0o755)
        return path

    def review(self, adapter, executable, config):
        route = adapter.make_route('r', model='m', family='f', roles=('review',), effort=None, price='unknown')
        request = self.b.LaunchRequest(route=route, role='review', packet_path=self.packet, workdir=Path('/review'),
                                       owned_paths=(), timeout_seconds=60.0, worker_commands=(), review_view=self.root)
        if adapter is self.codex:
            return adapter.prepare_launch(request, executable=str(executable), codex_home=config)
        return adapter.prepare_launch(request, executable=str(executable), config_dir=config)

    def runtime_sources(self, spec):
        return [Path(m.source) for m in spec.mounts if not m.writable]

    def exposes_home(self, sources):
        return [s for s in sources if s == self.home or s in self.home.parents or s == Path('/')]

    def test_codex_in_home_bin_mounts_only_that_directory(self):
        exe = self.tool(self.home / 'bin' / 'codex')
        spec = self.review(self.codex, exe, self.home / '.codex')
        self.assertEqual(self.runtime_sources(spec), [self.home / 'bin'])

    def test_codex_package_root_is_still_mounted_when_it_is_one(self):
        package = self.home / '.local' / 'share' / 'codex-package'
        exe = self.tool(package / 'bin' / 'codex')
        (package / 'codex-package.json').write_text('{}')
        spec = self.review(self.codex, exe, self.home / '.codex')
        self.assertEqual(self.runtime_sources(spec), [package])

    def test_drivers_never_mount_the_home_directory(self):
        for adapter, name, config in ((self.codex, 'codex', self.home / '.codex'),
                                      (self.claude, 'claude', self.home / '.claude')):
            with self.subTest(adapter=name):
                spec = self.review(adapter, self.tool(self.home / name), config)
                self.assertEqual(self.exposes_home(self.runtime_sources(spec)), [])

    def test_kernel_refuses_home_and_its_ancestors_as_mount_sources(self):
        seal = importlib.import_module('supervise.kernel.seal')
        src = self.root / 'src'
        src.mkdir()
        (src / 'stdout.txt').write_text('x')
        store, views = self.root / 'store', self.root / 'views'
        store.mkdir(mode=0o700)
        views.mkdir(mode=0o700)
        binding = {'mission_id': 'm', 'transaction_id': 'u', 'worker_id': 'w', 'worker_role': 'impl',
                   'attempt_id': 'a', 'source_id': 'b' * 64}
        handle = seal.seal(store, binding=binding, files={'stdout.txt': src / 'stdout.txt'}, pins={})
        view = Path(self.rv.materialize(handle, expected_binding=binding, expected_identity=handle['identity'],
                                        reviewer_id='r', view_root=views)['path'])
        ok_dir = self.home / '.local' / 'share' / 'tool'
        ok_dir.mkdir(parents=True)
        self.rv.reviewer_argv(view, ['/usr/bin/true'], expected_identity=handle['identity'],
                              mounts=[(ok_dir, '/opt/tool', False)], home='/sandbox/home')
        for source in (self.home, self.home.parent, Path('/')):
            with self.subTest(source=source):
                with self.assertRaises(self.rv.ViewError):
                    self.rv.reviewer_argv(view, ['/usr/bin/true'], expected_identity=handle['identity'],
                                          mounts=[(source, '/opt/x', False)], home='/sandbox/home')


if __name__ == '__main__':
    unittest.main()
