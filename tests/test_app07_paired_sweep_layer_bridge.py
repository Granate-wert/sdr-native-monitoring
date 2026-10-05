"""SAME admitted paired Sweep journals: compiled mock only, never RF proof."""
from contextlib import nullcontext
import time
import unittest
from unittest.mock import patch

from sdr_monitor.domain.layer_journal import LayerJournalState, SweepLayerScope
from sdr_monitor.domain.layer_ready import SweepLayerIdentity
from sdr_monitor.services.native_layer_journal import NativeLayerJournal, sweep_layer_host_reservation
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
from tests.test_app07_product_layer_bridge import setup, raw_ref, batch, Kinds
from tests import test_app07_paired_sweep_product_lease as lease_tests


class SweepEpochObservationTests(unittest.TestCase):
    def test_unknown_epoch_observed_from_original_batch_not_floor_or_empty_read(self):
        native, _, base, _, _ = setup()
        journal = NativeLayerJournal(native, 64, density=False)
        scope = SweepLayerScope(base.clock_scope_id, base.host_process_id, "run", "source", "RX1",
                                "session", None, minimum_epoch=17)
        journal.begin(scope)
        journal.drain(lambda _: batch([]))
        self.assertIsNone(journal.current().scope.acquisition_epoch)
        ref = raw_ref(kind=Kinds.SweepTerminal, sweep_epoch=81, line_sequence=2,
                      config_generation=0, update_sequence=0, source_frame_sequence=0, accumulation_sequence=0)
        journal.drain(lambda _: batch([ref], created=1, drained=1))
        self.assertEqual(journal.current().scope.acquisition_epoch, 81)
        journal.drain(lambda _: batch([replace_raw(ref, creation_sequence=2, sweep_epoch=82)], created=2))
        self.assertIs(journal.current().state, LayerJournalState.INCOMPLETE)
        self.assertEqual(journal.current().scope.acquisition_epoch, 81)

    def test_mixed_or_below_floor_batch_never_partially_admits_epoch(self):
        native, _, base, _, _ = setup()
        for epochs in ((16,), (81, 82)):
            with self.subTest(epochs=epochs):
                journal = NativeLayerJournal(native, 64, density=False)
                scope = SweepLayerScope(base.clock_scope_id, base.host_process_id, "run", "source", "RX1",
                                        "session", None, minimum_epoch=17)
                journal.begin(scope)
                refs = [raw_ref(i + 1, kind=Kinds.SweepTerminal, sweep_epoch=epoch,
                    config_generation=0, update_sequence=0, source_frame_sequence=0, accumulation_sequence=0)
                        for i, epoch in enumerate(epochs)]
                journal.drain(lambda _: batch(refs))
                value = journal.current()
                self.assertIs(value.state, LayerJournalState.INCOMPLETE)
                self.assertIsNone(value.scope.acquisition_epoch)
                self.assertIsNone(value.counters)
                self.assertEqual(value.events, ())


def replace_raw(raw, **changes):
    from types import SimpleNamespace
    return SimpleNamespace(**(vars(raw) | changes))


