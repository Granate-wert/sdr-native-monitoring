"""Actual WaterfallPane integration for the optional incremental raster path."""

from __future__ import annotations

import gc
import os
import tempfile
import unittest
import weakref
from dataclasses import replace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
from sdr_monitor.ui.v2.waterfall import WaterfallPane, WaterfallPalette
from sdr_monitor.ui.v2.waterfall.contracts import WaterfallLineFrame
from tests.test_app07_protected_waterfall_accounting import (
    explicit_native_support,
    restore_explicit_native_support,
)
from tests.ui_v2.test_app04_progressive_waterfall import progress


def _line(value: float, timestamp_ns: int, *, columns: int = 8, generation: int = 1):
    return WaterfallLineFrame(
        values=np.linspace(value - 3.0, value + 3.0, columns, dtype=np.float32),
        frequency_edges_hz=np.linspace(100.0, 100.0 + columns, columns + 1, dtype=np.float64),
        timestamp_ns=timestamp_ns,
        configuration_generation=generation,
        unit_label="dBm",
    )


def _pixels(image: QImage):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return (rgba.width(), rgba.height(), rgba.format(),
            bytes(rgba.constBits())[:rgba.sizeInBytes()])


class IncrementalWaterfallPaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.native, cls.support = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls):
        restore_explicit_native_support(cls)

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.budget = PresentationAllocationBudget(16 * 1024 * 1024)
        self.assertTrue(self.budget.install_waterfall_storage_support(self.support))
        self.pane = WaterfallPane(settings=QSettings(
            self._temporary.name + "/settings.ini", QSettings.Format.IniFormat,
        ))
        self.pane._renderer.allocation_budget = self.budget

    def tearDown(self):
        self.pane.close()
        self.pane.deleteLater()
        self.app.processEvents()
        self._temporary.cleanup()

    def _assert_original_item_pixels(self):
        visible = [item for item in self.pane._image_items if item.isVisible()]
        self.assertTrue(visible)
        for item in visible:
            item.render()
            self.assertIsNotNone(item.qimage)
            # Use the original ImageItem implementation on the exact retained
            # float image, levels, LUT, and row-major axis order.
            import pyqtgraph as pg

            reference_item = pg.ImageItem(axisOrder="row-major")
            reference_item.setLookupTable(item.lut)
            reference_item.setImage(item.image, autoLevels=False, levels=item.levels)
            reference_item.render()
            self.assertIsNotNone(reference_item.qimage)
            self.assertEqual(_pixels(item.qimage), _pixels(reference_item.qimage))

    def test_real_pane_pixels_palette_levels_sweep_and_clear(self):
        self.pane.set_history_seconds(1)
        for index in range(3):
            self.assertTrue(self.pane.set_line(_line(-60.0 + index, index * 40_000_000 + 1)))
        self._assert_original_item_pixels()
        self.assertGreater(self.pane.raster_metrics[0], 0)
        self.assertGreater(self.pane.raster_metrics[4], 0)
        self.assertTrue(any(item.raster_fast_renders for item in self.pane._image_items))

        converted_before_policy = self.pane.raster_metrics[0]
        self.pane.set_palette(WaterfallPalette.TURBO)
        self.pane.set_levels(-95.0, -35.0)
        self._assert_original_item_pixels()
        self.assertGreater(self.pane.raster_metrics[0], converted_before_policy)

        sweep = waterfall_line_from_sweep(progress())
        self.assertTrue(self.pane.set_sweep_line(sweep))
        # The actual pane accepts a newer partial Sweep revision in place,
        # then rejects the stale revision without replacing the admitted row.
        before_rows = self.pane.history_rows
        before_updates = self.pane.metrics.sweep_rows_updated
        before_rejections = self.pane.metrics.sweep_updates_rejected
        self.assertTrue(self.pane.set_sweep_line(
            waterfall_line_from_sweep(progress(revision=2)),
        ))
        self.assertEqual(self.pane.history_rows, before_rows)
        self.assertEqual(self.pane.metrics.sweep_rows_updated, before_updates + 1)
        self.assertFalse(self.pane.set_sweep_line(
            waterfall_line_from_sweep(progress(revision=1)),
        ))
        self.assertEqual(self.pane.history_rows, before_rows)
        self.assertEqual(self.pane.metrics.sweep_updates_rejected, before_rejections + 1)
        self._assert_original_item_pixels()
        self.assertTrue(self.pane._sweep_mode)

        retained = self.pane._image_items[0].qimage.copy()
        self.pane.clear_history()
        self.assertEqual(self.pane.history_rows, 0)
        self.assertIsNone(self.pane._raster_cache.plane)
        self.assertFalse(any(item.isVisible() for item in self.pane._image_items))
        # A detached QImage remains a caller-owned snapshot after cache retire.
        self.assertGreater(retained.width() * retained.height(), 0)

    def test_grid_epoch_hide_resize_and_terminal_release_retire_plane(self):
        self.pane.set_history_seconds(1)
        self.assertTrue(self.pane.set_line(_line(-70.0, 1)))
        self._assert_original_item_pixels()
        self.assertIsNotNone(self.pane._raster_cache.plane)

        self.assertTrue(self.pane.set_line(_line(-69.0, 40_000_001, columns=12, generation=2)))
        self.assertEqual(self.pane.epoch, 2)
        self._assert_original_item_pixels()
        self.assertIsNotNone(self.pane._raster_cache.plane)

        self.pane.set_render_visible(False)
        self.assertIsNone(self.pane._raster_cache.plane)
        self.assertTrue(self.pane.set_line(_line(-68.0, 80_000_001, columns=12, generation=2)))
        self.assertIsNone(self.pane._raster_cache.plane)
        self.pane.set_render_visible(True)
        self._assert_original_item_pixels()
        self.assertIsNotNone(self.pane._raster_cache.plane)

        self.pane.release_presentation_after_shutdown()
        gc.collect()
        self.assertIsNone(self.pane._raster_cache.plane)

    def test_compatible_rtbw_and_sweep_handoffs_retire_only_color_plane(self):
        self.pane.set_history_seconds(1)
        self.assertTrue(self.pane.set_line(_line(-72.0, 1)))
        self._assert_original_item_pixels()
        old_ring = self.pane._renderer.buffer
        old_plane = self.pane._raster_cache.plane
        old_plane_ref = weakref.ref(old_plane)
        old_item_image = QImage(self.pane._image_items[0].qimage)
        old_pixels = _pixels(old_item_image)
        del old_plane

        self.assertTrue(self.pane.set_line(
            _line(-71.0, 40_000_001, generation=2), segment_boundary=True,
        ))
        self.assertIs(self.pane._renderer.buffer, old_ring)
        self.assertEqual(self.pane.epoch, 2)
        self.assertIsNotNone(old_plane_ref())
        self.assertEqual(_pixels(old_item_image), old_pixels)
        self.assertEqual(self.pane.history_rows, 3)

        # The old serialized view keeps only its full RGBA root charged. A
        # second plane that does not fit must fall back without replacing rows.
        ring = self.pane._renderer.buffer
        payload = (ring.rows * ring.columns * 4 + ring.rows * 8 + ring.rows + 1024)
        self.budget.limit_bytes = self.budget.snapshot().observed_bytes + payload - 1
        item = self.pane._image_items[0]
        before_fallbacks = item.raster_fallback_renders
        item.render()
        self.assertIsNone(self.pane._raster_cache.plane)
        self.assertGreater(item.raster_fallback_renders, before_fallbacks)
        self.assertEqual(self.pane.history_rows, 3)

        del old_item_image
        gc.collect()
        self.assertIsNone(old_plane_ref())
        self.budget.limit_bytes = 16 * 1024 * 1024
        self._assert_original_item_pixels()
        self.assertIsNotNone(self.pane._raster_cache.plane)

        self.pane.clear_history(reset_kind=True)
        first_sweep = waterfall_line_from_sweep(progress())
        self.assertTrue(self.pane.set_sweep_line(first_sweep))
        self._assert_original_item_pixels()
        sweep_ring = self.pane._renderer.buffer
        old_sweep_plane = self.pane._raster_cache.plane
        old_sweep_ref = weakref.ref(old_sweep_plane)
        old_sweep_qimage = QImage(self.pane._image_items[0].qimage)
        old_sweep_pixels = _pixels(old_sweep_qimage)
        del old_sweep_plane

        new_epoch_frame = replace(progress(sequence=1), epoch=8)
        self.assertTrue(self.pane.set_sweep_line(
            waterfall_line_from_sweep(new_epoch_frame), segment_boundary=True,
        ))
        self.assertIs(self.pane._renderer.buffer, sweep_ring)
        self.assertEqual(self.pane.epoch, 4)
        self.assertIsNotNone(old_sweep_ref())
        self.assertEqual(_pixels(old_sweep_qimage), old_sweep_pixels)
        self._assert_original_item_pixels()
        self.assertIsNot(self.pane._raster_cache.plane, old_sweep_ref())

        del old_sweep_qimage
        gc.collect()
        self.assertIsNone(old_sweep_ref())

    def test_cached_live_resize_pressure_preserves_history_and_external_pixels(self):
        self.pane.set_history_seconds(1)
        self.assertTrue(self.pane.set_line(_line(-64.0, 1)))
        self._assert_original_item_pixels()

        ring = self.pane._renderer.buffer
        old_rows, columns = ring.rows, ring.columns
        old_plane = self.pane._raster_cache.plane
        self.assertIsNotNone(old_plane)
        image = QImage(self.pane._image_items[0].qimage)
        image_pixels = _pixels(image)
        del old_plane
        old_config = self.pane._config
        observed = self.budget.snapshot().observed_bytes
        cache_metadata = old_rows * 8 + old_rows + 1024
        new_rows = old_rows * 2
        new_ring_payload = new_rows * (columns * 4 + 8)

        # Retirement drops the cache metadata, while the caller-held QImage
        # keeps its exact RGBA plane. Leave even the ORIGINAL generic ring
        # reservation one byte short: refusing only the larger protected ring
        # permits its valid generic fallback and must not reject that resize.
        self.budget.limit_bytes = observed - cache_metadata + new_ring_payload - 1
        self.pane.set_history_seconds(2)
        self.assertEqual(self.pane._config, old_config)
        self.assertIs(self.pane._renderer.buffer, ring)
        self.assertEqual(self.pane.history_rows, 1)
        self.assertIsNone(self.pane._raster_cache.plane)
        self.assertEqual(_pixels(image), image_pixels)

        del image
        gc.collect()
        self.budget.limit_bytes = 16 * 1024 * 1024
        self.pane.set_history_seconds(2)
        self.assertEqual(self.pane._config.history_seconds, 2)
        self.assertEqual(self.pane.history_rows, 1)
        self._assert_original_item_pixels()
        self.assertIsNotNone(self.pane._raster_cache.plane)


if __name__ == "__main__":
    unittest.main()
