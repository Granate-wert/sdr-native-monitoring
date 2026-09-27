"""Pure/native-factory optional DSP protocol. No SDK or physical I/O."""

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
from sdr_monitor.services.hackrf_activation_preflight import HackrfActivationPreflightService
from sdr_monitor.services.hackrf_dsp_contract import hackrf_dsp_profile_contract_version
from sdr_monitor.services.hackrf_live_admission import admit_hackrf_live
from sdr_monitor.services.hackrf_native_factory import HackrfNativeFactoryError, HackrfNativeRuntimeFactory
from sdr_monitor.services.source_capability_providers import _qualified_hackrf_sdk_directory
from tests.test_app06_frozen_shared_runtime import native_fixture, package_fixture
from tests.test_app06_hackrf_burst_budget import request
from tests.test_r11m_hackrf_live_admission import _observed
from tests.test_r11n_hackrf_native_factory import _IdentityPort, _NativeFactory


def permit(profile):
    snapshot, identity, _ = _observed()
    admission = admit_hackrf_live(snapshot, identity, profile)
    return HackrfActivationPreflightService(_IdentityPort).verify(admission.plan).permit


class HackrfDetectorProfileTests(unittest.TestCase):
    def test_group_bounds_reject_bool_float_and_outside_range(self):
        for value in (True, False, 0, 257, -1, 1.0, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                request(averaging_frames=value)
        self.assertEqual(request(averaging_frames=256).averaging_frames, 256)

    def test_profile_replacement_preserves_queues_and_recalculates_only_auto_policy(self):
        base = request(averaging_frames=8, slot_count=12, ready_capacity=7, presentation_capacity=9)
        updated = replace(base, fft_size=1024, hop_size=256, window="nuttall", detector="peak")
        self.assertEqual((updated.averaging_frames, updated.slot_count, updated.ready_capacity,
                          updated.presentation_capacity), (8, 12, 7, 9))
        self.assertIsNone(updated.dsp_output_capacity)
        self.assertEqual(updated.resolved_dsp_output_capacity, 512)
        explicit = replace(updated, dsp_output_capacity=3)
        self.assertEqual(replace(explicit, hop_size=512).resolved_dsp_output_capacity, 3)

    def test_watchdog_accounts_for_group_without_claiming_observed_period(self):
        self.assertEqual(request().spectrum_stall_timeout_s, 2)
        long = request(fft_size=262144, hop_size=262144, averaging_frames=256)
        self.assertAlmostEqual(long.spectrum_stall_timeout_s, 3 * 262144 * 256 / 8e6)
        self.assertLessEqual(replace(long, sample_rate_hz=1_750_000, baseband_filter_hz=1_750_000).spectrum_stall_timeout_s, 120)

    def test_default_factory2_keeps_original_keyword_surface(self):
        native = _NativeFactory()
        HackrfNativeRuntimeFactory(lambda: native).create(permit(request()))
        self.assertNotIn("averaging_frames", native.calls[0])
        self.assertFalse(native.calls[0]["bias_tee_enabled"])
        self.assertFalse(native.calls[0]["rf_amplifier_enabled"])

    def test_extension_refuses_before_consuming_permit_and_then_uses_exact_group_once(self):
        for version in (None, True, False, 0, 2, "1"):
            with self.subTest(version=version):
                native = _NativeFactory()
                native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = version
                current = permit(request(averaging_frames=8))
                factory = HackrfNativeRuntimeFactory(lambda native=native: native)
                with self.assertRaises(HackrfNativeFactoryError):
                    factory.create(current)
                self.assertEqual(native.calls, [])
                native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = 1
                factory.create(current)
                self.assertEqual(native.calls[0]["averaging_frames"], 8)
                self.assertEqual(native.calls[0]["expected_serial_words"], (0, 0, 0x010961DC, 0x2B78454F))
                with self.assertRaises(HackrfNativeFactoryError):
                    factory.create(current)
                self.assertEqual(len(native.calls), 1)

    def test_each_window_detector_and_hop_reaches_native_without_substitution(self):
        windows = ("rectangular", "hann", "blackman_harris_4term", "flat_top", "nuttall", "kaiser")
        detectors = ("sample", "peak", "negative_peak", "rms", "average_power")
        for window in windows:
            for detector in detectors:
                with self.subTest(window=window, detector=detector):
                    native = _NativeFactory()
                    native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = 1
                    native.WindowType = SimpleNamespace(**{token.upper(): token for token in windows})
                    native.DetectorType = SimpleNamespace(**{token.upper(): token for token in detectors})
                    profile = request(window=window, detector=detector, hop_size=1024, averaging_frames=4)
                    HackrfNativeRuntimeFactory(lambda native=native: native).create(permit(profile))
                    call = native.calls[0]
                    self.assertEqual((call["window"], call["detector"], call["hop_size"], call["averaging_frames"]),
                                     (window, detector, 1024, 4))

    def test_optional_enum_absence_does_not_consume_permit(self):
        native = _NativeFactory()
        saved = native.WindowType
        native.WindowType = object()
        current = permit(request())
        factory = HackrfNativeRuntimeFactory(lambda: native)
        with self.assertRaises(HackrfNativeFactoryError):
            factory.create(current)
        self.assertEqual(native.calls, [])
        native.WindowType = saved
        factory.create(current)
        self.assertEqual(len(native.calls), 1)

    def test_native_manifest_extension_exact_agreement_or_both_absent(self):
        native = _NativeFactory()
        manifest = {"hackrf_official_compiled": True, "hackrf_factory_contract_version": 2}
        self.assertIsNone(hackrf_dsp_profile_contract_version(native, manifest))
        validate_hackrf_factory(native, manifest)
        for observed, declared in ((1, None), (None, 1), (None, None), (True, 1), (1, True), (2, 2), (0, 0), (1, "1")):
            with self.subTest(observed=observed, declared=declared):
                native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = observed
                mutated = {**manifest, "hackrf_dsp_profile_contract_version": declared}
                with self.assertRaises(ValueError):
                    hackrf_dsp_profile_contract_version(native, mutated)
                with self.assertRaises(ContractSurfaceError):
                    validate_hackrf_factory(native, mutated)
        native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = 1
        self.assertEqual(hackrf_dsp_profile_contract_version(native,
            {**manifest, "hackrf_dsp_profile_contract_version": 1}), 1)

    def test_catalog_optional_native_manifest_mismatch_refuses_static_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, _, manifest = package_fixture(Path(tmp).resolve())
            native = native_fixture(module)
            self.assertEqual(_qualified_hackrf_sdk_directory(native), module.parent)
            for observed, declared in ((1, None), (None, 1), (True, 1), (1, True), (2, 2)):
                with self.subTest(observed=observed, declared=declared):
                    native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = observed
                    (module.parent / "native_build_manifest.json").write_text(json.dumps({**manifest,
                        "hackrf_dsp_profile_contract_version": declared}), encoding="utf-8")
                    self.assertIsNone(_qualified_hackrf_sdk_directory(native))
            native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = 1
            (module.parent / "native_build_manifest.json").write_text(json.dumps({**manifest,
                "hackrf_dsp_profile_contract_version": 1}), encoding="utf-8")
            self.assertEqual(_qualified_hackrf_sdk_directory(native), module.parent)
            native.create_hackrf_runtime_dsp_control.assert_not_called()
            native.scan_pluto_contexts.assert_not_called()

    def test_frozen_optional_contract_before_library_metadata_no_sdk_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, _, manifest = package_fixture(Path(tmp).resolve())
            native = native_fixture(module)
            with (patch.object(sys, "frozen", True, create=True),
                  patch.object(runtime, "hold_package_libiio_metadata", return_value=nullcontext()),
                  patch.object(runtime, "windows_loaded_module_paths",
                      return_value=tuple(module.parent / name for name in runtime.SHARED_COMPONENTS))):
                native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = 1
                with self.assertRaises(ValueError):
                    runtime.packaged_shared_runtime_verdict(native)
                native.pluto_runtime_info.assert_not_called()
                (module.parent / "native_build_manifest.json").write_text(json.dumps({**manifest,
                    "hackrf_dsp_profile_contract_version": 1}), encoding="utf-8")
                verdict = runtime.packaged_shared_runtime_verdict(native)
                self.assertEqual(verdict["hackrf_dsp_profile_contract_version"], 1)
                self.assertFalse(verdict["factory_constructed"])
                self.assertTrue(verdict["metadata_hold_released"])
                native.create_hackrf_runtime_dsp_control.assert_not_called()
                native.hackrf_init.assert_not_called()
                native.scan_pluto_contexts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
