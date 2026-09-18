"""Single-flight Sweep drain/lifecycle tests. Deterministic fake, no hardware."""
from __future__ import annotations

import os
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as product
from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay
from tests.test_app04_sweep_publication_order import _line


class _DelayedDisplay:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = []
        self.threads = []
        self.polls = 0
        self.failure = False

    def start(self, _request):
        self.calls.append("start")

    def poll_latest(self):
        self.polls += 1
        self.threads.append(threading.get_ident())
        if self.polls == 1:
            self.entered.set()
            if not self.release.wait(2):
                raise RuntimeError("test drain barrier expired")
            if self.failure:
                raise RuntimeError("injected drain failure")
        self.calls.append(f"poll:{self.polls}")
        return ContinuousSweepDisplaySnapshot(_line(self.polls), ContinuousSweepDisplayMetrics())

    def stop(self):
        self.calls.append("stop")

    def close(self):
        self.calls.append("close")


class SweepPollResponsivenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertTrue(predicate(), "Qt/worker condition not reached")

    def start_presenter(self, service):
        presenter = ContinuousSweepPresenter(service, max_poll_hz=1000)
        self.addCleanup(presenter.shutdown)
        self.addCleanup(service.release.set)
        presenter.start(object())
        self.wait(lambda: not presenter.is_starting)
        presenter._poll()
        self.assertTrue(service.entered.wait(.5))
        return presenter

    def test_delayed_drain_keeps_qt_alive_and_stop_queues_once(self):
        service = _DelayedDisplay()
        presenter = self.start_presenter(service)
        self.assertNotEqual(service.threads[0], threading.get_ident())
        frames = []
        presenter.line_ready.connect(lambda line: frames.append(line.sequence))
        beats = []
        heartbeat = QTimer()
        heartbeat.setInterval(1)
        heartbeat.timeout.connect(lambda: beats.append(time.monotonic()))
        heartbeat.start()
        try:
            self.wait(lambda: len(beats) >= 12)
            self.assertEqual(service.polls, 1)
            for _ in range(1000):
                presenter._poll()
            before = time.monotonic()
            presenter.stop()
            self.assertLess(time.monotonic() - before, .1)
            stop_future = presenter._stop_future
            presenter.stop()
            presenter.start(object())
            self.assertIs(presenter._stop_future, stop_future)
            self.assertTrue(presenter.is_stopping)
            self.assertFalse(presenter.can_close())
            self.assertEqual(service.calls, ["start"])
            self.wait(lambda: len(beats) >= 24)
            service.release.set()
            self.wait(presenter.can_close)
            self.assertEqual(frames, [1, 2])
            self.assertEqual(service.calls, ["start", "poll:1", "stop", "poll:2"])
        finally:
            heartbeat.stop()

    def test_completed_worker_still_occupies_one_slot_until_gui_delivery(self):
        service = _DelayedDisplay()
        presenter = self.start_presenter(service)
        service.release.set()
        future = presenter._poll_future
        future.result(timeout=1)
        for _ in range(1000):
            presenter._poll()
        self.assertIs(presenter._poll_future, future)
        self.assertEqual(service.polls, 1)
        presenter.stop()
        self.wait(presenter.can_close)

    def test_delayed_error_uses_owned_stop_and_does_not_emit_failed_packet(self):
        service = _DelayedDisplay()
        service.failure = True
        presenter = self.start_presenter(service)
        errors, frames = [], []
        presenter.task_failed.connect(errors.append)
        presenter.line_ready.connect(lambda line: frames.append(line.sequence))
        service.release.set()
        self.wait(presenter.can_close)
        self.assertEqual(errors, ["injected drain failure"])
        self.assertEqual(frames, [2])
        self.assertEqual(service.calls.count("stop"), 1)

    def test_stop_delivery_orders_inflight_terminal_even_if_qt_callback_is_late(self):
        service = _DelayedDisplay()
        presenter = self.start_presenter(service)
        frames = []
        presenter.line_ready.connect(lambda line: frames.append(line.sequence))
        presenter.stop()
        service.release.set()
        future = presenter._stop_future
        future.result(timeout=1)
        # Deliberately process completion in reverse order, then flush the
        # queued original callbacks. Neither duplicate nor rollback is allowed.
        presenter._finish_stop(future)
        self.app.processEvents()
        self.assertEqual(frames, [1, 2])
        self.assertTrue(presenter.can_close())

    def test_legacy_shutdown_joins_existing_stop_once_then_ignores_late_callbacks(self):
        service = _DelayedDisplay()
        presenter = self.start_presenter(service)
        frames = []
        presenter.line_ready.connect(lambda line: frames.append(line.sequence))
        presenter.stop()
        service.release.set()
        presenter.shutdown()
        self.app.processEvents()
        presenter.shutdown()
        self.assertEqual(frames, [1, 2])
        self.assertEqual(service.calls, ["start", "poll:1", "stop", "poll:2", "close"])

    def test_actual_v2_stop_remains_actionable_while_display_drain_is_delayed(self):
        harness = product.AnalyzerWorkspaceProductTests("runTest")
        harness.app = self.app
        harness.setUp()
        entered, release = threading.Event(), threading.Event()
        original = _FakeAnalyzerDisplay.poll_latest
        calls = []

        def delayed(display):
            calls.append(threading.get_ident())
            if len(calls) == 1:
                entered.set()
                if not release.wait(2):
                    raise RuntimeError("composition drain barrier expired")
            return original(display)

        try:
            harness.select_and_apply()
            page = harness.page
            page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
            with patch.object(_FakeAnalyzerDisplay, "poll_latest", delayed):
                page.primary.click()
                self.wait(entered.is_set)
                self.assertNotEqual(calls[0], threading.get_ident())
                self.assertTrue(page.primary.isEnabled())
                self.assertFalse(harness.composition.can_close())
                page.primary.click()
                presenter = harness.composition.analyzer_presenter
                self.assertTrue(presenter.is_stopping)
                self.assertFalse(page.primary.isEnabled())
                self.assertEqual(harness.events, ["sweep-start"])
                release.set()
                self.wait(presenter.can_close)
                self.assertEqual(harness.events, ["sweep-start", "sweep-stop"])
                self.assertEqual(len(calls), 2)
        finally:
            release.set()
            presenter = harness.composition.analyzer_presenter
            presenter.stop()
            self.wait(presenter.can_close)
            harness.tearDown()
            harness.doCleanups()
