"""Marker readout contrast is independent of the scientific pixels underneath."""
import unittest

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.design import ThemeId, tokens_for_theme
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.ui_v2.test_spectrum_scene import _frame


def luminance(color):
    components = [color.redF(), color.greenF(), color.blueF()]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in components]
    return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))


class MarkerReadabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_all_themes_have_opaque_high_contrast_noninteractive_marker_readouts(self):
        scene = SpectrumScene()
        try:
            scene.resize(1100, 700)
            scene.show()
            frame = _frame(np.linspace(-110, -20, 4096))
            scene.set_frame(frame)
            scene.place_marker('M1', 100_300_000)
            scene.place_marker('M2', 100_700_000)
            original = scene.markers
            values = frame.values.copy()
            for theme in ThemeId:
                scene.set_theme(theme)
                self.app.processEvents()
                tokens = tokens_for_theme(theme)
                for name, label in scene._marker_labels.items():
                    self.assertTrue(label.isVisible())
                    self.assertEqual(label.fill.color().alpha(), 255)
                    self.assertEqual(label.fill.style(), Qt.BrushStyle.SolidPattern)
                    self.assertEqual(label.fill.color(), QColor(tokens.colors.panel))
                    self.assertEqual(label.textItem.defaultTextColor(), QColor(tokens.colors.primary_text))
                    low, high = sorted([luminance(label.fill.color()), luminance(label.textItem.defaultTextColor())])
                    self.assertGreaterEqual((high + .05) / (low + .05), 7)
                    self.assertEqual(label.border.color(), scene._marker_lines[name].pen.color())
                    self.assertGreater(label.zValue(), scene._sweep_position.region.zValue())
                    self.assertEqual(label.acceptedMouseButtons(), Qt.MouseButton.NoButton)
                    self.assertIn(name, label.toPlainText())
                self.assertEqual(scene.markers, original)
                np.testing.assert_array_equal(frame.values, values)
        finally:
            scene.close()
            scene.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
