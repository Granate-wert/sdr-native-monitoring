"""MainWindow-level integration tests for the persistence controller (P2/P3).

Offscreen Qt, synthetic session with a real fake DFL on disk: logical-target
routing, stale-layer policy, frame-skipping analytics, export stale guard.
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication

from esw_dfl.domain import MeasurementMetadata, MeasurementSession, SpectrumTrace, WaterfallData
from esw_dfl.frame_navigation import NavigationReason
from esw_dfl.gui import MainWindow
from esw_dfl.heatmap_persistence import WindowUnit
from esw_dfl.models import AcquisitionTiming, SpectrogramInfo
from esw_dfl.spectrogram import SpectrogramFrameReader, SpectrogramIndex, SpectrogramRow
from heatmap_persistence_fixtures import make_frame, offline_density
from heatmap_test_isolation import (
    make_temp_settings,
    patched_qsettings,
    reset_heatmap_controls,
    shutdown_window,
)


FREQ_BINS = 8
FRAME_COUNT = 300
WINDOW = 50
SIGNAL_BIN = 3
FREQUENCIES = np.linspace(100.0, 800.0, FREQ_BINS)


def _write_fake_dfl(root: Path, frames: list[np.ndarray]) -> tuple[Path, SpectrogramIndex]:
    sector_size = 512
    stream = bytearray()
    offsets: list[int] = []
    lengths: list[int] = []
    for index, values in enumerate(frames):
        payload = base64.b64encode(np.ascontiguousarray(values, dtype="<f4").tobytes()).decode("ascii")
        line = (
            f'<SgramLine Line="{index}"><DataBlock Block="0" Data="' + payload + '"/></SgramLine>'
        ).encode("ascii")
        offsets.append(len(stream))
        lengths.append(len(line))
        stream += line
    sector_count = (len(stream) + sector_size - 1) // sector_size
    stream += b"\x00" * (sector_count * sector_size - len(stream))
    path = root / "fake_a.dfl"
    path.write_bytes(b"\x00" * sector_size + bytes(stream))
    info = SpectrogramInfo(
        key="waterfall",
        title="Waterfall",
        mode="RT",
        measurement="Spectrum",
        measurement_type="Spectrogram",
        source_stream="stream",
        line_count=len(frames),
        point_count=int(frames[0].size),
        start_hz=float(FREQUENCIES[0]),
        stop_hz=float(FREQUENCIES[-1]),
    )
    index = SpectrogramIndex(
        info=info,
        line_indices=np.arange(len(frames), dtype=np.int64),
        timestamps=np.arange(len(frames), dtype=np.float64),
        offsets=np.asarray(offsets, dtype=np.int64),
        lengths=np.asarray(lengths, dtype=np.int32),
        sector_chain=np.arange(sector_count, dtype=np.int32),
        sector_size=sector_size,
    )
    return path, index


class _BlockingFactory:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.readers: list[Any] = []

    def __call__(self, path: Path, index: SpectrogramIndex) -> Any:
        factory = self

        class _Reader:
            def __init__(self) -> None:
                self._reader = SpectrogramFrameReader(path, index)
                factory.readers.append(self)

            def read_frame(self, frame_index: int) -> SpectrogramRow:
                factory.started.set()
                factory.release.wait(timeout=30.0)
                return self._reader.read_frame(frame_index)

            def close(self) -> None:
                self._reader.close()

        return _Reader()


class PersistenceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.frames = [make_frame(SIGNAL_BIN) for _ in range(FRAME_COUNT)]
        self.dfl_path, self.index = _write_fake_dfl(Path(self._tmp.name), self.frames)
        _settings = make_temp_settings(self._tmp.name)
        with patched_qsettings(_settings):
            self.window = MainWindow()
        reset_heatmap_controls(self.window)
        # test-specific override: smaller window keeps integration tests fast
        self.window.heatmap_window_frames_spin.setValue(WINDOW)
        self.window.heatmap_power_bins.setCurrentText("64")
        self.session = MeasurementSession(
            "session-a", self.dfl_path, "session-a", MeasurementMetadata()
        )
        waterfall = WaterfallData(
            "waterfall", "Waterfall", FRAME_COUNT, FREQ_BINS,
            float(FREQUENCIES[0]), float(FREQUENCIES[-1]),
            float(FREQUENCIES[1] - FREQUENCIES[0]), "stream",
        )
        values = np.stack(self.frames)
        waterfall.set_preview(values, np.arange(FRAME_COUNT, dtype=np.float64), np.arange(FRAME_COUNT))
        self.session.waterfalls["waterfall"] = waterfall
        self.session.active_waterfall_id = "waterfall"
        trace = SpectrumTrace(
            "trace-1", "Trace 1", float(FREQUENCIES[0]), float(FREQUENCIES[-1]),
            float(FREQUENCIES[1] - FREQUENCIES[0]), self.frames[-1].copy(),
        )
        self.session.traces[trace.trace_id] = trace
        self.session.active_trace_id = trace.trace_id
        self.window.repository.add(self.session)
        self.window._spectrogram_indexes[("session-a", "waterfall")] = self.index
        self.window.set_active_session("session-a")
        self.window.no_skip_check.setChecked(False)
        self.window._frame_nav.config.sequential_mode = False
        self.window._frame_scheduler.set_sequential_mode(False)
        self.window._frame_nav.config.wheel_step = 1
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.heatmap_enabled.setChecked(False)
        shutdown_window(self.window, self.app)
        self._tmp.cleanup()

    # --- helpers ---------------------------------------------------------------
    def _wait_until(self, predicate, timeout_s: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return predicate()

    @property
    def controller(self):
        return self.window._heatmap_controller

    def _enable_rolling(self, current_frame: int) -> None:
        self.session.current_frame = current_frame
        self.window.heatmap_enabled.setChecked(True)

    def _applied_snapshot(self):
        return self.controller.applied_snapshot

    def _wait_applied_target(self, target: int, timeout_s: float = 15.0) -> bool:
        return self._wait_until(
            lambda: self.controller.applied_snapshot is not None
            and self.controller.applied_snapshot.target_frame == target
            and self.controller.active_ticket is None
            and self.controller.pending_ticket is None,
            timeout_s,
        )

    # --- tests -------------------------------------------------------------------
    def test_span_event_drives_target_before_ui_snapshot(self) -> None:
        factory = _BlockingFactory()
        self.controller._reader_factory = factory
        self._enable_rolling(10)
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.window._frame_nav.seek(20, NavigationReason.FRAME_INPUT)
        self.app.processEvents()
        self.assertEqual(self.controller.desired_target.frame_index, 20)
        self.assertIsNone(self.controller.applied_snapshot)
        factory.release.set()
        self.assertTrue(self._wait_applied_target(20))

    def test_duplicate_connection_is_idempotent(self) -> None:
        events: list[str] = []
        original = self.controller._audit_cb
        self.controller._audit_cb = lambda event, **details: (
            events.append(event),
            original(event, **details),
        )[0]
        self.window._connect_heatmap_navigation()
        self.window._connect_heatmap_navigation()
        self._enable_rolling(10)
        self.assertTrue(self._wait_applied_target(10))
        self.window._frame_nav.seek(11, NavigationReason.FRAME_INPUT)
        self.app.processEvents()
        received = [event for event in events if event == "HEATMAP_TARGET_RECEIVED"]
        self.assertEqual(len(received), 1)
        self.assertEqual(int(self.controller.diagnostics()["heatmap_navigation_target"]), 11)

    def test_ui_frame_skipping_does_not_skip_target_window_analytics(self) -> None:
        self._enable_rolling(0)
        self.assertTrue(self._wait_applied_target(0))
        # 300 logical playback targets emitted synchronously; the presentation
        # layer applies only a few snapshots, analytics must reach the end.
        for frame in range(1, FRAME_COUNT):
            self.window._frame_nav.seek(frame, NavigationReason.PLAYBACK)
        self.assertTrue(self._wait_applied_target(FRAME_COUNT - 1, timeout_s=60.0))
        snapshot = self._applied_snapshot()
        assert snapshot is not None
        expected = offline_density(self.frames, FRAME_COUNT - WINDOW, FRAME_COUNT - 1)
        np.testing.assert_array_equal(snapshot.density, expected)
        self.assertEqual(self.controller.diagnostics()["heatmap_lag_frames"], 0)

    def test_playback_gap_coalesces_latest_target_and_keeps_layer_visible(self) -> None:
        self._enable_rolling(99)
        self.assertTrue(self._wait_applied_target(99))
        self.assertTrue(self.window.spectrum_renderer.heatmap_visible)
        factory = _BlockingFactory()
        self.controller._reader_factory = factory
        events: list[str] = []
        original = self.controller._audit_cb
        self.controller._audit_cb = lambda event, **details: (
            events.append(event),
            original(event, **details),
        )[0]
        # 99 -> 170 has no overlap with a 50-frame exact window. It is a
        # playback gap, not a manual seek: the current image must remain.
        self.window._frame_nav.seek(170, NavigationReason.PLAYBACK)
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.app.processEvents()
        self.assertTrue(self.window.spectrum_renderer.heatmap_visible)
        # Another UI tick updates the stream high-water target. It must not
        # create a second reader/rebuild or discard frames 171..239.
        self.window._frame_nav.seek(240, NavigationReason.PLAYBACK)
        self.app.processEvents()
        self.assertEqual(self.controller.diagnostics()["heatmap_stream_active"], 1)
        self.assertIsNone(self.controller.pending_ticket)
        self.assertTrue(self.window.spectrum_renderer.heatmap_visible)
        factory.release.set()
        self.assertTrue(self._wait_applied_target(240, timeout_s=30.0))
        snapshot = self._applied_snapshot()
        assert snapshot is not None
        np.testing.assert_array_equal(snapshot.density, offline_density(self.frames, 191, 240))
        self.assertIn("HEATMAP_STREAM_TARGET_PUBLISHED", events)
        self.assertEqual(self.controller.diagnostics()["heatmap_cancel_count"], 0)

    def test_seek_hides_stale_layer_until_rebuild_applies(self) -> None:
        self._enable_rolling(199)
        self.assertTrue(self._wait_applied_target(199))
        self.assertTrue(self.window.spectrum_renderer.heatmap_visible)
        factory = _BlockingFactory()
        self.controller._reader_factory = factory
        self.window._frame_nav.seek(75, NavigationReason.FRAME_INPUT)
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.app.processEvents()
        self.assertFalse(self.window.spectrum_renderer.heatmap_visible)
        self.assertIn("Rebuilding", self.window.heatmap_status.text())
        factory.release.set()
        self.assertTrue(self._wait_applied_target(75))
        self.assertTrue(self.window.spectrum_renderer.heatmap_visible)
        snapshot = self._applied_snapshot()
        assert snapshot is not None
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (26, 75))
        self.assertIn("Current", self.window.heatmap_status.text())

    def test_late_rebuild_result_is_discarded(self) -> None:
        self._enable_rolling(199)
        self.assertTrue(self._wait_applied_target(199))
        factory = _BlockingFactory()
        self.controller._reader_factory = factory
        self.window._frame_nav.seek(100, NavigationReason.FRAME_INPUT)
        self.assertTrue(factory.started.wait(timeout=10.0))
        # The blocked rebuild is superseded by a clear (generation bump, no cancel).
        self.window._heatmap_clear()
        factory.release.set()
        self.assertTrue(
            self._wait_until(
                lambda: self.controller.diagnostics()["heatmap_stale_results_discarded"] >= 1
            )
        )
        self.assertIsNone(self.controller.applied_snapshot)
        self.assertFalse(self.window.spectrum_renderer.heatmap_visible)

    def test_export_is_disabled_for_stale_snapshot(self) -> None:
        self._enable_rolling(199)
        self.assertTrue(self._wait_applied_target(199))
        factory = _BlockingFactory()
        self.controller._reader_factory = factory
        self.window._frame_nav.seek(100, NavigationReason.FRAME_INPUT)
        self.assertTrue(factory.started.wait(timeout=10.0))
        self.app.processEvents()
        self.assertEqual(self.controller.phase.name, "REBUILDING")
        with patch.object(self.window, "_show_error") as show_error:
            self.assertIsNone(self.window._current_heatmap_result())
            show_error.assert_called_once()
        factory.release.set()
        self.assertTrue(self._wait_applied_target(100))

    def test_playback_updates_density_and_keeps_latest_target_bounded(self) -> None:
        self._enable_rolling(99)
        self.assertTrue(self._wait_applied_target(99))
        max_tickets = 0
        for frame in range(100, 200):
            self.window._frame_nav.seek(frame, NavigationReason.PLAYBACK)
            self.app.processEvents()
            tickets = int(self.controller.active_ticket is not None) + int(
                self.controller.pending_ticket is not None
            )
            max_tickets = max(max_tickets, tickets)
        self.assertTrue(self._wait_applied_target(199, timeout_s=60.0))
        self.assertLessEqual(max_tickets, 2)
        snapshot = self._applied_snapshot()
        assert snapshot is not None
        np.testing.assert_array_equal(
            snapshot.density, offline_density(self.frames, 150, 199)
        )

    def test_decay_mode_builds_through_engine_with_half_life(self) -> None:
        from esw_dfl import heatmap_persistence_controller as controller_module
        from unittest.mock import patch as _patch

        self.window.heatmap_range_mode.setCurrentIndex(1)  # Exponential Decay
        self.window.heatmap_half_life_spin.setValue(2.0)
        self.window.heatmap_half_life_unit.setCurrentText("s")
        self.session.current_frame = 99
        with _patch.object(controller_module, "compute_heatmap") as compute_spy:
            self.window.heatmap_enabled.setChecked(True)
            self.assertTrue(self._wait_applied_target(99))
            compute_spy.assert_not_called()
        snapshot = self._applied_snapshot()
        assert snapshot is not None
        self.assertTrue(snapshot.approximate)
        self.assertFalse(snapshot.exact)
        self.assertEqual(snapshot.half_life_seconds, 2.0)
        self.assertIn("half-life 2", self.window.heatmap_status.text())

    # --- wave 4: HMP-PERSIST-008 UI modes/units/migration/status ----------------
    def test_default_mode_is_rolling_exact(self) -> None:
        from esw_dfl.heatmap_persistence import PersistenceMode

        _s = make_temp_settings(self._tmp.name)
        with patched_qsettings(_s):
            window2 = MainWindow()
        try:
            self.assertEqual(window2._heatmap_mode(), PersistenceMode.ROLLING_EXACT)
        finally:
            shutdown_window(window2, self.app)

    def test_mode_specific_controls_are_enabled_correctly(self) -> None:
        w = self.window
        # Rolling Exact: window/unit/follow enabled, half-life hidden, start/end disabled.
        w.heatmap_range_mode.setCurrentIndex(0)
        w._update_heatmap_controls_for_mode()
        self.assertTrue(w.heatmap_window_unit.isEnabled())
        self.assertTrue(w.heatmap_window_frames_spin.isEnabled())
        self.assertTrue(w.heatmap_follow_playhead.isEnabled())
        self.assertFalse(w.heatmap_half_life_row.isVisibleTo(w.heatmap_dock))
        self.assertFalse(w.heatmap_start_spin.isEnabled())
        self.assertEqual(w.heatmap_recalculate_button.text(), "Rebuild now")
        # Decay: half-life visible, start/end disabled.
        w.heatmap_range_mode.setCurrentIndex(1)
        w._update_heatmap_controls_for_mode()
        self.assertTrue(w.heatmap_half_life_row.isVisibleTo(w.heatmap_dock))
        self.assertFalse(w.heatmap_start_spin.isEnabled())
        # Selected: start/end enabled, window/follow disabled.
        w.heatmap_range_mode.setCurrentIndex(2)
        w._update_heatmap_controls_for_mode()
        self.assertTrue(w.heatmap_start_spin.isEnabled())
        self.assertTrue(w.heatmap_end_spin.isEnabled())
        self.assertFalse(w.heatmap_window_unit.isEnabled())
        self.assertFalse(w.heatmap_follow_playhead.isEnabled())
        self.assertEqual(w.heatmap_recalculate_button.text(), "Apply / Rebuild")
        # Full: window/follow disabled, compute mode enabled.
        w.heatmap_range_mode.setCurrentIndex(3)
        w._update_heatmap_controls_for_mode()
        self.assertFalse(w.heatmap_window_unit.isEnabled())
        self.assertTrue(w.heatmap_compute_mode.isEnabled())

    def test_full_recording_status_explicitly_says_fixed(self) -> None:
        # Keep the render timer active so the fixed snapshot is queued while
        # phase=CURRENT is emitted. The GUI must refresh status when the queued
        # snapshot is eventually applied, not keep the previous Rolling text.
        self.controller._render_timer.start(10_000)
        self.window.heatmap_range_mode.setCurrentIndex(3)  # Full Recording
        self.window.heatmap_enabled.setChecked(True)
        controller = self.controller
        self.assertTrue(
            self._wait_until(
                lambda: controller.applied_snapshot is not None and controller.active_ticket is None
            )
        )
        controller._render_timer.stop()
        controller._render_timeout()
        self.app.processEvents()
        self.assertIs(self.window._heatmap_applied_snapshot, controller.applied_snapshot)
        self.assertIn("Full Recording · Fixed", self.window.heatmap_status.text())
        self.assertIn("playback does not change this layer", self.window.heatmap_status.text())

    def test_selected_range_start_end_are_only_active_in_selected(self) -> None:
        w = self.window
        w.heatmap_range_mode.setCurrentIndex(2)
        w._update_heatmap_controls_for_mode()
        self.assertTrue(w.heatmap_start_spin.isEnabled())
        w.heatmap_range_mode.setCurrentIndex(0)
        w._update_heatmap_controls_for_mode()
        self.assertFalse(w.heatmap_start_spin.isEnabled())
        self.assertFalse(w.heatmap_end_spin.isEnabled())

    def test_time_window_builds_config_from_seconds(self) -> None:
        from esw_dfl.heatmap_persistence import WindowUnit

        self.window._combo_set_data(self.window.heatmap_window_unit, WindowUnit.SECONDS.value)
        self.window.heatmap_window_seconds_spin.setValue(5.0)
        config = self.window._heatmap_build_persistence_config()
        self.assertEqual(config.window_unit, WindowUnit.SECONDS)
        self.assertEqual(config.window_seconds, 5.0)
        # Enable through the seconds window: exact rolling over [t-5, t].
        self.session.current_frame = 99
        self.window.heatmap_range_mode.setCurrentIndex(0)
        self.window.heatmap_enabled.setChecked(True)
        controller = self.controller
        self.assertTrue(
            self._wait_until(
                lambda: controller.applied_snapshot is not None and controller.active_ticket is None
            )
        )
        snapshot = controller.applied_snapshot
        assert snapshot is not None
        # Timestamps are arange(t): [94..99] fall into the 5-second window.
        self.assertEqual((snapshot.frame_start, snapshot.frame_end), (94, 99))
        self.assertTrue(snapshot.exact)

    def test_speed_and_no_skip_refresh_both_rolling_window_floors(self) -> None:
        waterfall = self.session.waterfalls["waterfall"]
        waterfall.metadata["mode"] = "RT"
        self.session.acquisition_timing["RT"] = AcquisitionTiming(
            mode="RT", instrument_sweep_time_s=0.01
        )
        self.window.fps_combo.setCurrentText("60")
        self.window.no_skip_check.setChecked(False)
        self.window.speed_combo.setCurrentText("1×")
        self.window._refresh_heatmap_render_budget()
        self.assertEqual(self.window.heatmap_window_frames_spin.minimum(), 3)
        self.assertAlmostEqual(self.window.heatmap_window_seconds_spin.minimum(), 0.03)

        self.window.speed_combo.setCurrentText("10×")
        self.assertEqual(self.window.heatmap_window_frames_spin.minimum(), 26)
        self.assertAlmostEqual(self.window.heatmap_window_seconds_spin.minimum(), 0.26)
        self.assertIn("10×", self.window.heatmap_window_budget_label.text())

        self.window._combo_set_data(self.window.heatmap_window_unit, WindowUnit.SECONDS.value)
        config = self.window._heatmap_build_persistence_config()
        self.assertAlmostEqual(config.minimum_window_seconds or 0.0, 0.26)

        self.window.no_skip_check.setChecked(True)
        self.assertEqual(self.window.heatmap_window_frames_spin.minimum(), 3)
        self.assertAlmostEqual(self.window.heatmap_window_seconds_spin.minimum(), 0.03)

    def test_legacy_centered_setting_migrates_to_rolling_exact(self) -> None:
        from esw_dfl.heatmap_persistence import PersistenceMode
        from esw_dfl import gui as gui_module

        settings = self.window.settings
        settings.setValue("heatmap/range_mode", "centered")
        settings.sync()
        with patch.object(gui_module, "QSettings", lambda *args: settings):
            window2 = MainWindow()
        try:
            self.assertEqual(window2._heatmap_mode(), PersistenceMode.ROLLING_EXACT)
        finally:
            window2.heatmap_enabled.setChecked(False)
            shutdown_window(window2, self.app)

    def test_legacy_decay_coefficient_is_not_silently_used_as_half_life(self) -> None:
        from esw_dfl import gui as gui_module

        settings = self.window.settings
        settings.setValue("heatmap/decay", 0.42)
        settings.sync()
        with patch.object(gui_module, "QSettings", lambda *args: settings):
            window2 = MainWindow()
        try:
            # The legacy coefficient never becomes seconds: the documented
            # product default (1.0 s) is used instead.
            self.assertEqual(window2.heatmap_half_life_spin.value(), 1.0)
        finally:
            window2.heatmap_enabled.setChecked(False)
            shutdown_window(window2, self.app)

    def test_persistence_settings_roundtrip_preserves_new_fields(self) -> None:
        from esw_dfl.heatmap_persistence import ColorScaleMode, WindowUnit
        from esw_dfl import gui as gui_module

        settings = self.window.settings
        self.window.heatmap_range_mode.setCurrentIndex(2)
        self.window._combo_set_data(self.window.heatmap_window_unit, WindowUnit.SECONDS.value)
        self.window.heatmap_window_frames_spin.setValue(777)
        self.window.heatmap_window_seconds_spin.setValue(42.5)
        self.window.heatmap_follow_playhead.setChecked(False)
        self.window._combo_set_data(self.window.heatmap_color_scale_mode, ColorScaleMode.FIXED.value)
        self.window.heatmap_color_min.setValue(0.25)
        self.window.heatmap_color_max.setValue(0.75)
        settings.sync()
        with patch.object(gui_module, "QSettings", lambda *args: settings):
            window2 = MainWindow()
        try:
            self.assertEqual(window2.heatmap_range_mode.currentIndex(), 2)
            self.assertEqual(window2.heatmap_window_unit.currentData(), WindowUnit.SECONDS.value)
            self.assertEqual(window2.heatmap_window_frames_spin.value(), 777)
            self.assertEqual(window2.heatmap_window_seconds_spin.value(), 42.5)
            self.assertFalse(window2.heatmap_follow_playhead.isChecked())
            self.assertEqual(window2.heatmap_color_scale_mode.currentData(), ColorScaleMode.FIXED.value)
            self.assertEqual(window2.heatmap_color_min.value(), 0.25)
            self.assertEqual(window2.heatmap_color_max.value(), 0.75)
        finally:
            window2.heatmap_enabled.setChecked(False)
            shutdown_window(window2, self.app)


if __name__ == "__main__":
    unittest.main()
