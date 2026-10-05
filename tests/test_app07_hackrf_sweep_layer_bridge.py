"""Actual HackRF Sweep service + original scalar sidecars; MOCK only."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sdr_monitor.domain.layer_journal import LayerJournalState
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.services.layer_ready_admission import LayerReadyAdmissionState, admit_layer_ready
from tests.test_app06_hackrf_sweep_display import setup_owner
from tests.test_app07_layer_ready_admission import progress_frame, terminal_frame
from tests.test_app07_product_layer_bridge import Kinds, batch, protocol, raw_ref


def configured():
    service, native, control, exclusion, request, selection = setup_owner()
    protocol(native)
    native.analytical_ready_clock_ns = Mock(side_effect=range(100, 10000, 100))
    control.drain_sweep_layer_ready_events = Mock(return_value=batch([], created=0))
    return service, native, control, exclusion, request, selection


def publication(request, sequence=1, *, terminal=False, producer=41):
    ref = raw_ref(kind=Kinds.SweepTerminal if terminal else Kinds.SweepProgress,
        producer_instance_id=producer, sweep_epoch=request.epoch, line_sequence=sequence,
        revision=0 if terminal else 1, config_generation=0, update_sequence=0,
        source_frame_sequence=0, accumulation_sequence=0)
    base = terminal_frame() if terminal else progress_frame()
    frame = replace(base, source_id=request.source.device_id, epoch=request.epoch,
        sequence=sequence, **({"segment_config_generations": ((0, request.epoch), (1, request.epoch))}
        if terminal else {"acquired_segment_generations": ((0, request.epoch),)}))
    fields = dict(source_id=frame.source_id, epoch=frame.epoch, line_sequence=frame.sequence,
        unit=frame.unit, frequencies_hz=frame.frequencies_hz, values=frame.values_db,
        quality_flags_per_bin=frame.quality_flags, source_segment_indices=frame.source_segment_indices,
        layer_ready=ref)
    if terminal:
        fields.update(completed_ns=frame.completed_at_ns, state=frame.state.value,
            gap_reasons=(), missing_segment_indices=(), segment_config_generations=frame.segment_config_generations)
    else:
        fields.update(revision=frame.revision, pending_segment_indices=frame.pending_segment_indices,
            acquired_segment_generations=frame.acquired_segment_generations)
    return SimpleNamespace(**fields), ref


class HackrfSweepLayerBridgeTests(unittest.TestCase):
    def test_same_control_original_progress_terminal_before_one_real_conversion_and_stop(self):
        service, native, control, exclusion, request, selection = configured()
        service.preflight(request, selection)
        native.analytical_ready_clock_ns.assert_not_called()
        service.start(request, selection)
        self.assertEqual(native.create_hackrf_sweep_runtime_control.call_args.kwargs,
            {"layer_event_capacity": 64})
        for terminal in (False, True):
            raw, ref = publication(request, terminal=terminal)
            control.poll_next_publication.return_value = raw
            # ONE original event per poll; terminal creation has its own identity.
            ref.creation_sequence = 2 if terminal else 1
            control.drain_sweep_layer_ready_events.return_value = batch([ref], created=ref.creation_sequence)
            snapshot = service.poll_latest()
            frame = snapshot.line if terminal else snapshot.progress
            self.assertIsNotNone(frame)
            self.assertIsNotNone(frame.layer_ready)
            self.assertEqual(frame.layer_ready.ready_native_ns, ref.ready_native_ns)
            self.assertEqual(frame.layer_ready.producer_instance_id, ref.producer_instance_id)
            self.assertEqual(frame.layer_ready.creation_sequence, ref.creation_sequence)
            self.assertIs(admit_layer_ready(frame, frame.layer_ready).state, LayerReadyAdmissionState.MATCHED)
            self.assertIsNone(frame.receiver_id)  # no fabricated AD RX identifier
            self.assertFalse(frame.values_db.flags.writeable)
        control.drain_sweep_layer_ready_events.return_value = batch([], created=2, drained=2)
        probes, drains = native.analytical_ready_clock_ns.call_count, control.drain_sweep_layer_ready_events.call_count
        for _ in range(20):
            service.layer_journal_snapshot()
        self.assertEqual((native.analytical_ready_clock_ns.call_count,
            control.drain_sweep_layer_ready_events.call_count), (probes, drains))
        before_release = []
        release = exclusion.release_external_analyzer_rx
        exclusion.release_external_analyzer_rx = lambda token: (
            before_release.append(service.layer_journal_snapshot()), release(token))
        service.stop()
        self.assertTrue(before_release[0].native_stop_confirmed)
        self.assertIs(before_release[0].state, LayerJournalState.FINAL)
        service.close()
        self.assertEqual(service.layer_journal_snapshot(), before_release[0])

    def test_diagnostic_failure_does_not_stop_valid_progress_and_never_retries_drain(self):
        service, _, control, _, request, selection = configured()
        control.drain_sweep_layer_ready_events.side_effect = RuntimeError("optional journal failure")
        service.start(request, selection)
        raw, _ = publication(request)
        control.poll_next_publication.return_value = raw
        self.assertIsNone(service.poll_latest().progress.layer_ready)
        control.poll_next_publication.return_value = publication(request, 2)[0]
        self.assertIsNotNone(service.poll_latest().progress)
        self.assertIs(service.layer_journal_snapshot().state, LayerJournalState.INCOMPLETE)
        self.assertEqual(control.drain_sweep_layer_ready_events.call_count, 1)
        service.stop()
        self.assertTrue(service.layer_journal_snapshot().native_stop_confirmed)
        self.assertEqual(control.drain_sweep_layer_ready_events.call_count, 1)

    def test_failed_stop_preserves_owner_and_does_not_invent_final_flush(self):
        service, _, control, exclusion, request, selection = configured()
        service.start(request, selection)
        control.stop.return_value = {"complete": False}
        with self.assertRaises(RuntimeError):
            service.stop()
        self.assertFalse(service.layer_journal_snapshot().native_stop_confirmed)
        control.drain_sweep_layer_ready_events.assert_not_called()
        self.assertEqual([event for event, _ in exclusion.events], ["claim"])
        control.stop.return_value = {"complete": True}
        service.stop()
        self.assertTrue(service.layer_journal_snapshot().native_stop_confirmed)

    def test_new_start_has_distinct_owner_scope_and_refuses_previous_producer(self):
        service, _, control, _, request, selection = configured()
        service.start(request, selection)
        old = service.layer_journal_snapshot().scope
        old_raw, old_ref = publication(request)
        control.poll_next_publication.return_value = old_raw
        control.drain_sweep_layer_ready_events.return_value = batch([old_ref])
        service.poll_latest()
        control.drain_sweep_layer_ready_events.return_value = batch([], created=1, drained=1)
        service.stop()
        service.start(request, selection)
        new = service.layer_journal_snapshot().scope
        self.assertNotEqual(new.owner_run_id, old.owner_run_id)
        self.assertNotEqual(new.session_id, old.session_id)
        raw, ref = publication(request, producer=42)
        raw.layer_ready = old_ref
        control.poll_next_publication.return_value = raw
        control.drain_sweep_layer_ready_events.return_value = batch([ref], producer=42)
        self.assertIsNone(service.poll_latest().progress.layer_ready)
        control.drain_sweep_layer_ready_events.return_value = batch([], created=1, drained=1, producer=42)
        service.stop()

    def test_unsupported_api_never_adds_factory_argument_clock_or_drain(self):
        service, native, control, _, request, selection = configured()
        native.HACKRF_LAYER_CREATION_CONTRACT_VERSION = True
        service.start(request, selection)
        self.assertEqual(native.create_hackrf_sweep_runtime_control.call_args.kwargs, {})
        raw, _ = publication(request)
        control.poll_next_publication.return_value = raw
        self.assertIsNone(service.poll_latest().progress.layer_ready)
        service.stop()
        native.analytical_ready_clock_ns.assert_not_called()
        control.drain_sweep_layer_ready_events.assert_not_called()
        self.assertIs(service.layer_journal_snapshot().state, LayerJournalState.UNSUPPORTED)

    def test_aggregate_host_reservation_refuses_before_identity_claim_or_rf(self):
        service, native, _, exclusion, request, selection = configured()
        with patch("sdr_monitor.services.hackrf_sweep_display.sweep_layer_reserved_bytes", return_value=1 << 30):
            with self.assertRaises(LiveAdmissionRejected):
                service.start(request, selection)
        native.create_hackrf_sweep_runtime_control.assert_not_called()
        native.analytical_ready_clock_ns.assert_not_called()
        self.assertEqual(exclusion.events, [])

    def test_stale_foreign_measurement_is_still_rejected_before_conversion(self):
        service, _, control, _, request, selection = configured()
        service.start(request, selection)
        raw, _ = publication(request)
        raw.source_id = "foreign"
        control.poll_next_publication.return_value = raw
        with self.assertRaises(RuntimeError):
            service.poll_latest()
        service.stop()


if __name__ == "__main__":
    unittest.main()
