"""Native factory-only contracts; synthetic RTL C ABI, never physical SDR.

The mock DLL must be explicitly supplied and export its synthetic counter API.
These tests do not claim Python owner-context or UI processing integration.
"""

import ctypes
import hashlib
import importlib
import os
from pathlib import Path
import time
import unittest

import numpy as np

from sdr_monitor.domain.processing_policy import DC_REMOVED_MASK, HostDcMode, SdrProcessingPolicyV1
from sdr_monitor.services.native_spectrum_provenance import native_spectrum_provenance


class NativeFamilyProcessingFactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.native = importlib.import_module("sdr_monitor._sdr_native")
        except ImportError as error:
            raise unittest.SkipTest("optional native module absent") from error
        if not all(hasattr(cls.native, name) for name in (
                "hackrf_processing_factory_contract", "rtl_processing_factory_contract")):
            raise unittest.SkipTest("legacy native has no family processing factory protocol")

    def test_separate_exact_factory_scope_is_not_full_owner_or_spur_support(self):
        for name, family in (("hackrf_processing_factory_contract", "hackrf"),
                             ("rtl_processing_factory_contract", "rtl-sdr")):
            with self.subTest(family=family):
                actual = getattr(self.native, name)()
                expected = {"schema_version": 1, "family": family,
                    "scope": "native_rtbw_factory_only", "dc_modes": ("off", "block_mean_v1"),
                    "argument": "dc_removal_block_mean", "default_off": True, "full_owner_context": False}
                self.assertEqual(actual, expected)
                for key, value in expected.items():
                    self.assertIs(type(actual[key]), type(value))
                self.assertEqual(self.native.CONTRACT_SCHEMA_VERSION, 5)

    def hackrf_arguments(self):
        n = self.native
        # Invalid Fs is deliberately rejected by pure native configuration
        # BEFORE the official SDK/device constructor can run.
        return dict(center_frequency_hz=100e6, sample_rate_hz=0.0,
            baseband_filter_hz=8_000_000, lna_gain_db=16, vga_gain_db=20,
            rf_amplifier_enabled=False, bias_tee_enabled=False,
            fft_size=4096, hop_size=2048, window=n.WindowType.HANN,
            detector=n.DetectorType.SAMPLE, slot_count=32, ready_capacity=24,
            dsp_output_capacity=8, presentation_capacity=4, configuration_generation=1,
            source_id="native.factory.test", expected_serial_words=(0, 0, 0, 0))

    def test_hackrf_strict_bool_and_inert_invalid_profile(self):
        factory = self.native.create_hackrf_runtime_dsp_control
        for malformed in (None, 0, 1, 1.0, "true", [True]):
            with self.subTest(value=malformed), self.assertRaises(TypeError):
                factory(**self.hackrf_arguments(), dc_removal_block_mean=malformed)
        for arguments in ({}, {"dc_removal_block_mean": False}, {"dc_removal_block_mean": True}):
            with self.subTest(arguments=arguments), self.assertRaises(self.native.ConfigurationError):
                factory(**self.hackrf_arguments(), **arguments)

    def test_rtl_strict_bool_refuses_before_loading_any_runtime(self):
        n = self.native
        # A Python text file, not an SDK library: a coercion must never reach
        # runtime loading. Construction of these values has no SDK effects.
        file = Path(__file__).resolve()
        runtime = n.RtlExternalRuntime(n.RtlExternalFile(str(file),
            hashlib.sha256(file.read_bytes()).hexdigest()), [])
        for malformed in (None, 0, 1, 1.0, "true", [True]):
            with self.subTest(value=malformed), self.assertRaises(TypeError):
                n.create_rtl_runtime_control(runtime, 100_000_000, 2_400_000,
                    4096, 2048, 8, 6, 10, 4, 1, "native.factory.test", n.DetectorType.SAMPLE,
                    dc_removal_block_mean=malformed)

    def test_actual_rtl_mock_cabi_default_off_explicit_off_blockmean_and_cleanup(self):
        location = os.environ.get("SDR_APP07_TEST_MOCK_RTL")
        if not location:
            self.skipTest("explicit synthetic RTL C ABI DLL required, no SDK fallback")
        mock = Path(location).resolve(strict=True)
        # Export qualification distinguishes a synthetic fixture from a vendor
        # runtime. Never enumerate/open a physical RTL to find this fixture.
        sdk = ctypes.CDLL(str(mock))
        sdk.mock_rtl_set_scenario.argtypes = (ctypes.c_int,)
        sdk.mock_rtl_reset_counters.argtypes = ()
        sdk.mock_rtl_counter.argtypes = (ctypes.c_int,)
        sdk.mock_rtl_counter.restype = ctypes.c_int
        sdk.mock_rtl_set_scenario(3)  # second descriptor open would refuse
        n = self.native
        runtime = n.RtlExternalRuntime(n.RtlExternalFile(str(mock),
            hashlib.sha256(mock.read_bytes()).hexdigest()), [])
        saved = []
        for selected in (None, False, True):
            sdk.mock_rtl_reset_counters()
            options = {} if selected is None else {"dc_removal_block_mean": selected}
            route = n.RtlSessionRoute("Mock", "RTL tuner", "00000001", 5, 1)
            control = n.create_rtl_runtime_control(runtime, 100_000_000, 2_400_000,
                4096, 2048, 8, 6, 10, 4, 7, "native.factory.test", n.DetectorType.SAMPLE,
                session_route=route, analytical_event_capacity=128, **options)
            try:
                deadline = time.monotonic() + 2.0
                while control.metrics().dsp.fft_frames_computed != 11:
                    if time.monotonic() >= deadline:
                        self.fail("synthetic RTL factory did not complete its bounded two input blocks")
                    time.sleep(0.002)
                frame = control.drain_latest_spectrum_frame().frame
                self.assertIsNotNone(frame)
                provenance = native_spectrum_provenance(frame)
                recipe = provenance.processing_recipe
                self.assertIsNotNone(recipe)
                mode = HostDcMode.BLOCK_MEAN if selected else HostDcMode.OFF
                self.assertIs(recipe.dc_mode, mode)
                self.assertEqual(recipe.policy_digest, SdrProcessingPolicyV1(dc_mode=mode).digest)
                self.assertEqual(recipe.whole_frame_modified, bool(selected))
                self.assertEqual(bool(frame.quality_flags & DC_REMOVED_MASK), bool(selected))
                self.assertEqual((frame.center_frequency_hz, frame.sample_rate_hz,
                    frame.fft_size, frame.hop_size, frame.config_generation), (100e6, 2.4e6, 4096, 2048, 7))
                self.assertEqual(frame.source.backend_id, "native.librtlsdr.unbundled.cpu.v1")
                self.assertEqual(control.metrics().dsp.fft_frames_dropped, 0)
                self.assertEqual(control.metrics().samples_admitted, 24576)
                actual = control.readback()
                self.assertEqual((actual.actual_center_hz, actual.actual_sample_rate_hz), (100e6, 2.4e6))
                saved.append((np.array(frame.values, copy=True), frame.quality_flags))
            finally:
                self.assertTrue(control.stop(2000).complete())
                self.assertFalse(control.cleanup_required())
            self.assertEqual((sdk.mock_rtl_counter(0), sdk.mock_rtl_counter(1), sdk.mock_rtl_counter(2)), (1, 1, 1))
            self.assertFalse(n.rtl_process_is_quarantined())
        np.testing.assert_array_equal(saved[0][0], saved[1][0])
        self.assertEqual(saved[0][1], saved[1][1])
        # Independent DFT: removing the unweighted mean before symmetric Hann
        # does not imply a zero window-weighted DC. Use the same CU8 fixture,
        # but no native window, FFT or DSP helpers to derive either bin.
        size = 4096
        indices = np.arange(size)
        samples = np.where(indices % 8 == 0, 127 / 128, 0.0)
        window = np.hanning(size)
        for selected, (values, _) in zip((False, False, True), saved, strict=True):
            weighted = (samples - (samples.mean() if selected else 0)) * window
            for bin_index in (0, 512):
                reference = np.sum(weighted * np.exp(-2j * np.pi * bin_index * indices / size))
                expected_db = 20 * np.log10(abs(reference) / window.sum())
                self.assertAlmostEqual(float(values[2048 + bin_index]), expected_db, delta=0.0001)
        self.assertAlmostEqual(float(saved[0][0][2560]), float(saved[2][0][2560]), places=4)
        self.assertEqual(saved[2][1], saved[0][1] | DC_REMOVED_MASK)


if __name__ == "__main__":
    unittest.main()
