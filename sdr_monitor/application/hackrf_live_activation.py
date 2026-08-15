"""Qt-free use cases for explicit HackRF activation, never default startup.

The adapter turns independently bounded R11-M admission, R11-O identity
preflight and R11-P ownership results into a small presenter-safe view state.
It does not construct vendor ports, coordinators or native factories itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..services import (
    HackrfActivationPreflight,
    HackrfActivationPreflightReason,
    HackrfActivationPermit,
    HackrfLiveActivationPlan,
    HackrfProductLiveCoordinator,
    HackrfProductLiveFailure,
    HackrfProductLiveStartResult,
    HackrfProductLiveState,
    HackrfProductLiveStopResult,
)
from ..services.hackrf_activation_preflight import _is_issued_hackrf_activation_permit


class HackrfActivationApplicationState(StrEnum):
    """Finite, user-visible control state with no device details."""

    READY_FOR_PREFLIGHT = "ready_for_preflight"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    BUSY = "busy"
    ACTIVE = "active"
    FAULTED = "faulted"


class HackrfActivationApplicationReason(StrEnum):
    """Stable redacted outcome labels spanning three R11 boundaries."""

    PREFLIGHT_PLAN_NOT_ADMITTED = "preflight_plan_not_admitted"
    PREFLIGHT_RUNTIME_OBSERVATION = "preflight_runtime_observation"
    PREFLIGHT_IDENTITY_MISMATCH = "preflight_identity_mismatch"
    PREFLIGHT_ALREADY_PENDING = "preflight_already_pending"
    COORDINATOR_BUSY = "coordinator_busy"
    CONFIRMATION_REQUIRED = "confirmation_required"
    NO_PENDING_PERMIT = "no_pending_permit"
    ALREADY_ACTIVE = "already_active"
    START_FAILED = "start_failed"
    STOP_FAILED = "stop_failed"
    FAULTED = "faulted"


@dataclass(frozen=True, slots=True)
class HackrfActivationApplicationSnapshot:
    """Presenter-safe state; a permit, serial and route never leave the service."""

    state: HackrfActivationApplicationState
    can_request_preflight: bool
    can_confirm_start: bool
    active: bool
    reason: HackrfActivationApplicationReason | None = None

    def __post_init__(self) -> None:
        state = HackrfActivationApplicationState(self.state)
        object.__setattr__(self, "state", state)
        if self.reason is not None:
            object.__setattr__(self, "reason", HackrfActivationApplicationReason(self.reason))
        expected = {
            HackrfActivationApplicationState.READY_FOR_PREFLIGHT: (True, False, False),
            HackrfActivationApplicationState.AWAITING_CONFIRMATION: (False, True, False),
            HackrfActivationApplicationState.BUSY: (False, False, False),
            HackrfActivationApplicationState.ACTIVE: (False, False, True),
            HackrfActivationApplicationState.FAULTED: (False, False, False),
        }[state]
        if (self.can_request_preflight, self.can_confirm_start, self.active) != expected:
            raise ValueError("activation application flags must match state")


class HackrfActivationPreflightPort(Protocol):
    """One explicit R11-O preflight request, controlled by the application event."""

    def verify(self, plan: HackrfLiveActivationPlan) -> HackrfActivationPreflight: ...


class HackrfActivationUseCases(Protocol):
    """The bounded API a future presenter may use without direct SDK access."""

    def current(self) -> HackrfActivationApplicationSnapshot: ...
    def request_preflight(self, plan: HackrfLiveActivationPlan) -> HackrfActivationApplicationSnapshot: ...
    def confirm_start(self, *, user_confirmed: bool) -> HackrfActivationApplicationSnapshot: ...
    def stop(self, timeout_ms: int) -> HackrfActivationApplicationSnapshot: ...
    def poll_spectrum_frames(self, max_items: int = 0) -> tuple[object, ...]: ...
    def metrics(self) -> object | None: ...


def _preflight_reason(value: HackrfActivationPreflightReason) -> HackrfActivationApplicationReason:
    return {
        HackrfActivationPreflightReason.PLAN_NOT_ADMITTED: HackrfActivationApplicationReason.PREFLIGHT_PLAN_NOT_ADMITTED,
        HackrfActivationPreflightReason.RUNTIME_OBSERVATION: HackrfActivationApplicationReason.PREFLIGHT_RUNTIME_OBSERVATION,
        HackrfActivationPreflightReason.IDENTITY_MISMATCH: HackrfActivationApplicationReason.PREFLIGHT_IDENTITY_MISMATCH,
    }[value]


def _start_reason(value: HackrfProductLiveFailure) -> HackrfActivationApplicationReason:
    if value is HackrfProductLiveFailure.CONFIRMATION_REQUIRED:
        return HackrfActivationApplicationReason.CONFIRMATION_REQUIRED
    if value is HackrfProductLiveFailure.ALREADY_ACTIVE:
        return HackrfActivationApplicationReason.ALREADY_ACTIVE
    if value is HackrfProductLiveFailure.FAULTED:
        return HackrfActivationApplicationReason.FAULTED
    return HackrfActivationApplicationReason.START_FAILED


class HackrfLiveActivationApplicationService:
    """Make the preflight → confirmation → one-owner transition inspectable."""

    def __init__(
        self,
        preflight: HackrfActivationPreflightPort,
        coordinator: HackrfProductLiveCoordinator,
    ) -> None:
        if not callable(getattr(preflight, "verify", None)):
            raise ValueError("HackRF preflight must provide verify")
        if not isinstance(coordinator, HackrfProductLiveCoordinator):
            raise ValueError("HackRF coordinator must be a bounded R11-P owner")
        self._preflight = preflight
        self._coordinator = coordinator
        self._pending_permit: HackrfActivationPermit | None = None
        self._snapshot = self._ready()

    def current(self) -> HackrfActivationApplicationSnapshot:
        return self._snapshot

    def request_preflight(self, plan: HackrfLiveActivationPlan) -> HackrfActivationApplicationSnapshot:
        """Explicitly request identity verification; never call it on construction."""

        if self._pending_permit is not None:
            return self._set_reason(HackrfActivationApplicationReason.PREFLIGHT_ALREADY_PENDING)
        coordinator_state = self._coordinator.snapshot().state
        if coordinator_state is HackrfProductLiveState.FAULTED:
            return self._faulted(HackrfActivationApplicationReason.FAULTED)
        if coordinator_state is HackrfProductLiveState.ACTIVE:
            return self._active_with_reason(HackrfActivationApplicationReason.ALREADY_ACTIVE)
        if coordinator_state is not HackrfProductLiveState.IDLE:
            return self._busy(HackrfActivationApplicationReason.COORDINATOR_BUSY)
        result = self._preflight.verify(plan)
        if (
            not isinstance(result, HackrfActivationPreflight)
            or result.permit is None
            or not _is_issued_hackrf_activation_permit(result.permit)
        ):
            self._pending_permit = None
            if isinstance(result, HackrfActivationPreflight) and result.reason is not None:
                return self._set_reason(_preflight_reason(result.reason))
            return self._set_reason(HackrfActivationApplicationReason.PREFLIGHT_RUNTIME_OBSERVATION)
        self._pending_permit = result.permit
        self._snapshot = HackrfActivationApplicationSnapshot(
            HackrfActivationApplicationState.AWAITING_CONFIRMATION,
            can_request_preflight=False,
            can_confirm_start=True,
            active=False,
        )
        return self._snapshot

    def confirm_start(self, *, user_confirmed: bool) -> HackrfActivationApplicationSnapshot:
        """Forward exactly one user decision to the one-owner coordinator."""

        permit = self._pending_permit
        if permit is None:
            return self._set_reason(HackrfActivationApplicationReason.NO_PENDING_PERMIT)
        result: HackrfProductLiveStartResult = self._coordinator.start_after_confirmation(
            permit,
            user_confirmed=user_confirmed,
        )
        if result.started:
            self._pending_permit = None
            self._snapshot = HackrfActivationApplicationSnapshot(
                HackrfActivationApplicationState.ACTIVE,
                can_request_preflight=False,
                can_confirm_start=False,
                active=True,
            )
            return self._snapshot
        if result.failure is HackrfProductLiveFailure.CONFIRMATION_REQUIRED:
            return self._set_reason(HackrfActivationApplicationReason.CONFIRMATION_REQUIRED)
        self._pending_permit = None
        return self._after_start_failure(result)

    def stop(self, timeout_ms: int) -> HackrfActivationApplicationSnapshot:
        """Retain active state when the existing native stop is incomplete."""

        result: HackrfProductLiveStopResult = self._coordinator.stop(timeout_ms)
        if result.stopped:
            self._snapshot = self._ready()
            return self._snapshot
        if result.failure is HackrfProductLiveFailure.FAULTED:
            return self._faulted(HackrfActivationApplicationReason.FAULTED)
        if result.failure is HackrfProductLiveFailure.NOT_ACTIVE:
            return self._set_reason(HackrfActivationApplicationReason.NO_PENDING_PERMIT)
        return self._active_with_reason(HackrfActivationApplicationReason.STOP_FAILED)

    def poll_spectrum_frames(self, max_items: int = 0) -> tuple[object, ...]:
        return self._coordinator.poll_spectrum_frames(max_items)

    def metrics(self) -> object | None:
        return self._coordinator.metrics()

    @staticmethod
    def _ready() -> HackrfActivationApplicationSnapshot:
        return HackrfActivationApplicationSnapshot(
            HackrfActivationApplicationState.READY_FOR_PREFLIGHT,
            can_request_preflight=True,
            can_confirm_start=False,
            active=False,
        )

    def _set_reason(
        self, reason: HackrfActivationApplicationReason
    ) -> HackrfActivationApplicationSnapshot:
        if self._snapshot.state is HackrfActivationApplicationState.AWAITING_CONFIRMATION:
            self._snapshot = HackrfActivationApplicationSnapshot(
                HackrfActivationApplicationState.AWAITING_CONFIRMATION,
                can_request_preflight=False,
                can_confirm_start=True,
                active=False,
                reason=reason,
            )
        elif self._coordinator.snapshot().state is HackrfProductLiveState.FAULTED:
            self._snapshot = self._faulted(HackrfActivationApplicationReason.FAULTED)
        elif self._coordinator.snapshot().state is HackrfProductLiveState.ACTIVE:
            self._snapshot = self._active_with_reason(reason)
        elif self._coordinator.snapshot().state is not HackrfProductLiveState.IDLE:
            self._snapshot = self._busy(HackrfActivationApplicationReason.COORDINATOR_BUSY)
        else:
            self._snapshot = HackrfActivationApplicationSnapshot(
                HackrfActivationApplicationState.READY_FOR_PREFLIGHT,
                can_request_preflight=True,
                can_confirm_start=False,
                active=False,
                reason=reason,
            )
        return self._snapshot

    def _after_start_failure(
        self, result: HackrfProductLiveStartResult
    ) -> HackrfActivationApplicationSnapshot:
        failure = result.failure
        if failure is HackrfProductLiveFailure.FAULTED:
            return self._faulted(HackrfActivationApplicationReason.FAULTED)
        return self._set_reason(_start_reason(failure or HackrfProductLiveFailure.FACTORY_FAILED))

    def _active_with_reason(
        self, reason: HackrfActivationApplicationReason
    ) -> HackrfActivationApplicationSnapshot:
        self._snapshot = HackrfActivationApplicationSnapshot(
            HackrfActivationApplicationState.ACTIVE,
            can_request_preflight=False,
            can_confirm_start=False,
            active=True,
            reason=reason,
        )
        return self._snapshot

    def _busy(
        self, reason: HackrfActivationApplicationReason
    ) -> HackrfActivationApplicationSnapshot:
        self._snapshot = HackrfActivationApplicationSnapshot(
            HackrfActivationApplicationState.BUSY,
            can_request_preflight=False,
            can_confirm_start=False,
            active=False,
            reason=reason,
        )
        return self._snapshot

    def _faulted(
        self, reason: HackrfActivationApplicationReason
    ) -> HackrfActivationApplicationSnapshot:
        self._snapshot = HackrfActivationApplicationSnapshot(
            HackrfActivationApplicationState.FAULTED,
            can_request_preflight=False,
            can_confirm_start=False,
            active=False,
            reason=reason,
        )
        return self._snapshot


__all__ = [
    "HackrfActivationApplicationReason",
    "HackrfActivationApplicationSnapshot",
    "HackrfActivationApplicationState",
    "HackrfActivationPreflightPort",
    "HackrfActivationUseCases",
    "HackrfLiveActivationApplicationService",
]
