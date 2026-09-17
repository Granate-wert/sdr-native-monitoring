"""Negative and positive coherence gates for separately published layers."""

import unittest
from dataclasses import replace
from types import SimpleNamespace

import numpy as np

from sdr_monitor.domain.analyzer import bundle_from_live
from sdr_monitor.domain.analyzer_identity import MeasurementIdentity, identities_compatible
from sdr_monitor.domain.live import (
    LivePersistenceFrame, LiveSessionState, LiveSnapshot, LiveSpectrumFrame, TimestampQuality,
)
from sdr_monitor.ui.v2.state.analyzer_layers import (
    persistence_density_from_native, waterfall_line_from_spectrum,
)
from sdr_monitor.ui.v2.spectrum import DensityValueMode
from sdr_monitor.ui.v2.state.live_view_state import build_live_view_state


def _spectrum() -> LiveSpectrumFrame:
    return LiveSpectrumFrame(
        sequence=7, timestamp_ns=10, center_frequency_hz=101.0,
        sample_rate_hz=2.0, fft_size=2, hop_size=1,
        frequencies_hz=np.array([100.0, 101.0]), values=np.array([-2.0, -1.0]),
        unit="dBFS/Hz", source_id="source-a", config_generation=4,
    )


def _layer(**changes: object) -> SimpleNamespace:
    values = dict(
        frequencies_hz=np.array([100.0, 101.0]), unit="dBFS/Hz",
        source_id="source-a", config_generation=4, source_frame_sequence=7,
        receiver_id=None, acquisition_epoch=None, clock_domain=None,
        producer_identity_available=True,
    )
    values.update(changes)
    return SimpleNamespace(**values)


