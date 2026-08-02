"""Immutable presentation state for the Diagnostics workspace.

Plain dataclasses only: no Qt objects, no numpy buffers, no native handles.
Values are pre-formatted strings so the presenter stays the single place where
number/unit formatting and anonymization are decided.  The workspace holds no
authoritative state — it only renders what :class:`DiagnosticsPresenter`
publishes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ValidationRunState(StrEnum):
    """Lifecycle of the optional heavy offline validation run."""

    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DiagnosticsSectionSnapshot:
    """One labelled diagnostics section as pre-formatted key/value rows."""

    title: str
    rows: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ValidationRowSnapshot:
    """One P15 validation-case summary row."""

    name: str
    status: str  # PASS | FAIL | NOT_VERIFIED (plain string for UI rendering)
    detail: str


@dataclass(frozen=True, slots=True)
class SupportBundleSnapshot:
    """State of the last support-bundle export (anonymized)."""

    path_hint: str | None = None
    file_count: str = "0"
    size: str = "0"
    error: str | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticsSnapshot:
    """Everything the Diagnostics workspace needs for one UI refresh."""

    generation: int
    sections: tuple[DiagnosticsSectionSnapshot, ...] = ()
    validation_state: ValidationRunState = ValidationRunState.IDLE
    validation_rows: tuple[ValidationRowSnapshot, ...] = ()
    support_bundle: SupportBundleSnapshot | None = None
    hardware_confirmed: bool = False
    error: str | None = None
    stale: bool = False


__all__ = [
    "DiagnosticsSectionSnapshot",
    "DiagnosticsSnapshot",
    "SupportBundleSnapshot",
    "ValidationRowSnapshot",
    "ValidationRunState",
]
