"""Current backend domain → V2 presentation contract, no hardware."""
import unittest
from dataclasses import replace

import numpy as np

from sdr_monitor.domain.live import LiveSnapshot, LiveSpectrumFrame, LiveSessionState
from sdr_monitor.domain.analyzer import (
    AnalyzerPublicationKind, bundle_from_live, bundle_from_sweep,
)
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState, SweepQualitySchema
from sdr_monitor.ui.v2.state.live_view_state import build_live_view_state
from sdr_monitor.services.native_continuous_sweep import (
    ContinuousSweepDisplaySnapshot, ContinuousSweepDisplayMetrics,
)


class AnalyzerBundleTests(unittest.TestCase):
    def test_actual_domain_frame_owns_units_quality_and_generation_in_v2(self):
        frame = LiveSpectrumFrame(
            sequence=4, timestamp_ns=12, center_frequency_hz=100.,
            sample_rate_hz=2., fft_size=2, hop_size=1,
            frequencies_hz=np.array([99., 100.]), values=np.array([-80., -40.]),
            unit="dBFS/Hz", source_id="producer-a", config_generation=9,
            native_quality_flags=0x2031,
        )
        snapshot = LiveSnapshot(
            generation=70, sequence=80, state=LiveSessionState.RUNNING,
            spectrum=frame, unit="dBm", session_id="session-a",
        )
        state = build_live_view_state(snapshot)
        bundle = state.analyzer_bundle
        self.assertIsNotNone(bundle)
        self.assertIs(bundle.spectrum, frame)
        self.assertIs(bundle.spectrum.values, frame.values)
        self.assertEqual(state.unit_label, "dBFS/Hz")
        self.assertEqual(bundle.spectrum.config_generation, 9)
        self.assertEqual(bundle.spectrum.native_quality_flags, 0x2031)
        self.assertEqual(bundle.session_id, "session-a")
        self.assertIsNone(bundle.receiver_id)
        self.assertIsNone(bundle.acquisition_epoch)
        self.assertIs(bundle.publication_kind, AnalyzerPublicationKind.RTBW_FRAME)
        self.assertFalse(bundle.terminal_sweep)
        with self.assertRaisesRegex(ValueError, "match its measurement"):
            replace(bundle, rtbw=replace(bundle.rtbw, sample_rate_hz=4.))

    def test_empty_snapshot_does_not_fabricate_measurement(self):
        snapshot = LiveSnapshot(generation=0, sequence=0, state=LiveSessionState.CONNECTED)
        self.assertIsNone(bundle_from_live(snapshot))

    def test_sweep_envelope_preserves_terminal_gap_and_per_bin_provenance(self):
        line = SweepLineFrame(
            sequence=12, epoch=3, completed_at_ns=900, source_id="sweep-a",
            state=SweepLineState.GAP, frequencies_hz=np.array([100., 101.]),
            values_db=np.array([-80., np.nan]),
            quality_flags=np.array([1, 4096], dtype=np.uint16),
            source_segment_indices=np.array([0, -1]), missing_segment_indices=(1,),
            segment_config_generations=((0, 7),), gap_reasons=(), unit="dBFS/bin",
            quality_schema=SweepQualitySchema.NATIVE_V5,
        )
        bundle = bundle_from_sweep(line)
        self.assertEqual(bundle.mode, "sweep")
        self.assertIs(bundle.publication_kind, AnalyzerPublicationKind.SWEEP_GAP)
        self.assertTrue(bundle.terminal_sweep)
        self.assertIs(bundle.spectrum, line)
        self.assertIs(bundle.values, line.values_db)
        self.assertIs(bundle.frequencies_hz, line.frequencies_hz)
        self.assertFalse(bundle.spectrum.is_complete)
        self.assertEqual(bundle.acquisition_epoch, 3)
        self.assertIsNone(bundle.rtbw)
        self.assertIsNone(bundle.session_id)
        self.assertEqual(bundle.spectrum.aggregate_quality_flags, 4097)
        published = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics())
        self.assertIs(published.analyzer_bundle.spectrum, line)
        self.assertIsNone(ContinuousSweepDisplaySnapshot(
            None, ContinuousSweepDisplayMetrics(),
        ).analyzer_bundle)
        with self.assertRaises(ValueError):
            replace(bundle, acquisition_epoch=4)
        with self.assertRaisesRegex(ValueError, "Sweep measurement"):
            replace(bundle, identity=replace(bundle.identity, source_id="foreign"))
        with self.assertRaisesRegex(ValueError, "does not declare"):
            replace(bundle, receiver_id="rx-invented")
        complete = replace(
            line, state=SweepLineState.COMPLETE,
            values_db=np.array([-80., -90.]),
            quality_flags=np.array([1, 1], dtype=np.uint16),
            source_segment_indices=np.array([0, 1]), missing_segment_indices=(),
            segment_config_generations=((0, 7), (1, 8)),
        )
        completed_bundle = bundle_from_sweep(complete)
        self.assertIs(completed_bundle.publication_kind, AnalyzerPublicationKind.SWEEP_COMPLETE)
        self.assertTrue(completed_bundle.terminal_sweep)
