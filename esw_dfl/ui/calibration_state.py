"""Immutable presentation snapshots for the P16 calibration workspace."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..sdr.calibration_store import CalibrationApplicationStatus


@dataclass(frozen=True, slots=True)
class CalibrationComparisonRow:
    """One explicit profile/current-settings comparison row."""

    field: str
    expected: str
    actual: str
    matches: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CalibrationProfileSnapshot:
    """Public profile summary; source paths are intentionally absent."""

    profile_id: str
    profile_version: int
    device_serial: str
    backend: str
    rf_port_path: str
    gain: str
    sample_rate: str
    bandwidth: str
    valid_range: str
    reference_plane: str
    created_at: str
    uncertainty: str
    point_count: int
    applicability: CalibrationApplicationStatus
    applicability_reason: str
    active: bool = False


@dataclass(frozen=True, slots=True)
class CalibrationPlotSnapshot:
    """Small immutable plot payload; it contains no numpy arrays."""

    frequency_hz: tuple[float, ...] = ()
    correction_db: tuple[float, ...] = ()
    uncertainty_db: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class CalibrationImportPreview:
    """Validated CSV preview before an immutable profile is finalized."""

    source_name: str = ""
    profile_id: str = ""
    profile_version: int = 0
    points: tuple[tuple[float, float, float], ...] = ()
    plot: CalibrationPlotSnapshot = field(default_factory=CalibrationPlotSnapshot)
    valid: bool = False
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CalibrationWorkspaceSnapshot:
    """Everything the calibration workspace needs for one refresh."""

    profiles: tuple[CalibrationProfileSnapshot, ...] = ()
    selected_profile_id: str | None = None
    selected_profile_version: int | None = None
    active_profile_id: str | None = None
    active_profile_version: int | None = None
    applicability: CalibrationApplicationStatus = CalibrationApplicationStatus.UNCALIBRATED
    applicability_reason: str = ""
    comparison: tuple[CalibrationComparisonRow, ...] = ()
    plot: CalibrationPlotSnapshot = field(default_factory=CalibrationPlotSnapshot)
    import_preview: CalibrationImportPreview | None = None
    error: str | None = None


__all__ = [
    "CalibrationComparisonRow",
    "CalibrationImportPreview",
    "CalibrationPlotSnapshot",
    "CalibrationProfileSnapshot",
    "CalibrationWorkspaceSnapshot",
]
