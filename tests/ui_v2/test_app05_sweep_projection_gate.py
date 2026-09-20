"""Normal V2 Sweep poll backpressure preserves cadence tickets and control."""
from concurrent.futures import Future
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from tests import test_app02_analyzer_workspace_product as product
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.spectrum.projection import project_spectrum
from tests.ui_v2.test_app05_projection_cancellation import request


class SweepProjectionGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = product.AnalyzerWorkspaceProductTests("runTest")
        self.fixture.app = self.app
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        self.presenter = self.fixture.composition.analyzer_presenter
        self.port = self.fixture.composition.spectrum_projector

    def test_actual_composition_holds_one_poll_until_gui_ack_without_new_tick(self):
        p = self.presenter
        future = Future()
        interval = p._timer.interval()
        p._timer.start(100000)  # deterministic timer ticket, no actual driver
        try:
            with patch.object(p._stop_executor, "submit", return_value=future) as submit:
                self.port.work_active_changed.emit(True)
                for _ in range(20):
                    p._poll()
                submit.assert_not_called()
                self.assertTrue(p._projection_poll_pending)
                self.port.work_active_changed.emit(False)
                submit.assert_called_once_with(p._poll_and_prepare)
                self.assertIs(p._poll_future, future)
                self.assertFalse(p._projection_poll_pending)
                self.port.work_active_changed.emit(True)
                self.port.work_active_changed.emit(False)
                self.assertEqual(submit.call_count, 1)
        finally:
            p._poll_future = None
            p.poll_preparation_active_changed.emit(False)
            p._timer.stop()
            p._timer.setInterval(interval)

    def test_ack_without_pending_cadence_does_not_start_poll_and_close_disconnects(self):
        p = self.presenter
        with patch.object(p._stop_executor, "submit") as submit:
            self.port.work_active_changed.emit(True)
            self.port.work_active_changed.emit(False)
            submit.assert_not_called()
        self.fixture.shell.close()
        self.fixture.wait(lambda: self.fixture.shell._is_closed)
        self.assertIsNone(self.fixture.composition._sweep_projection_backpressure)
        self.port.work_active_changed.emit(True)
        self.assertFalse(p._projection_in_flight)

    def test_actual_projection_success_failure_and_cancel_release_waiting_poll(self):
        p = self.presenter
        interval = p._timer.interval()
        for outcome in ("success", "failure", "cancel"):
            projected, polled = Future(), Future()
            p._timer.start(100000)
            try:
                with self.subTest(outcome=outcome), \
                        patch.object(self.port, "_submit", return_value=projected), \
                        patch.object(p._stop_executor, "submit", return_value=polled) as submit:
                    value = request()
                    self.port.offer(value)
                    self.port.request_commit()
                    self.assertTrue(p._projection_in_flight)
                    p._poll()
                    submit.assert_not_called()
                    if outcome == "success":
                        projected.set_result(project_spectrum(value))
                    elif outcome == "failure":
                        projected.set_exception(RuntimeError("projection fixture error"))
                    else:
                        projected.cancel()
                    self.fixture.wait(lambda: p._poll_future is polled)
                    self.assertFalse(p._projection_in_flight)
                    self.assertFalse(p._projection_poll_pending)
                    self.assertEqual(submit.call_count, 1)
            finally:
                p._poll_future = None
                p.poll_preparation_active_changed.emit(False)
                p._timer.stop()
        p._timer.setInterval(interval)

    def test_stop_bypasses_gate_clears_pending_and_preserves_terminal_delivery(self):
        f, p = self.fixture, self.presenter
        f.select_and_apply()
        f.page.mode.setCurrentIndex(f.page.mode.findData(AnalyzerMode.SWEEP))
        f.page.primary.click()
        f.wait(lambda: not p.is_starting and p._poll_future is None)
        self.port.work_active_changed.emit(True)
        p._poll()
        self.assertTrue(p._projection_poll_pending)
        delivered = []
        p.snapshot_ready.connect(delivered.append)
        f.page.primary.click()
        self.assertFalse(p._projection_poll_pending)
        f.wait(p.can_close)
        self.assertEqual(f.events, ["sweep-start", "sweep-stop"])
        self.assertTrue(delivered)
        self.assertEqual(delivered[-1].metrics.terminal_control_gaps, 1)


if __name__ == "__main__":
    unittest.main()
