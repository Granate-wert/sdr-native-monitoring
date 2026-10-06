"""Original-view paint-return receipts are optional and preserve legacy custody."""

from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
import unittest
from unittest.mock import patch

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
from PySide6.QtCore import QRect
from PySide6.QtGui import QPaintEvent
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryObligationRef, PaneDeliveryStage as Stage
from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView
from sdr_monitor.domain.pane_paint_timing import PanePaintReturnReceipt
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from sdr_monitor.ui.v2.spectrum.paint_cadence import (
    UniquePaintCadence,
    cadence_graphics_widget,
)
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.waterfall.contracts import WaterfallLineFrame
from sdr_monitor.ui.v2.waterfall.pane import WaterfallPane
from tests.test_app07_pane_analytical_identity import owner_scope, ready
from tests.test_app07_pane_delivery_obligations import identity


def spectrum_frame(sequence: int) -> SimpleNamespace:
    return SimpleNamespace(
        source_id="source", receiver_id="rx1", session_id="session",
        epoch=1, config_generation=1, sequence=sequence, unit="dBm",
        frequencies_hz=np.linspace(100e6, 101e6, 128),
        values=np.linspace(-100.0, -40.0, 128),
    )


def ref_for_current_process() -> PaneDeliveryObligationRef:
    scope = owner_scope(host_process_id=os.getpid())
    ref = PaneDeliveryLedger(("one",)).admit("one", identity(owner_scope=scope, ready=ready(scope)))
    assert ref is not None
    return replace(ref, view=PaneDeliveryView.SPECTRUM)


def waterfall_ref_for_current_process() -> PaneDeliveryObligationRef:
    scope = owner_scope(host_process_id=os.getpid())
    ref = PaneDeliveryLedger(("one",)).admit("one", identity(owner_scope=scope, ready=ready(scope)))
    assert ref is not None
    return replace(ref, view=PaneDeliveryView.WATERFALL)


class PaintReturnTimingTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls):
        cls.app = cast(QApplication, QApplication.instance() or QApplication([]))

    def tearDown(self):
        self.app.processEvents()

    def retire(self, widget):
        widget.close()
        widget.deleteLater()
        self.app.processEvents()

    def test_receipt_observer_runs_after_base_paint_before_legacy_custody(self):
        ref = ref_for_current_process()
        events: list[tuple[str, object]] = []
        scene = SpectrumScene()
        scene.set_delivery_stage_callback(lambda actual, stage: events.append(("legacy", stage)))

        def timed(receipt):
            events.append(("timed", receipt))
            return False

        scene.set_paint_return_callback(timed)
        scene.resize(720, 420)
        scene.set_frame(spectrum_frame(1), obligation_ref=ref)
        scene.show()
        self.app.processEvents()
        scene._graphics.viewport().repaint()
        self.app.processEvents()

        receipts = [value for kind, value in events if kind == "timed"]
        self.assertEqual(len(receipts), 1)
        self.assertIsInstance(receipts[0], PanePaintReturnReceipt)
        receipt = cast(PanePaintReturnReceipt, receipts[0])
        self.assertGreaterEqual(receipt.sampled_after_return_ns, receipt.before_paint_ns)
        self.assertEqual([kind for kind, _value in events if kind in ("timed", "legacy")][-2:],
                         ["timed", "legacy"])
        self.assertEqual(sum(stage is Stage.PAINT_RETURNED for kind, stage in events if kind == "legacy"), 1)
        self.retire(scene)

    def test_waterfall_receipt_uses_original_ref(self):
        ref = waterfall_ref_for_current_process()
        events: list[tuple[str, object]] = []
        pane = WaterfallPane()
        pane.set_delivery_stage_callback(lambda actual, stage: events.append(("legacy", stage)))

        def timed(receipt):
            events.append(("timed", receipt))
            return False

        pane.set_paint_return_callback(timed)
        values = np.linspace(-90.0, -40.0, 64, dtype=np.float32)
        edges = np.linspace(100e6, 101e6, 65, dtype=np.float64)
        values.setflags(write=False)
        edges.setflags(write=False)
        self.assertTrue(pane.set_line(WaterfallLineFrame(
            values, edges, 1_000_000_000, 1, "dBm", sequence=1), obligation_ref=ref))
        pane.resize(720, 420)
        pane.show()
        self.app.processEvents()
        pane._graphics.viewport().repaint()
        self.app.processEvents()

        receipts = [value for kind, value in events if kind == "timed"]
        self.assertEqual(len(receipts), 1)
        self.assertIs(cast(PanePaintReturnReceipt, receipts[0]).ref, ref)
        self.assertEqual(sum(stage is Stage.PAINT_RETURNED for kind, stage in events if kind == "legacy"), 1)
        self.retire(pane)

    def test_failed_base_paint_has_no_timed_success(self):
        calls: list[object] = []

        class FailingGraphics(pg.GraphicsLayoutWidget):
            def paintEvent(self, event):
                raise RuntimeError("base paint failed")

        meter = UniquePaintCadence()
        meter.admit(spectrum_frame(1))
        with patch.object(pg, "GraphicsLayoutWidget", FailingGraphics):
            widget = cadence_graphics_widget(
                None, meter, lambda _event: meter.key,
                lambda _event: ref_for_current_process(),
                lambda _ref: calls.append("legacy"),
                lambda receipt: calls.append(receipt),
            )
        try:
            with self.assertRaises(RuntimeError):
                widget.paintEvent(QPaintEvent(QRect(0, 0, 10, 10)))
            self.assertEqual(calls, [])
            self.assertEqual(len(meter._times), 0)
        finally:
            self.retire(widget)

    def test_clock_sampling_failure_keeps_base_paint_and_legacy_custody(self):
        calls: list[object] = []
        meter = UniquePaintCadence()
        meter.admit(spectrum_frame(1))
        widget = cadence_graphics_widget(
            None, meter, lambda _event: meter.key,
            lambda _event: ref_for_current_process(),
            lambda _ref: calls.append("legacy"),
            lambda receipt: calls.append(receipt),
        )
        try:
            with patch("sdr_monitor.ui.v2.spectrum.paint_cadence.perf_counter_ns",
                       side_effect=RuntimeError("clock unavailable")):
                widget.paintEvent(QPaintEvent(QRect(0, 0, 10, 10)))
            self.assertEqual(calls, ["legacy"])
            self.assertEqual(len(meter._times), 0)
        finally:
            self.retire(widget)


if __name__ == "__main__":
    unittest.main()
