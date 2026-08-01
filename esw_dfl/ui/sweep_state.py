"""Immutable presentation snapshots for the Wideband Sweep workspace.

All types in this module are plain frozen dataclasses: they carry no Qt
objects, numpy buffers, or native handles, so the workspace can render them
from a GUI timer without touching the sweep worker. Pre-formatted strings are
allowed where the presenter is the appropriate formatting boundary. The
conventions match :mod:`esw_dfl.ui.live_state`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SweepRunStatus(StrEnum):
    """Lifecycle status visible to the Wideband Sweep workspace."""

    IDLE = "idle"
    PLANNED = "planned"
    PLANNING = "planning"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SweepPlanSegmentSnapshot:
    """One planned segment used by the workspace diagram and table."""

    segment_index: int
    center_frequency_hz: float
    requested_start_hz: float
    requested_stop_hz: float
    overlap_hz: float
    expected_total_duration_s: float


@dataclass(frozen=True, slots=True)
class SweepPlanSnapshot:
    """Device-independent preview of the planned RF coverage."""

    requested_start_hz: float = 0.0
    requested_stop_hz: float = 0.0
    usable_bandwidth_hz: float = 0.0
    expected_duration_s: float = 0.0
    segment_count: int = 0
    segments: tuple[SweepPlanSegmentSnapshot, ...] = ()
    coverage_gaps_hz: tuple[tuple[float, float], ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        if self.segments and self.segment_count != len(self.segments):
            raise ValueError("segment_count must match segments")


@dataclass(frozen=True, slots=True)
class SweepRunSnapshot:
    """Progress and terminal status of one execution."""

    status: SweepRunStatus = SweepRunStatus.IDLE
    current_segment_index: int | None = None
    completed_segments: int = 0
    total_segments: int = 0
    stage: str | None = None
    elapsed_s: float = 0.0
    eta_s: float | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SweepSeamSnapshot:
    """One overlap-correction quality metric for presentation."""

    left_segment_index: int
    right_segment_index: int
    correction_db: float
    before_p95_db: float
    after_p95_db: float
    sample_count: int


@dataclass(frozen=True, slots=True)
class SweepQualitySnapshot:
    """Aggregate bin quality and seam metrics without spectrum arrays."""

    missing_bins: int = 0
    overlap_bins: int = 0
    seams: tuple[SweepSeamSnapshot, ...] = ()
    unit: str = ""
    calibration_status: str = ""
    calibration_profile_id: str | None = None

    @property
    def seam_count(self) -> int:
        """Number of overlap seams reported by the stitcher."""

        return len(self.seams)


@dataclass(frozen=True, slots=True)
class SweepResultSnapshot:
    """Small result summary; the full frame remains owned by the presenter."""

    present: bool = False
    sweep_id: int = 0
    bin_count: int = 0
    requested_start_hz: float = 0.0
    requested_stop_hz: float = 0.0
    nominal_rbw_hz: float = 0.0
    quality: SweepQualitySnapshot = field(default_factory=SweepQualitySnapshot)
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SweepWorkspaceSnapshot:
    """Everything the Wideband Sweep workspace needs for one UI refresh."""

    generation: int = 0
    run: SweepRunSnapshot = field(default_factory=SweepRunSnapshot)
    plan: SweepPlanSnapshot | None = None
    result: SweepResultSnapshot = field(default_factory=SweepResultSnapshot)
    error: str | None = None
    stale: bool = False


__all__ = [
    "SweepPlanSegmentSnapshot",
    "SweepPlanSnapshot",
    "SweepQualitySnapshot",
    "SweepResultSnapshot",
    "SweepRunSnapshot",
    "SweepRunStatus",
    "SweepSeamSnapshot",
    "SweepWorkspaceSnapshot",
]
