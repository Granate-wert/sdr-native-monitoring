"""Only unique admitted source revisions count as newly painted frames."""

import gc
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.paint_cadence import UniquePaintCadence, cadence_graphics_widget


def frame(sequence, *, epoch=1, revision=None, state=None):
    return SimpleNamespace(source_id="s", epoch=epoch, sequence=sequence,
                           revision=revision, state=state, unit="dBm")


class PaintCadenceTests(unittest.TestCase):
    def setUp(self):
        self.meter = UniquePaintCadence()

    def paint(self, source, at_ns):
        self.meter.admit(source)
        self.meter.painted(self.meter.key, at_ns)

    def test_repeated_paints_do_not_inflate_new_frame_cadence(self):
        self.paint(frame(1), 1_000_000_000)
        self.paint(frame(1), 1_020_000_000)
        self.assertIsNone(self.meter.period_ms(1_020_000_000))
        self.paint(frame(2), 1_100_000_000)
        self.paint(frame(2), 1_150_000_000)
        self.assertEqual(self.meter.period_ms(1_150_000_000), 100)
        self.assertIsNone(self.meter.period_ms(2_200_000_000))

    def test_partial_revisions_and_terminal_are_distinct_but_epoch_resets(self):
        for at, value in enumerate((frame(1, revision=1), frame(1, revision=2), frame(1, state="complete"))):
            self.paint(value, (at + 1) * 100_000_000)
        self.assertEqual(self.meter.period_ms(300_000_000), 100)
        self.paint(frame(1, epoch=2), 400_000_000)
        self.assertIsNone(self.meter.period_ms(400_000_000))
        self.meter.clear()
        self.assertIsNone(self.meter.key)

    def test_scalar_meter_does_not_pin_frame_or_spectrum(self):
        class Frame:
            source_id, sequence, epoch, unit = "s", 1, 1, "dBm"
        value = Frame()
        value.identity = value  # Hostile generic metadata must not retain it.
        value.receiver_id = value
        reference = weakref.ref(value)
        self.meter.admit(value)
        del value
        gc.collect()
        self.assertIsNone(reference())

    def test_ring_bounded_time_regression_and_old_paint_do_not_fake_cadence(self):
        for sequence in range(2000):
            self.paint(frame(sequence), 1_000_000_000 + sequence * 1_000_000)
        self.assertEqual(len(self.meter._times), 512)
        before = len(self.meter._times)
        self.paint(frame(1), 4_000_000_000)
        self.assertEqual(len(self.meter._times), before)
        self.paint(frame(2001), 100)
        self.assertIsNone(self.meter.period_ms(100))

    def test_scope_change_or_unknown_key_clears_previous_measurement(self):
        self.paint(frame(1), 1_000_000_000)
        self.paint(frame(2), 1_100_000_000)
        self.meter.admit(SimpleNamespace(sequence=3))
        self.assertIsNone(self.meter.period_ms(1_100_000_000))


class PaintCadenceWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_actual_qt_paint_uses_injected_widget_without_retaining_callbacks(self):
        calls = []
        class InjectedGraphics(pg.GraphicsLayoutWidget):
            def paintEvent(self, event):
                calls.append(1)
                super().paintEvent(event)
        meter = UniquePaintCadence()
        with patch.object(pg, "GraphicsLayoutWidget", InjectedGraphics):
            widget = cadence_graphics_widget(None, meter)
        try:
            self.assertIsInstance(widget, InjectedGraphics)
            meter.admit(frame(1))
            widget.show()
            self.app.processEvents()
            self.assertEqual(len(meter._times), 1)
            widget.viewport().repaint()
            self.assertEqual(len(meter._times), 1)
            meter.admit(frame(2))
            widget.viewport().repaint()
            self.assertEqual(len(meter._times), 2)
            self.assertGreaterEqual(len(calls), 3)
        finally:
            widget.close()
            widget.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
