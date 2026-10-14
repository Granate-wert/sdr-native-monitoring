"""Positive native-backed incremental RGBA mapping and lifetime contracts."""

import gc
import os
import threading
import unittest
import weakref
from unittest.mock import patch

import numpy as np
import pyqtgraph as pg

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.waterfall.bounded_ring import BoundedWaterfallRenderer
from sdr_monitor.ui.v2.waterfall.incremental_raster import WaterfallImageItem, WaterfallRasterCache
from tests.test_app07_protected_waterfall_accounting import (
    explicit_native_support,
    restore_explicit_native_support,
)


def _lut():
    table = np.empty((256, 4), dtype=np.uint8)
    values = np.arange(256, dtype=np.uint8)
    table[:, 0] = values
    table[:, 1] = values[::-1]
    table[:, 2] = np.bitwise_xor(values, np.uint8(0x5A))
    table[:, 3] = 255
    return table


def _qimage_bytes(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return (rgba.width(), rgba.height(), rgba.format(),
            bytes(rgba.constBits())[:rgba.sizeInBytes()])


class IncrementalWaterfallRasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.native, cls.support = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls):
        restore_explicit_native_support(cls)

    def setUp(self):
        self.budget = PresentationAllocationBudget(2 * 1024 * 1024)
        self.assertTrue(self.budget.install_waterfall_storage_support(self.support))
        self.renderer = BoundedWaterfallRenderer()
        self.renderer.allocation_budget = self.budget
        self.cache = WaterfallRasterCache()
        self.items = []

    def tearDown(self):
        for item in self.items:
            item.clear()
        self.cache.retire()

    def _upload(self, lut, levels=(-1.0, 1.0)):
        ring = self.renderer.buffer
        tiles = ring.chronological_tiles()
        spans = ring.chronological_spans()
        self.assertEqual(len(tiles), len(spans))
        if len(self.items) < len(tiles):
            self.items.extend(WaterfallImageItem() for _ in range(len(tiles) - len(self.items)))
        for index, (tile, (start, count)) in enumerate(zip(tiles, spans)):
            item = self.items[index]
            item.setLookupTable(lut)
            item.setImage(tile, autoLevels=False, levels=levels)
            self.assertTrue(item.bind_raster(self.cache, ring, tile, start, count, levels, lut))
        return ring, tiles, spans

    def _assert_original_pixels(self, item):
        reference = pg.ImageItem(axisOrder="row-major")
        reference.setLookupTable(item.lut)
        reference.setImage(item.image, autoLevels=False, levels=item.levels)
        reference.render()
        self.assertIsNotNone(reference.qimage)
        self.assertEqual(_qimage_bytes(item.qimage), _qimage_bytes(reference.qimage))

    def test_wrap_dirty_runs_and_policy_edits_match_original_mapper_pixels(self):
        rows = [
            np.array([0.0, np.nan, 1.0, -1.0, 0.0], np.float32),
            np.array([np.inf, -np.inf, 0.25, -0.25, 0.5], np.float32),
            np.array([-1.0, -0.5, 0.0, 0.5, 1.0], np.float32),
            np.array([0.75, 0.5, 0.25, 0.0, -0.25], np.float32),
            np.array([0.1, 0.2, 0.3, 0.4, 0.5], np.float32),
        ]
        for index, row in enumerate(rows):
            self.renderer.append(row, rows=4, timestamp_ns=index + 1)
        ring, tiles, spans = self._upload(_lut())
        self.assertEqual(spans, ((1, 3), (0, 1)))
        for item in self.items[:len(tiles)]:
            item.render()
            self._assert_original_pixels(item)
        self.assertEqual(self.cache.converted_rows, 4)
        self.assertEqual(self.cache.converted_pixels, 20)
        self.assertEqual(self.cache.converted_runs, 2)
        self.assertGreaterEqual(self.cache.fast_render_prepares, 2)
        self.assertTrue(all(item.raster_fast_renders > 0 for item in self.items[:len(tiles)]))

        converted_before = self.cache.converted_rows
        reused_before = self.cache.reused_rows
        for item in self.items[:len(tiles)]:
            item.updateImage()
            item.render()
            self._assert_original_pixels(item)
        self.assertEqual(self.cache.converted_rows, converted_before)
        self.assertGreaterEqual(self.cache.reused_rows - reused_before, ring.count)

        self.renderer.append(np.array([0.6, np.nan, -0.7, np.inf, -np.inf], np.float32),
                             rows=4, timestamp_ns=6)
        ring, tiles, spans = self._upload(_lut())
        for item in self.items[:len(tiles)]:
            item.render()
            self._assert_original_pixels(item)
        self.assertEqual(self.cache.converted_rows, converted_before + 1)
        self.assertEqual(spans, ((2, 2), (0, 2)))

        edited_lut = _lut()
        ring_data_before = self.cache.converted_rows
        ring, tiles, spans = self._upload(edited_lut, levels=(-2.0, 2.0))
        edited_lut[17, 0] ^= np.uint8(0xFF)
        for item in self.items[:len(tiles)]:
            item.update_raster_policy((-2.0, 2.0), edited_lut)
            item.render()
            self._assert_original_pixels(item)
        self.assertGreaterEqual(self.cache.converted_rows - ring_data_before, ring.count)

        # A directly changed per-item option refuses without recolouring the
        # plane used by its peer; the installed ImageItem mapper remains the fallback.
        stable = self.cache.plane.copy()
        item = self.items[0]
        item.setAutoDownsample(True)
        item.updateImage()
        item.render()
        self.assertGreater(item.raster_fallback_renders, 0)
        np.testing.assert_array_equal(self.cache.plane, stable)

    def test_qimage_live_view_copy_isolation_and_full_plane_budget_lifetime(self):
        self.renderer.append(np.array([0.0, np.nan, np.inf, -np.inf], np.float32),
                             rows=3, timestamp_ns=1)
        ring, _, _ = self._upload(_lut())
        item = self.items[0]
        item.render()
        self._assert_original_pixels(item)
        expected_cache_bytes = ring.rows * ring.columns * 4 + ring.rows * 8 + ring.rows + 1024
        baseline_observed = self.budget.snapshot().observed_bytes - expected_cache_bytes
        observed_with_cache = self.budget.snapshot().observed_bytes
        self.assertEqual(observed_with_cache - baseline_observed, expected_cache_bytes)

        independent = item.qimage.copy()
        before = self.cache.plane[0, 0].copy()
        independent.setPixelColor(0, 0, QColor(1, 2, 3, 4))
        np.testing.assert_array_equal(self.cache.plane[0, 0], before)
        self.assertNotEqual(independent.pixelColor(0, 0), item.qimage.pixelColor(0, 0))

        # Installed PySide6 exposes the live backing to non-const QImage APIs.
        # This mutation is deliberately documented as unsupported consumer use.
        item.qimage.setPixelColor(0, 0, QColor(9, 8, 7, 6))
        self.assertFalse(np.array_equal(self.cache.plane[0, 0], before))

        external = QImage(item.qimage)
        plane = self.cache.plane
        plane_size = plane.nbytes
        plane_ref = weakref.ref(plane)
        seen_ref = weakref.ref(self.cache._seen)
        dirty_ref = weakref.ref(self.cache._dirty)
        lut_ref = weakref.ref(self.cache._lut_snapshot)
        del plane
        item.clear()
        self.cache.retire()
        gc.collect()
        self.assertIsNotNone(plane_ref())
        self.assertIsNone(seen_ref())
        self.assertIsNone(dirty_ref())
        self.assertIsNone(lut_ref())
        self.assertEqual(self.budget.snapshot().observed_bytes - baseline_observed, plane_size)
        del external
        gc.collect()
        self.assertIsNone(plane_ref())
        self.assertEqual(self.budget.snapshot().observed_bytes, baseline_observed)

    def test_exact_active_tile_span_and_foreign_source_refuse(self):
        self.renderer.append(np.array([0.0, 0.5, np.nan], np.float32), rows=3, timestamp_ns=1)
        ring = self.renderer.buffer
        tile = ring.chronological_tiles()[0]
        start, count = ring.chronological_spans()[0]
        self.assertTrue(ring.owns_chronological_tile(tile, start, count))

        item = WaterfallImageItem()
        item.setLookupTable(_lut())
        foreign = tile.copy()
        item.setImage(foreign, autoLevels=False, levels=(-1.0, 1.0))
        self.assertFalse(item.bind_raster(self.cache, ring, foreign, start, count,
                                          (-1.0, 1.0), item.lut))
        item.render()
        self.assertEqual(item.raster_fast_renders, 0)

        item.setImage(tile, autoLevels=False, levels=(-1.0, 1.0))
        self.assertFalse(item.bind_raster(self.cache, ring, tile, start + 1, count,
                                          (-1.0, 1.0), item.lut))
        item.render()
        self.assertEqual(item.raster_fast_renders, 0)

    def test_only_active_rows_map_and_separated_revision_changes_make_two_runs(self):
        self.assertIsNone(self.cache.plane)
        self.assertEqual(self.cache.converted_rows, 0)
        self.renderer.append(np.full(5, 0.0, dtype=np.float32), rows=4, timestamp_ns=0)
        ring, tiles, spans = self._upload(_lut())
        self.assertEqual(spans, ((0, 1),))
        self.assertEqual(sum(count for _start, count in spans), ring.count)
        for item in self.items[:len(tiles)]:
            item.render()
        self.assertEqual(self.cache.converted_rows, 1)
        self.assertEqual(np.count_nonzero(self.cache._seen != np.iinfo(np.uint64).max), 1)

        for index in range(1, 4):
            self.renderer.append(np.full(5, index / 4, dtype=np.float32), rows=4,
                                 timestamp_ns=index + 1)
        ring, tiles, spans = self._upload(_lut())
        self.assertEqual(sum(count for _start, count in spans), ring.count)
        for item in self.items[:len(tiles)]:
            item.render()
        self.assertEqual(self.cache.converted_rows, ring.count)
        self.assertEqual(np.count_nonzero(self.cache._seen != np.iinfo(np.uint64).max), ring.count)

        before_rows = self.cache.converted_rows
        before_runs = self.cache.converted_runs
        ring._protected_storage.write_row(0, np.full(5, 0.75, dtype=np.float32))
        ring._protected_storage.write_row(2, np.full(5, -0.75, dtype=np.float32))
        ring, tiles, spans = self._upload(_lut())
        for item in self.items[:len(tiles)]:
            item.render()
        self.assertEqual(self.cache.converted_rows - before_rows, 2)
        self.assertEqual(self.cache.converted_runs - before_runs, 2)

    def test_oom_releases_reservation_and_partial_cache_outputs(self):
        self.renderer.append(np.array([0.0, np.nan, 0.5], np.float32), rows=3, timestamp_ns=1)
        ring, tiles, spans = self._upload(_lut())
        item = self.items[0]
        observed_before = self.budget.snapshot().observed_bytes
        with patch("sdr_monitor.ui.v2.waterfall.incremental_raster.np.empty",
                   side_effect=MemoryError):
            self.assertIsNone(self.cache.render_for(item))
        snapshot = self.budget.snapshot()
        self.assertEqual(snapshot.reserved_bytes, 0)
        self.assertEqual(snapshot.observed_bytes, observed_before)
        self.assertIsNone(self.cache.plane)
        self.assertIsNone(self.cache._seen)
        self.assertIsNone(self.cache._dirty)
        self.assertIsNone(self.cache._lut_snapshot)
        item.render()
        self.assertGreater(item.raster_fallback_renders, 0)

    def test_budget_refusal_is_stable_and_foreign_creator_cannot_render(self):
        self.renderer.append(np.array([0.0, np.nan, 0.5], np.float32), rows=3, timestamp_ns=1)
        ring, _, _ = self._upload(_lut())
        item = self.items[0]
        source_bytes = self.budget.snapshot().observed_bytes
        payload = ring.rows * ring.columns * 4 + ring.rows * 8 + ring.rows + 1024
        self.budget.limit_bytes = source_bytes + payload - 1
        item.render()
        first_rejections = self.budget.snapshot().rejections
        self.assertEqual(self.budget.snapshot().reserved_bytes, 0)
        self.assertIsNone(self.cache.plane)
        item.render()
        self.assertEqual(self.budget.snapshot().rejections, first_rejections)
        self.assertGreater(item.raster_fallback_renders, 0)

        self.budget.limit_bytes = 2 * 1024 * 1024
        item.render()
        self.assertIsNotNone(self.cache.plane)
        plane_before = self.cache.plane.copy()
        seen_before = self.cache._seen.copy()
        revision_before = self.cache._last_revision
        counters_before = (self.cache.converted_rows, self.cache.converted_runs,
                           self.cache.fast_render_prepares)
        failures = []

        def foreign_render():
            try:
                self.cache.render_for(item)
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=foreign_render)
        thread.start()
        thread.join()
        self.assertTrue(failures)
        np.testing.assert_array_equal(self.cache.plane, plane_before)
        np.testing.assert_array_equal(self.cache._seen, seen_before)
        self.assertEqual(self.cache._last_revision, revision_before)
        self.assertEqual((self.cache.converted_rows, self.cache.converted_runs,
                          self.cache.fast_render_prepares), counters_before)
        self.assertEqual(self.budget.snapshot().reserved_bytes, 0)

    def test_unbound_generic_item_keeps_original_render_fallback(self):
        item = WaterfallImageItem()
        source = np.array([[0.0, np.nan], [1.0, -1.0]], dtype=np.float32)
        item.setLookupTable(_lut())
        item.setImage(source, autoLevels=False, levels=(-1.0, 1.0))
        item.render()
        self.assertIs(item.image.base, source)
        self.assertIsNotNone(item.qimage)
        self.assertEqual(item.raster_fast_renders, 0)
        self.assertEqual(item.raster_fallback_renders, 1)

    def test_overlapping_native_rows_do_not_prove_exact_physical_origin(self):
        for index in range(3):
            self.renderer.append(np.full(4, -0.8 + 0.2 * index, np.float32),
                                 rows=4, timestamp_ns=index + 1)
        ring = self.renderer.buffer
        overlapping = ring._data[1:4]
        self.assertEqual(ring.chronological_spans(), ((0, 3),))
        self.assertTrue(np.shares_memory(overlapping, ring._data[:3]))
        item = WaterfallImageItem()
        self.items.append(item)
        lut = _lut()
        item.setLookupTable(lut)
        item.setImage(overlapping, autoLevels=False, levels=(-1.0, 1.0))
        self.assertFalse(item.bind_raster(self.cache, ring, overlapping, 0, 3, (-1.0, 1.0), lut))
        item.render()
        self.assertEqual(item.raster_fast_renders, 0)
        self.assertEqual(item.raster_fallback_renders, 1)
        self._assert_original_pixels(item)


if __name__ == "__main__":
    unittest.main()
