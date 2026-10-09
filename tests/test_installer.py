# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S5-2: `supervise.installer`.

Every test installs into temporary data roots; nothing touches a real home, state or data directory.

Layout assumed (see NOTES.md):
* `source_root` is the directory that contains the `supervise` package. The release directory mirrors it, so
  `data_root/releases/<release>/supervise/...` is importable with PYTHONPATH=<release dir>.
* `publication.json` lives at the top of the release; its `files` map is keyed by POSIX paths relative to the
  release directory (e.g. `supervise/backend.py`) with sha256 hex values, never lists itself, never lists
  `__pycache__` content, and validates against the accepted `publication-manifest` schema with
  `state` = "installed" and `predecessor` = the previously installed release name in this data root, or null
  (Chief ruling).
* `verify_installation(data_root)` returns a dict with `verdict` in {PASS, FAIL}; a FAIL names the offending
  relative path somewhere in the result.

Interruption seams (the implementation must look these up as module attributes at call time):
* `supervise.installer._copy_file(source, target)` copies one package file into the new release.
* `supervise.installer._write_pointer(path, data)` atomically writes pointer bytes (install, rollback, uninstall).

Data roots are named `.../supervise` so release paths contain `/supervise/releases/`, which is what the
foreign-pointer refusal recognises; a pointer naming such a path under the same data root is an upgrade.
"""
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
PY = sys.executable
HOME_PREFIX = '/ho' + 'me/'


def load(test, name='supervise.installer'):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        test.fail(name + ' is not implemented: ' + str(error))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree(root):
    """Names, bytes and permission bits of every entry below root (None when absent)."""
    root = Path(root)
    if not root.exists():
        return None
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None, stat.S_IMODE(p.lstat().st_mode))
            for p in sorted(root.rglob('*'))}


def strings(value):
    """Every string inside a JSON-like value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings(item)


def make_writable(root):
    root = Path(root)
    if not root.exists():
        return
    for path in [root, *root.rglob('*')]:
        if not path.is_symlink():
            os.chmod(path, 0o700 if path.is_dir() else 0o600)


