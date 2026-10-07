"""Explicit owner-scoped profile commands, not UI/RF qualification."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.calibration import CalibrationProfileError
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from sdr_monitor.services.receiver_calibration import ReceiverCalibrationRegistry
from tests import test_current_frame_calibration as fixtures
from tests.test_live_calibration_owner import SnapshotPort


class CalibrationCommandPreviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CurrentFrameCalibrationTests()
        self.fixture.setUp()
        self.port = SnapshotPort(self.fixture.current)
        self.owner = LiveSessionApplicationService(self.port)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.registry = ReceiverCalibrationRegistry(CalibrationProfileStore(Path(folder.name)))
        self.service = self.registry.for_device(self.fixture.facts.device, self.fixture.facts.endpoint)
        self.profile = self.fixture.profile(self.fixture.signature())
        self.lane = self.owner.captured_calibration_lane(
            self.registry, self.fixture.facts.endpoint, self.fixture.facts.frontend)

    def test_preview_inert_select_explicit_and_repeated_apply_refuses(self):
        before = self.service.current_settings(), self.service.selection_receipt()
        preview = self.lane.preview_selection(self.profile)
        self.assertIsNone(self.service.active_profile())
        self.assertEqual(before, (self.service.current_settings(), self.service.selection_receipt()))
        self.assertTrue(preview.applicability.applicable)
        self.assertTrue(self.lane.apply_selection(preview).applicable)
        self.assertIs(self.service.active_profile(), self.profile)
        with self.assertRaises(CalibrationProfileError):
            self.lane.apply_selection(preview)

    def test_incompatible_profile_does_not_replace_actual_settings_or_selection(self):
        self.lane.apply_selection(self.lane.preview_selection(self.profile))
        before = self.service.current_settings(), self.service.selection_receipt(), self.service.active_profile()
        wrong = self.fixture.profile(replace(self.profile.signature, manual_gain_db=99))
        preview = self.lane.preview_selection(wrong)
        self.assertFalse(preview.applicability.applicable)
        self.assertEqual(preview.signature, self.fixture.signature())
        with self.assertRaises(CalibrationProfileError):
            self.lane.apply_selection(preview)
        self.assertEqual(before, (self.service.current_settings(), self.service.selection_receipt(), self.service.active_profile()))

    def test_clear_is_explicit_and_invalidates_retained_result(self):
        self.lane.apply_selection(self.lane.preview_selection(self.profile))
        result = self.lane.prepare()
        preview = self.lane.preview_selection(None)
        self.assertIs(self.service.active_profile(), self.profile)
        self.assertIsNone(self.lane.apply_selection(preview))
        self.assertIsNone(self.service.active_profile())
        self.assertFalse(self.lane.is_valid(result))
        self.assertEqual(self.lane.prepare().analytical.result.unit, 'dBFS/bin')

    def test_cadence_advancement_allowed_but_control_epoch_and_selection_aba_refuse(self):
        preview = self.lane.preview_selection(self.profile)
        frame = replace(self.fixture.frame, sequence=2)
        self.port.current = replace(self.fixture.current, spectrum=frame, sequence=2)
        self.lane.apply_selection(preview)
        preview = self.lane.preview_selection(None)
        self.service.clear_active_profile()
        self.service.select_active_profile(self.profile)
        with self.assertRaises(CalibrationProfileError):
            self.lane.apply_selection(preview)
        preview = self.lane.preview_selection(None)
        self.port.stop = lambda: self.port.current
        self.owner.stop()
        with self.assertRaises(CalibrationProfileError):
            self.lane.apply_selection(preview)
        preview = self.lane.preview_selection(None)
        self.port.current = replace(self.port.current, acquisition_epoch=4)
        with self.assertRaises(CalibrationProfileError):
            self.lane.apply_selection(preview)

    def test_retired_scope_foreign_binding_and_closed_lane_refuse(self):
        preview = self.lane.preview_selection(self.profile)
        peer = self.owner.captured_calibration_lane(
            self.registry, self.fixture.facts.endpoint, self.fixture.facts.frontend)
        with self.assertRaises(CalibrationProfileError):
            peer.apply_selection(preview)
        self.registry.release(self.fixture.facts.device, self.fixture.facts.endpoint)
        self.registry.for_device(self.fixture.facts.device, self.fixture.facts.endpoint)
        with self.assertRaises(CalibrationProfileError):
            self.lane.apply_selection(preview)
        self.lane.close()
        with self.assertRaises(CalibrationProfileError):
            self.lane.preview_selection(self.profile)

    def test_same_rx_panes_share_command_revision_but_other_service_receipt_refuses(self):
        peer = self.owner.captured_calibration_lane(self.registry,
            replace(self.fixture.facts.endpoint, endpoint_id='other-pane'), self.fixture.facts.frontend)
        preview = self.lane.preview_selection(self.profile)
        peer.apply_selection(peer.preview_selection(self.profile))
        with self.assertRaises(CalibrationProfileError):
            self.lane.apply_selection(preview)
        foreign = ReceiverCalibrationRegistry(self.registry.store).for_device(
            self.fixture.facts.device, self.fixture.facts.endpoint)
        with self.assertRaises(CalibrationProfileError):
            self.service.apply_checked_selection(self.profile, self.fixture.signature(), foreign.selection_receipt())


if __name__ == '__main__':
    unittest.main()
