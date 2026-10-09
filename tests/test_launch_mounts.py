# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Supervisor-owned acceptance oracle for S4-0: LaunchSpec carries the reviewer harness runtime/auth mounts.

Scope revision (Chief, 2026-10-08, recorded in OPEN-DESIGN-ITEMS.md item 2): S0 proved a reviewer harness needs its
own runtime and auth mounted into the review sandbox; only the backend knows them. The accepted S1 interface had no way
to express them. This unit adds one trailing field with a safe default; nothing else in the interface changes.
"""
import dataclasses
import importlib
import unittest


class LaunchMountsTests(unittest.TestCase):
    def setUp(self):
        self.b = importlib.import_module('supervise.backend')

    def test_launchspec_gains_one_trailing_mounts_field_defaulting_to_empty(self):
        names = [f.name for f in dataclasses.fields(self.b.LaunchSpec)]
        self.assertEqual(names, ['argv', 'env', 'requires_review_sandbox', 'stdout_format', 'mounts'])
        spec = self.b.LaunchSpec(argv=('/usr/bin/tool',), env={}, requires_review_sandbox=False, stdout_format='x')
        self.assertEqual(spec.mounts, ())
        self.assertTrue(self.b.LaunchSpec.__dataclass_params__.frozen)

    def test_mount_records_are_frozen_triples(self):
        mount_type = getattr(self.b, 'Mount', None)
        self.assertIsNotNone(mount_type, 'supervise.backend.Mount is missing')
        self.assertTrue(dataclasses.is_dataclass(mount_type) and mount_type.__dataclass_params__.frozen)
        self.assertEqual([f.name for f in dataclasses.fields(self.b.Mount)], ['source', 'target', 'writable'])
        mount = self.b.Mount(source='/opt/tool', target='/opt/tool', writable=False)
        spec = self.b.LaunchSpec(argv=('/opt/tool/bin/x',), env={}, requires_review_sandbox=True, stdout_format='x',
                                 mounts=(mount,))
        self.assertEqual(spec.mounts[0].target, '/opt/tool')
        with self.assertRaises(dataclasses.FrozenInstanceError):
            mount.writable = True

    def test_docstring_states_the_kernel_validates_mounts(self):
        doc = (self.b.__doc__ or '') + (self.b.LaunchSpec.__doc__ or '')
        self.assertIn('mount', doc.lower())
        self.assertIn('kernel', doc.lower())


if __name__ == '__main__':
    unittest.main()
