"""Contract tests for the strict manual acceptance harness (HMP-PERSIST-006).

The harness must fail on any false condition — "no exception" is never a
pass. These tests exercise the evaluation helpers and run_step directly, so
they are fast and never touch a DFL.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from esw_dfl.gui import MainWindow
from heatmap_test_isolation import (
    make_temp_settings,
    patched_qsettings,
    reset_heatmap_controls,
    shutdown_window,
)
from manual_heatmap_acceptance import (
    StepRecorder,
    evaluate_close,
    evaluate_playback_results,
    run_step,
    wait_until,
)


class HeatmapIsolationContractTests(unittest.TestCase):
    """Verify reset defaults and real-QSettings non-contamination (TZ §P2.3)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_heatmap_controls_reset_to_documented_defaults(self) -> None:
        """After reset_heatmap_controls all controls match TZ §2.1 defaults."""
        _settings = make_temp_settings(self._tmp.name)
        # Pre-poison the isolated settings to prove reset wins over restore.
        _settings.setValue("heatmap/enabled", True)
        _settings.setValue("heatmap/persistence_mode", "selected_range")
        _settings.setValue("heatmap/follow_playhead", False)
        _settings.setValue("heatmap/window_frames", 100)
        _settings.sync()

        with patched_qsettings(_settings):
            window = MainWindow()
        try:
            window.heatmap_enabled.setChecked(True)
            window.heatmap_range_mode.setCurrentIndex(3)
            window.heatmap_window_unit.setCurrentIndex(1)
            window.heatmap_window_frames_spin.setValue(100)
            window.heatmap_window_seconds_spin.setValue(2.5)
            window.heatmap_follow_playhead.setChecked(False)
            window.heatmap_compute_mode.setCurrentIndex(1)
            window.heatmap_normalization.setCurrentIndex(0)
            window.heatmap_power_min.setValue(-80.0)
            window.heatmap_power_max.setValue(-10.0)
            window.heatmap_power_bins.setCurrentText("64")
            window.heatmap_opacity.setValue(0.2)
            window.heatmap_palette.setCurrentText("Inferno")
            window.heatmap_half_life_spin.setValue(4.0)
            window.heatmap_half_life_unit.setCurrentText("ms")
            window.heatmap_color_scale_mode.setCurrentIndex(1)
            window.heatmap_color_min.setValue(0.2)
            window.heatmap_color_max.setValue(0.8)
            reset_heatmap_controls(window)

            self.assertFalse(window.heatmap_enabled.isChecked(), "enabled=False")
            self.assertEqual(window.heatmap_range_mode.currentIndex(), 0, "range_mode=0")
            self.assertEqual(window.heatmap_window_unit.currentIndex(), 0, "window_unit=0")
            self.assertEqual(window.heatmap_window_frames_spin.value(), 500, "frames=500")
            self.assertAlmostEqual(window.heatmap_window_seconds_spin.value(), 10.0, places=3, msg="seconds=10.0")
            self.assertTrue(window.heatmap_follow_playhead.isChecked(), "follow=True")
            self.assertEqual(window.heatmap_compute_mode.currentIndex(), 0, "compute=0")
            self.assertEqual(window.heatmap_normalization.currentIndex(), 2, "norm=2 (LogDensity)")
            self.assertAlmostEqual(window.heatmap_power_min.value(), -120.0, places=1, msg="pwr_min=-120")
            self.assertAlmostEqual(window.heatmap_power_max.value(), 0.0, places=1, msg="pwr_max=0")
            self.assertEqual(window.heatmap_power_bins.currentText(), "256", "bins=256")
            self.assertAlmostEqual(window.heatmap_opacity.value(), 0.65, places=2, msg="opacity=0.65")
            self.assertEqual(window.heatmap_palette.currentText(), "Viridis", "palette=Viridis")
            self.assertAlmostEqual(window.heatmap_half_life_spin.value(), 1.0, places=3, msg="half_life=1.0")
            self.assertEqual(window.heatmap_half_life_unit.currentText(), "s", "half_life_unit=s")
            self.assertEqual(window.heatmap_color_scale_mode.currentIndex(), 0, "color_scale=0")
            self.assertAlmostEqual(window.heatmap_color_min.value(), 0.0, places=3, msg="color_min=0.0")
            self.assertAlmostEqual(window.heatmap_color_max.value(), 1.0, places=3, msg="color_max=1.0")
            self.assertEqual(window.heatmap_start_spin.value(), 1, "start=1")
            self.assertEqual(window.heatmap_end_spin.value(), 1, "end=1")
        finally:
            window.heatmap_enabled.setChecked(False)
            shutdown_window(window, self.app)

    def test_real_qsettings_not_touched_by_window_construction(self) -> None:
        """Constructing MainWindow with patched_qsettings must not write to real settings."""
        real = QSettings("RohdeSchwarzTools", "R&S DFL parcer")
        real.beginGroup("heatmap")
        keys_before = sorted(real.allKeys())
        snapshot_before = {k: real.value(k) for k in keys_before}
        real.endGroup()

        _settings = make_temp_settings(self._tmp.name)
        with patched_qsettings(_settings):
            window = MainWindow()
        try:
            reset_heatmap_controls(window)
        finally:
            window.heatmap_enabled.setChecked(False)
            shutdown_window(window, self.app)

        real2 = QSettings("RohdeSchwarzTools", "R&S DFL parcer")
        real2.beginGroup("heatmap")
        keys_after = sorted(real2.allKeys())
        snapshot_after = {k: real2.value(k) for k in keys_after}
        real2.endGroup()

        self.assertEqual(keys_before, keys_after, "real QSettings heatmap keys unchanged")
        self.assertEqual(snapshot_before, snapshot_after, "real QSettings heatmap values unchanged")


class AcceptanceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_wait_until_releases_gil_for_python_worker(self) -> None:
        start = threading.Event()
        completed = threading.Event()

        def worker() -> None:
            start.wait(timeout=1.0)
            completed.set()

        def predicate() -> bool:
            start.set()
            return completed.is_set()

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            elapsed = wait_until(self.app, predicate, 0.5, "Python worker")
            self.assertLess(elapsed, 0.5)
        finally:
            thread.join(timeout=1.0)

    def test_false_acceptance_result_marks_step_failed(self) -> None:
        observations: list[dict] = []
        failed: list[str] = []
        recorder = StepRecorder(observations, failed)

        def bad_step():
            recorder.check(False, "impossible_condition")
            return {}

        ok = run_step(recorder, "X", "step with false condition", bad_step)
        self.assertFalse(ok)
        self.assertIn("X", failed)
        self.assertEqual(observations[-1]["ok"], False)
        self.assertIn("impossible_condition", repr(observations[-1]["error"]))

    def test_false_playback_density_condition_returns_nonzero(self) -> None:
        failures = evaluate_playback_results(
            density_changed=False,
            applied_target=100,
            desired_target=100,
            lag_frames=0,
            phase_name="CURRENT",
        )
        self.assertEqual(failures, ["density_hash_unchanged"])
        # All-green evaluation returns an empty list (pass condition).
        self.assertEqual(
            evaluate_playback_results(
                density_changed=True,
                applied_target=100,
                desired_target=100,
                lag_frames=0,
                phase_name="CURRENT",
            ),
            [],
        )

    def test_close_step_fails_when_worker_remains(self) -> None:
        self.assertEqual(evaluate_close(workers_empty=False, thread_pool_idle=True), ["workers_not_empty"])
        self.assertEqual(evaluate_close(workers_empty=True, thread_pool_idle=False), ["thread_pool_not_idle"])
        self.assertEqual(evaluate_close(workers_empty=True, thread_pool_idle=True), [])


if __name__ == "__main__":
    unittest.main()
