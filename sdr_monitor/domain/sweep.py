"""Qt-free contracts for planned wideband sweep operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SweepMode(StrEnum):
    FAST = "fast"
    BALANCED = "balanced"
    PRECISE = "precise"


class SweepState(StrEnum):
    IDLE = "idle"
    PLANNED = "planned"
    RUNNING = "running"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SweepConfiguration:
    start_hz: float = 400e6
    stop_hz: float = 6e9
    mode: SweepMode = SweepMode.BALANCED
    overlap_fraction: float = 0.10
    dc_margin_hz: float = 100e3
    settling_s: float = 0.02
    dwell_s: float = 0.10
    discard_blocks: int = 1

    def __post_init__(self) -> None:
        if self.start_hz < 0 or self.stop_hz <= self.start_hz:
            raise ValueError("sweep stop frequency must exceed start frequency")
        if not 0 <= self.overlap_fraction < 0.5:
            raise ValueError("sweep overlap must be in [0, 0.5)")
        if min(self.dc_margin_hz, self.settling_s, self.dwell_s, self.discard_blocks) < 0:
            raise ValueError("sweep expert settings must be non-negative")


@dataclass(frozen=True, slots=True)
class SweepSegment:
    index: int
    start_hz: float
    stop_hz: float
    usable_start_hz: float
    usable_stop_hz: float


@dataclass(frozen=True, slots=True)
class SweepPlan:
    configuration: SweepConfiguration
    segments: tuple[SweepSegment, ...]
    estimated_seconds: float
    resolution_hz: float


@dataclass(frozen=True, slots=True)
class SweepProgress:
    state: SweepState
    completed_segments: int
    total_segments: int
    current_hz: float | None = None
    stage: str = ""

    @property
    def percent(self) -> float:
        return 0.0 if self.total_segments == 0 else 100.0 * self.completed_segments / self.total_segments


@dataclass(frozen=True, slots=True)
class SweepQuality:
    missing_segments: int
    seam_p95_db: float | None
    calibration_coverage_percent: float | None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class SweepResult:
    state: SweepState
    plan: SweepPlan
    duration_seconds: float
    quality: SweepQuality
    error: str | None = None
