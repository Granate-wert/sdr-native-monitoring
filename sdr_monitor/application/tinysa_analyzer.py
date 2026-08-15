"""Qt-free tinySA one-shot trace and explicit settings application use cases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollection,
)
from ..services.tinysa_sweep_settings_controller import (
    R11W_TINYSA_SETTINGS_CONFIRMATION,
    TinySaSettingsApplyResult,
    TinySaSettingsApplyStatus,
    TinySaSweepSettingsPlan,
)
from ..services.tinysa_trace_parser import TinySaSpectrumTrace
from ..services.tinysa_trace_presentation import (
    TinySaTracePresentation,
    reduce_tinysa_trace_for_width,
)


class TinySaAnalyzerPhase(StrEnum):
    READY = "ready"
    BUSY = "busy"
    TRACE_READY = "trace_ready"
    SETTINGS_REVIEW = "settings_review"
    FAULTED = "faulted"


class TinySaAnalyzerReason(StrEnum):
    NO_SETTINGS_CHANGES = "no_settings_changes"
    NO_PENDING_SETTINGS = "no_pending_settings"
    SETTINGS_CANCELLED = "settings_cancelled"
    SETTINGS_ACKNOWLEDGED_UNVERIFIED = "settings_acknowledged_unverified"
    TRACE_COLLECTION_FAILED = "trace_collection_failed"
    SETTINGS_APPLICATION_FAILED = "settings_application_failed"
    OPERATION_ALREADY_PENDING = "operation_already_pending"


class TinySaAnalyzerOperationError(RuntimeError):
    """Redacted application-boundary failure."""


@dataclass(frozen=True, slots=True)
class TinySaAnalyzerSnapshot:
    phase: TinySaAnalyzerPhase
    can_collect: bool
    can_review_settings: bool
    can_confirm_settings: bool
    has_analytical_trace: bool
    reason: TinySaAnalyzerReason | None = None

    def __post_init__(self) -> None:
        phase = TinySaAnalyzerPhase(self.phase)
        object.__setattr__(self, "phase", phase)
        if self.reason is not None:
            object.__setattr__(self, "reason", TinySaAnalyzerReason(self.reason))
        if phase is TinySaAnalyzerPhase.BUSY:
            expected = (False, False, False)
        elif phase is TinySaAnalyzerPhase.SETTINGS_REVIEW:
            expected = (False, False, True)
        else:
            expected = (True, True, False)
        if (self.can_collect, self.can_review_settings, self.can_confirm_settings) != expected:
            raise ValueError("tinySA analyzer action flags must match the current phase")
        if phase is TinySaAnalyzerPhase.TRACE_READY and not self.has_analytical_trace:
            raise ValueError("tinySA trace-ready phase requires a retained analytical trace")


@dataclass(frozen=True, slots=True)
class TinySaSettingsReview:
    commands: tuple[str, ...]
    warnings: tuple[str, ...]
    state_readback_available_for_all_fields: bool = False
    previous_state_restorable: bool = False

    def __post_init__(self) -> None:
        if not self.commands:
            raise ValueError("tinySA settings review requires at least one explicit change")
        if len(self.commands) > 7:
            raise ValueError("tinySA settings review exceeds the command bound")
        if self.state_readback_available_for_all_fields or self.previous_state_restorable:
            raise ValueError("tinySA settings review cannot overstate readback or rollback")


@dataclass(frozen=True, slots=True)
class TinySaTraceAcquisitionSummary:
    """Scalar-only completion facts that may cross the presenter/UI boundary."""

    point_count: int
    finite_point_count: int
    measurement_commands: int
    readback_commands: int
    retries: int
    port_closed: bool

    def __post_init__(self) -> None:
        if self.point_count < 2 or self.finite_point_count != self.point_count:
            raise ValueError("tinySA acquisition summary must describe one finite trace")
        if (self.measurement_commands, self.readback_commands, self.retries) != (1, 1, 0):
            raise ValueError("tinySA acquisition summary action accounting is invalid")
        if not self.port_closed:
            raise ValueError("tinySA acquisition summary requires a closed port")


@dataclass(frozen=True, slots=True)
class TinySaTraceApplicationResult:
    snapshot: TinySaAnalyzerSnapshot
    presentation: TinySaTracePresentation
    elapsed_seconds: float
    observed_points_per_second: float
    unit: str
    acquisition: TinySaTraceAcquisitionSummary

    def __post_init__(self) -> None:
        if self.snapshot.phase is not TinySaAnalyzerPhase.TRACE_READY:
            raise ValueError("tinySA trace result requires trace-ready state")
        if self.elapsed_seconds <= 0.0 or self.observed_points_per_second <= 0.0:
            raise ValueError("tinySA trace timing scalars must be positive")
        if self.unit != "dBm":
            raise ValueError("tinySA application trace must remain device-reported dBm")
        if not isinstance(self.acquisition, TinySaTraceAcquisitionSummary):
            raise TypeError("tinySA trace result requires scalar acquisition facts")
        if self.presentation.source_point_count != self.acquisition.point_count:
            raise ValueError("tinySA trace result presentation/source point count differs")


class TinySaTraceCollectorPort(Protocol):
    def collect(self, request: TinySaScanRawRequest) -> TinySaTraceCollection: ...


class TinySaSettingsExecutorPort(Protocol):
    def apply(
        self,
        plan: TinySaSweepSettingsPlan,
        *,
        confirmation: str,
    ) -> TinySaSettingsApplyResult: ...


class TinySaAnalyzerUseCases(Protocol):
    def current(self) -> TinySaAnalyzerSnapshot: ...
    def collect_trace(
        self, request: TinySaScanRawRequest, pixel_width: int
    ) -> TinySaTraceApplicationResult: ...
    def stage_settings(self, plan: TinySaSweepSettingsPlan) -> TinySaSettingsReview | None: ...
    def confirm_settings(self, *, user_confirmed: bool) -> TinySaAnalyzerSnapshot: ...


class TinySaAnalyzerApplicationService:
    """Own the full trace and expose only bounded presentation to the GUI."""

    def __init__(
        self,
        collector: TinySaTraceCollectorPort,
        settings_executor: TinySaSettingsExecutorPort,
    ) -> None:
        if not callable(getattr(collector, "collect", None)):
            raise TypeError("tinySA collector must provide collect")
        if not callable(getattr(settings_executor, "apply", None)):
            raise TypeError("tinySA settings executor must provide apply")
        self._collector = collector
        self._settings_executor = settings_executor
        self._analytical_trace: TinySaSpectrumTrace | None = None
        self._pending_settings: TinySaSweepSettingsPlan | None = None
        self._snapshot = self._idle_snapshot()

    def current(self) -> TinySaAnalyzerSnapshot:
        return self._snapshot

    def analytical_trace(self) -> TinySaSpectrumTrace | None:
        """Return the immutable full trace for measurements/export, never for repaint."""

        return self._analytical_trace

    def collect_trace(
        self,
        request: TinySaScanRawRequest,
        pixel_width: int,
    ) -> TinySaTraceApplicationResult:
        if not isinstance(request, TinySaScanRawRequest):
            raise TypeError("tinySA trace collection requires a validated request")
        if self._pending_settings is not None:
            raise TinySaAnalyzerOperationError("tinySA operation is already pending")
        self._snapshot = self._busy_snapshot()
        try:
            collection = self._collector.collect(request)
            if not isinstance(collection, TinySaTraceCollection):
                raise TypeError("collector returned an invalid result")
            presentation = reduce_tinysa_trace_for_width(collection.trace, pixel_width)
        except Exception as error:
            self._snapshot = self._faulted(TinySaAnalyzerReason.TRACE_COLLECTION_FAILED)
            raise TinySaAnalyzerOperationError("tinySA trace collection failed") from error
        self._analytical_trace = collection.trace
        self._snapshot = self._idle_snapshot()
        return TinySaTraceApplicationResult(
            snapshot=self._snapshot,
            presentation=presentation,
            elapsed_seconds=collection.elapsed_seconds,
            observed_points_per_second=(
                collection.trace.values_dbm.size / collection.elapsed_seconds
            ),
            unit=collection.trace.unit,
            acquisition=TinySaTraceAcquisitionSummary(
                point_count=int(collection.trace.values_dbm.size),
                finite_point_count=int(collection.trace.values_dbm.size),
                measurement_commands=collection.measurement_commands,
                readback_commands=collection.readback_commands,
                retries=collection.retries,
                port_closed=collection.port_closed,
            ),
        )

    def stage_settings(self, plan: TinySaSweepSettingsPlan) -> TinySaSettingsReview | None:
        if not isinstance(plan, TinySaSweepSettingsPlan):
            raise TypeError("tinySA settings review requires a validated plan")
        if self._pending_settings is not None:
            raise TinySaAnalyzerOperationError("tinySA operation is already pending")
        commands = plan.commands
        if not commands:
            self._snapshot = self._idle_snapshot(TinySaAnalyzerReason.NO_SETTINGS_CHANGES)
            return None
        review = TinySaSettingsReview(commands=commands, warnings=plan.warnings)
        self._pending_settings = plan
        self._snapshot = TinySaAnalyzerSnapshot(
            phase=TinySaAnalyzerPhase.SETTINGS_REVIEW,
            can_collect=False,
            can_review_settings=False,
            can_confirm_settings=True,
            has_analytical_trace=self._analytical_trace is not None,
        )
        return review

    def confirm_settings(self, *, user_confirmed: bool) -> TinySaAnalyzerSnapshot:
        pending = self._pending_settings
        if pending is None:
            self._snapshot = self._idle_snapshot(TinySaAnalyzerReason.NO_PENDING_SETTINGS)
            return self._snapshot
        if not user_confirmed:
            self._pending_settings = None
            self._snapshot = self._idle_snapshot(TinySaAnalyzerReason.SETTINGS_CANCELLED)
            return self._snapshot
        self._snapshot = self._busy_snapshot()
        try:
            result = self._settings_executor.apply(
                pending,
                confirmation=R11W_TINYSA_SETTINGS_CONFIRMATION,
            )
            if (
                not isinstance(result, TinySaSettingsApplyResult)
                or result.status is not TinySaSettingsApplyStatus.ACKNOWLEDGED_UNVERIFIED
                or result.state_verified
                or result.previous_state_restored
            ):
                raise TypeError("settings executor overstated the result")
        except Exception as error:
            self._pending_settings = None
            self._snapshot = self._faulted(TinySaAnalyzerReason.SETTINGS_APPLICATION_FAILED)
            raise TinySaAnalyzerOperationError("tinySA settings application failed") from error
        self._pending_settings = None
        self._snapshot = self._idle_snapshot(
            TinySaAnalyzerReason.SETTINGS_ACKNOWLEDGED_UNVERIFIED
        )
        return self._snapshot

    def _idle_snapshot(
        self,
        reason: TinySaAnalyzerReason | None = None,
    ) -> TinySaAnalyzerSnapshot:
        has_trace = self._analytical_trace is not None
        return TinySaAnalyzerSnapshot(
            phase=(TinySaAnalyzerPhase.TRACE_READY if has_trace else TinySaAnalyzerPhase.READY),
            can_collect=True,
            can_review_settings=True,
            can_confirm_settings=False,
            has_analytical_trace=has_trace,
            reason=reason,
        )

    def _busy_snapshot(self) -> TinySaAnalyzerSnapshot:
        return TinySaAnalyzerSnapshot(
            phase=TinySaAnalyzerPhase.BUSY,
            can_collect=False,
            can_review_settings=False,
            can_confirm_settings=False,
            has_analytical_trace=self._analytical_trace is not None,
        )

    def _faulted(self, reason: TinySaAnalyzerReason) -> TinySaAnalyzerSnapshot:
        return TinySaAnalyzerSnapshot(
            phase=TinySaAnalyzerPhase.FAULTED,
            can_collect=True,
            can_review_settings=True,
            can_confirm_settings=False,
            has_analytical_trace=self._analytical_trace is not None,
            reason=reason,
        )


__all__ = [
    "TinySaAnalyzerApplicationService",
    "TinySaAnalyzerOperationError",
    "TinySaAnalyzerPhase",
    "TinySaAnalyzerReason",
    "TinySaAnalyzerSnapshot",
    "TinySaAnalyzerUseCases",
    "TinySaSettingsExecutorPort",
    "TinySaSettingsReview",
    "TinySaTraceAcquisitionSummary",
    "TinySaTraceApplicationResult",
    "TinySaTraceCollectorPort",
]
