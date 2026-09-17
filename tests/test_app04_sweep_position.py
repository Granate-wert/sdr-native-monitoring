"""Producer-owned admission position through actual native binding and domain."""
from dataclasses import replace
import gc
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.sweep_acquisition import SweepSegmentPosition
from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.services.native_continuous_sweep import _to_domain_line, _to_domain_progress, _to_domain_position
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal


class SweepPositionTests(unittest.TestCase):
    def test_native_assembler_binding_preserves_reordered_admission_and_gaps(self):
        from sdr_monitor import _sdr_native as native
        raw = native._make_test_sweep_position_frames()
        self.assertEqual([frame.last_admitted_segment for frame in raw], [
            (1, 12, 104e6, 108e6), (0, 11, 100e6, 104e6), (0, 11, 100e6, 104e6), None,
        ])
        with self.assertRaises(AttributeError):
            raw[0].last_admitted_segment = None
        early = _to_domain_progress(raw[0])
        final, gap, empty = [_to_domain_line(frame) for frame in raw[1:]]
        del raw
        gc.collect()
        self.assertEqual(early.last_admitted_segment.segment_index, 1)
        self.assertEqual(final.last_admitted_segment.segment_index, 0)
        self.assertEqual(gap.last_admitted_segment, final.last_admitted_segment)
        self.assertIsNone(empty.last_admitted_segment)
        # Terminal wins its own stale preview; next pass owns a new position.
        snapshot = ContinuousSweepDisplaySnapshot(final, ContinuousSweepDisplayMetrics(), early)
        self.assertIs(snapshot.analyzer_bundle.spectrum, final)
        next_pass = replace(early, sequence=2)
        snapshot = replace(snapshot, progress=next_pass)
        self.assertIs(snapshot.analyzer_bundle.spectrum, next_pass)
        self.assertFalse(early.values_db.flags.writeable)

    def test_domain_rejects_pending_wrong_generation_and_untyped_positions(self):
        for invalid in (SweepSegmentPosition(1, 12, 101e6, 102e6),
                        SweepSegmentPosition(0, 99, 100e6, 101e6), (0, 11, 100e6, 101e6)):
            with self.subTest(invalid=invalid), self.assertRaises((TypeError, ValueError)):
                replace(progress(), last_admitted_segment=invalid)
        with self.assertRaises(ValueError):
            replace(terminal(gap=True), last_admitted_segment=SweepSegmentPosition(2, 13, 102e6, 103e6))
        position = SweepSegmentPosition(0, 11, 100e6, 101e6)
        self.assertIs(replace(progress(), last_admitted_segment=position).last_admitted_segment, position)

    def test_no_fallback_from_order_or_time_and_invalid_wire_shapes_refused(self):
        self.assertIsNone(_to_domain_position(SimpleNamespace()))
        self.assertIsNone(_to_domain_position(SimpleNamespace(last_admitted_segment=None)))
        for invalid in ([0, 11, 1, 2], (0, 11, 1), "0"):
            with self.assertRaises(ValueError):
                _to_domain_position(SimpleNamespace(last_admitted_segment=invalid))
        for values in ((True, 11, 1, 2), (0, 0, 1, 2), (0, 11, 2, 1),
                       (0, 11, float("nan"), 2), (0, 11, 1, float("inf"))):
            with self.assertRaises(ValueError):
                SweepSegmentPosition(*values)
