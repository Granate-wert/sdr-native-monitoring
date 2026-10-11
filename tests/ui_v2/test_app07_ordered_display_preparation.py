"""Qt/SDK-free real-preparer regression fixtures for ordered RTBW display work.

No worker is launched. The fake session supplies already admitted deliveries;
the compiled layout, carriers, preparation, budget, queue and custody are real.
"""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, PairedCaptureMetadata, RtbwFrameMetadata
from sdr_monitor.domain.analytical_journal import OwnerJournalScope
from sdr_monitor.domain.analytical_ready import DetectorReadyReceipt, ReadyClockMapping
from sdr_monitor.domain.layer_journal import LayerCreationEvent
from sdr_monitor.domain.layer_ready import DensityLayerIdentity, LayerReadyKind, LayerReadyReceipt
from sdr_monitor.domain.live import LivePersistenceFrame, LiveSpectrumFrame
from sdr_monitor.domain.pane_analytical_identity import PaneAnalyticalIdentity
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView as View, PaneLayerAnalyticalIdentity
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneLayout, PaneLayoutSlot, compile_pane_schedule
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint,
)
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from sdr_monitor.services.pane_resource_diagnostics import PaneDiagnosticError, PaneFailureStage
from sdr_monitor.services.pane_resource_session import PaneDelivery
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.state import analyzer_layer_cache
from sdr_monitor.ui.v2.state.analyzer_layers import persistence_density_from_native, validate_native_persistence_display
from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
from sdr_monitor.ui.v2_pane_runtime import _ResourceWorker
from tests.test_app07_shared_capture_schedule import group, pane, profile


PROFILES = ((20_000_000.0, 144_000_000.0), (30_720_000.0, 2_450_000_000.0),
            (2_400_000.0, 100_000_000.0))


