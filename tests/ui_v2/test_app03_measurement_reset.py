"""Measurement reset and stopped-frame localization; no hardware access."""

import unittest

from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.i18n import UiLocale, text
from sdr_monitor.ui.v2.spectrum import TraceKind
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.ui_v2.test_spectrum_scene import _frame


class MeasurementResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.scene = SpectrumScene(locale=UiLocale.EN)
        self.addCleanup(self.scene.close)

    def test_reset_invalidates_data_and_all_markers_without_recreating_canvas(self):
        scene = self.scene
        frame = _frame()
        scene.set_frame(frame)
        scene.place_marker("M1", 100.2e6)
        scene.place_marker("M2", 100.8e6)
        scene.set_trace(TraceKind.MAXIMUM, frame)
        canvas = scene.view_box
        scene.clear_measurement()
        scene.clear_measurement()  # Idempotent, including before a new frame.
        self.assertIs(scene.view_box, canvas)
        self.assertIsNone(scene.latest_frame)
        self.assertEqual(scene.markers, ())
        self.assertTrue(all(scene.trace_envelope(kind) is None for kind in TraceKind))
        self.assertIsNone(scene.place_selected_marker_at_view_center())
        self.assertIsNone(scene.move_selected_marker_to_peak())
        self.assertEqual(scene._cursor_readout.text(), text("spectrum.cursor.empty", UiLocale.EN))
        scene.set_frame(frame)
        self.assertEqual(scene.markers, ())
        self.assertIsNotNone(scene.place_marker("M1", 100.2e6))

    def test_geometry_change_invalidates_markers_but_same_grid_refresh_keeps_them(self):
        scene = self.scene
        scene.set_frame(_frame())
        scene.place_marker("M1", 100.2e6)
        scene.set_frame(_frame())
        self.assertEqual(len(scene.markers), 1)
        scene.set_frame(_frame(start_hz=200e6, stop_hz=201e6))
        self.assertEqual(scene.markers, ())

    def test_locale_refreshes_stopped_marker_without_new_data_or_marker_event(self):
        scene = self.scene
        frame = _frame()
        scene.set_frame(frame)
        marker = scene.place_marker("M1", 100.2e6)
        events = []
        scene.marker_changed.connect(events.append)
        scene.set_locale(UiLocale.RU)
        self.assertIn("МГц", scene._marker_labels["M1"].toPlainText())
        scene.set_locale(UiLocale.EN)
        self.assertIn("MHz", scene._marker_labels["M1"].toPlainText())
        self.assertIs(scene.latest_frame, frame)
        self.assertEqual(scene.markers, (marker,))
        self.assertEqual(events, [])
