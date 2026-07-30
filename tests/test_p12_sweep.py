from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from types import SimpleNamespace
import unittest

import numpy as np

from esw_dfl.domain import SourceDescriptor
from esw_dfl.sdr.contracts import (
    CalibrationStatus,
    ComputeBackendKind,
    DeviceConfig,
    DetectorType,
    DspConfig,
    PrecisionMode,
    QualityFlag,
    SourceType,
    SpectrumFrame,
    SpectrumUnit,
    SweepConfig,
    WindowType,
)
from esw_dfl.sdr.fixed_band import FixedBandOptions
from esw_dfl.sdr.sweep import (
    SweepExecutionStatus,
    SweepExecutor,
    SweepPlannerOptions,
    SweepPlanningError,
    SweepSegmentStatus,
    plan_sweep,
)


def base_options() -> FixedBandOptions:
    return FixedBandOptions(
        device=DeviceConfig(
            source_id="p12-test",
            context_uri="mock:",
            center_frequency_hz=150.0,
            sample_rate_hz=100.0,
            analog_bandwidth_hz=80.0,
            buffer_samples=1024,
        ),
        dsp=DspConfig(
            fft_size=256,
            hop_size=128,
            window=WindowType.HANN,
            detector=DetectorType.SAMPLE,
            unit=SpectrumUnit.DBFS_BIN,
            precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
        ),
        backend=ComputeBackendKind.CPU,
    )


def sweep_config(**changes: object) -> SweepConfig:
    values: dict[str, object] = {
        "start_frequency_hz": 100.0,
        "stop_frequency_hz": 380.0,
        "sample_rate_hz": 100.0,
        "analog_bandwidth_hz": 80.0,
        "overlap_hz": 10.0,
        "fft_size": 256,
        "hop_size": 128,
        "dwell_frames": 2,
        "settling_time_seconds": 0.001,
        "discard_blocks": 1,
    }
    values.update(changes)
    return SweepConfig(**values)


class SweepPlannerTests(unittest.TestCase):
    def test_plan_covers_span_with_overlap_and_explicit_dc_crops(self) -> None:
        plan = plan_sweep(
            sweep_config(),
            SweepPlannerOptions(edge_margin_hz=5.0, dc_exclusion_hz=10.0),
        )
        self.assertGreater(len(plan.segments), 1)
        self.assertEqual(plan.coverage_gaps_hz, ())
        self.assertAlmostEqual(plan.segments[0].requested_start_hz, 100.0)
        self.assertAlmostEqual(plan.segments[-1].requested_stop_hz, 380.0)
        self.assertEqual(plan.segments[0].capture_samples, 384)
        self.assertEqual(len(plan.segments[0].crop_ranges), 2)
        self.assertTrue(all(
            crop.start_bin < crop.stop_bin
            for segment in plan.segments
            for crop in segment.crop_ranges
        ))
        self.assertTrue(all(
            left.requested_stop_hz >= right.requested_start_hz
            for left, right in zip(plan.segments, plan.segments[1:])
        ))

    def test_one_segment_and_invalid_plans(self) -> None:
        one = plan_sweep(
            sweep_config(stop_frequency_hz=120.0),
            SweepPlannerOptions(edge_margin_hz=2.0),
        )
        self.assertEqual(len(one.segments), 1)
        self.assertEqual(one.coverage_gaps_hz, ())
        with self.assertRaises(SweepPlanningError):
            plan_sweep(sweep_config(), SweepPlannerOptions(edge_margin_hz=50.0))
        with self.assertRaises(SweepPlanningError):
            plan_sweep(sweep_config(overlap_hz=70.0), SweepPlannerOptions(edge_margin_hz=5.0, dc_exclusion_hz=10.0))


