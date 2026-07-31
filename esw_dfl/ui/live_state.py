"""Immutable presentation state for the Live Monitor workspace.

All types in this module are plain dataclasses: they carry no Qt objects,
no numpy buffers and no native handles, so the workspace can render them
from a GUI timer without ever touching the controller thread.  Values are
pre-formatted strings so the presenter stays the single place where units
and number formatting are decided.

Field-level *requested/applied* semantics:

* ``requested`` — what the user asked for (formatted);
* ``applied`` — what the device/engine confirmed (``None`` until the first
  controller publication carries an applied value for the field);
* ``pending`` — requested differs from applied, or applied is not yet known;
* ``unsupported`` — the requested value was rejected by validation and was
  never sent to the device;
* ``reason`` — human-readable explanation for the unsupported/pending state.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..sdr.contracts import CalibrationStatus, ComputeBackendKind
from ..sdr.controller import LiveControllerState


@dataclass(frozen=True, slots=True)
class RequestedAppliedValue:
    """One field of the requested/applied comparison table."""

    field: str
    requested: str
    applied: str | None = None
    pending: bool = False
    unsupported: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class BackendBadge:
    """Compute backend indicator: what was asked, what runs, what happened."""

    requested: ComputeBackendKind
    active: ComputeBackendKind | None
    available: tuple[ComputeBackendKind, ...]
    fallback_count: int = 0
    note: str | None = None


@dataclass(frozen=True, slots=True)
class CalibrationBadge:
    """Calibration provenance badge derived from the latest spectrum frame."""

    status: CalibrationStatus
    profile_id: str | None = None
    applicable: bool = False
    note: str | None = None


@dataclass(frozen=True, slots=True)
class QualityFlagItem:
    """One row of the quality panel (loss, gap, overload, fallback...)."""

    label: str
    value: str
    severity: str  # "ok" | "warn" | "error"


@dataclass(frozen=True, slots=True)
class RecordingHookState:
    """State of the recording *action hook* (no recorder UI yet)."""

    supported: bool
    active: bool = False
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class LiveMonitorSnapshot:
    """Everything the Live Monitor workspace needs for one UI refresh."""

    generation: int
    state: LiveControllerState
    requested_applied: tuple[RequestedAppliedValue, ...] = ()
    backend: BackendBadge | None = None
    calibration: CalibrationBadge | None = None
    quality: tuple[QualityFlagItem, ...] = ()
    recording: RecordingHookState | None = None
    frame_rate_hz: float = 0.0
    error: str | None = None
    stale: bool = False


__all__ = [
    "BackendBadge",
    "CalibrationBadge",
    "LiveMonitorSnapshot",
    "QualityFlagItem",
    "RecordingHookState",
    "RequestedAppliedValue",
]
