"""Real Qt viewport middle-button events, not direct ViewBox method calls."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.waterfall import SpectrumWaterfallView


def middle_move(graphics, point) -> None:
    viewport = graphics.viewport()
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(point),
                        QPointF(viewport.mapToGlobal(point)), Qt.MouseButton.NoButton,
                        Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(viewport, event)
    QApplication.processEvents()


def plot_points(plot, distance=60):
    box = plot.view_box
    graphics = plot._graphics
    first = graphics.mapFromScene(box.mapToScene(QPointF(box.width() / 2, box.height() / 2)))
    last = first + QPoint(distance, 0)
    return graphics, first, last


class RfGestureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.view = SpectrumWaterfallView(settings=QSettings(
            str(Path(self.temporary.name) / "gesture.ini"), QSettings.Format.IniFormat))
        self.view.resize(1000, 700)
        self.view.show()
        self.view.spectrum_scene.set_frame(SimpleNamespace(
            frequencies_hz=np.linspace(100e6, 110e6, 32), values=np.full(32, -80), unit="dBFS/bin"))
        self.app.processEvents()
        QTest.qWait(40)  # Let initial axis/layout timers settle before measuring pixel scale.
        self.anchor = ("source", "rx1", "session", 1, 2, "clock", "dBFS/bin")
        self.view.set_rf_shift_provider(lambda: self.anchor)
        self.proposals = []
        self.view.rf_shift_requested.connect(lambda delta, anchor: self.proposals.append((delta, anchor)))

    def tearDown(self):
        self.view.waterfall_pane.release_presentation_after_shutdown()
        self.view.spectrum_scene.release_graphics_after_shutdown()
        self.view.close()
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temporary.cleanup()

    def drag(self, plot, *, before_release=None, distance=60):
        graphics, first, last = plot_points(plot, distance)
        QTest.mousePress(graphics.viewport(), Qt.MouseButton.MiddleButton, pos=first)
        middle_move(graphics, last)
        self.assertEqual(self.proposals, [])  # No per-pixel receiver proposals.
        if before_release is not None:
            before_release(graphics)
        QTest.mouseRelease(graphics.viewport(), Qt.MouseButton.MiddleButton, pos=last)
        self.app.processEvents()

    def test_spectrum_actual_middle_release_proposes_once_without_viewport_pan(self):
        plot = self.view.spectrum_scene
        before = tuple(plot.view_box.viewRange()[0])
        expected = -60 * (before[1] - before[0]) / plot.view_box.width()
        self.drag(plot)
        self.assertEqual(len(self.proposals), 1)
        self.assertAlmostEqual(self.proposals[0][0], expected, delta=abs(expected) * .025)
        self.assertEqual(self.proposals[0][1], self.anchor)
        self.assertEqual(tuple(plot.view_box.viewRange()[0]), before)

    def test_waterfall_uses_same_gesture_and_frequency_mapping(self):
        before = tuple(self.view.spectrum_scene.view_box.viewRange()[0])
        self.drag(self.view.waterfall_pane)
        self.assertEqual(len(self.proposals), 1)
        self.assertLess(self.proposals[0][0], 0)
        self.assertEqual(tuple(self.view.spectrum_scene.view_box.viewRange()[0]), before)

    def test_epoch_change_during_hold_cancels_without_pan(self):
        before = tuple(self.view.spectrum_scene.view_box.viewRange()[0])
        self.drag(self.view.spectrum_scene,
                  before_release=lambda _: setattr(self, "anchor", ("source", "rx1", "new epoch")))
        self.assertEqual(self.proposals, [])
        self.assertEqual(tuple(self.view.spectrum_scene.view_box.viewRange()[0]), before)

    def test_absent_provider_does_not_fall_back_to_middle_viewport_pan(self):
        self.view.set_rf_shift_provider(None)
        before = tuple(self.view.spectrum_scene.view_box.viewRange()[0])
        self.drag(self.view.spectrum_scene)
        self.assertEqual(self.proposals, [])
        self.assertEqual(tuple(self.view.spectrum_scene.view_box.viewRange()[0]), before)

    def test_escape_cancels_held_gesture(self):
        self.drag(self.view.spectrum_scene,
                  before_release=lambda graphics: QTest.keyClick(graphics.viewport(), Qt.Key.Key_Escape))
        self.assertEqual(self.proposals, [])

    def test_linked_viewport_zoom_during_hold_cancels_rf_proposal(self):
        self.drag(self.view.waterfall_pane,
                  before_release=lambda _: self.view.spectrum_scene.view_box.setXRange(102e6, 108e6, padding=0))
        self.assertEqual(self.proposals, [])

    def test_click_without_three_pixel_drag_is_not_an_rf_request(self):
        self.drag(self.view.spectrum_scene, distance=1)
        self.assertEqual(self.proposals, [])


if __name__ == "__main__":
    unittest.main()
