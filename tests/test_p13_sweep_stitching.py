from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest

import numpy as np

from esw_dfl.domain import SourceDescriptor
from esw_dfl.sdr.contracts import (
    CalibrationStatus,
    DetectorType,
    PrecisionMode,
    QualityFlag,
    SourceType,
    SpectrumFrame,
    SpectrumUnit,
    SweepConfig,
    WindowType,
)
from esw_dfl.sdr.sweep import (
    SweepExecutionResult,
    SweepExecutionStatus,
    SweepSegmentResult,
    SweepSegmentStatus,
    SweepTiming,
    plan_sweep,
)
from esw_dfl.sdr.stitching import SweepStitchError, SweepStitchOptions, stitch_sweep
from esw_dfl.sdr.session_adapter import sweep_trace_from_frame


def sweep_plan():
    return plan_sweep(SweepConfig(
        start_frequency_hz=100.0,
        stop_frequency_hz=118.0,
        sample_rate_hz=16.0,
        analog_bandwidth_hz=12.0,
        overlap_hz=2.0,
        fft_size=256,
        hop_size=128,
        dwell_frames=1,
    ))


def frame_for(segment, value_db: float, *, unit=SpectrumUnit.DBFS_BIN, calibration=CalibrationStatus.UNCALIBRATED, profile=None, generation=7, spacing_shift=0.0):
    fft_size = segment.fft_size
    rate = segment.sample_rate_hz
    frequencies = (
        segment.center_frequency_hz - rate / 2.0
        + np.arange(fft_size, dtype=np.float64) * rate / fft_size
        + spacing_shift * np.arange(fft_size, dtype=np.float64)
    )
    return SpectrumFrame(
        source=SourceDescriptor(SourceType.SYNTHETIC, "p13-test", "P13 test", uri="synthetic:p13"),
        frame_sequence=segment.segment_index,
        first_sample_index=0,
        timestamp_ns=1_700_000_000_000_000_000 + segment.segment_index,
        config_generation=generation,
        center_frequency_hz=segment.center_frequency_hz,
        sample_rate_hz=rate,
        analog_bandwidth_hz=segment.analog_bandwidth_hz,
        fft_bin_width_hz=rate / fft_size,
        enbw_hz=rate / fft_size,
        nominal_rbw_hz=rate / fft_size,
        fft_size=fft_size,
        hop_size=segment.hop_size,
        window=WindowType.HANN,
        detector=DetectorType.SAMPLE,
        precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
        unit=unit,
        frequencies_hz=frequencies,
        values=np.full(fft_size, value_db, dtype=np.float32),
        calibration_status=calibration,
        calibration_profile_id=profile,
        estimated_uncertainty_db=0.5,
        quality_flags=QualityFlag.NONE if calibration is CalibrationStatus.APPLIED else QualityFlag.UNCALIBRATED,
    )


def execution_for(values: tuple[float, ...], *, missing: set[int] | None = None, spacing_shift=0.0, unit=SpectrumUnit.DBFS_BIN, calibration=CalibrationStatus.UNCALIBRATED, profile=None):
    plan = sweep_plan()
    missing = missing or set()
    results = []
    for segment, value in zip(plan.segments, values):
        if segment.segment_index in missing:
            results.append(SweepSegmentResult(
                plan=segment,
                status=SweepSegmentStatus.MISSING,
                quality_flags=QualityFlag.MISSING_SEGMENT,
                error="test missing",
            ))
        else:
            results.append(SweepSegmentResult(
                plan=segment,
                status=SweepSegmentStatus.COMPLETED,
                applied_config=SimpleNamespace(config_generation=7),
                frames=(frame_for(
                    segment,
                    value,
                    unit=unit,
                    calibration=calibration,
                    profile=profile,
                    spacing_shift=spacing_shift if segment.segment_index else 0.0,
                ),),
                timing=SweepTiming(total_s=0.01),
                quality_flags=QualityFlag.NONE if calibration is CalibrationStatus.APPLIED else QualityFlag.UNCALIBRATED,
            ))
    return SweepExecutionResult(
        status=SweepExecutionStatus.COMPLETED,
        plan=plan,
        segments=tuple(results),
        started_ns=10,
        completed_ns=20,
        restored=True,
        sweep_id=42,
    )


