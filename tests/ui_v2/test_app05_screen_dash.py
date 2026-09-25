"""High-contrast average dashes are bounded, visible, and gap-safe."""

from __future__ import annotations

import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QTransform
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.design.tokens import ThemeId
from sdr_monitor.ui.v2.spectrum import TraceKind
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.spectrum.screen_dash import (
    ScreenDashCurveItem,
    screen_dash_segments,
)


class ScreenDashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_finite_runs_never_bridge_a_missing_sample(self) -> None:
        x = np.arange(7, dtype=np.float64)
        y = np.array([0.0, 1.0, np.nan, 3.0, 4.0, 5.0, np.nan])
        transform = QTransform(10.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        lines = screen_dash_segments(x, y, transform, on_pixels=15.0, off_pixels=5.0)
        self.assertIsNotNone(lines)
        assert lines is not None
        self.assertGreater(len(lines), 0)
        self.assertFalse(np.any((lines[:, 0] < 2.0) & (lines[:, 2] > 2.0)))
        self.assertFalse(np.any((lines[:, 0] < 3.0) & (lines[:, 2] > 3.0)))
        self.assertTrue(np.all(np.isfinite(lines)))
        for first_x, first_y, last_x, last_y in lines:
            self.assertGreaterEqual(last_x, first_x)
            self.assertAlmostEqual(first_y, first_x)
            self.assertAlmostEqual(last_y, last_x)

    def test_screen_anchor_recomputes_on_transform_change(self) -> None:
        x = np.array([0.0, 100.0])
        y = np.array([-80.0, -80.0])
        first = screen_dash_segments(
            x, y, QTransform(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            on_pixels=10.0, off_pixels=10.0,
        )
        shifted = screen_dash_segments(
            x, y, QTransform(1.0, 0.0, 0.0, 1.0, 5.0, 0.0),
            on_pixels=10.0, off_pixels=10.0,
        )
        zoomed = screen_dash_segments(
            x, y, QTransform(2.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            on_pixels=10.0, off_pixels=10.0,
        )
        assert first is not None and shifted is not None and zoomed is not None
        self.assertNotEqual(first.tobytes(), shifted.tobytes())
        self.assertGreater(len(zoomed), len(first))

    def test_unsupported_or_oversized_geometry_falls_back(self) -> None:
        x = np.array([0.0, 10.0])
        y = np.array([1.0, 1.0])
        rotated = QTransform(0.0, 1.0, -1.0, 0.0, 0.0, 0.0)
        self.assertIsNone(screen_dash_segments(x, y, rotated, on_pixels=10.0, off_pixels=5.0))
        large = np.arange(65_537, dtype=np.float64)
        self.assertIsNone(screen_dash_segments(
            large, large, QTransform(), on_pixels=10.0, off_pixels=5.0,
        ))
        empty = screen_dash_segments(
            x, np.full(2, np.nan), QTransform(), on_pixels=10.0, off_pixels=5.0,
        )
        assert empty is not None
        self.assertEqual(empty.shape, (0, 4))

    def test_actual_curve_paint_contains_bright_dashes_and_dark_gaps(self) -> None:
        curve = ScreenDashCurveItem()
        pen = QPen(QColor("white"), 1.4, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        curve.setPen(pen)
        curve.setData(np.array([0.0, 200.0]), np.array([30.0, 30.0]), connect="finite")
        image = QImage(220, 60, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("black"))
        painter = QPainter(image)
        curve.paint(painter, None, None)
        painter.end()
        self.assertGreater(image.pixelColor(4, 30).red(), 200)
        self.assertEqual(image.pixelColor(14, 30).red(), 0)
        self.assertGreater(image.pixelColor(22, 30).red(), 200)
        curve.clear()
        image.fill(QColor("black"))
        painter = QPainter(image)
        curve.paint(painter, None, None)
        painter.end()
        self.assertEqual(image.pixelColor(4, 30).red(), 0)

    def test_transformed_dpr_gap_and_native_fallback_paint(self) -> None:
        curve = ScreenDashCurveItem()
        pen = QPen(QColor("white"), 1.4, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        curve.setPen(pen)
        curve.setData(
            np.array([0.0, 50.0, 70.0, 90.0, 150.0]),
            np.array([30.0, 30.0, np.nan, 30.0, 30.0]),
            connect="finite",
        )
        image = QImage(480, 120, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(1.5)
        image.fill(QColor("black"))
        painter = QPainter(image)
        painter.scale(2.0, 1.0)
        curve.paint(painter, None, None)
        painter.end()
        self.assertGreater(image.pixelColor(4, 45).red(), 200)
        self.assertEqual(image.pixelColor(20, 45).red(), 0)
        self.assertGreater(image.pixelColor(30, 45).red(), 200)
        self.assertTrue(all(image.pixelColor(pixel, 45).red() == 0
                            for pixel in range(155, 265)))
        # Rotation is intentionally unsupported by the fast renderer; Qt's
        # native path remains available instead of silently dropping a trace.
        self.assertIsNone(screen_dash_segments(
            np.array([0.0, 100.0]), np.array([30.0, 30.0]),
            QTransform(0.0, 1.0, -1.0, 0.0, 0.0, 0.0),
            on_pixels=10.0, off_pixels=5.0,
        ))
        curve.setData(np.array([20.0, 100.0]), np.array([20.0, 20.0]), connect="finite")
        rotated_image = QImage(180, 100, QImage.Format.Format_ARGB32_Premultiplied)
        rotated_image.fill(QColor("black"))
        painter = QPainter(rotated_image)
        painter.rotate(10.0)
        curve.paint(painter, None, None)
        painter.end()
        self.assertTrue(any(rotated_image.pixelColor(pixel, row).red() > 200
                            for row in range(10, 60) for pixel in range(10, 120)))

    def test_scene_theme_zoom_and_marker_source_remain_coherent(self) -> None:
        frequencies = np.linspace(2.3e9, 2.6e9, 4600)
        current = np.linspace(-90.0, -70.0, frequencies.size).astype(np.float32)
        average = np.full(frequencies.size, -80.0, dtype=np.float32)
        average[2300:2310] = np.nan
        frame = type("Frame", (), {"frequencies_hz": frequencies,
                                    "values": current, "unit": "dBFS/Hz"})()
        average_frame = type("Frame", (), {"frequencies_hz": frequencies,
                                            "values": average, "unit": "dBFS/Hz"})()
        before = (frequencies.tobytes(), current.tobytes(), average.tobytes())
        scene = SpectrumScene(theme=ThemeId.HIGH_CONTRAST)
        try:
            scene.resize(900, 600)
            scene.show()
            self.app.processEvents()
            scene.set_frame(frame)
            scene.set_trace(TraceKind.AVERAGE, average_frame)
            curve = scene._curves[TraceKind.AVERAGE]
            self.assertIsInstance(curve.curve, ScreenDashCurveItem)
            self.assertEqual(curve.opts["pen"].style(), Qt.PenStyle.DashLine)
            initial_items = len(scene._graphics.scene().items())
            marker = scene.place_marker("M1", 2.45e9)
            self.assertIsNotNone(marker)
            for theme in (ThemeId.DARK, ThemeId.HIGH_CONTRAST, ThemeId.LIGHT,
                          ThemeId.HIGH_CONTRAST):
                scene.set_theme(theme)
                scene._graphics.grab()
            scene.plot_item.setXRange(2.4e9, 2.5e9, padding=0.0)
            scene._graphics.grab()
            self.assertEqual(len(scene._graphics.scene().items()), initial_items)
            self.assertIs(scene.latest_frame, frame)
            self.assertEqual((frequencies.tobytes(), current.tobytes(), average.tobytes()), before)
            self.assertEqual(scene.markers[0].frequency_hz, marker.frequency_hz)
            self.assertEqual(curve.opts["pen"].style(), Qt.PenStyle.DashLine)
        finally:
            scene.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
