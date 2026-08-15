"""Explicit one-owner HackRF Live orchestration over an R11-O permit.

This Qt-free control-plane service is deliberately inert until its caller
supplies both a valid permit and an explicit confirmation.  It owns only the
bounded coarse R11-L control returned by the factory: no raw I/Q, device route,
SDK handle, discovery, retry or unrelated workflow path enters this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import threading
from typing import Protocol

from .hackrf_activation_preflight import (
    HackrfActivationPermit,
    _is_issued_hackrf_activation_permit,
)


_MIN_STOP_TIMEOUT_MS = 1
_MAX_STOP_TIMEOUT_MS = 5_000
_MAX_POLL_ITEMS = 256


class HackrfProductLiveState(StrEnum):
    """Finite owner states, separate from a native receiver state."""

    IDLE = "idle"
    ACTIVATING = "activating"
    ACTIVE = "active"
    STOPPING = "stopping"
    FAULTED = "faulted"


class HackrfProductLiveFailure(StrEnum):
    """Redacted fail-closed outcomes visible to a future presenter."""

    PERMIT_NOT_ADMITTED = "permit_not_admitted"
    CONFIRMATION_REQUIRED = "confirmation_required"
    ALREADY_ACTIVE = "already_active"
    FACTORY_FAILED = "factory_failed"
    CONTROL_CONTRACT = "control_contract"
    NOT_ACTIVE = "not_active"
    STOP_FAILED = "stop_failed"
    FAULTED = "faulted"


class HackrfRuntimeControlPort(Protocol):
    """The existing R11-L coarse native surface; raw I/Q is intentionally absent."""

    def poll_spectrum_frames(self, max_items: int = 0) -> list[object]: ...

    def metrics(self) -> object: ...

    def stop(self, timeout_ms: int) -> object: ...


class HackrfNativeFactoryPort(Protocol):
    """One explicit factory action; implementations must not retry implicitly."""

    def create(self, permit: HackrfActivationPermit) -> HackrfRuntimeControlPort: ...


@dataclass(frozen=True, slots=True)
class HackrfProductLiveSnapshot:
    state: HackrfProductLiveState
    active: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", HackrfProductLiveState(self.state))
        if self.active is not (self.state is HackrfProductLiveState.ACTIVE):
            raise ValueError("active must match the product Live state")


@dataclass(frozen=True, slots=True)
class HackrfProductLiveStartResult:
    snapshot: HackrfProductLiveSnapshot
    failure: HackrfProductLiveFailure | None = None

    def __post_init__(self) -> None:
        if self.failure is not None:
            object.__setattr__(self, "failure", HackrfProductLiveFailure(self.failure))

    @property
    def started(self) -> bool:
        return self.failure is None and self.snapshot.state is HackrfProductLiveState.ACTIVE


@dataclass(frozen=True, slots=True)
class HackrfProductLiveStopResult:
    snapshot: HackrfProductLiveSnapshot
    failure: HackrfProductLiveFailure | None = None

    def __post_init__(self) -> None:
        if self.failure is not None:
            object.__setattr__(self, "failure", HackrfProductLiveFailure(self.failure))

    @property
    def stopped(self) -> bool:
        return self.failure is None and self.snapshot.state is HackrfProductLiveState.IDLE


def _valid_control(value: object) -> bool:
    return all(callable(getattr(value, name, None)) for name in ("poll_spectrum_frames", "metrics", "stop"))


def _stop_complete(value: object) -> bool:
    complete = getattr(value, "complete", None)
    if not callable(complete):
        return False
    try:
        return complete() is True
    except Exception:
        return False


class HackrfProductLiveCoordinator:
    """Own one explicitly confirmed factory result until explicit clean stop."""

    def __init__(self, factory: HackrfNativeFactoryPort) -> None:
        if not callable(getattr(factory, "create", None)):
            raise ValueError("HackRF factory must provide create")
        self._factory = factory
        self._lock = threading.RLock()
        self._state = HackrfProductLiveState.IDLE
        self._control: HackrfRuntimeControlPort | None = None

    def snapshot(self) -> HackrfProductLiveSnapshot:
        with self._lock:
            return HackrfProductLiveSnapshot(
                state=self._state,
                active=self._state is HackrfProductLiveState.ACTIVE,
            )

    def start_after_confirmation(
        self,
        permit: HackrfActivationPermit,
        *,
        user_confirmed: bool,
    ) -> HackrfProductLiveStartResult:
        """Issue exactly one explicit factory request; never discover or retry."""

        if not _is_issued_hackrf_activation_permit(permit):
            return HackrfProductLiveStartResult(
                self.snapshot(), HackrfProductLiveFailure.PERMIT_NOT_ADMITTED
            )
        if user_confirmed is not True:
            return HackrfProductLiveStartResult(
                self.snapshot(), HackrfProductLiveFailure.CONFIRMATION_REQUIRED
            )
        with self._lock:
            if self._state is HackrfProductLiveState.FAULTED:
                return HackrfProductLiveStartResult(
                    self.snapshot(), HackrfProductLiveFailure.FAULTED
                )
            if self._state is not HackrfProductLiveState.IDLE:
                return HackrfProductLiveStartResult(
                    self.snapshot(), HackrfProductLiveFailure.ALREADY_ACTIVE
                )
            self._state = HackrfProductLiveState.ACTIVATING
        try:
            control = self._factory.create(permit)
        except Exception:
            with self._lock:
                self._state = HackrfProductLiveState.IDLE
            return HackrfProductLiveStartResult(
                self.snapshot(), HackrfProductLiveFailure.FACTORY_FAILED
            )
        if not _valid_control(control):
            with self._lock:
                self._state = HackrfProductLiveState.FAULTED
            return HackrfProductLiveStartResult(
                self.snapshot(), HackrfProductLiveFailure.CONTROL_CONTRACT
            )
        with self._lock:
            self._control = control
            self._state = HackrfProductLiveState.ACTIVE
        return HackrfProductLiveStartResult(self.snapshot())

    def poll_spectrum_frames(self, max_items: int = 0) -> tuple[object, ...]:
        """Forward bounded reduced SpectrumFrames from the active coarse control."""

        if isinstance(max_items, bool) or not isinstance(max_items, int) or not 0 <= max_items <= _MAX_POLL_ITEMS:
            raise ValueError(f"max_items must be an integer in [0, {_MAX_POLL_ITEMS}]")
        with self._lock:
            control = self._control if self._state is HackrfProductLiveState.ACTIVE else None
        if control is None:
            return ()
        try:
            return tuple(control.poll_spectrum_frames(max_items))
        except Exception:
            return ()

    def metrics(self) -> object | None:
        """Return scalar native metrics for an active owner, never raw I/Q."""

        with self._lock:
            control = self._control if self._state is HackrfProductLiveState.ACTIVE else None
        if control is None:
            return None
        try:
            return control.metrics()
        except Exception:
            return None

    def stop(self, timeout_ms: int) -> HackrfProductLiveStopResult:
        """Run only the existing explicit three-phase native stop once requested."""

        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not _MIN_STOP_TIMEOUT_MS <= timeout_ms <= _MAX_STOP_TIMEOUT_MS:
            raise ValueError(f"timeout_ms must be an integer in [{_MIN_STOP_TIMEOUT_MS}, {_MAX_STOP_TIMEOUT_MS}]")
        with self._lock:
            if self._state is HackrfProductLiveState.FAULTED:
                return HackrfProductLiveStopResult(
                    self.snapshot(), HackrfProductLiveFailure.FAULTED
                )
            if self._state is not HackrfProductLiveState.ACTIVE or self._control is None:
                return HackrfProductLiveStopResult(
                    self.snapshot(), HackrfProductLiveFailure.NOT_ACTIVE
                )
            control = self._control
            self._state = HackrfProductLiveState.STOPPING
        try:
            stopped = _stop_complete(control.stop(timeout_ms))
        except Exception:
            stopped = False
        with self._lock:
            if not stopped:
                self._state = HackrfProductLiveState.ACTIVE
                return HackrfProductLiveStopResult(
                    self.snapshot(), HackrfProductLiveFailure.STOP_FAILED
                )
            self._control = None
            self._state = HackrfProductLiveState.IDLE
        return HackrfProductLiveStopResult(self.snapshot())


__all__ = [
    "HackrfNativeFactoryPort",
    "HackrfProductLiveCoordinator",
    "HackrfProductLiveFailure",
    "HackrfProductLiveSnapshot",
    "HackrfProductLiveStartResult",
    "HackrfProductLiveState",
    "HackrfProductLiveStopResult",
    "HackrfRuntimeControlPort",
]
