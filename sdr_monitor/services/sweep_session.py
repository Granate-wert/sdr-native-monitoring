"""Safe deterministic sweep planner/executor for standalone UI validation."""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np

from ..domain.sweep import (
    SweepConfiguration,
    SweepMode,
    SweepPlan,
    SweepProgress,
    SweepQuality,
    SweepRateEvidence,
    SweepRateMetrics,
    SweepResult,
    SweepSegment,
    SweepSegmentSpectrum,
    SweepState,
)
from .sweep_stitching import SweepStitchOptions, stitch_sweep_segments


_MODE_PARAMETERS: dict[SweepMode, tuple[float, float, float]] = {
    SweepMode.FAST: (50e6, 250e3, 0.015),
    SweepMode.BALANCED: (20e6, 100e3, 0.050),
    SweepMode.PRECISE: (5e6, 25e3, 0.125),
}


class InMemorySweepService:
    """No-hardware reference implementation with explicit unknown quality."""

    def __init__(self) -> None:
        self._cancel_requested = threading.Event()
        self._closed = False

    def plan(self, configuration: SweepConfiguration) -> SweepPlan:
        if self._closed:
            raise RuntimeError("sweep service is closed")
        width_hz, resolution_hz, per_segment_s = _MODE_PARAMETERS[configuration.mode]
        span_hz = configuration.stop_hz - configuration.start_hz
        step_hz = width_hz * (1.0 - configuration.overlap_fraction)
        count = max(1, math.ceil(max(0.0, span_hz - width_hz) / step_hz) + 1)
        segments = []
        for index in range(count):
            start_hz = configuration.start_hz + index * step_hz
            stop_hz = min(configuration.stop_hz, start_hz + width_hz)
            segments.append(
                SweepSegment(
                    index=index,
                    start_hz=start_hz,
                    stop_hz=stop_hz,
                    usable_start_hz=min(stop_hz, start_hz + configuration.dc_margin_hz),
                    usable_stop_hz=max(start_hz, stop_hz - configuration.dc_margin_hz),
                )
            )
        estimate = count * (per_segment_s + configuration.settling_s + configuration.dwell_s)
        return SweepPlan(configuration, tuple(segments), estimate, resolution_hz)

    def execute(self, configuration: SweepConfiguration, progress: Callable[[SweepProgress], None]) -> SweepResult:
        self._cancel_requested.clear()
        plan = self.plan(configuration)
        started = time.monotonic()
        total = len(plan.segments)
        for completed, segment in enumerate(plan.segments):
            if self._cancel_requested.is_set():
                return self._cancelled_result(plan, started, completed)
            progress(SweepProgress(SweepState.RUNNING, completed, total, segment.start_hz, "stabilизация"))
            time.sleep(0.001)
            if self._cancel_requested.is_set():
                return self._cancelled_result(plan, started, completed)
            progress(SweepProgress(SweepState.RUNNING, completed + 1, total, segment.stop_hz, "сбор"))
        progress(SweepProgress(SweepState.COMPLETED, total, total, plan.configuration.stop_hz, "готово"))
        duration = time.monotonic() - started
        spectra = self._synthetic_segment_spectra(plan)
        grid = stitch_sweep_segments(
            plan,
            spectra,
            SweepStitchOptions(target_spacing_hz=plan.resolution_hz),
        )
        return SweepResult(
            SweepState.COMPLETED,
            plan,
            duration,
            SweepQuality(
                len(grid.missing_segment_indices),
                None,
                None,
                "Synthetic control-plane result: grid and seam math are not physical sweep or calibration evidence.",
                missing_bins=grid.missing_bin_count,
                seam_count=0,
            ),
            stitched_grid=grid,
            rate_metrics=SweepRateMetrics(
                SweepRateEvidence.SYNTHETIC,
                duration,
                (configuration.stop_hz - configuration.start_hz) / duration / 1e6 if duration > 0.0 else None,
                1.0 / duration if duration > 0.0 else None,
                None,
            ),
        )

    def cancel(self) -> None:
        self._cancel_requested.set()

    def close(self) -> None:
        self._closed = True
        self.cancel()

    def export_result(self, result: SweepResult, output_path: Path) -> Path:
        payload = {
            "state": result.state.value,
            "duration_seconds": result.duration_seconds,
            "configuration": {
                **asdict(result.plan.configuration),
                "mode": result.plan.configuration.mode.value,
                "execution_mode": result.plan.configuration.execution_mode.value,
            },
            "segment_count": len(result.plan.segments),
            "resolution_hz": result.plan.resolution_hz,
            "quality": asdict(result.quality),
            "stitched_grid": None if result.stitched_grid is None else {
                "bin_count": int(result.stitched_grid.frequencies_hz.size),
                "missing_bin_count": result.stitched_grid.missing_bin_count,
                "missing_segment_count": len(result.stitched_grid.missing_segment_indices),
                "seam_count": len(result.stitched_grid.seams),
                "source_id": result.stitched_grid.source_id,
                "config_generation": result.stitched_grid.config_generation,
                "unit": result.stitched_grid.unit,
            },
            "rate_metrics": None if result.rate_metrics is None else {
                "evidence": result.rate_metrics.evidence.value,
                "elapsed_seconds": result.rate_metrics.elapsed_seconds,
                "megahertz_per_second": result.rate_metrics.megahertz_per_second,
                "sweeps_per_second": result.rate_metrics.sweeps_per_second,
                "fft_lps": result.rate_metrics.fft_lps,
            },
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(output_path.suffix + ".part")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, output_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return output_path

    @staticmethod
    def _cancelled_result(plan: SweepPlan, started: float, completed: int) -> SweepResult:
        return SweepResult(
            SweepState.CANCELLED,
            plan,
            time.monotonic() - started,
            SweepQuality(len(plan.segments) - completed, None, None, "Cancelled sweep has explicit missing segments."),
        )

    @staticmethod
    def _synthetic_segment_spectra(plan: SweepPlan) -> tuple[SweepSegmentSpectrum, ...]:
        """Build a deterministic demo only after all segment progress completed.

        This never emulates a device FFT rate.  The values exist solely to
        exercise the same bounded stitching/presentation contracts that a
        future physical adapter will supply with actual segment spectra.
        """

        spectra: list[SweepSegmentSpectrum] = []
        for segment in plan.segments:
            start_hz = segment.usable_start_hz
            stop_hz = segment.usable_stop_hz
            count = max(2, int(round((stop_hz - start_hz) / plan.resolution_hz)) + 1)
            frequency = np.linspace(start_hz, stop_hz, count, dtype=np.float64)
            normalized = frequency / 1e6
            values = -95.0 + 6.0 * np.sin(normalized / 9.0) + 2.0 * np.cos(normalized / 2.7)
            spectra.append(SweepSegmentSpectrum(segment.index, "in-memory-sweep", 0, frequency, values))
        return tuple(spectra)
