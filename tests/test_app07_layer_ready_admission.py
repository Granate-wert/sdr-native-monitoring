"""Real domain frames, pure layer evidence checks; no native/SDK/paint proof."""
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.analytical_ready import ReadyClockBracket, ReadyClockMapping, ReadyHostBounds
from sdr_monitor.domain.layer_ready import (
    DensityLayerIdentity, LayerReadyKind, LayerReadyReceipt, MAX_LAYER_SEGMENTS, SweepLayerIdentity,
)
from sdr_monitor.domain.live import LivePersistenceFrame
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineGapReason, SweepLineState, SweepQualitySchema
from sdr_monitor.services.layer_ready_admission import (
    LayerReadyAdmission, LayerReadyAdmissionState as State, admit_layer_ready,
)
from tests.test_app01_sweep_progress import frame as progress_frame


def sweep_identity():
    frame = progress_frame()
    return SweepLayerIdentity(frame.source_id, frame.epoch, frame.sequence, frame.revision,
        frame.acquired_segment_generations, frame.pending_segment_indices)


def density_frame():
    return LivePersistenceFrame(
        update_sequence=4, timestamp_ns=999, source_frame_sequence=20,
        power_min_db=-120, power_max_db=0, power_bins=2, frequency_bins=2,
        processed_frames=30, exponential_decay=True,
        frequencies_hz=np.array([100., 101.]), density=np.array([[1., 2.], [3., 4.]], dtype=np.float32),
        probability_scale=0.25, count_scale=0.5, source_id="source", config_generation=7,
        unit="dBFS/bin", producer_identity_available=True, receiver_id="rx2",
        acquisition_epoch=5, clock_domain="host_steady_ns", accumulation_id="accumulation")


def density_identity(frame=None):
    frame = frame if frame is not None else density_frame()
    return DensityLayerIdentity(frame.source_id, frame.config_generation, frame.update_sequence,
        frame.source_frame_sequence, frame.accumulation_id, frame.receiver_id, frame.acquisition_epoch)


def terminal_frame(gap=False):
    frame = progress_frame()
    return SweepLineFrame(sequence=frame.sequence, epoch=frame.epoch, completed_at_ns=123,
        source_id=frame.source_id, state=SweepLineState.GAP if gap else SweepLineState.COMPLETE,
        frequencies_hz=frame.frequencies_hz,
        values_db=frame.values_db if gap else np.array([-80., -70.], dtype=np.float32),
        quality_flags=frame.quality_flags if gap else np.array([1, 1], dtype=np.uint32),
        source_segment_indices=frame.source_segment_indices if gap else np.array([0, 1], dtype=np.int32),
        missing_segment_indices=(1,) if gap else (),
        segment_config_generations=((0, 11), (1, 12)),
        gap_reasons=(SweepLineGapReason.MISSING_SEGMENT,) if gap else (), unit="dBFS/bin",
        quality_schema=SweepQualitySchema.NATIVE_V5)


def receipt(kind=LayerReadyKind.SWEEP_PROGRESS, identity=None, **changes):
    return LayerReadyReceipt(kind, identity if identity is not None else sweep_identity(),
        "clock-scope", 123, 4, 7, 150, ReadyClockMapping.OUTSIDE_SAMPLES, **changes)


