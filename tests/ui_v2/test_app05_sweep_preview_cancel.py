"""Stop skips only unneeded preview presentation, never drained terminals."""
from concurrent.futures import CancelledError
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from scripts.benchmark_app04_poll_overload import run_qt_until
from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.state import prepared_sweep
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal
from tests import test_app02_analyzer_workspace_product as product
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode


class SweepPreviewCancelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_stop_between_spectrum_and_waterfall_skips_preview_but_preserves_terminal(self):
        for with_terminal in (False, True):
            with self.subTest(with_terminal=with_terminal):
                self.exercise_barrier(with_terminal, "spectrum")

    def test_stop_after_domain_poll_preserves_terminal_and_skips_preview_work(self):
        for with_terminal in (False, True):
            with self.subTest(with_terminal=with_terminal):
                self.exercise_barrier(with_terminal, "poll")

    def test_cancellation_releases_reservation_and_preparer_remains_reusable(self):
        budget = PresentationAllocationBudget()
        preparer = prepared_sweep.SweepSnapshotPreparer(budget)
        snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), progress())
        bundle = snapshot.analyzer_bundle
        stop = threading.Event()
        original = prepared_sweep.prepare_sweep_snapshot

        def stop_after_rows(*args, **kwargs):
            value = original(*args, **kwargs)
            stop.set()
            return value

        with patch.object(prepared_sweep, "prepare_sweep_snapshot", side_effect=stop_after_rows):
            with self.assertRaises(CancelledError):
                preparer.prepare_cancellable(snapshot, bundle, cancelled=stop.is_set)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        stop.clear()
        value = preparer.prepare_cancellable(snapshot, bundle, cancelled=stop.is_set)
        self.assertIs(value.snapshot, snapshot)
        self.assertEqual(len(value.waterfall_rows), 1)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)

    def test_terminal_or_error_snapshot_never_uses_cancellation(self):
        preparer = prepared_sweep.SweepSnapshotPreparer(PresentationAllocationBudget())
        for snapshot in (
            ContinuousSweepDisplaySnapshot(terminal(), ContinuousSweepDisplayMetrics(), progress(2)),
            ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(has_error=True), progress()),
        ):
            value = preparer.prepare_cancellable(snapshot, snapshot.analyzer_bundle, cancelled=lambda: True)
            self.assertIs(value.snapshot, snapshot)
            self.assertIsNotNone(value.spectrum)

    def test_final_preview_and_legacy_callable_are_not_cancelled(self):
        snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), progress())
        for legacy in (False, True):
            preparer = prepared_sweep.SweepSnapshotPreparer(PresentationAllocationBudget())
            callback = (lambda s, b: preparer(s, b)) if legacy else preparer
            service = SimpleNamespace(stop=lambda: None, poll_latest=lambda: snapshot, close=lambda: None)
            presenter = ContinuousSweepPresenter(service, snapshot_preparer=callback)
            try:
                presenter._stop_requested.set()
                value = presenter._stop_and_snapshot()
                self.assertIs(value.presentation.snapshot, snapshot)
                if legacy:
                    self.assertIs(presenter._poll_and_prepare().presentation.snapshot, snapshot)
            finally:
                presenter.shutdown()

    def test_failed_stop_submission_does_not_cancel_preview_preparation(self):
        service = SimpleNamespace(stop=lambda: None, close=lambda: None)
        presenter = ContinuousSweepPresenter(service)
        try:
            presenter._timer.start(100000)
            with patch.object(presenter._stop_executor, "submit", side_effect=RuntimeError("submit failed")):
                with self.assertRaisesRegex(RuntimeError, "submit failed"):
                    presenter.stop()
            self.assertFalse(presenter._stop_requested.is_set())
        finally:
            presenter.shutdown()

    def test_actual_v2_stop_during_preview_preparation_keeps_last_frame_then_final_gap(self):
        f = product.AnalyzerWorkspaceProductTests("runTest")
        f.app = self.app
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.addCleanup(f.tearDown)
        f.select_and_apply()
        p = f.composition.analyzer_presenter
        scene = f.page.visualization.spectrum_scene
        f.page.mode.setCurrentIndex(f.page.mode.findData(AnalyzerMode.SWEEP))
        f.page.primary.click()
        f.wait(lambda: scene.displayed_frame is not None and not p.is_starting)
        p._timer.setInterval(100000)
        f.wait(lambda: p._poll_future is None and f.composition.spectrum_projector._future is None)
        old_frame = scene.displayed_frame
        entered, release = threading.Event(), threading.Event()
        spectrum_type = prepared_sweep.PreparedSpectrumFrame
        original_poll = p._service.poll_latest
        source = []

        def preview_only():
            from dataclasses import replace
            value = original_poll()
            if not source:
                value = replace(value, line=None)
                self.assertIsNotNone(value.progress)
                source.append(value.progress)
            elif value.metrics.terminal_control_gaps:
                # Default fixture reports only counters; supply a real final
                # terminal so the normal rendering path is checked as well.
                value = replace(value, line=terminal(2, gap=True), progress=None)
            return value

        def spectrum(bundle, **kwargs):
            value = spectrum_type(bundle, **kwargs)
            if source and bundle.spectrum is source[0]:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("actual V2 preview cancellation")
            return value

        snapshots, errors = [], []
        p.snapshot_ready.connect(snapshots.append)
        p.task_failed.connect(errors.append)
        try:
            with patch.object(p._service, "poll_latest", side_effect=preview_only), \
                 patch.object(prepared_sweep, "PreparedSpectrumFrame", side_effect=spectrum):
                p._poll()
                run_qt_until(entered.is_set, 1)
                f.page.primary.click()
                self.assertTrue(p.is_stopping)
                self.assertIs(scene.displayed_frame, old_frame)
                release.set()
                run_qt_until(p.can_close, 2)
            self.assertEqual(errors, [])
            self.assertEqual(p.preview_preparations_cancelled, 1)
            self.assertEqual(snapshots[-1].metrics.terminal_control_gaps, 1)
            self.assertTrue(scene.latest_frame.terminal_sweep)
            self.assertEqual(f.events, ["sweep-start", "sweep-stop"])
            # New Start must not inherit the prior Stop's cooperative flag.
            f.page.primary.click()
            run_qt_until(lambda: not p.is_starting, 1)
            self.assertFalse(p._stop_requested.is_set())
            f.page.primary.click()
            run_qt_until(p.can_close, 2)
        finally:
            release.set()

    def exercise_barrier(self, with_terminal, stage):
        first = ContinuousSweepDisplaySnapshot(terminal(1) if with_terminal else None,
                                               ContinuousSweepDisplayMetrics(), progress(2))
        final = ContinuousSweepDisplaySnapshot(terminal(2, gap=True),
                    ContinuousSweepDisplayMetrics(terminal_control_gaps=1))
        entered, release = threading.Event(), threading.Event()
        calls, prepared, delivered, errors, rows = [], [], [], [], []

        def barrier():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("preview preparation barrier")

        class Service:
            def start(self, _):
                calls.append("start")

            def poll_latest(self):
                calls.append("poll")
                if calls.count("poll") == 1:
                    if stage == "poll":
                        barrier()
                    return first
                return final

            def stop(self):
                calls.append("stop")

            def close(self):
                calls.append("close")

        budget = PresentationAllocationBudget()
        preparer = prepared_sweep.SweepSnapshotPreparer(budget)
        presenter = ContinuousSweepPresenter(Service(), snapshot_preparer=preparer)
        presenter.prepared_snapshot_ready.connect(prepared.append)
        presenter.snapshot_ready.connect(delivered.append)
        presenter.task_failed.connect(errors.append)
        skipped = []
        if hasattr(presenter, "preview_preparation_cancelled"):
            presenter.preview_preparation_cancelled.connect(skipped.append)
        gates = []
        presenter.poll_preparation_active_changed.connect(gates.append)
        spectrum_type = prepared_sweep.PreparedSpectrumFrame
        row_function = prepared_sweep.waterfall_line_from_sweep

        def spectrum(bundle, **kwargs):
            value = spectrum_type(bundle, **kwargs)
            if bundle.spectrum is first.progress and stage == "spectrum":
                barrier()
            return value

        def row(frame, **kwargs):
            rows.append(frame)
            return row_function(frame, **kwargs)

        try:
            with patch.object(prepared_sweep, "PreparedSpectrumFrame", side_effect=spectrum), \
                 patch.object(prepared_sweep, "waterfall_line_from_sweep", side_effect=row):
                presenter.start(object())
                run_qt_until(lambda: not presenter.is_starting, 1)
                presenter._timer.setInterval(100000)
                presenter._poll()
                run_qt_until(entered.is_set, 1)
                presenter.stop()
                presenter.stop()
                self.assertFalse(presenter.can_close())
                release.set()
                # Reverse GUI acknowledgement order deliberately, after both workers finish.
                stopped = presenter._stop_future
                stopped.result(timeout=2)
                presenter._finish_stop(stopped)
                run_qt_until(presenter.can_close, 1)
            self.assertEqual(errors, [])
            self.assertEqual(gates, [True, False])
            self.assertEqual(calls.count("stop"), 1)
            self.assertIs(delivered[-1], final)
            self.assertEqual(prepared[-1].snapshot.metrics.terminal_control_gaps, 1)
            if with_terminal:
                self.assertEqual(len(delivered), 2)
                self.assertIs(delivered[0], first)
                self.assertEqual([id(x) for x in rows], [id(first.line), id(first.progress), id(final.line)])
            else:
                self.assertEqual(len(delivered), 1, "obsolete preview must not delay terminal delivery")
                self.assertEqual([id(x) for x in rows], [id(final.line)])
                self.assertEqual(len(skipped), 1)
                self.assertIs(skipped[0], first)
            self.assertEqual(presenter.preview_preparations_cancelled, int(not with_terminal))
            self.assertEqual(budget.snapshot().reserved_bytes, 0)
        finally:
            release.set()
            presenter.shutdown()


if __name__ == "__main__":
    unittest.main()
