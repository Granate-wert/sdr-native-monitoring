"""Opt-in compiled Python binding contract against the test-only IIO DLL."""

from __future__ import annotations

import ctypes
import os
import unittest
from pathlib import Path

from sdr_monitor.services.ad936x_identity_admission import create_identity_bound_owner
from sdr_monitor.services.native_live import NativeLiveSessionService


class CompiledPlutoIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = os.environ.get("SDR_APP06_IDENTITY_MOCK_DLL")
        if os.name != "nt" or not path:
            raise unittest.SkipTest("requires explicitly supplied native test-only IIO DLL")
        mock_path = Path(path).resolve(strict=True)
        # Validate the test-only counter export before permitting any owner.
        cls.mock = ctypes.CDLL(str(mock_path))
        for name in ("mock_iio_reset_context_counts", "mock_iio_created_contexts",
                     "mock_iio_destroyed_contexts", "mock_iio_live_contexts",
                     "mock_iio_rf_mutation_calls", "mock_iio_created_buffers"):
            function = getattr(cls.mock, name)
            function.argtypes = []
            function.restype = None if name == "mock_iio_reset_context_counts" else ctypes.c_int
        cls.previous_path = os.environ.get("LIBIIO_DLL_PATH")
        cls.addClassCleanup(cls._restore_path)
        os.environ["LIBIIO_DLL_PATH"] = str(mock_path)
        from sdr_monitor import _sdr_native

        cls.native = _sdr_native
        if getattr(cls.native, "PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION", None) != 1:
            raise unittest.SkipTest("requires staged receiver-owned identity admission runtime")

    @classmethod
    def _restore_path(cls) -> None:
        if cls.previous_path is None:
            os.environ.pop("LIBIIO_DLL_PATH", None)
        else:
            os.environ["LIBIIO_DLL_PATH"] = cls.previous_path

    def test_keyword_identity_admission_and_one_context_cleanup_for_all_owners(self) -> None:
        for factory_name in ("PlutoDevice", "PlutoFixedBandEngine", "NativeContinuousSweepCoordinator"):
            with self.subTest(factory_name=factory_name):
                self.mock.mock_iio_reset_context_counts()
                before_mutations = self.mock.mock_iio_rf_mutation_calls()
                before_buffers = self.mock.mock_iio_created_buffers()
                owner = create_identity_bound_owner(self.native, factory_name, "usb:mock", 3000,
                                                    expected_serial=" MOCK ")
                self.assertEqual(self.mock.mock_iio_created_contexts(), 1)
                self.assertEqual(self.mock.mock_iio_live_contexts(), 1)
                if factory_name == "PlutoDevice":
                    self.assertEqual(self.native.PLUTO_OBSERVATION_PROTOCOL_VERSION, 1)
                    self.assertEqual(owner.receiver_topology().context.serial, owner.probe().serial)
                    self.assertEqual(self.mock.mock_iio_created_contexts(), 1)
                owner.disconnect()
                self.assertEqual(self.mock.mock_iio_destroyed_contexts(), 1)
                self.assertEqual(self.mock.mock_iio_live_contexts(), 0)
                with self.assertRaisesRegex(RuntimeError, "identity was not confirmed"):
                    create_identity_bound_owner(self.native, factory_name, "usb:mock", 3000,
                                                expected_serial="PRIVATE-DIFFERENT-SERIAL")
                self.assertEqual(self.mock.mock_iio_created_contexts(), 2)
                self.assertEqual(self.mock.mock_iio_destroyed_contexts(), 2)
                self.assertEqual(self.mock.mock_iio_live_contexts(), 0)
                self.assertEqual(self.mock.mock_iio_rf_mutation_calls(), before_mutations)
                self.assertEqual(self.mock.mock_iio_created_buffers(), before_buffers)

    def test_compiled_discovery_is_one_context_and_publishes_existing_inventory_after_close(self) -> None:
        self.mock.mock_iio_reset_context_counts()
        before_mutations = self.mock.mock_iio_rf_mutation_calls()
        before_buffers = self.mock.mock_iio_created_buffers()
        service = NativeLiveSessionService(self.native)
        devices = service.discover_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(self.mock.mock_iio_created_contexts(), 1)
        self.assertEqual(self.mock.mock_iio_destroyed_contexts(), 1)
        self.assertEqual(self.mock.mock_iio_live_contexts(), 0)
        inventory = service.capability_inventory()
        self.assertEqual(len(inventory.snapshots), 1)
        self.assertIs(inventory.snapshots[0], devices[0].capability_snapshot)
        self.assertEqual(inventory.snapshots[0].rx_channel_count, 1)
        self.assertEqual(self.mock.mock_iio_created_contexts(), 1)
        self.assertEqual(self.mock.mock_iio_rf_mutation_calls(), before_mutations)
        self.assertEqual(self.mock.mock_iio_created_buffers(), before_buffers)
        service.close_live()


if __name__ == "__main__":
    unittest.main()
