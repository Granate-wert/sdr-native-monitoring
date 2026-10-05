"""Actual graph/ledger layer custody with fake owners, NOT hardware/paint proof."""
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from sdr_monitor.application.analyzer_rtbw_router import AnalyzerRtbwRouter
from sdr_monitor.application.analyzer_sweep_router import AnalyzerSweepRouter
from sdr_monitor.domain.analytical_ready import ReadyClockMapping
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, bundle_from_sweep
from sdr_monitor.domain.device_capabilities import stable_identity_key
from sdr_monitor.domain.layer_journal import (
    LayerCreationCounters, LayerCreationEvent, LayerJournalSnapshot, LayerJournalState, SweepLayerScope,
)
from sdr_monitor.domain.layer_ready import DensityLayerIdentity, LayerReadyKind, LayerReadyReceipt, SweepLayerIdentity
from sdr_monitor.domain.live import LivePersistenceFrame
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView as View
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, compile_pane_schedule
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.pane_delivery_ledger import HOST_GRAPH_SCALAR_BUDGET, PaneDeliveryLedger
from sdr_monitor.services.pane_layer_admission import bind_pane_layer_identity, cached_layer_journals
from sdr_monitor.services.pane_resource_session import PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from tests import test_app07_pane_analytical_identity as analytic
from tests import test_app07_pane_resource_session as graph
from tests import test_app07_pane_delivery_obligations as custody
from tests import test_app07_layer_ready_admission as layers
from tests import test_app07_product_layer_bridge as native_layers


def packet(frame, *, scope=None, creation=1, producer=41):
    if isinstance(frame, LivePersistenceFrame):
        kind = LayerReadyKind.DENSITY
        identity = DensityLayerIdentity(frame.source_id, frame.config_generation, frame.update_sequence,
            frame.source_frame_sequence, frame.accumulation_id, frame.receiver_id,
            frame.acquisition_epoch, frame.native_accumulation_sequence)
        event = LayerCreationEvent(kind, producer, creation, 150, False, 0, 0, 0,
            frame.config_generation, frame.update_sequence, frame.source_frame_sequence,
            frame.native_accumulation_sequence)
    else:
        progress = hasattr(frame, "revision")
        kind = LayerReadyKind.SWEEP_PROGRESS if progress else LayerReadyKind.SWEEP_TERMINAL
        identity = SweepLayerIdentity(frame.source_id, frame.epoch, frame.sequence,
            frame.revision if progress else None,
            frame.acquired_segment_generations if progress else tuple(pair for pair in
                frame.segment_config_generations if pair[0] not in frame.missing_segment_indices),
            frame.pending_segment_indices if progress else frame.missing_segment_indices, frame.receiver_id)
        event = LayerCreationEvent(kind, producer, creation, 150, False,
            frame.epoch, frame.sequence, frame.revision if progress else 0, 0, 0, 0, 0)
        scope = scope or SweepLayerScope("clock", 123, "run", frame.source_id,
            frame.receiver_id, "session", frame.epoch)
    ref = LayerReadyReceipt(kind, identity, scope.clock_scope_id, scope.host_process_id,
        producer, creation, 150, ReadyClockMapping.OUTSIDE_SAMPLES,
        owner_run_id=scope.owner_run_id, session_id=scope.session_id)
    snapshot = LayerJournalSnapshot(LayerJournalState.ACTIVE, scope,
        LayerCreationCounters(producer, creation, 0, 64, 0, creation, 0, 0, 0), (event,))
    return replace(frame, layer_ready=ref), snapshot


def bind(frame, snapshots, **changes):
    args = dict(physical_stream_resource_id="resource", capture_id="capture",
        receiver_endpoint_id="rx", pane_id="one", host_run_serial=1, host_activation_serial=1)
    args.update(changes)
    return bind_pane_layer_identity(frame, snapshots, **args)


