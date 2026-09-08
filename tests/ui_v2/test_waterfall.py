"""Synthetic/offscreen contract tests for the UI2-06 bounded waterfall pane."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

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
        labels = pane._time_axis.tickStrings([0.0, 15.0, 30.0], 1.0, 1.0)
        self.assertEqual(labels[0], "−0 мс")
        self.assertEqual(labels[-1], "−1.0 с")

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
        labels = pane._time_axis.tickStrings([0.0, float(pane.history_rows)], 1.0, 1.0)
        self.assertGreaterEqual(float(labels[0][1:].split()[0]), float(labels[-1][1:].split()[0]))

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
