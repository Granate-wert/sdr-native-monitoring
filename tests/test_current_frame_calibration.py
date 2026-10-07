"""Current original frame guards and unit-safe correction, synthetic ONLY."""
from dataclasses import replace
import unittest

import numpy as np

from sdr_monitor.domain.calibration import CalibrationPoint, CalibrationProfile, CalibrationProfileError, apply_calibration
from sdr_monitor.domain.live import LiveSessionState, LiveSnapshot, LiveSpectrumFrame
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection
from sdr_monitor.services.live_calibration_signature import build_current_frame_calibration_signature
import tests.test_live_calibration_signature as signature_fixtures


class CurrentFrameCalibrationTests(unittest.TestCase):
    def setUp(self):
        facts = signature_fixtures.LiveCalibrationSignatureTests()
        facts.setUp()
        facts.provenance = replace(facts.provenance, detector="sample", averaging_frames=1)
        self.facts = facts
        actual = facts.applied.applied
        self.frame = LiveSpectrumFrame(
            1, 100, actual.center_hz, actual.sample_rate_hz, actual.fft_size, 2048,
            np.array([100., 150., 200.]), np.array([-30., -20., -10.]),
            source_id="actual-producer-rx1", config_generation=7, receiver_id="RX1",
            acquisition_epoch=3, clock_domain="host_steady_ns", numerical_provenance=facts.provenance,
        )
        self.current = LiveSnapshot(
            7, 1, LiveSessionState.RUNNING, device=facts.device, applied=facts.applied,
            spectrum=self.frame, session_id="admitted-session", active_source_id=self.frame.source_id,
            receiver_id="RX1", acquisition_epoch=3, clock_domain="host_steady_ns", active_config_generation=7,
        )

    def signature(self, *, current=None, frame=None, endpoint=None):
        return build_current_frame_calibration_signature(
            self.current if current is None else current, self.frame if frame is None else frame,
            self.facts.endpoint if endpoint is None else endpoint, self.facts.frontend,
        )

    def profile(self, signature):
        return CalibrationProfile("current-frame", 1, signature,
            (CalibrationPoint(100., 1., .2), CalibrationPoint(200., 2., .3)))

    def test_original_current_producer_can_differ_from_logical_device_id(self):
        signature = self.signature()
        corrected = apply_calibration(self.frame.values, self.frame.frequencies_hz, self.profile(signature), signature)
        self.assertEqual(corrected.unit, "dBm/bin")
        np.testing.assert_allclose(corrected.values, [-29., -18.5, -8.])
        np.testing.assert_array_equal(self.frame.values, [-30., -20., -10.])
        self.assertFalse(self.frame.values.flags.writeable)

    def test_clone_retained_stop_and_changed_epoch_or_generation_refuse(self):
        with self.assertRaises(CalibrationProfileError):
            self.signature(frame=replace(self.frame))
        for changes in (dict(state=LiveSessionState.CONNECTED), dict(session_id="unknown"),
                        dict(acquisition_epoch=4), dict(active_config_generation=8),
                        dict(active_source_id="other"), dict(clock_domain="other"), dict(unit="dBFS/Hz")):
            with self.subTest(changes=changes), self.assertRaises(CalibrationProfileError):
                self.signature(current=replace(self.current, **changes))

    def test_rx_unknown_other_or_both_refuse(self):
        for changes in (dict(receiver_id=None), dict(receiver_id="RX2")):
            with self.subTest(changes=changes), self.assertRaises(CalibrationProfileError):
                self.signature(current=replace(self.current, **changes))
        with self.assertRaises(CalibrationProfileError):
            self.signature(endpoint=replace(self.facts.endpoint, selection=ReceiverChainSelection.BOTH))

    def test_frame_retune_wrong_fft_or_backend_transition_refuse(self):
        for changes in (dict(center_frequency_hz=self.frame.center_frequency_hz+1),
                        dict(sample_rate_hz=self.frame.sample_rate_hz+1), dict(fft_size=2048),
                        dict(hop_size=1024), dict(backend_discontinuity=True)):
            with self.subTest(changes=changes):
                frame = replace(self.frame, **changes)
                with self.assertRaises(CalibrationProfileError):
                    self.signature(frame=frame, current=replace(self.current, spectrum=frame))
        for changes in (dict(window="rectangular"), dict(detector="peak"), dict(averaging_frames=2)):
            with self.subTest(changes=changes):
                frame = replace(self.frame, numerical_provenance=replace(self.facts.provenance, **changes))
                with self.assertRaises(CalibrationProfileError):
                    self.signature(frame=frame, current=replace(self.current, spectrum=frame))

    def test_psd_units_survive_success_no_profile_mismatch_and_outside_coverage(self):
        frame = replace(self.frame, unit="dBFS/Hz")
        current = replace(self.current, spectrum=frame, unit=frame.unit)
        signature = self.signature(current=current, frame=frame)
        profile = self.profile(signature)
        successful = apply_calibration(frame.values, frame.frequencies_hz, profile, signature)
        self.assertEqual(successful.unit, "dBm/Hz")
        for item, frequencies in ((None, frame.frequencies_hz),
                                  (self.profile(replace(signature, manual_gain_db=99.)), frame.frequencies_hz),
                                  (profile, np.array([99., 150., 200.]))):
            with self.subTest(profile=item):
                result = apply_calibration(frame.values, frequencies, item, signature)
                self.assertEqual(result.unit, "dBFS/Hz")
                np.testing.assert_array_equal(result.values, frame.values)

    def test_unknown_or_absolute_input_convention_is_not_guessed(self):
        signature = self.signature()
        for unit in ("unknown", "dBm/bin", "dBm/Hz"):
            with self.subTest(unit=unit), self.assertRaises(CalibrationProfileError):
                apply_calibration(self.frame.values, self.frame.frequencies_hz, None,
                                  replace(signature, fft_unit_convention=unit))
