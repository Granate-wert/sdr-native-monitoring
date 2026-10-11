"""Original-reference custody across the bounded UI handoff."""
from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, RtbwFrameMetadata
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneLayout, PaneLayoutSlot, compile_pane_schedule
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from sdr_monitor.services.pane_resource_session import PaneDelivery
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer, PreparedPaneDelivery
from sdr_monitor.ui.v2_pane_runtime import _ResourceWorker
from tests.test_app07_pane_delivery_obligations import identity
from tests.test_app07_shared_capture_schedule import group, pane, profile


def prepared(ref, sequence):
    packet = object.__new__(PreparedPaneDelivery)
    bundle = SimpleNamespace(acquisition_epoch=1, publication_kind="preview",
                             spectrum=SimpleNamespace(sequence=sequence))
    object.__setattr__(packet, "delivery", SimpleNamespace(
        pane_id="one", host_activation_serial=1, obligation_ref=ref, bundle=bundle))
    return packet


def worker_fixture(*, refs=True, during_prepare=None):
    """Real constructor and typed single input; no worker thread or SDK owner.

    Real preparation creates the returned packet. Inert adapters isolate the
    worker's same-ref custody boundaries and permit finite control injection.
    """
    groups = (group("resource", "rx"),)
    request = pane("one", "rx", 134_000_000.0, 154_000_000.0,
                   ReceiverBindingMode.DEDICATED_PARALLEL)
    capture_profile = replace(profile(20_000_000.0, CaptureMeasurementMode.RTBW),
                              sample_rate_hz=20_000_000.0, analog_bandwidth_hz=20_000_000.0)
    schedule = compile_pane_schedule(groups, (request,), {"capture": capture_profile})
    layout = PaneLayout((PaneLayoutSlot(1, request),), schedule)
    preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget())
    binding = preparer.bindings["one"]
    original = identity()
    scope = replace(original.owner_scope, source_id=binding.source_id, receiver_id=None)
    ready = replace(original.ready, source_id=binding.source_id, receiver_id=None)
    analytical = replace(original, owner_scope=scope, ready=ready, capture_id=binding.capture_id)
    ledger = PaneDeliveryLedger(("one",))
    ref = ledger.admit("one", analytical) if refs else None
    if refs and ref is None:
        raise AssertionError("fixture did not admit original Spectrum custody")
    grid = 144_000_000.0 + (np.arange(4096, dtype=np.float64) - 2048) * (20_000_000.0 / 4096)
    values = np.full(4096, -85.0, dtype=np.float32)
    grid.setflags(write=False)
    values.setflags(write=False)
    spectrum = LiveSpectrumFrame(1, 10_000_000, 144_000_000.0, 20_000_000.0, 4096, 2048,
        grid, values, source_id=scope.source_id, config_generation=scope.configuration_generation,
        acquisition_epoch=scope.acquisition_epoch, accumulation_id=scope.session_id,
        detector_ready=ready if refs else None)
    bundle = AnalyzerFrameBundle(spectrum, scope.session_id, None, scope.acquisition_epoch,
                                RtbwFrameMetadata(144_000_000.0, 20_000_000.0, 4096, 2048))
    delivery = PaneDelivery("resource", binding.capture_id, "rx", 1, binding.crop,
                           bundle, 0.0, 1, analytical if refs else None, ref)
    packet = preparer.prepare(delivery)
    events, prepare_calls, offered = [], [], []

    def prepare(item):
        if item is not delivery:
            raise AssertionError("worker prepared a different input")
        prepare_calls.append(item)
        if during_prepare is not None:
            during_prepare(worker)
        return packet

    def offer(item):
        offered.append(item)
        events.append(("offer", item))
        return True

    worker = _ResourceWorker(schedule.resources[0], ("one",),
        SimpleNamespace(poll_resource=lambda _resource: (delivery,)),
        SimpleNamespace(bindings=preparer.bindings, prepare=prepare),
        SimpleNamespace(rtbw_delivery_is_stale=lambda _delivery: False, offer=offer), .01,
        stage_callback=lambda actual, stage: events.append((actual, stage)))
    return SimpleNamespace(worker=worker, delivery=delivery, packet=packet, ref=ref,
                           ledger=ledger, events=events, prepare_calls=prepare_calls, offered=offered)


class PaneCustodyQueueTests(unittest.TestCase):
    def test_worker_reports_original_ref_across_prepare_before_queue(self):
        fixture = worker_fixture()
        fixture.worker._poll_and_advance()

        self.assertEqual(fixture.events[:2], [(fixture.ref, Stage.PREPARING), (fixture.ref, Stage.PREPARED)])
        self.assertEqual(fixture.events[2], ("offer", fixture.packet))
        self.assertEqual(len(fixture.events), 3)
        self.assertIs(fixture.events[0][0], fixture.ref)
        self.assertIs(fixture.events[1][0], fixture.ref)
        self.assertIs(fixture.offered[0], fixture.packet)
        self.assertIs(fixture.packet.delivery, fixture.delivery)
        self.assertEqual(fixture.prepare_calls, [fixture.delivery])
        self.assertEqual(fixture.worker.snapshot().prepared_publications, 1)

    def test_worker_none_ref_stays_legacy_inert(self):
        fixture = worker_fixture(refs=False)
        fixture.worker._poll_and_advance()
        stages = [event for event in fixture.events if event[0] != "offer"]
        self.assertEqual(stages, [])
        self.assertIsNone(fixture.delivery.obligation_ref)
        self.assertEqual(fixture.packet.active_obligation_refs, ())
        self.assertEqual(fixture.events, [("offer", fixture.packet)])
        self.assertIs(fixture.offered[0], fixture.packet)
        self.assertEqual(fixture.prepare_calls, [fixture.delivery])
        self.assertEqual(fixture.worker.snapshot().prepared_publications, 1)

    def test_pending_stop_or_plan_cancels_single_input_before_prepare(self):
        for control in ("stop", "plan"):
            with self.subTest(control=control):
                fixture = worker_fixture()
                # Populate an EXISTING constructor-owned field; do not launch
                # or imitate a hardware Stop. Plan uses its real request path.
                if control == "plan":
                    fixture.worker._request_plan(lambda: None)
                with patch.object(fixture.worker, "_pending_stop",
                                  Future() if control == "stop" else None):
                    fixture.worker._poll_and_advance()
                self.assertEqual(fixture.events, [(fixture.ref, Stage.ADMISSION_CANCELLED)])
                self.assertIs(fixture.events[0][0], fixture.ref)
                self.assertEqual(fixture.prepare_calls, [])
                self.assertEqual(fixture.offered, [])
                self.assertEqual(fixture.worker.snapshot().prepared_publications, 0)

    def test_pending_stop_or_plan_during_prepare_cancels_before_offer(self):
        for control in ("stop", "plan"):
            with self.subTest(control=control):
                def request_control(worker):
                    if control == "stop":
                        with worker._condition:
                            worker._pending_stop = Future()
                    else:
                        worker._request_plan(lambda: None)

                fixture = worker_fixture(during_prepare=request_control)
                fixture.worker._poll_and_advance()
                self.assertEqual(fixture.events, [(fixture.ref, Stage.PREPARING),
                                                  (fixture.ref, Stage.PREPARATION_CANCELLED)])
                self.assertTrue(all(actual is fixture.ref for actual, _stage in fixture.events))
                self.assertEqual(fixture.prepare_calls, [fixture.delivery])
                self.assertEqual(fixture.offered, [])
                self.assertEqual(fixture.worker.snapshot().prepared_publications, 0)

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
