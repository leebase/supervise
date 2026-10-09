# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S2-3: content-addressed evidence seals."""
import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

STDLIB = set(getattr(sys, 'stdlib_module_names', ()))
BINDING = {'mission_id': 'm-1', 'transaction_id': 'u-1', 'worker_id': 'route-a', 'worker_role': 'impl',
           'attempt_id': 'att-1', 'source_id': 'a' * 64}


def seal_module(test):
    try:
        return importlib.import_module('supervise.kernel.seal')
    except ImportError as error:
        test.fail('Kernel seal is not implemented: ' + str(error))


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SealTests(unittest.TestCase):
    def setUp(self):
        self.s = seal_module(self)
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.store = root / 'store'
        self.store.mkdir(mode=0o700)
        self.src = root / 'src'
        self.src.mkdir()
        self.files = {}
        for name, data in {'attempt.json': b'{"verdict":"pass"}', 'stdout.txt': b'line\n' * 1000,
                           'artifacts/source.diff': b'diff --git a b\n'}.items():
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.files[name] = path

    def tearDown(self):
        for path in Path(self.dir.name).rglob('*'):
            if not path.is_symlink():
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        self.dir.cleanup()

    def make(self, **overrides):
        values = dict(binding=BINDING, files=self.files, pins={'attempt.json': sha(b'{"verdict":"pass"}')})
        values.update(overrides)
        return self.s.seal(self.store, **values)

    def test_constants_match_the_accepted_limits(self):
        self.assertEqual(self.s.CHUNK, 2 * 1024 * 1024)
        self.assertEqual(self.s.MAX_LOGICAL, 128 * 1024 * 1024)
        self.assertEqual(self.s.MAX_TOTAL, 256 * 1024 * 1024)
        self.assertEqual(set(self.s.BINDING_FIELDS), set(BINDING))

    def test_seal_verify_and_read_round_trip(self):
        handle = self.make()
        self.assertEqual(set(handle), {'path', 'identity'})
        self.assertEqual(Path(handle['path']).name, handle['identity'])
        self.assertEqual(Path(handle['path']).parent, self.store)
        result = self.s.verify(handle, expected_binding=BINDING, expected_identity=handle['identity'])
        self.assertEqual(result['files'], {name: sha(path.read_bytes()) for name, path in self.files.items()})
        for name, path in self.files.items():
            self.assertEqual(self.s.read(handle, name), path.read_bytes())

    def test_identity_is_content_addressed_and_resealing_is_idempotent(self):
        first = self.make()
        second = self.make()
        self.assertEqual(first, second)
        self.files['stdout.txt'].write_bytes(b'changed\n')
        self.assertNotEqual(self.make()['identity'], first['identity'])
        other = dict(BINDING, attempt_id='att-2')
        self.assertNotEqual(self.make(binding=other)['identity'], first['identity'])

    def test_large_files_are_chunked_and_reassembled(self):
        big = os.urandom(self.s.CHUNK * 2 + 17)
        path = self.src / 'stdout.txt'
        path.write_bytes(big)
        handle = self.make()
        self.assertEqual(self.s.read(handle, 'stdout.txt'), big)
        record = json.loads((Path(handle['path']) / 'seal.json').read_text())
        chunks = record['files']['stdout.txt']['chunks']
        self.assertEqual(len(chunks), 3)
        self.assertEqual(record['files']['stdout.txt']['sha256'], sha(big))
        self.assertEqual(record['binding'], BINDING)

    def test_sealed_bundle_is_read_only(self):
        handle = self.make()
        for path in [Path(handle['path']), *Path(handle['path']).rglob('*')]:
            self.assertFalse(path.stat().st_mode & 0o222, path)

    def test_any_tampering_is_detected(self):
        def flip(path):
            os.chmod(path.parent, 0o700); os.chmod(path, 0o600)
            data = bytearray(path.read_bytes()); data[0] ^= 1; path.write_bytes(bytes(data))
        cases = ('chunk', 'seal', 'missing', 'extra')
        for case in cases:
            with self.subTest(case=case):
                handle = self.make(binding=dict(BINDING, attempt_id='att-' + case))
                bundle = Path(handle['path'])
                chunk = next(p for p in bundle.rglob('*') if p.is_file() and p.name != 'seal.json')
                if case == 'chunk':
                    flip(chunk)
                elif case == 'seal':
                    flip(bundle / 'seal.json')
                elif case == 'missing':
                    os.chmod(chunk.parent, 0o700); chunk.unlink()
                else:
                    os.chmod(bundle, 0o700); (bundle / 'extra').write_bytes(b'x')
                for path in [bundle, *bundle.rglob('*')]:                 # restore read-only so content checks run
                    os.chmod(path, 0o555 if path.is_dir() else 0o444)
                with self.assertRaises(self.s.SealError) as caught:
                    self.s.verify(handle, expected_binding=dict(BINDING, attempt_id='att-' + case),
                                  expected_identity=handle['identity'])
                self.assertNotIn('writable', str(caught.exception).lower())

    def test_binding_and_identity_mismatches_are_refused(self):
        handle = self.make()
        with self.assertRaises(self.s.SealError):
            self.s.verify(handle, expected_binding=dict(BINDING, worker_id='route-b'), expected_identity=handle['identity'])
        with self.assertRaises(self.s.SealError):
            self.s.verify(handle, expected_binding=BINDING, expected_identity='b' * 64)
        with self.assertRaises(self.s.SealError):
            self.s.verify({'path': handle['path'], 'identity': handle['identity'], 'x': 1},
                          expected_binding=BINDING, expected_identity=handle['identity'])

    def test_invalid_binding_names_and_pins_are_refused(self):
        for bad in ({'binding': {k: v for k, v in BINDING.items() if k != 'source_id'}},
                    {'binding': dict(BINDING, extra='x')},
                    {'binding': dict(BINDING, attempt_id='bad id/../x')},
                    {'pins': {'attempt.json': 'c' * 64}},
                    {'files': {**self.files, '../escape': self.files['stdout.txt']}},
                    {'files': {**self.files, '/abs': self.files['stdout.txt']}}):
            with self.subTest(bad=list(bad)):
                with self.assertRaises(self.s.SealError):
                    self.make(**bad)

    def test_unsafe_sources_are_refused(self):
        link = self.src / 'link.txt'
        link.symlink_to(self.files['stdout.txt'])
        with self.assertRaises(self.s.SealError):
            self.make(files={**self.files, 'link.txt': link})
        with self.assertRaises(self.s.SealError):
            self.make(files={**self.files, 'dir': self.src})
        with self.assertRaises(self.s.SealError):
            self.make(files={**self.files, 'gone': self.src / 'missing'})

    def test_credential_material_is_never_sealed(self):
        for name, data in (('auth.json', b'{}'), ('.credentials.json', b'{}'), ('key.pem', b'x'),
                           ('notes.txt', b'-----BEGIN ' + b'OPENSSH PRIVATE ' + b'KEY-----\nabc\n')):
            with self.subTest(name=name):
                path = self.src / name
                path.write_bytes(data)
                with self.assertRaises(self.s.SealError):
                    self.make(files={**self.files, name: path})

    def test_logical_and_total_limits_are_enforced(self):
        original = (self.s.MAX_LOGICAL, self.s.MAX_TOTAL)
        try:
            self.s.MAX_LOGICAL = 4000
            with self.assertRaises(self.s.SealError):
                self.make()                                  # stdout.txt is 5000 bytes
            self.s.MAX_LOGICAL, self.s.MAX_TOTAL = original[0], 5010
            with self.assertRaises(self.s.SealError):
                self.make()                                  # all files together exceed the total
        finally:
            self.s.MAX_LOGICAL, self.s.MAX_TOTAL = original

    def test_store_must_be_private(self):
        os.chmod(self.store, 0o755)
        with self.assertRaises(self.s.SealError):
            self.make()

    def test_source_never_contains_a_contiguous_pem_private_key_header(self):
        # Production review evidence refuses any file containing this marker; detection must be assembled from parts.
        marker = b'PRIVATE ' + b'KEY-----'
        self.assertNotIn(marker, Path(self.s.__file__).read_bytes())

    def test_seal_module_is_stdlib_only(self):
        names = set()
        for node in ast.walk(ast.parse(Path(self.s.__file__).read_text(encoding='utf-8'))):
            if isinstance(node, ast.Import):
                names.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split('.')[0])
        names -= {'__future__', 'supervise'}
        self.assertTrue(names <= STDLIB, sorted(names - STDLIB))


if __name__ == '__main__':
    unittest.main()
