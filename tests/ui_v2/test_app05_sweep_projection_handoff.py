"""A visible viewport waits for the one existing Sweep preparation, not another worker."""
from concurrent.futures import Future
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from scripts.benchmark_app04_poll_overload import run_qt_until

from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector
from sdr_monitor.ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as product
from tests import test_app04_sweep_poll_responsiveness as poll_fixture
from tests.ui_v2.test_app05_projection_cancellation import request


class SweepProjectionHandoffTests(unittest.TestCase):
    wait = poll_fixture.SweepPollResponsivenessTests.wait

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_gate_retains_latest_and_commits_coherent_scene_before_dispatch(self):
        submitted = []
        port = SpectrumProjector(lambda operation: submitted.append(operation) or Future())
        old, latest = request(), request()
        try:
            port.set_preparation_in_flight(True)
            port.offer(old)
            port.set_suspended(True)
            port.set_suspended(False)
            self.assertFalse(submitted)
            port.commit_requested.connect(lambda: port.offer(latest))
            port.set_preparation_in_flight(False)
            self.assertEqual(len(submitted), 1)
            self.assertIs(port._active, latest)
            self.assertIsNone(port._pending)
        finally:
            port.dispose()
            self.app.processEvents()

    def test_control_suspend_and_dispose_still_bar_dispatch_after_preparation_ack(self):
        submitted = []
        port = SpectrumProjector(lambda operation: submitted.append(operation) or Future())
        port.set_preparation_in_flight(True)
        port.offer(request())
        port.set_suspended(True)
        port.set_preparation_in_flight(False)
        self.assertFalse(submitted)
        port.dispose()
        port.set_suspended(False)
        port.set_preparation_in_flight(False)
        self.assertFalse(submitted)

    def test_source_refresh_still_obeys_control_suspension(self):
        submitted = []
        port = SpectrumProjector(lambda operation: submitted.append(operation) or Future())
        try:
            port.set_preparation_in_flight(True)
            port.set_suspended(True)
            port.offer(replace(request(), requires_preparation_handoff=False))
            self.assertFalse(submitted)
            port.set_suspended(False)
            self.assertEqual(len(submitted), 1)
        finally:
            port.dispose()
            self.app.processEvents()

    def test_no_waiting_viewport_does_not_flush_normal_coalescing(self):
        port = SpectrumProjector(lambda _: Future())
        commits = []
        port.commit_requested.connect(lambda: commits.append(True))
        port.set_preparation_in_flight(True)
        port.set_preparation_in_flight(False)
        self.assertFalse(commits)
        port.dispose()

    def test_failed_or_cancelled_poll_releases_preparation_gate_after_error_stop(self):
        for outcome in ("failed", "cancelled"):
            service = poll_fixture._DelayedDisplay()
            service.release.set()
            presenter = ContinuousSweepPresenter(service)
            changes, errors = [], []
            presenter.poll_preparation_active_changed.connect(changes.append)
            presenter.task_failed.connect(errors.append)
            future = Future()
            try:
                presenter._timer.start(100000)
                with patch.object(presenter._stop_executor, "submit", return_value=future):
                    presenter._poll()
                if outcome == "failed":
                    future.set_exception(RuntimeError("injected poll failure"))
                else:
                    future.cancel()
                self.wait(presenter.can_close)
                self.assertEqual(changes, [True, False])
                self.assertEqual(len(errors), 1)
                self.assertEqual(service.calls.count("stop"), 1)
            finally:
                presenter.shutdown()

    def test_submit_failure_never_acquires_preparation_gate(self):
        service = poll_fixture._DelayedDisplay()
        presenter = ContinuousSweepPresenter(service)
        changes = []
        presenter.poll_preparation_active_changed.connect(changes.append)
        try:
            presenter._timer.start(100000)
            with patch.object(presenter._stop_executor, "submit", side_effect=RuntimeError("executor unavailable")):
                with self.assertRaisesRegex(RuntimeError, "executor unavailable"):
                    presenter._poll()
            self.assertIsNone(presenter._poll_future)
            self.assertEqual(changes, [])
        finally:
            presenter._timer.stop()
            presenter.shutdown()

    def test_actual_hidden_poll_hands_latest_source_to_resumed_projection_then_stop(self):
        self.actual_handoff(stop_while_preparing=False)

    def test_actual_changed_viewport_waits_for_existing_preparation(self):
        self.actual_handoff(stop_while_preparing=False, viewport_only=True)

    def test_steady_prepared_source_projects_while_next_poll_is_running(self):
        f = product.AnalyzerWorkspaceProductTests("runTest")
        f.app = self.app
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.addCleanup(f.tearDown)
        f.select_and_apply()
        p = f.composition.analyzer_presenter
        port = f.composition.spectrum_projector
        scene = f.page.visualization.spectrum_scene
        f.page.mode.setCurrentIndex(f.page.mode.findData(AnalyzerMode.SWEEP))
        f.page.primary.click()
        f.wait(lambda: scene.displayed_frame is not None and not p.is_starting)
        p._timer.setInterval(100000)
        f.wait(lambda: p._poll_future is None and port._future is None)
        prepare, poll = p._snapshot_preparer, p._service.poll_latest
        entered, release = threading.Event(), threading.Event()
        sequence = [100]
        sources = []

        def fresh():
            snapshot = poll()
            sequence[0] += 1
            return replace(snapshot, progress=replace(snapshot.progress, sequence=sequence[0]))

        def blocked(snapshot, bundle):
            value = prepare(snapshot, bundle)
            sources.append(value.analyzer_bundle)
            if len(sources) == 2:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("steady next-poll barrier")
            return value

        def next_poll(active):
            # Run before Qt can deliver the prior source's zero-timer offer.
            if not active and len(sources) == 1:
                p._poll()

        p.poll_preparation_active_changed.connect(next_poll)
        try:
            with patch.object(p._service, "poll_latest", side_effect=fresh), \
                 patch.object(p, "_snapshot_preparer", side_effect=blocked):
                p._poll()
                f.wait(entered.is_set)
                run_qt_until(lambda: scene.displayed_frame is sources[0], 1.)
                self.assertTrue(p._poll_future is not None)
                self.assertTrue(port._preparation_in_flight)
                release.set()
                f.wait(lambda: scene.displayed_frame is sources[1] and port._future is None)
        finally:
            release.set()
            p.poll_preparation_active_changed.disconnect(next_poll)
        f.page.primary.click()
        f.wait(p.can_close)
        self.assertEqual(f.events, ["sweep-start", "sweep-stop"])

    def test_actual_stop_during_waiting_projection_keeps_terminal_gap_and_releases_gates(self):
        self.actual_handoff(stop_while_preparing=True)

    def actual_handoff(self, *, stop_while_preparing, viewport_only=False):
        f = product.AnalyzerWorkspaceProductTests("runTest")
        f.app = self.app
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.addCleanup(f.tearDown)
        f.select_and_apply()
        p = f.composition.analyzer_presenter
        port = f.composition.spectrum_projector
        scene = f.page.visualization.spectrum_scene
        f.page.mode.setCurrentIndex(f.page.mode.findData(AnalyzerMode.SWEEP))
        f.page.primary.click()
        f.wait(lambda: scene.displayed_frame is not None and not p.is_starting)
        p._timer.setInterval(100000)
        if not viewport_only:
            f.shell.select_workspace("calibration")
        f.wait(lambda: p._poll_future is None and port._future is None)
        prepare, poll = p._snapshot_preparer, p._service.poll_latest
        entered, release = threading.Event(), threading.Event()
        sources = []
        delivered = []
        p.snapshot_ready.connect(delivered.append)

        def fresh():
            value = poll()
            if value.progress is not None:
                value = replace(value, progress=replace(value.progress, sequence=99))
            return value

        def blocked(snapshot, bundle):
            value = prepare(snapshot, bundle)
            sources.append(value.analyzer_bundle)
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Sweep handoff barrier")
            return value

        with patch.object(p._service, "poll_latest", side_effect=fresh), \
             patch.object(p, "_snapshot_preparer", side_effect=blocked):
            try:
                p._poll()
                f.wait(entered.is_set)
                if viewport_only:
                    left, right, _ = scene._viewport()
                    scene._view_box.setXRange(left, (left + right) / 2, padding=0)
                else:
                    f.shell.select_workspace("analyzer")
                scene.commit_projection()
                self.assertIsNone(port._future)
                self.assertIsNotNone(port._pending)
                self.assertTrue(port._pending.requires_preparation_handoff)
                self.assertTrue(port._preparation_in_flight)
                if stop_while_preparing:
                    f.page.primary.click()
                    self.assertTrue(p.is_stopping)
                release.set()
                if stop_while_preparing:
                    f.wait(p.can_close)
                else:
                    f.wait(lambda: scene.displayed_frame is sources[0] and port._future is None)
                self.assertFalse(port._preparation_in_flight)
            finally:
                release.set()
        if not stop_while_preparing:
            f.page.primary.click()
            f.wait(p.can_close)
        self.assertEqual(delivered[-1].metrics.terminal_control_gaps, 1)
        self.assertEqual(f.events, ["sweep-start", "sweep-stop"])
        f.shell.close()
        f.wait(lambda: f.shell._is_closed)
        self.assertIsNone(f.composition._sweep_preparation_signal)
        p.poll_preparation_active_changed.emit(True)
        self.assertFalse(port._preparation_in_flight)


if __name__ == "__main__":
    unittest.main()
