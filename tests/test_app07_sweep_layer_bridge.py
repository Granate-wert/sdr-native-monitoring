"""Sweep sidecar/domain/converter qualification; not automatic owner/HIL proof."""
from dataclasses import FrozenInstanceError, replace
import importlib.util
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.analytical_ready import ReadyClockMapping
from sdr_monitor.domain.layer_ready import LayerReadyKind, LayerReadyReceipt, SweepLayerIdentity
from sdr_monitor.services.layer_ready_admission import admit_layer_ready, LayerReadyAdmissionState
from sdr_monitor.services.native_continuous_sweep import _to_domain_line, _to_domain_progress
from sdr_monitor.services.native_ready_bridge import NativeReadyBridge
from tests.native_test_dependencies import native_test_dll_directory
from tests.test_app07_layer_ready_admission import progress_frame, receipt, terminal_frame


def identity(frame):
    if hasattr(frame, "revision"):
        return SweepLayerIdentity(frame.source_id, frame.epoch, frame.sequence, frame.revision,
            frame.acquired_segment_generations, frame.pending_segment_indices, frame.receiver_id)
    missing = frozenset(frame.missing_segment_indices)
    return SweepLayerIdentity(frame.source_id, frame.epoch, frame.sequence, None,
        tuple(pair for pair in frame.segment_config_generations if pair[0] not in missing),
        frame.missing_segment_indices, frame.receiver_id)


class SweepLayerSidecarTests(unittest.TestCase):
    def test_progress_terminal_gap_keep_distinct_exact_coverage_and_receiver(self):
        for base in (progress_frame(), terminal_frame(), terminal_frame(True)):
            for receiver in (None, "RX1", "RX2"):
                frame = replace(base, receiver_id=receiver)
                kind = LayerReadyKind.SWEEP_PROGRESS if hasattr(frame, "revision") else LayerReadyKind.SWEEP_TERMINAL
                ref = receipt(kind, identity(frame))
                result = replace(frame, layer_ready=ref)
                self.assertIs(result.layer_ready, ref)
                self.assertIs(admit_layer_ready(result, ref).state, LayerReadyAdmissionState.MATCHED)
                np.testing.assert_array_equal(result.values_db, base.values_db)
                np.testing.assert_array_equal(result.quality_flags, base.quality_flags)
                self.assertFalse(result.values_db.flags.writeable)
                with self.assertRaises(FrozenInstanceError):
                    result.layer_ready = None
                for changes in (dict(receiver_id="RX2" if receiver != "RX2" else "RX1"),
                                dict(source_id="different-source"), dict(epoch=frame.epoch + 1),
                                dict(sequence=frame.sequence + 1)):
                    with self.subTest(changes=changes), self.assertRaises(ValueError):
                        replace(result, **changes)

    def test_sidecar_cannot_rewrite_revision_coverage_or_be_swapped_with_terminal(self):
        frame = progress_frame()
        ref = receipt(identity=identity(frame))
        for key in (replace(ref.identity, acquired_segment_generations=((0, 99),)),
                    replace(ref.identity, pending_segment_indices=(2,))):
            with self.assertRaises(ValueError):
                replace(frame, layer_ready=replace(ref, identity=key))
        terminal = terminal_frame(True)
        terminal_ref = receipt(LayerReadyKind.SWEEP_TERMINAL, identity(terminal))
        with self.assertRaises(ValueError):
            replace(frame, layer_ready=terminal_ref)
        with self.assertRaises(ValueError):
            replace(terminal, layer_ready=ref)
        with self.assertRaises(ValueError):
            replace(terminal, layer_ready=replace(terminal_ref, identity=replace(terminal_ref.identity,
                acquired_segment_generations=((0, 99),))))

    def test_missing_evidence_adds_no_stamp_and_legacy_terminal_does_not_scan_coverage_twice(self):
        base = terminal_frame()
        with patch("sdr_monitor.domain.sweep_lines.validate_sweep_layer_receipt") as validate:
            result = replace(base)
        validate.assert_not_called()
        self.assertIsNone(result.layer_ready)
        self.assertIsNone(result.receiver_id)
        self.assertEqual(result.completed_at_ns, base.completed_at_ns)
        for invalid in ("RX0", "rx1", True):
            for frame in (base, progress_frame()):
                with self.assertRaises(ValueError):
                    replace(frame, receiver_id=invalid)

    def test_domain_coverage_still_supports_full_2048_segments_not_density_limit(self):
        frame = terminal_frame()
        coverage = tuple((index, index + 1) for index in range(2048))
        frame = replace(frame, segment_config_generations=coverage, receiver_id="RX1")
        ref = receipt(LayerReadyKind.SWEEP_TERMINAL, identity(frame))
        result = replace(frame, layer_ready=ref)
        self.assertEqual(len(result.layer_ready.identity.acquired_segment_generations), 2048)
        self.assertIs(admit_layer_ready(result, ref).receipt, ref)
        # Domain support is NOT the host-journal retention/whole-owner budget proof.


class CompiledSweepConverterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = os.environ.get("SDR_APP07_READY_NATIVE")
        if not selected:
            raise unittest.SkipTest("explicit matching native required, no SDK fallback")
        path = Path(selected).resolve(strict=True)
        scope = native_test_dll_directory(str(path))
        scope.__enter__()
        cls.addClassCleanup(scope.__exit__, None, None, None)
        # One pybind module per process. Re-importing the same file under a
        # second name registers SourceType twice, before any converter test.
        # Reuse ONLY the exact explicitly selected path, never a legacy SDK.
        cls.native = sys.modules.get("sdr_monitor._sdr_native")
        if cls.native is None:
            spec = importlib.util.spec_from_file_location("sdr_monitor._sdr_native", path)
            cls.native = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.native)
            sys.modules[spec.name] = cls.native
        assert Path(cls.native.__file__).resolve() == path

    def test_actual_native_creation_ref_through_progress_and_both_terminal_converters(self):
        native = self.native
        bridge = NativeReadyBridge(native)
        bridge.begin()
        frames = native._make_test_sweep_position_frames(layer_creation=True)
        bridge.sample()
        for index, raw in enumerate(frames):
            convert = _to_domain_progress if index == 0 else _to_domain_line
            base = convert(raw, receiver_id="RX2")
            original = raw.layer_ready
            kind = LayerReadyKind.SWEEP_PROGRESS if index == 0 else LayerReadyKind.SWEEP_TERMINAL
            mapping, bounds = bridge.map_native_clock(original)
            ref = LayerReadyReceipt(kind, identity(base), bridge.clock_scope_id, bridge.host_process_id,
                original.producer_instance_id, original.creation_sequence, original.ready_native_ns,
                mapping, bounds)
            result = convert(raw, receiver_id="RX2", layer_ready=ref)
            self.assertIs(result.layer_ready, ref)
            self.assertIs(ref.mapping, ReadyClockMapping.BOUNDED)
            self.assertEqual(ref.ready_native_ns, original.ready_native_ns)
            self.assertIsNone(ref.owner_run_id)  # fixture conversion != product SAME owner authentication
            self.assertIs(admit_layer_ready(result, ref).receipt, ref)
            np.testing.assert_array_equal(result.values_db, base.values_db)
            np.testing.assert_array_equal(result.quality_flags, base.quality_flags)
            self.assertEqual(result.segment_acquisition, base.segment_acquisition)
            self.assertEqual(result.last_admitted_segment, base.last_admitted_segment)
            if index:
                self.assertEqual(result.completed_at_ns, raw.completed_ns)
            with self.assertRaises((ValueError, RuntimeError)):
                convert(raw, receiver_id="RX1", layer_ready=ref)
            baseline = native.analytical_ready_clock_ns()
            for _ in range(10):
                self.assertIs(result.layer_ready, ref)
                self.assertEqual(result.layer_ready.ready_native_ns, original.ready_native_ns)
            self.assertGreaterEqual(native.analytical_ready_clock_ns(), baseline)


if __name__ == "__main__":
    unittest.main()