@dataclass
class FakeSweepService:
    fail_reconfigure_at: int | None = None

    def __post_init__(self) -> None:
        self.streaming = False
        self.configured_centers: list[float] = []
        self.current: FixedBandOptions | None = None
        self.generation = 0
        self.poll_count = 0

    def configure(self, options: FixedBandOptions) -> object:
        self.current = options
        self.generation += 1
        self.configured_centers.append(options.device.center_frequency_hz)
        return SimpleNamespace(
            center_frequency_hz=options.device.center_frequency_hz,
            sample_rate_hz=options.device.sample_rate_hz,
            analog_bandwidth_hz=options.device.analog_bandwidth_hz,
            config_generation=self.generation,
        )

    def reconfigure(self, options: FixedBandOptions) -> object:
        if self.fail_reconfigure_at is not None and len(self.configured_centers) == self.fail_reconfigure_at:
            raise RuntimeError("mock retune failure")
        self.streaming = False
        applied = self.configure(options)
        self.streaming = True
        return applied

    def start(self) -> None:
        self.streaming = True

    def request_stop(self) -> None:
        self.streaming = False

    def join(self) -> None:
        return None

    def poll_spectrum(self, max_items: int = 0) -> tuple[SpectrumFrame, ...]:
        if self.current is None:
            return ()
        count = max_items or 1
        self.poll_count += count
        fft_size = self.current.dsp.fft_size
        center = self.current.device.center_frequency_hz
        sample_rate = self.current.device.sample_rate_hz
        frequencies = center - sample_rate / 2.0 + np.arange(fft_size, dtype=np.float64) * sample_rate / fft_size
        return tuple(
            SpectrumFrame(
                source=SourceDescriptor(SourceType.LIVE_IQ, "p12-test", "P12 test", uri="mock:"),
                frame_sequence=self.poll_count + index,
                first_sample_index=0,
                timestamp_ns=1_700_000_000_000_000_000 + self.poll_count + index,
                config_generation=self.generation,
                center_frequency_hz=center,
                sample_rate_hz=sample_rate,
                analog_bandwidth_hz=self.current.device.analog_bandwidth_hz,
                fft_bin_width_hz=sample_rate / fft_size,
                enbw_hz=sample_rate / fft_size,
                nominal_rbw_hz=sample_rate / fft_size,
                fft_size=fft_size,
                hop_size=self.current.dsp.hop_size,
                window=self.current.dsp.window,
                detector=self.current.dsp.detector,
                precision_mode=self.current.dsp.precision_mode,
                unit=SpectrumUnit.DBFS_BIN,
                frequencies_hz=frequencies,
                values=np.full(fft_size, -70.0, dtype=np.float32),
                calibration_status=CalibrationStatus.UNCALIBRATED,
                quality_flags=QualityFlag.UNCALIBRATED,
            )
            for index in range(count)
        )


class SweepExecutorTests(unittest.TestCase):
    def test_executor_captures_segments_reports_progress_and_restores_fixed_mode(self) -> None:
        plan = plan_sweep(sweep_config(stop_frequency_hz=230.0), SweepPlannerOptions(edge_margin_hz=5.0))
        service = FakeSweepService()
        progress = []
        result = SweepExecutor(service, base_options(), poll_batch_size=2).execute(
            plan,
            progress=progress.append,
        )
        self.assertEqual(result.status, SweepExecutionStatus.COMPLETED)
        self.assertTrue(result.restored)
        self.assertEqual(len(result.segments), len(plan.segments))
        self.assertTrue(all(item.status is SweepSegmentStatus.COMPLETED for item in result.segments))
        self.assertTrue(all(len(item.frames) == 2 for item in result.segments))
        self.assertEqual(service.configured_centers[-1], base_options().device.center_frequency_hz)
        self.assertIn("finished", [item.stage for item in progress])
        self.assertTrue(all(item.timing.total_s >= 0.0 for item in result.segments))

    def test_retune_failure_marks_current_and_remaining_segments_explicitly_missing(self) -> None:
        plan = plan_sweep(sweep_config(stop_frequency_hz=230.0), SweepPlannerOptions(edge_margin_hz=5.0))
        service = FakeSweepService(fail_reconfigure_at=1)
        result = SweepExecutor(service, base_options()).execute(plan)
        self.assertEqual(result.status, SweepExecutionStatus.FAILED)
        self.assertEqual(result.segments[0].status, SweepSegmentStatus.COMPLETED)
        self.assertEqual(result.segments[1].status, SweepSegmentStatus.FAILED)
        self.assertTrue(all(item.status is SweepSegmentStatus.MISSING for item in result.segments[2:]))
        self.assertTrue(any("mock retune failure" in error for error in result.errors))
        self.assertTrue(result.restored)

    def test_cancellation_is_safe_and_does_not_hide_missing_segments(self) -> None:
        plan = plan_sweep(sweep_config(stop_frequency_hz=230.0), SweepPlannerOptions(edge_margin_hz=5.0))
        service = FakeSweepService()
        cancel = Event()

        def stop_after_first(progress: object) -> None:
            if getattr(progress, "stage", "") == "segment_complete":
                cancel.set()

        result = SweepExecutor(service, base_options()).execute(plan, cancel=cancel, progress=stop_after_first)
        self.assertEqual(result.status, SweepExecutionStatus.CANCELLED)
        self.assertEqual(result.segments[0].status, SweepSegmentStatus.COMPLETED)
        self.assertTrue(all(item.status is SweepSegmentStatus.MISSING for item in result.segments[1:]))
        self.assertTrue(result.restored)


    def test_cancellation_before_first_segment_marks_all_segments_missing(self) -> None:
        plan = plan_sweep(sweep_config(stop_frequency_hz=380.0), SweepPlannerOptions(edge_margin_hz=5.0))
        service = FakeSweepService()
        cancel = Event()
        cancel.set()

        result = SweepExecutor(service, base_options()).execute(plan, cancel=cancel)

        self.assertEqual(result.status, SweepExecutionStatus.CANCELLED)
        self.assertEqual(len(result.segments), len(plan.segments))
        self.assertTrue(all(item.status is SweepSegmentStatus.MISSING for item in result.segments))
        self.assertEqual(service.configured_centers, [])
        self.assertTrue(result.restored)

if __name__ == "__main__":
    unittest.main()
