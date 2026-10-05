"""Optional density admission/factory/coordinator contract; no SDK or RX."""

import json
import sys
import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.preflight_sdr_native_build import ContractSurfaceError, validate_hackrf_factory
from sdr_monitor import frozen_shared_runtime as runtime
from sdr_monitor.services.hackrf_dsp_contract import hackrf_persistence_contract_version
from sdr_monitor.services.hackrf_native_factory import HackrfNativeFactoryError, HackrfNativeRuntimeFactory
from sdr_monitor.services.hackrf_product_live import HackrfProductLiveCoordinator
from sdr_monitor.services.source_capability_providers import _qualified_hackrf_sdk_directory
from tests.test_app06_frozen_shared_runtime import native_fixture, package_fixture
from tests.test_app06_hackrf_burst_budget import request
from tests.test_app06_hackrf_dsp_profile import permit
from tests.test_r11n_hackrf_native_factory import _NativeFactory


def density_request(**kwargs):
    return request(persistence_enabled=True, persistence_mode="exponential-decay", **kwargs)


class HackrfPersistenceContractTests(unittest.TestCase):
    def test_request_modes_finite_dimensions_and_boolean_are_strict(self):
        cases = (
            {"persistence_enabled": 1}, {"persistence_mode": "unknown"},
            {"persistence_enabled": True, "persistence_mode": "disabled"},
            {"persistence_power_min_db": float("nan")}, {"persistence_power_max_db": float("inf")},
            {"persistence_power_min_db": True}, {"persistence_power_bins": True},
            {"persistence_power_bins": 15}, {"persistence_power_bins": 4097},
            {"persistence_window_frames": 0}, {"persistence_window_frames": 1_000_001},
            {"persistence_half_life_s": 0}, {"persistence_half_life_s": float("inf")},
            {"persistence_snapshot_rate_hz": 9}, {"persistence_snapshot_rate_hz": 31})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(request(), **changes)
        self.assertEqual(density_request().persistence_snapshot_rate_hz, 15)

    def test_budget_matches_native_policy_and_never_silently_clamps(self):
        profile = density_request()
        self.assertEqual(profile.persistence_allocation_bytes, profile.fft_size * (256 * 4 * 5 + 8 * 4) + 1024)
        exact = replace(profile, persistence_mode="rolling-exact", persistence_window_frames=123)
        self.assertEqual(exact.persistence_allocation_bytes - profile.persistence_allocation_bytes, 123 * profile.fft_size * 4)
        with self.assertRaisesRegex(ValueError, "256 MiB"):
            density_request(fft_size=262144, hop_size=262144, persistence_power_bins=256)
        self.assertEqual(request().persistence_allocation_bytes, 0)

    def test_old_disabled_factory_omits_optional_keyword(self):
        native = _NativeFactory()
        HackrfNativeRuntimeFactory(lambda: native).create(permit(request()))
        self.assertNotIn("persistence", native.calls[0])

    def test_native_extension_and_constructor_refuse_before_permit_consume(self):
        for version in (None, True, 0, 2, "1"):
            with self.subTest(version=version):
                native = _NativeFactory()
                native.HACKRF_PERSISTENCE_CONTRACT_VERSION = version
                native.PersistenceConfig = lambda *args: args
                native.PersistenceMode = SimpleNamespace(EXPONENTIAL_DECAY="decay", ROLLING_EXACT="rolling")
                issued = permit(density_request())
                factory = HackrfNativeRuntimeFactory(lambda native=native: native)
                with self.assertRaises(HackrfNativeFactoryError):
                    factory.create(issued)
                self.assertEqual(native.calls, [])
                native.HACKRF_PERSISTENCE_CONTRACT_VERSION = 1
                factory.create(issued)
                self.assertEqual(native.calls[0]["persistence"], (True, "decay", 500, 1., -140., 20., 256, 15., 5))
                with self.assertRaises(HackrfNativeFactoryError):
                    factory.create(issued)
        native = _NativeFactory()
        native.HACKRF_PERSISTENCE_CONTRACT_VERSION = 1
        issued = permit(density_request())
        factory = HackrfNativeRuntimeFactory(lambda: native)
        with self.assertRaises(HackrfNativeFactoryError):
            factory.create(issued)
        self.assertEqual(native.calls, [])

    def test_optional_manifest_and_native_must_agree_even_when_request_disabled(self):
        native = _NativeFactory()
        manifest = {"hackrf_official_compiled": True, "hackrf_factory_contract_version": 2}
        self.assertIsNone(hackrf_persistence_contract_version(native, manifest))
        for observed, declared in ((True, 1), (1, True), (1, None), (None, 1), (2, 2)):
            with self.subTest(observed=observed, declared=declared):
                native.HACKRF_PERSISTENCE_CONTRACT_VERSION = observed
                current = {**manifest, "hackrf_persistence_contract_version": declared}
                with self.assertRaises(ValueError):
                    hackrf_persistence_contract_version(native, current)
                with self.assertRaises(ContractSurfaceError):
                    validate_hackrf_factory(native, current)
        native.HACKRF_PERSISTENCE_CONTRACT_VERSION = 1
        self.assertEqual(hackrf_persistence_contract_version(native,
            {**manifest, "hackrf_persistence_contract_version": 1}), 1)

    def test_coordinator_optional_poll_is_bounded_redacted_and_does_not_release_owner(self):
        coordinator = HackrfProductLiveCoordinator(object_factory := SimpleNamespace(create=lambda _: None))
        self.assertIs(coordinator._factory, object_factory)
        for count in (0, 3, True, 1.5):
            with self.assertRaises(ValueError):
                coordinator.poll_persistence_snapshots(count)
        from sdr_monitor.services.hackrf_product_live import HackrfProductLiveState
        control = SimpleNamespace(poll_persistence_snapshots=lambda _: iter(range(1000000)))
        coordinator._control, coordinator._state = control, HackrfProductLiveState.ACTIVE
        with self.assertRaisesRegex(RuntimeError, "bound"):
            coordinator.poll_persistence_snapshots(2)
        self.assertIs(coordinator._control, control)
        self.assertTrue(coordinator.snapshot().active)
        def fail(_):
            raise RuntimeError("PRIVATE SDK detail")
        control.poll_persistence_snapshots = fail
        with self.assertRaises(RuntimeError) as caught:
            coordinator.poll_persistence_snapshots(1)
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertTrue(coordinator.snapshot().active)

    def test_catalog_persistence_mismatch_refuses_static_sdk_without_io(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, _, manifest = package_fixture(Path(tmp).resolve())
            native = native_fixture(module)
            self.assertEqual(_qualified_hackrf_sdk_directory(native), module.parent)
            for observed, declared in ((1, None), (None, 1), (True, 1), (1, True), (2, 2)):
                with self.subTest(observed=observed, declared=declared):
                    native.HACKRF_PERSISTENCE_CONTRACT_VERSION = observed
                    (module.parent / "native_build_manifest.json").write_text(json.dumps({**manifest,
                        "hackrf_persistence_contract_version": declared}), encoding="utf-8")
                    self.assertIsNone(_qualified_hackrf_sdk_directory(native))
            native.HACKRF_PERSISTENCE_CONTRACT_VERSION = 1
            (module.parent / "native_build_manifest.json").write_text(json.dumps({**manifest,
                "hackrf_persistence_contract_version": 1}), encoding="utf-8")
            self.assertEqual(_qualified_hackrf_sdk_directory(native), module.parent)
            native.create_hackrf_runtime_dsp_control.assert_not_called()
            native.scan_pluto_contexts.assert_not_called()

    def test_frozen_persistence_agreement_precedes_metadata_and_device_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, _, manifest = package_fixture(Path(tmp).resolve())
            native = native_fixture(module)
            with (patch.object(sys, "frozen", True, create=True),
                  patch.object(runtime, "hold_package_libiio_metadata", return_value=nullcontext()),
                  patch.object(runtime, "windows_loaded_module_paths",
                      return_value=tuple(module.parent / name for name in runtime.SHARED_COMPONENTS))):
                native.HACKRF_PERSISTENCE_CONTRACT_VERSION = 1
                with self.assertRaises(ValueError):
                    runtime.packaged_shared_runtime_verdict(native)
                native.pluto_runtime_info.assert_not_called()
                (module.parent / "native_build_manifest.json").write_text(json.dumps({**manifest,
                    "hackrf_persistence_contract_version": 1}), encoding="utf-8")
                verdict = runtime.packaged_shared_runtime_verdict(native)
                self.assertEqual(verdict["hackrf_persistence_contract_version"], 1)
                self.assertFalse(verdict["factory_constructed"])
                self.assertTrue(verdict["metadata_hold_released"])
                native.create_hackrf_runtime_dsp_control.assert_not_called()
                native.hackrf_init.assert_not_called()
                native.scan_pluto_contexts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
