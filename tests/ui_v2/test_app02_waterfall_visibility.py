"""Waterfall Show/Hide remains a local presentation operation."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout

from sdr_monitor.ui.v2.waterfall import SpectrumWaterfallView, WaterfallLineFrame


class WaterfallVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.view = SpectrumWaterfallView(settings=QSettings(
            str(Path(self._temporary.name) / "waterfall.ini"), QSettings.Format.IniFormat,
        ))
        self.view.resize(1000, 700)
        self.view.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.view.close()
        self.view.deleteLater()
        self.app.processEvents()
        self._temporary.cleanup()

    def test_standalone_hide_expands_spectrum_restores_axis_and_keeps_history(self) -> None:
        self._append_row()
        before = self.view.splitter.sizes()[0]
        toggle = self.view.waterfall_pane._visible_toggle
        toggle.click()
        self.app.processEvents()
        self.assertTrue(self.view.waterfall_pane.isHidden())
        self.assertIs(toggle.parentWidget(), self.view.waterfall_pane._toolbar)
        self.assertIs(self.view.waterfall_pane._toolbar.parentWidget(), self.view)
        self.assertTrue(self.view.spectrum_scene.plot_item.getAxis("bottom").isVisible())
        self.assertGreater(self.view.splitter.sizes()[0], before)
        self.assertEqual(self.view.waterfall_pane.history_rows, 1)

        toggle.click()
        self.app.processEvents()
        self.assertFalse(self.view.waterfall_pane.isHidden())
        self.assertIs(toggle.parentWidget(), self.view.waterfall_pane._toolbar)
        self.assertFalse(self.view.spectrum_scene.plot_item.getAxis("bottom").isVisible())
        self.assertEqual(self.view.waterfall_pane.history_rows, 1)

    def test_detached_analyzer_control_can_hide_and_show_without_becoming_a_standalone_toolbar(self) -> None:
        controls = self.view.waterfall_pane.take_display_controls()
        toggle = self.view.waterfall_pane._visible_toggle
        self.assertIsNone(controls.parentWidget())
        self._append_row()
        toggle.click()
        self.assertTrue(self.view.waterfall_pane.isHidden())
        self.assertIsNone(controls.parentWidget())
        toggle.click()
        self.assertFalse(self.view.waterfall_pane.isHidden())
        self.assertEqual(self.view.waterfall_pane.history_rows, 1)

    def test_persisted_hidden_layout_keeps_show_control_and_never_reclaims_external_toolbar(self) -> None:
        settings = QSettings(
            str(Path(self._temporary.name) / "persisted-hidden.ini"), QSettings.Format.IniFormat,
        )
        settings.setValue("ui_v2/live/waterfall/v1/version", "1")
        settings.setValue("ui_v2/live/waterfall/v1/visible", False)
        settings.sync()
        view = SpectrumWaterfallView(settings=settings)
        view.resize(1000, 700)
        view.show()
        self.app.processEvents()
        try:
            pane = view.waterfall_pane
            self.assertFalse(pane.render_visible)
            self.assertTrue(pane.isHidden())
            self.assertIs(pane._toolbar.parentWidget(), view)
            self.assertTrue(view.spectrum_scene.plot_item.getAxis("bottom").isVisible())

            analyzer_host = QFrame()
            analyzer_layout = QVBoxLayout(analyzer_host)
            controls = pane.take_display_controls()
            analyzer_layout.addWidget(controls)
            self.assertIs(controls.parentWidget(), analyzer_host)
            pane._visible_toggle.click()
            self.assertFalse(pane.isHidden())
            self.assertIs(controls.parentWidget(), analyzer_host)
        finally:
            view.close()
            view.deleteLater()
            self.app.processEvents()

    def _append_row(self) -> None:
        self.view.waterfall_pane.set_line(WaterfallLineFrame(
            values=np.array((-90.0, -80.0), dtype=np.float32),
            frequency_edges_hz=np.array((99.5, 100.5, 101.5)),
            timestamp_ns=1_000_000_000, configuration_generation=1, unit_label="dBm",
        ))


if __name__ == "__main__":
    unittest.main()
