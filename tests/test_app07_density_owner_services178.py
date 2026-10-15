"""Optional native owner admission and legacy converter-order proof."""

import os
from dataclasses import replace
from types import ModuleType, SimpleNamespace
import unittest

import numpy as np

from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.device_capabilities import DeviceCapabilityBinding, DeviceFamily
from sdr_monitor.domain.live import (
    ConfigurationGeneration,
    FrameSequence,
    LivePersistenceFrame,
    LiveSessionState,
    LiveSnapshot,
    SessionId,
)
from sdr_monitor.services.density_processing import DensityProcessingJoin
from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.native_persistence_storage import native_persistence_arrays
from tests.test_app07_protected_waterfall_accounting import (
    explicit_native_support,
    restore_explicit_native_support,
)


class PersistenceStorageServices178Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get("SDR_APP07_READY_NATIVE"):
            raise unittest.SkipTest("requires the explicitly selected clean APP07-178 native")
        cls.native, _ = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls):
        restore_explicit_native_support(cls)

    def test_exact_native_snapshot_receipt_survives_domain_normalization_and_replace(self):
        image = np.full((2, 3), .25, dtype=np.float32)
        axes = np.arange(3, dtype=np.float64) + 10.0
        snapshot = self.native._make_test_immutable_persistence_snapshot(image, axes)
        density, frequencies, receipt = native_persistence_arrays(self.native, snapshot)
        self.assertIsNotNone(receipt)
        frame = LivePersistenceFrame(1, 1, 1, -100.0, 0.0, 2, 3, 1, False,
            frequencies, density, immutable_storage=receipt)
        self.assertIs(frame.immutable_storage, receipt)
        updated = replace(frame, update_sequence=2)
        self.assertIs(updated.immutable_storage, receipt)
        with self.assertRaises(ValueError):
            LivePersistenceFrame(1, 1, 1, -100.0, 0.0, 2, 3, 1, False,
                frequencies, density.copy(), immutable_storage=receipt)
        self.assertNotIn("immutable_storage", repr(frame))

    def test_both_original_native_service_converters_retain_cpp_owner_receipt(self):
        def snapshot(source_id="test-density"):
            return self.native._make_test_immutable_persistence_snapshot(
                np.full((16, 256), .25, dtype=np.float32),
                np.linspace(99_000_000.0, 101_000_000.0, 256, dtype=np.float64),
                source_id, 1, 1)

        class Journal:
            def receipt(self, *_args):
                return None

            def current(self):
                return None

        context = LiveSnapshot(ConfigurationGeneration(1), FrameSequence(1),
            LiveSessionState.DISCONNECTED, acquisition_epoch=2, clock_domain="host_steady_ns",
            session_id=SessionId("density-owner-test"))

        native_service = NativeLiveSessionService.__new__(NativeLiveSessionService)
        native_service._native = self.native
        native_service._density_journals = (Journal(), Journal())
        native_service._ready_bridge = object()
        native_service._paired_request = None
        native_service._density_processing_join = DensityProcessingJoin()
        native_service._processing_revision = 1
        native_frame = native_service._convert_persistence(snapshot(), context)
        self.assertIsNotNone(native_frame.immutable_storage)
        self.assertTrue(native_frame.immutable_storage.matches(
            native_frame.density, native_frame.frequencies_hz))

        request = HackrfLiveRequest(center_frequency_hz=100_000_000.0,
            sample_rate_hz=2_000_000.0, baseband_filter_hz=1_750_000,
            lna_gain_db=16, vga_gain_db=20, fft_size=256, hop_size=128,
            persistence_enabled=True, persistence_mode="rolling-exact",
            persistence_power_bins=16, source_id="test-density")
        binding = DeviceCapabilityBinding("test-density", DeviceFamily.HACKRF, "adapter-test")
        choice = AnalyzerSourceChoice(binding, None, "test", "USB")
        hackrf_context = replace(context, hackrf_request=request, source_choice=choice,
            selection_revision=1, active_source_id=request.source_id,
            active_config_generation=request.configuration_generation,
            hackrf_persistence_available=True, unit="dBFS/bin")
        hackrf_service = HackrfAnalyzerService.__new__(HackrfAnalyzerService)
        hackrf_service._native = self.native
        hackrf_service._layer_journal = Journal()
        hackrf_service._ready_bridge = object()
        hackrf_service._density_processing_join = DensityProcessingJoin()
        hackrf_frame = hackrf_service._convert_persistence(snapshot(), hackrf_context)
        self.assertIsNotNone(hackrf_frame.immutable_storage)
        self.assertTrue(hackrf_frame.immutable_storage.matches(
            hackrf_frame.density, hackrf_frame.frequencies_hz))

    def test_both_legacy_service_converters_keep_frequency_then_density_fallback(self):
        order = []

        class LegacySnapshot:
            update_sequence = 1
            timestamp_ns = 10
            source_frame_sequence = 1
            power_min_db = -140.0
            power_max_db = 20.0
            power_bins = 16
            frequency_bins = 256
            processed_frames = 1
            exponential_decay = False
            source_id = "test-density"
            config_generation = 1
            quality_flags = 0
            probability_scale = 1.0
            count_scale = 1.0
            unit = "dbfs_bin"

            @property
            def frequencies_hz(self):
                order.append("frequencies")
                return np.linspace(99_000_000.0, 101_000_000.0, 256, dtype=np.float64)

            @property
            def density(self):
                order.append("density")
                return np.full((16, 256), .25, dtype=np.float32)

        class Journal:
            def receipt(self, *_args):
                return None

            def current(self):
                return None

        context = LiveSnapshot(ConfigurationGeneration(1), FrameSequence(1),
            LiveSessionState.DISCONNECTED, acquisition_epoch=2, clock_domain="host_steady_ns",
            session_id=SessionId("density-owner-test"))
        native_service = NativeLiveSessionService.__new__(NativeLiveSessionService)
        # Deliberately omit _native to cover older __new__-constructed fixtures.
        native_service._density_journals = (Journal(), Journal())
        native_service._ready_bridge = object()
        native_service._paired_request = None
        native_service._density_processing_join = DensityProcessingJoin()
        native_service._processing_revision = 1
        converted = native_service._convert_persistence(LegacySnapshot(), context)
        self.assertIsNone(converted.immutable_storage)
        self.assertEqual(order, ["frequencies", "density"])

        order.clear()
        request = HackrfLiveRequest(center_frequency_hz=100_000_000.0,
            sample_rate_hz=2_000_000.0, baseband_filter_hz=1_750_000,
            lna_gain_db=16, vga_gain_db=20, fft_size=256, hop_size=128,
            persistence_enabled=True, persistence_mode="rolling-exact",
            persistence_power_bins=16, source_id="test-density")
        binding = DeviceCapabilityBinding("test-density", DeviceFamily.HACKRF, "adapter-test")
        choice = AnalyzerSourceChoice(binding, None, "test", "USB")
        hackrf_context = replace(context, hackrf_request=request, source_choice=choice,
            selection_revision=1, active_source_id=request.source_id,
            active_config_generation=request.configuration_generation,
            hackrf_persistence_available=True, unit="dBFS/bin")
        hackrf_service = HackrfAnalyzerService.__new__(HackrfAnalyzerService)
        # A missing native runtime remains a supported legacy fixture; quality flags
        # still use the original wire-bit fallback.
        hackrf_service._native = None
        hackrf_service._layer_journal = Journal()
        hackrf_service._ready_bridge = object()
        hackrf_service._density_processing_join = DensityProcessingJoin()
        converted = hackrf_service._convert_persistence(LegacySnapshot(), hackrf_context)
        self.assertIsNone(converted.immutable_storage)
        self.assertEqual(order, ["frequencies", "density"])

        class RaisingScalarSnapshot(LegacySnapshot):
            @property
            def power_bins(self):
                raise RuntimeError("preceding scalar sentinel")

        order.clear()
        with self.assertRaisesRegex(RuntimeError, "preceding scalar sentinel"):
            native_service._convert_persistence(RaisingScalarSnapshot(), context)
        self.assertEqual(order, [])

        class RaisingSourceSnapshot(LegacySnapshot):
            @property
            def source_id(self):
                raise RuntimeError("source admission sentinel")

        with self.assertRaisesRegex(RuntimeError, "source admission sentinel"):
            hackrf_service._convert_persistence(RaisingSourceSnapshot(), hackrf_context)
        self.assertEqual(order, [])

    def test_optional_probe_failure_keeps_legacy_frequency_then_density_getters(self):
        order = []

        class LegacySnapshot:
            @property
            def immutable_storage(self):
                raise AssertionError("ownership getter must not precede the builtin probe")

            @property
            def frequencies_hz(self):
                order.append("frequencies")
                return np.arange(3, dtype=np.float64)

            @property
            def density(self):
                order.append("density")
                return np.ones((2, 3), dtype=np.float32)

        native = ModuleType("sdr_monitor._sdr_native")
        native.__spec__ = SimpleNamespace(origin="_sdr_native.cp313-win_amd64.pyd")
        native.PERSISTENCE_IMMUTABLE_STORAGE_PROTOCOL_VERSION = 1
        native.PersistenceSnapshot = LegacySnapshot
        native.PersistenceStorage = type("PersistenceStorage", (), {})
        native._persistence_storage_builtin_type_probe = lambda _value: True
        image, axes, receipt = native_persistence_arrays(native, LegacySnapshot())
        self.assertEqual(order, ["frequencies", "density"])
        self.assertEqual(image.shape, (2, 3))
        self.assertEqual(axes.shape, (3,))
        self.assertIsNone(receipt)

    def test_unavailable_native_uses_original_required_getters(self):
        order = []

        class LegacySnapshot:
            @property
            def frequencies_hz(self):
                order.append("frequencies")
                return np.arange(2, dtype=np.float64)

            @property
            def density(self):
                order.append("density")
                return np.ones((1, 2), dtype=np.float32)

        image, axes, receipt = native_persistence_arrays(object(), LegacySnapshot())
        self.assertEqual(order, ["frequencies", "density"])
        self.assertIsNone(receipt)
        self.assertEqual(image.shape, (1, 2))
        self.assertEqual(axes.shape, (2,))
