"""Actual V2 Close releases UI payloads, not stopped/hidden/retryable measurements."""
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch
import weakref

import numpy as np

from sdr_monitor.domain.live import LiveSpectrumFrame
from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
from tests import test_app02_analyzer_workspace_product as product


class TerminalPresentationTests(unittest.TestCase):
    setUpClass = classmethod(product.AnalyzerWorkspaceProductTests.setUpClass.__func__)
    setUp = product.AnalyzerWorkspaceProductTests.setUp
    tearDown = product.AnalyzerWorkspaceProductTests.tearDown
    wait = product.AnalyzerWorkspaceProductTests.wait
    select_and_apply = product.AnalyzerWorkspaceProductTests.select_and_apply

    def measurement(self):
        self.select_and_apply()
        self.page.primary.click()
        self.wait(lambda: self.live.is_running() and not self.composition.view_model.state.busy)
        snapshot = self.live.latest_snapshot()
        config = snapshot.applied.applied
        frame = LiveSpectrumFrame(sequence=1, timestamp_ns=1, source_id="fake-pluto-usb",
            config_generation=snapshot.generation, center_frequency_hz=config.center_hz,
            sample_rate_hz=61.44e6, fft_size=4096, hop_size=4096,
            frequencies_hz=config.center_hz + (np.arange(4096) - 2048) * (61.44e6 / 4096),
            values=np.full(4096, -70, np.float32), unit="dBFS/bin")
        snapshot = replace(snapshot, spectrum=frame, persistence=synthetic_persistence(frame, 32, 1, 1))
        self.live._snapshot = snapshot
        self.presenter.offer_snapshot_for_render(snapshot)
        scene = self.page.visualization.spectrum_scene
        pane = self.page.visualization.waterfall_pane
        self.wait(lambda: scene.displayed_frame is not None and scene._persistence.metrics.image_uploads > 0
                  and pane.history_rows > 0)
        self.page.primary.click()
        self.wait(lambda: not self.live.is_running() and not self.composition.view_model.state.busy)
        self.wait(lambda: self.composition.spectrum_projector._future is None)

    def assert_payloads_released(self):
        snapshot = self.composition.memory_snapshot(self.page)
        self.assertEqual(snapshot.unique_array_bytes, 0, snapshot)
        self.assertIsNone(self.page.visualization.waterfall_pane._renderer.buffer)
        self.assertIsNone(self.composition.view_model.state.snapshot)
        self.assertIsNone(self.composition.analyzer_view_model.state.bundle)
        self.assertEqual(snapshot.allocation_budget.reserved_bytes, 0)

    def test_complete_close_releases_arrays_while_fixture_and_qobjects_stay_alive(self):
        self.measurement()
        scene = self.page.visualization.spectrum_scene
        pane = self.page.visualization.waterfall_pane
        density = weakref.ref(scene._persistence._latest_view.density)
        ring = weakref.ref(pane._renderer.buffer._data)
        grid = weakref.ref(scene.measurement_grid)
        self.assertGreater(self.composition.memory_snapshot(self.page).unique_array_bytes, 0)
        self.shell.close()
        self.wait(lambda: self.shell._is_closed)
        self.assert_payloads_released()
        self.assertIsNone(density())
        self.assertIsNone(ring())
        self.assertIsNone(grid())
        self.shell.close()  # Repeated close must not resurrect storage or repeat backend work.
        self.assert_payloads_released()

    def test_stop_and_hide_preserve_last_frame_and_history(self):
        self.measurement()
        scene = self.page.visualization.spectrum_scene
        pane = self.page.visualization.waterfall_pane
        frame, ring = scene.latest_frame, pane._renderer.buffer
        self.shell.select_workspace("calibration")
        self.app.processEvents()
        self.shell.select_workspace("analyzer")
        self.wait(lambda: scene.displayed_frame is not None)
        self.assertIs(scene.latest_frame, frame)
        self.assertIs(pane._renderer.buffer, ring)
        self.assertGreater(pane.history_rows, 0)
        self.assertEqual(self.events, ["rtbw-start", "rtbw-stop"])

    def test_failed_close_retains_measurement_until_successful_retry(self):
        self.measurement()
        scene = self.page.visualization.spectrum_scene
        before = scene.latest_frame
        original = self.presenter._use_cases.shutdown
        calls = []

        def fail_once(timeout):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("terminal release test")
            original(timeout)

        with patch.object(self.presenter._use_cases, "shutdown", side_effect=fail_once):
            self.shell.close()
            self.wait(lambda: self.composition.close_lifecycle.state.phase == "failed")
            self.assertFalse(self.shell._is_closed)
            self.assertIs(scene.latest_frame, before)
            self.assertIsNotNone(self.composition.view_model.state.snapshot)
            self.shell.close()
            self.wait(lambda: self.shell._is_closed)
        self.assert_payloads_released()
        self.assertEqual(len(calls), 2)

    def test_pending_timeout_retains_measurement_until_worker_ack(self):
        self.measurement()
        scene = self.page.visualization.spectrum_scene
        before = scene.latest_frame
        release, entered = threading.Event(), threading.Event()
        self.composition.close_lifecycle.timeout_s = .03
        original = self.presenter._use_cases.shutdown

        def slow(timeout):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test barrier expired")
            original(timeout)

        try:
            with patch.object(self.presenter._use_cases, "shutdown", side_effect=slow):
                self.shell.close()
                self.wait(lambda: entered.is_set() and self.composition.close_lifecycle.state.phase == "timeout")
                self.assertIs(scene.latest_frame, before)
                self.assertIsNotNone(self.composition.view_model.state.snapshot)
                self.assertFalse(self.shell._is_closed)
                release.set()
                self.wait(lambda: self.shell._is_closed)
        finally:
            release.set()
        self.assert_payloads_released()
