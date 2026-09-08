"""APP-01 quality survives the native-shaped Python boundary, not only plots."""

from types import SimpleNamespace
from dataclasses import replace
import unittest

import numpy as np

from sdr_monitor.services.native_continuous_sweep import _to_domain_line
from sdr_monitor.domain.sweep_lines import SweepQualitySchema


class SweepQualityTests(unittest.TestCase):
    def test_complete_coverage_preserves_quality_and_immutable_copy(self):
        # Stable native wire bits: Uncalibrated, IqDropped, StitchOverlap.
        left, right, overlap = 1, 1 << 5, 1 << 11
        flags = np.array([left, left | right | overlap, right], dtype=np.uint32)
        native = SimpleNamespace(
            line_sequence=12, epoch=7, completed_ns=1001, source_id="mock-source",
            state="complete", gap_reasons=(), frequencies_hz=np.array([100., 101., 102.]),
            values=np.array([-90., -85., -80.]), quality_flags_per_bin=flags,
            source_segment_indices=np.array([0, 0, 1]), missing_segment_indices=(),
            segment_config_generations=((0, 11), (1, 12)), unit="dBFS/bin",
        )
        line = _to_domain_line(native)
        self.assertTrue(line.is_complete)
        self.assertEqual(line.aggregate_quality_flags, left | right | overlap)
        np.testing.assert_array_equal(line.quality_flags, flags)
        flags[:] = 0
        self.assertEqual(line.aggregate_quality_flags, left | right | overlap)
        self.assertFalse(line.quality_flags.flags.writeable)
        self.assertEqual(line.segment_config_generations, ((0, 11), (1, 12)))
        self.assertIs(line.quality_schema, SweepQualitySchema.NATIVE_V5)
        # Native missing coverage must still fail closed; preserving warning
        # bits must never turn MissingSegment into a complete measurement.
        with self.assertRaises(ValueError):
            replace(line, quality_flags=np.array([4096, 0, 0], dtype=np.uint16))
        # Historical reference masks retain their old meaning, explicitly.
        legacy = replace(line, quality_schema=SweepQualitySchema.REFERENCE_V1,
                         quality_flags=np.array([2, 4, 0], dtype=np.uint16))
        self.assertEqual(legacy.aggregate_quality_flags, 6)
        with self.assertRaises(ValueError):
            replace(legacy, quality_flags=np.array([1, 0, 0], dtype=np.uint16))
        with self.assertRaises(ValueError):
            replace(line, quality_schema="unknown")
        for invalid in (
            [-1, 0, 0], [65536, 0, 0], [0.5, 0, 0],
            [float("nan"), 0, 0], [True, False, False],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    replace(line, quality_flags=np.asarray(invalid))
                native.quality_flags_per_bin = np.asarray(invalid)
                with self.assertRaises(RuntimeError):
                    _to_domain_line(native)


if __name__ == "__main__":
    unittest.main()
