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
from sdr_monitor.domain.host_clock import HostClockKind, HostClockScope
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
    ref = PaneDeliveryLedger(("one",)).admit(
        "one", identity(owner_scope=scope, ready=ready(scope)), view=PaneDeliveryView.WATERFALL)
    assert ref is not None
    return ref


def paint_receipt(ref: PaneDeliveryObligationRef) -> PanePaintReturnReceipt:
    return PanePaintReturnReceipt(
        ref, HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid()), 1, 2)


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
        ledger = PaneDeliveryLedger(("one",))
        scope = owner_scope(host_process_id=os.getpid())
        ref = ledger.admit("one", identity(owner_scope=scope, ready=ready(scope)),
                           view=PaneDeliveryView.WATERFALL)
        assert ref is not None
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                      Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
            self.assertTrue(ledger.note(ref, stage))
        pane = WaterfallPane()
        pane.set_delivery_stage_callback(lambda actual, stage: ledger.note(actual, stage))

        def timed(receipt):
            return ledger.note(receipt.ref, Stage.PAINT_RETURNED, paint_return=receipt)

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

        events = [event for event in ledger.snapshot().events if event.ref == ref]
        returned = [event for event in events if event.stage is Stage.PAINT_RETURNED]
        self.assertEqual(len(returned), 1)
        self.assertIsNotNone(returned[0].paint_return)
        paint = cast(PanePaintReturnReceipt, returned[0].paint_return)
        self.assertIs(paint.ref, ref)
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

    def test_timed_terminal_ref_is_not_reattached_after_stop(self):
        """Regression: typed success must enter the same local terminal fence."""
        ledger = PaneDeliveryLedger(("one",))
        scope = owner_scope(host_process_id=os.getpid())
        ref = ledger.admit("one", identity(owner_scope=scope, ready=ready(scope)))
        self.assertIsNotNone(ref)
        assert ref is not None
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                      Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
            self.assertTrue(ledger.note(ref, stage))
        scene = SpectrumScene()
        scene.set_delivery_stage_callback(lambda actual, stage: ledger.note(actual, stage))

        def timed(receipt):
            ledger.note(receipt.ref, Stage.PAINT_RETURNED, paint_return=receipt)
            return True

        scene.set_paint_return_callback(timed)
        scene.resize(720, 420)
        scene.set_frame(spectrum_frame(1), obligation_ref=ref)
        scene.show()
        self.app.processEvents()
        scene._graphics.viewport().repaint()
        self.app.processEvents()
        scene.stop_delivery_custody((ref,))
        scene.set_frame(spectrum_frame(2), obligation_ref=ref)
        scene._graphics.viewport().repaint()
        self.app.processEvents()
        self.assertEqual(ledger.snapshot().duplicate_events, 0)
        self.retire(scene)

    def test_reentrant_spectrum_observer_cannot_mark_replacement_ref(self):
        first = ref_for_current_process()
        second = replace(first, sequence=first.sequence + 1)
        scene = SpectrumScene()
        scene._displayed_delivery_ref = first

        def reentrant(_receipt):
            scene._displayed_delivery_ref = second
            return True

        scene.set_paint_return_callback(reentrant)
        scene._spectrum_paint_returned_timed(paint_receipt(first))
        self.assertIs(scene.displayed_delivery_ref, second)
        self.assertFalse(scene._displayed_delivery_paint_returned)
        self.assertTrue(scene._delivery_ref_is_closed(first))
        self.retire(scene)

    def test_reentrant_persistence_observer_cannot_mark_replacement_ref(self):
        from tests.ui_v2.test_app07_allview_custody import persistence_identity

        source = persistence_identity()
        scope = replace(source.owner_scope, host_process_id=os.getpid())
        ready_identity = replace(source.ready, host_process_id=os.getpid())
        ledger = PaneDeliveryLedger(("one",))
        first = ledger.admit("one", replace(source, owner_scope=scope, ready=ready_identity),
                             view=PaneDeliveryView.PERSISTENCE)
        assert first is not None
        second = replace(first, sequence=first.sequence + 1)
        scene = SpectrumScene()
        scene._persistence_delivery_ref = first

        def reentrant(_receipt):
            scene._persistence_delivery_ref = second
            return True

        scene.set_paint_return_callback(reentrant)
        scene._spectrum_paint_returned_timed(paint_receipt(first))
        self.assertIs(scene._persistence_delivery_ref, second)
        self.assertFalse(scene._persistence_delivery_paint_returned)
        self.assertFalse(scene.persistence_delivery_requires_ui_rejection(second))
        self.assertFalse(scene.persistence_delivery_requires_ui_rejection(first))
        self.retire(scene)

    def test_reentrant_waterfall_observer_cannot_mark_replacement_ref(self):
        first = waterfall_ref_for_current_process()
        second = replace(first, sequence=first.sequence + 1)
        pane = WaterfallPane()
        pane._waterfall_delivery_ref = first

        def reentrant(_receipt):
            pane._waterfall_delivery_ref = second
            return True

        pane.set_paint_return_callback(reentrant)
        pane._waterfall_paint_returned_timed(paint_receipt(first))
        self.assertIs(pane._waterfall_delivery_ref, second)
        self.assertFalse(pane._waterfall_delivery_returned)
        self.assertFalse(pane.delivery_requires_ui_rejection(second))
        self.assertFalse(pane.delivery_requires_ui_rejection(first))
        self.retire(pane)

    def test_real_product_handle_board_forwards_typed_receipt_to_same_ledger(self):
        from tests.ui_v2.test_app07_start_refresh_responsive import StartRefreshResponsiveTests

        owner = StartRefreshResponsiveTests("run")
        setattr(owner, "app", self.app)
        product = owner._surface()
        try:
            handle = product.handle
            callback = product.surface.board._paint_return_callback
            self.assertIsNotNone(callback)
            self.assertIs(callback.__self__, handle)
            self.assertIs(callback.__func__, handle.report_paint_return.__func__)
            pane_id = handle.layout.slots[0].request.pane_id
            ledger = handle.session._delivery_ledger
            scope = owner_scope(host_process_id=os.getpid())
            ref = ledger.admit(pane_id, identity(
                pane=pane_id, owner_scope=scope, ready=ready(scope)))
            self.assertIsNotNone(ref)
            assert ref is not None
            for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                          Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
                self.assertTrue(ledger.note(ref, stage))
            pane = product.surface.board.pane(1)
            product.surface.resize(900, 700)
            product.surface.show()
            self.app.processEvents()
            pane.spectrum_scene.set_frame(spectrum_frame(1), obligation_ref=ref)
            pane.spectrum_scene._graphics.viewport().repaint()
            self.app.processEvents()
            returned = [event for event in ledger.snapshot().events
                        if event.ref == ref and event.stage is Stage.PAINT_RETURNED]
            self.assertEqual(len(returned), 1)
            self.assertIsNotNone(returned[0].paint_return)
            self.assertEqual(ledger.snapshot().duplicate_events, 0)
            self.assertEqual(ledger.snapshot().accounting_failures, 0)
        finally:
            cleanup_error = owner._dispose(product.graph, product.pool, product.handle, product.surface)
            if cleanup_error is not None:
                raise cleanup_error


if __name__ == "__main__":
    unittest.main()
