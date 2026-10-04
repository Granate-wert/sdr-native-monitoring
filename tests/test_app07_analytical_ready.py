"""Explicit selected-native producer receipts; no physical SDR opens or UI claims."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest

import numpy as np

from tests.native_test_dependencies import native_test_dll_directory


class AnalyticalReadyBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected:
            raise unittest.SkipTest("requires explicit matching native path")
        path = Path(selected).resolve(strict=True)
        cls.dll_scope = native_test_dll_directory(str(path))
        cls.dll_scope.__enter__()
        cls.addClassCleanup(cls.dll_scope.__exit__, None, None, None)
        spec = importlib.util.spec_from_file_location("_sdr_native", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("explicit native spec unavailable; no fallback")
        cls.native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.native)

    def backend(self, capacity=64, averaging=1):
        native = self.native
        backend = native.CpuDspBackend(analytical_event_capacity=capacity)
        config = native.DspConfig(
            256, 256, native.WindowType.HANN, native.DetectorType.AVERAGE_POWER,
            native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.ACCURATE_F32_F64_ACCUM,
            1, averaging, 8.6, native.CalibrationStatus.UNCALIBRATED, "", 5,
        )
        backend.configure(config)
        return backend

    def test_original_receipt_readonly_and_not_poll_time(self):
        self.assertEqual(self.native.ANALYTICAL_READY_CONTRACT_VERSION, 1)
        backend = self.backend()
        before = self.native.analytical_ready_clock_ns()
        backend.push_samples(np.ones(768, dtype=np.complex64), 256000.0, 100000000.0)
        after = self.native.analytical_ready_clock_ns()
        frames = backend.poll_spectrum()
        self.assertEqual(len(frames), 3)
        for sequence, frame in enumerate(frames, 1):
            ref = frame.analytical_ready
            self.assertEqual(ref.offer_sequence, sequence)
            self.assertGreaterEqual(ref.ready_native_ns, before)
            self.assertLessEqual(ref.ready_native_ns, after)
            with self.assertRaises(AttributeError):
                ref.ready_native_ns = 0
        summary = backend.analytical_ready_summary()
        self.assertEqual((summary.offered, summary.handed_off, summary.outstanding), (3, 3, 0))
        events = backend.poll_analytical_ready_events()
        self.assertEqual(len(events), 6)
        self.assertEqual([event.event_sequence for event in events], list(range(1, 7)))
        self.assertEqual(summary.events_lost, 0)

    def test_evictions_known_before_only_latest_survives(self):
        backend = self.backend()
        backend.push_samples(np.ones(256 * 20, dtype=np.complex64), 256000.0, 100000000.0)
        summary = backend.analytical_ready_summary()
        self.assertEqual((summary.offered, summary.producer_superseded, summary.outstanding),
                         (20, 12, 8))
        frames = backend.poll_spectrum()
        self.assertEqual([frame.analytical_ready.offer_sequence for frame in frames],
                         list(range(13, 21)))
        summary = backend.analytical_ready_summary()
        self.assertEqual(summary.offered, summary.handed_off + summary.producer_superseded)
        self.assertEqual(len(backend.poll_analytical_ready_events()), 40)

    def test_reset_reconfigure_and_distinct_producers(self):
        backend = self.backend()
        backend.push_samples(np.ones(256, dtype=np.complex64), 256000.0, 100000000.0)
        backend.reset()
        summary = backend.analytical_ready_summary()
        self.assertEqual((summary.offered, summary.producer_cancelled, summary.outstanding), (1, 1, 0))
        backend.push_samples(np.ones(256, dtype=np.complex64), 256000.0, 100000000.0)
        ref = backend.poll_spectrum()[0].analytical_ready
        self.assertEqual(ref.offer_sequence, 2)
        other = self.backend()
        self.assertNotEqual(ref.producer_instance_id, other.analytical_ready_summary().producer_instance_id)

    def test_averaging_not_per_fft_and_overflow_explicit(self):
        backend = self.backend(capacity=1, averaging=4)
        backend.push_samples(np.ones(2048, dtype=np.complex64), 256000.0, 100000000.0)
        frames = backend.poll_spectrum()
        self.assertEqual(len(frames), 2)
        self.assertEqual(backend.metrics().fft_frames_computed, 8)
        summary = backend.analytical_ready_summary()
        self.assertEqual((summary.offered, summary.events_pending, summary.events_lost), (2, 1, 3))
        self.assertEqual(summary.events_generated,
                         summary.events_pending + summary.events_drained + summary.events_lost)
        with self.assertRaises(self.native.ConfigurationError):
            self.native.CpuDspBackend(analytical_event_capacity=4097)

    def test_legacy_and_summary_only_are_not_false_evidence(self):
        self.assertIsNone(self.native._make_test_spectrum_frame(16).analytical_ready)
        backend = self.backend(capacity=0)
        backend.push_samples(np.ones(256, dtype=np.complex64), 256000.0, 100000000.0)
        backend.poll_spectrum()
        summary = backend.analytical_ready_summary()
        self.assertEqual((summary.events_lost, summary.event_storage_bytes), (2, 0))
        self.assertEqual(backend.poll_analytical_ready_events(), [])

    def test_actual_native_receipt_survives_existing_pluto_converter_clock_bridge(self):
        from sdr_monitor.domain.identity import ConfigurationGeneration, FrameSequence, SessionId
        from sdr_monitor.domain.live import LiveSessionState, LiveSnapshot
        from sdr_monitor.services.native_live import NativeLiveSessionService
        from sdr_monitor.domain.analytical_ready import ReadyClockMapping

        backend = self.backend()
        owner = NativeLiveSessionService(self.native)
        owner._ready_bridge.begin()
        backend.push_samples(np.ones(256, dtype=np.complex64), 256000.0, 100000000.0)
        raw = backend.poll_spectrum()[0]
        owner._ready_bridge.sample()
        context = LiveSnapshot(ConfigurationGeneration(raw.config_generation), FrameSequence(0),
            LiveSessionState.CONNECTED, acquisition_epoch=1, session_id=SessionId("mock-computation"))
        mapped = owner._convert_spectrum(raw, context)
        ref = mapped.detector_ready
        self.assertEqual(ref.ready_native_ns, raw.analytical_ready.ready_native_ns)
        self.assertEqual(ref.offer_sequence, raw.analytical_ready.offer_sequence)
        self.assertEqual(ref.producer_instance_id, raw.analytical_ready.producer_instance_id)
        self.assertEqual(ref.source_id, raw.source.source_id)
        self.assertEqual(ref.mapping, ReadyClockMapping.BOUNDED)
        before = ref.host_bounds.earliest_host_ns
        after = ref.host_bounds.latest_host_ns
        self.assertLessEqual(before, after)
        self.assertEqual(mapped.timestamp_ns, raw.timestamp_ns)
        self.assertEqual(owner._convert_spectrum(raw, context).detector_ready, ref)
        # Fresh computation receipt above is NOT a physical acquisition proof.


if __name__ == "__main__":
    unittest.main()