class PaneLayerCustodyTests(unittest.TestCase):
    def assert_conserved(self, ledger):
        snapshot = ledger.snapshot()
        self.assertLessEqual(snapshot.retained_scalar_bytes, HOST_GRAPH_SCALAR_BUDGET)
        for counts in (*snapshot.panes, *(item.counters for item in snapshot.views)):
            self.assertEqual(counts.qualified_admissions, counts.pending + counts.terminal + counts.untracked_admissions)
            self.assertGreaterEqual(counts.pending, 0)

    def test_same_creation_two_views_have_independent_actual_outcomes(self):
        frame, snapshot = packet(layers.terminal_frame(True))
        identity = bind(frame, (snapshot,))
        self.assertIs(identity.original_event, snapshot.events[0])
        self.assertIs(identity.ready, frame.layer_ready)
        ledger = PaneDeliveryLedger(("one",))
        spectrum = ledger.admit("one", identity)
        waterfall = ledger.admit("one", identity, view=View.WATERFALL)
        self.assertNotEqual(spectrum, waterfall)
        self.assertIs(spectrum.identity, waterfall.identity)
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED, Stage.QUEUE_DRAINED,
                      Stage.UI_ADMITTED, Stage.PAINT_SCHEDULED, Stage.PAINT_RETURNED):
            self.assertTrue(ledger.note(spectrum, stage))
        self.assertEqual(ledger.snapshot().records[1].stage, Stage.ADMITTED)
        ledger.cancel_unclaimed("resource")
        self.assertEqual(ledger.snapshot().records[1].stage, Stage.ADMISSION_CANCELLED)
        self.assertEqual(ledger.snapshot().panes[0].terminal, 2)
        self.assert_conserved(ledger)
        with self.assertRaises(FrozenInstanceError):
            identity.pane_id = "foreign"

    def test_density_cannot_become_spectrum_waterfall_and_progress_not_full_row(self):
        _, bridge, scope, journal, identity = native_layers.setup()
        raw = native_layers.raw_ref()
        journal.drain(lambda _: native_layers.batch([raw]))
        ready = journal.receipt(raw, identity, bridge)
        frame = replace(layers.density_frame(), receiver_id="RX2", native_accumulation_sequence=3, layer_ready=ready)
        bound = bind(frame, (journal.current(),), admitted_density_scope=scope)
        self.assertIsNotNone(bound)
        ledger = PaneDeliveryLedger(("one",))
        self.assertIsNone(ledger.admit("one", bound))
        self.assertIsNone(ledger.admit("one", bound, view=View.WATERFALL))
        self.assertIsNotNone(ledger.admit("one", bound, view=View.PERSISTENCE))
        progress, snapshot = packet(layers.progress_frame())
        self.assertIsNone(ledger.admit("one", bind(progress, (snapshot,)), view=View.WATERFALL))
        self.assertEqual(ledger.snapshot().accounting_failures, 3)
        self.assert_conserved(ledger)

    def test_cached_owner_match_required_not_sequence_below_counter(self):
        frame, snapshot = packet(layers.terminal_frame())
        for changes in (dict(events=()), dict(state=LayerJournalState.INCOMPLETE),
                        dict(scope=replace(snapshot.scope, owner_run_id="other-run")),
                        dict(scope=replace(snapshot.scope, clock_scope_id="foreign-clock")),
                        dict(scope=replace(snapshot.scope, acquisition_epoch=frame.epoch + 1)),
                        dict(events=(replace(snapshot.events[0], ready_native_ns=151),)),
                        dict(counters=replace(snapshot.counters, producer_instance_id=42))):
            with self.subTest(changes=changes):
                self.assertIsNone(bind(frame, (replace(snapshot, **changes),)))
        self.assertIsNone(bind(frame, (snapshot, snapshot)))
        self.assertIsNotNone(bind(frame, (replace(snapshot, state=LayerJournalState.FINAL),)))
        self.assertIsNone(bind(replace(frame, layer_ready=None), (snapshot,)))

    def test_density_also_requires_start_scope_and_no_second_native_drain_or_probe(self):
        native, bridge, scope, journal, identity = native_layers.setup()
        reader = Mock(return_value=native_layers.batch([native_layers.raw_ref()]))
        journal.drain(reader)
        ready = journal.receipt(native_layers.raw_ref(), identity, bridge)
        frame = replace(layers.density_frame(), receiver_id="RX2", native_accumulation_sequence=3, layer_ready=ready)
        probes = native.analytical_ready_clock_ns.call_count
        port = SimpleNamespace(density_layer_journal_snapshot=journal.current)
        for _ in range(20):
            self.assertIsNone(bind(frame, cached_layer_journals(port)))
            self.assertIsNotNone(bind(frame, cached_layer_journals(port), admitted_density_scope=scope))
            self.assertIsNone(bind(frame, cached_layer_journals(port),
                                   admitted_density_scope=replace(scope, owner_run_id="old")))
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(native.analytical_ready_clock_ns.call_count, probes)

    def test_same_sweep_stream_progress_then_terminal_not_namespace_change(self):
        progress, first = packet(layers.progress_frame())
        terminal, second = packet(layers.terminal_frame(True), scope=first.scope, creation=2)
        ledger = PaneDeliveryLedger(("one",))
        one = ledger.admit("one", bind(progress, (first,)))
        two = ledger.admit("one", bind(terminal, (second,)))
        self.assertIsNotNone(one)
        self.assertIsNotNone(two)
        self.assertIsNone(ledger.admit("one", bind(progress, (first,))))
        self.assertIsNotNone(ledger.admit("one", bind(terminal, (second,)), view=View.WATERFALL))
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        self.assert_conserved(ledger)

    def test_shared_budget_all_views_under_pressure_and_view_tampering_refused(self):
        frame, snapshot = packet(layers.terminal_frame())
        bound = bind(frame, (snapshot,))
        ledger = PaneDeliveryLedger(("one",))
        first = ledger.admit("one", bound)
        self.assertFalse(ledger.note(replace(first, view=View.WATERFALL), Stage.PREPARING))
        for index in range(2, 500):
            frame, snapshot = packet(layers.terminal_frame(), creation=index)
            bound = bind(frame, (snapshot,))
            for view in (View.SPECTRUM, View.WATERFALL):
                ledger.admit("one", bound, view=view)
        self.assertGreater(ledger.snapshot().panes[0].untracked_admissions, 0)
        self.assertEqual(ledger.snapshot().record_evictions, 0)
        self.assertEqual(ledger.snapshot().reserved_bytes, HOST_GRAPH_SCALAR_BUDGET)
        self.assert_conserved(ledger)

    def test_cached_contract_refuses_unbounded_mutable_or_wrong_events(self):
        _, snapshot = packet(layers.terminal_frame())
        for value in ((snapshot,) * 3, [snapshot], (replace(snapshot, events=list(snapshot.events)),),
                      (replace(snapshot, events=snapshot.events * 33),),
                      (replace(snapshot, events=(object(),)),),
                      (replace(snapshot, counters=object()),), (replace(snapshot, scope=object()),)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cached_layer_journals(SimpleNamespace(layer_journal_snapshots=lambda: value))
            self.assertIsNone(bind(layers.terminal_frame(), value))
        self.assertEqual(cached_layer_journals(object()), ())

    def test_router_uses_captured_not_selected_owner_and_terminal_stop_cached(self):
        _, snapshot = packet(layers.terminal_frame())
        port = SimpleNamespace(density_layer_journal_snapshot=Mock(return_value=snapshot),
            stop=Mock(return_value=SimpleNamespace(error=None, stop_required=False)), is_running=lambda: False)
        wrong = SimpleNamespace(density_layer_journal_snapshot=Mock(side_effect=AssertionError("selected peer")))
        rtbw = AnalyzerRtbwRouter(wrong, Mock(), None)
        rtbw._dispatched = port
        self.assertEqual(rtbw.density_layer_journal_snapshots(), (snapshot,))
        rtbw.stop()
        calls = port.density_layer_journal_snapshot.call_count
        self.assertEqual(rtbw.density_layer_journal_snapshots(), (snapshot,))
        self.assertEqual(port.density_layer_journal_snapshot.call_count, calls)
        sweep = AnalyzerSweepRouter(wrong, None, None)
        sweep._dispatched = SimpleNamespace(layer_journal_snapshot=lambda: snapshot, stop=Mock())
        sweep._terminal = sweep._dispatched
        sweep.stop()
        self.assertEqual(sweep.layer_journal_snapshots(), (snapshot,))
        wrong.density_layer_journal_snapshot.assert_not_called()


class PaneGraphLayerCustodyTests(unittest.TestCase):
    def make_sweep(self):
        helper = graph.PaneResourceSessionTests(methodName="runTest")
        helper.setUp()
        owner = graph.FakeOwner("device")
        session = helper.session((graph.group("device", "rx"),),
            (graph.pane("one", "rx", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),), {"device": owner})
        self.addCleanup(session.stop_all)
        session.apply()
        activation = session.start_resource("device")
        return session, owner, activation

    def test_actual_sweep_delivery_progress_terminal_two_views_original_arrays_stop(self):
        session, owner, activation = self.make_sweep()
        base = graph.frame("device:source", owner.admission_epoch - 1, 100e6, 108e6).spectrum
        progress, snapshot = packet(base)
        owner.layer_journal_snapshots = Mock(return_value=(snapshot,))
        one = session.accept_frame(activation, "rx", bundle_from_sweep(progress))[0]
        self.assertIsNone(one.obligation_ref)
        self.assertEqual([ref.view for ref in one.layer_obligation_refs], [View.SPECTRUM])
        terminal = replace(layers.terminal_frame(True), source_id=base.source_id, epoch=base.epoch,
            frequencies_hz=base.frequencies_hz, values_db=base.values_db,
            quality_flags=base.quality_flags, source_segment_indices=base.source_segment_indices)
        terminal, snapshot = packet(terminal, scope=snapshot.scope, creation=2)
        owner.layer_journal_snapshots.return_value = (snapshot,)
        two = session.accept_frame(activation, "rx", bundle_from_sweep(terminal))[0]
        self.assertIs(two.bundle.spectrum.values_db, terminal.values_db)
        self.assertEqual([ref.view for ref in two.layer_obligation_refs], [View.SPECTRUM, View.WATERFALL])
        self.assertTrue(all(ref.identity.ready is terminal.layer_ready for ref in two.layer_obligation_refs))
        session.stop_resource("device")
        self.assertTrue(all(record.stage is Stage.ADMISSION_CANCELLED
                            for record in session.pane_delivery_ledger_snapshot().records))
        self.assertFalse(owner.running)

    def test_cached_diagnostic_failure_does_not_drop_measurement_or_forge_ticket(self):
        session, owner, activation = self.make_sweep()
        frame = graph.frame("device:source", owner.admission_epoch - 1, 100e6, 108e6).spectrum
        frame, _ = packet(frame)
        owner.layer_journal_snapshots = Mock(side_effect=RuntimeError("diagnostic failure"))
        delivered = session.accept_frame(activation, "rx", bundle_from_sweep(frame))
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].layer_obligation_refs, ())
        self.assertIs(delivered[0].bundle.spectrum, frame)
        self.assertTrue(owner.running)
        self.assertEqual(session.pane_delivery_ledger_snapshot().panes[0].unqualified_deliveries, 1)

    def test_actual_rtbw_three_views_shared_scalar_read_and_density_separate(self):
        class Owner(analytic.ScopedOwner):
            def start_capture(self, job):
                admission = super().start_capture(job)
                self.scope = replace(self.scope, receiver_id=None)
                return replace(admission, owner_journal_scopes=(("rx", self.scope),))

        owner = Owner()
        groups = (graph.group("device", "rx"),)
        schedule = compile_pane_schedule(groups, (graph.pane("one", "rx", 100e6, 108e6,
            ReceiverBindingMode.DEDICATED_PARALLEL),), {"capture": graph.profile(36e6, CaptureMeasurementMode.RTBW)})
        session = PaneResourceSession(schedule, groups, {"device": owner}, ReceiverLeaseManager(),
            source_identity_keys={"device:source": stable_identity_key("device:source")})
        self.addCleanup(session.stop_all)
        session.apply()
        activation = session.start_resource("device")
        base = graph.live_frame(owner.scope.source_id, owner.scope.session_id, owner.scope.acquisition_epoch)
        spectrum = replace(base.spectrum, detector_ready=analytic.ready(owner.scope))
        density = replace(layers.density_frame(), source_id=spectrum.source_id,
            config_generation=spectrum.config_generation, source_frame_sequence=spectrum.sequence,
            frequency_bins=len(spectrum.values), frequencies_hz=spectrum.frequencies_hz,
            density=np.zeros((2, len(spectrum.values)), dtype=np.float32), receiver_id=None,
            accumulation_id=owner.scope.session_id, acquisition_epoch=owner.scope.acquisition_epoch,
            native_accumulation_sequence=3)
        density, snapshot = packet(density, scope=owner.scope)
        owner.layer_journal_snapshots = Mock(return_value=(snapshot,))
        bundle = AnalyzerFrameBundle(spectrum, base.session_id, base.receiver_id, base.acquisition_epoch,
            base.rtbw, persistence=density)
        delivery = session.accept_frame(activation, "rx", bundle)[0]
        self.assertEqual([ref.view for ref in delivery.layer_obligation_refs], [View.WATERFALL, View.PERSISTENCE])
        self.assertIs(delivery.layer_obligation_refs[1].identity.ready, density.layer_ready)
        self.assertIs(delivery.bundle.persistence.density, density.density)
        owner.layer_journal_snapshots.assert_called_once()
        repeated = session.accept_frame(activation, "rx", bundle)[0]
        self.assertIsNone(repeated.obligation_ref)
        self.assertEqual(repeated.layer_obligation_refs, ())
        self.assertEqual(session.pane_delivery_ledger_snapshot().panes[0].qualified_admissions, 3)
        with self.assertRaises(ValueError):
            replace(delivery, layer_obligation_refs=(replace(delivery.layer_obligation_refs[0],
                identity=custody.identity(pane="foreign")),))


if __name__ == "__main__":
    unittest.main()
