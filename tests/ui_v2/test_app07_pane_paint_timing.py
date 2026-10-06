"""Original-view paint-return receipts are optional and preserve legacy custody."""

from __future__ import annotations

import os
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import replace
from threading import Event, get_ident
from time import monotonic, sleep
from types import SimpleNamespace
from typing import cast
import unittest
from unittest.mock import patch

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
from PySide6.QtCore import QRect, QCoreApplication, QEvent
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

    def wait_for(self, predicate, timeout=8.0):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            sleep(0.01)
        self.fail("finite UI Stop handoff did not settle")

    @contextmanager
    def stop_product(self, *, peers=False):
        from tests.ui_v2.test_app07_start_refresh_responsive import StartRefreshResponsiveTests
        from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph
        from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
        from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
        from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
        from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session

        fixture = StartRefreshResponsiveTests("run")
        fixture.app = self.app
        if not peers:
            _native, graph = _ad_graph()
            graphs = (graph,)
            source = graph.live.discover(startup=True)[0].device_id
            pool = PaneProductGraphPool(lambda _resource: graph)
            prepared = prepare_user_pane_session((
                PaneSlotDraft(1, source, 100e6, 108e6), PaneSlotDraft(2),
                PaneSlotDraft(3), PaneSlotDraft(4),
            ), pool_factory=lambda: pool)
            apply_user_pane_session(prepared)
            closed = []
            surface = IndependentPaneSessionV2(prepared.handle,
                close_layout=lambda handle: closed.append(handle))
            product = SimpleNamespace(graph=graph, pool=pool, handle=prepared.handle,
                                      surface=surface, closed=closed)
        else:
            graphs = tuple(_ad_graph(uri=f"ip:paint-{number}.local", serial=f"paint-{number}")[1]
                           for number in (1, 2))
            sources = tuple(graph.live.discover(startup=True)[0].device_id for graph in graphs)
            pool = PaneProductGraphPool(lambda resource: graphs[int(resource.rsplit("-", 1)[1]) - 1])
            prepared = prepare_user_pane_session((
                PaneSlotDraft(1, sources[0], 100e6, 108e6),
                PaneSlotDraft(2, sources[0], 100e6, 108e6),
                PaneSlotDraft(3, sources[1], 100e6, 108e6), PaneSlotDraft(4),
            ), pool_factory=lambda: pool)
            apply_user_pane_session(prepared)
            closed = []
            surface = IndependentPaneSessionV2(prepared.handle, confirm_shared_stop=lambda _impact: True,
                close_layout=lambda handle: closed.append(handle))
            product = SimpleNamespace(graph=graphs[0], pool=pool, handle=prepared.handle,
                                      surface=surface, closed=closed)
        try:
            yield product
        finally:
            # Explicit UI Stop/retry must settle before the ordinary fixture can close.
            product.surface._stop_all()
            self.wait_for(lambda: not product.surface._ui_stop_handoffs)
            error = fixture._dispose(product.graph, product.pool, product.handle, product.surface)
            for graph in graphs[1:]:
                graph.live.shutdown()
            if error is not None:
                raise error

    def admitted_ref(self, ledger, pane="one", resource="resource", *, offer=1,
                     view=PaneDeliveryView.SPECTRUM):
        scope = owner_scope(host_process_id=os.getpid())
        if view is PaneDeliveryView.PERSISTENCE:
            from tests.ui_v2.test_app07_allview_custody import persistence_identity
            bound = replace(persistence_identity(offer), pane_id=pane, physical_stream_resource_id=resource)
        else:
            bound = identity(pane=pane, offer=offer, owner_scope=scope,
                             ready=ready(scope, offer=offer), physical_stream_resource_id=resource)
        ref = ledger.admit(pane, bound, view=view)
        self.assertIsNotNone(ref)
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                      Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
            self.assertTrue(ledger.note(ref, stage))
        return ref

    def test_exact_observer_lower_pending_survives_higher_terminal_each_view(self):
        from tests.ui_v2.test_app07_allview_custody import density_frame, persistence_identity
        from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_spectrum
        from tests.ui_v2.test_app07_allview_custody import live_frame
        for view in PaneDeliveryView:
            with self.subTest(view=view):
                ledger = PaneDeliveryLedger(("one",))
                scene, waterfall = SpectrumScene(), WaterfallPane()
                scene.set_delivery_stage_callback(ledger.note)
                waterfall.set_delivery_stage_callback(ledger.note)
                scene.set_delivery_stage_observer(ledger.delivery_stage_if_retained)
                waterfall.set_delivery_stage_observer(ledger.delivery_stage_if_retained)
                refs = []
                for offer in range(1, 7):
                    if view is PaneDeliveryView.PERSISTENCE:
                        ref = ledger.admit("one", persistence_identity(offer), view=view)
                        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                                      Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
                            self.assertTrue(ledger.note(ref, stage))
                    else:
                        ref = self.admitted_ref(ledger, offer=offer, view=view)
                    refs.append(ref)
                for index in (3, 5):
                    self.assertTrue(ledger.note(refs[index], Stage.UI_REJECTED))
                    if view is PaneDeliveryView.WATERFALL:
                        waterfall._remember_waterfall_delivery_ref(refs[index])
                    else:
                        scene._remember_terminal_delivery_ref(refs[index])
                for index in (2, 4):
                    ref = refs[index]
                    if view is PaneDeliveryView.WATERFALL:
                        waterfall.set_line(waterfall_line_from_spectrum(live_frame(index + 1)), obligation_ref=ref)
                        self.assertIs(waterfall._waterfall_delivery_ref, ref)
                    elif view is PaneDeliveryView.PERSISTENCE:
                        scene.set_persistence_delivery_ref(ref, density_frame(index + 1))
                        self.assertIs(scene._persistence_delivery_ref, ref)
                    else:
                        scene.set_frame(spectrum_frame(index + 1), obligation_ref=ref)
                        self.assertIs(scene.displayed_delivery_ref, ref)
                before = ledger.snapshot()
                scene.detach_delivery_custody(tuple(refs))
                waterfall.detach_delivery_custody(tuple(refs))
                ledger.reconcile_ui_stop_cleared(tuple(refs))
                self.assertEqual(ledger.snapshot().accounting_failures, before.accounting_failures)
                scene.release_graphics_after_shutdown()
                waterfall.release_presentation_after_shutdown()
                self.retire(scene)
                self.retire(waterfall)

    def test_exact_observer_terminal_copy_foreign_evicted_unknown_never_attaches(self):
        from tests.ui_v2.test_app07_allview_custody import live_frame, density_frame
        from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_spectrum
        ledger = PaneDeliveryLedger(("one",))
        scene, waterfall = SpectrumScene(), WaterfallPane()
        scene.set_delivery_stage_callback(ledger.note)
        waterfall.set_delivery_stage_callback(ledger.note)
        scene.set_delivery_stage_observer(ledger.delivery_stage_if_retained)
        waterfall.set_delivery_stage_observer(ledger.delivery_stage_if_retained)
        terminal = self.admitted_ref(ledger)
        self.assertTrue(ledger.note(terminal, Stage.UI_REJECTED))
        copied = replace(self.admitted_ref(ledger, offer=2))
        foreign = self.admitted_ref(PaneDeliveryLedger(("one",)))
        for offer in range(3, 260):
            ref = self.admitted_ref(ledger, offer=offer)
            self.assertTrue(ledger.note(ref, Stage.UI_REJECTED))
        self.assertIsNone(ledger.delivery_stage_if_retained(terminal))
        for ref in (terminal, copied, foreign):
            with self.subTest(ref=ref.sequence, graph=ref.graph_instance_id):
                before = ledger.snapshot()
                scene.set_frame(spectrum_frame(5), obligation_ref=ref)
                self.assertIsNone(scene.displayed_delivery_ref)
                self.assertFalse(scene.delivery_requires_ui_rejection(ref))
                self.assertEqual(ledger.snapshot(), before)
        unknown = self.admitted_ref(ledger, offer=260, view=PaneDeliveryView.WATERFALL)
        waterfall.set_delivery_stage_observer(lambda _ref: None)
        rows = waterfall.metrics.rows_admitted
        waterfall.set_line(waterfall_line_from_spectrum(live_frame(1)), obligation_ref=unknown)
        self.assertIsNone(waterfall._waterfall_delivery_ref)
        self.assertGreater(waterfall.metrics.rows_admitted, rows)
        scene.set_delivery_stage_observer(lambda _ref: None)
        pending = self.admitted_ref(ledger, offer=261, view=PaneDeliveryView.PERSISTENCE)
        density = density_frame()
        scene.set_persistence_delivery_ref(pending, density)
        scene.set_persistence_frame(density)
        self.assertIsNone(scene._persistence_delivery_ref)
        self.assertIsNotNone(scene._persistence.latest_view)
        scene.release_graphics_after_shutdown()
        waterfall.release_presentation_after_shutdown()
        self.retire(scene)
        self.retire(waterfall)

    def test_stage_observer_reentrant_stop_or_replacement_preserves_later_custody(self):
        for kind in ("stop", "replace"):
            with self.subTest(kind=kind):
                ledger = PaneDeliveryLedger(("one",))
                first = self.admitted_ref(ledger)
                second = self.admitted_ref(ledger, offer=2)
                scene = SpectrumScene()
                scene.set_delivery_stage_callback(ledger.note)
                entered = False
                def observe(ref):
                    nonlocal entered
                    if not entered:
                        entered = True
                        if kind == "stop":
                            scene.set_ui_stop_pending(True)
                        else:
                            scene.set_frame(spectrum_frame(2), obligation_ref=second)
                    return ledger.delivery_stage_if_retained(ref)
                scene.set_delivery_stage_observer(observe)
                scene.set_frame(spectrum_frame(1), obligation_ref=first)
                self.assertIs(scene.displayed_delivery_ref, None if kind == "stop" else second)
                scene.detach_delivery_custody((first, second))
                ledger.reconcile_ui_stop_cleared((first, second))
                scene.release_graphics_after_shutdown()
                self.retire(scene)

    def test_complete_stop_captures_many_unknown_unowned_refs_off_qt_and_gates_ack(self):
        with self.stop_product(peers=True) as product:
            handle, surface = product.handle, product.surface
            ledger = handle.session._delivery_ledger
            resources = tuple(item.physical_stream_resource_id for item in handle.pump.snapshot())
            resource, peer = resources
            refs = []
            for number in (1, 2):
                pane_id = handle.layout.slots[number - 1].request.pane_id
                pane = surface.board.pane(number)
                pane.spectrum_scene.set_delivery_stage_observer(lambda _ref: None)
                for offer in range(1, 9):
                    ref = self.admitted_ref(ledger, pane_id, resource, offer=offer)
                    refs.append(ref)
                    pane.spectrum_scene.set_frame(spectrum_frame(offer), obligation_ref=ref)
                self.assertIsNone(pane.spectrum_scene.displayed_delivery_ref)
            peer_id = handle.layout.slots[2].request.pane_id
            peer_ref = self.admitted_ref(ledger, peer_id, peer)
            qt_thread = get_ident()
            threads, captured_refs = [], []
            ack_entered, release_ack = Event(), Event()
            original_capture = handle.session.pane_ui_stop_refs_after_stop
            original_ack = handle.session.reconcile_ui_stop_cleared
            def capture(resource_id):
                threads.append(("capture", get_ident()))
                result = original_capture(resource_id)
                captured_refs.extend(result)
                return result
            def ack(batch):
                threads.append(("ack", get_ident()))
                ack_entered.set()
                if not release_ack.wait(8):
                    raise RuntimeError("test ack watchdog")
                return original_ack(batch)
            try:
                with patch.object(handle.session, "pane_ui_stop_refs_after_stop", side_effect=capture), \
                     patch.object(handle.session, "reconcile_ui_stop_cleared", side_effect=ack):
                    surface._begin_ui_stop_handoff(resource)
                    already_done = handle.pump.stop_resource(resource)
                    self.wait_for(already_done.done)
                    self.assertIsNone(already_done.exception())
                    surface._watch_stop_boundary(already_done, resource)
                    self.wait_for(ack_entered.is_set)
                    self.assertEqual(captured_refs, refs)
                    self.assertEqual({thread for _stage, thread in threads}, {
                        handle.pump._workers[resource]._thread.ident})
                    self.assertTrue(all(thread != qt_thread for _stage, thread in threads))
                    self.assertTrue(handle.ui_stop_pending(resource))
                    self.assertFalse(handle.ui_stop_pending(peer))
                    self.assertFalse(handle.can_close())
                    surface._refresh()
                    self.assertFalse(surface.start_selected.isEnabled())
                    self.assertTrue(surface.start_all.isEnabled())
                    self.assertFalse(surface.close_layout.isEnabled())
                    surface._request_close_layout()
                    self.assertEqual(product.closed, [])
                    with patch.object(handle.pump, "start_resource") as start:
                        surface._start_selected()
                        start.assert_not_called()
                    self.assertEqual(ledger.delivery_stage_if_retained(peer_ref), Stage.UI_ADMITTED)
                    release_ack.set()
                    self.wait_for(lambda: not handle.ui_stop_pending(resource))
            finally:
                release_ack.set()
            snapshot = ledger.snapshot()
            self.assertTrue(all(ledger.delivery_stage_if_retained(ref) is Stage.STOP_CLEARED for ref in refs))
            self.assertEqual((snapshot.duplicate_events, snapshot.accounting_failures), (0, 0))
            self.assertEqual(ledger.delivery_stage_if_retained(peer_ref), Stage.UI_ADMITTED)

    def test_stop_capture_detach_ack_failures_remain_visible_and_explicitly_retry(self):
        for failure in ("stop", "capture", "detach", "ack"):
            with self.subTest(failure=failure), self.stop_product() as product:
                handle, surface = product.handle, product.surface
                resource = handle.pump.snapshot()[0].physical_stream_resource_id
                ledger = handle.session._delivery_ledger
                pane_id = handle.layout.slots[0].request.pane_id
                ref = self.admitted_ref(ledger, pane_id, resource)
                surface._begin_ui_stop_handoff(resource)
                actual_stop = handle.pump.stop_resource(resource)
                self.wait_for(actual_stop.done)
                self.assertIsNone(actual_stop.exception())
                stop = Future()
                method = {"capture": (handle.pump, "capture_ui_stop_refs"),
                          "detach": (surface.board, "detach_ui_stop_refs"),
                          "ack": (handle.pump, "reconcile_ui_stop_cleared")}
                if failure == "stop":
                    surface._watch_stop_boundary(stop, resource)
                    stop.set_exception(RuntimeError("injected failed Stop"))
                    self.wait_for(lambda: surface._ui_stop_handoffs[resource].phase == "error")
                else:
                    target, name = method[failure]
                    with patch.object(target, name, side_effect=RuntimeError("injected " + failure)) as rejected:
                        surface._watch_stop_boundary(stop, resource)
                        stop.set_result(None)
                        self.wait_for(lambda: surface._ui_stop_handoffs[resource].phase == "error")
                        rejected.assert_called_once()
                surface._refresh()
                self.assertTrue(handle.ui_stop_pending(resource))
                self.assertFalse(handle.can_close())
                self.assertTrue(surface.stop_selected.isEnabled())
                self.assertTrue(surface.stop_all.isEnabled())
                self.assertFalse(surface.error.isHidden())
                self.assertEqual(ledger.delivery_stage_if_retained(ref), Stage.UI_ADMITTED)
                with patch.object(handle.pump, "start_resource") as start:
                    surface._stop_selected()
                    self.wait_for(lambda: not handle.ui_stop_pending(resource))
                    start.assert_not_called()
                self.assertEqual(ledger.delivery_stage_if_retained(ref), Stage.STOP_CLEARED)
                self.assertEqual((ledger.snapshot().duplicate_events, ledger.snapshot().accounting_failures), (0, 0))

    def test_stale_stop_future_and_queued_token_cannot_bind_new_attempt_or_foreign_handle(self):
        from sdr_monitor.ui.v2.workspaces.independent_pane_session import _UiStopHandoff
        with self.stop_product() as product:
            handle, surface = product.handle, product.surface
            resource = handle.pump.snapshot()[0].physical_stream_resource_id
            old = surface._begin_ui_stop_handoff(resource)
            delayed = Future()
            surface._watch_stop_boundary(delayed, resource)
            surface._fail_ui_stop_handoff(resource, "retry required")
            current = surface._begin_ui_stop_handoff(resource)
            self.assertIsNotNone(current)
            with patch.object(handle.pump, "capture_ui_stop_refs") as capture:
                delayed.set_result(None)
                self.app.processEvents()
                capture.assert_not_called()
            surface._on_stop_boundary(replace(old, phase="acked"))
            surface._on_stop_boundary(_UiStopHandoff(resource, current.generation, object(), object(), phase="acked"))
            self.assertIs(surface._ui_stop_handoffs[resource], current)
            self.assertTrue(handle.ui_stop_pending(resource))
            done = handle.pump.stop_resource(resource)
            self.wait_for(done.done)
            surface._watch_stop_boundary(done, resource)
            self.wait_for(lambda: not handle.ui_stop_pending(resource))
            surface._on_stop_boundary(replace(old, phase="captured"))
            self.assertFalse(handle.ui_stop_pending(resource))
            from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase
            surface._start_selected()
            self.wait_for(lambda: handle.pump.snapshot()[0].phase is PanePumpPhase.RUNNING)
            pane_id = handle.layout.slots[0].request.pane_id
            new_ref = self.admitted_ref(handle.session._delivery_ledger, pane_id, resource, offer=2)
            scene = surface.board.pane(1).spectrum_scene
            scene.set_frame(spectrum_frame(2), obligation_ref=new_ref)
            surface._on_stop_boundary(replace(old, phase="captured", refs=(new_ref,)))
            self.assertIs(scene.displayed_delivery_ref, new_ref)
            self.assertFalse(handle.ui_stop_pending(resource))

    def test_overlapping_resources_keep_separate_detach_fences_and_duplicate_tokens_are_inert(self):
        with self.stop_product(peers=True) as product:
            handle, surface = product.handle, product.surface
            ledger = handle.session._delivery_ledger
            resources = tuple(item.physical_stream_resource_id for item in handle.pump.snapshot())
            refs = {}
            for number in (1, 2, 3):
                pane_id = handle.layout.slots[number - 1].request.pane_id
                resource = surface._pane_resources[pane_id]
                refs[number] = self.admitted_ref(ledger, pane_id, resource)
                surface.board.pane(number).spectrum_scene.set_frame(spectrum_frame(number),
                                                                    obligation_ref=refs[number])
            held = {resource: Future() for resource in resources}
            batches = {}
            original_ack = handle.pump.reconcile_ui_stop_cleared
            def hold_ack(resource, batch):
                batches[resource] = batch
                return held[resource]
            with patch.object(handle.pump, "reconcile_ui_stop_cleared", side_effect=hold_ack):
                surface._stop_all()
                self.wait_for(lambda: len(batches) == 2)
                first = surface._ui_stop_handoffs[resources[0]]
                self.assertEqual(surface.board.pane(1).spectrum_scene._ui_stop_detached_refs,
                                 batches[resources[0]])
                self.assertEqual(surface.board.pane(2).spectrum_scene._ui_stop_detached_refs,
                                 batches[resources[0]])
                self.assertEqual(surface.board.pane(3).spectrum_scene._ui_stop_detached_refs,
                                 batches[resources[1]])
                self.assertFalse(handle.can_close())
                # Repeated captured payloads must not submit another ack or overwrite a fence.
                surface._on_stop_boundary(replace(first, phase="captured"))
                self.assertEqual(len(batches), 2)
                actual_peer = original_ack(resources[1], batches[resources[1]])
                self.wait_for(actual_peer.done)
                held[resources[1]].set_result(actual_peer.result())
                self.wait_for(lambda: not handle.ui_stop_pending(resources[1]))
                self.assertTrue(handle.ui_stop_pending(resources[0]))
                self.assertEqual(surface.board.pane(1).spectrum_scene._ui_stop_detached_refs,
                                 batches[resources[0]])
                self.assertFalse(surface.board.pane(3).spectrum_scene._ui_stop_detached_refs)
                actual_first = original_ack(resources[0], batches[resources[0]])
                self.wait_for(actual_first.done)
                held[resources[0]].set_result(actual_first.result())
                self.wait_for(lambda: not surface._ui_stop_handoffs)
                before = ledger.snapshot()
                surface._on_stop_boundary(replace(first, phase="acked"))
                self.assertEqual(ledger.snapshot(), before)
            self.assertTrue(all(ledger.delivery_stage_if_retained(ref) is Stage.STOP_CLEARED
                                for ref in refs.values()))
            self.assertEqual((ledger.snapshot().duplicate_events, ledger.snapshot().accounting_failures), (0, 0))

    def test_paint_after_capture_before_queued_detach_wins_once_and_late_retry_cannot_attach(self):
        with self.stop_product() as product:
            handle, surface = product.handle, product.surface
            pane_id = handle.layout.slots[0].request.pane_id
            resource = surface._pane_resources[pane_id]
            ledger = handle.session._delivery_ledger
            ref = self.admitted_ref(ledger, pane_id, resource)
            scene = surface.board.pane(1).spectrum_scene
            surface.resize(900, 700)
            surface.show()
            self.app.processEvents()
            frame = spectrum_frame(1)
            scene.set_frame(frame, obligation_ref=ref)
            self.assertEqual(ledger.delivery_stage_if_retained(ref), Stage.PAINT_SCHEDULED)
            surface._begin_ui_stop_handoff(resource)
            stop = handle.pump.stop_resource(resource)
            # Complete the REAL worker Stop without processing queued Qt boundaries.
            stopped = Event()
            stop.add_done_callback(lambda _done: stopped.set())
            self.assertTrue(stopped.wait(5))
            self.assertIsNone(stop.exception())
            captured = Event()
            original = handle.session.pane_ui_stop_refs_after_stop
            def observe_capture(resource_id):
                batch = original(resource_id)
                captured.set()
                return batch
            with patch.object(handle.session, "pane_ui_stop_refs_after_stop", side_effect=observe_capture):
                surface._watch_stop_boundary(stop, resource)
                self.assertTrue(captured.wait(5))
                scene._graphics.viewport().repaint()
                self.assertEqual(ledger.delivery_stage_if_retained(ref), Stage.PAINT_RETURNED)
                self.wait_for(lambda: not handle.ui_stop_pending(resource))
            self.assertIsNone(scene.displayed_delivery_ref)
            before = ledger.snapshot()
            scene.set_frame(frame, obligation_ref=ref)
            scene.set_frame(spectrum_frame(2), obligation_ref=ref)
            scene._graphics.viewport().repaint()
            self.app.processEvents()
            self.assertIsNone(scene.displayed_delivery_ref)
            self.assertEqual(ledger.snapshot(), before)
            self.assertEqual((before.duplicate_events, before.accounting_failures), (0, 0))

    def test_async_capture_and_ack_failed_futures_keep_gate_until_stop_all_retry(self):
        for operation in ("capture_ui_stop_refs", "reconcile_ui_stop_cleared"):
            with self.subTest(operation=operation), self.stop_product() as product:
                handle, surface = product.handle, product.surface
                resource = handle.pump.snapshot()[0].physical_stream_resource_id
                rejected = Future()
                with patch.object(handle.pump, operation, return_value=rejected) as call:
                    surface._stop_selected()
                    self.wait_for(lambda: call.call_count == 1)
                    self.assertTrue(handle.ui_stop_pending(resource))
                    rejected.set_exception(RuntimeError("injected asynchronous cleanup failure"))
                    self.wait_for(lambda: surface._ui_stop_handoffs[resource].phase == "error")
                self.assertFalse(handle.can_close())
                surface._stop_all()
                self.wait_for(lambda: not handle.ui_stop_pending(resource))
                self.assertTrue(handle.can_close())

    def test_explicit_retry_after_failed_stop_future_precedes_queued_error_safely(self):
        with self.stop_product() as product:
            handle, surface = product.handle, product.surface
            resource = handle.pump.snapshot()[0].physical_stream_resource_id
            old = surface._begin_ui_stop_handoff(resource)
            rejected = Future()
            surface._watch_stop_boundary(rejected, resource)
            rejected.set_exception(RuntimeError("Stop failed before Qt error handoff"))
            # The error signal is queued, but explicit retry sees the original done failure.
            current = surface._begin_ui_stop_handoff(resource)
            self.assertIsNotNone(current)
            self.assertGreater(current.generation, old.generation)
            self.app.processEvents()
            self.assertEqual(surface._ui_stop_handoffs[resource].generation, current.generation)
            self.assertTrue(handle.ui_stop_pending(resource))
            stop = handle.pump.stop_resource(resource)
            surface._watch_stop_boundary(stop, resource)
            self.wait_for(lambda: not handle.ui_stop_pending(resource))

    def test_stage_observer_waterfall_and_persistence_reentrant_boundaries(self):
        from tests.ui_v2.test_app07_allview_custody import live_frame, density_frame
        from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_spectrum
        for view in (PaneDeliveryView.WATERFALL, PaneDeliveryView.PERSISTENCE):
            for action in ("stop", "replace"):
                with self.subTest(view=view, action=action):
                    ledger = PaneDeliveryLedger(("one",))
                    first = self.admitted_ref(ledger, view=view)
                    second = self.admitted_ref(ledger, view=view, offer=2)
                    widget = WaterfallPane() if view is PaneDeliveryView.WATERFALL else SpectrumScene()
                    widget.set_delivery_stage_callback(ledger.note)
                    def admit(ref):
                        if view is PaneDeliveryView.WATERFALL:
                            widget.set_line(waterfall_line_from_spectrum(live_frame(ref.sequence)),
                                            obligation_ref=ref)
                        else:
                            widget.set_persistence_delivery_ref(ref, density_frame(ref.sequence))
                    entered = False
                    def observe(ref):
                        nonlocal entered
                        if not entered:
                            entered = True
                            if action == "stop":
                                widget.set_ui_stop_pending(True)
                            else:
                                admit(second)
                        return ledger.delivery_stage_if_retained(ref)
                    widget.set_delivery_stage_observer(observe)
                    admit(first)
                    actual = (widget._waterfall_delivery_ref if view is PaneDeliveryView.WATERFALL
                              else widget._persistence_delivery_ref)
                    self.assertIs(actual, None if action == "stop" else second)
                    widget.detach_delivery_custody((first, second))
                    ledger.reconcile_ui_stop_cleared((first, second))
                    if view is PaneDeliveryView.WATERFALL:
                        widget.release_presentation_after_shutdown()
                    else:
                        widget.release_graphics_after_shutdown()
                    self.retire(widget)

    def test_deleted_qobject_retains_handle_gate_until_new_owner_explicit_stop_retry(self):
        from tests.ui_v2.test_app07_start_refresh_responsive import StartRefreshResponsiveTests
        from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
        from shiboken6 import isValid
        fixture = StartRefreshResponsiveTests("run")
        fixture.app = self.app
        product = fixture._surface()
        handle, surface = product.handle, product.surface
        replacement = None
        try:
            resource = handle.pump.snapshot()[0].physical_stream_resource_id
            surface._begin_ui_stop_handoff(resource)
            actual_stop = handle.pump.stop_resource(resource)
            self.wait_for(actual_stop.done)
            delayed = Future()
            surface._watch_stop_boundary(delayed, resource)
            surface._state_timer.stop()
            surface.delivery.stop()
            surface.deleteLater()
            QCoreApplication.sendPostedEvents(surface, QEvent.Type.DeferredDelete)
            self.assertFalse(isValid(surface))
            delayed.set_result(None)
            self.app.processEvents()
            self.assertTrue(handle.ui_stop_pending(resource))
            self.assertFalse(handle.can_close())
            replacement = IndependentPaneSessionV2(handle)
            replacement._stop_all()
            self.wait_for(lambda: not handle.ui_stop_pending(resource))
            self.assertTrue(handle.can_close())
        finally:
            if replacement is not None:
                error = fixture._dispose(product.graph, product.pool, handle, replacement)
                if error is not None:
                    raise error


if __name__ == "__main__":
    unittest.main()
