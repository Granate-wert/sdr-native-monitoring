"""Actual cached application boundary with fake snapshots; no hardware qualification."""
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from sdr_monitor.application.analyzer_session import AnalyzerMode, AnalyzerPhase, AnalyzerSessionApplicationService
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.calibration import CalibrationProfileError
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from sdr_monitor.services.receiver_calibration import ReceiverCalibrationRegistry
from sdr_monitor.ui.v2.spectrum.retained_bytes import retained_arrays
from tests.test_live_calibration_owner import SnapshotPort
from tests import test_current_frame_calibration as fixtures


class CurrentCalibrationBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CurrentFrameCalibrationTests()
        self.fixture.setUp()
        self.port = SnapshotPort(self.fixture.current)
        self.owner = LiveSessionApplicationService(self.port)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.registry = ReceiverCalibrationRegistry(CalibrationProfileStore(Path(folder.name)))
        self.frontend = self.fixture.facts.frontend

    def test_actual_producer_endpoint_and_no_array_retention_or_activation(self):
        binding = self.owner.current_calibration_binding(self.frontend)
        self.assertEqual(self.port.reads, 1)
        self.assertEqual(binding.endpoint.endpoint_id, self.fixture.frame.source_id)
        self.assertEqual(binding.endpoint.source_id, self.fixture.current.device.device_id)
        self.assertIs(binding.endpoint.selection, ReceiverChainSelection.RX1)
        self.assertEqual(retained_arrays(binding), {})
        lane = self.owner.captured_bound_calibration_lane(self.registry, binding)
        publication = lane.correct(lane.capture(lambda _: True))
        self.assertEqual(publication.analytical.result.unit, 'dBFS/bin')
        self.assertIs(publication.analytical.raw, self.fixture.frame)

    def test_exact_rx2_not_caption_suffix_and_noncanonical_refuses(self):
        frame = replace(self.fixture.frame, receiver_id='RX2', source_id='actual-second-producer')
        self.port.current = replace(self.fixture.current, spectrum=frame, receiver_id='RX2',
                                    active_source_id=frame.source_id,
                                    device=replace(self.fixture.current.device, label='RX1 misleading caption'))
        binding = self.owner.current_calibration_binding(self.frontend)
        self.assertIs(binding.endpoint.selection, ReceiverChainSelection.RX2)
        self.owner.captured_bound_calibration_lane(self.registry, binding)
        for value in (None, 'rx2', 'BOTH', 'RX1-suffix'):
            self.port.current = replace(self.port.current, receiver_id=value)
            with self.subTest(value=value), self.assertRaises(CalibrationProfileError):
                self.owner.current_calibration_binding(self.frontend)

    def test_same_context_frame_advancement_keeps_binding_and_lane(self):
        binding = self.owner.current_calibration_binding(self.frontend)
        lane = self.owner.captured_bound_calibration_lane(self.registry, binding)
        frame = replace(self.fixture.frame, sequence=2)
        self.port.current = replace(self.fixture.current, spectrum=frame, sequence=2)
        self.assertEqual(self.owner.current_calibration_binding(self.frontend), binding)
        self.assertIs(lane.capture(lambda _: True).captured.raw, frame)

    def test_session_epoch_source_config_and_control_changes_refuse_before_admission(self):
        binding = self.owner.current_calibration_binding(self.frontend)
        lane = self.owner.captured_bound_calibration_lane(self.registry, binding)
        for changes in (dict(session_id='new-session'), dict(acquisition_epoch=4),
                        dict(active_source_id='different'), dict(active_config_generation=8),
                        dict(state=LiveSessionState.CONNECTED)):
            self.port.current = replace(self.fixture.current, **changes)
            calls = []
            with self.subTest(changes=changes), self.assertRaises(CalibrationProfileError):
                lane.capture(lambda handle: calls.append(handle) or True)
            self.assertEqual(calls, [])
            with self.assertRaises(CalibrationProfileError):
                self.owner.captured_bound_calibration_lane(self.registry, binding)
        self.port.current = self.fixture.current
        self.owner._analytical_control_revision += 1
        with self.assertRaises(CalibrationProfileError):
            lane.capture(lambda _: True)

    def test_foreign_owner_missing_frame_and_nonordinary_modes_refuse(self):
        binding = self.owner.current_calibration_binding(self.frontend)
        peer = LiveSessionApplicationService(SnapshotPort(self.fixture.current))
        with self.assertRaises(CalibrationProfileError):
            peer.captured_bound_calibration_lane(self.registry, binding)
        self.port.current = replace(self.fixture.current, spectrum=None)
        with self.assertRaises(CalibrationProfileError):
            self.owner.current_calibration_binding(self.frontend)
        self.port.current = self.fixture.current
        self.owner._pane_control_claim = object()
        with self.assertRaises(CalibrationProfileError):
            self.owner.current_calibration_binding(self.frontend)
        self.owner._pane_control_claim = None
        self.owner._analyzer = SimpleNamespace(state=SimpleNamespace(mode=AnalyzerMode.SWEEP))
        with self.assertRaises(CalibrationProfileError):
            self.owner.current_calibration_binding(self.frontend)

    def test_actual_running_analyzer_cleanup_flag_allows_capture_but_rf_failure_refuses(self):
        self.port.is_running = lambda: False
        self.port.start = lambda: self.port.current
        analyzer = AnalyzerSessionApplicationService(self.port, SimpleNamespace())
        self.owner = LiveSessionApplicationService(self.port, analyzer=analyzer)
        analyzer.start()
        self.assertTrue(self.owner.current_snapshot().stop_required)
        binding = self.owner.current_calibration_binding(self.frontend)
        lane = self.owner.captured_bound_calibration_lane(self.registry, binding)
        publication = lane.correct(lane.capture(lambda _: True))
        self.assertTrue(lane.is_valid(publication))
        for phase in (AnalyzerPhase.STARTING, AnalyzerPhase.STOPPING, AnalyzerPhase.ERROR):
            analyzer._state = replace(analyzer._state, phase=phase)
            with self.subTest(phase=phase):
                self.assertFalse(lane.is_valid(publication))
                with self.assertRaises(CalibrationProfileError):
                    self.owner.current_calibration_binding(self.frontend)
        analyzer._state = replace(analyzer._state, phase=AnalyzerPhase.RUNNING)
        analyzer._idle_control_active = True
        self.assertFalse(lane.is_valid(publication))
        analyzer._idle_control_active = False
        self.owner._rf_receipt_failed = True
        self.assertFalse(lane.is_valid(publication))
        with self.assertRaises(CalibrationProfileError):
            self.owner.current_calibration_binding(self.frontend)


if __name__ == '__main__':
    unittest.main()
