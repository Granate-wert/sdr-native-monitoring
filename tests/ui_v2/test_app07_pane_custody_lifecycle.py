"""Per-scene custody clear and Stop boundaries preserve peer isolation."""
from __future__ import annotations

from types import SimpleNamespace
from concurrent.futures import Future
import unittest
import os
from unittest.mock import patch

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
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


class PaneCustodyLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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

    def test_confirmed_stop_and_actual_clear_are_pane_local(self):
        first_ref = PaneDeliveryLedger(("one",)).admit("one", identity())
        peer_ref = PaneDeliveryLedger(("two",)).admit("two", identity("two"))
        first_stages = []
        peer_stages = []
        first = SpectrumScene()
        peer = SpectrumScene()
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

    def test_hidden_latest_replacement_and_captured_stop_refs_conserve_real_ledger(self):
        ledger = PaneDeliveryLedger(("one",))
        stages = []
        scene = SpectrumScene()
        scene.set_delivery_stage_callback(lambda ref, stage: (stages.append((ref, stage)), ledger.note(ref, stage)))
        scene.set_presentation_active(False)
        first = self.ui_admitted(ledger, 1)
        second = self.ui_admitted(ledger, 2)
        scene.set_frame(self.frame(1, -70), obligation_ref=first)
        scene.set_frame(self.frame(2, -60), obligation_ref=second)
        captured_at_stop = tuple(record.ref for record in ledger.snapshot().records
            if record.ref.identity.physical_stream_resource_id == "resource"
            and record.stage in {Stage.UI_ADMITTED, Stage.PAINT_SCHEDULED})
        self.assertIn((first, Stage.UI_REJECTED), stages)
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
        scene = SpectrumScene()
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
        scene = SpectrumScene()
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
        scene = SpectrumScene()
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
        scene = SpectrumScene()
        scene.set_delivery_stage_callback(lambda ref, stage: ledger.note(ref, stage))
        frame = self.frame(1, -70)
        with patch.object(scene, "_set_trace_view", side_effect=RuntimeError("precommit")):
            with self.assertRaisesRegex(RuntimeError, "precommit"):
                scene.set_frame(frame, obligation_ref=rejected)
        self.assertTrue(scene.delivery_requires_ui_rejection(rejected))
        self.assertTrue(ledger.note(rejected, Stage.UI_REJECTED))
        scene.set_frame(frame, obligation_ref=rejected)
        self.assertIsNone(scene._latest_delivery_ref)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        scene.close()
        self.app.processEvents()

    def test_stop_future_uses_cached_snapshot_queued_exact_refs_and_ignores_failure(self):
        ledger = PaneDeliveryLedger(("one", "two"))
        scene = SpectrumScene()
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
        scene = SpectrumScene()
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


if __name__ == "__main__":
    unittest.main()