class LayerReadyContractTests(unittest.TestCase):
    def test_immutable_scalar_identity_keeps_coverage_tuple(self):
        key = sweep_identity()
        frame = progress_frame()
        actual = SweepLayerIdentity(frame.source_id, frame.epoch, frame.sequence, frame.revision,
            frame.acquired_segment_generations, frame.pending_segment_indices)
        self.assertEqual(actual, key)
        self.assertIs(actual.acquired_segment_generations, frame.acquired_segment_generations)
        with self.assertRaises(FrozenInstanceError):
            actual.epoch = 4

    def test_progress_coverage_refuses_bool_unresolved_mutable_duplicate_overlap(self):
        key = sweep_identity()
        changes = (
            dict(epoch=True), dict(line_sequence=-1), dict(revision=2),
            dict(source_id="unknown"), dict(acquired_segment_generations=((0, 0),)),
            dict(acquired_segment_generations=[(0, 11)]),
            dict(acquired_segment_generations=((0, 11), (0, 12))),
            dict(pending_segment_indices=(0,)), dict(pending_segment_indices=()),
            dict(pending_segment_indices=(2, 1)), dict(pending_segment_indices=(True,)),
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(key, **change)

    def test_terminal_gap_and_complete_use_distinct_exact_coverage(self):
        complete = SweepLayerIdentity("source", 0, 0, None, ((0, 1), (1, 2)), ())
        gap = SweepLayerIdentity("source", 0, 0, None, ((0, 1),), (1,))
        empty_gap = SweepLayerIdentity("source", 0, 0, None, (), (0, 1))
        self.assertNotEqual(complete, gap)
        self.assertEqual(empty_gap.acquired_segment_generations, ())
        self.assertFalse(hasattr(complete, "config_generation"))

    def test_coverage_has_exact_capacity_and_no_unbounded_allocation(self):
        key = SweepLayerIdentity("source", 0, 0, None, (), tuple(range(MAX_LAYER_SEGMENTS)))
        self.assertEqual(len(key.pending_segment_indices), MAX_LAYER_SEGMENTS)
        with self.assertRaises(ValueError):
            replace(key, pending_segment_indices=tuple(range(MAX_LAYER_SEGMENTS + 1)))

    def test_layer_variants_do_not_accept_detector_or_other_layer_keys(self):
        density = density_identity()
        for kind, key in ((LayerReadyKind.DENSITY, sweep_identity()),
                          (LayerReadyKind.SWEEP_PROGRESS, density),
                          (LayerReadyKind.SWEEP_TERMINAL, sweep_identity())):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                receipt(kind, key)
        with self.assertRaises(ValueError):
            receipt("sweep_progress")

    def test_density_identity_refuses_unknown_generation_chain_and_accumulation(self):
        key = density_identity()
        for change in (dict(config_generation=0), dict(update_sequence=True),
                       dict(source_frame_sequence=-1), dict(accumulation_id="unknown"),
                       dict(receiver_id="rx\x00"), dict(acquisition_epoch=0)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(key, **change)

    def test_measured_bounds_not_frame_timestamp_or_extrapolation(self):
        bounds = ReadyHostBounds(ReadyClockBracket(100, 1000, 1010), ReadyClockBracket(200, 1100, 1110))
        value = replace(receipt(), mapping=ReadyClockMapping.BOUNDED, host_bounds=bounds)
        self.assertEqual(value.ready_native_ns, 150)
        for timestamp in (100, 200, 999):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                replace(value, ready_native_ns=timestamp)
        with self.assertRaises(ValueError):
            replace(value, mapping=ReadyClockMapping.PROBE_FAILED)
        for mapping in ReadyClockMapping:
            if mapping is not ReadyClockMapping.BOUNDED:
                self.assertIsNone(replace(receipt(), mapping=mapping).host_bounds)

    def test_receipt_requires_exact_positive_identity_and_signed_clock_range(self):
        for change in (dict(host_process_id=0), dict(producer_instance_id=True),
                       dict(creation_sequence=0), dict(adapter_clock_scope_id=" scope "),
                       dict(ready_native_ns=1 << 63), dict(mapping="bounded")):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(receipt(), **change)
        self.assertEqual(replace(receipt(), ready_native_ns=-(1 << 63)).ready_native_ns, -(1 << 63))


class LayerReadyAdmissionTests(unittest.TestCase):
    def test_missing_native_evidence_never_uses_completion_timestamp(self):
        for frame in (progress_frame(), terminal_frame(), density_frame()):
            with self.subTest(frame=type(frame).__name__):
                value = admit_layer_ready(frame, None)
                self.assertIs(value.state, State.MISSING)
                self.assertIsNone(value.receipt)

    def test_progress_matches_identity_but_not_owner_or_paint(self):
        frame, ref = progress_frame(), receipt()
        value = admit_layer_ready(frame, ref)
        self.assertIs(value.state, State.MATCHED)
        self.assertIs(value.receipt, ref)
        self.assertIn("not_owner_authenticated", value.state.value)
        for change in (dict(source_id="peer"), dict(epoch=4), dict(line_sequence=2),
                       dict(acquired_segment_generations=((0, 12),))):
            with self.subTest(change=change):
                self.assertIs(admit_layer_ready(frame, replace(ref, identity=replace(ref.identity, **change))).state,
                              State.REFUSED)

    def test_complete_and_gap_match_without_terminal_progress_conversion(self):
        for gap in (False, True):
            frame = terminal_frame(gap)
            acquired = ((0, 11),) if gap else ((0, 11), (1, 12))
            key = SweepLayerIdentity(frame.source_id, frame.epoch, frame.sequence, None,
                acquired, frame.missing_segment_indices)
            ref = receipt(LayerReadyKind.SWEEP_TERMINAL, key)
            self.assertIs(admit_layer_ready(frame, ref).receipt, ref)
            self.assertIs(admit_layer_ready(frame, receipt()).state, State.REFUSED)

    def test_density_matches_original_update_generation_accumulation_and_rx(self):
        frame = density_frame()
        ref = receipt(LayerReadyKind.DENSITY, density_identity(frame))
        self.assertIs(admit_layer_ready(frame, ref).receipt, ref)
        for change in (dict(config_generation=8), dict(update_sequence=5),
                       dict(source_frame_sequence=21), dict(accumulation_id="new"),
                       dict(receiver_id="rx1"), dict(acquisition_epoch=6)):
            with self.subTest(change=change):
                self.assertIs(admit_layer_ready(frame, replace(ref, identity=replace(ref.identity, **change))).state,
                              State.REFUSED)
        for frame_change in (dict(producer_identity_available=False), dict(accumulation_id=None)):
            self.assertIs(admit_layer_ready(replace(frame, **frame_change), ref).state, State.REFUSED)

    def test_no_fake_frame_or_detector_shaped_object_is_layer_evidence(self):
        self.assertIs(admit_layer_ready(SimpleNamespace(), receipt()).state, State.REFUSED)
        self.assertIs(admit_layer_ready(progress_frame(), SimpleNamespace(ready_native_ns=150)).state, State.REFUSED)

    def test_admission_is_inert_and_keeps_arrays_and_time_provenance(self):
        frame = density_frame()
        image, frequencies = frame.density, frame.frequencies_hz
        ref = receipt(LayerReadyKind.DENSITY, density_identity(frame))
        with patch("time.monotonic_ns", side_effect=AssertionError("no clock probe")), \
             patch("time.monotonic", side_effect=AssertionError("no clock probe")):
            for _ in range(20):
                self.assertIs(admit_layer_ready(frame, ref).receipt, ref)
        self.assertIs(frame.density, image)
        self.assertIs(frame.frequencies_hz, frequencies)
        self.assertEqual((frame.timestamp_ns, frame.probability_scale, frame.count_scale), (999, 0.25, 0.5))

    def test_refused_and_missing_cannot_retain_unqualified_receipts(self):
        for state in (State.MISSING, State.REFUSED):
            for value in (receipt(), object()):
                with self.subTest(state=state), self.assertRaises(ValueError):
                    LayerReadyAdmission(state, value)
        with self.assertRaises(ValueError):
            LayerReadyAdmission(State.MATCHED)