class SweepStitchingTests(unittest.TestCase):
    def test_identical_segments_are_combined_in_power_with_overlap_quality(self) -> None:
        frame = stitch_sweep(execution_for((-30.0, -30.0)))
        present = np.isfinite(frame.values)
        self.assertTrue(np.allclose(frame.values[present], -30.0, atol=1.0e-4))
        self.assertTrue(np.any(frame.source_segment_count_per_bin > 1))
        self.assertTrue(np.any(frame.quality_flags_per_bin & np.uint16(QualityFlag.STITCH_OVERLAP)))
        self.assertEqual(frame.config_generation, 7)
        self.assertEqual(frame.sweep_id, 42)
        self.assertEqual(len(frame.seam_metrics), 1)
        self.assertLess(frame.seam_metrics[0].after_p95_db, 1.0e-6)
    def test_known_offset_is_corrected_in_linear_power(self) -> None:
        frame = stitch_sweep(execution_for((-30.0, -27.0)))
        present = np.isfinite(frame.values)
        self.assertTrue(np.allclose(frame.values[present], -30.0, atol=2.0e-3))
        self.assertAlmostEqual(frame.correction_db_by_segment[1], 3.0, places=3)
        self.assertAlmostEqual(frame.seam_metrics[0].before_p50_db, 3.0, places=3)
        self.assertLess(frame.seam_metrics[0].after_p95_db, 1.0e-5)

    def test_missing_segment_is_explicit_and_not_interpolated(self) -> None:
        frame = stitch_sweep(execution_for((-30.0, -30.0), missing={1}))
        missing_flag = np.uint16(QualityFlag.MISSING_SEGMENT)
        self.assertTrue(np.any(frame.quality_flags_per_bin & missing_flag))
        self.assertTrue(np.any(np.isnan(frame.values)))
        self.assertTrue(np.any(frame.source_segment_indices_per_bin == -1))
        self.assertTrue(np.all(frame.values[np.isfinite(frame.values)] <= -29.9))

    def test_nonuniform_grid_requires_explicit_target_spacing(self) -> None:
        execution = execution_for((-30.0, -30.0), spacing_shift=0.001)
        with self.assertRaises(SweepStitchError):
            stitch_sweep(execution)
        frame = stitch_sweep(execution, SweepStitchOptions(target_spacing_hz=0.0625))
        self.assertGreater(frame.frequencies_hz.size, 2)

    def test_calibrated_profile_and_per_bin_calibration_status_are_retained(self) -> None:
        frame = stitch_sweep(execution_for(
            (-30.0, -30.0),
            unit=SpectrumUnit.DBM_BIN,
            calibration=CalibrationStatus.APPLIED,
            profile="p13-profile",
        ))
        self.assertEqual(frame.unit, SpectrumUnit.DBM_BIN)
        self.assertEqual(frame.calibration_status, CalibrationStatus.APPLIED)
        self.assertEqual(frame.calibration_profile_id, "p13-profile")
        self.assertTrue(np.all(
            frame.calibration_status_per_bin == list(CalibrationStatus).index(CalibrationStatus.APPLIED)
        ))


    def test_trace_adapter_preserves_quality_and_seam_evidence(self) -> None:
        missing_frame = stitch_sweep(execution_for((-30.0, -27.0), missing={1}))
        missing_trace = sweep_trace_from_frame(missing_frame)
        self.assertEqual(missing_trace.name, "Stitched Full-span Sweep")
        self.assertEqual(missing_trace.metadata["sweep_id"], 42)
        self.assertGreater(missing_trace.metadata["missing_bins"], 0)
        self.assertFalse(missing_trace.metadata["quality_flags_per_bin"].flags.writeable)
        self.assertFalse(missing_trace.metadata["uncertainty_db_per_bin"].flags.writeable)
        seam_trace = sweep_trace_from_frame(stitch_sweep(execution_for((-30.0, -27.0))))
        self.assertEqual(seam_trace.metadata["seam_count"], 1)
        self.assertGreater(seam_trace.metadata["overlap_bins"], 0)

    def test_mixed_units_are_rejected(self) -> None:
        plan = sweep_plan()
        first = execution_for((-30.0, -30.0)).segments[0]
        second = replace(
            execution_for((-30.0, -30.0)).segments[1],
            frames=(frame_for(plan.segments[1], -30.0, unit=SpectrumUnit.DBM_BIN, calibration=CalibrationStatus.APPLIED, profile="p"),),
        )
        execution = replace(
            execution_for((-30.0, -30.0)),
            segments=(first, second),
        )
        with self.assertRaises(SweepStitchError):
            stitch_sweep(execution)


if __name__ == "__main__":
    unittest.main()