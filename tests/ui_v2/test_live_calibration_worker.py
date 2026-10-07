"""Budget, cancellation and single-flight tests on existing owner worker."""
from dataclasses import replace
from threading import Event, get_ident
from time import monotonic, sleep
import weakref
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.state.prepared_calibration import prepare_calibrated_current
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.live import LivePersistenceFrame
from sdr_monitor.ui.presenters.live_calibration_presenter import LiveCalibrationPresenter
from sdr_monitor.ui.v2.view_models.live_calibration_view_model import LiveCalibrationViewModel
from tests.test_live_calibration_owner import SnapshotPort
from tests.ui_v2.test_live_calibration_composition import CalibrationCompositionFixture


class LiveCalibrationWorkerTests(CalibrationCompositionFixture):
    def lane(self):
        owner = self.presenter.owner
        binding = owner.current_calibration_binding(self.frontend)
        return owner.captured_bound_calibration_lane(self.registry, binding)

    def test_large_backing_slice_denied_before_math_and_reservation_exhaustion(self):
        backing = np.arange(100000., dtype=np.float32)
        backing.setflags(write=False)
        frame = replace(self.facts.frame, values=backing[:3])
        self.port.current = replace(self.facts.current, spectrum=frame)
        lane = self.lane()
        with patch.object(lane, "correct", wraps=lane.correct) as math:
            with self.assertRaisesRegex(ValueError, "memory admission refused"):
                prepare_calibrated_current(lane, PresentationAllocationBudget(1000))
            math.assert_not_called()
        self.port.current = self.facts.current
        lane = self.lane()
        # 48 raw bytes fit; 48 declared outputs do not. No math on denial.
        budget = PresentationAllocationBudget(60)
        with patch.object(lane, "correct", wraps=lane.correct) as math:
            with self.assertRaisesRegex(ValueError, "allocation budget exceeded"):
                prepare_calibrated_current(lane, budget)
            math.assert_not_called()
        self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_worker_capture_exact_budget_order_and_no_snapshot_persistence_custody(self):
        self.bind()
        lane = self.presenter._lane
        ids = []
        original = lane.correct
        def correct(handle):
            ids.append(get_ident())
            self.assertFalse(hasattr(handle, "snapshot"))
            self.assertFalse(hasattr(handle.captured, "persistence"))
            self.assertGreater(self.presenter.budget.snapshot().reserved_bytes, 0)
            return original(handle)
        gui = get_ident()
        with patch.object(lane, "correct", side_effect=correct):
            old = self.model.state.current
            self.worker.render_ready.emit(self.port.current)
            self.wait(lambda: self.model.state.current is not old)
        self.assertTrue(ids)
        self.assertNotIn(gui, ids)
        self.assertEqual(self.presenter.budget.snapshot().reserved_bytes, 0)
        value = self.model.state.current.frame.publication.analytical
        self.assertIs(value.raw, self.facts.frame)
        # Source aliases/raw wrapper repeat admission without double charge.
        before = self.presenter.budget.snapshot().observed_bytes
        self.assertTrue(self.presenter.budget.admit_sources(value, value.raw))
        self.assertEqual(self.presenter.budget.snapshot().observed_bytes, before)

    def test_large_density_snapshot_is_not_retained_or_charged_to_corrected_current(self):
        density = LivePersistenceFrame(1, 100, 1, -100., 0., 256, 3, 1, False,
                                       self.facts.frame.frequencies_hz, np.ones((256, 3), dtype=np.float32))
        self.port.current = replace(self.facts.current, persistence=density)
        snapshot = self.port.current
        lane = self.lane()
        budget = PresentationAllocationBudget(128)
        result = prepare_calibrated_current(lane, budget)
        self.assertEqual(budget.snapshot().observed_bytes, 108)
        self.assertIs(result.frame.publication.analytical.raw, self.facts.frame)
        self.assertIs(self.port.current, snapshot)
        self.assertIs(self.port.current.persistence, density)

    def test_one_active_through_queued_gui_ack_latest_pending_request_only(self):
        self.bind()
        lane = self.presenter._lane
        started, release = Event(), Event()
        original = lane.correct
        def held(handle):
            started.set()
            if not release.wait(3):
                raise RuntimeError("test hold timed out")
            return original(handle)
        with patch.object(lane, "correct", side_effect=held):
            self.presenter.request_current()
            self.assertTrue(started.wait(1))
            future = self.presenter._active
            for _ in range(100):
                self.presenter.request_current()
            self.assertIs(self.presenter._active, future)
            self.assertEqual(self.presenter._pending.phase, "current")
            self.assertFalse(hasattr(self.presenter._pending, "captured"))
            release.set()
            deadline = monotonic() + 3
            while not future.done() and monotonic() < deadline:
                sleep(.001)
            self.assertTrue(future.done())
            # Completed worker does not release the slot until queued GUI ack.
            self.assertIs(self.presenter._active, future)
            self.wait(lambda: self.presenter._active is None and self.presenter._pending is None)
        reference = weakref.ref(future)
        del future
        self.assertIsNone(reference())  # No forced GC or another command.

    def test_cancel_and_failure_release_reservation_and_late_close_never_republishes(self):
        self.bind()
        lane = self.presenter._lane
        with patch.object(lane, "correct", side_effect=RuntimeError("math failed")):
            self.presenter.request_current()
            self.wait(lambda: self.model.state.error is not None)
        self.assertEqual(self.presenter.budget.snapshot().reserved_bytes, 0)
        started, release = Event(), Event()
        original = lane.correct
        def held(handle):
            started.set()
            release.wait(3)
            return original(handle)
        with patch.object(lane, "correct", side_effect=held):
            self.presenter.request_current()
            self.assertTrue(started.wait(1))
            future = self.presenter._active
            self.model.close_binding()
            self.assertIsNone(self.model.state.current)
            release.set()
            self.wait(lambda: self.presenter._active is None)
            self.assertIsNone(self.model.state.current)
        self.assertEqual(self.presenter.budget.snapshot().reserved_bytes, 0)
        reference = weakref.ref(future)
        del future
        self.assertIsNone(reference())

    def test_accepted_control_has_priority_and_pending_bind_is_array_free(self):
        started, release = Event(), Event()
        def control():
            started.set()
            if not release.wait(3):
                raise RuntimeError("test control hold timed out")
            return None
        self.worker._submit(control, lambda _: None, force_gui=True)
        self.assertTrue(started.wait(1))
        reads = self.port.reads
        self.model.bind(self.frontend)
        self.assertIsNone(self.presenter._active)
        self.assertEqual(self.port.reads, reads)
        self.assertEqual(self.presenter._pending.phase, "bind")
        release.set()
        self.wait(lambda: self.model.state.current is not None or self.model.state.error is not None)
        self.assertIsNone(self.model.state.error)

    def test_reentrant_gui_ack_cannot_release_active_slot_or_recapture_pending(self):
        self.bind()
        previous = self.model.state.current
        observed = []
        def reenter(state):
            if state.current is not None and state.current is not previous and not observed:
                active = self.presenter._active
                self.presenter.request_current()
                observed.append((active is self.presenter._active, self.presenter._pending.phase))
        unsubscribe = self.model.subscribe(reenter)
        try:
            self.presenter.request_current()
            self.wait(lambda: bool(observed))
            self.assertEqual(observed, [(True, "current")])
            self.wait(lambda: self.presenter._active is None and self.presenter._pending is None)
        finally:
            unsubscribe()

    def test_completed_bind_future_and_disposed_owner_release_without_gc_or_later_command(self):
        self.wait(lambda: self.worker.calibration_worker_ready)
        owner = LiveSessionApplicationService(SnapshotPort(self.facts.current))
        owner_ref = weakref.ref(owner)
        presenter = LiveCalibrationPresenter(owner, self.registry, self.worker, self.presenter.budget)
        model = LiveCalibrationViewModel(presenter)
        model.bind(self.frontend)
        future = presenter._active
        deadline = monotonic() + 3
        while not future.done() and monotonic() < deadline:
            sleep(.001)
        self.assertTrue(future.done())
        # GUI acknowledgement is intentionally not pumped. Queued notification
        # carries only a scalar; disposing releases the Future/owner immediately.
        model.dispose()
        future_ref = weakref.ref(future)
        del owner, future
        self.assertIsNone(future_ref())
        self.assertIsNone(owner_ref())
        self.app.processEvents()
        self.assertIsNone(model.state.binding)
