"""Frame delivery must not reparse unchanged rich-text axis chrome."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.i18n import UiLocale, text
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
from tests.ui_v2 import test_waterfall as fixture
from tests.ui_v2.test_app04_progressive_waterfall import progress
from tests.ui_v2.test_spectrum_scene import _frame


class AxisChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_spectrum_same_unit_does_not_rebuild_axis_but_new_unit_and_clear_do(self):
        scene = SpectrumScene()
        try:
            frame = _frame()
            scene.set_frame(frame)
            with patch.object(scene.plot_item, "setLabel", wraps=scene.plot_item.setLabel) as setter:
                for _ in range(10):
                    scene.set_frame(replace(frame, values=frame.values.copy()))
                setter.assert_not_called()
                scene.set_frame(replace(frame, unit="dBFS/bin"))
                self.assertEqual(scene.plot_item.getAxis("left").labelText, "dBFS/bin")
                self.assertGreater(setter.call_count, 0)
                scene.clear_measurement()
                self.assertEqual(scene.plot_item.getAxis("left").labelText, "")
                scene.set_frame(frame)
                self.assertEqual(scene.plot_item.getAxis("left").labelText, "dBm")
        finally:
            scene.close()
            scene.deleteLater()
            self.app.processEvents()


class WaterfallChromeTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WaterfallPaneTests.setUpClass.__func__)
    setUp = fixture.WaterfallPaneTests.setUp
    tearDown = fixture.WaterfallPaneTests.tearDown
    _pane = fixture.WaterfallPaneTests._pane
    _settings = fixture.WaterfallPaneTests._settings

    def test_same_mode_does_not_rebuild_caption_but_time_kind_mode_and_locale_do(self):
        pane = self._pane()
        pane.set_line(fixture._line(-80, timestamp_ns=1_000_000_000))
        with patch.object(pane.plot_item, "setLabel", wraps=pane.plot_item.setLabel) as setter:
            for index in range(10):
                pane.set_line(fixture._line(-70, timestamp_ns=(index + 2) * 1_000_000_000))
            setter.assert_not_called()
            pane.set_line(replace(fixture._line(-70, timestamp_ns=0), timestamp_known=False, sequence=50))
            self.assertEqual(pane.plot_item.getAxis("left").labelText, text("waterfall.time_axis.unknown"))
            pane.set_sweep_line(waterfall_line_from_sweep(progress()))
            self.assertEqual(pane.plot_item.getAxis("left").labelText, text("waterfall.axis.sweep"))
            setter.reset_mock()
            pane.set_sweep_line(waterfall_line_from_sweep(progress(1, 2)))
            setter.assert_not_called()
            pane.set_locale(UiLocale.EN)
            self.assertEqual(pane.plot_item.getAxis("left").labelText, text("waterfall.axis.sweep", UiLocale.EN))
            self.assertEqual(pane._graphics.accessibleDescription(), text("waterfall.sweep.help", UiLocale.EN))


if __name__ == "__main__":
    unittest.main()