def make_ordered_fixture(*, topology="independent", count=3, limit_bytes=256 * 1024 * 1024):
    """Small deterministic factory also usable by root's matched measurement.

    Eight 4096 float32 spectra and two 256x4096 float32 densities per source;
    no list retains materialized outputs. Refs are opt-in, not fabricated by UI.
    Paired topology has two typed chains plus an optional independent peer.
    """
    if topology not in {"independent", "shared", "paired"} or not 1 <= count <= 3:
        raise ValueError("explicit bounded fixture topology required")
    groups, requests, profiles, specs, producers = [], [], {}, {}, {}
    for number in range(1, count + 1):
        joined = topology == "shared" or topology == "paired" and number <= 2
        resource = "resource-1" if joined else f"resource-{number}"
        endpoint = f"rx-{number}" if topology == "paired" and number <= 2 else (
            "rx-1" if joined else f"rx-{number}")
        rate, center = PROFILES[1] if topology == "paired" and number <= 2 else PROFILES[0 if joined else number - 1]
        key = "shared" if joined else f"profile-{number}"
        profiles[key] = replace(profile(rate, CaptureMeasurementMode.RTBW),
                                sample_rate_hz=rate, analog_bandwidth_hz=rate)
        binding_mode = (ReceiverBindingMode.SHARED_CAPTURE if topology == "shared" or topology == "paired" and number <= 2
                        else ReceiverBindingMode.DEDICATED_PARALLEL)
        request = replace(pane(f"pane-{number}", endpoint, center - rate / 2, center + rate / 2,
                               binding_mode), profile_id=key)
        requests.append(request)
        specs[request.pane_id] = (rate, center)
        if topology == "paired" and number <= 2:
            producers[endpoint] = f"producer-{number}"
        elif not any(item.physical_stream_resource_id == resource for item in groups):
            groups.append(group(resource, endpoint))
    if topology == "paired":
        if count < 2:
            raise ValueError("paired fixture requires both typed chains")
        endpoints = tuple(ReceiverEndpoint(f"rx-{number}", "resource-1:source", "resource-1",
                          ReceiverChainSelection.RX1 if number == 1 else ReceiverChainSelection.RX2)
                          for number in (1, 2))
        groups.insert(0, AcquisitionGroup("paired-group", "resource-1", endpoints))
    groups = tuple(groups)
    schedule = compile_pane_schedule(groups, tuple(requests), profiles)
    layout = PaneLayout(tuple(PaneLayoutSlot(number, request) for number, request in enumerate(requests, 1)), schedule)
    budget = PresentationAllocationBudget(limit_bytes)
    producer_lookup = (lambda resource, endpoint: producers.get(endpoint, resource + ":source")) if topology == "paired" else None
    preparer = PaneDeliveryPreparer(layout, groups, budget, admitted_producer_source_id=producer_lookup)
    panes = tuple(request.pane_id for request in requests)
    ledger = PaneDeliveryLedger(panes)
    stages = []
    def note(ref, stage):
        stages.append((ref, stage))
        ledger.note(ref, stage)
    queue = PaneFairDeliveryQueue(panes, stage_callback=note)
    batches = {resource.physical_stream_resource_id: [] for resource in schedule.resources}
    session = SimpleNamespace(poll_resource=lambda resource: tuple(batches[resource].pop(0)) if batches[resource] else ())
    workers = {resource.physical_stream_resource_id: _ResourceWorker(resource,
        tuple(crop.pane_id for job in resource.jobs for crop in job.crops), session, preparer, queue, .01,
        stage_callback=note) for resource in schedule.resources}
    banks = {}
    for pane_id, binding in preparer.bindings.items():
        rate, center = specs[pane_id]
        source = producers.get(binding.receiver_endpoint_id, binding.source_id)
        if source not in banks:
            grid = center + (np.arange(4096, dtype=np.float64) - 2048) * (rate / 4096)
            spectra = tuple(np.full(4096, -85 + position, np.float32) for position in range(8))
            densities = tuple(np.full((256, 4096), (position + 1) / 256, np.float32) for position in range(2))
            for array in (grid, *spectra, *densities):
                array.setflags(write=False)
            banks[source] = (grid, spectra, densities)

    def delivery(number, sequence, *, refs=False, scale=1.0, sync=1, gaps=0,
                 grid=None, density=True, native_reset=1, quality=0):
        binding = preparer.bindings[f"pane-{number}"]
        source = producers.get(binding.receiver_endpoint_id, binding.source_id)
        receiver = binding.receiver_selection.name if topology == "paired" and number <= 2 else None
        scope = OwnerJournalScope("clock139", 139, "owner139", source, receiver, "session139", 5, 7)
        bank_grid, spectra, densities = banks[source]
        grid = bank_grid if grid is None else grid
        ready = DetectorReadyReceipt(scope.clock_scope_id, 139, number, sequence, 5,
            10_000 + sequence, source, receiver, 7, scope.session_id,
            ReadyClockMapping.OUTSIDE_SAMPLES, owner_run_id=scope.owner_run_id)
        rate, center = specs[binding.pane_id]
        shared = dict(source_id=source, config_generation=5, receiver_id=receiver,
                      acquisition_epoch=7, accumulation_id=scope.session_id)
        spectrum = LiveSpectrumFrame(sequence, sequence * 10_000_000, center, rate, 4096, 2048,
            grid, spectra[sequence % 8], detector_ready=ready, native_quality_flags=quality, **shared)
        layer_identity = DensityLayerIdentity(source, 5, sequence, sequence, scope.session_id,
                                             receiver, 7, native_reset)
        layer = LayerReadyReceipt(LayerReadyKind.DENSITY, layer_identity, scope.clock_scope_id,
            139, 100 + number, sequence, 10_000 + sequence, ReadyClockMapping.OUTSIDE_SAMPLES,
            owner_run_id=scope.owner_run_id, session_id=scope.session_id)
        event = LayerCreationEvent(LayerReadyKind.DENSITY, 100 + number, sequence, 10_000 + sequence,
                                  False, 0, 0, 0, 5, sequence, sequence, native_reset)
        persistence = (LivePersistenceFrame(sequence, sequence * 10_000_000, sequence, -140, 20,
            256, 4096, sequence, True, grid, densities[sequence % 2], probability_scale=scale,
            count_scale=float(sequence), unit=spectrum.unit, native_accumulation_sequence=native_reset,
            layer_ready=layer, producer_identity_available=True, **shared) if density else None)
        pair = PairedCaptureMetadata(sync, sequence * 2048, gaps) if receiver is not None else None
        bundle = AnalyzerFrameBundle(spectrum, scope.session_id, receiver, 7,
            RtbwFrameMetadata(center, rate, 4096, 2048), persistence=persistence, paired_capture=pair)
        host = (binding.physical_stream_resource_id, binding.capture_id, binding.receiver_endpoint_id,
                binding.pane_id, 1, 1)
        analytical = PaneAnalyticalIdentity(scope, ready, *host) if refs else None
        spectrum_ref = ledger.admit(binding.pane_id, analytical) if refs else None
        layer_refs = ()
        if refs:
            waterfall_ref = ledger.admit(binding.pane_id, analytical, view=View.WATERFALL)
            layer_refs = (waterfall_ref,)
            if persistence is not None:
                density_identity = PaneLayerAnalyticalIdentity(scope, layer, event, *host)
                density_ref = ledger.admit(binding.pane_id, density_identity, view=View.PERSISTENCE)
                layer_refs += (density_ref,)
            if spectrum_ref is None or any(ref is None for ref in layer_refs):
                raise AssertionError("fixture did not admit original supplied custody")
        return PaneDelivery(binding.physical_stream_resource_id, binding.capture_id,
            binding.receiver_endpoint_id, 1, binding.crop, bundle, 0.0, 1, analytical,
            spectrum_ref, layer_refs)

    def poll(resource, deliveries):
        batches[resource].append(tuple(deliveries))
        workers[resource]._poll_and_advance()
    return SimpleNamespace(layout=layout, groups=groups, budget=budget, preparer=preparer,
        queue=queue, ledger=ledger, workers=workers, batches=batches, banks=banks,
        stages=stages, delivery=delivery, poll=poll)


