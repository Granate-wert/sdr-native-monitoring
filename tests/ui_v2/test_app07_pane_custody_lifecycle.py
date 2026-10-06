"""Per-scene custody clear and Stop boundaries preserve peer isolation."""
from __future__ import annotations

from types import SimpleNamespace
from concurrent.futures import Future
import gc
import unittest
import os
import weakref
from unittest.mock import patch

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from shiboken6 import isValid as is_qobject_valid
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
from sdr_monitor.ui.v2_pane_presentation import PreparedPaneDelivery
from tests.test_app07_pane_delivery_obligations import identity
from tests.ui_v2.test_app05_viewport_projection import ManualWorker
from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector
from sdr_monitor.ui.v2.workspaces.independent_pane_delivery import IndependentPaneDeliveryPort
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2


class _StopBoundaryHarness(QObject):
    stop_boundary = Signal(object)

    def __init__(self, scene, ledger):
        super().__init__()
        self.handle = SimpleNamespace(
            session=SimpleNamespace(pane_delivery_ledger_snapshot=ledger.snapshot),
            layout=SimpleNamespace(slots=(SimpleNamespace(request=object(), number=1),)))
        self.board = SimpleNamespace(pane=lambda _number: SimpleNamespace(spectrum_scene=scene))
        self.stop_boundary.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def _deliver(self, payload):
        IndependentPaneSessionV2._on_stop_boundary(self, payload)

    def watch(self, future, resource="resource"):
        IndependentPaneSessionV2._watch_stop_boundary(self, future, resource)


class _DeliveryPortHarness(QObject):
    rendered = Signal(str)
    render_failed = Signal(str, str)

    def __init__(self, board, queue):
        super().__init__()
        self._board = board
        self._queue = queue
        self._failed_panes = set()

    def _report(self, packet, stage):
        callback = getattr(self._board, "_stage_callback", None)
        if callback is not None:
            callback(packet.delivery.obligation_ref, stage)

    def _reject_uncommitted(self, packet):
        IndependentPaneDeliveryPort._reject_uncommitted(self, packet)


class PaneCustodyLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _owned_scene(self, *, register_cleanup=True):
        scene = SpectrumScene()
        if register_cleanup:
            # LIFO gives release -> close -> targeted DeferredDelete.  Each
            # callback remains independently reportable if an earlier one
            # raises, while unittest still runs the later callbacks.
            self.addCleanup(self._delete_scene, scene)
            self.addCleanup(self._close_scene, scene)
            self.addCleanup(self._release_scene, scene)
        return scene

    def _release_scene(self, scene) -> None:
        if not is_qobject_valid(scene):
            return
        if scene._graphics_terminal_released:
            return
        scene.clear_measurement()
        scene.release_graphics_after_shutdown()

    @staticmethod
    def _close_scene(scene) -> None:
        if not is_qobject_valid(scene):
            return
        scene.close()

    def _delete_scene(self, scene) -> None:
        if not is_qobject_valid(scene):
            return
        scene.deleteLater()
        QCoreApplication.sendPostedEvents(scene, QEvent.Type.DeferredDelete)
        self.assertFalse(is_qobject_valid(scene))

    def _run_registered_scene_cleanups(self, scene) -> None:
        """Exercise unittest's cleanup order while retaining the first error."""
        errors = []
        for cleanup in (self._release_scene, self._close_scene, self._delete_scene):
            try:
                cleanup(scene)
            except BaseException as exc:  # preserve cleanup errors for the assertion
                errors.append(exc)
        if errors:
            raise errors[0]

    @staticmethod
    def ui_admitted(ledger, offer=1):
        ref = ledger.admit("one", identity(offer=offer))
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                      Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
            assert ledger.note(ref, stage)
        return ref

    @staticmethod
    def frame(sequence, value):
        frequencies = np.linspace(100.0, 200.0, 8192, dtype=np.float64)
        values = np.full(8192, value, dtype=np.float32)
        frequencies.setflags(write=False)
        values.setflags(write=False)
        return SimpleNamespace(source_id="source", receiver="rx", session="session",
            epoch=1, config_generation=1, sequence=sequence, unit="dBm",
            frequencies_hz=frequencies, values=values)

    @staticmethod
    def packet(ref):
        packet = object.__new__(PreparedPaneDelivery)
        object.__setattr__(packet, "delivery", SimpleNamespace(
            pane_id="one", capture_id="capture", obligation_ref=ref,
            host_activation_serial=1,
            bundle=SimpleNamespace(terminal_sweep=False, acquisition_epoch=1,
                publication_kind="preview", spectrum=SimpleNamespace(sequence=ref.sequence, revision=0))))
        object.__setattr__(packet, "binding", SimpleNamespace(slot_number=1))
        return packet

    def test_confirmed_stop_and_actual_clear_are_pane_local(self):
        first_ref = PaneDeliveryLedger(("one",)).admit("one", identity())
        peer_ref = PaneDeliveryLedger(("two",)).admit("two", identity("two"))
        first_stages = []
        peer_stages = []
        first = self._owned_scene()
        peer = self._owned_scene()
        first.set_delivery_stage_callback(lambda ref, stage: first_stages.append((ref, stage)))
        peer.set_delivery_stage_callback(lambda ref, stage: peer_stages.append((ref, stage)))
        retained = SimpleNamespace(source_id="source", epoch=1, sequence=1, unit="dBm",
            frequencies_hz=np.array([100.0, 101.0]), values=np.array([-80.0, -70.0]))
        first.set_frame(retained, obligation_ref=first_ref)
        peer.set_frame(SimpleNamespace(source_id="peer", epoch=1, sequence=1, unit="dBm",
            frequencies_hz=np.array([100.0, 101.0]), values=np.array([-90.0, -75.0])), obligation_ref=peer_ref)

        first.stop_delivery_custody()
        self.assertEqual(first_stages[-1], (first_ref, Stage.STOP_CLEARED))
        self.assertIs(first.latest_frame, retained)
        self.assertEqual(peer_stages[-1], (peer_ref, Stage.PAINT_SCHEDULED))
        self.assertEqual(peer.latest_frame.sequence, 1)

        peer.clear_measurement()
        self.assertEqual(peer_stages[-1], (peer_ref, Stage.PAINT_SUPERSEDED))
        self.assertIsNone(peer.latest_frame)
        first.close()
        peer.close()
        self.app.processEvents()

    def test_stop_retains_pixels_but_terminal_scene_release_drops_source_arrays(self):
        ledger = PaneDeliveryLedger(("one",))
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(ledger.note)
        ref = self.ui_admitted(ledger)
        frame = self.frame(1, -70)
        frequencies_ref = weakref.ref(frame.frequencies_hz)
        values_ref = weakref.ref(frame.values)
        scene.set_frame(frame, obligation_ref=ref)

        # The dynamically-created paint method may be process-lived, so it
        # must not close over scene-bound delivery callbacks.
        paint_method = type(scene._graphics).paintEvent
        closure_values = (() if paint_method.__closure__ is None else
                          tuple(cell.cell_contents for cell in paint_method.__closure__))
        self.assertFalse(any(getattr(value, "__self__", None) is scene
                             for value in closure_values))

        scene.stop_delivery_custody((ref,))
        self.assertIs(scene.latest_frame, frame)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        self.assertIsNotNone(frequencies_ref())
        self.assertIsNotNone(values_ref())

        scene.clear_measurement()
        scene.release_graphics_after_shutdown()
        frame = None
        gc.collect()
        self.assertIsNone(scene.latest_frame)
        self.assertEqual(scene._projection_delivery_slots, ())
        self.assertIsNone(frequencies_ref())
        self.assertIsNone(values_ref())
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        scene.close()
        self.app.processEvents()

    def test_cleanup_failure_is_reported_after_owned_scene_is_deleted(self):
        cases = ("clear", "release", "close")
        for failure in cases:
            with self.subTest(failure=failure):
                scene = self._owned_scene()
                if failure == "clear":
                    failure_patch = patch.object(
                        scene, "clear_measurement",
                        side_effect=RuntimeError("clear cleanup failure"))
                    expected = "clear cleanup failure"
                elif failure == "release":
                    original_release = scene.release_graphics_after_shutdown

                    def release_then_fail():
                        original_release()
                        raise RuntimeError("release cleanup failure")

                    failure_patch = patch.object(
                        scene, "release_graphics_after_shutdown",
                        side_effect=release_then_fail)
                    expected = "release cleanup failure"
                else:
                    failure_patch = patch.object(
                        self, "_close_scene",
                        side_effect=RuntimeError("close cleanup failure"))
                    expected = "close cleanup failure"

                with failure_patch:
                    with self.assertRaisesRegex(RuntimeError, expected):
                        self._run_registered_scene_cleanups(scene)
                self.assertFalse(is_qobject_valid(scene))

    def test_hidden_latest_replacement_and_captured_stop_refs_conserve_real_ledger(self):
        ledger = PaneDeliveryLedger(("one",))
        stages = []
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: (stages.append((ref, stage)), ledger.note(ref, stage)))
        scene.set_presentation_active(False)
        first = self.ui_admitted(ledger, 1)
        second = self.ui_admitted(ledger, 2)
        scene.set_frame(self.frame(1, -70), obligation_ref=first)
        scene.set_frame(self.frame(2, -60), obligation_ref=second)
        captured_at_stop = tuple(record.ref for record in ledger.snapshot().records
            if record.ref.identity.physical_stream_resource_id == "resource"
            and record.stage in {Stage.UI_ADMITTED, Stage.PAINT_SCHEDULED})
        self.assertIn((first, Stage.PAINT_SUPERSEDED), stages)
        third = self.ui_admitted(ledger, 3)
        scene.set_frame(self.frame(3, -50), obligation_ref=third)
        scene.stop_delivery_custody(captured_at_stop)
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.panes[0].pending, 1)
        self.assertEqual(snapshot.panes[0].terminal, 2)
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(scene._latest_delivery_ref, third)
        scene.close()
        self.app.processEvents()

    def test_projector_keeps_exact_active_and_latest_original_refs(self):
        ledger = PaneDeliveryLedger(("one",))
        worker = ManualWorker()
        projector = SpectrumProjector(worker.submit)
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        scene.plot_item.getAxis("left").setWidth(80)
        scene.set_projection_port(projector)
        scene.resize(1000, 600)
        scene.show()
        for _ in range(8):
            self.app.processEvents()
        first = self.ui_admitted(ledger, 1)
        second = self.ui_admitted(ledger, 2)
        frame1, frame2 = self.frame(1, -70), self.frame(2, -60)
        scene.set_frame(frame1, obligation_ref=first)
        for _ in range(8):
            self.app.processEvents()
        scene.set_frame(frame2, obligation_ref=second)
        for _ in range(8):
            self.app.processEvents()
        self.assertEqual(len(worker.jobs), 1)
        self.assertEqual({ref for _source, ref in scene._projection_delivery_slots}, {first, second})

        for _ in range(8):
            self.app.processEvents()
        worker.finish()
        for _ in range(8):
            self.app.processEvents()
        self.assertEqual(scene.displayed_delivery_ref, first)
        first_events = [event.stage for event in ledger.snapshot().events if event.ref == first]
        self.assertLess(first_events.index(Stage.PAINT_SCHEDULED), first_events.index(Stage.PAINT_RETURNED))
        self.assertEqual(len(worker.jobs), 1)
        worker.finish()
        for _ in range(12):
            self.app.processEvents()
        records = {record.ref: record.stage for record in ledger.snapshot().records}
        self.assertEqual(records[first], Stage.PAINT_RETURNED)
        self.assertIn(records[second], {Stage.PAINT_SCHEDULED, Stage.PAINT_RETURNED})
        self.assertEqual(scene.displayed_delivery_ref, second)
        scene.stop_delivery_custody((second,))
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.panes[0].pending, 0)
        self.assertEqual(snapshot.panes[0].terminal, 2)
        self.assertEqual(snapshot.accounting_failures, 0)
        projector.dispose()
        scene.close()
        scene.deleteLater()
        self.app.processEvents()

    def test_port_rejection_after_exact_commit_is_inert_and_terminal_ref_does_not_resurrect(self):
        ledger = PaneDeliveryLedger(("one",))
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        accepted = self.ui_admitted(ledger, 1)
        frame = self.frame(1, -70)
        scene.set_frame(frame, obligation_ref=accepted)
        observed = [event.stage for event in ledger.snapshot().events if event.ref == accepted]
        self.assertIn(Stage.PAINT_SCHEDULED, observed)
        # Exercise the actual port branch used when later pane-layer work
        # raises. It must retain the ledger's committed exact Spectrum ref.
        port = SimpleNamespace(
            _board=SimpleNamespace(pane=lambda _slot: SimpleNamespace(spectrum_scene=scene)),
            _report=lambda _prepared, stage: ledger.note(accepted, stage))
        prepared = SimpleNamespace(delivery=SimpleNamespace(obligation_ref=accepted),
                                   binding=SimpleNamespace(slot_number=1))
        IndependentPaneDeliveryPort._reject_uncommitted(port, prepared)
        self.assertEqual({record.ref: record.stage for record in ledger.snapshot().records}[accepted],
                         Stage.PAINT_SCHEDULED)
        scene.stop_delivery_custody((accepted,))
        self.assertFalse(scene.delivery_requires_ui_rejection(accepted))
        scene.set_frame(frame, obligation_ref=accepted)
        self.assertIsNone(scene._latest_delivery_ref)
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.panes[0].terminal, 1)
        self.assertEqual(snapshot.accounting_failures, 0)
        scene.close()
        self.app.processEvents()

    def test_port_reports_precommit_failure_and_scene_blocks_same_ref_retry(self):
        ledger = PaneDeliveryLedger(("one",))
        rejected = self.ui_admitted(ledger, 1)
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        prepared = SimpleNamespace(delivery=SimpleNamespace(obligation_ref=rejected),
                                   binding=SimpleNamespace(slot_number=1))
        port = SimpleNamespace(
            _board=SimpleNamespace(pane=lambda _slot: SimpleNamespace(spectrum_scene=scene)),
            _report=lambda _prepared, stage: ledger.note(rejected, stage))
        with patch.object(scene, "_set_trace_view", side_effect=RuntimeError("precommit")):
            with self.assertRaisesRegex(RuntimeError, "precommit"):
                scene.set_frame(self.frame(1, -70), obligation_ref=rejected)
            IndependentPaneDeliveryPort._reject_uncommitted(port, prepared)
        scene.set_frame(self.frame(1, -70), obligation_ref=rejected)
        self.assertIsNone(scene._latest_delivery_ref)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        scene.close()
        self.app.processEvents()

    def test_precommit_rejection_is_terminal_and_cannot_be_reattached(self):
        ledger = PaneDeliveryLedger(("one",))
        rejected = self.ui_admitted(ledger, 1)
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        frame = self.frame(1, -70)
        with patch.object(scene, "_set_trace_view", side_effect=RuntimeError("precommit")):
            with self.assertRaisesRegex(RuntimeError, "precommit"):
                scene.set_frame(frame, obligation_ref=rejected)
        self.assertFalse(scene.delivery_requires_ui_rejection(rejected))
        stages = [event.stage for event in ledger.snapshot().events if event.ref == rejected]
        self.assertEqual(stages[-1], Stage.UI_REJECTED)
        scene.set_frame(frame, obligation_ref=rejected)
        self.assertIsNone(scene._latest_delivery_ref)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        scene.close()
        self.app.processEvents()

    def test_stop_future_uses_cached_snapshot_queued_exact_refs_and_ignores_failure(self):
        ledger = PaneDeliveryLedger(("one", "two"))
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        first = self.ui_admitted(ledger, 1)
        scene.set_frame(self.frame(1, -70), obligation_ref=first)
        peer = ledger.admit("two", identity("two", 1, physical_stream_resource_id="peer"))
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                      Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
            self.assertTrue(ledger.note(peer, stage))
        harness = _StopBoundaryHarness(scene, ledger)

        successful_stop = Future()
        harness.watch(successful_stop)
        successful_stop.set_result(None)
        # The future callback snapshots and queues exact refs, but does not
        # mutate QWidget custody off the Qt thread.
        self.assertEqual(scene._latest_delivery_ref, first)
        self.app.processEvents()
        stages = {record.ref: record.stage for record in ledger.snapshot().records}
        self.assertEqual(stages[first], Stage.STOP_CLEARED)
        self.assertEqual(stages[peer], Stage.UI_ADMITTED)
        self.assertEqual(scene.latest_frame.sequence, 1)

        failed_ref = self.ui_admitted(ledger, 2)
        scene.set_frame(self.frame(2, -60), obligation_ref=failed_ref)
        failed_stop = Future()
        harness.watch(failed_stop)
        failed_stop.set_exception(RuntimeError("Stop failed"))
        self.app.processEvents()
        self.assertEqual(scene._latest_delivery_ref, failed_ref)
        stages = {record.ref: record.stage for record in ledger.snapshot().records}
        self.assertEqual(stages[failed_ref], Stage.PAINT_SCHEDULED)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        scene.close()
        self.app.processEvents()

    def test_delayed_successful_stop_cannot_detach_new_run_and_deleted_widget_is_inert(self):
        ledger = PaneDeliveryLedger(("one",))
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        old = self.ui_admitted(ledger, 1)
        scene.set_frame(self.frame(1, -70), obligation_ref=old)
        harness = _StopBoundaryHarness(scene, ledger)
        delayed = Future()
        harness.watch(delayed)
        delayed.set_result(None)  # captures only old while still GUI-queued

        new = ledger.admit("one", identity(offer=2, host_run_serial=2, host_activation_serial=2))
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                      Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
            self.assertTrue(ledger.note(new, stage))
        scene.set_frame(self.frame(2, -60), obligation_ref=new)
        self.app.processEvents()
        self.assertEqual(scene._latest_delivery_ref, new)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)

        doomed = _StopBoundaryHarness(scene, ledger)
        late = Future()
        doomed.watch(late)
        doomed.deleteLater()
        self.app.sendPostedEvents(doomed, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        late.set_result(None)  # deleted signal emission is telemetry-only
        self.assertEqual(scene._latest_delivery_ref, new)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        scene.close()
        self.app.processEvents()

    def test_queue_drained_stop_barrier_closes_before_async_projection_can_paint(self):
        ledger = PaneDeliveryLedger(("one",))
        worker = ManualWorker()
        projector = SpectrumProjector(worker.submit)
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        scene.set_projection_port(projector)
        scene.resize(900, 500)
        scene.show()
        for _ in range(8):
            self.app.processEvents()
        ref = ledger.admit("one", identity())
        self.assertTrue(ledger.note(ref, Stage.PREPARING))
        self.assertTrue(ledger.note(ref, Stage.PREPARED))
        queue = PaneFairDeliveryQueue(("one",), stage_callback=ledger.note)
        packet = self.packet(ref)
        self.assertTrue(queue.offer(packet))
        stop_harness = _StopBoundaryHarness(scene, ledger)
        stop_future = Future()
        stop_harness.watch(stop_future)

        class DrainBarrier:
            pane_ids = queue.pane_ids

            def drain(_self, *, max_items):
                batch = queue.drain(max_items=max_items)
                # Worker Stop succeeds after queue drain but before this Qt
                # turn can call board.apply_prepared.
                stop_future.set_result(None)
                return batch

            def clear(_self, pane_id):
                queue.clear(pane_id)

        board = SimpleNamespace(
            _stage_callback=ledger.note,
            pane=lambda _slot: SimpleNamespace(spectrum_scene=scene),
            apply_prepared=lambda prepared: (
                ledger.note(ref, Stage.UI_ADMITTED),
                scene.set_frame(self.frame(1, -70), obligation_ref=prepared.delivery.obligation_ref),
                True)[-1])
        port = _DeliveryPortHarness(board, DrainBarrier())
        IndependentPaneDeliveryPort.tick_once(port)
        self.assertEqual(ledger.snapshot().records[0].stage, Stage.UI_ADMITTED)
        scene.commit_projection()
        self.assertEqual(len(worker.jobs), 1)
        self.app.sendPostedEvents(stop_harness, QEvent.Type.MetaCall)
        self.assertEqual(ledger.snapshot().records[0].stage, Stage.STOP_CLEARED)
        worker.finish()
        for _ in range(12):
            self.app.processEvents()
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.panes[0].pending, 0)
        self.assertEqual(snapshot.panes[0].terminal, 1)
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertNotIn(Stage.PAINT_SCHEDULED, [event.stage for event in snapshot.events if event.ref == ref])
        self.assertNotIn(Stage.PAINT_RETURNED, [event.stage for event in snapshot.events if event.ref == ref])
        projector.dispose()
        scene.close()
        scene.deleteLater()
        self.app.processEvents()

    def test_async_spectrum_setter_success_survives_later_waterfall_failure(self):
        ledger = PaneDeliveryLedger(("one",))
        worker = ManualWorker()
        projector = SpectrumProjector(worker.submit)
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        scene.set_projection_port(projector)
        scene.resize(900, 500)
        scene.show()
        for _ in range(8):
            self.app.processEvents()
        ref = ledger.admit("one", identity())
        for stage in (Stage.PREPARING, Stage.PREPARED):
            self.assertTrue(ledger.note(ref, stage))
        queue = PaneFairDeliveryQueue(("one",), stage_callback=ledger.note)
        packet = self.packet(ref)
        self.assertTrue(queue.offer(packet))

        def apply_then_waterfall_failure(prepared):
            self.assertTrue(ledger.note(ref, Stage.UI_ADMITTED))
            scene.set_frame(self.frame(1, -70), obligation_ref=prepared.delivery.obligation_ref)
            raise RuntimeError("later Waterfall update failed")

        board = SimpleNamespace(
            _stage_callback=ledger.note,
            pane=lambda _slot: SimpleNamespace(spectrum_scene=scene),
            apply_prepared=apply_then_waterfall_failure)
        port = _DeliveryPortHarness(board, queue)
        IndependentPaneDeliveryPort.tick_once(port)
        self.assertTrue(scene._latest_spectrum_setter_accepted)
        scene.commit_projection()
        for _ in range(12):
            self.app.processEvents()
            if worker.jobs:
                worker.finish()
        for _ in range(8):
            self.app.processEvents()
        snapshot = ledger.snapshot()
        record_stage = {record.ref: record.stage for record in snapshot.records}[ref]
        self.assertIn(record_stage, {Stage.PAINT_SCHEDULED, Stage.PAINT_RETURNED},
            (snapshot.events, scene.projection_stale, scene._projection_error, projector.completed))
        self.assertNotIn(Stage.UI_REJECTED, [event.stage for event in snapshot.events if event.ref == ref])
        self.assertEqual(snapshot.accounting_failures, 0)
        projector.dispose()
        scene.close()
        scene.deleteLater()
        self.app.processEvents()

    def test_async_spectrum_setter_throw_cancels_ref_before_current_commit_and_retry(self):
        ledger = PaneDeliveryLedger(("one",))
        worker = ManualWorker()
        projector = SpectrumProjector(worker.submit)
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        scene.set_projection_port(projector)
        scene.resize(900, 500)
        scene.show()
        ref = ledger.admit("one", identity())
        for stage in (Stage.PREPARING, Stage.PREPARED):
            self.assertTrue(ledger.note(ref, stage))
        queue = PaneFairDeliveryQueue(("one",), stage_callback=ledger.note)
        packet = self.packet(ref)
        self.assertTrue(queue.offer(packet))
        frame = self.frame(1, -70)
        original_setter = scene._set_trace_view

        def schedule_then_throw(kind, view):
            original_setter(kind, view)
            raise RuntimeError("Spectrum setter failed before CURRENT setData")

        def apply_failed_spectrum(prepared):
            self.assertTrue(ledger.note(ref, Stage.UI_ADMITTED))
            scene.set_frame(frame, obligation_ref=prepared.delivery.obligation_ref)
            return True

        board = SimpleNamespace(
            _stage_callback=ledger.note,
            pane=lambda _slot: SimpleNamespace(spectrum_scene=scene),
            apply_prepared=apply_failed_spectrum)
        port = _DeliveryPortHarness(board, queue)
        with patch.object(scene, "_set_trace_view", side_effect=schedule_then_throw):
            IndependentPaneDeliveryPort.tick_once(port)
        snapshot = ledger.snapshot()
        self.assertEqual({record.ref: record.stage for record in snapshot.records}[ref], Stage.UI_REJECTED)
        self.assertIsNone(scene._latest_delivery_ref)
        self.assertFalse(scene._latest_spectrum_setter_accepted)
        scene.set_presentation_active(False)
        scene.set_presentation_active(True)
        scene.set_frame(frame, obligation_ref=ref)
        scene.commit_projection()
        for _ in range(8):
            self.app.processEvents()
        if worker.jobs:
            worker.finish()
            for _ in range(12):
                self.app.processEvents()
        stages = [event.stage for event in ledger.snapshot().events if event.ref == ref]
        self.assertEqual(stages[-1], Stage.UI_REJECTED)
        self.assertNotIn(Stage.PAINT_SCHEDULED, stages)
        self.assertNotIn(Stage.PAINT_RETURNED, stages)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        projector.dispose()
        scene.close()
        scene.deleteLater()
        self.app.processEvents()

    def test_scene_defensive_failed_replacement_restores_prior_acceptance(self):
        """Scene-level defensive invariant; the production port latches failures."""
        ledger = PaneDeliveryLedger(("one",))
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        scene.set_presentation_active(False)
        accepted_a = self.ui_admitted(ledger, 1)
        failed_b = self.ui_admitted(ledger, 2)
        replacement_c = self.ui_admitted(ledger, 3)

        scene.set_frame(self.frame(1, -70), obligation_ref=accepted_a)
        self.assertTrue(scene._latest_spectrum_setter_accepted)
        with patch.object(scene, "_set_trace_view", side_effect=RuntimeError("B setter failure")):
            with self.assertRaisesRegex(RuntimeError, "B setter failure"):
                scene.set_frame(self.frame(2, -60), obligation_ref=failed_b)

        self.assertEqual(scene._latest_delivery_ref, accepted_a)
        self.assertTrue(scene._latest_spectrum_setter_accepted)
        scene.set_frame(self.frame(3, -50), obligation_ref=replacement_c)
        stages = {record.ref: record.stage for record in ledger.snapshot().records}
        self.assertEqual(stages[accepted_a], Stage.PAINT_SUPERSEDED)
        self.assertEqual(stages[failed_b], Stage.UI_REJECTED)
        self.assertEqual(stages[replacement_c], Stage.UI_ADMITTED)
        self.assertEqual(ledger.snapshot().panes[0].pending, 1)
        self.assertEqual(ledger.snapshot().panes[0].terminal, 2)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        self.assertEqual(scene._latest_delivery_ref, replacement_c)
        self.assertTrue(scene._latest_spectrum_setter_accepted)
        scene.close()
        self.app.processEvents()

    def test_current_setdata_witness_survives_later_spectrum_chrome_failure(self):
        ledger = PaneDeliveryLedger(("one",))
        worker = ManualWorker()
        projector = SpectrumProjector(worker.submit)
        scene = self._owned_scene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        scene.set_projection_port(projector)
        scene.resize(900, 500)
        scene.show()
        for _ in range(8):
            self.app.processEvents()
        ref = self.ui_admitted(ledger, 1)
        frame = self.frame(1, -70)
        scene.set_frame(frame, obligation_ref=ref)
        scene.set_trace(TraceKind.AVERAGE, frame)
        scene.commit_projection()
        self.assertEqual(len(worker.jobs), 1)
        _future, operation = worker.jobs[0]
        result = operation()
        original_paint_trace = scene._paint_trace

        def fail_after_current_setdata(kind, view, envelope):
            original_paint_trace(kind, view, envelope)
            if kind is TraceKind.CURRENT:
                raise RuntimeError("Spectrum chrome failed after CURRENT setData")

        with patch.object(scene, "_paint_trace", side_effect=fail_after_current_setdata):
            with self.assertRaisesRegex(RuntimeError, "after CURRENT setData"):
                scene._accept_projection(result)
        self.assertIs(scene.displayed_frame, frame)
        self.assertEqual(scene.displayed_delivery_ref, ref)
        port = SimpleNamespace(
            _board=SimpleNamespace(pane=lambda _slot: SimpleNamespace(spectrum_scene=scene)),
            _report=lambda _prepared, stage: ledger.note(ref, stage))
        prepared = SimpleNamespace(delivery=SimpleNamespace(obligation_ref=ref),
                                   binding=SimpleNamespace(slot_number=1))
        IndependentPaneDeliveryPort._reject_uncommitted(port, prepared)
        events = [event.stage for event in ledger.snapshot().events if event.ref == ref]
        self.assertIn(Stage.PAINT_SCHEDULED, events)
        self.assertNotIn(Stage.UI_REJECTED, events)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        projector.dispose()
        scene.close()
        scene.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
