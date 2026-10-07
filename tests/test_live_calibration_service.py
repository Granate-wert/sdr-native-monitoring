"""Selected profile to current spectrum, synthetic source/readback only."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.calibration import CalibrationProfileError, CalibrationStatus
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.services.calibration_service import CalibrationService
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from tests import test_current_frame_calibration as fixtures


class LiveCalibrationServiceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CurrentFrameCalibrationTests()
        self.fixture.setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.service = CalibrationService(CalibrationProfileStore(Path(folder.name)))
        self.profile = self.fixture.profile(self.fixture.signature())
        self.service.set_current_settings(self.profile.signature)

    def correct(self, current=None):
        return self.service.correct_current_spectrum(
            self.fixture.current if current is None else current,
            self.fixture.facts.endpoint, self.fixture.facts.frontend)

    def test_selected_revision_and_raw_provenance_are_preserved(self):
        self.service.select_active_profile(self.profile)
        output = self.correct()
        self.assertIs(output.raw, self.fixture.frame)
        self.assertEqual(output.session_id, self.fixture.current.session_id)
        self.assertEqual(output.profile_version, 1)
        self.assertEqual(output.profile_fingerprint, self.profile.fingerprint)
        self.assertEqual(output.result.profile_id, self.profile.profile_id)
        self.assertEqual(output.result.unit, 'dBm/bin')
        np.testing.assert_allclose(output.result.values, [-29, -18.5, -8])
        np.testing.assert_array_equal(output.raw.values, [-30, -20, -10])
        self.assertFalse(output.result.values.flags.writeable)
        self.assertFalse(output.result.uncertainty_db.flags.writeable)
        self.assertFalse(np.shares_memory(output.raw.values, output.result.values))

    def test_no_profile_or_incompatible_expert_profile_keeps_raw_units(self):
        output = self.correct()
        self.assertIsNone(output.profile_version)
        self.assertIsNone(output.profile_fingerprint)
        self.assertEqual(output.result.unit, 'dBFS/bin')
        wrong = replace(self.profile, signature=replace(self.profile.signature, manual_gain_db=99))
        self.service.select_active_profile(wrong, expert_override=True)
        output = self.correct()
        self.assertIs(output.result.status, CalibrationStatus.INVALID)
        self.assertEqual(output.result.unit, 'dBFS/bin')
        np.testing.assert_array_equal(output.result.values, output.raw.values)

    def test_stopped_or_missing_frame_refuses_without_activating(self):
        for current in (replace(self.fixture.current, spectrum=None),
                        replace(self.fixture.current, state=LiveSessionState.CONNECTED)):
            with self.assertRaises(CalibrationProfileError):
                self.correct(current)
        self.assertIsNone(self.service.active_profile())

    def test_nonfinite_or_unordered_arrays_refuse(self):
        for changes in (dict(values=np.array([0., np.nan, 1.])),
                        dict(frequencies_hz=np.array([100., 100., 200.])),
                        dict(frequencies_hz=np.array([200., 150., 100.]))):
            frame = replace(self.fixture.frame, **changes)
            with self.assertRaises(CalibrationProfileError):
                self.correct(replace(self.fixture.current, spectrum=frame))

    def test_selection_change_during_math_does_not_mix_revisions(self):
        from sdr_monitor.services import calibration_service as module
        original = module.apply_calibration
        self.service.select_active_profile(self.profile)
        replacement = replace(self.profile, profile_version=2)

        def switched(*args, **kwargs):
            self.service.select_active_profile(replacement)
            return original(*args, **kwargs)

        with patch.object(module, 'apply_calibration', side_effect=switched):
            output = self.correct()
        self.assertEqual(output.profile_version, 1)
        self.assertEqual(output.profile_fingerprint, self.profile.fingerprint)
        self.assertEqual(self.service.active_profile().profile_version, 2)
        self.assertEqual(self.correct().profile_version, 2)

    def test_psd_result_is_per_hz_and_cleared_selection_returns_raw(self):
        frame = replace(self.fixture.frame, unit='dBFS/Hz')
        current = replace(self.fixture.current, spectrum=frame, unit=frame.unit)
        signature = self.fixture.signature(current=current, frame=frame)
        profile = self.fixture.profile(signature)
        self.service.set_current_settings(signature)
        self.service.select_active_profile(profile)
        self.assertEqual(self.correct(current).result.unit, 'dBm/Hz')
        self.service.clear_active_profile()
        output = self.correct(current)
        self.assertEqual(output.result.unit, 'dBFS/Hz')
        self.assertIsNone(output.profile_fingerprint)

    def test_outside_coverage_returns_whole_raw_frame_without_extrapolation(self):
        self.service.select_active_profile(self.profile)
        frame = replace(self.fixture.frame, frequencies_hz=np.array([99., 150., 200.]))
        output = self.correct(replace(self.fixture.current, spectrum=frame))
        self.assertEqual(output.result.unit, 'dBFS/bin')
        self.assertIs(output.result.status, CalibrationStatus.INVALID)
        np.testing.assert_array_equal(output.result.values, frame.values)


if __name__ == '__main__':
    unittest.main()