class AnalyzerCoherenceTests(unittest.TestCase):
    def test_known_core_identity_joins_without_inventing_unknown_clock_or_rx(self):
        frame = _spectrum()
        snapshot = LiveSnapshot(
            generation=99, sequence=1, state=LiveSessionState.RUNNING,
            session_id="session-a", spectrum=frame, persistence=_layer(),
        )
        bundle = bundle_from_live(snapshot)
        self.assertIsNotNone(bundle)
        self.assertIs(bundle.persistence, snapshot.persistence)
        self.assertIsNone(bundle.identity.receiver_id)
        self.assertIsNone(bundle.identity.acquisition_epoch)
        self.assertIsNone(bundle.identity.clock_domain)

    def test_auxiliary_layer_mismatch_or_unproven_identity_fails_closed(self):
        for changes in (
            {"source_id": "source-b"}, {"config_generation": 5},
            {"frequencies_hz": np.array([100.0, 102.0])}, {"unit": "dBm"},
            {"producer_identity_available": False}, {"source_frame_sequence": 6},
        ):
            with self.subTest(changes=changes):
                snapshot = LiveSnapshot(
                    generation=1, sequence=1, state=LiveSessionState.RUNNING,
                    spectrum=_spectrum(), persistence=_layer(**changes),
                )
                bundle = bundle_from_live(snapshot)
                self.assertIsNotNone(bundle)
                self.assertIsNone(bundle.persistence)
                self.assertIs(bundle.spectrum, snapshot.spectrum)

    def test_optional_known_receiver_epoch_and_clock_conflicts_do_not_match(self):
        for changes in ({"receiver_id": "rx-b"}, {"acquisition_epoch": 7},
                        {"clock_domain": "host"}, {"accumulation_id": "other"}):
            with self.subTest(changes=changes):
                layer = _layer(receiver_id="rx-a", acquisition_epoch=8, clock_domain="device-a",
                               accumulation_id="acc-a")
                for name, value in changes.items():
                    setattr(layer, name, value)
                reference = MeasurementIdentity.from_frame(_layer(
                    receiver_id="rx-a", acquisition_epoch=8, clock_domain="device-a",
                    accumulation_id="acc-a",
                ))
                self.assertFalse(identities_compatible(reference, MeasurementIdentity.from_frame(layer)))

    def test_stale_frame_is_rejected_against_published_active_identity(self):
        frame = replace(
            _spectrum(), receiver_id="rx-old", acquisition_epoch=3,
            clock_domain="device-clock", config_generation=4,
        )
        snapshot = LiveSnapshot(
            generation=90, sequence=1, state=LiveSessionState.RUNNING,
            spectrum=frame, receiver_id="rx-active", acquisition_epoch=3,
            clock_domain="device-clock", active_config_generation=4,
        )
        self.assertIsNone(bundle_from_live(snapshot))

        generation_stale = replace(snapshot, receiver_id="rx-old", active_config_generation=5)
        self.assertIsNone(bundle_from_live(generation_stale))

        source_stale = replace(snapshot, receiver_id="rx-old", active_source_id="source-new")
        self.assertIsNone(bundle_from_live(source_stale))

    def test_rtbw_envelope_cannot_contradict_frame_receiver_or_epoch(self):
        frame = replace(_spectrum(), receiver_id="rx-a", acquisition_epoch=2)
        snapshot = LiveSnapshot(
            generation=1, sequence=1, state=LiveSessionState.RUNNING, spectrum=frame,
            receiver_id="rx-a", acquisition_epoch=2,
        )
        bundle = bundle_from_live(snapshot)
        with self.assertRaisesRegex(ValueError, "receiver/epoch"):
            replace(bundle, receiver_id="rx-b")

    def test_validated_native_layers_convert_to_existing_v2_contracts(self):
        frame = replace(
            _spectrum(), clock_domain="unix_ns", timestamp_quality=TimestampQuality.HARDWARE,
        )
        persistence = LivePersistenceFrame(
            update_sequence=8, timestamp_ns=10, source_frame_sequence=7,
            power_min_db=-120.0, power_max_db=-20.0, power_bins=2, frequency_bins=2,
            processed_frames=4, exponential_decay=False,
            frequencies_hz=frame.frequencies_hz,
            density=np.array(((0.0, 2.0), (4.0, 1.0)), dtype=np.float32),
            probability_scale=0.25, count_scale=1.0,
            source_id=frame.source_id, config_generation=frame.config_generation,
            clock_domain=frame.clock_domain, unit=frame.unit,
            producer_identity_available=True,
        )
        snapshot = LiveSnapshot(
            generation=1, sequence=1, state=LiveSessionState.RUNNING,
            spectrum=frame, persistence=persistence,
        )
        bundle = bundle_from_live(snapshot)
        self.assertIs(bundle.persistence, persistence)
        density = persistence_density_from_native(bundle.persistence)
        self.assertTrue(np.array_equal(density.density, persistence.density * 0.25))
        self.assertFalse(density.density.flags.writeable)
        self.assertEqual(density.frequency_edges_hz.tolist(), [99.5, 100.5, 101.5])
        counts = persistence_density_from_native(persistence, value_mode=DensityValueMode.COUNT)
        self.assertTrue(np.array_equal(counts.density, persistence.density))
        self.assertIs(counts.value_mode, DensityValueMode.COUNT)
        waterfall = waterfall_line_from_spectrum(frame)
        self.assertIs(waterfall.values, frame.values)
        self.assertEqual(waterfall.timestamp_ns, frame.timestamp_ns)

    def test_malformed_auxiliary_geometry_does_not_suppress_valid_current(self):
        frame = _spectrum()
        persistence = LivePersistenceFrame(
            update_sequence=8, timestamp_ns=10, source_frame_sequence=7,
            power_min_db=-120.0, power_max_db=-20.0, power_bins=2, frequency_bins=2,
            processed_frames=4, exponential_decay=False,
            frequencies_hz=frame.frequencies_hz,
            density=np.ones((2, 2), dtype=np.float32), probability_scale=float("nan"),
            source_id=frame.source_id, config_generation=frame.config_generation,
            unit=frame.unit, producer_identity_available=True,
        )
        state = build_live_view_state(LiveSnapshot(
            generation=1, sequence=1, state=LiveSessionState.RUNNING,
            spectrum=frame, persistence=persistence,
        ))
        self.assertIs(state.spectrum, frame)
        self.assertIsNone(state.persistence_frame)
        self.assertEqual(state.measurement_unavailable_reason, "persistence_values_invalid")

        irregular = replace(frame, frequencies_hz=np.array((100.0, 101.0, 103.0)),
                            values=np.array((-2.0, -1.0, -3.0), dtype=np.float32), fft_size=3)
        irregular_state = build_live_view_state(LiveSnapshot(
            generation=1, sequence=1, state=LiveSessionState.RUNNING, spectrum=irregular,
        ))
        # An irregular RTBW grid contradicts the physical FFT metadata; it is
        # invalid current data, not merely an unsupported Waterfall geometry.
        self.assertIsNone(irregular_state.spectrum)
        self.assertIsNone(irregular_state.waterfall_line)
        self.assertIsNotNone(irregular_state.measurement_unavailable_reason)

    def test_invalid_central_measurement_fails_closed_before_ui(self):
        cases = (
            {"frequencies_hz": np.array((101.0, 100.0))},
            {"frequencies_hz": np.array((100.0, np.inf))},
            {"values": np.array((-1.0, np.inf), dtype=np.float32)},
            {"sample_rate_hz": 0.0}, {"fft_size": 0}, {"hop_size": 3},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                snapshot = LiveSnapshot(
                    generation=1, sequence=1, state=LiveSessionState.RUNNING,
                    spectrum=replace(_spectrum(), **changes),
                )
                state = build_live_view_state(snapshot)
                self.assertIsNone(state.spectrum)
                self.assertEqual(state.measurement_unavailable_reason, "invalid_measurement")

        nan_gap = replace(_spectrum(), values=np.array((-1.0, np.nan), dtype=np.float32))
        self.assertIs(bundle_from_live(LiveSnapshot(
            generation=1, sequence=1, state=LiveSessionState.RUNNING, spectrum=nan_gap,
        )).spectrum, nan_gap)


if __name__ == "__main__":
    unittest.main()
