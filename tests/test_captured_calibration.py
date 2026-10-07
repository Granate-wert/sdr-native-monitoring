"""Actual application-owned captured analytics; fake RX, no RF claims."""
from dataclasses import replace
from pathlib import Path
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.calibration import CalibrationProfileError
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from sdr_monitor.services.receiver_calibration import ReceiverCalibrationRegistry
from tests.test_live_calibration_owner import SnapshotPort
from tests import test_current_frame_calibration as fixtures


class CapturedCalibrationTests(unittest.TestCase):
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
        self.service.set_current_settings(self.profile.signature)
        self.service.select_active_profile(self.profile)
        self.lane = self.owner.captured_calibration_lane(
            self.registry, self.fixture.facts.endpoint, self.fixture.facts.frontend)

    def advance(self):
        frame = replace(self.port.current.spectrum, sequence=self.port.current.spectrum.sequence + 1)
        self.port.current = replace(self.port.current, spectrum=frame, sequence=frame.sequence)

    def test_fast_same_context_advancement_during_math_and_delivery_still_renders(self):
        from sdr_monitor.services import calibration_service as module
        original = module.apply_calibration

        def advance_during_math(*args, **kwargs):
            for _ in range(20):
                self.advance()
            return original(*args, **kwargs)

        with patch.object(module, 'apply_calibration', side_effect=advance_during_math):
            result = self.lane.prepare()
        self.assertIs(result.analytical.raw, self.fixture.frame)
        self.assertIsNot(result.analytical.raw, self.port.current.spectrum)
        self.advance()
        self.assertTrue(self.lane.admit_delivery(result))
        self.assertTrue(self.lane.is_valid(result))
        self.assertEqual(result.analytical.result.unit, 'dBm/bin')

    def test_binding_is_inert_and_pending_control_observation_does_not_wait(self):
        self.assertEqual(self.port.reads, 0)
        ready, release = Event(), Event()

        def hold_control():
            with self.owner._pane_application_lock:
                ready.set()
                release.wait(3)

        thread = Thread(target=hold_control)
        thread.start()
        try:
            self.assertTrue(ready.wait(1))
            with self.assertRaisesRegex(CalibrationProfileError, 'control is pending'):
                self.lane.prepare()
            self.assertEqual(self.port.reads, 0)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())

    def test_stop_retune_epoch_source_and_discontinuity_expire_captured_result(self):
        for changes in (dict(state=LiveSessionState.CONNECTED), dict(acquisition_epoch=4),
                        dict(session_id='another'), dict(active_source_id='another'),
                        dict(stop_required=True), dict(applied=replace(self.fixture.facts.applied,
                            applied=replace(self.fixture.facts.applied.applied, center_hz=123456789)))):
            self.port.current = self.fixture.current
            result = self.lane.prepare()
            self.port.current = replace(self.fixture.current, **changes)
            with self.subTest(changes=changes):
                self.assertFalse(self.lane.is_valid(result))
        self.port.current = self.fixture.current
        result = self.lane.prepare()
        self.port.current = replace(self.fixture.current, spectrum=replace(self.fixture.frame, backend_discontinuity=True))
        self.assertFalse(self.lane.is_valid(result))

    def test_actual_stop_command_invalidates_even_same_snapshot_aba(self):
        result = self.lane.prepare()
        self.port.stop = lambda: self.fixture.current
        self.owner.stop()
        self.assertFalse(self.lane.is_valid(result))
        self.assertTrue(self.lane.is_valid(self.lane.prepare()))

    def test_registry_retirement_selection_aba_and_close_refuse(self):
        result = self.lane.prepare()
        self.service.clear_active_profile()
        self.service.select_active_profile(self.profile)
        self.assertFalse(self.lane.is_valid(result))
        result = self.lane.prepare()
        self.registry.release(self.fixture.facts.device, self.fixture.facts.endpoint)
        self.service.select_active_profile(self.profile)
        self.assertFalse(self.lane.is_valid(result))
        self.lane.close()
        with self.assertRaises(CalibrationProfileError):
            self.lane.prepare()

    def test_gap_observations_and_changed_frontend_binding_refuse(self):
        result = self.lane.prepare()
        self.port.current = replace(self.fixture.current, performance=replace(
            self.fixture.current.performance, source_sample_index_discontinuities=1))
        self.assertFalse(self.lane.is_valid(result))
        self.port.current = self.fixture.current
        peer = self.owner.captured_calibration_lane(self.registry, self.fixture.facts.endpoint,
            replace(self.fixture.facts.frontend, reference_plane='new-plane'))
        self.assertFalse(peer.is_valid(result))

    def test_selection_change_during_math_and_reserved_graph_refuse(self):
        from sdr_monitor.services import calibration_service as module
        original = module.apply_calibration

        def changed(*args, **kwargs):
            self.service.clear_active_profile()
            self.service.select_active_profile(self.profile)
            return original(*args, **kwargs)

        with patch.object(module, 'apply_calibration', side_effect=changed):
            with self.assertRaises(CalibrationProfileError):
                self.lane.prepare()
        result = self.lane.prepare()
        self.owner._pane_control_claim = object()
        self.assertFalse(self.lane.is_valid(result))
        with self.assertRaises(CalibrationProfileError):
            self.lane.prepare()

    def test_binding_authority_and_delivery_order_no_rollback_or_duplicate(self):
        first = self.lane.prepare()
        self.advance()
        second = self.lane.prepare()
        peer = self.owner.captured_calibration_lane(
            self.registry, self.fixture.facts.endpoint, self.fixture.facts.frontend)
        self.assertFalse(peer.is_valid(second))
        self.assertTrue(self.lane.admit_delivery(second))
        self.assertFalse(self.lane.is_valid(first))
        self.assertFalse(self.lane.admit_delivery(first))
        self.assertFalse(self.lane.admit_delivery(second))

    def test_strict_pull_still_refuses_new_frame_during_math(self):
        from sdr_monitor.services import calibration_service as module
        original = module.apply_calibration

        def changed(*args, **kwargs):
            self.advance()
            return original(*args, **kwargs)

        with patch.object(module, 'apply_calibration', side_effect=changed):
            with self.assertRaises(CalibrationProfileError):
                self.owner.calibrated_receiver_spectrum(
                    self.registry, self.fixture.facts.endpoint, self.fixture.facts.frontend)


if __name__ == '__main__':
    unittest.main()
