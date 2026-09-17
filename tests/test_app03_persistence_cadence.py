"""Accumulation endpoints may lag spectra without losing producer identity."""

from dataclasses import replace
import unittest

import numpy as np

from sdr_monitor.domain.analyzer import bundle_from_live
from sdr_monitor.domain.live import LivePersistenceFrame, LiveSessionState, LiveSnapshot
from tests.test_app01_analyzer_coherence import _spectrum


class PersistenceCadenceTests(unittest.TestCase):
    def snapshot(self):
        frame = replace(_spectrum(), acquisition_epoch=3, receiver_id="rx1", clock_domain="unix_ns")
        layer = LivePersistenceFrame(
            update_sequence=1, timestamp_ns=9, source_frame_sequence=6,
            power_min_db=-120, power_max_db=0, power_bins=2, frequency_bins=2,
            processed_frames=6, exponential_decay=False,
            frequencies_hz=frame.frequencies_hz, density=np.ones((2, 2), dtype=np.float32),
            probability_scale=0.1, unit=frame.unit, source_id=frame.source_id,
            config_generation=frame.config_generation, producer_identity_available=True,
            acquisition_epoch=3, receiver_id="rx1", clock_domain="unix_ns",
        )
        return LiveSnapshot(generation=1, sequence=1, state=LiveSessionState.RUNNING,
                            spectrum=frame, persistence=layer)

    def test_lagging_accumulation_retains_original_endpoint_and_buffers(self):
        snapshot = self.snapshot()
        bundle = bundle_from_live(snapshot)
        self.assertIs(bundle.persistence, snapshot.persistence)
        self.assertEqual(bundle.persistence.source_frame_sequence, 6)
        self.assertEqual(bundle.coherence_issues, ())

    def test_future_endpoint_waits_until_spectrum_catches_up(self):
        snapshot = self.snapshot()
        snapshot = replace(snapshot, persistence=replace(snapshot.persistence, source_frame_sequence=8))
        waiting = bundle_from_live(snapshot)
        self.assertIsNone(waiting.persistence)
        self.assertEqual(waiting.coherence_issues, ("persistence_pending",))
        later = replace(snapshot, spectrum=replace(snapshot.spectrum, sequence=9))
        self.assertIs(bundle_from_live(later).persistence, snapshot.persistence)

    def test_lag_never_allows_cross_measurement_layers(self):
        snapshot = self.snapshot()
        for changes in ({"source_id": "other"}, {"config_generation": 99},
                        {"acquisition_epoch": 4}, {"receiver_id": "rx2"},
                        {"clock_domain": "other"}, {"unit": "dBm"},
                        {"frequencies_hz": np.array([100., 102.])},
                        {"producer_identity_available": False}):
            with self.subTest(changes=changes):
                wrong = replace(snapshot, persistence=replace(snapshot.persistence, **changes))
                self.assertIsNone(bundle_from_live(wrong).persistence)
                future_wrong = replace(wrong, persistence=replace(wrong.persistence, source_frame_sequence=8))
                self.assertEqual(bundle_from_live(future_wrong).coherence_issues,
                                 ("persistence_identity_mismatch",))
