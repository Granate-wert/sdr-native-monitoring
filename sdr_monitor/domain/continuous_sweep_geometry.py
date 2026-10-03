"""One common LO sequence for single/paired native Sweep plan construction."""

from dataclasses import dataclass
import math

from .continuous_sweep_request import ContinuousSweepPlanRequest
from .sweep_capacity import SWEEP_MAX_SEGMENTS


def sweep_segment_count(request: ContinuousSweepPlanRequest) -> int:
    if not isinstance(request, ContinuousSweepPlanRequest):
        raise TypeError("Sweep geometry requires a typed request")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (
        request.start_hz, request.stop_hz, request.usable_window_hz, request.overlap_hz,
    )) or not (0 < request.start_hz < request.stop_hz
               and 0 <= request.overlap_hz < request.usable_window_hz):
        raise ValueError("Sweep geometry requires finite increasing frequencies/window")
    ratio = max(0., request.stop_hz - request.start_hz - request.usable_window_hz) / (
        request.usable_window_hz - request.overlap_hz)
    if not math.isfinite(ratio) or ratio > SWEEP_MAX_SEGMENTS - 1:
        raise ValueError("continuous sweep plan exceeds native 2048-segment bound")
    return max(1, math.ceil(ratio) + 1)


@dataclass(frozen=True, slots=True)
class SweepStepGeometry:
    segment_index: int
    usable_start_hz: float
    usable_stop_hz: float

    def __post_init__(self) -> None:
        if (type(self.segment_index) is not int or not 0 <= self.segment_index < SWEEP_MAX_SEGMENTS
                or any(type(v) not in (int, float) or not math.isfinite(v)
                       for v in (self.usable_start_hz, self.usable_stop_hz))
                or not 0 < self.usable_start_hz < self.usable_stop_hz):
            raise ValueError("Sweep step requires a bounded index and increasing usable span")

    @property
    def center_hz(self) -> float:
        # Preserve the ordinary device-range rounding; avoid overflow for
        # finite, very large host-only draft frequencies.
        return self.usable_start_hz + (self.usable_stop_hz - self.usable_start_hz) / 2.


def sweep_step_geometry(request: ContinuousSweepPlanRequest, segment_index: int) -> SweepStepGeometry:
    count = sweep_segment_count(request)
    if type(segment_index) is not int or not 0 <= segment_index < count:
        raise ValueError("Sweep step index is outside the exact common plan")
    start = request.start_hz + segment_index * (request.usable_window_hz - request.overlap_hz)
    return SweepStepGeometry(segment_index, start, min(request.stop_hz, start + request.usable_window_hz))