class InstallerFixture(unittest.TestCase):
    def setUp(self):
        self.inst = load(self)
        self.dir = tempfile.TemporaryDirectory()
        self.base = Path(self.dir.name).resolve()
        self.addCleanup(self.cleanup)
        env = {'HOME': str(self.base / 'home'), 'SUPERVISE_STATE': str(self.base / 'state'),
               'SUPERVISE_DATA': str(self.base / 'unused-data'), 'XDG_STATE_HOME': str(self.base / 'xdg-state'),
               'XDG_DATA_HOME': str(self.base / 'xdg-data'), 'XDG_CONFIG_HOME': str(self.base / 'xdg-config')}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.base / 'home').mkdir()
        self.src = self.base / 'src'
        package = Path(importlib.import_module('supervise').__file__).resolve().parent
        shutil.copytree(package, self.src / 'supervise', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (self.src / 'supervise' / '__pycache__').mkdir()
        (self.src / 'supervise' / '__pycache__' / 'stray.cpython-312.pyc').write_bytes(b'not a source file')
        self.data = self.base / 'one' / 'supervise'
        self.pointers = self.base / 'pointers'
        self.pointers.mkdir()
        self.pointer = self.pointers / 'supervise.pointer'

    def cleanup(self):
        make_writable(self.base)
        self.dir.cleanup()

    # -- helpers ------------------------------------------------------------------------------------------------
    def release_dir(self, name, data=None):
        return (data or self.data) / 'releases' / name

    def install(self, release='r1', data=None, pointer=True):
        return self.inst.install(self.src, data_root=data or self.data,
                                 pointer_path=self.pointer if pointer else None, release=release)

    def installation(self, data=None):
        return json.loads(((data or self.data) / 'INSTALLATION.json').read_text())

    def publication(self, name='r1', data=None):
        return json.loads((self.release_dir(name, data) / 'publication.json').read_text())

    def verified(self, data=None):
        return self.inst.verify_installation(data or self.data)

    def assert_pass(self, data=None):
        result = self.verified(data)
        self.assertEqual(result['verdict'], 'PASS', result)

    def assert_fail(self, fragment, data=None):
        result = self.verified(data)
        self.assertEqual(result['verdict'], 'FAIL', result)
        self.assertIn(fragment, json.dumps(result, default=str))

    def refused(self, call):
        with self.assertRaises(self.inst.InstallError):
            call()

    def package_sources(self):
        return sorted(p.relative_to(self.src).as_posix() for p in (self.src / 'supervise').rglob('*.py')
                      if '__pycache__' not in p.parts)


class InstallTests(InstallerFixture):
    def test_install_copies_every_package_file_with_publication_hashes(self):
        result = self.install()
        release = self.release_dir('r1')
        publication = self.publication()
        files = publication['files']
        self.assertEqual(publication['release'], 'r1')
        self.assertTrue(isinstance(publication['version'], str) and publication['version'])
        self.assertNotIn('publication.json', files)
        self.assertFalse([name for name in files if '__pycache__' in name or name.endswith('.pyc')], files)
        for name in self.package_sources():
            with self.subTest(file=name):
                self.assertIn(name, files)
                self.assertEqual(files[name], sha(self.src / name))
        for name, digest in files.items():
            self.assertFalse(Path(name).is_absolute() or '..' in Path(name).parts, name)
            self.assertEqual(sha(release / name), digest, name)
        python = [str(value) for key, value in publication.items() if 'python' in key.lower()]
        self.assertTrue(any('3.12' in value for value in python), 'publication records the python requirement')
        schemas = load(self, 'supervise.schemas')
        self.assertEqual(schemas.validate('publication-manifest', publication), [])
        self.assertEqual(publication['state'], 'installed')
        self.assertIsNone(publication['predecessor'], 'a first installation has no predecessor')
        self.assertEqual(result['publication_sha256'], sha(release / 'publication.json'))

    def test_release_is_read_only(self):
        self.install()
        release = self.release_dir('r1')
        for path in [release, *release.rglob('*')]:
            with self.subTest(path=str(path.relative_to(release.parent))):
                self.assertFalse(path.is_symlink())
                self.assertFalse(stat.S_IMODE(path.stat().st_mode) & 0o222, 'release entries are read-only')

    def test_installation_record(self):
        self.pointer.write_bytes(b'release = none\n')
        result = self.install()
        record = self.installation()
        release = self.release_dir('r1')
        self.assertTrue(record['release'] in ('r1', str(release)), record['release'])
        self.assertEqual(record['publication_sha256'], sha(release / 'publication.json'))
        self.assertEqual(result['publication_sha256'], record['publication_sha256'])
        values = list(strings(record))
        self.assertIn(str(self.pointer), values, 'INSTALLATION.json records the pointer path')
        backups = [Path(v) for v in values if v.startswith('/') and Path(v).is_file()
                   and Path(v).read_bytes() == b'release = none\n']
        self.assertTrue(backups, 'INSTALLATION.json records a backup holding the previous pointer bytes')
        self.assert_pass()

    def test_pointer_names_new_release_and_writes_atomically(self):
        self.pointer.write_bytes(b'release = none\n')
        self.install()
        text = self.pointer.read_text()
        self.assertIn(str(self.release_dir('r1')), text)
        self.assertLess(len(self.pointer.read_bytes()), 4096, 'a small pointer file')
        mentioned = set(strings(self.installation()))
        leftovers = [p.name for p in self.pointers.iterdir() if p != self.pointer and str(p) not in mentioned]
        self.assertEqual(leftovers, [], 'no staging files remain beside the pointer')

    def test_install_without_pointer_writes_no_pointer(self):
        self.install(pointer=False)
        self.assertFalse(self.pointer.exists())
        self.assertEqual(list(self.pointers.iterdir()), [])
        self.assert_pass()

    def test_default_release_name_is_generated(self):
        self.inst.install(self.src, data_root=self.data, pointer_path=None)
        releases = [p for p in (self.data / 'releases').iterdir()]
        self.assertEqual(len(releases), 1)
        name = releases[0].name
        self.assertTrue(name and '/' not in name)
        self.assertEqual(json.loads((releases[0] / 'publication.json').read_text())['release'], name)
        self.assertIn(self.installation()['release'], (name, str(releases[0])))
        self.assert_pass()

    def test_install_never_modifies_the_source_tree(self):
        before = tree(self.src)
        self.install()
        self.assertEqual(tree(self.src), before)

    def test_installed_release_is_importable(self):
        self.install()
        release = self.release_dir('r1')
        env = dict(os.environ, PYTHONPATH=str(release), PYTHONDONTWRITEBYTECODE='1')
        done = subprocess.run([PY, '-c', 'import supervise, supervise.installer; print(supervise.__file__)'],
                              cwd=str(self.base), env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(Path(done.stdout.strip()).resolve().is_relative_to(release), done.stdout)

    def test_upgrade_within_the_same_data_root_is_allowed(self):
        self.install('r1')
        first = self.pointer.read_bytes()
        self.install('r2')
        self.assertEqual(self.publication('r2')['predecessor'], 'r1')
        self.assertEqual(self.publication('r2')['state'], 'installed')
        self.assertIsNone(self.publication('r1')['predecessor'])
        text = self.pointer.read_text()
        self.assertIn(str(self.release_dir('r2')), text)
        self.assertNotIn(str(self.release_dir('r1')), text)
        self.assertTrue(self.release_dir('r1').is_dir(), 'earlier releases are history')
        self.assertTrue(any(Path(v).is_file() and Path(v).read_bytes() == first
                            for v in strings(self.installation()) if v.startswith('/')))
        self.assert_pass()


class RefusalTests(InstallerFixture):
    def assert_nothing_installed(self, pointer_before, data=None):
        data = data or self.data
        self.assertEqual(self.pointer.read_bytes() if self.pointer.exists() else None, pointer_before)
        releases = data / 'releases'
        self.assertFalse(releases.exists() and any(releases.iterdir()), 'no release directory was left behind')
        self.assertFalse((data / 'INSTALLATION.json').exists())

    def test_foreign_production_pointer_is_refused(self):
        other = self.base / 'two' / 'supervise'
        self.inst.install(self.src, data_root=other, pointer_path=self.pointer, release='theirs')
        owned = self.pointer.read_bytes()
        other_tree = tree(other)
        self.refused(lambda: self.install('r1'))
        self.assert_nothing_installed(owned)
        self.assertEqual(tree(other), other_tree, 'the other installation is untouched')

    def test_pointer_naming_a_home_installation_is_refused(self):
        content = ('release = ' + HOME_PREFIX + 'someone/.local/share/supervise/releases/20261001-x\n').encode()
        self.pointer.write_bytes(content)
        self.refused(lambda: self.install('r1'))
        self.assert_nothing_installed(content)

    def test_data_root_inside_source_is_refused(self):
        before = tree(self.src)
        inside = self.src / 'nested' / 'supervise'
        self.refused(lambda: self.inst.install(self.src, data_root=inside, pointer_path=self.pointer, release='r1'))
        self.assertFalse(self.pointer.exists())
        self.assertFalse((inside / 'releases' / 'r1').exists())
        self.assertFalse((inside / 'INSTALLATION.json').exists())
        self.assertEqual(tree(self.src), before, 'nothing is written into the source tree')

    def test_existing_release_is_refused_and_kept(self):
        self.install('r1')
        release = tree(self.release_dir('r1'))
        record = (self.data / 'INSTALLATION.json').read_bytes()
        pointer = self.pointer.read_bytes()
        self.refused(lambda: self.install('r1'))
        self.assertEqual(tree(self.release_dir('r1')), release, 'never overwrite or remove an existing release')
        self.assertEqual((self.data / 'INSTALLATION.json').read_bytes(), record)
        self.assertEqual(self.pointer.read_bytes(), pointer)
        self.assert_pass()

    def test_foreign_directory_at_release_path_is_refused_and_kept(self):
        target = self.release_dir('r1')
        target.mkdir(parents=True)
        (target / 'keep.txt').write_text('someone else')
        self.refused(lambda: self.install('r1'))
        self.assertEqual((target / 'keep.txt').read_text(), 'someone else')
        self.assertFalse(self.pointer.exists())
        self.assertFalse((self.data / 'INSTALLATION.json').exists())

    def test_tampered_copy_is_refused_and_rolled_back(self):
        self.install('r1')
        pointer = self.pointer.read_bytes()
        record = (self.data / 'INSTALLATION.json').read_bytes()
        real = self.inst._copy_file
        seen = []

        def corrupting(source, target, *args, **kwargs):
            outcome = real(source, target, *args, **kwargs)
            seen.append(str(target))
            if len(seen) == 2:
                mode = stat.S_IMODE(os.stat(target).st_mode)
                os.chmod(target, 0o600)
                with open(target, 'ab') as stream:
                    stream.write(b'\n# tampered\n')
                os.chmod(target, mode)
            return outcome
        with mock.patch.object(self.inst, '_copy_file', corrupting):
            self.refused(lambda: self.install('r2'))
        self.assertGreaterEqual(len(seen), 2, 'the copy seam was used')
        self.assertFalse(self.release_dir('r2').exists(), 'the release this call created is removed')
        self.assertTrue(self.release_dir('r1').is_dir())
        self.assertEqual(self.pointer.read_bytes(), pointer)
        self.assertEqual((self.data / 'INSTALLATION.json').read_bytes(), record)
        self.assert_pass()


class RollbackTests(InstallerFixture):
    def test_failure_during_copy_removes_only_this_release(self):
        self.install('r1')
        pointer = self.pointer.read_bytes()
        real = self.inst._copy_file
        calls = []

        def failing(source, target, *args, **kwargs):
            calls.append(target)
            if len(calls) == 3:
                raise OSError('CONTROL simulated copy failure')
            return real(source, target, *args, **kwargs)
        with mock.patch.object(self.inst, '_copy_file', failing):
            with self.assertRaises((OSError, self.inst.InstallError)):
                self.install('r2')
        self.assertFalse(self.release_dir('r2').exists())
        self.assertTrue(self.release_dir('r1').is_dir())
        self.assertEqual(self.pointer.read_bytes(), pointer)
        self.assert_pass()

    def test_failure_after_pointer_write_restores_pointer_and_record(self):
        self.install('r1')
        pointer = self.pointer.read_bytes()
        record = self.installation()
        r1 = tree(self.release_dir('r1'))
        real = self.inst._write_pointer
        calls = []

        def die_after_first_write(path, data, *args, **kwargs):
            outcome = real(path, data, *args, **kwargs)
            calls.append(Path(path))
            if len(calls) == 1:
                raise RuntimeError('CONTROL simulated failure after the pointer changed')
            return outcome
        with mock.patch.object(self.inst, '_write_pointer', die_after_first_write):
            with self.assertRaises((RuntimeError, self.inst.InstallError)):
                self.install('r2')
        self.assertTrue(calls and calls[0] == self.pointer, 'the pointer seam was used for the install write')
        self.assertEqual(self.pointer.read_bytes(), pointer, 'pointer restored')
        self.assertFalse(self.release_dir('r2').exists(), 'release created by this call removed')
        self.assertEqual(tree(self.release_dir('r1')), r1, 'other releases untouched')
        self.assertEqual(self.installation()['publication_sha256'], record['publication_sha256'])
        self.assert_pass()

    def test_failure_on_first_install_leaves_no_pointer(self):
        real = self.inst._write_pointer

        def die(path, data, *args, **kwargs):
            real(path, data, *args, **kwargs)
            raise RuntimeError('CONTROL simulated failure after the pointer changed')
        calls = []

        def once(path, data, *args, **kwargs):
            calls.append(path)
            if len(calls) == 1:
                return die(path, data, *args, **kwargs)
            return real(path, data, *args, **kwargs)
        with mock.patch.object(self.inst, '_write_pointer', once):
            with self.assertRaises((RuntimeError, self.inst.InstallError)):
                self.install('r1')
        self.assertFalse(self.pointer.exists(), 'a pointer that did not exist before is removed again')
        self.assertFalse(self.release_dir('r1').exists())


class UninstallTests(InstallerFixture):
    def test_uninstall_restores_previous_pointer_and_keeps_release(self):
        self.pointer.write_bytes(b'release = none\n')
        self.install('r1')
        self.inst.uninstall(self.data, pointer_path=self.pointer)
        self.assertEqual(self.pointer.read_bytes(), b'release = none\n')
        self.assertTrue((self.release_dir('r1') / 'publication.json').is_file(), 'releases are history')
        recorded = [p for p in self.data.iterdir() if p.is_file() and 'uninstall' in p.read_text().lower()]
        self.assertTrue(recorded, 'the uninstall is recorded in the data root')

    def test_uninstall_removes_pointer_when_none_existed(self):
        self.install('r1')
        self.assertTrue(self.pointer.exists())
        self.inst.uninstall(self.data, pointer_path=self.pointer)
        self.assertFalse(self.pointer.exists())
        self.assertTrue(self.release_dir('r1').is_dir())

    def test_uninstall_after_upgrade_restores_the_earlier_release(self):
        self.install('r1')
        first = self.pointer.read_bytes()
        self.install('r2')
        self.inst.uninstall(self.data, pointer_path=self.pointer)
        self.assertEqual(self.pointer.read_bytes(), first)
        self.assertTrue(self.release_dir('r1').is_dir())
        self.assertTrue(self.release_dir('r2').is_dir())


class VerifyInstallationTests(InstallerFixture):
    def test_changed_installed_file_fails(self):
        self.install('r1')
        self.assert_pass()
        target = self.release_dir('r1') / 'supervise' / 'backend.py'
        os.chmod(target, 0o600)
        target.write_bytes(target.read_bytes() + b'\n# changed\n')
        os.chmod(target, 0o444)
        self.assert_fail('backend.py')

    def test_missing_installed_file_fails(self):
        self.install('r1')
        package = self.release_dir('r1') / 'supervise'
        os.chmod(package, 0o700)
        (package / 'schemas.py').unlink()
        self.assert_fail('schemas.py')

    def test_changed_publication_fails(self):
        self.install('r1')
        target = self.release_dir('r1') / 'publication.json'
        publication = json.loads(target.read_text())
        publication['version'] = publication['version'] + '-forged'
        os.chmod(target, 0o600)
        target.write_text(json.dumps(publication))
        os.chmod(target, 0o444)
        self.assert_fail('publication')

    def test_verification_is_read_only(self):
        self.install('r1')
        before = tree(self.data)
        self.assert_pass()
        self.assertEqual(tree(self.data), before)


class StdlibTests(unittest.TestCase):
    def test_installer_is_stdlib_only_and_has_no_private_paths(self):
        module = load(self)
        source = Path(module.__file__).read_text(encoding='utf-8')
        names = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names -= {'__future__', 'supervise'}
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))
        self.assertNotIn(HOME_PREFIX, source)
        self.assertNotIn('.omp/', source)


if __name__ == '__main__':
    unittest.main()
