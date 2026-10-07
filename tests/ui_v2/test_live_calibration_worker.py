"""Budget, cancellation and single-flight tests on existing owner worker."""
from dataclasses import replace
from concurrent.futures import Future
from threading import Event, get_ident
from time import monotonic, sleep
import weakref
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.state.prepared_calibration import prepare_calibrated_current
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.live import LivePersistenceFrame
from sdr_monitor.domain.calibration import CalibrationPoint
from sdr_monitor.ui.presenters.live_calibration_presenter import LiveCalibrationPresenter
from sdr_monitor.ui.v2.view_models.live_calibration_view_model import LiveCalibrationViewModel
from tests.test_live_calibration_owner import SnapshotPort
from tests.ui_v2.test_live_calibration_composition import CalibrationCompositionFixture


class LiveCalibrationWorkerTests(CalibrationCompositionFixture):
    def exercise_saturated_raw(self, visible):
        # The analytical fixture's three-bin input is intentionally sufficient
        # for correction only. Normal raw admission requires the FULL physical
        # Fs/FFT grid; do not count refused raw packets as renderer progress.
        frame = self.facts.frame
        grid = frame.center_frequency_hz + (np.arange(frame.fft_size) - frame.fft_size // 2) * (
            frame.sample_rate_hz / frame.fft_size)
        self.facts.frame = replace(frame, frequencies_hz=grid, values=np.full(frame.fft_size, -30.))
        self.facts.current = replace(self.facts.current, spectrum=self.facts.frame)
        self.port.current = self.facts.current
        if visible:
            self.shell.select_workspace("analyzer")
            self.shell.resize(1920, 1080)
            self.shell.show()
            self.app.processEvents()
        worker = self.worker
        original_prepare, original_deliver = worker._prepare, worker._deliver_prepared
        started, release = Event(), Event()
        first = True
        replenish = True
        raw_count = 0
        current_ids = set()
        submit_with_raw_pending = []
        bypasses = []
        offered_at = 0
        projection_releases = []
        current_projection_activity = []
        def prepare(*args):
            nonlocal first
            if first:
                first = False
                started.set()
                if not release.wait(3):
                    raise RuntimeError("initial raw hold timed out")
            return original_prepare(*args)
        def deliver(value):
            nonlocal raw_count
            self.assertIsNone(value.error)
            self.assertIsNotNone(value.value.analyzer_bundle)
            original_deliver(value)
            if value.render:
                raw_count += 1
                if replenish:
                    frame = replace(self.facts.frame, sequence=raw_count + 1)
                    self.port.current = replace(self.facts.current, spectrum=frame, sequence=raw_count + 1)
                    worker._offer_preparation(self.port.current, worker._control_revision)
        def observe(state):
            if state.current is not None:
                current_ids.add(id(state.current))
        original_submit = worker.submit_calibration_task
        original_offer = self.presenter._offer
        def offer(request):
            nonlocal offered_at
            offered_at = raw_count
            return original_offer(request)
        def submit(operation):
            submit_with_raw_pending.append(worker._pending_preparation is not None)
            bypasses.append(raw_count - offered_at)
            return original_submit(operation)
        original_projection = worker.set_projection_in_flight
        def projection(active):
            if not active:
                projection_releases.append(raw_count)
            return original_projection(active)
        unsubscribe = self.model.subscribe(observe)
        try:
            with patch.object(worker, "_prepare", side_effect=prepare), \
                 patch.object(worker, "_deliver_prepared", side_effect=deliver), \
                 patch.object(worker, "submit_calibration_task", side_effect=submit), \
                 patch.object(self.presenter, "_offer", side_effect=offer), \
                 patch.object(worker, "set_projection_in_flight", side_effect=projection):
                # Composition's existing bound backpressure callback is already
                # connected; inspect actual aggregate activity too, not readiness mocks.
                projector = self.composition.spectrum_projector
                def active_changed(active):
                    if not active:
                        projection_releases.append(raw_count)
                projector.work_active_changed.connect(active_changed)
                current_projector = self.composition._calibration_projector
                current_projector.work_active_changed.connect(current_projection_activity.append)
                try:
                    worker._offer_preparation(self.port.current, worker._control_revision)
                    self.assertTrue(started.wait(1))
                    self.model.bind(self.frontend)
                    self.assertEqual(self.presenter._pending.phase, "bind")
                    release.set()
                    self.wait(lambda: self.model.state.current is not None)
                    profile = self.facts.profile(self.model.state.binding.signature)
                    profile = replace(profile, valid_start_hz=None, valid_stop_hz=None,
                                      points=(CalibrationPoint(float(grid[0]), 1., .2),
                                                       CalibrationPoint(float(grid[-1]), 2., .3)))
                    self.model.preview(profile)
                    self.wait(lambda: self.model.state.preview is not None and not self.model.state.busy)
                    self.model.select()
                    self.wait(lambda: self.model.state.acknowledged == "select" and self.model.state.current is not None)
                    before = len(current_ids)
                    self.wait(lambda: len(current_ids) >= before + 3)
                    self.model.clear()
                    self.wait(lambda: self.model.state.acknowledged == "clear" and self.model.state.current is not None)
                    self.assertEqual(self.model.state.current.frame.unit, "dBFS/bin")
                    self.assertGreaterEqual(raw_count, 4)
                    self.assertGreaterEqual(len(submit_with_raw_pending), 8)
                    self.assertTrue(all(submit_with_raw_pending))
                    self.assertTrue(all(0 <= n <= 1 for n in bypasses), bypasses)
                    if visible:
                        self.assertTrue(projection_releases)
                        scene = self.composition._analyzer_workspace_ref().visualization.spectrum_scene
                        self.assertTrue(scene._presentation_active)
                        self.assertIsNotNone(scene.displayed_frame)
                        self.assertIn(True, current_projection_activity)
                        self.assertIn(False, current_projection_activity)
                    self.assertFalse(hasattr(self.presenter._pending, "captured"))
                finally:
                    projector.work_active_changed.disconnect(active_changed)
                    current_projector.work_active_changed.disconnect(current_projection_activity.append)
                    replenish = False
                    release.set()
                self.wait(lambda: self.presenter._active is None and self.presenter._pending is None
                          and worker._preparation_future is None and not worker._projection_in_flight)
        finally:
            replenish = False
            release.set()
            unsubscribe()

    def test_commands_and_current_progress_with_replenished_raw_preparation(self):
        self.exercise_saturated_raw(False)

    def test_commands_and_current_progress_with_actual_projection_backlog(self):
        self.exercise_saturated_raw(True)

    def test_pending_bind_gets_first_replenished_raw_release(self):
        previous = Future()
        self.worker._preparation_future = previous
        try:
            self.model.bind(self.frontend)
            self.assertIsNone(self.presenter._active)
            self.worker._pending_preparation = (self.port.current, self.worker._control_revision, True)
            with patch.object(self.worker, "_deliver_prepared"), patch.object(
                self.worker._executor, "submit", side_effect=lambda *args, **kwargs: Future()
            ):
                previous.set_result(None)
                self.worker._finish_preparation(previous)
                self.assertIsNotNone(self.presenter._active)
                self.assertIsNone(self.worker._preparation_future)
                self.assertIsNotNone(self.worker._pending_preparation)
        finally:
            # Only diagnostic-owned, unsubmitted Futures; no real job abandoned.
            active = self.presenter._active
            if active is not None:
                active.cancel()
            self.worker._preparation_future = None
            self.worker._pending_preparation = None
            self.worker._active_preparation_snapshot = None
            self.model.close_binding()
            self.app.processEvents()

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
            serial = self.worker.calibration_slot_serial
            for _ in range(100):
                self.worker._offer_preparation(self.port.current, self.worker._control_revision)
            self.assertIsNone(self.worker._preparation_future)
            self.assertTrue(self.worker._calibration_in_flight)
            self.worker.release_calibration_slot(serial - 1)
            self.assertTrue(self.worker._calibration_in_flight)
            self.assertIsNotNone(self.worker._pending_preparation)
            self.wait(lambda: self.presenter._active is None and self.presenter._pending is None)
        reference = weakref.ref(future)
        del future
        self.assertIsNone(reference())  # No forced GC or another command.

    def test_dispose_running_completion_releases_exact_slot_once_without_later_command(self):
        self.bind()
        self.model.close_binding()
        presenter = LiveCalibrationPresenter(self.presenter.owner, self.registry, self.worker, self.presenter.budget)
        model = LiveCalibrationViewModel(presenter)
        model.bind(self.frontend)
        self.wait(lambda: model.state.current is not None and presenter._active is None)
        lane = presenter._lane
        started, release = Event(), Event()
        original = lane.correct
        def held(handle):
            started.set()
            if not release.wait(3):
                raise RuntimeError("test hold timed out")
            return original(handle)
        with patch.object(lane, "correct", side_effect=held):
            presenter.request_current()
            self.assertTrue(started.wait(1))
            future = presenter._active
            serial = self.worker.calibration_slot_serial
            model.dispose()
            self.assertTrue(self.worker._calibration_in_flight)
            release.set()
            self.wait(lambda: not self.worker._calibration_in_flight)
            self.worker.release_calibration_slot(serial)  # Duplicate stale notification is inert.
            self.assertFalse(self.worker._calibration_in_flight)
            self.assertEqual(self.worker.calibration_slot_serial, serial)
        reference = weakref.ref(future)
        del future
        self.assertIsNone(reference())

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
