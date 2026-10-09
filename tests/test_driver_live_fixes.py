# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S4-L1: driver fixes found by the S4 live smokes in the proof VM.

Live smoke (fresh Ubuntu 24.04 VM, official installers): the Claude reviewer failed to start inside the review sandbox,
`execvp ~/.local/bin/claude: No such file or directory`, because the official installer makes that path a symlink into
`~/.local/share/claude/versions/<v>` and the driver mounted only the symlink's directory. The official Codex package is
laid out as `<package>/bin/codex` plus companions (`bin/codex-code-mode-host`, `codex-path/`, `codex-resources/`) and is
also reached through a symlink. Review launches must use the resolved executable and mount where it really lives.
"""
import importlib
import os
from pathlib import Path
import tempfile
import unittest


class DriverLiveFixTests(unittest.TestCase):
    def setUp(self):
        self.b = importlib.import_module('supervise.backend')
        self.codex = importlib.import_module('supervise.backends.simple.codex')
        self.claude = importlib.import_module('supervise.backends.simple.claude')
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name).resolve()
        (self.root / 'bin').mkdir()
        # Claude: versions/<v> file reached through a bin symlink
        versions = self.root / 'share' / 'claude' / 'versions'
        versions.mkdir(parents=True)
        (versions / '9.9.9').write_text('#!/bin/sh\n'); os.chmod(versions / '9.9.9', 0o755)
        (self.root / 'bin' / 'claude').symlink_to(versions / '9.9.9')
        self.claude_real = versions / '9.9.9'
        # Codex: package/bin/codex + companion, reached through a bin symlink
        package = self.root / 'share' / 'codex-package'
        (package / 'bin').mkdir(parents=True); (package / 'codex-path').mkdir(); (package / 'codex-resources').mkdir()
        for name in ('codex', 'codex-code-mode-host'):
            (package / 'bin' / name).write_text('#!/bin/sh\n'); os.chmod(package / 'bin' / name, 0o755)
        (self.root / 'bin' / 'codex').symlink_to(package / 'bin' / 'codex')
        self.package = package
        for name in ('claude-config', 'codex-home', 'work'):
            (self.root / name).mkdir()
        self.packet = self.root / 'packet.md'
        self.packet.write_text('Review it.')

    def tearDown(self):
        self.dir.cleanup()

    def request(self, route, role):
        return self.b.LaunchRequest(route=route, role=role, packet_path=self.packet,
                                    workdir=self.root / 'work' if role == 'impl' else Path('/review'), owned_paths=(),
                                    timeout_seconds=60.0, worker_commands=(), review_view=self.root if role == 'review' else None)

    def covered(self, spec, path):
        return any(str(path) == m.target or str(path).startswith(m.target.rstrip('/') + '/') for m in spec.mounts)

    def test_claude_review_uses_the_resolved_executable_and_mounts_its_real_directory(self):
        route = self.claude.make_route('claude-sonnet', model='m', family='f', roles=('review',), effort=None, price='unknown')
        spec = self.claude.prepare_launch(self.request(route, 'review'), executable=str(self.root / 'bin' / 'claude'),
                                          config_dir=self.root / 'claude-config')
        self.assertEqual(spec.argv[0], str(self.claude_real))
        self.assertTrue(self.covered(spec, self.claude_real), [m.target for m in spec.mounts])
        real_mounts = [m for m in spec.mounts if Path(m.source) == self.claude_real.parent]
        self.assertTrue(real_mounts and not real_mounts[0].writable)

    def test_codex_review_mounts_the_whole_package_root(self):
        route = self.codex.make_route('codex-gpt', model='m', family='f', roles=('review',), effort='low', price='unknown')
        spec = self.codex.prepare_launch(self.request(route, 'review'), executable=str(self.root / 'bin' / 'codex'),
                                         codex_home=self.root / 'codex-home')
        self.assertEqual(spec.argv[0], str(self.package / 'bin' / 'codex'))
        for path in (self.package / 'bin' / 'codex-code-mode-host', self.package / 'codex-path', self.package / 'codex-resources'):
            self.assertTrue(self.covered(spec, path), (path, [m.target for m in spec.mounts]))
        package_mounts = [m for m in spec.mounts if Path(m.source) == self.package]
        self.assertTrue(package_mounts and not package_mounts[0].writable)

    def test_codex_implementation_path_finds_companions(self):
        route = self.codex.make_route('codex-gpt', model='m', family='f', roles=('impl',), effort='low', price='unknown')
        spec = self.codex.prepare_launch(self.request(route, 'impl'), executable=str(self.root / 'bin' / 'codex'),
                                         codex_home=self.root / 'codex-home')
        self.assertEqual(spec.env['PATH'].split(':')[0], str(self.package / 'bin'))

    def test_missing_executables_keep_working_offline(self):
        route = self.claude.make_route('c', model='m', family='f', roles=('review',), effort=None, price='unknown')
        spec = self.claude.prepare_launch(self.request(route, 'review'), executable='/opt/missing/claude',
                                          config_dir=self.root / 'claude-config')
        self.assertEqual(spec.argv[0], '/opt/missing/claude')


if __name__ == '__main__':
    unittest.main()
