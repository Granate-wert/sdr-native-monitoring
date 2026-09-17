"""Immutable continuous-Sweep intent, independent of native services and Qt."""
from dataclasses import dataclass
import math
from .sweep_statistics import SweepStatisticsSettings
from .sweep_speed import SweepSpeedProfile


@dataclass(frozen=True, slots=True)
class ContinuousSweepPlanRequest:
    start_hz: float
    stop_hz: float
    usable_window_hz: float = 36e6
    overlap_hz: float = 2e6
    # Lower bound for the shared session owner, not permission to reuse an
    # earlier acquisition epoch. Direct native evidence callers own their epoch.
    epoch: int = 0
    output_queue_capacity: int = 4
    segment_frame_timeout_ms: int = 1000
    acquisition_buffer_samples: int = 262_144
    line_snapshot_rate_hz: float | None = None
    # Zero retains the physical-FFT grid; nonzero is analysis N within W.
    analysis_bins_per_usable_window: int = 0
    allow_r10d5_evidence_buffer_geometry: bool = False
    statistics: SweepStatisticsSettings | None = None
    speed_profile: SweepSpeedProfile = SweepSpeedProfile.APPLIED

    def __post_init__(self) -> None:
        object.__setattr__(self, "speed_profile", SweepSpeedProfile(self.speed_profile))
        if self.statistics is not None and not isinstance(self.statistics, SweepStatisticsSettings):
            raise TypeError("Sweep statistics requires explicit bounded settings")
        values = (self.start_hz, self.stop_hz, self.usable_window_hz, self.overlap_hz)
        if not all(math.isfinite(value) for value in values) or self.start_hz <= 0.0 or self.stop_hz <= self.start_hz:
            raise ValueError("continuous sweep frequencies must be finite and increasing")
        if self.usable_window_hz <= 0.0 or self.overlap_hz < 0.0 or self.overlap_hz >= self.usable_window_hz:
            raise ValueError("continuous sweep usable window/overlap is invalid")
        if (type(self.epoch) is not int or not 0 <= self.epoch <= (1 << 64) - 1
                or not 1 <= self.output_queue_capacity <= 64 or not 1 <= self.segment_frame_timeout_ms <= 60_000):
            raise ValueError("continuous sweep epoch, queue capacity or timeout is invalid")
        standard_geometry = not (
            self.acquisition_buffer_samples < 4096
            or self.acquisition_buffer_samples > 262_144
            or self.acquisition_buffer_samples & (self.acquisition_buffer_samples - 1)
        )
        d5_geometry = (
            self.allow_r10d5_evidence_buffer_geometry
            and self.acquisition_buffer_samples in (262_144, 308_224)
        )
        if not standard_geometry and not d5_geometry:
            raise ValueError("continuous sweep buffer must be a power of two in [4096, 262144]")
        if self.line_snapshot_rate_hz is not None and (
            not math.isfinite(self.line_snapshot_rate_hz)
            or not 1.0 <= self.line_snapshot_rate_hz <= 2000.0
        ):
            raise ValueError("continuous sweep line snapshot rate must be in [1, 2000] Hz")
        if self.analysis_bins_per_usable_window and (
            self.analysis_bins_per_usable_window < 256
            or self.analysis_bins_per_usable_window > 262_144
            or self.analysis_bins_per_usable_window & (self.analysis_bins_per_usable_window - 1)
        ):
            raise ValueError("continuous sweep analysis bins must be a power of two in [256, 262144]")
