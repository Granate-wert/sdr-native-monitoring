"""The real WaterfallPane consumes the native-backed ring without new mapping."""

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pyqtgraph as pg

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.waterfall.contracts import WaterfallLineFrame
from sdr_monitor.ui.v2.waterfall.contracts import SweepWaterfallLine
from sdr_monitor.ui.v2.waterfall.pane import WaterfallPane, waterfall_lookup_table
from sdr_monitor.ui.v2.waterfall.sweep_rows import SweepRowStamp, SweepRowState
from tests.test_app07_protected_waterfall_accounting import (
    explicit_native_support,
    restore_explicit_native_support,
)


def _line(values, timestamp, generation=1):
    values = np.asarray(values, dtype=np.float32)
    return WaterfallLineFrame(
        values=values,
        frequency_edges_hz=np.linspace(100.0, 100.0 + values.size, values.size + 1, dtype=np.float64),
        timestamp_ns=timestamp,
        configuration_generation=generation,
        unit_label="dBm",
    )


class ProtectedWaterfallPaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.native, cls.support = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls):
        restore_explicit_native_support(cls)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.pane = WaterfallPane(settings=QSettings(
            str(Path(self.directory.name) / "waterfall.ini"), QSettings.Format.IniFormat,
        ))
        self.pane._renderer.allocation_budget = PresentationAllocationBudget(2 * 1024 * 1024)

    def tearDown(self):
        self.pane.close()
        self.pane.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    @staticmethod
    def _render_bytes(item):
        item.render()
        rendered = item.qimage
        if not isinstance(rendered, QImage):
            raise AssertionError(f"ImageItem.render returned {type(rendered)!r}, not QImage")
        return (rendered.width(), rendered.height(), rendered.format(),
                bytes(rendered.constBits())[:rendered.sizeInBytes()])

    def test_original_image_mapping_nan_alpha_and_lifecycle_keep_protected_ring(self):
        self.pane.set_history_seconds(1)
        self.assertTrue(self.pane.set_line(_line([-90, -80, -70, -60], 1_000_000_000)))
        ring = self.pane._renderer.buffer
        self.assertIs(type(ring._protected_storage), self.native._WaterfallProtectedStorage)
        self.assertEqual(self.pane.history_rows, 1)

        self.assertTrue(self.pane.set_line(_line([np.nan, -75, -65, -55], 1_040_000_000)))
        self.assertEqual(self.pane.history_rows, 2)
        chronological = np.concatenate(self.pane._renderer.tiles(), axis=0)
        self.assertTrue(np.isnan(chronological[1, 0]))
        image = self.pane._image_items[0].image
        self.assertIsNotNone(image)
        generic = pg.ImageItem(axisOrder="row-major")
        generic.setLookupTable(waterfall_lookup_table(self.pane.config.palette))
        generic.setImage(image, autoLevels=False,
                         levels=(self.pane.config.level_min, self.pane.config.level_max))
        actual_pixels = self._render_bytes(self.pane._image_items[0])
        generic_pixels = self._render_bytes(generic)
        self.assertEqual(actual_pixels[:3], generic_pixels[:3])
        self.assertEqual(actual_pixels[3], generic_pixels[3])
        width, height, image_format, raw = actual_pixels
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)
        self.assertEqual(image_format, generic_pixels[2])
        # Use QImage's pixel API to avoid assuming byte order for alpha.
        self.pane._image_items[0].render()
        rendered = self.pane._image_items[0].qimage
        self.assertEqual(rendered.pixelColor(0, 1).alpha(), 0)

        # A same-grid presentation gap, a new grid epoch, a resize and clear
        # remain ordinary pane operations on the same protected-ring adapter.
        self.assertTrue(self.pane.set_line(
            _line([-70, -60, -50, -40], 1_080_000_000, generation=2), segment_boundary=True,
        ))
        self.assertTrue(self.pane._renderer.buffer._protected_storage is not None)
        self.pane.set_history_seconds(2)
        self.assertTrue(self.pane._renderer.buffer._protected_storage is not None)
        self.pane.set_render_visible(False)
        before_uploads = self.pane.metrics.image_uploads
        self.assertTrue(self.pane.set_line(_line([-65, -55, -45, -35], 1_120_000_000, generation=2)))
        self.assertEqual(self.pane.metrics.image_uploads, before_uploads)
        self.assertEqual(self.pane.history_rows, 5)
        self.pane.clear_history()
        self.assertEqual(self.pane.history_rows, 0)
        self.assertIsNotNone(self.pane._renderer.buffer)
        self.assertTrue(self.pane._renderer.buffer._protected_storage is not None)

        before_epoch = self.pane.epoch
        self.assertTrue(self.pane.set_line(
            _line([-60, -50, -40, -30], 1_160_000_000, generation=3),
        ))
        self.assertEqual(self.pane.epoch, before_epoch + 1)
        self.assertEqual(self.pane.history_rows, 1)
        self.assertTrue(self.pane._renderer.buffer._protected_storage is not None)
        self.pane.set_render_visible(True)
        sweep_values = np.array([-55, -45, -35, -25], dtype=np.float32)
        terminal_values = np.array([-95, -85, np.nan, -65], dtype=np.float32)
        sweep_edges = np.linspace(100.0, 104.0, 5, dtype=np.float64)
        sweep_values.setflags(write=False)
        terminal_values.setflags(write=False)
        sweep_edges.setflags(write=False)
        partial_stamp = SweepRowStamp(7, 1, SweepRowState.PARTIAL, acquisition_epoch=4)
        partial = SweepWaterfallLine(
            WaterfallLineFrame(sweep_values, sweep_edges, 0, 4, "dBm", False, 7), partial_stamp,
        )
        complete_stamp = SweepRowStamp(7, 2, SweepRowState.COMPLETE, acquisition_epoch=4)
        complete = SweepWaterfallLine(
            WaterfallLineFrame(terminal_values, sweep_edges, 0, 4, "dBm", False, 7), complete_stamp,
        )
        self.assertTrue(self.pane.set_sweep_line(partial))
        partial_pixels = self._render_bytes(self.pane._image_items[0])
        self.assertTrue(self.pane.set_sweep_line(complete))
        self.assertEqual(self.pane.metrics.sweep_rows_updated, 1)
        self.assertEqual(self.pane.history_rows, 1)
        self.assertTrue(self.pane._renderer.buffer._protected_storage is not None)
        complete_pixels = self._render_bytes(self.pane._image_items[0])
        self.assertNotEqual(partial_pixels[3], complete_pixels[3])
        generic.setImage(terminal_values[np.newaxis, :], autoLevels=False,
                         levels=(self.pane.config.level_min, self.pane.config.level_max))
        self.assertEqual(complete_pixels, self._render_bytes(generic))
        self.assertEqual(self.pane._image_items[0].qimage.pixelColor(2, 0).alpha(), 0)


if __name__ == "__main__":
    unittest.main()