def run_layer_case(graph, intent, case, hooks, native):
    disabled = case == "layer-disabled"
    with (patch.object(native, "LAYER_CREATION_CONTRACT_VERSION", 0) if disabled else nullcontext()):
        factory = NativeContinuousSweepPlanFactory.from_paired_application(graph.live, intent)
        config = factory.build_paired()
        if not disabled:
            from sdr_monitor.services.native_sweep import NativeSweepSource
            source = NativeSweepSource(factory._lease.source.context_uri, "budget", intent.pair.configuration,
                                       expected_serial=intent.selected_snapshot.device.serial)
            with patch.object(native, "LAYER_CREATION_CONTRACT_VERSION", 0):
                before = NativeContinuousSweepPlanFactory.build_paired_native_config(native, source, intent)
            assert config.product_publication_reserved_bytes - before.product_publication_reserved_bytes == (
                2 * sweep_layer_host_reservation(len(config.primary.segments)))
        assert config.primary.layer_event_capacity == config.secondary.layer_event_capacity == (0 if disabled else 64)
        owner = factory.create_coordinator()
        raw = owner._owner
        originals = {}

        class Observer:
            def __getattr__(self, name): return getattr(raw, name)
            def poll_paired_lines(self, maximum):
                values = raw.poll_paired_lines(maximum)
                for value in values:
                    for frame in (value.primary, value.secondary):
                        originals[(frame.source_id, frame.epoch, frame.line_sequence, None)] = frame
                return values
            def poll_paired_progress(self):
                value = raw.poll_paired_progress()
                if value is not None:
                    for frame in (value.primary, value.secondary):
                        originals[(frame.source_id, frame.epoch, frame.line_sequence, frame.revision)] = frame
                return value
            def drain_sweep_layer_ready_events(self, receiver, maximum):
                if case == "layer-failure" and receiver == native.PlutoReceiverSelection.RX2:
                    raise RuntimeError("injected optional RX2 journal failure")
                return raw.drain_sweep_layer_ready_events(receiver, maximum)
            def start(self):
                raw.start()
                if case == "layer-partial-start":
                    raise RuntimeError("injected after actual Start")

        owner._owner = Observer()
        try:
            previous = None
            for iteration in range(2):
                owner.configure_paired(config)
                if case == "layer-partial-start":
                    try:
                        owner.start()
                    except RuntimeError as error:
                        assert "injected" in str(error)
                    else:
                        raise AssertionError("partial Start not propagated")
                    assert owner.active_run is None
                    try:
                        owner.join()
                    except RuntimeError as error:
                        assert "requires request_stop" in str(error)
                    else:
                        raise AssertionError("running partial Start join would block")
                    owner.stop()
                    assert all(value.native_stop_confirmed for value in owner.layer_journal_snapshots())
                    break
                run = owner.start()
                if not disabled:
                    assert all(value.scope.acquisition_epoch is None for value in owner.layer_journal_snapshots())
                if case == "layer-close":
                    factory.close()  # actual registered raw cleanup, not wrapper Stop
                    assert all(value.native_stop_confirmed for value in owner.layer_journal_snapshots())
                    break
                deadline = time.monotonic() + 5
                saw_terminal = saw_progress = False
                refs = []
                while time.monotonic() < deadline:
                    progress = owner.poll_observed_progress()
                    lines = owner.poll_observed_lines()
                    for publication in ((progress,) if progress is not None else ()) + lines:
                        is_progress = hasattr(publication.primary, "revision")
                        saw_progress |= is_progress
                        saw_terminal |= not is_progress
                        for frame, value in zip((publication.primary, publication.secondary), owner.layer_journal_snapshots()):
                            if disabled:
                                assert frame.layer_ready is None and frame.receiver_id is None
                                continue
                            assert frame.receiver_id == value.scope.receiver_id
                            if case == "layer-failure" and frame.receiver_id == "RX2":
                                assert frame.layer_ready is None
                                assert value.state is LayerJournalState.INCOMPLETE
                                continue
                            ref = frame.layer_ready
                            if ref is None:
                                continue  # finite host retention may miss old queued frames
                            original = originals[(frame.source_id, frame.epoch, frame.sequence,
                                                  frame.revision if is_progress else None)].layer_ready
                            assert ref.ready_native_ns == original.ready_native_ns
                            assert ref.producer_instance_id == original.producer_instance_id == value.counters.producer_instance_id
                            assert ref.creation_sequence == original.creation_sequence
                            assert ref.identity.epoch == value.scope.acquisition_epoch == frame.epoch
                            assert ref.owner_run_id == value.scope.owner_run_id
                            assert ref.session_id == intent.pair.session_id
                            if previous is not None:
                                assert frame.epoch > previous[0].scope.acquisition_epoch
                                assert value.scope.owner_run_id != previous[0].scope.owner_run_id
                            refs.append(ref)
                    if saw_terminal and saw_progress and (refs or disabled):
                        break
                    time.sleep(.001)
                assert saw_terminal and saw_progress
                if not disabled:
                    assert refs
                if case == "layer-failure":
                    assert all(ref.identity.receiver_id == "RX1" for ref in refs)
                before = owner.layer_journal_snapshots()
                for _ in range(20):
                    assert owner.layer_journal_snapshots() == before
                owner.stop()
                archive = owner.poll_retired_archive()
                assert archive.run is run
                final = owner.layer_journal_snapshots()
                if not disabled:
                    assert all(value.native_stop_confirmed for value in final)
                    assert final[0].state is LayerJournalState.FINAL and final[0].counters.events_pending == 0
                    if case != "layer-failure":
                        assert final[1].state is LayerJournalState.FINAL and final[1].counters.events_pending == 0
                        assert final[0].scope.acquisition_epoch == final[1].scope.acquisition_epoch
                        assert final[0].scope.owner_run_id == final[1].scope.owner_run_id
                        assert final[0].counters.producer_instance_id != final[1].counters.producer_instance_id
                    ref = refs[0]
                    key = SweepLayerIdentity(ref.identity.source_id, ref.identity.epoch, ref.identity.line_sequence,
                        ref.identity.revision, ref.identity.acquired_segment_generations, ref.identity.pending_segment_indices,
                        "RX2" if ref.identity.receiver_id == "RX1" else "RX1")
                    # Even an original native ref cannot borrow another typed receiver.
                    original = next(frame.layer_ready for frame in originals.values()
                                    if frame.layer_ready.producer_instance_id == ref.producer_instance_id
                                    and frame.layer_ready.creation_sequence == ref.creation_sequence)
                    assert owner._layer_journals[0].receipt(original, key, owner._ready_bridge) is None
                previous = final
            factory.close()
            assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
            assert graph.live._pane_control_claim is None
            saved = owner.layer_journal_snapshots()
            assert owner.layer_journal_snapshots() == saved
        finally:
            factory.close()


class CompiledPairedSweepLayerTests(unittest.TestCase):
    def test_actual_owner_journals_epoch_rearm_original_refs_and_shared_budget(self):
        runner = lease_tests.PairedSweepProductLeaseTests()
        for case in ("layer-workflow", "layer-disabled", "layer-failure", "layer-close", "layer-partial-start"):
            with self.subTest(case=case):
                runner.run_native(case)
