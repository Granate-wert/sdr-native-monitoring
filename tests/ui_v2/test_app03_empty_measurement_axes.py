"""Empty plots must not publish invented frequency/amplitude coordinates."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.waterfall import SpectrumWaterfallView
from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.design import ThemeId
from tests.ui_v2.test_app03_waterfall_link import _line, _spectrum


class EmptyMeasurementAxesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        settings = QSettings(str(Path(self.temporary.name) / "ui.ini"), QSettings.Format.IniFormat)
        self.view = SpectrumWaterfallView(settings=settings)
        self.addCleanup(self.view.close)

    def assert_values(self, plot, axis, visible):
        self.assertEqual(plot.getAxis(axis).style["showValues"], visible)

    def test_axes_wait_for_data_and_hide_after_measurement_invalidation(self):
        scene, pane = self.view.spectrum_scene, self.view.waterfall_pane
        self.assert_values(scene.plot_item, "left", False)
        self.assert_values(scene.plot_item, "bottom", False)
        self.assert_values(pane.plot_item, "bottom", False)
        # A genuine spectrum grid is sufficient even before the first history row.
        scene.set_frame(_spectrum("dBFS/bin", -90, -30))
        self.assert_values(scene.plot_item, "left", True)
        self.assert_values(scene.plot_item, "bottom", True)
        self.assert_values(pane.plot_item, "bottom", True)
        pane.set_line(_line(-80, 1_000_000_000, unit="dBFS/bin"))
        pane.clear_history()
        self.assert_values(pane.plot_item, "bottom", True)  # Spectrum still exists.
        scene.clear_measurement()
        self.assert_values(scene.plot_item, "left", False)
        self.assert_values(pane.plot_item, "bottom", False)

    def test_standalone_row_grid_remains_real_until_history_is_cleared(self):
        scene, pane = self.view.spectrum_scene, self.view.waterfall_pane
        pane.set_line(_line(-80, 1_000_000_000))
        self.assert_values(pane.plot_item, "bottom", True)
        self.assert_values(scene.plot_item, "left", False)
        pane.clear_history()
        self.assert_values(pane.plot_item, "bottom", False)

    def test_availability_changes_only_on_transition_and_survives_appearance(self):
        scene, pane = self.view.spectrum_scene, self.view.waterfall_pane
        changes = []
        scene.measurement_available_changed.connect(changes.append)
        for theme in ThemeId:
            self.view.set_theme(theme)
            scene.set_locale(UiLocale.EN)
            self.assert_values(scene.plot_item, "bottom", False)
            self.assert_values(pane.plot_item, "bottom", False)
        frame = _spectrum("dBFS/bin", -90, -30)
        scene.set_frame(frame)
        scene.set_frame(frame)
        self.assertEqual(changes, [True])
        scene.clear_measurement()
        scene.clear_measurement()
        self.assertEqual(changes, [True, False])
