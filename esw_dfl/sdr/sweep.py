"""Bounded wide-span sweep planning and sequential execution.

P12 stops at immutable segment results.  It does not stitch segments into a
single spectrum; P13 owns that operation.  The executor reuses the existing
fixed-band native service so acquisition, DSP, queues and device lifecycle stay
on the established native boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import math
import time
from collections.abc import Callable
from threading import Event
from typing import Any, Protocol

from .contracts import QualityFlag, SpectrumFrame, SweepConfig
from .fixed_band import FixedBandOptions


class SweepPlanningError(ValueError):
    """Raised when a requested span cannot be covered safely."""


class SweepSegmentStatus(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    MISSING = "missing"


class SweepExecutionStatus(StrEnum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SweepPlannerOptions:
    """Crop policy applied to every configured RF segment."""

    edge_margin_hz: float = 0.0
    dc_exclusion_hz: float = 0.0

    def __post_init__(self) -> None:
        for name in ("edge_margin_hz", "dc_exclusion_hz"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise SweepPlanningError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class SweepCropRange:
    """One inclusive-frequency/exclusive-bin crop interval."""

    start_frequency_hz: float
    stop_frequency_hz: float
    start_bin: int
    stop_bin: int

    def __post_init__(self) -> None:
        if not self.start_frequency_hz < self.stop_frequency_hz:
            raise SweepPlanningError("crop range must have positive frequency width")
        if self.start_bin < 0 or self.stop_bin <= self.start_bin:
            raise SweepPlanningError("crop bin range must be positive")


@dataclass(frozen=True, slots=True)
class SweepSegmentPlan:
    segment_index: int
    center_frequency_hz: float
    requested_start_hz: float
    requested_stop_hz: float
    actual_start_hz: float
    actual_stop_hz: float
    sample_rate_hz: float
    analog_bandwidth_hz: float
    edge_margin_hz: float
    dc_exclusion_hz: float
    overlap_hz: float
    fft_size: int
    hop_size: int
    dwell_frames: int
    settling_time_seconds: float
    discard_blocks: int
    capture_samples: int
    expected_capture_duration_s: float
    expected_total_duration_s: float
    crop_ranges: tuple[SweepCropRange, ...]

    @property
    def requested_bandwidth_hz(self) -> float:
        return self.requested_stop_hz - self.requested_start_hz


@dataclass(frozen=True, slots=True)
class SweepPlan:
    config: SweepConfig
    options: SweepPlannerOptions
    usable_bandwidth_hz: float
    segments: tuple[SweepSegmentPlan, ...]
    expected_duration_s: float

    @property
    def requested_start_hz(self) -> float:
        return self.config.start_frequency_hz

    @property
    def requested_stop_hz(self) -> float:
        return self.config.stop_frequency_hz

    @property
    def coverage_gaps_hz(self) -> tuple[tuple[float, float], ...]:
        gaps: list[tuple[float, float]] = []
        if not self.segments:
            return tuple(gaps)
        cursor = self.requested_start_hz
        for segment in self.segments:
            if segment.requested_start_hz > cursor:
                gaps.append((cursor, segment.requested_start_hz))
            cursor = max(cursor, segment.requested_stop_hz)
        if cursor < self.requested_stop_hz:
            gaps.append((cursor, self.requested_stop_hz))
        return tuple(gaps)


def _bin_range(
    start_hz: float,
    stop_hz: float,
    capture_start_hz: float,
    bin_width_hz: float,
    fft_size: int,
) -> tuple[int, int]:
    first = max(0, min(fft_size, int(math.ceil((start_hz - capture_start_hz) / bin_width_hz))))
    last = max(first + 1, min(fft_size, int(math.floor((stop_hz - capture_start_hz) / bin_width_hz)) + 1))
    if last <= first:
        raise SweepPlanningError("crop range does not contain an FFT bin")
    return first, last


def plan_sweep(
    config: SweepConfig,
    options: SweepPlannerOptions | None = None,
) -> SweepPlan:
    """Create an ordered, overlapping plan with explicit crop intervals."""

    if not isinstance(config, SweepConfig):
        raise TypeError("config must be SweepConfig")
    options = options or SweepPlannerOptions()
    raw_width = min(config.sample_rate_hz, config.analog_bandwidth_hz) - 2.0 * options.edge_margin_hz
    if raw_width <= 0.0:
        raise SweepPlanningError("edge margins leave no usable RF bandwidth")
    if options.dc_exclusion_hz >= raw_width:
        raise SweepPlanningError("DC exclusion leaves no usable RF bandwidth")
    # DC exclusion is an explicit crop gap, not a hidden loss of the
    # continuous RF envelope used to place overlapping segments.
    usable = raw_width
    if config.overlap_hz >= usable:
        raise SweepPlanningError("overlap must be smaller than cropped usable bandwidth")

    span = config.stop_frequency_hz - config.start_frequency_hz
    stride = usable - config.overlap_hz
    count = max(1, int(math.ceil(max(0.0, span - usable) / stride)) + 1)
    bin_width = config.sample_rate_hz / config.fft_size
    capture_bandwidth = min(config.sample_rate_hz, config.analog_bandwidth_hz)
    segments: list[SweepSegmentPlan] = []
    for index in range(count):
        requested_start = config.start_frequency_hz + index * stride
        if index == count - 1:
            requested_start = max(config.start_frequency_hz, config.stop_frequency_hz - usable)
        requested_stop = min(config.stop_frequency_hz, requested_start + usable)
        center = (requested_start + requested_stop) / 2.0
        actual_start = center - capture_bandwidth / 2.0
        actual_stop = center + capture_bandwidth / 2.0
        crop_start = max(requested_start, center - raw_width / 2.0)
        crop_stop = min(requested_stop, center + raw_width / 2.0)
        dc_low = center - options.dc_exclusion_hz / 2.0
        dc_high = center + options.dc_exclusion_hz / 2.0
        ranges: list[SweepCropRange] = []
        capture_start = center - config.sample_rate_hz / 2.0
        if dc_low > crop_start:
            first, last = _bin_range(crop_start, dc_low, capture_start, bin_width, config.fft_size)
            ranges.append(SweepCropRange(crop_start, dc_low, first, last))
        if crop_stop > dc_high:
            first, last = _bin_range(dc_high, crop_stop, capture_start, bin_width, config.fft_size)
            ranges.append(SweepCropRange(dc_high, crop_stop, first, last))
        if not ranges:
            raise SweepPlanningError(f"segment {index} has no FFT bins after crop")
        capture_samples = config.fft_size + max(0, config.dwell_frames - 1) * config.hop_size
        capture_duration = capture_samples / config.sample_rate_hz
        segments.append(SweepSegmentPlan(
            segment_index=index,
            center_frequency_hz=center,
            requested_start_hz=requested_start,
            requested_stop_hz=requested_stop,
            actual_start_hz=actual_start,
            actual_stop_hz=actual_stop,
            sample_rate_hz=config.sample_rate_hz,
            analog_bandwidth_hz=config.analog_bandwidth_hz,
            edge_margin_hz=options.edge_margin_hz,
            dc_exclusion_hz=options.dc_exclusion_hz,
            overlap_hz=(0.0 if index == 0 else max(0.0, segments[-1].requested_stop_hz - requested_start)),
            fft_size=config.fft_size,
            hop_size=config.hop_size,
            dwell_frames=config.dwell_frames,
            settling_time_seconds=config.settling_time_seconds,
            discard_blocks=config.discard_blocks,
            capture_samples=capture_samples,
            expected_capture_duration_s=capture_duration,
            expected_total_duration_s=config.settling_time_seconds + capture_duration,
            crop_ranges=tuple(ranges),
        ))
    plan = SweepPlan(config, options, usable, tuple(segments), sum(item.expected_total_duration_s for item in segments))
    if plan.coverage_gaps_hz:
        raise SweepPlanningError(f"planner produced uncovered frequency gaps: {plan.coverage_gaps_hz}")
    return plan


@dataclass(frozen=True, slots=True)
class SweepTiming:
    retune_s: float = 0.0
    readback_s: float = 0.0
    settling_s: float = 0.0
    capture_s: float = 0.0
    process_s: float = 0.0
    total_s: float = 0.0


@dataclass(frozen=True, slots=True)
class SweepProgress:
    fraction: float
    stage: str
    segment_index: int | None
    completed_segments: int
    total_segments: int


@dataclass(frozen=True, slots=True)
class SweepSegmentResult:
    plan: SweepSegmentPlan
    status: SweepSegmentStatus
    applied_config: Any | None = None
    frames: tuple[SpectrumFrame, ...] = ()
    timing: SweepTiming = field(default_factory=SweepTiming)
    processing_result: Any | None = None
    quality_flags: QualityFlag = QualityFlag.NONE
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SweepExecutionResult:
    status: SweepExecutionStatus
    plan: SweepPlan
    segments: tuple[SweepSegmentResult, ...]
    started_ns: int
    completed_ns: int
    restored: bool
    restore_error: str | None = None
    errors: tuple[str, ...] = ()


class SweepService(Protocol):
    def configure(self, options: FixedBandOptions) -> Any: ...
    def reconfigure(self, options: FixedBandOptions) -> Any: ...
    def start(self) -> None: ...
    def request_stop(self) -> None: ...
    def join(self) -> None: ...
    def poll_spectrum(self, max_items: int = 0) -> tuple[SpectrumFrame, ...]: ...


class _SweepCancelled(Exception):
    pass


def _is_cancelled(cancel: Event | Callable[[], bool] | None) -> bool:
    if cancel is None:
        return False
    return bool(cancel.is_set() if hasattr(cancel, "is_set") else cancel())


class SweepExecutor:
    """Execute one plan through a fixed-band service with safe restoration."""

    def __init__(
        self,
        service: SweepService,
        base_options: FixedBandOptions,
        *,
        poll_batch_size: int = 8,
        idle_timeout_s: float = 2.0,
    ) -> None:
        if not isinstance(base_options, FixedBandOptions):
            raise TypeError("base_options must be FixedBandOptions")
        if poll_batch_size <= 0:
            raise ValueError("poll_batch_size must be positive")
        if not math.isfinite(idle_timeout_s) or idle_timeout_s <= 0.0:
            raise ValueError("idle_timeout_s must be finite and positive")
        self.service = service
        self.base_options = base_options
        self.poll_batch_size = int(poll_batch_size)
        self.idle_timeout_s = float(idle_timeout_s)

    def _segment_options(self, segment: SweepSegmentPlan) -> FixedBandOptions:
        device = replace(
            self.base_options.device,
            center_frequency_hz=segment.center_frequency_hz,
            sample_rate_hz=segment.sample_rate_hz,
            analog_bandwidth_hz=segment.analog_bandwidth_hz,
        )
        dsp = replace(
            self.base_options.dsp,
            fft_size=segment.fft_size,
            hop_size=segment.hop_size,
        )
        return replace(
            self.base_options,
            device=device,
            dsp=dsp,
            discard_blocks_after_start=segment.discard_blocks,
        )

    @staticmethod
    def _notify(
        callback: Callable[[SweepProgress], None] | None,
        progress: SweepProgress,
    ) -> None:
        if callback is not None:
            callback(progress)

    @staticmethod
    def _wait_seconds(seconds: float, cancel: Event | Callable[[], bool] | None) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            if _is_cancelled(cancel):
                raise _SweepCancelled
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return
            time.sleep(min(0.01, remaining))

    def _capture(
        self,
        segment: SweepSegmentPlan,
        cancel: Event | Callable[[], bool] | None,
    ) -> tuple[SpectrumFrame, ...]:
        frames: list[SpectrumFrame] = []
        deadline = time.monotonic() + max(
            self.idle_timeout_s,
            segment.expected_capture_duration_s * 4.0 + 0.1,
        )
        while len(frames) < segment.dwell_frames:
            if _is_cancelled(cancel):
                raise _SweepCancelled
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"segment {segment.segment_index} produced {len(frames)}/{segment.dwell_frames} frames"
                )
            batch = tuple(self.service.poll_spectrum(min(
                self.poll_batch_size,
                segment.dwell_frames - len(frames),
            )))
            if batch:
                frames.extend(batch)
            else:
                self._wait_seconds(0.001, cancel)
        return tuple(frames[:segment.dwell_frames])

    @staticmethod
    def _validate_frames(
        frames: tuple[SpectrumFrame, ...], applied_config: Any,
    ) -> None:
        expected_generation = getattr(applied_config, "config_generation", None)
        if expected_generation is None:
            return
        mismatched = [
            frame.frame_sequence for frame in frames
            if frame.config_generation != int(expected_generation)
        ]
        if mismatched:
            raise RuntimeError(
                "segment contains frames from another config generation: "
                + ",".join(map(str, mismatched))
            )

    def execute(
        self,
        plan: SweepPlan,
        *,
        cancel: Event | Callable[[], bool] | None = None,
        progress: Callable[[SweepProgress], None] | None = None,
        process_segment: Callable[[SweepSegmentPlan, tuple[SpectrumFrame, ...]], Any] | None = None,
    ) -> SweepExecutionResult:
        if not isinstance(plan, SweepPlan):
            raise TypeError("plan must be SweepPlan")
        started_ns = time.time_ns()
        results: list[SweepSegmentResult] = []
        errors: list[str] = []
        initial_streaming = bool(getattr(self.service, "streaming", False))
        service_started = False
        touched_service = False
        cancelled = False
        failed = False

        for segment in plan.segments:
            if _is_cancelled(cancel):
                cancelled = True
                for missing in plan.segments[segment.segment_index:]:
                    results.append(self._missing(missing, "not executed: sweep cancelled"))
                break
            self._notify(progress, SweepProgress(
                segment.segment_index / len(plan.segments), "segment_start",
                segment.segment_index, len(results), len(plan.segments),
            ))
            segment_started = time.perf_counter_ns()
            applied: Any | None = None
            frames: tuple[SpectrumFrame, ...] = ()
            processing_result: Any | None = None
            status = SweepSegmentStatus.COMPLETED
            error: str | None = None
            quality_flags = QualityFlag.NONE
            retune_s = readback_s = settle_s = capture_s = process_s = 0.0
            try:
                options = self._segment_options(segment)
                retune_start = time.perf_counter_ns()
                touched_service = True
                applied = (
                    self.service.reconfigure(options)
                    if service_started or initial_streaming
                    else self.service.configure(options)
                )
                retune_s = (time.perf_counter_ns() - retune_start) / 1.0e9
                # The native configure/reconfigure call returns the atomic
                # applied readback; there is no second device readback call.
                readback_s = 0.0
                if not service_started and not initial_streaming:
                    self.service.start()
                    service_started = True
                settle_start = time.perf_counter_ns()
                self._wait_seconds(segment.settling_time_seconds, cancel)
                settle_s = (time.perf_counter_ns() - settle_start) / 1.0e9
                capture_start = time.perf_counter_ns()
                frames = self._capture(segment, cancel)
                capture_s = (time.perf_counter_ns() - capture_start) / 1.0e9
                self._validate_frames(frames, applied)
                quality_flags = QualityFlag(0)
                for frame in frames:
                    quality_flags |= frame.quality_flags
                if process_segment is not None:
                    process_start = time.perf_counter_ns()
                    processing_result = process_segment(segment, frames)
                    process_s = (time.perf_counter_ns() - process_start) / 1.0e9
            except _SweepCancelled:
                cancelled = True
                status = SweepSegmentStatus.CANCELLED
                quality_flags = QualityFlag.MISSING_SEGMENT
                error = "sweep cancelled"
            except Exception as exc:
                failed = True
                status = SweepSegmentStatus.FAILED
                quality_flags = QualityFlag.MISSING_SEGMENT
                error = f"{type(exc).__name__}: {exc}"
            total_s = (time.perf_counter_ns() - segment_started) / 1.0e9
            results.append(SweepSegmentResult(
                plan=segment,
                status=status,
                applied_config=applied,
                frames=frames,
                timing=SweepTiming(retune_s, readback_s, settle_s, capture_s, process_s, total_s),
                processing_result=processing_result,
                quality_flags=quality_flags,
                error=error,
            ))
            if error:
                errors.append(f"segment {segment.segment_index}: {error}")
            self._notify(progress, SweepProgress(
                (segment.segment_index + 1) / len(plan.segments), "segment_complete",
                segment.segment_index, len(results), len(plan.segments),
            ))
            if cancelled or failed:
                remaining = segment.segment_index + 1
                for missing in plan.segments[remaining:]:
                    results.append(self._missing(missing, "not executed after previous segment failure/cancellation"))
                break

        restore_error: str | None = None
        restored = not results
        try:
            if touched_service:
                if initial_streaming:
                    self.service.reconfigure(self.base_options)
                    restored = True
                else:
                    if service_started:
                        self.service.request_stop()
                        self.service.join()
                        service_started = False
                    self.service.configure(self.base_options)
                    restored = True
            else:
                restored = True
        except Exception as exc:
            restored = False
            restore_error = f"{type(exc).__name__}: {exc}"
            errors.append(f"restore: {restore_error}")
        completed_ns = time.time_ns()
        status = (
            SweepExecutionStatus.FAILED if failed or restore_error is not None
            else SweepExecutionStatus.CANCELLED if cancelled
            else SweepExecutionStatus.COMPLETED
        )
        self._notify(progress, SweepProgress(
            1.0 if status is SweepExecutionStatus.COMPLETED else len(results) / len(plan.segments),
            "finished", None, len(results), len(plan.segments),
        ))
        return SweepExecutionResult(
            status=status,
            plan=plan,
            segments=tuple(results),
            started_ns=started_ns,
            completed_ns=completed_ns,
            restored=restored,
            restore_error=restore_error,
            errors=tuple(errors),
        )

    @staticmethod
    def _missing(segment: SweepSegmentPlan, reason: str) -> SweepSegmentResult:
        return SweepSegmentResult(
            plan=segment,
            status=SweepSegmentStatus.MISSING,
            quality_flags=QualityFlag.MISSING_SEGMENT,
            error=reason,
        )


__all__ = [
    "SweepCropRange",
    "SweepExecutionResult",
    "SweepExecutionStatus",
    "SweepExecutor",
    "SweepPlan",
    "SweepPlannerOptions",
    "SweepPlanningError",
    "SweepProgress",
    "SweepSegmentPlan",
    "SweepSegmentResult",
    "SweepSegmentStatus",
    "SweepService",
    "SweepTiming",
    "plan_sweep",
]
