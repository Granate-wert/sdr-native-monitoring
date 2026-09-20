"""Control priority and cooperative cancellation, no RX or new worker owner."""
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum import envelope, sweep_coverage, projection
from sdr_monitor.ui.v2.spectrum.contracts import SpectrumFrameView, TraceKind, finite_value_extent
from sdr_monitor.ui.v2.spectrum.projection import ProjectionRequest, SpectrumProjector
from tests.ui_v2.test_app04_sweep_coverage import line, snapshot
from tests.ui_v2.test_app05_viewport_projection import ManualWorker


def request(owner=None, generation=1):
    frequencies = np.arange(200003, dtype=np.float64)
    values = np.sin(frequencies).astype(np.float32)
    frequencies.setflags(write=False)
    values.setflags(write=False)
    view = SpectrumFrameView(object(), frequencies, values, "dBFS/bin")
    return ProjectionRequest(owner or object(), generation, (0, 200003, 1000), ((TraceKind.CURRENT, view),))


class ProjectionCancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pump(self):
        for _ in range(10):
            self.app.processEvents()

    def test_each_large_reducer_stops_at_next_batch_without_partial_result(self):
        value = request().traces[0][1]
        cancel = threading.Event()
        original = envelope.extrema_rows
        calls = []

        def reduce(*args, **kwargs):
            calls.append(args[1].size)
            result = original(*args, **kwargs)
            cancel.set()
            return result

        with patch.object(envelope, "extrema_rows", side_effect=reduce):
            with self.assertRaises(CancelledError):
                envelope.peak_preserving_envelope(value, 1000, cancelled=cancel.is_set)
        self.assertEqual(len(calls), 1)
        self.assertLessEqual(calls[0], 65536)
        cancel.clear()
        calls.clear()
        state = sweep_coverage.SweepCoverageState()
        state.accept(snapshot(line(1, value.values)))
        state.accept(snapshot(line(2, np.full(value.point_count, np.nan))))
        with patch.object(sweep_coverage, "extrema_rows", side_effect=reduce):
            with self.assertRaises(CancelledError):
                state.project(0, 1e12, 1000, cancelled=cancel.is_set)
        self.assertEqual(len(calls), 1)
        checks = []

        def after_first():
            checks.append(True)
            return len(checks) == 2

        with self.assertRaises(CancelledError):
            finite_value_extent(value.values, cancelled=after_first)
        self.assertEqual(len(checks), 2)

    def test_queued_work_cancelled_control_suspends_and_resumes_latest_only(self):
        worker = ManualWorker()
        port = SpectrumProjector(worker.submit)
        self.addCleanup(port.dispose)
        first = request()
        port.offer(first)
        port.set_suspended(True)
        port.set_suspended(True)
        for _ in range(100):
            latest = replace(first, traces=request().traces)
            port.offer(latest)
        self.assertTrue(worker.jobs[0][0].cancelled())
        self.assertEqual(len(worker.jobs), 1)
        worker.finish()
        self.pump()
        self.assertEqual(port.cancelled, 1)
        self.assertEqual(worker.jobs, [])
        port.set_suspended(False)
        self.assertIs(port._active, latest)
        self.assertEqual(len(worker.jobs), 1)
        delivered, failures = [], []
        port.ready.connect(delivered.append)
        port.failed.connect(lambda *args: failures.append(args))
        worker.finish()
        self.pump()
        self.assertEqual(len(delivered), 1)
        self.assertIs(delivered[0].request, latest)
        self.assertEqual(failures, [])

    def test_newer_same_viewport_does_not_cancel_running_work_but_geometry_does(self):
        worker = ManualWorker()
        port = SpectrumProjector(worker.submit)
        self.addCleanup(port.dispose)
        first = request()
        port.offer(first)
        future = port._future
        self.assertTrue(future.set_running_or_notify_cancel())
        for _ in range(100):
            port.offer(replace(first, traces=request().traces))
        self.assertFalse(port._cancel.is_set())
        port.offer(replace(first, viewport=(1, 1000, 200)))
        self.assertTrue(port._cancel.is_set())
        future.set_exception(CancelledError())
        self.pump()
        self.assertEqual(port.cancelled, 1)

    def test_cancelled_completed_result_is_not_delivered_as_failure(self):
        worker = ManualWorker()
        port = SpectrumProjector(worker.submit)
        self.addCleanup(port.dispose)
        first = request()
        delivered, failures = [], []
        port.ready.connect(delivered.append)
        port.failed.connect(lambda *args: failures.append(args))
        port.offer(first)
        worker.finish()
        port.cancel_pending(first.owner)
        self.pump()
        self.assertEqual((delivered, failures, port.completed, port.cancelled), ([], [], 0, 1))

    def test_backpressure_releases_at_gui_ack_even_with_pending_failure_or_cancel(self):
        for outcome in ("success", "error", "cancel"):
            with self.subTest(outcome=outcome):
                worker = ManualWorker()
                port = SpectrumProjector(worker.submit)
                events = []
                port.work_active_changed.connect(events.append)
                first = request()
                port.offer(first)
                port.offer(replace(first, traces=request().traces))
                if outcome == "cancel":
                    port._cancel_active()
                worker.finish(RuntimeError("injected") if outcome == "error" else None)
                self.assertEqual(events, [True])  # worker done is NOT GUI ack
                self.pump()
                self.assertEqual(events, [True, True])  # newer prepared source has priority
                self.assertEqual(len(worker.jobs), 1)  # latest viewport still runs
                worker.finish()
                self.pump()
                self.assertEqual(events, [True, True, False])
                port.dispose()

    def test_same_source_viewport_ack_releases_preparation_before_next_projection(self):
        worker = ManualWorker()
        port = SpectrumProjector(worker.submit)
        events = []
        port.work_active_changed.connect(events.append)
        first = request()
        port.offer(first)
        port.offer(replace(first, viewport=(1, 1000, 200)))
        worker.finish()
        self.pump()
        self.assertEqual(events, [True, False, True])
        worker.finish()
        self.pump()
        self.assertEqual(events, [True, False, True, False])
        port.dispose()

    def test_pending_new_source_submit_failure_releases_backpressure(self):
        worker = ManualWorker()
        port = SpectrumProjector(worker.submit)
        events = []
        port.work_active_changed.connect(events.append)
        first = request()
        port.offer(first)
        port.offer(replace(first, traces=request().traces))
        def reject(_operation):
            raise RuntimeError("pending submit rejected")
        port._submit = reject
        worker.finish()
        self.pump()
        self.assertEqual(events, [True, False])
        self.assertIsNone(port._future)
        port.dispose()

    def test_submit_failure_never_latches_backpressure(self):
        def reject(_operation):
            raise RuntimeError("worker rejected")

        port = SpectrumProjector(reject)
        events, errors = [], []
        port.work_active_changed.connect(events.append)
        port.failed.connect(lambda *_: errors.append(True))
        port.offer(request())
        self.assertEqual(events, [])
        self.assertEqual(errors, [True])
        self.assertIsNone(port._future)
        port.dispose()

    def test_dispose_cooperatively_interrupts_real_running_batch(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        original = envelope.extrema_rows

        def reduce(*args, **kwargs):
            calls.append(True)
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test barrier expired")
            return original(*args, **kwargs)

        with ThreadPoolExecutor(max_workers=1) as worker:
            port = SpectrumProjector(worker.submit)
            with patch.object(envelope, "extrema_rows", side_effect=reduce):
                try:
                    port.offer(request())
                    self.assertTrue(entered.wait(3))
                    future = port._future
                    port.dispose()
                    release.set()
                    with self.assertRaises(CancelledError):
                        future.result(timeout=3)
                    # Future.result wakes before its done callbacks necessarily
                    # enqueue Qt acknowledgement. Join a following single-worker
                    # task before pumping; ten immediate processEvents calls
                    # alone can all run before the completion signal is emitted.
                    worker.submit(lambda: None).result(timeout=3)
                    self.pump()
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(port.cancelled, 1)
                finally:
                    release.set()
                    port.dispose()


class ActualControlPriorityTests(unittest.TestCase):
    def test_sweep_stop_suspends_same_projector_until_terminal_ack(self):
        from tests import test_app02_analyzer_workspace_product as product
        from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
        f = product.AnalyzerWorkspaceProductTests("runTest")
        f.app = QApplication.instance() or QApplication([])
        f.setUp()
        entered, release = threading.Event(), threading.Event()
        try:
            f.select_and_apply()
            f.page.mode.setCurrentIndex(f.page.mode.findData(AnalyzerMode.SWEEP))
            f.page.primary.click()
            f.wait(lambda: f.page.visualization.spectrum_scene.displayed_frame is not None)
            port = f.composition.spectrum_projector
            sweep = f.composition.analyzer_presenter
            original = sweep._service.stop

            def stop():
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test barrier expired")
                return original()

            with patch.object(sweep._service, "stop", side_effect=stop):
                f.page.primary.click()
                f.wait(entered.is_set)
                self.assertTrue(port._suspended)
                self.assertFalse(f.composition.can_close())
                release.set()
                f.wait(lambda: sweep.can_close())
                self.assertFalse(port._suspended)
                self.assertEqual(f.events, ["sweep-start", "sweep-stop"])
        finally:
            release.set()
            f.tearDown()
            f.doCleanups()

    def test_rtbw_stop_cancels_projection_before_waiting_worker_and_keeps_gui_alive(self):
        from tests import test_app02_analyzer_workspace_product as product
        from tests.ui_v2.test_app05_prepared_live import measurement
        f = product.AnalyzerWorkspaceProductTests("runTest")
        f.app = QApplication.instance() or QApplication([])
        f.setUp()
        release, entered = threading.Event(), threading.Event()
        original = projection.project_spectrum
        cancellation_seen = []

        def slow(req, *, cancelled=None):
            if not entered.is_set():
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test barrier expired")
                cancellation_seen.append(cancelled())
            return original(req, cancelled=cancelled)

        try:
            f.select_and_apply()
            f.page.primary.click()
            f.wait(lambda: f.live.is_running() and not f.composition.view_model.state.busy)
            port = f.composition.spectrum_projector
            with patch.object(projection, "project_spectrum", side_effect=slow):
                state = measurement(f)
                f.live._snapshot = state
                f.presenter.offer_snapshot_for_render(state)
                f.wait(entered.is_set)
                f.page.primary.click()
                self.assertTrue(port._suspended)
                self.assertTrue(port._cancel.is_set())
                self.assertTrue(f.composition.view_model.state.busy)
                self.assertFalse(f.composition.can_close())
                # A blocked numerical batch does not hold the GUI call stack.
                f.app.processEvents()
                release.set()
                f.wait(lambda: not f.live.is_running() and not f.composition.view_model.state.busy)
                f.wait(lambda: port.cancelled > 0)
                self.assertEqual(cancellation_seen, [True])
                self.assertFalse(port._suspended)
                self.assertEqual(f.events, ["rtbw-start", "rtbw-stop"])
                self.assertNotIn("obsolete spectrum", f.page.error.text())
        finally:
            release.set()
            f.tearDown()
            f.doCleanups()
