"""Actual Qt lifecycle with delayed in-memory RTBW cleanup, never hardware."""

from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.live import LiveSpectrumFrame

import tests.test_app02_analyzer_workspace_product as product_fixture


class RtbwLifecycleTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls):
        instance = QApplication.instance()
        cls.app = instance if isinstance(instance, QApplication) else QApplication([])

    def test_delayed_stop_keeps_gui_alive_and_refuses_premature_close(self):
        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        entered, release = threading.Event(), threading.Event()
        timer = QTimer()
        ticks = []
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        original_stop = fixture.live.stop

        def delayed_stop():
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test cleanup barrier expired")
            return original_stop()

        try:
            fixture.select_and_apply()
            fixture.page.primary.click()
            fixture.wait(lambda: fixture.live.is_running()
                         and not fixture.composition.view_model.state.busy)
            timer.start()
            with patch.object(fixture.live, "stop", side_effect=delayed_stop) as stop:
                started = time.monotonic()
                fixture.page.primary.click()
                self.assertLess(time.monotonic() - started, 0.5)
                fixture.wait(entered.is_set)
                ticks.clear()
                deadline = time.monotonic() + 0.15
                while time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.001)
                self.assertGreaterEqual(len(ticks), 5)
                self.assertTrue(fixture.live.is_running())
                self.assertFalse(fixture.composition.can_close())
                self.assertFalse(fixture.shell.close())
                self.assertTrue(fixture.shell.isVisible())
                self.assertFalse(fixture.page.source.isEnabled())
                self.assertFalse(fixture.page.mode.isEnabled())
                stop.assert_called_once()
                release.set()
                fixture.wait(lambda: not fixture.live.is_running()
                             and not fixture.composition.view_model.state.busy)
                self.assertTrue(fixture.composition.can_close())
                stop.assert_called_once()
                fixture.shell.close()
                fixture.wait(lambda: fixture.shell._is_closed)
                # The in-memory shutdown port performs an idempotent stop
                # after normal Stop. This is not a second owned RX cleanup.
                self.assertFalse(fixture.live.is_running())
                self.assertEqual(fixture.live.stop_and_wait_calls, 1)
        finally:
            release.set()
            timer.stop()
            try:
                fixture.wait(lambda: not fixture.composition.view_model.state.busy)
                fixture.tearDown()
            finally:
                fixture.doCleanups()

    def test_delayed_stop_exception_is_visible_and_requires_explicit_retry(self):
        self._exercise_delayed_stop_failure(RuntimeError("fake Stop cleanup failed"))

    def test_delayed_stop_timeout_is_visible_and_requires_explicit_retry(self):
        self._exercise_delayed_stop_failure(TimeoutError("fake Stop cleanup timed out"))

    def _exercise_delayed_stop_failure(self, failure: Exception) -> None:
        """Prove a failed Stop stays non-terminal until one explicit retry succeeds."""

        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        entered, release = threading.Event(), threading.Event()
        timer = QTimer()
        ticks: list[float] = []
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        original_stop = fixture.live.stop
        attempts = 0

        def delayed_then_retry_stop():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test cleanup barrier expired")
                raise failure
            return original_stop()

        try:
            fixture.select_and_apply()
            fixture.page.primary.click()
            fixture.wait(lambda: fixture.live.is_running()
                         and not fixture.composition.view_model.state.busy)

            snapshot = fixture.live.latest_snapshot()
            configuration = snapshot.applied.applied
            frame = LiveSpectrumFrame(
                sequence=41,
                timestamp_ns=123_000,
                source_id="fake-pluto-usb",
                config_generation=snapshot.generation,
                center_frequency_hz=configuration.center_hz,
                sample_rate_hz=configuration.sample_rate_hz,
                fft_size=configuration.fft_size,
                hop_size=configuration.fft_size,
                frequencies_hz=(
                    configuration.center_hz
                    + (np.arange(configuration.fft_size) - configuration.fft_size // 2)
                    * (configuration.sample_rate_hz / configuration.fft_size)
                ),
                values=np.full(configuration.fft_size, -67.0, dtype=np.float32),
                unit="dBFS/bin",
            )
            delivered = replace(snapshot, spectrum=frame)
            fixture.live._snapshot = delivered
            fixture.presenter.offer_snapshot_for_render(delivered)
            fixture.wait(lambda: fixture.page._last_bundle is not None)
            last_frame = fixture.page._last_bundle.spectrum
            last_spectrum = fixture.page.visualization.spectrum_scene.latest_frame.spectrum

            timer.start()
            with patch.object(
                fixture.live,
                "stop",
                side_effect=delayed_then_retry_stop,
            ) as stop:
                started = time.monotonic()
                fixture.page.primary.click()
                self.assertLess(time.monotonic() - started, 0.5)
                fixture.wait(entered.is_set)
                ticks.clear()
                deadline = time.monotonic() + 0.15
                while time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.001)
                self.assertGreaterEqual(len(ticks), 5)
                self.assertTrue(fixture.composition.view_model.state.busy)
                self.assertTrue(fixture.live.is_running())
                self.assertFalse(fixture.composition.can_close())
                self.assertFalse(fixture.shell.close())
                self.assertTrue(fixture.shell.isVisible())
                stop.assert_called_once()

                release.set()
                fixture.wait(lambda: not fixture.composition.view_model.state.busy)
                state = fixture.composition.analyzer_view_model.state
                self.assertTrue(state.running)
                self.assertFalse(state.live.busy)
                self.assertEqual(state.error, str(failure))
                self.assertIn(str(failure), fixture.page.error.text())
                self.assertIs(fixture.page._last_bundle.spectrum, last_frame)
                self.assertEqual(fixture.page._last_bundle.spectrum.sequence, 41)
                self.assertIs(
                    fixture.page.visualization.spectrum_scene.latest_frame.spectrum,
                    last_spectrum,
                )
                self.assertFalse(fixture.composition.can_close())
                self.assertFalse(fixture.shell.close())
                self.assertTrue(fixture.shell.isVisible())

                # Failure is not terminal. One explicit retry performs the
                # sole successful service Stop and only then permits close.
                fixture.page.primary.click()
                fixture.wait(lambda: not fixture.live.is_running()
                             and not fixture.composition.view_model.state.busy)
                self.assertEqual(stop.call_count, 2)
                self.assertEqual(fixture.events.count("rtbw-stop"), 1)
                self.assertTrue(fixture.composition.can_close())
                fixture.shell.close()
                fixture.wait(lambda: fixture.shell._is_closed)
                self.assertEqual(fixture.live.stop_and_wait_calls, 1)
        finally:
            release.set()
            timer.stop()
            try:
                fixture.wait(lambda: not fixture.composition.view_model.state.busy)
                fixture.tearDown()
            finally:
                fixture.doCleanups()
