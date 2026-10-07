"""Exact exposed-backing admission before correction; fake source only."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.calibration import CalibrationProfileError
from sdr_monitor.domain.live import LivePersistenceFrame
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from sdr_monitor.services.receiver_calibration import ReceiverCalibrationRegistry
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.retained_bytes import retained_arrays
from tests import test_current_frame_calibration as fixtures
from tests.test_live_calibration_owner import SnapshotPort


class CalibrationCaptureAdmissionTests(unittest.TestCase):
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

    def test_large_backing_slice_refuses_before_correction_and_no_charge(self):
        backing = np.full(10000, -20, dtype=np.float32)
        frame = replace(self.fixture.frame, values=backing[:3])
        self.port.current = replace(self.fixture.current, spectrum=frame)
        budget = PresentationAllocationBudget(1024)
        with patch('sdr_monitor.services.calibration_service.apply_calibration') as correction:
            with self.assertRaisesRegex(CalibrationProfileError, 'memory admission refused'):
                self.lane.capture(budget.admit_sources)
            correction.assert_not_called()
        self.assertGreater(sum(retained_arrays(frame).values()), 1024)
        self.assertEqual(budget.snapshot().observed_bytes, 0)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_capture_budget_correct_commit_exact_raw_no_recapture_and_dedup(self):
        budget = PresentationAllocationBudget(1024)
        handle = self.lane.capture(budget.admit_sources)
        before = budget.snapshot().observed_bytes
        self.assertEqual(before, sum(retained_arrays(self.fixture.frame).values()))
        self.assertEqual(handle.derived_output_bytes, 48)
        newer = replace(self.fixture.frame, sequence=2)
        self.port.current = replace(self.fixture.current, spectrum=newer, sequence=2)
        with budget.reserve(handle.derived_output_bytes, handle) as reservation:
            publication = self.lane.correct(handle)
            reservation.commit(publication)
        self.assertIs(publication.analytical.raw, self.fixture.frame)
        self.assertIsNot(publication.analytical.raw, newer)
        self.assertTrue(self.lane.admit_delivery(publication))
        self.assertEqual(budget.snapshot().observed_bytes, before + 48)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.assertEqual(sum(retained_arrays(handle, publication).values()), before + 48)

    def test_handle_does_not_retain_snapshot_persistence(self):
        density = LivePersistenceFrame(update_sequence=1, timestamp_ns=100, source_frame_sequence=1,
            power_min_db=-120, power_max_db=-30, power_bins=128, frequency_bins=3,
            processed_frames=1, exponential_decay=True,
            frequencies_hz=self.fixture.frame.frequencies_hz, density=np.ones((128, 3), dtype=np.float32))
        self.port.current = replace(self.fixture.current, persistence=density)
        handle = self.lane.capture(PresentationAllocationBudget(1024).admit_sources)
        self.assertEqual(retained_arrays(handle), retained_arrays(self.fixture.frame))
        self.assertGreater(sum(retained_arrays(self.port.current).values()), sum(retained_arrays(handle).values()))

    def test_aba_close_and_foreign_handle_refuse_before_math_and_release_reservation(self):
        budget = PresentationAllocationBudget(1024)
        handle = self.lane.capture(budget.admit_sources)
        self.service.clear_active_profile()
        self.service.select_active_profile(self.profile)
        with budget.reserve(handle.derived_output_bytes, handle), patch(
                'sdr_monitor.services.calibration_service.apply_calibration') as correction:
            with self.assertRaises(CalibrationProfileError):
                self.lane.correct(handle)
            correction.assert_not_called()
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        handle = self.lane.capture(budget.admit_sources)
        peer = self.owner.captured_calibration_lane(
            self.registry, self.fixture.facts.endpoint, self.fixture.facts.frontend)
        with self.assertRaises(CalibrationProfileError):
            peer.correct(handle)
        self.lane.close()
        with self.assertRaises(CalibrationProfileError):
            self.lane.correct(handle)

    def test_output_bound_covers_raw_fallback_and_unexpected_layout_refuses(self):
        budget = PresentationAllocationBudget(1024)
        self.service.clear_active_profile()
        handle = self.lane.capture(budget.admit_sources)
        result = self.lane.correct(handle)
        self.assertEqual(result.analytical.result.unit, 'dBFS/bin')
        self.assertEqual(result.analytical.result.values.nbytes + result.analytical.result.uncertainty_db.nbytes,
                         handle.derived_output_bytes)
        wrong = replace(result.analytical.result, values=np.ones(30, dtype=np.float64))
        with budget.reserve(handle.derived_output_bytes, handle), patch(
                'sdr_monitor.services.calibration_service.apply_calibration', return_value=wrong):
            with self.assertRaisesRegex(CalibrationProfileError, 'declared owned-array layout'):
                self.lane.correct(handle)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_callback_control_change_refuses_and_computation_error_releases(self):
        budget = PresentationAllocationBudget(1024)

        def changed(handle):
            self.port.stop = lambda: self.port.current
            self.owner.stop()
            return budget.admit_sources(handle)

        with self.assertRaisesRegex(CalibrationProfileError, 'expired during admission'):
            self.lane.capture(changed)
        handle = self.lane.capture(budget.admit_sources)
        with budget.reserve(handle.derived_output_bytes, handle), patch(
                'sdr_monitor.services.calibration_service.apply_calibration', side_effect=RuntimeError('injected')):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                self.lane.correct(handle)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)


if __name__ == '__main__':
    unittest.main()
