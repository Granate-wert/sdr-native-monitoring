"""Native readonly ownership proof for the bounded persistence exporter."""

import os
import gc
import unittest
import weakref

import numpy as np

from tests.test_app07_protected_waterfall_accounting import (
    explicit_native_support,
    restore_explicit_native_support,
)
from sdr_monitor.ui.v2.spectrum.retained_bytes import retained_arrays


class PersistenceStorageBinding178Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("SDR_APP07_READY_NATIVE"):
            raise unittest.SkipTest("requires the explicitly selected clean APP07-178 native")
        cls.native, _ = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls):
        restore_explicit_native_support(cls)

    def test_snapshot_exporters_are_readonly_and_match_only_the_exact_owner(self):
        density = np.arange(12, dtype=np.float32).reshape(3, 4)
        axes = np.arange(4, dtype=np.float64) + 100.0
        snapshot = self.native._make_test_immutable_persistence_snapshot(density, axes)
        storage = snapshot.immutable_storage
        owned_density, owned_axes = storage.density(), storage.frequencies()
        self.assertTrue(storage.matches(owned_density, owned_axes))
        self.assertFalse(owned_density.flags.writeable)
        self.assertFalse(owned_axes.flags.writeable)
        with self.assertRaises(ValueError):
            owned_density.setflags(write=True)
        with self.assertRaises(ValueError):
            owned_axes.setflags(write=True)
        self.assertTrue(memoryview(owned_density).readonly)
        self.assertTrue(memoryview(owned_axes).readonly)
        self.assertTrue(storage.matches(owned_density.view(), owned_axes.view()))
        self.assertFalse(storage.matches(owned_density.copy(), owned_axes))
        self.assertFalse(storage.matches(owned_density[:, 1:], owned_axes))
        self.assertFalse(storage.matches(owned_density[:, ::-1], owned_axes))
        self.assertFalse(storage.matches(owned_density.reshape(4, 3), owned_axes))
        self.assertFalse(storage.matches(owned_density, owned_axes[1:]))
        self.assertFalse(storage.matches(owned_density.view(np.uint8), owned_axes))
        self.assertFalse(storage.matches(memoryview(owned_density), owned_axes))
        self.assertFalse(snapshot.density.flags.writeable)
        self.assertFalse(snapshot.frequencies_hz.flags.writeable)

    def test_factory_refuses_wrong_dtype_shape_and_noncontiguous_inputs(self):
        axes = np.arange(4, dtype=np.float64)
        with self.assertRaises(ValueError):
            self.native._make_test_immutable_persistence_snapshot(np.ones((2, 4), np.float64), axes)
        with self.assertRaises(ValueError):
            self.native._make_test_immutable_persistence_snapshot(np.ones((2, 3), np.float32), axes)
        with self.assertRaises(ValueError):
            self.native._make_test_immutable_persistence_snapshot(np.ones((2, 8), np.float32)[:, ::2], axes)

    def test_frequency_exporter_lifetime_and_exposed_root_accounting_are_axis_only(self):
        snapshot = self.native._make_test_immutable_persistence_snapshot(
            np.ones((1024, 4096), dtype=np.float32), np.arange(4096, dtype=np.float64))
        storage = snapshot.immutable_storage
        density = storage.density()
        axes = storage.frequencies()

        def exporter(array):
            base = array
            while isinstance(base, np.ndarray):
                base = base.base
            while isinstance(base, memoryview):
                base = base.obj
            return base

        float_exporter = exporter(density)
        axis_exporter = exporter(axes)
        self.assertIs(type(float_exporter), self.native._PersistenceFloatBuffer)
        self.assertIs(type(axis_exporter), self.native._PersistenceDoubleBuffer)
        density_ref = weakref.ref(density)
        axes_ref = weakref.ref(axes)
        try:
            storage_ref = weakref.ref(storage)
        except TypeError:
            storage_ref = None
        try:
            float_exporter_ref = weakref.ref(float_exporter)
        except TypeError:
            float_exporter_ref = None
        try:
            axis_exporter_ref = weakref.ref(axis_exporter)
        except TypeError:
            axis_exporter_ref = None

        del float_exporter, axis_exporter, density, snapshot, storage
        gc.collect()

        self.assertIsNone(density_ref())
        self.assertIsNotNone(axes_ref())
        if float_exporter_ref is not None:
            self.assertIsNone(float_exporter_ref())
        if axis_exporter_ref is not None:
            self.assertIsNotNone(axis_exporter_ref())
        if storage_ref is not None:
            self.assertIsNone(storage_ref())
        self.assertEqual(sum(retained_arrays(axes_ref()).values()), axes_ref().nbytes)
