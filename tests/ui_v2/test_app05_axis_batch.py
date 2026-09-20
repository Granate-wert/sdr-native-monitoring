"""Batching tick commands must preserve the original raster pixels exactly."""
from types import SimpleNamespace
import unittest

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPicture
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.axis import BatchedAxisItem, FrequencyAxis
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene


def record(method, *, varied=False, text=True):
    axis = SimpleNamespace(style={"tickFont": QFont("Arial", 9)},
        textPen=lambda: QPen(QColor(210, 220, 230)), boundingRect=lambda: QRectF(0, 0, 300, 200))
    pens = [pg.mkPen((150, 160, 170, 40), width=1),
            pg.mkPen((100, 150, 200, 80), width=1.4, style=Qt.PenStyle.DashLine)]
    ticks = [(pens[(n // 7) % 2] if varied else pens[0],
              QPointF(10.25, n * 5.25), QPointF(295.25, n * 5.25)) for n in range(35)]
    if varied:
        # Deliberately overlapping translucent segments expose reorder/union
        # bugs; drawLines must draw every segment independently and in order.
        ticks.extend((pens[0], QPointF(30, 50), QPointF(240, 50)) for _ in range(3))
        ticks.append((pens[1], QPointF(125.5, -10), QPointF(125.5, 210)))
    labels = [(QRectF(15, 20, 150, 30), Qt.AlignmentFlag.AlignLeft, "−80 dBFS / 2.4 GHz")] if text else []
    picture = QPicture()
    painter = QPainter(picture)
    method(axis, painter, (pens[0], QPointF(10.25, 0), QPointF(10.25, 200)), ticks, labels)
    painter.end()
    return picture


def pixels(picture, scale):
    image = QImage(640, 460, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(21, 25, 30))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(.25, .5)
    painter.scale(scale, scale)
    picture.play(painter)
    painter.end()
    return bytes(image.constBits())


class AxisBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_translucent_grid_dashes_text_and_fractional_scales_are_pixel_exact(self):
        for varied in (False, True):
            for labels in (False, True):
                original = record(pg.AxisItem.drawPicture, varied=varied, text=labels)
                batched = record(BatchedAxisItem.drawPicture, varied=varied, text=labels)
                for scale in (1., 1.25, 1.5, 2.):
                    with self.subTest(varied=varied, labels=labels, scale=scale):
                        self.assertEqual(pixels(original, scale), pixels(batched, scale))

    def test_same_grid_uses_smaller_existing_picture_not_an_extra_cache(self):
        original = record(pg.AxisItem.drawPicture)
        batched = record(BatchedAxisItem.drawPicture)
        self.assertLess(batched.size(), original.size())

    def test_actual_v2_scene_uses_batched_axes(self):
        scene = SpectrumScene()
        try:
            self.assertIsInstance(scene.plot_item.getAxis("left"), BatchedAxisItem)
            self.assertIsInstance(scene.plot_item.getAxis("bottom"), FrequencyAxis)
            self.assertIsInstance(scene.plot_item.getAxis("bottom"), BatchedAxisItem)
        finally:
            scene.close()
            scene.deleteLater()


if __name__ == "__main__":
    unittest.main()
