"""Same application owner pull and race rejection, fake port not physical RX."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.calibration import CalibrationProfileError
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.services.calibration_service import CalibrationService
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from tests import test_current_frame_calibration as fixtures


class SnapshotPort:
    def __init__(self, current):
        self.current = current
        self.reads = 0

    def latest_snapshot(self):
        self.reads += 1
        return self.current


class CalibrationOwnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CurrentFrameCalibrationTests()
        self.fixture.setUp()
        self.port = SnapshotPort(self.fixture.current)
        self.owner = LiveSessionApplicationService(self.port)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.calibration = CalibrationService(CalibrationProfileStore(Path(folder.name)))
        self.profile = self.fixture.profile(self.fixture.signature())
        self.calibration.set_current_settings(self.profile.signature)
        self.calibration.select_active_profile(self.profile)

    def pull(self):
        return self.owner.calibrated_current_spectrum(
            self.calibration, self.fixture.facts.endpoint, self.fixture.facts.frontend)

    def test_same_owner_reads_before_and_after_without_hardware_open(self):
        result = self.pull()
        self.assertEqual(self.port.reads, 2)
        self.assertIs(result.raw, self.port.current.spectrum)
        self.assertEqual(result.result.unit, 'dBm/bin')
        self.assertTrue(self.calibration.is_current_selection(result))

    def test_owner_stop_frame_epoch_and_session_changes_during_math_refuse(self):
        from sdr_monitor.services import calibration_service as module
        original = module.apply_calibration
        for changes in (dict(state=LiveSessionState.CONNECTED), dict(session_id='other-session'),
                        dict(acquisition_epoch=4), dict(spectrum=replace(self.fixture.frame))):
            self.port.current = self.fixture.current

            def changed(*args, **kwargs):
                self.port.current = replace(self.fixture.current, **changes)
                return original(*args, **kwargs)

            with self.subTest(changes=changes), patch.object(module, 'apply_calibration', side_effect=changed):
                with self.assertRaises(CalibrationProfileError):
                    self.pull()

    def test_profile_clear_and_reselect_same_fingerprint_aba_refuses(self):
        from sdr_monitor.services import calibration_service as module
        original = module.apply_calibration

        def changed(*args, **kwargs):
            self.calibration.clear_active_profile()
            self.calibration.select_active_profile(self.profile)
            return original(*args, **kwargs)

        with patch.object(module, 'apply_calibration', side_effect=changed):
            with self.assertRaisesRegex(CalibrationProfileError, 'selected profile changed'):
                self.pull()
        self.assertEqual(self.pull().result.unit, 'dBm/bin')

    def test_retained_result_invalid_after_clear_or_settings_deactivation(self):
        result = self.pull()
        self.calibration.clear_active_profile()
        self.assertFalse(self.calibration.is_current_selection(result))
        self.calibration.select_active_profile(self.profile)
        result = self.pull()
        self.calibration.set_current_settings(replace(self.profile.signature, manual_gain_db=99))
        self.assertFalse(self.calibration.is_current_selection(result))

    def test_equal_profile_revision_in_other_selection_service_is_not_same_owner(self):
        result = self.pull()
        peer = CalibrationService(self.calibration.store)
        peer.set_current_settings(self.profile.signature)
        peer.select_active_profile(self.profile)
        self.assertFalse(peer.is_current_selection(result))
        self.assertTrue(self.calibration.is_current_selection(result))


if __name__ == '__main__':
    unittest.main()
