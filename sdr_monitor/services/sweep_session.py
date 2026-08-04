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

from ..domain.sweep import SweepConfiguration, SweepMode, SweepPlan, SweepProgress, SweepQuality, SweepResult, SweepSegment, SweepState


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
        return SweepResult(
            SweepState.COMPLETED,
            plan,
            time.monotonic() - started,
            SweepQuality(0, None, None, "Synthetic planning service: seam and calibration are not measured."),
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
            "configuration": {**asdict(result.plan.configuration), "mode": result.plan.configuration.mode.value},
            "segment_count": len(result.plan.segments),
            "resolution_hz": result.plan.resolution_hz,
            "quality": asdict(result.quality),
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
