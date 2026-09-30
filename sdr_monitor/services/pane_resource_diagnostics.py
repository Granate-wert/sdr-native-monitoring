"""Finite, immutable first-cause codes for the existing pane owner boundary.

No exception object, vendor text, endpoint, serial bytes or traceback is stored
in a diagnostic. tinySA contributes its already validated cached scalar codes;
this is not an extra serial query or a new acquisition/identity model.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .tinysa_owned_acquisition import TinySaAcquisitionFailure


class PaneFailureStage(StrEnum):
    REARM = "rearm"
    START = "start"
    ADMISSION = "admission"
    OWNER_POLL = "owner_poll"
    PUBLICATION_VALIDATION = "publication_validation"
    PREPARE = "prepare"
    QUEUE = "queue"
    ADVANCE = "advance"
    STOP = "stop"
    CONTROL_RELEASE = "control_release"
    LEASE_RELEASE = "lease_release"
    TRANSACTION = "transaction"
    RENDER = "render"


class PaneFailureReason(StrEnum):
    OPERATION_FAILED = "operation_failed"
    INVALID_ADMISSION = "invalid_admission"
    INVALID_PUBLICATION = "invalid_publication"
    INSTRUMENT_FAILURE = "instrument_failure"


@dataclass(frozen=True, slots=True)
class PaneResourceFailure:
    stage: PaneFailureStage
    reason: PaneFailureReason = PaneFailureReason.OPERATION_FAILED
    instrument: TinySaAcquisitionFailure | None = None

    def __post_init__(self) -> None:
        if (type(self.stage) is not PaneFailureStage or type(self.reason) is not PaneFailureReason
                or self.instrument is not None and type(self.instrument) is not TinySaAcquisitionFailure):
            raise TypeError("pane diagnostics require fixed scalar codes")


class PaneDiagnosticError(RuntimeError):
    """Internal carrier; public consumers use validated codes, never str(error)."""

    def __init__(self, message: str, *, failure: PaneResourceFailure | None = None) -> None:
        if failure is not None and type(failure) is not PaneResourceFailure:
            raise TypeError("pane diagnostic carrier requires fixed scalar codes")
        super().__init__(message)
        self.failure = failure


def pane_failure_from_exception(error: Exception, stage: PaneFailureStage, *,
                                reason: PaneFailureReason = PaneFailureReason.OPERATION_FAILED) -> PaneResourceFailure:
    """Keep only a known immutable carrier, otherwise use a fixed fallback."""
    if isinstance(error, PaneDiagnosticError) and type(error.failure) is PaneResourceFailure:
        return error.failure
    return PaneResourceFailure(stage, reason)


__all__ = ["PaneDiagnosticError", "PaneFailureReason", "PaneFailureStage",
           "PaneResourceFailure", "pane_failure_from_exception"]
