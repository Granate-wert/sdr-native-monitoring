"""Compact analyzer control extraction without canvas-state loss."""

from __future__ import annotations

import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.waterfall.contracts import WaterfallLineFrame
from sdr_monitor.ui.v2.waterfall.pane import WaterfallPane
from sdr_monitor.ui.v2.workspaces.analyzer_display_controls import AnalyzerDisplayControls


class CompactDisplayControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_detached_controls_keep_frame_history_axes_and_connections(self) -> None:
        settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                             "OpenAI", "app02-compact-controls-test")
        settings.clear()
        scene = SpectrumScene()
        frequencies = np.array((100.0, 101.0), dtype=np.float64)
        values = np.array((-80.0, -70.0), dtype=np.float32)
        frequencies.setflags(write=False)
        values.setflags(write=False)
        frame = type("Frame", (), {"frequencies_hz": frequencies, "values": values,
                                    "unit": "dBFS/Hz"})()
        scene.set_frame(frame)
        pane = WaterfallPane(settings=settings)
        pane.set_line(WaterfallLineFrame(
            values=values, frequency_edges_hz=np.array((99.5, 100.5, 101.5)),
            timestamp_ns=1_000_000_000, configuration_generation=1,
            unit_label="dBFS/Hz",
        ))
        scene_controls = scene.take_display_controls()
        waterfall_controls = pane.take_display_controls()
        self.assertIs(scene.latest_frame, frame)
        self.assertEqual(pane.history_rows, 1)
        overlay = AnalyzerDisplayControls(scene_controls, waterfall_controls)
        spy = QSignalSpy(overlay.close_requested)
        overlay.close_button.click()
        self.assertEqual(spy.count(), 1)
        pane._clear_button.click()
        self.assertEqual(pane.history_rows, 0)

    def test_waterfall_locale_changes_in_place_without_history_loss(self) -> None:
        pane = WaterfallPane()
        values = np.array((-80.0, -70.0), dtype=np.float32)
        pane.set_line(WaterfallLineFrame(
            values=values, frequency_edges_hz=np.array((99.5, 100.5, 101.5)),
            timestamp_ns=1_000_000_000, configuration_generation=1,
            unit_label="dBFS/Hz",
        ))
        signature = pane.grid_signature
        pane.set_locale(UiLocale.EN)
        self.assertEqual(pane.history_rows, 1)
        self.assertEqual(pane.grid_signature, signature)
        self.assertEqual(pane._frequency_axis.tickStrings([1_000_000.0], 1.0, 1.0), ["1.000 MHz"])


if __name__ == "__main__":
    unittest.main()
