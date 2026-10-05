"""Original-reference custody across the bounded UI handoff."""
from __future__ import annotations

from types import SimpleNamespace
import unittest
from threading import Condition

from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.ui.v2_pane_presentation import PreparedPaneDelivery
from sdr_monitor.ui.v2_pane_runtime import PanePumpResourceState, _ResourceWorker
from tests.test_app07_pane_delivery_obligations import identity


def prepared(ref, sequence):
    packet = object.__new__(PreparedPaneDelivery)
    bundle = SimpleNamespace(acquisition_epoch=1, publication_kind="preview",
                             spectrum=SimpleNamespace(sequence=sequence))
    object.__setattr__(packet, "delivery", SimpleNamespace(
        pane_id="one", host_activation_serial=1, obligation_ref=ref, bundle=bundle))
    return packet


class PaneCustodyQueueTests(unittest.TestCase):
    def test_worker_reports_original_ref_across_prepare_before_queue(self):
        ref = PaneDeliveryLedger(("one",)).admit("one", identity())
        events = []
        delivery = SimpleNamespace(obligation_ref=ref, capture_id="capture",
                                   bundle=SimpleNamespace(terminal_sweep=False))
        prepared = SimpleNamespace(delivery=delivery)
        worker = object.__new__(_ResourceWorker)
        worker.resource = SimpleNamespace(physical_stream_resource_id="resource", slots=(object(),))
        worker.session = SimpleNamespace(poll_resource=lambda _resource: (delivery,))
        worker.preparer = SimpleNamespace(prepare=lambda item: prepared)
        worker.queue = SimpleNamespace(offer=lambda item: events.append(("offer", item)))
        worker.stage_callback = lambda actual, stage: events.append((actual, stage))
        worker._condition = Condition()
        worker._state = PanePumpResourceState("resource")

        worker._poll_and_advance()

        self.assertEqual(events[:2], [(ref, Stage.PREPARING), (ref, Stage.PREPARED)])
        self.assertEqual(events[2], ("offer", prepared))

    def test_worker_none_ref_stays_legacy_inert(self):
        stages = []
        delivery = SimpleNamespace(obligation_ref=None, capture_id="capture",
                                   bundle=SimpleNamespace(terminal_sweep=False))
        prepared = SimpleNamespace(delivery=delivery)
        worker = object.__new__(_ResourceWorker)
        worker.resource = SimpleNamespace(physical_stream_resource_id="resource", slots=(object(),))
        worker.session = SimpleNamespace(poll_resource=lambda _resource: (delivery,))
        worker.preparer = SimpleNamespace(prepare=lambda item: prepared)
        worker.queue = SimpleNamespace(offer=lambda item: None)
        worker.stage_callback = lambda actual, stage: stages.append((actual, stage))
        worker._condition = Condition()
        worker._state = PanePumpResourceState("resource")
        worker._poll_and_advance()
        self.assertEqual(stages, [])

    def test_original_ref_supersession_drain_clear_and_callback_failure(self):
        ledger = PaneDeliveryLedger(("one",))
        first = ledger.admit("one", identity())
        second = ledger.admit("one", identity(offer=2))
        events = []

        def prepared_stage(ref):
            self.assertTrue(ledger.note(ref, Stage.PREPARING))
            self.assertTrue(ledger.note(ref, Stage.PREPARED))

        prepared_stage(first)
        prepared_stage(second)
        third = None

        def record(ref, stage):
            self.assertIn(ref, (first, second, third))
            events.append((ref, stage))
            ledger.note(ref, stage)
            if stage is Stage.QUEUED:
                raise RuntimeError("telemetry only")

        queue = PaneFairDeliveryQueue(("one",), stage_callback=record)
        self.assertTrue(queue.offer(prepared(first, 1)))
        self.assertTrue(queue.offer(prepared(second, 2)))
        self.assertEqual(events, [(first, Stage.QUEUED),
                                  (first, Stage.QUEUE_SUPERSEDED),
                                  (second, Stage.QUEUED)])
        self.assertIs(queue.drain()[0].delivery.obligation_ref, second)
        self.assertEqual(events[-1], (second, Stage.QUEUE_DRAINED))
        self.assertTrue(ledger.note(second, Stage.UI_ADMITTED))
        self.assertTrue(ledger.note(second, Stage.UI_REJECTED))
        third = ledger.admit("one", identity(offer=3))
        prepared_stage(third)
        queue.offer(prepared(third, 3))
        queue.clear("one")
        self.assertEqual(events[-1], (third, Stage.STOP_CLEARED))
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 0)
        self.assertEqual(snapshot.panes[0].terminal, 3)

    def test_legacy_none_reference_remains_inert(self):
        stages = []
        queue = PaneFairDeliveryQueue(("one",), stage_callback=lambda ref, stage: stages.append((ref, stage)))
        self.assertTrue(queue.offer(prepared(None, 1)))
        queue.drain()
        queue.clear()
        self.assertEqual(stages, [])


if __name__ == "__main__":
    unittest.main()
