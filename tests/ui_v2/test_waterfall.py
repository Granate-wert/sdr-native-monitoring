"""Synthetic/offscreen contract tests for the UI2-06 bounded waterfall pane."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.waterfall import (
    SpectrumWaterfallView,
    WaterfallDirection,
    WaterfallLineFrame,
    WaterfallPane,
    WaterfallPalette,
)


def _line(
    value: float,
    *,
    timestamp_ns: int,
    generation: int = 1,
    columns: int = 8,
    start_hz: float = 433_000_000.0,
    stop_hz: float = 434_000_000.0,
) -> WaterfallLineFrame:
    return WaterfallLineFrame(
        values=np.full(columns, value, dtype=np.float32),
        frequency_edges_hz=np.linspace(start_hz, stop_hz, columns + 1, dtype=np.float64),
        timestamp_ns=timestamp_ns,
        configuration_generation=generation,
        unit_label="dBm",
    )


class WaterfallContractTests(unittest.TestCase):
    def test_physical_edges_and_presentation_budget_fail_closed(self) -> None:
        line = _line(-90.0, timestamp_ns=1)
        self.assertEqual(line.frequency_edges_hz.size, line.values.size + 1)
        self.assertEqual(line.grid_signature.columns, 8)
        with self.assertRaisesRegex(ValueError, "regular physical frequency"):
            WaterfallLineFrame(
                values=np.ones(2),
                frequency_edges_hz=np.array((100.0, 101.0, 103.0)),
                timestamp_ns=1,
                configuration_generation=1,
                unit_label="dBm",
            )
        with self.assertRaisesRegex(ValueError, "presentation budget"):
            _line(-90.0, timestamp_ns=1, columns=2049)


class WaterfallPaneTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self._widgets: list[WaterfallPane | SpectrumWaterfallView] = []

    def tearDown(self) -> None:
        for widget in self._widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        self._temporary.cleanup()

    def test_ring_order_wrap_and_monotonic_relative_time_axis(self) -> None:
        pane = self._pane()
        pane.set_history_seconds(1)
        for value in range(32):
            pane.set_line(_line(float(value), timestamp_ns=value * 40_000_000))
        self.assertEqual(pane.history_rows, 30)
        tiles = pane._renderer.tiles()
        self.assertEqual(len(tiles), 2)
        ordered = np.concatenate(tiles, axis=0)
        np.testing.assert_array_equal(ordered[:, 0], np.arange(2, 32, dtype=np.float32))
        labels = pane._time_axis.tickStrings([0.0, 15.0, 29.0, 30.0], 1.0, 1.0)
        self.assertEqual(labels[0], "−0 мс")
        # Producer timestamps, not display cadence, own the age axis.
        self.assertEqual(labels[-2], "−1.2 с")
        self.assertEqual(labels[-1], "")

    def test_irregular_producer_time_is_preserved_and_pause_marked(self) -> None:
        pane = self._pane()
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000))
        pane.set_line(_line(-80.0, timestamp_ns=1_040_000_000))
        pane.set_line(_line(-70.0, timestamp_ns=5_000_000_000))
        labels = pane._time_axis.tickStrings([0.0, 1.0, 2.0], 1.0, 1.0)
        self.assertTrue(labels[0].startswith("−0 мс"))
        self.assertIn("⏸", labels[0])
        self.assertEqual(labels[-1], "−4.0 с")

    def test_empty_waterfall_axis_has_no_repeated_zero_age_labels(self) -> None:
        pane = self._pane()
        self.assertEqual(pane.history_rows, 0)
        self.assertEqual(pane._time_axis.tickStrings([0.0, 0.5, 1.0], 1.0, 0.5), ["", "", ""])

    def test_clear_freeze_and_hidden_suppression_are_local_only(self) -> None:
        pane = self._pane()
        pane.set_render_visible(False)
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000))
        self.assertEqual(pane.history_rows, 1)
        self.assertEqual(pane.metrics.image_uploads, 0)
        self.assertEqual(pane.metrics.hidden_uploads_suppressed, 1)
        pane.set_render_visible(True)
        self.assertGreater(pane.metrics.image_uploads, 0)
        pane.set_frozen(True)
        pane.set_line(_line(-80.0, timestamp_ns=2_000_000_000))
        self.assertEqual(pane.history_rows, 1)
        self.assertEqual(pane.metrics.rows_frozen_suppressed, 1)
        pane.clear_history()
        self.assertEqual(pane.history_rows, 0)
        self.assertEqual(pane.metrics.local_history_clears, 1)

    def test_visible_row_upload_does_not_repeat_rtbw_axis_synchronization(self) -> None:
        pane = self._pane()
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            self.assertTrue(pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000)))
        update_axis.assert_called_once_with()
        upload.assert_called_once_with(axis_already_updated=True)

    def test_visible_row_upload_does_not_repeat_sweep_axis_synchronization(self) -> None:
        from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
        from tests.ui_v2.test_app04_progressive_waterfall import progress

        pane = self._pane()
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            self.assertTrue(pane.set_sweep_line(waterfall_line_from_sweep(progress())))
        update_axis.assert_called_once_with()
        upload.assert_called_once_with(axis_already_updated=True)

    def test_hidden_row_admission_keeps_axis_sync_without_upload(self) -> None:
        pane = self._pane()
        pane.set_render_visible(False)
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            self.assertTrue(pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000)))
        update_axis.assert_called_once_with()
        upload.assert_not_called()

    def test_default_upload_keeps_axis_sync_for_configuration_paths(self) -> None:
        pane = self._pane()
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000))
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            pane.set_direction(WaterfallDirection.NEWEST_AT_BOTTOM)
        update_axis.assert_called_once_with()
        upload.assert_called_once_with(axis_already_updated=True)

    def test_activation_reuses_its_axis_sync_for_visible_history(self) -> None:
        pane = self._pane()
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000))
        pane.set_presentation_active(False)
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            pane.set_presentation_active(True)
        update_axis.assert_called_once_with()
        upload.assert_called_once_with(axis_already_updated=True)

    def test_successful_history_configuration_reuses_axis_sync(self) -> None:
        pane = self._pane()
        line = _line(-90.0, timestamp_ns=1_000_000_000)
        pane.set_line(line)
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            pane.set_history_seconds(pane.config.history_seconds + 1)
        update_axis.assert_called_once_with()
        upload.assert_called_once_with(axis_already_updated=True)
        self.assertEqual(pane.history_rows, 1)
        self.assertEqual(pane._renderer.timestamps_ns().tolist(), [line.timestamp_ns])

    def test_successful_sweep_history_configuration_preserves_unknown_time_and_stamp(self) -> None:
        from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
        from tests.ui_v2.test_app04_progressive_waterfall import progress

        pane = self._pane()
        sweep = waterfall_line_from_sweep(progress())
        self.assertTrue(pane.set_sweep_line(sweep))
        stamps_before = pane._renderer.sweep_stamps()
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            pane.set_history_seconds(pane.config.history_seconds + 1)
        update_axis.assert_called_once_with()
        upload.assert_called_once_with(axis_already_updated=True)
        self.assertFalse(pane.grid_signature.timestamp_known)
        self.assertEqual(pane._time_axis._timestamps_ns.size, 0)
        self.assertEqual(pane._renderer.sweep_stamps(), stamps_before)

    def test_refused_history_configuration_does_not_sync_or_upload(self) -> None:
        pane = self._pane()
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000, columns=2048))
        pane.set_render_visible(False)
        config_before = pane.config
        timestamps_before = pane._renderer.timestamps_ns().copy()
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            pane.set_history_seconds(1000)
        update_axis.assert_not_called()
        upload.assert_not_called()
        self.assertEqual(pane.config, config_before)
        np.testing.assert_array_equal(pane._renderer.timestamps_ns(), timestamps_before)

    def test_inactive_and_hidden_rows_keep_admission_sync_without_upload(self) -> None:
        pane = self._pane()
        pane.set_presentation_active(False)
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            self.assertTrue(pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000)))
        update_axis.assert_called_once_with()
        upload.assert_not_called()
        self.assertEqual(pane.history_rows, 1)

    def test_direct_upload_and_show_keep_default_axis_sync(self) -> None:
        pane = self._pane()
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000))
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            pane._upload_tiles()
        update_axis.assert_called_once_with()
        upload.assert_called_once_with()

        pane.set_render_visible(False)
        with patch.object(pane, "_update_time_axis", wraps=pane._update_time_axis) as update_axis, \
             patch.object(pane, "_upload_tiles", wraps=pane._upload_tiles) as upload:
            pane.set_render_visible(True)
        update_axis.assert_called_once_with()
        upload.assert_called_once_with()

    def test_grid_change_clears_history_and_never_stretches_old_rows(self) -> None:
        pane = self._pane()
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000, generation=1))
        pane.set_line(_line(-80.0, timestamp_ns=2_000_000_000, generation=2, stop_hz=435_000_000.0))
        self.assertEqual(pane.epoch, 2)
        self.assertEqual(pane.metrics.grid_epoch_resets, 1)
        self.assertEqual(pane.history_rows, 1)
        assert pane.grid_signature is not None
        self.assertEqual(pane.grid_signature.last_edge_hz, 435_000_000.0)
        self.assertTrue(np.allclose(pane._renderer.tiles()[0], -80.0))

    def test_explicit_same_grid_segment_keeps_history_with_blank_gap(self) -> None:
        pane = self._pane()
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000, generation=1))
        pane.set_line(_line(-80.0, timestamp_ns=1_040_000_000, generation=1))
        pane.set_line(_line(-70.0, timestamp_ns=2_000_000_000, generation=2), segment_boundary=True)
        ordered = np.concatenate(pane._renderer.tiles(), axis=0)
        self.assertEqual(pane.history_rows, 4)
        np.testing.assert_array_equal(ordered[:, 0], np.array((-90, -80, np.nan, -70), dtype=np.float32))
        self.assertTrue(np.isnan(ordered[2]).all())
        self.assertEqual(pane.metrics.presentation_gap_rows, 1)
        self.assertEqual(pane.metrics.grid_epoch_resets, 0)
        self.assertEqual(pane.grid_signature.configuration_generation, 2)
        pane.set_line(_line(-60.0, timestamp_ns=3_000_000_000, generation=3,
                            stop_hz=435_000_000.0), segment_boundary=True)
        self.assertEqual(pane.history_rows, 1)  # A changed physical grid still resets.
        self.assertEqual(pane.metrics.grid_epoch_resets, 1)

    def test_generation_change_without_handoff_or_backward_time_resets_history(self) -> None:
        pane = self._pane()
        pane.set_line(_line(-90.0, timestamp_ns=2_000_000_000, generation=1))
        pane.set_line(_line(-80.0, timestamp_ns=3_000_000_000, generation=2))
        self.assertEqual(pane.history_rows, 1)  # The generic caller did not opt in.
        self.assertEqual(pane.metrics.presentation_gap_rows, 0)
        pane.set_line(_line(-70.0, timestamp_ns=1_000_000_000, generation=3),
                      segment_boundary=True)
        self.assertEqual(pane.history_rows, 1)
        self.assertEqual(pane.metrics.presentation_gap_rows, 0)
        self.assertEqual(pane.metrics.grid_epoch_resets, 2)

    def test_levels_palette_follow_and_direction_only_change_presentation(self) -> None:
        pane = self._pane()
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000))
        uploads = pane.metrics.image_uploads
        pane.set_levels(-110.0, -50.0)
        self.assertEqual(pane.metrics.image_uploads, uploads)
        self.assertEqual(pane.metrics.level_only_updates, 1)
        pane.set_palette(WaterfallPalette.TURBO)
        self.assertEqual(pane.metrics.palette_only_updates, 1)
        self.assertTrue(pane.follow_spectrum_levels(-100.0, -40.0, unit_label="dBm"))
        self.assertFalse(pane.follow_spectrum_levels(-100.0, -40.0, unit_label="dBFS/bin"))
        pane.set_direction(WaterfallDirection.NEWEST_AT_BOTTOM)
        capacity, _ = pane.config.dimensions(pane.grid_signature.columns)  # type: ignore[union-attr]
        labels = pane._time_axis.tickStrings([0.0, float(capacity - 1), float(capacity)], 1.0, 1.0)
        self.assertEqual(labels[0], "")
        self.assertTrue(labels[1].startswith("−0 мс"))
        self.assertEqual(labels[2], "")

    def test_one_row_uses_one_history_cell_and_ticks_only_name_acquired_rows(self) -> None:
        pane = self._pane()
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000))
        assert pane.grid_signature is not None
        capacity, _ = pane.config.dimensions(pane.grid_signature.columns)
        top_labels = pane._time_axis.tickStrings([0.0, 1.0, float(capacity - 1)], 1.0, 1.0)
        self.assertTrue(top_labels[0].startswith("−0 мс"))
        self.assertEqual(top_labels[1:], ["", ""])
        top_range = pane.view_box.viewRange()[1]
        self.assertAlmostEqual(top_range[0], 0.0)
        self.assertAlmostEqual(top_range[1], float(capacity))

        pane.set_direction(WaterfallDirection.NEWEST_AT_BOTTOM)
        bottom_labels = pane._time_axis.tickStrings(
            [0.0, float(capacity - 2), float(capacity - 1), float(capacity)], 1.0, 1.0,
        )
        self.assertEqual(bottom_labels[:2], ["", ""])
        self.assertTrue(bottom_labels[2].startswith("−0 мс"))
        self.assertEqual(bottom_labels[3], "")

    def test_full_capacity_keeps_all_rows_addressable_in_both_directions(self) -> None:
        for direction in (WaterfallDirection.NEWEST_AT_TOP, WaterfallDirection.NEWEST_AT_BOTTOM):
            with self.subTest(direction=direction):
                pane = self._pane()
                pane.set_history_seconds(1)
                pane.set_direction(direction)
                for value in range(30):
                    pane.set_line(_line(float(value), timestamp_ns=value * 40_000_000))
                assert pane.grid_signature is not None
                capacity, _ = pane.config.dimensions(pane.grid_signature.columns)
                self.assertEqual(pane.history_rows, capacity)
                labels = pane._time_axis.tickStrings([0.0, float(capacity - 1), float(capacity)], 1.0, 1.0)
                self.assertNotEqual(labels[0], "")
                self.assertNotEqual(labels[1], "")
                self.assertEqual(labels[2], "")

    def test_follow_checkbox_uses_connected_spectrum_range_signal(self) -> None:
        view = self._view(self._settings())
        pane = view.waterfall_pane
        pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000))
        frame = SimpleNamespace(
            frequencies_hz=np.linspace(433_000_000.0, 434_000_000.0, 8),
            values=np.linspace(-130.0, -10.0, 8),
            unit="dBm",
        )
        view.spectrum_scene.set_frame(frame)
        self.assertLessEqual(pane.config.level_min, -130.0)
        self.assertGreaterEqual(pane.config.level_max, -10.0)
        pane.set_follow_spectrum_levels(False)
        before = (pane.config.level_min, pane.config.level_max)
        view.spectrum_scene.set_reference_level(-30.0)
        self.assertEqual((pane.config.level_min, pane.config.level_max), before)

    def test_history_rate_transition_rejects_incompatible_budget_without_qt_exception(self) -> None:
        pane = self._pane()
        pane.set_history_seconds(100)
        pane.set_rows_per_second(120)
        self.assertEqual(pane.config.history_seconds, 100)
        self.assertEqual(pane.config.rows_per_second, 30)
        self.assertEqual(pane.metrics.configuration_rejections, 1)
        self.assertEqual(pane._rows_per_second.currentData(), 30)

    def test_splitter_default_restore_and_linked_frequency_view(self) -> None:
        settings = self._settings()
        first = self._view(settings)
        first.resize(1200, 760)
        first.show()
        self.app.processEvents()
        initial = first.splitter.sizes()
        self.assertEqual(first.splitter.orientation().value, 2)
        self.assertGreaterEqual(initial[1], 120)
        self.assertGreater(initial[0], initial[1])
        self.assertAlmostEqual(initial[0] / sum(initial), 0.60, delta=0.08)
        first.splitter.setSizes([420, 260])
        first.flush_settings()
        restored = self._view(settings)
        restored.resize(1200, 760)
        restored.show()
        self.app.processEvents()
        sizes = restored.splitter.sizes()
        self.assertGreater(sizes[0], sizes[1])
        first.spectrum_scene.plot_item.setXRange(433_000_000.0, 434_000_000.0, padding=0.0)
        self.app.processEvents()
        waterfall_range = first.waterfall_pane.view_box.viewRange()[0]
        self.assertAlmostEqual(waterfall_range[0], 433_000_000.0, places=1)
        self.assertAlmostEqual(waterfall_range[1], 434_000_000.0, places=1)

    def test_first_waterfall_row_makes_its_declared_grid_visible_before_spectrum(self) -> None:
        view = self._view(self._settings())
        view.resize(1000, 700)
        view.show()
        view.waterfall_pane.set_line(_line(-90.0, timestamp_ns=1_000_000_000))
        self.app.processEvents()
        spectrum_range = view.spectrum_scene.view_box.viewRange()[0]
        waterfall_range = view.waterfall_pane.view_box.viewRange()[0]
        self.assertAlmostEqual(spectrum_range[0], 433_000_000.0, places=1)
        self.assertAlmostEqual(spectrum_range[1], 434_000_000.0, places=1)
        self.assertAlmostEqual(waterfall_range[0], 433_000_000.0, places=1)
        self.assertAlmostEqual(waterfall_range[1], 434_000_000.0, places=1)

    def test_presentation_settings_restore_without_retaining_history_rows(self) -> None:
        settings = self._settings()
        first = self._view(settings)
        pane = first.waterfall_pane
        pane.set_rows_per_second(60)
        pane.set_history_seconds(2)
        pane.set_palette(WaterfallPalette.CIVIDIS)
        pane.set_levels(-100.0, -40.0)
        pane.set_follow_spectrum_levels(False)
        pane.set_direction(WaterfallDirection.NEWEST_AT_BOTTOM)
        pane.set_render_visible(False)
        pane.set_line(_line(-80.0, timestamp_ns=1_000_000_000))
        first.flush_settings()
        restored = self._view(settings).waterfall_pane
        self.assertEqual(restored.config.rows_per_second, 60)
        self.assertEqual(restored.config.history_seconds, 2)
        self.assertEqual(restored.config.palette, WaterfallPalette.CIVIDIS)
        self.assertEqual((restored.config.level_min, restored.config.level_max), (-100.0, -40.0))
        self.assertFalse(restored.config.follow_spectrum_levels)
        self.assertEqual(restored.config.direction, WaterfallDirection.NEWEST_AT_BOTTOM)
        self.assertEqual(restored.history_rows, 0)

    def _pane(self) -> WaterfallPane:
        pane = WaterfallPane(settings=self._settings())
        self._widgets.append(pane)
        return pane

    def _view(self, settings: QSettings) -> SpectrumWaterfallView:
        view = SpectrumWaterfallView(settings=settings)
        self._widgets.append(view)
        return view

    def _settings(self) -> QSettings:
        return QSettings(str(Path(self._temporary.name) / "ui2-waterfall.ini"), QSettings.Format.IniFormat)
