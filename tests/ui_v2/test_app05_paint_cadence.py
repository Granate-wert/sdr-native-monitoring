"""Only unique admitted source revisions count as newly painted frames."""

import gc
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
from PySide6.QtCore import QRect
from PySide6.QtGui import QPaintEvent
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
from sdr_monitor.ui.v2.spectrum.paint_cadence import (
    UniquePaintCadence,
    cadence_graphics_widget,
    spectrum_paint_key,
)
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene


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

    def test_supplied_receiver_session_and_activation_identity_are_in_key(self):
        def identified(session, activation):
            return SimpleNamespace(
                source_id="s", sequence=1, epoch=1, config_generation=2,
                identity=SimpleNamespace(receiver_id="rx1", session_id=session,
                    host_run_serial=3, host_activation_serial=activation,
                    physical_resource_id="resource"),
            )
        first = spectrum_paint_key(identified("session-a", 4))
        other_session = spectrum_paint_key(identified("session-b", 4))
        other_activation = spectrum_paint_key(identified("session-a", 5))
        self.assertNotEqual(first, other_session)
        self.assertNotEqual(first, other_activation)

    def test_late_same_pass_revision_is_not_a_fresh_frame(self):
        self.paint(frame(1, revision=1), 1_000_000_000)
        self.paint(frame(1, revision=3), 1_100_000_000)
        self.paint(frame(1, revision=2), 1_200_000_000)
        self.assertEqual(len(self.meter._times), 2)
        self.assertEqual(self.meter.period_ms(1_200_000_000), 100)
        self.paint(frame(1, state="complete"), 1_300_000_000)
        self.paint(frame(1, revision=4), 1_400_000_000)
        self.assertEqual(len(self.meter._times), 3)
        self.assertEqual(self.meter._last_key[1][2], "complete")
        self.paint(frame(1, state="gap"), 1_500_000_000)
        self.assertEqual(len(self.meter._times), 4)


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
        def candidate(event):
            if event.region().intersects(QRect(0, 0, 1, 1)):
                return meter.key
            return None
        with patch.object(pg, "GraphicsLayoutWidget", InjectedGraphics):
            widget = cadence_graphics_widget(None, meter, candidate)
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

    def test_spectrum_candidate_requires_displayed_curve_and_relevant_region(self):
        scene = SpectrumScene()
        try:
            scene.resize(720, 420)
            scene.set_frame(SimpleNamespace(
                source_id="source-a", receiver_id="rx1", session_id="session-a",
                epoch=7, config_generation=3, sequence=1, unit="dBm",
                frequencies_hz=np.linspace(100e6, 101e6, 128),
                values=np.linspace(-100.0, -40.0, 128),
            ))
            scene.show()
            self.app.processEvents()
            curve = scene._curves[TraceKind.CURRENT]
            clipped = curve.curve.mapRectToScene(curve.curve.boundingRect()).intersected(
                scene._view_box.sceneBoundingRect())
            relevant = scene._graphics.mapFromScene(clipped).boundingRect()
            self.assertFalse(relevant.isEmpty())
            key = scene.paint_cadence.key
            self.assertIsNotNone(key)
            self.assertEqual(scene._spectrum_paint_candidate(QPaintEvent(relevant)), key)
            self.assertIsNone(scene._spectrum_paint_candidate(QPaintEvent(QRect(-10, -10, 2, 2))))

            # A pending newer key is not eligible while the older curve remains displayed.
            scene.paint_cadence.admit(SimpleNamespace(
                source_id="source-a", receiver_id="rx1", session_id="session-a",
                epoch=7, config_generation=3, sequence=2, unit="dBm"))
            self.assertIsNone(scene._spectrum_paint_candidate(QPaintEvent(relevant)))
            scene.paint_cadence.admit(scene.displayed_frame)
            curve.hide()
            self.assertIsNone(scene._spectrum_paint_candidate(QPaintEvent(relevant)))
            curve.show()
            scene.clear_trace(TraceKind.CURRENT)
            self.assertIsNone(scene._spectrum_paint_candidate(QPaintEvent(relevant)))
            scene.hide()
            self.assertIsNone(scene._spectrum_paint_candidate(QPaintEvent(relevant)))
        finally:
            scene.close()
            scene.deleteLater()
            self.app.processEvents()

    def test_actual_relevant_spectrum_paint_is_committed_after_base_paint_returns(self):
        observed_before_commit = []

        class PaintReturnProbe(pg.GraphicsLayoutWidget):
            def paintEvent(self, event):
                super().paintEvent(event)
                observed_before_commit.append((event.region().boundingRect(),
                                               len(self.paint_cadence._times)))

        with patch.object(pg, "GraphicsLayoutWidget", PaintReturnProbe):
            scene = SpectrumScene()
        try:
            scene.resize(720, 420)
            scene.set_frame(SimpleNamespace(
                source_id="source-a", receiver_id="rx1", session_id="session-a",
                epoch=7, config_generation=3, sequence=1, unit="dBm",
                frequencies_hz=np.linspace(100e6, 101e6, 128),
                values=np.linspace(-100.0, -40.0, 128),
            ))
            scene.show()
            self.app.processEvents()
            # Allow the first show/layout pass to settle, then request a paint
            # of the required trace viewport with its final mapped geometry.
            scene._graphics.viewport().repaint()
            self.app.processEvents()
            self.assertEqual(len(scene.paint_cadence._times), 1)
            self.assertTrue(any(before == 0 for _region, before in observed_before_commit))

            # A disjoint viewport/chrome region and repeated old data cannot advance it.
            curve = scene._curves[TraceKind.CURRENT]
            clipped = curve.curve.mapRectToScene(curve.curve.boundingRect()).intersected(
                scene._view_box.sceneBoundingRect())
            relevant = scene._graphics.mapFromScene(clipped).boundingRect()
            viewport = scene._graphics.viewport().rect()
            outside = next(rect for rect in (
                QRect(viewport.left(), viewport.top(), 1, 1),
                QRect(viewport.right(), viewport.top(), 1, 1),
                QRect(viewport.left(), viewport.bottom(), 1, 1),
                QRect(viewport.right(), viewport.bottom(), 1, 1),
            ) if not relevant.intersects(rect))
            scene._graphics.viewport().repaint(outside)
            self.app.processEvents()
            scene._graphics.viewport().repaint()
            self.app.processEvents()
            self.assertEqual(len(scene.paint_cadence._times), 1)

            # A fresh displayed key is now unpainted. Force and observe an
            # actual disjoint viewport paint before allowing any queued full
            # update to run; deduplication cannot mask a false eligibility.
            scene.set_frame(SimpleNamespace(
                source_id="source-a", receiver_id="rx1", session_id="session-a",
                epoch=7, config_generation=3, sequence=2, unit="dBm",
                frequencies_hz=np.linspace(100e6, 101e6, 128),
                values=np.linspace(-99.0, -39.0, 128),
            ))
            curve = scene._curves[TraceKind.CURRENT]
            clipped = curve.curve.mapRectToScene(curve.curve.boundingRect()).intersected(
                scene._view_box.sceneBoundingRect())
            relevant = scene._graphics.mapFromScene(clipped).boundingRect()
            outside = next(rect for rect in (
                QRect(viewport.left(), viewport.top(), 1, 1),
                QRect(viewport.right(), viewport.top(), 1, 1),
                QRect(viewport.left(), viewport.bottom(), 1, 1),
                QRect(viewport.right(), viewport.bottom(), 1, 1),
            ) if not relevant.intersects(rect))
            before_disjoint = len(observed_before_commit)
            scene._graphics.viewport().repaint(outside)
            self.assertEqual(len(scene.paint_cadence._times), 1)
            disjoint_events = observed_before_commit[before_disjoint:]
            self.assertTrue(any(region == outside and before == 1
                                for region, before in disjoint_events))

            # Only a subsequent paint region that intersects the new trace
            # can close its cadence observation, after the base handler exits.
            before_relevant = len(observed_before_commit)
            scene._graphics.viewport().repaint()
            self.app.processEvents()
            self.assertEqual(len(scene.paint_cadence._times), 2)
            relevant_events = observed_before_commit[before_relevant:]
            self.assertTrue(any(region.intersects(relevant) and before == 1
                                for region, before in relevant_events))
            scene.hide()
            scene._graphics.repaint()
            self.app.processEvents()
            self.assertEqual(len(scene.paint_cadence._times), 2)
        finally:
            scene.close()
            scene.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
