from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QScrollArea, QToolBar

from esw_dfl.domain import (
    AnalysisResult,
    FrequencyRegion,
    Marker,
    MeasurementMetadata,
    MeasurementSession,
    SpectrumTrace,
    WaterfallData,
)
from esw_dfl.gui import MainWindow, _analyze_time_gated_waterfall
from esw_dfl.models import SpectrogramInfo
from esw_dfl.renderers import SpectrumViewBox
from esw_dfl.spectrogram import SpectrogramFrameReader, SpectrogramIndex, SpectrogramRow
from esw_dfl.time_gated_power import (
    ActivityDetectionConfig,
    ActivityThresholdMode,
    ChannelPowerRequest,
    ChannelPowerMode,
    ManualOverride,
    PowerSemantics,
    SmoothingMode,
    TimeGatedChannelPowerService,
)
from heatmap_test_isolation import shutdown_window


class _FakeWheelEvent:
    def __init__(self, delta: int, modifiers: Qt.KeyboardModifier) -> None:
        self._delta = delta
        self._modifiers = modifiers
        self.accepted = False

    def delta(self) -> int:
        return self._delta

    def angleDelta(self) -> Any:
        from PySide6.QtCore import QPoint
        return QPoint(0, self._delta)

    def pixelDelta(self) -> Any:
        from PySide6.QtCore import QPoint
        return QPoint(0, 0)

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers

    def accept(self) -> None:
        self.accepted = True


class UiRequirementsAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = MainWindow()
        self.session = MeasurementSession(
            "session", Path("synthetic.dfl"), "Synthetic", MeasurementMetadata()
        )
        self.trace1 = SpectrumTrace(
            "trace-1", "Trace 1", 100.0, 300.0, 100.0,
            np.array([-100.0, -90.0, -80.0], dtype=np.float32),
        )
        self.trace2 = SpectrumTrace(
            "trace-2", "Trace 2", 100.0, 300.0, 100.0,
            np.array([-1.0, -2.0, -3.0], dtype=np.float32), color="#ffb347",
        )
        self.session.traces = {self.trace1.trace_id: self.trace1, self.trace2.trace_id: self.trace2}
        self.session.active_trace_id = self.trace1.trace_id
        self.values = np.array(
            [
                [-80.0, -70.0, -60.0],
                [-50.0, -40.0, -30.0],
                [-20.0, -10.0, 0.0],
                [10.0, 20.0, 30.0],
            ],
            dtype=np.float32,
        )
        self.timestamps = np.array([0.0, 0.1, 0.5, 1.0], dtype=np.float64)
        self.waterfall = WaterfallData(
            "waterfall", "Waterfall", 4, 3, 100.0, 300.0, 100.0, "stream"
        )
        self.waterfall.set_preview(self.values, self.timestamps, np.arange(4))
        self.session.waterfalls[self.waterfall.waterfall_id] = self.waterfall
        self.session.active_waterfall_id = self.waterfall.waterfall_id
        info = SpectrogramInfo(
            "waterfall", "Waterfall", "RT", "Spectrum", "Spectrogram", "stream",
            4, 3, 100.0, 300.0,
        )
        self.index = SpectrogramIndex(
            info,
            np.arange(4, dtype=np.int64),
            self.timestamps.copy(),
            np.zeros(4, dtype=np.int64),
            np.ones(4, dtype=np.int32),
        )
        self.window.repository.add(self.session)
        self.window._spectrogram_indexes[("session", "waterfall")] = self.index
        for frame, row in enumerate(self.values):
            self.window._frame_loader._cache[("session", "waterfall", frame)] = SpectrogramRow(
                frame, self.timestamps[frame], row.copy()
            )
        self.window.set_active_session("session")
        self.window._show_frame(0)
        self.app.processEvents()
        # Ensure deterministic navigation settings for all tests.
        self.window._frame_nav.config.sequential_mode = False
        self.window._frame_scheduler.set_sequential_mode(False)
        self.window.no_skip_check.setChecked(False)
        self.window._frame_nav.config.wheel_step = 1
        self.window._frame_nav.reset(0)
        self.window.time_slider.setValue(0)

    def tearDown(self) -> None:
        if self.window._view_settings_dialog is not None:
            self.window._view_settings_dialog.close()
        shutdown_window(self.window, self.app)

    def _build_time_gated_result(self):
        config = ActivityDetectionConfig(
            threshold_mode=ActivityThresholdMode.ABSOLUTE,
            absolute_threshold_dbm=-35.0,
            smoothing_mode=SmoothingMode.NONE,
            min_active_frames=1,
            min_inactive_frames=1,
            max_gap_frames=0,
            merge_gap_frames=0,
        )
        request = ChannelPowerRequest(
            "session", "waterfall", 100.0, 300.0,
            activity_config=config,
            power_semantics=PowerSemantics.POWER_PER_BIN,
        )
        rows = [
            SpectrogramRow(index, self.timestamps[index], values.copy())
            for index, values in enumerate(self.values)
        ]
        return TimeGatedChannelPowerService().analyze(
            request, self.waterfall.frequencies_hz, rows
        )

    def test_all_frame_controls_clamp_and_synchronize_last_frame_without_off_by_one(self) -> None:
        self.assertEqual(self.window.time_slider.maximum(), 3)
        self.assertEqual(self.window.frame_spin.minimum(), 1)
        self.assertEqual(self.window.frame_spin.maximum(), 4)
        self.window.frame_spin.setValue(4)
        self.app.processEvents()
        self.assertEqual(self.window.time_slider.value(), 3)
        self.assertEqual(self.session.current_frame, 3)
        self.assertEqual(self.window.frame_spin.value(), 4)
        self.assertAlmostEqual(self.window.waterfall_renderer.time_cursor.value(), 4.0)
        self.assertEqual(self.window.waterfall_renderer.time_cursor.zValue(), 100.0)
        self.assertEqual(self.window.waterfall_renderer.time_cursor.label.format, "Кадр 4")
        image_rect = self.window.waterfall_renderer.image.mapRectToParent(
            self.window.waterfall_renderer.image.boundingRect()
        )
        self.assertAlmostEqual(image_rect.top(), 1.0)
        self.assertAlmostEqual(image_rect.bottom(), 4.0)
        _x, y = self.window.spectrum_renderer.items["trace-1"].getData()
        np.testing.assert_array_equal(y, self.values[3])
        self.assertIn("Кадр 4/4", self.window.status_cursor.text())
        self.window.frame_spin.setValue(999)
        self.assertEqual(self.window.frame_spin.value(), 4)
        self.window.frame_spin.setValue(-10)
        self.assertEqual(self.window.frame_spin.value(), 1)
        editor = self.window.frame_spin.lineEdit()
        editor.selectAll()
        QTest.keyClicks(editor, "999999")
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertEqual(self.window.frame_spin.value(), 4)

    def test_waterfall_wheel_scrolls_frames_accepts_event_and_never_zooms(self) -> None:
        self.window.time_slider.setValue(1)
        before = self.window.waterfall_renderer.plot.viewRange()
        event = _FakeWheelEvent(-120, Qt.KeyboardModifier.NoModifier)
        self.window.waterfall_renderer.view_box.wheelEvent(event)
        self.app.processEvents()
        self.assertTrue(event.accepted)
        # The first step of the wheel click is applied immediately.
        self.assertEqual(self.window.time_slider.value(), 2)
        np.testing.assert_allclose(self.window.waterfall_renderer.plot.viewRange(), before)
        parent = self.window.waterfall_renderer.widget.parentWidget()
        while parent is not None:
            self.assertNotIsInstance(parent, QScrollArea)
            parent = parent.parentWidget()

    def test_spectrum_wheel_uses_horizontal_axis_and_ctrl_uses_both_axes(self) -> None:
        plain = _FakeWheelEvent(120, Qt.KeyboardModifier.NoModifier)
        control = _FakeWheelEvent(120, Qt.KeyboardModifier.ControlModifier)
        with patch.object(pg.ViewBox, "wheelEvent", autospec=True) as inherited:
            SpectrumViewBox.wheelEvent(self.window.spectrum_renderer.view_box, plain)
            self.assertEqual(inherited.call_args.kwargs["axis"], 0)
            SpectrumViewBox.wheelEvent(self.window.spectrum_renderer.view_box, control)
            self.assertIsNone(inherited.call_args.kwargs["axis"])

    def test_reset_zoom_and_dynamic_range_dialog_refresh_do_not_conflict(self) -> None:
        item = self.window.spectrum_renderer.items["trace-1"]
        item.setData(self.trace1.x_values, np.array([-120.0, -10.0, 5.0]))
        self.window.spectrum_renderer.plot.setXRange(150.0, 180.0, padding=0)
        self.window.spectrum_renderer.plot.setYRange(-10.0, 10.0, padding=0)
        self.window._show_view_settings()
        dialog = self.window._view_settings_dialog
        self.assertIsNotNone(dialog)
        self.assertAlmostEqual(dialog.x_min.value(), 150.0)
        dialog.x_min.setValue(175.0)
        dialog.x_max.setValue(225.0)
        dialog.y_min.setValue(-55.0)
        dialog.y_max.setValue(-5.0)
        dialog.apply()
        self.assertAlmostEqual(self.window.spectrum_renderer.plot.viewRange()[0][0], 175.0)
        with patch.object(
            item,
            "getData",
            return_value=(np.array([200.0]), np.array([-10.0])),
        ):
            self.window._reset_zoom()
        x_range, y_range = self.window.spectrum_renderer.plot.viewRange()
        self.assertLessEqual(x_range[0], 100.0)
        self.assertGreaterEqual(x_range[1], 300.0)
        self.assertLessEqual(y_range[0], -120.0)
        self.assertGreaterEqual(y_range[1], 5.0)
        self.window._show_view_settings()
        self.assertAlmostEqual(dialog.x_min.value(), x_range[0])
        toolbar = self.window.findChild(QToolBar, "mainToolbar")
        actions = toolbar.actions()
        self.assertEqual(
            actions[actions.index(self.window.auto_scale_action) + 1],
            self.window.reset_zoom_action,
        )
        self.assertEqual(
            actions[actions.index(self.window.reset_zoom_action) + 1],
            self.window.view_settings_action,
        )

    def test_marker_snaps_to_raw_trace_and_switches_binding_explicitly(self) -> None:
        self.window._show_frame(2)
        marker = Marker(name="M1", frequency_hz=200.0, trace_id="trace-1")
        self.session.markers.append(marker)
        self.window.spectrum_renderer.set_marker(marker)
        self.window._connect_marker_line(marker)
        self.window._connect_marker_line(marker)
        line = self.window.spectrum_renderer.markers[marker.marker_id][0]
        with patch.object(self.window, "_marker_moved") as moved:
            line.setValue(210.0)
            self.app.processEvents()
            moved.assert_called_once()
        self.window._marker_moved(marker.marker_id, 260.0)
        self.assertEqual(marker.frequency_hz, 300.0)
        self.assertEqual(marker.power, -80.0)
        self.window._marker_trace_changed(marker.marker_id, "trace-2")
        self.assertEqual(marker.trace_id, "trace-2")
        self.assertEqual(marker.frequency_hz, 300.0)
        self.assertEqual(marker.power, -3.0)
        self.session.active_trace_id = "trace-1"
        self.assertEqual(marker.trace_id, "trace-2")

    def test_peak_search_uses_raw_current_frame(self) -> None:
        self.trace1.power_values = np.array([50.0, -100.0, -100.0], dtype=np.float32)
        self.window.spectrum_renderer.update_trace(
            self.trace1.trace_id,
            self.trace1.x_values,
            np.array([-80.0, -20.0, 7.0], dtype=np.float32),
        )
        self.window.add_peak_marker()
        marker = self.session.markers[-1]
        self.assertEqual(marker.trace_id, self.trace1.trace_id)
        self.assertEqual(marker.frequency_hz, 300.0)
        self.assertEqual(marker.power, 7.0)

    def test_timestamp_playback_controls_loop_and_current_measurement_stay_synchronized(self) -> None:
        result = self._build_time_gated_result()
        self.window._channel_power_results[("session", "waterfall")] = result
        self.window.speed_combo.setCurrentText("10×")
        self.window.time_slider.setValue(0)
        self.window._advance_frame()
        self.assertEqual(self.window.time_slider.value(), 2)
        self.assertEqual(self.window.frame_spin.value(), 3)
        self.assertEqual(self.session.current_frame, 2)
        self.assertAlmostEqual(self.window.waterfall_renderer.time_cursor.value(), 3.0)
        self.assertIn("dBm", self.window.current_frame_measurement.text())
        self.window.play()
        self.assertTrue(self.window.playback_timer.isActive())
        self.window.pause()
        self.assertFalse(self.window.playback_timer.isActive())
        self.window.next_frame()
        self.assertEqual(self.window.time_slider.value(), 3)
        self.window.previous_frame()
        self.assertEqual(self.window.time_slider.value(), 2)
        self.window.last_frame()
        self.window.loop_check.setChecked(True)
        self.window._advance_frame()
        self.assertEqual(self.window.time_slider.value(), 0)
        self.window.stop()
        self.assertEqual(self.window.time_slider.value(), 0)

    def test_ultraslow_multipliers_and_no_skip_mode_use_timestamp_period(self) -> None:
        speeds = [self.window.speed_combo.itemText(i) for i in range(self.window.speed_combo.count())]
        self.assertIn("0.0001×", speeds)
        self.assertIn("0.001×", speeds)
        self.assertIn("0.01×", speeds)
        self.window.speed_combo.setCurrentText("0.0001×")
        self.window._update_playback_interval()
        self.assertGreaterEqual(self.window.playback_timer.interval(), 1_000_000)
        self.window.speed_combo.setCurrentText("10×")
        self.window.no_skip_check.setChecked(True)
        self.window.time_slider.setValue(0)
        self.window._advance_frame()
        self.assertEqual(self.window.time_slider.value(), 1)

    def test_rapid_scrubbing_reuses_cache_and_navigation_connection_is_idempotent(self) -> None:
        self.window._connect_navigation()
        self.window.time_slider.setValue(0)
        # One mouse-wheel notch (120 angle units) moves one wheel step and
        # applies the resulting frame immediately from the loader cache.
        self.window.waterfall_renderer.view_box.frameWheel.emit(120, None, Qt.KeyboardModifier.NoModifier)
        self.app.processEvents()
        self.assertEqual(self.window.time_slider.value(), 1)
        started = time.perf_counter()
        with patch("esw_dfl.frame_navigation.read_spectrogram_frame") as reader:
            for index in range(400):
                self.window._show_frame(index % 4)
            self.app.processEvents()
            reader.assert_not_called()
        self.assertLess(time.perf_counter() - started, 3.0)
        self.assertLessEqual(len(self.window._frame_loader._cache), 256)

    def test_rapid_scrubbing_coalesces_to_latest_target(self) -> None:
        self.window.set_active_session("session")
        self.app.processEvents()
        # Clear cache so frame reads would be required for every target.
        self.window._frame_loader.clear_cache()
        # Rapidly request several frames; the controller coalesces them.
        self.window._show_frame(1)
        self.window._show_frame(2)
        self.window._show_frame(3)
        self.app.processEvents()
        self.assertEqual(self.window._frame_nav.requested_frame, 3)
        # Pending/active loader requests are limited; no backlog queue is built.
        self.assertLessEqual(
            self.window._frame_loader._diagnostics["active_loads"]
            + self.window._frame_loader._diagnostics["pending_loads"],
            2,
        )

    def test_frame_snapshot_applies_atomically(self) -> None:
        self.window.set_active_session("session")
        self.app.processEvents()
        self.window._show_frame(2)
        self.app.processEvents()
        self.assertEqual(self.window.time_slider.value(), 2)
        self.assertEqual(self.window.frame_spin.value(), 3)
        self.assertEqual(self.session.current_frame, 2)
        _x, y = self.window.spectrum_renderer.items["trace-1"].getData()
        np.testing.assert_array_equal(y, self.values[2])
        self.assertEqual(self.window.waterfall_renderer.time_cursor.value(), 3.0)
        self.assertEqual(self.window.waterfall_renderer.time_cursor.label.format, "Кадр 3")

    def test_target_seek_synchronizes_frame_input_before_snapshot(self) -> None:
        self.window.set_active_session("session")
        self.app.processEvents()
        self.window._frame_loader.clear_cache()
        self.window._show_frame(2)
        self.assertEqual(self.window.frame_spin.value(), 3)


    def test_scrubbing_direction_change_replaces_pending(self) -> None:
        self.window.set_active_session("session")
        self.app.processEvents()
        self.window._frame_loader.clear_cache()
        self.window._show_frame(3)
        self.window._show_frame(1)
        self.app.processEvents()
        self.assertEqual(self.window._frame_nav.requested_frame, 1)
        # Direction changed; no pending request for frame 3 should remain.
        if self.window._frame_loader._pending is not None:
            self.assertEqual(self.window._frame_loader._pending.frame_index, 1)

    def test_sequential_mode_advances_one_frame_per_playback_tick(self) -> None:
        self.window.set_active_session("session")
        self.app.processEvents()
        self.window.no_skip_check.setChecked(True)
        self.window.time_slider.setValue(0)
        self.window._frame_nav.reset(0)
        self.window._advance_frame()
        self.app.processEvents()
        self.assertEqual(self.window.time_slider.value(), 1)
        self.window._advance_frame()
        self.app.processEvents()
        self.assertEqual(self.window.time_slider.value(), 2)

    def test_latest_target_wins_during_playback(self) -> None:
        self.window.set_active_session("session")
        self.app.processEvents()
        self.window.speed_combo.setCurrentText("10×")
        self.window.time_slider.setValue(0)
        self.window._frame_nav.reset(0)
        self.window.play()
        self.assertTrue(self.window.playback_timer.isActive())
        # Simulate that playback started 100 ms ago so wall-clock target is ahead.
        self.window._playback_start_frame = 0
        self.window._playback_start_time = time.perf_counter() - 0.1
        self.window._advance_frame()
        self.app.processEvents()
        self.assertGreater(self.window.time_slider.value(), 0)
        self.window.pause()
        self.assertFalse(self.window.playback_timer.isActive())

    def test_frequency_view_cannot_pan_or_zoom_outside_full_data_range(self) -> None:
        self.window.spectrum_renderer.plot.setXRange(-100.0, 500.0, padding=0)
        self.app.processEvents()
        low, high = self.window.spectrum_renderer.plot.viewRange()[0]
        self.assertGreaterEqual(low, 100.0 - 1e-6)
        self.assertLessEqual(high, 300.0 + 1e-6)
        self.window.spectrum_renderer.plot.setXRange(150.0, 200.0, padding=0)
        self.window.spectrum_renderer.view_box.translateBy(x=1000.0)
        self.app.processEvents()
        low, high = self.window.spectrum_renderer.plot.viewRange()[0]
        self.assertGreaterEqual(low, 100.0 - 1e-6)
        self.assertLessEqual(high, 300.0 + 1e-6)

    def test_measurement_table_context_menu_can_disable_delete_and_clear_band(self) -> None:
        self.session.frequency_regions.clear()
        region = FrequencyRegion(start_frequency_hz=125.0, stop_frequency_hz=225.0)
        self.session.frequency_regions.append(region)
        result = AnalysisResult(
            "Channel Power", "Channel Power", {"power_dbm": -12.5},
            trace_id="trace-1", region_id=region.region_id,
        )
        self.session.analysis_results.append(result)
        self.window._refresh_measurement_table(self.session)
        self.window.measurement_results.selectRow(0)

        def choose(text: str):
            return lambda menu, *_args: next(
                action for action in menu.actions() if action.text() == text
            )

        with patch.object(
            self.window, "_exec_context_menu",
            side_effect=lambda menu: choose("Выключить выбранные результаты")(menu),
        ):
            self.window._measurement_context_menu(None)
        self.assertFalse(result.enabled)
        self.assertIn("[выкл.]", self.window.measurement_results.item(0, 0).text())

        self.window.measurement_results.selectRow(0)
        with patch.object(
            self.window, "_exec_context_menu",
            side_effect=lambda menu: choose("Удалить выбранные результаты")(menu),
        ):
            self.window._measurement_context_menu(None)
        self.assertEqual(self.session.analysis_results, [])
        self.assertEqual(self.window.measurement_results.rowCount(), 0)

        self.window.toggle_frequency_region()
        self.assertFalse(region.enabled)
        self.assertFalse(self.window.waterfall_renderer.frequency_region.isVisible())
        self.window.delete_frequency_region()
        self.assertEqual(self.session.frequency_regions, [])

    def test_local_tool_contexts_and_global_clear_remove_visual_artifacts(self) -> None:
        result = self._build_time_gated_result()
        self.window._channel_power_serial = 3
        self.window._time_gated_ready("session", "waterfall", 3, result)
        self.session.frequency_regions.append(
            FrequencyRegion(start_frequency_hz=100.0, stop_frequency_hz=300.0)
        )
        self.window._render_sessions()
        self.window.add_marker()
        self.assertEqual(
            self.window.marker_table.contextMenuPolicy(),
            Qt.ContextMenuPolicy.CustomContextMenu,
        )
        self.assertEqual(
            self.window.measurement_results.contextMenuPolicy(),
            Qt.ContextMenuPolicy.CustomContextMenu,
        )
        self.assertEqual(
            self.window.waterfall_renderer.widget.contextMenuPolicy(),
            Qt.ContextMenuPolicy.CustomContextMenu,
        )
        self.window.clear_all_analysis_tools()
        self.assertEqual(self.session.markers, [])
        self.assertEqual(self.session.frequency_regions, [])
        self.assertEqual(self.session.time_regions, [])
        self.assertEqual(self.window.cp_result_table.rowCount(), 0)
        self.assertEqual(self.window.events_table.rowCount(), 0)
        self.assertFalse(self.window.waterfall_renderer.frequency_region.isVisible())
        self.assertFalse(self.window.waterfall_renderer.time_region.isVisible())
        self.assertFalse(self.window.waterfall_renderer.noise_region.isVisible())

    def test_meaningful_user_and_program_actions_emit_structured_audit_events(self) -> None:
        with self.assertLogs("esw_dfl", level="INFO") as captured:
            self.window.speed_combo.setCurrentText("2×")
            self.window.play()
            self.window.pause()
            # One wheel notch (120 angle units) moves the slider immediately.
            self.window._waterfall_wheel(120, None, Qt.KeyboardModifier.NoModifier)
            self.window._reset_zoom()
            self.window.add_marker()

        events = {getattr(record, "event_name", None) for record in captured.records}
        self.assertTrue(
            {
                "playback_speed_changed",
                "playback_started",
                "playback_paused",
                "waterfall_wheel_step_queued",
                "frame_selected",
                "reset_zoom_requested",
                "marker_added",
            }.issubset(events)
        )
        frame_record = next(
            record for record in captured.records
            if getattr(record, "event_name", None) == "frame_selected"
        )
        self.assertEqual(frame_record.event_category, "navigation")
        self.assertIn("session_id", frame_record.event_details)

    def test_time_gated_result_updates_plot_events_overlays_and_manual_mask(self) -> None:
        result = self._build_time_gated_result()
        self.window._channel_power_serial = 7
        self.window._time_gated_ready("session", "waterfall", 7, result)
        self.assertGreater(self.window.cp_result_table.rowCount(), 10)
        self.assertEqual(self.window.cp_raw_curve.getData()[0].size, 4)
        self.assertEqual(self.window.events_table.rowCount(), len(result.events))
        self.assertEqual(
            len(self.window.waterfall_renderer.event_regions), len(result.events)
        )
        self.assertIn("Channel Power", self.window.current_frame_measurement.text())
        stale_worker = Mock()
        self.window._channel_power_worker = stale_worker
        serial = self.window._channel_power_serial
        self.window._invalidate_channel_power()
        stale_worker.cancel.assert_called_once()
        self.assertEqual(self.window._channel_power_serial, serial + 1)
        with patch.object(self.window, "run_time_gated_channel_power") as recalculate:
            self.window.cp_start_frame.setValue(2)
            self.window.cp_stop_frame.setValue(3)
            self.window._set_manual_override(ManualOverride.FORCE_ACTIVE)
            recalculate.assert_called_once()
        override = self.window._activity_overrides[("session", "waterfall")]
        np.testing.assert_array_equal(
            override, np.array([ManualOverride.AUTO, 1, 1, ManualOverride.AUTO])
        )

    def test_channel_power_panel_keeps_results_readable_and_recalculation_explicit(self) -> None:
        self.assertGreaterEqual(self.window.cp_result_table.minimumHeight(), 240)
        self.assertEqual(
            self.window.cp_result_table.parentWidget().objectName(),
            "channelPowerSettingsResultsSplitter",
        )
        with patch.object(self.window, "run_time_gated_channel_power") as run:
            self.window.cp_on_offset.setValue(self.window.cp_on_offset.value() + 1.0)
            self.app.processEvents()
            run.assert_not_called()
        self.assertIn("Нажмите", self.window.cp_recalc_status.text())

    def test_current_frame_measurement_uses_sparse_source_frame_index(self) -> None:
        result = self._build_time_gated_result()
        sparse = 3
        result.series.frame_indices = np.array([sparse], dtype=np.int64)
        result.series.power_dbm = result.series.power_dbm[:1]
        result.series.power_mw = result.series.power_mw[:1]
        result.series.timestamps_s = result.series.timestamps_s[:1]
        result.series.valid_mask = result.series.valid_mask[:1]
        result.activity.effective_activity_mask = result.activity.effective_activity_mask[:1]
        self.window._channel_power_results[("session", "waterfall")] = result
        self.session.current_frame = sparse
        self.window._sync_current_frame_measurement()
        self.assertNotIn("—", self.window.current_frame_measurement.text())

    def test_current_frame_random_access_preserves_source_frame_number(self) -> None:
        request = ChannelPowerRequest(
            session_id="session",
            trace_id="waterfall",
            frequency_start_hz=100.0,
            frequency_stop_hz=300.0,
            mode=ChannelPowerMode.CURRENT_FRAME,
            selected_frame_index=3,
            activity_config=ActivityDetectionConfig(enabled=False),
        )
        with patch(
            "esw_dfl.gui.read_spectrogram_frame",
            return_value=SpectrogramRow(3, self.timestamps[3], self.values[3]),
        ):
            result = _analyze_time_gated_waterfall(
                self.window.time_gated_service,
                Path("synthetic.dfl"),
                self.index.info,
                self.waterfall.frequencies_hz,
                request,
                np.zeros(4, dtype=np.int8),
                self.index,
            )
        self.assertEqual(result.series.frame_indices.tolist(), [3])
        self.assertEqual(result.series.timestamps_s.tolist(), [self.timestamps[3]])

    def test_power_measurement_profile_regions_and_named_sources_are_editable(self) -> None:
        self.assertGreaterEqual(self.window.power_source.findData("trace:trace-1"), 0)
        profile_index = self.window.power_profile.findData("Wi-Fi 20 MHz")
        self.window.power_profile.setCurrentIndex(profile_index)
        self.assertEqual(self.window.cp_bandwidth.value(), 20.0)
        self.window._power_add_region()
        self.assertEqual(self.window.power_regions_table.rowCount(), 1)
        self.window.power_regions_table.item(0, 1).setText("noise")
        regions = self.window._power_regions()
        self.assertEqual(regions[0].role.value, "noise")
        self.window.power_regions_table.selectRow(0)
        self.window._power_remove_region()
        self.assertEqual(self.window.power_regions_table.rowCount(), 0)

    def test_power_measurement_selector_dispatches_time_gated_mode(self) -> None:
        self.assertEqual(self.window.power_measurement_mode.currentText(), "Time-Gated Channel Power")
        with patch.object(self.window, "run_time_gated_channel_power") as run:
            self.window._run_selected_power_measurement()
            run.assert_called_once()

    def test_channel_power_recalculation_never_runs_overlapping_workers(self) -> None:
        snapshot = self.window._channel_power_request()
        self.assertIsNotNone(snapshot)
        session, waterfall, _index, request, overrides = snapshot
        active_worker = Mock()
        self.window._channel_power_worker = active_worker
        with patch.object(self.window, "_channel_power_request", return_value=snapshot), patch.object(
            self.window, "_start_channel_power_request"
        ) as start:
            self.window.run_time_gated_channel_power()
            active_worker.cancel.assert_called_once()
            start.assert_not_called()
            pending = self.window._pending_channel_power_request
            self.assertIsNotNone(pending)
            self.assertIs(pending[0], session)
            self.assertIs(pending[1], waterfall)
            self.assertIs(pending[2], self.index)
            self.assertEqual(pending[3], request)
            np.testing.assert_array_equal(pending[4], overrides)
            self.window._channel_power_worker_finished(active_worker)
            self.app.processEvents()
            start.assert_called_once()
        self.window._channel_power_worker = None
        self.window._pending_channel_power_request = None

    def test_remove_session_deletes_all_indexes_and_closes_all_readers(self) -> None:
        """Regression for P01-SES-001: tuple-key indexes/readers must all go."""
        second_waterfall = WaterfallData(
            "waterfall-2", "Waterfall 2", 4, 3, 100.0, 300.0, 100.0, "stream"
        )
        second_waterfall.set_preview(self.values.copy(), self.timestamps.copy(), np.arange(4))
        self.session.waterfalls[second_waterfall.waterfall_id] = second_waterfall
        info2 = SpectrogramInfo(
            "waterfall-2", "Waterfall 2", "RT", "Spectrum", "Spectrogram", "stream",
            4, 3, 100.0, 300.0,
        )
        index2 = SpectrogramIndex(
            info2,
            np.arange(4, dtype=np.int64),
            self.timestamps.copy(),
            np.zeros(4, dtype=np.int64),
            np.ones(4, dtype=np.int32),
        )
        reader1 = Mock(spec=SpectrogramFrameReader)
        reader2 = Mock(spec=SpectrogramFrameReader)
        self.window._spectrogram_indexes[("session", "waterfall")] = self.index
        self.window._spectrogram_indexes[("session", "waterfall-2")] = index2
        self.window._frame_readers[("session", "waterfall")] = reader1
        self.window._frame_readers[("session", "waterfall-2")] = reader2

        with patch(
            "esw_dfl.gui.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window._remove_session("session")

        self.assertIsNone(
            next((s for s in self.window.repository.all() if s.session_id == "session"), None)
        )
        self.assertEqual(
            [k for k in self.window._spectrogram_indexes if k[0] == "session"],
            [],
        )
        self.assertEqual(
            [k for k in self.window._frame_readers if k[0] == "session"],
            [],
        )
        reader1.close.assert_called_once()
        reader2.close.assert_called_once()

    def test_remove_session_cancels_pending_frame_load_for_session(self) -> None:
        """Regression for P01-SES-001: pending exact-frame work must be discarded."""
        pending = Mock()
        pending.session_id = "session"
        self.window._frame_loader._pending = pending

        with patch(
            "esw_dfl.gui.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window._remove_session("session")

        self.assertIsNone(self.window._frame_loader._pending)
        self.assertEqual(self.window._frame_loader._diagnostics["pending_loads"], 0)

    def test_remove_session_cancels_active_channel_power_worker_for_session(self) -> None:
        """Regression for P01-SES-001: stale channel-power result must not land."""
        worker = Mock()
        self.window._channel_power_worker = worker
        self.window._channel_power_session_id = "session"

        with patch(
            "esw_dfl.gui.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window._remove_session("session")

        worker.cancel.assert_called_once()
        self.assertIsNone(self.window._channel_power_session_id)


if __name__ == "__main__":
    unittest.main()
