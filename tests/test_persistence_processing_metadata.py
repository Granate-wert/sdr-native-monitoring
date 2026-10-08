"""Native density metadata, not RF/owner authority. Explicit synthetic fixtures."""

from dataclasses import replace
import importlib
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.layer_ready import DENSITY_PROCESSING_METADATA_RESERVATION_BYTES
from sdr_monitor.domain.processing_policy import HostDcMode, SdrProcessingPolicyV1
from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.native_spectrum_provenance import native_persistence_provenance
from tests.test_source_processing import composition
from tests.test_processing_recipe_bridge import recipe
from tests.test_s15_live_rx_bridge import _FakeNative


def metadata(mode=HostDcMode.OFF):
    return SimpleNamespace(window="rectangular", detector="sample", precision_mode="reference_f64",
        fft_bin_width_hz=31250., enbw_hz=31250., nominal_rbw_hz=31250., averaging_frames=1,
        calibration_status="uncalibrated", calibration_profile_id="", estimated_uncertainty_db=float("nan"),
        window_normalization_version="power-norm-v1", dsp_processing_recipe=recipe(mode))


def density(mode=HostDcMode.OFF, source="mock-density", fft=256):
    return SimpleNamespace(source_id=source, config_generation=1, unit="dBFS/bin",
        update_sequence=1, source_frame_sequence=0, timestamp_ns=1, first_sample_index=0,
        quality_flags=512 if mode is HostDcMode.BLOCK_MEAN else 0, power_min_db=-140., power_max_db=20.,
        power_bins=16, frequency_bins=fft, processed_frames=1, exponential_decay=True,
        frequencies_hz=np.arange(fft, dtype=np.float64), density=np.zeros((16, fft), dtype=np.float32),
        probability_scale=1., count_scale=1., processing_metadata=metadata(mode))


class PersistenceProcessingMapperTests(unittest.TestCase):
    def test_legacy_absence_unknown_no_quality_inference(self):
        self.assertIsNone(native_persistence_provenance(SimpleNamespace(), native_quality_flags=512))

    def test_exact_recipe_mapping_and_no_hot_policy_serialization(self):
        for mode in HostDcMode:
            value = density(mode)
            with patch("sdr_monitor.domain.processing_policy.json.dumps", side_effect=AssertionError("hot JSON")), \
                    patch("sdr_monitor.domain.processing_policy.hashlib.sha256", side_effect=AssertionError("hot hash")):
                observed = native_persistence_provenance(value, native_quality_flags=value.quality_flags)
            self.assertIs(observed.processing_recipe.dc_mode, mode)
            self.assertEqual(observed.window_normalization_version, "power-norm-v1")
            self.assertIsNone(observed.estimated_uncertainty_db)
            self.assertIsNone(observed.calibration_profile_id)
        for flags in (True, "512", -1, 1 << 32):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                native_persistence_provenance(density(), native_quality_flags=flags)
        with self.assertRaisesRegex(ValueError, "DC_REMOVED"):
            native_persistence_provenance(density(HostDcMode.BLOCK_MEAN), native_quality_flags=0)

    def test_absolute_calibration_and_malformed_metadata_not_admitted(self):
        for unit in ("dBm/bin", SimpleNamespace(name="DBM_BIN")):
            value = density()
            value.unit = unit
            with self.subTest(unit=unit), self.assertRaisesRegex(ValueError, "dBm"):
                native_persistence_provenance(value, native_quality_flags=0)
        value = density()
        value.processing_metadata.calibration_status = "applied"
        with self.assertRaisesRegex(ValueError, "calibration"):
            native_persistence_provenance(value, native_quality_flags=0)

    def test_host_hf_metadata_budget_boundary_is_preallocation(self):
        from tests.test_app06_hackrf_burst_budget import request
        profile = request(fft_size=256, hop_size=256, persistence_enabled=True,
            persistence_mode="rolling-exact", persistence_power_bins=16,
            persistence_window_frames=262047)
        self.assertEqual(profile.persistence_allocation_bytes, 256 * 1024 * 1024)
        with self.assertRaisesRegex(ValueError, "256 MiB"):
            replace(profile, persistence_window_frames=262048)

    def test_original_ad_converter_retains_native_observation_only(self):
        service = NativeLiveSessionService(_FakeNative())
        for mode in HostDcMode:
            value = density(mode)
            converted = service._convert_persistence(value, service.latest_snapshot())
            self.assertIs(converted.numerical_provenance.processing_recipe.dc_mode, mode)
            self.assertIsNone(converted.layer_ready)  # No admitted synthetic owner receipt.
            self.assertFalse(converted.density.flags.writeable)
            self.assertEqual(converted.source_frame_sequence, value.source_frame_sequence)
            self.assertIs(converted.density.base, value.density)

    def test_original_hf_converter_does_not_borrow_latest_spectrum(self):
        _, current, _, _, _ = composition(policy=SdrProcessingPolicyV1())
        request = replace(current.hackrf_request, persistence_enabled=True, persistence_mode="exponential-decay",
            persistence_power_bins=16, window="rectangular")
        current = replace(current, hackrf_request=request, hackrf_persistence_available=True)
        service = object.__new__(HackrfAnalyzerService)
        service._native = SimpleNamespace()
        service._layer_journal = SimpleNamespace(receipt=lambda *args: None)
        service._ready_bridge = SimpleNamespace()
        value = density(source=str(request.source_id), fft=request.fft_size)
        converted = service._convert_persistence(value, current)
        self.assertIs(converted.numerical_provenance.processing_recipe.dc_mode, HostDcMode.OFF)
        self.assertIsNone(converted.layer_ready)
        with self.assertRaises(ValueError):
            replace(converted, numerical_provenance={"dc_mode": "off"})
        value.processing_metadata = metadata(HostDcMode.BLOCK_MEAN)
        value.quality_flags = 512
        with self.assertRaisesRegex(ValueError, "processing profile"):
            service._convert_persistence(value, current)


class NativePersistenceProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.native = importlib.import_module("sdr_monitor._sdr_native")
        except ImportError as error:
            raise unittest.SkipTest("optional native unavailable") from error
        if not hasattr(cls.native, "persistence_processing_contract"):
            raise unittest.SkipTest("legacy native has no density processing metadata")

    def test_exact_native_contract_budget_and_readonly_runtime_metadata(self):
        n = self.native
        self.assertEqual(n.persistence_processing_contract(), {
            "schema_version": 1, "scope": "native_contributing_density_metadata_only",
            "full_owner_context": False, "recipe_change_resets": True, "string_max_bytes": 256,
            "reserved_bytes": DENSITY_PROCESSING_METADATA_RESERVATION_BYTES})
        control = n._make_test_hackrf_runtime_dsp_control(blocks=4, layer_event_capacity=4)
        try:
            deadline = time.monotonic() + 3.
            snapshots = []
            while not snapshots and time.monotonic() < deadline:
                snapshots.extend(control.poll_persistence_snapshots(2))
                if not snapshots:
                    time.sleep(.005)
            self.assertTrue(snapshots)
            value = snapshots[-1]
            self.assertEqual(value.processing_metadata.dsp_processing_recipe.dc_mode, "off")
            self.assertEqual(value.processing_metadata.fft_size, value.frequency_bins)
            observed = native_persistence_provenance(value, native_quality_flags=int(value.quality_flags))
            self.assertIs(observed.processing_recipe.dc_mode, HostDcMode.OFF)
            with self.assertRaises(AttributeError):
                value.processing_metadata.fft_size = 512
            with self.assertRaises(AttributeError):
                value.processing_metadata = None
        finally:
            result = control.stop(1000)
            self.assertTrue(result.complete())
            self.assertFalse(control.metrics().lifecycle_open)