class OrderedDisplayPreparationTests(unittest.TestCase):
    def test_three_independent_full_profiles_select_exact_latest_with_fewer_materializations(self):
        fixture = make_ordered_fixture()
        original = fixture.preparer.materialize_rtbw_display
        with patch.object(fixture.preparer, "materialize_rtbw_display", wraps=original) as materialize:
            expected = {}
            for number in (1, 2, 3):
                deliveries = tuple(fixture.delivery(number, seq, scale=1 / seq) for seq in (1, 126, 252))
                fixture.poll(f"resource-{number}", deliveries)
                expected[f"pane-{number}"] = deliveries[-1]
            self.assertEqual(materialize.call_count, 3)
        for packet in fixture.queue.drain():
            source = expected[packet.delivery.pane_id]
            self.assertIs(packet.delivery, source)
            self.assertIs(packet.spectrum.view.source_frame, source.bundle)
            np.testing.assert_array_equal(packet.persistence.density,
                np.multiply(source.bundle.persistence.density, source.bundle.persistence.probability_scale, dtype=np.float32))
            self.assertEqual(packet.delivery.bundle.spectrum.timestamp_ns, 252 * 10_000_000)
            self.assertEqual(packet.active_obligation_refs, ())
        for worker in fixture.workers.values():
            state = worker.snapshot()
            self.assertEqual((state.ordered_validated_publications, state.prepared_publications,
                              state.display_preparation_cancelled, state.pending_display_peak), (3, 1, 2, 1))
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)
        self.assertLessEqual(fixture.budget.snapshot().peak_bytes, fixture.budget.limit_bytes)

    def test_shared_same_source_panes_and_original_ref_identity(self):
        fixture = make_ordered_fixture(topology="shared", count=2)
        deliveries = tuple(fixture.delivery(number, seq, refs=True) for seq in (1, 126) for number in (1, 2))
        fixture.poll("resource-1", deliveries)
        self.assertEqual(fixture.workers["resource-1"].snapshot().pending_display_peak, 2)
        for delivery in deliveries[:2]:
            for ref in (delivery.obligation_ref, *delivery.layer_obligation_refs):
                self.assertIn((ref, Stage.PREPARATION_CANCELLED), fixture.stages)
                self.assertNotIn((ref, Stage.PREPARED), fixture.stages)
        for packet in fixture.queue.drain():
            self.assertTrue(all(any(ref is source for source in
                (packet.delivery.obligation_ref, *packet.delivery.layer_obligation_refs))
                for ref in packet.active_obligation_refs))

    def test_barriers_flush_admission_order_before_grid_a_b_a_or_quality_reset(self):
        fixture = make_ordered_fixture(count=1)
        first = fixture.delivery(1, 1)
        grid_b = first.bundle.frequencies_hz.copy()
        # Same full physical coverage with a different valid interior grid.
        grid_b[1:-1] += .0001
        grid_b.setflags(write=False)
        # Irregular density/Waterfall grid is mandatory-invalid, not hidden.
        bad = fixture.delivery(1, 126, grid=grid_b)
        offered = []
        original = fixture.queue.offer
        with patch.object(fixture.queue, "offer", side_effect=lambda packet: (offered.append(packet.delivery), original(packet))[1]):
            with self.assertRaises(PaneDiagnosticError):
                fixture.poll("resource-1", (first, bad, fixture.delivery(1, 252)))
        self.assertEqual(offered, [first])
        fixture = make_ordered_fixture(count=1)
        deliveries = (fixture.delivery(1, 1), fixture.delivery(1, 126, quality=8192), fixture.delivery(1, 252))
        with patch.object(fixture.preparer, "materialize_rtbw_display", wraps=fixture.preparer.materialize_rtbw_display) as convert:
            fixture.poll("resource-1", deliveries)
            self.assertEqual(convert.call_count, 3)

    def test_valid_grid_a_b_a_is_not_erased_and_old_pending_cannot_cross_reset(self):
        fixture = make_ordered_fixture(count=1)
        first = fixture.delivery(1, 1)
        grid_b = first.bundle.frequencies_hz + .0001
        grid_b.setflags(write=False)
        deliveries = (first, fixture.delivery(1, 126, grid=grid_b), fixture.delivery(1, 252))
        offered = []
        original = fixture.queue.offer
        with patch.object(fixture.queue, "offer", side_effect=lambda packet: (offered.append(packet.delivery), original(packet))[1]):
            fixture.poll("resource-1", deliveries)
        self.assertEqual(offered, list(deliveries))
        pending = fixture.preparer.validate_rtbw_display(fixture.delivery(1, 378))
        fixture.preparer.clear_resource("resource-1")
        with self.assertRaisesRegex(ValueError, "cache reset"):
            fixture.preparer.materialize_rtbw_display(pending)

    def test_wrong_source_is_not_hidden_and_stale_input_still_prepares(self):
        fixture = make_ordered_fixture(count=1)
        first, bad, tail = (fixture.delivery(1, seq) for seq in (1, 126, 252))
        foreign = replace(bad.bundle.spectrum, source_id="foreign-source", detector_ready=None)
        bad = replace(bad, bundle=replace(bad.bundle, spectrum=foreign, identity=None, persistence=None))
        with self.assertRaises(PaneDiagnosticError):
            fixture.poll("resource-1", (first, bad, tail))
        fixture = make_ordered_fixture(count=1)
        deliveries = (fixture.delivery(1, 126), fixture.delivery(1, 1), fixture.delivery(1, 252))
        with patch.object(fixture.preparer, "validate_rtbw_display", wraps=fixture.preparer.validate_rtbw_display) as validate:
            fixture.poll("resource-1", deliveries)
            self.assertEqual(validate.call_count, 3)
        # Stale input was not silently selected away: its barrier causes real
        # queue rejection after materialization, using the original queue rule.
        self.assertEqual(fixture.queue.metrics().stale_rejected, 1)

    def test_prior_queued_or_drained_poll_stale_refs_reach_original_queue_rejection(self):
        for drained in (False, True):
            with self.subTest(drained=drained):
                fixture = make_ordered_fixture(count=1)
                # Admit original low-sequence custody before the later native
                # offer, then deliver it out of order. Never forge/rebind refs
                # or weaken the real ledger's admission high-water guard.
                stale = fixture.delivery(1, 1, refs=True)
                prior = fixture.delivery(1, 126, refs=True)
                latest = fixture.delivery(1, 252, refs=True)
                fixture.poll("resource-1", (prior,))
                if drained:
                    self.assertIs(fixture.queue.drain()[0].delivery, prior)
                metrics, pending = fixture.queue.metrics(), fixture.queue.pending_count
                stages_before = len(fixture.stages)
                self.assertTrue(fixture.queue.rtbw_delivery_is_stale(stale))
                self.assertFalse(fixture.queue.rtbw_delivery_is_stale(latest))
                self.assertEqual(fixture.queue.metrics(), metrics)
                self.assertEqual(fixture.queue.pending_count, pending)
                self.assertEqual(len(fixture.stages), stages_before)
                original = fixture.preparer.validate_rtbw_display
                with patch.object(fixture.preparer, "validate_rtbw_display", wraps=original) as validate:
                    fixture.poll("resource-1", (stale, latest))
                    self.assertEqual(validate.call_count, 2)
                    self.assertIs(validate.call_args_list[0].args[0], stale)
                for ref in (stale.obligation_ref, *stale.layer_obligation_refs):
                    # Exact original ref, exact disposition sequence, including
                    # real PREPARED despite queue rejection; no early cancel.
                    actual = [stage for source, stage in fixture.stages if source is ref]
                    self.assertEqual(actual, [Stage.PREPARING, Stage.PREPARED, Stage.QUEUE_REJECTED])
                self.assertEqual(fixture.queue.metrics().stale_rejected, 1)
                packet = fixture.queue.drain()[0]
                self.assertIs(packet.delivery, latest)
                self.assertEqual(fixture.workers["resource-1"].snapshot().display_preparation_cancelled, 0)
                self.assertEqual(fixture.ledger.snapshot().accounting_failures, 0)

    def test_each_invalid_host_run_fails_before_a_following_valid_input(self):
        for host_run in (True, -1, 1.0, "1", None):
            with self.subTest(host_run=host_run):
                fixture = make_ordered_fixture(count=1)
                invalid = replace(fixture.delivery(1, 1), host_run_serial=host_run)
                valid = fixture.delivery(1, 126, refs=True)
                for prepare in (fixture.preparer.prepare, fixture.preparer.validate_rtbw_display):
                    with self.assertRaisesRegex(ValueError, "explicit non-negative host run serial"):
                        prepare(invalid)
                original = fixture.preparer.validate_rtbw_display
                with patch.object(fixture.preparer, "validate_rtbw_display", wraps=original) as validate:
                    with self.assertRaises(PaneDiagnosticError):
                        fixture.poll("resource-1", (invalid, valid))
                    self.assertEqual(validate.call_count, 1)
                    self.assertIs(validate.call_args.args[0], invalid)
                self.assertEqual(fixture.queue.pending_count, 0)
                for ref in (valid.obligation_ref, *valid.layer_obligation_refs):
                    self.assertIn((ref, Stage.ADMISSION_CANCELLED), fixture.stages)
                    self.assertNotIn((ref, Stage.PREPARING), fixture.stages)
                self.assertEqual(fixture.ledger.snapshot().accounting_failures, 0)

    def test_deferred_host_guard_retains_original_geometry_error_priority(self):
        fixture = make_ordered_fixture(count=1)
        grid = fixture.delivery(1, 1).bundle.frequencies_hz.copy()
        grid[1:-1] += .0001
        grid.setflags(write=False)
        invalid = replace(fixture.delivery(1, 126, grid=grid), host_run_serial=True)
        for prepare in (fixture.preparer.prepare, fixture.preparer.validate_rtbw_display):
            with self.assertRaisesRegex(ValueError, "regular physical grid"):
                prepare(invalid)

    def test_unavailable_queue_order_does_not_mask_original_layer_validation(self):
        for irregular in (False, True):
            with self.subTest(irregular=irregular):
                fixture = make_ordered_fixture(count=1)
                grid = None
                if irregular:
                    grid = fixture.delivery(1, 1).bundle.frequencies_hz.copy()
                    grid[1:-1] += .0001
                    grid.setflags(write=False)
                delivery = fixture.delivery(1, 1, grid=grid, density=False)
                frame = replace(delivery.bundle.spectrum, acquisition_epoch=None, detector_ready=None)
                delivery = replace(delivery, bundle=replace(delivery.bundle, spectrum=frame,
                    acquisition_epoch=None, identity=None))
                tail = fixture.delivery(1, 126, refs=True)
                with patch.object(fixture.preparer, "validate_rtbw_display",
                                  wraps=fixture.preparer.validate_rtbw_display) as validate:
                    with self.assertRaises(PaneDiagnosticError) as failure:
                        fixture.poll("resource-1", (delivery, tail))
                    self.assertEqual(validate.call_count, 1)
                self.assertIs(failure.exception.failure.stage,
                              PaneFailureStage.PREPARE if irregular else PaneFailureStage.QUEUE)
                for ref in (tail.obligation_ref, *tail.layer_obligation_refs):
                    self.assertIn((ref, Stage.ADMISSION_CANCELLED), fixture.stages)

    def test_malformed_activation_hint_preserves_layer_or_final_queue_error_phase(self):
        for drained in (False, True):
            for irregular in (False, True):
                for batch in (False, True):
                    with self.subTest(drained=drained, irregular=irregular, batch=batch):
                        fixture = make_ordered_fixture(count=1)
                        grid = None
                        if irregular:
                            grid = fixture.delivery(1, 1).bundle.frequencies_hz.copy()
                            grid[1:-1] += .0001
                            grid.setflags(write=False)
                        malformed = replace(fixture.delivery(1, 1, grid=grid), host_activation_serial="1")
                        prior = fixture.delivery(1, 126, refs=True)
                        tail = fixture.delivery(1, 252, refs=True)
                        fixture.poll("resource-1", (prior,))
                        if drained:
                            self.assertIs(fixture.queue.drain()[0].delivery, prior)
                        before_metrics = fixture.queue.metrics()
                        with patch.object(fixture.preparer, "prepare", wraps=fixture.preparer.prepare) as prepare, \
                             patch.object(fixture.preparer, "validate_rtbw_display",
                                          wraps=fixture.preparer.validate_rtbw_display) as validate, \
                             patch.object(fixture.queue, "offer", wraps=fixture.queue.offer) as offer:
                            with self.assertRaises(PaneDiagnosticError) as failure:
                                fixture.poll("resource-1", (malformed, tail) if batch else (malformed,))
                            self.assertEqual(prepare.call_count, 0 if batch else 1)
                            self.assertEqual(validate.call_count, 1 if batch else 0)
                            selected = validate if batch else prepare
                            self.assertIs(selected.call_args.args[0], malformed)
                            self.assertEqual(offer.call_count, 0 if irregular else 1)
                            if not irregular:
                                self.assertIs(offer.call_args.args[0].delivery, malformed)
                        self.assertIs(failure.exception.failure.stage,
                                      PaneFailureStage.PREPARE if irregular else PaneFailureStage.QUEUE)
                        self.assertEqual(fixture.queue.metrics(), before_metrics)
                        for ref in (tail.obligation_ref, *tail.layer_obligation_refs):
                            if batch:
                                self.assertIn((ref, Stage.ADMISSION_CANCELLED), fixture.stages)
                            self.assertNotIn((ref, Stage.PREPARING), fixture.stages)
                        self.assertEqual(fixture.ledger.snapshot().accounting_failures, 0)

    def test_unexpected_stale_hint_exception_remains_diagnostic_failure(self):
        fixture = make_ordered_fixture(count=1)
        first, tail = (fixture.delivery(1, sequence, refs=True) for sequence in (1, 126))
        with patch.object(fixture.queue, "rtbw_delivery_is_stale", side_effect=RuntimeError("unexpected hint fault")), \
             patch.object(fixture.preparer, "validate_rtbw_display", wraps=fixture.preparer.validate_rtbw_display) as validate:
            with self.assertRaises(PaneDiagnosticError) as failure:
                fixture.poll("resource-1", (first, tail))
            self.assertEqual(validate.call_count, 0)
        self.assertIs(failure.exception.failure.stage, PaneFailureStage.PREPARE)
        for ref in (first.obligation_ref, *first.layer_obligation_refs):
            self.assertIn((ref, Stage.PREPARATION_FAILED), fixture.stages)
        for ref in (tail.obligation_ref, *tail.layer_obligation_refs):
            self.assertIn((ref, Stage.ADMISSION_CANCELLED), fixture.stages)

    def test_paired_shared_gap_flushes_both_and_leaves_independent_peer_cache(self):
        fixture = make_ordered_fixture(topology="paired")
        fixture.poll("resource-3", (fixture.delivery(3, 1),))
        peer_grid = fixture.preparer._grids["pane-3"].baseline
        deliveries = (fixture.delivery(1, 1), fixture.delivery(2, 1),
                      fixture.delivery(1, 126, sync=2, gaps=1), fixture.delivery(2, 126, sync=2, gaps=1))
        offered = []
        original = fixture.queue.offer
        with patch.object(fixture.queue, "offer", side_effect=lambda packet: (offered.append(packet.delivery), original(packet))[1]):
            fixture.poll("resource-1", deliveries)
        self.assertEqual(offered, list(deliveries))
        self.assertIs(fixture.preparer._grids["pane-3"].baseline, peer_grid)

    def test_failure_disposes_pending_failed_and_untouched_tail_without_fake_prepared(self):
        fixture = make_ordered_fixture(count=1)
        deliveries = tuple(fixture.delivery(1, seq, refs=True) for seq in (1, 126, 252))
        original = fixture.preparer.validate_rtbw_display
        def fail_current(delivery):
            if delivery is deliveries[1]:
                raise ValueError("injected validation failure")
            return original(delivery)
        with patch.object(fixture.preparer, "validate_rtbw_display", side_effect=fail_current):
            with self.assertRaises(PaneDiagnosticError):
                fixture.poll("resource-1", deliveries)
        for delivery, stage in zip(deliveries, (Stage.PREPARATION_CANCELLED, Stage.PREPARATION_FAILED, Stage.ADMISSION_CANCELLED)):
            for ref in (delivery.obligation_ref, *delivery.layer_obligation_refs):
                self.assertIn((ref, stage), fixture.stages)
                self.assertNotIn((ref, Stage.PREPARED), fixture.stages)
        self.assertEqual(fixture.queue.pending_count, 0)

    def test_stop_during_validation_or_materialization_cancels_without_publish(self):
        for method in ("validate_rtbw_display", "materialize_rtbw_display"):
            with self.subTest(method=method):
                fixture = make_ordered_fixture(count=1)
                worker = fixture.workers["resource-1"]
                worker._launched = True  # Only enables the original Stop command slot; no thread starts.
                original = getattr(fixture.preparer, method)
                futures = []
                def stop_inside(item):
                    result = original(item)
                    futures.append(worker.request_stop())
                    return result
                deliveries = tuple(fixture.delivery(1, seq, refs=True) for seq in (1, 126))
                with patch.object(fixture.preparer, method, side_effect=stop_inside):
                    fixture.poll("resource-1", deliveries)
                self.assertEqual(fixture.queue.pending_count, 0)
                self.assertIs(worker._pending_stop, futures[0])
                self.assertFalse(futures[0].done())
                self.assertFalse(any(stage is Stage.PREPARED for _, stage in fixture.stages))
                self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_single_delivery_legacy_fast_path_preserves_same_density_cache(self):
        fixture = make_ordered_fixture(count=1)
        first = fixture.delivery(1, 1)
        next_delivery = fixture.delivery(1, 126)
        next_delivery = replace(next_delivery, bundle=replace(next_delivery.bundle, persistence=first.bundle.persistence))
        with patch.object(fixture.preparer, "validate_rtbw_display", side_effect=AssertionError("no selection opportunity")), \
             patch.object(analyzer_layer_cache, "persistence_density_from_native", wraps=persistence_density_from_native) as convert:
            fixture.poll("resource-1", (first,))
            first_density = fixture.queue.drain()[0].persistence
            fixture.poll("resource-1", (next_delivery,))
            self.assertIs(fixture.queue.drain()[0].persistence, first_density)
            self.assertEqual(convert.call_count, 1)

    def test_missing_optional_density_and_queue_failure_only_ready_refs(self):
        fixture = make_ordered_fixture(count=1)
        deliveries = tuple(fixture.delivery(1, seq, refs=True, scale=float("nan")) for seq in (1, 126))
        with patch.object(fixture.queue, "offer", side_effect=ValueError("queue refused")):
            with self.assertRaises(PaneDiagnosticError):
                fixture.poll("resource-1", deliveries)
        density_ref = deliveries[-1].layer_obligation_refs[-1]
        self.assertIn((density_ref, Stage.PREPARATION_CANCELLED), fixture.stages)
        self.assertNotIn((density_ref, Stage.QUEUE_FAILED), fixture.stages)
        self.assertIn((deliveries[-1].obligation_ref, Stage.QUEUE_FAILED), fixture.stages)

    def test_bounded_scaled_validation_matches_original_classification(self):
        fixture = make_ordered_fixture(count=1)
        frame = fixture.delivery(1, 1).bundle.persistence
        for raw, scale in ((-.1, 1), (float("nan"), 1), (float("inf"), 1), (2, 1), (.5, 2), (.25, 1)):
            with self.subTest(raw=raw, scale=scale):
                density = np.full((256, 4096), raw, np.float32)
                density.setflags(write=False)
                changed = replace(frame, density=density, probability_scale=scale)
                self.assertEqual(validate_native_persistence_display(changed, fixture.budget),
                                 persistence_density_from_native(changed) is not None)
        self.assertEqual(fixture.budget.snapshot().reserved_bytes, 0)

    def test_invalid_scaled_input_and_geometry_error_priority_even_if_replaced(self):
        fixture = make_ordered_fixture(count=1)
        first = fixture.delivery(1, 1)
        # A finite probability >1 is an unavailable optional layer, not fatal.
        invalid = fixture.delivery(1, 126, scale=1024)
        last = fixture.delivery(1, 252)
        with patch.object(fixture.preparer, "validate_rtbw_display", wraps=fixture.preparer.validate_rtbw_display) as validate:
            fixture.poll("resource-1", (first, invalid, last))
            self.assertEqual(validate.call_count, 3)
        frame = first.bundle.persistence
        grid = frame.frequencies_hz.copy()
        grid[2] += 100
        grid.setflags(write=False)
        geometry_bad = replace(frame, frequencies_hz=grid)
        with self.assertRaises(ValueError):
            validate_native_persistence_display(geometry_bad, fixture.budget)
        negative = np.full((256, 4096), -.5, np.float32)
        negative.setflags(write=False)
        density_bad = replace(geometry_bad, density=negative)
        self.assertFalse(validate_native_persistence_display(density_bad, fixture.budget))
        self.assertIsNone(persistence_density_from_native(density_bad))

    def test_source_admission_and_scratch_remain_inside_original_shared_budget(self):
        fixture = make_ordered_fixture(count=1, limit_bytes=1024)
        with self.assertRaises(PaneDiagnosticError):
            fixture.poll("resource-1", (fixture.delivery(1, 1), fixture.delivery(1, 126)))
        snapshot = fixture.budget.snapshot()
        self.assertEqual(snapshot.reserved_bytes, 0)
        self.assertLessEqual(snapshot.peak_bytes, snapshot.limit_bytes)
        self.assertGreater(snapshot.rejections, 0)

    def test_progressive_and_terminal_sweep_use_original_full_preparation(self):
        groups = (group("resource-1", "rx-1"),)
        request = replace(pane("pane-1", "rx-1", 100e6, 103e6, ReceiverBindingMode.DEDICATED_PARALLEL),
                          profile_id="sweep")
        schedule = compile_pane_schedule(groups, (request,), {"sweep": profile(20e6)})
        layout = PaneLayout((PaneLayoutSlot(1, request),), schedule)
        preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget())
        queue = PaneFairDeliveryQueue(("pane-1",))
        binding = preparer.bindings["pane-1"]
        def readonly(values, dtype):
            result = np.asarray(values, dtype=dtype)
            result.setflags(write=False)
            return result
        progress = SweepProgressFrame("resource-1:source", 1, 7, 1, "dBFS/bin",
            readonly([100e6, 101e6, 102e6, 103e6], np.float64),
            readonly([-70, np.nan, np.nan, np.nan], np.float32),
            readonly([0, 4096, 4096, 4096], np.uint32),
            readonly([0, -1, -1, -1], np.int32), ((0, 11),), (1, 2, 3))
        terminal = SweepLineFrame(1, 7, 987654321, "resource-1:source", SweepLineState.COMPLETE,
            np.array([100e6, 101e6, 102e6, 103e6]), np.array([-70, -65, -60, -55]),
            np.zeros(4, np.uint16), np.arange(4, dtype=np.int32), (),
            ((0, 11), (1, 12), (2, 13), (3, 14)), (), "dBFS/bin")
        deliveries = tuple(PaneDelivery("resource-1", binding.capture_id, "rx-1", 1, binding.crop,
            AnalyzerFrameBundle(frame, None, None, 7, None), 0.0, 1) for frame in (progress, terminal))
        session = SimpleNamespace(poll_resource=lambda resource: deliveries)
        worker = _ResourceWorker(schedule.resources[0], ("pane-1",), session, preparer, queue, .01)
        with patch.object(preparer, "validate_rtbw_display", side_effect=AssertionError("Sweep must not defer")), \
             patch.object(preparer, "prepare", wraps=preparer.prepare) as prepare:
            worker._poll_and_advance()
            self.assertEqual(prepare.call_count, 2)
        packet = queue.drain()[0]
        self.assertIs(packet.delivery, deliveries[-1])
        self.assertTrue(packet.bundle.terminal_sweep)
        self.assertEqual(worker.snapshot().ordered_validated_publications, 0)


if __name__ == "__main__":
    unittest.main()
