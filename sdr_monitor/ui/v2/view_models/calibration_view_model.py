"""Read-only calibration-profile adapter over the frozen public presenter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from sdr_monitor.domain import CalibrationApplicability, CalibrationProfile


class _SignalPort(Protocol):
    def connect(self, callback: Callable[..., None]) -> object: ...

    def disconnect(self, callback: Callable[..., None]) -> object: ...


class CalibrationProfilePresenterPort(Protocol):
    """Narrow no-write Calibration presenter boundary admitted by UI2-09A."""

    profiles_changed: _SignalPort
    applicability_changed: _SignalPort
    busy_changed: _SignalPort
    task_failed: _SignalPort

    def refresh(self) -> None: ...

    def compare(self, profile: CalibrationProfile) -> None: ...


@dataclass(frozen=True, slots=True)
class CalibrationProfileViewState:
    """Immutable V2 state; it never asserts active/dBm-valid calibration."""

    profiles: tuple[CalibrationProfile, ...] = ()
    selected: CalibrationProfile | None = None
    applicability: CalibrationApplicability | None = None
    busy: bool = False
    error: str | None = None


class CalibrationProfileViewModel:
    """Own signal subscriptions only; profile-store work remains in the presenter."""

    def __init__(self, presenter: CalibrationProfilePresenterPort) -> None:
        self._presenter = presenter
        self._state = CalibrationProfileViewState()
        self._listeners: list[Callable[[CalibrationProfileViewState], None]] = []
        presenter.profiles_changed.connect(self._on_profiles_changed)
        presenter.applicability_changed.connect(self._on_applicability_changed)
        presenter.busy_changed.connect(self._on_busy_changed)
        presenter.task_failed.connect(self._on_task_failed)

    @property
    def state(self) -> CalibrationProfileViewState:
        return self._state

    def subscribe(self, listener: Callable[[CalibrationProfileViewState], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        listener(self._state)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def refresh(self) -> bool:
        if self._state.busy:
            return False
        self._presenter.refresh()
        return True

    def select(self, profile: CalibrationProfile | None) -> bool:
        if self._state.busy or profile is None or profile not in self._state.profiles:
            return False
        self._set_state(replace(self._state, selected=profile, applicability=None, error=None))
        self._presenter.compare(profile)
        return True

    def dispose(self) -> None:
        for signal, callback in (
            (self._presenter.profiles_changed, self._on_profiles_changed),
            (self._presenter.applicability_changed, self._on_applicability_changed),
            (self._presenter.busy_changed, self._on_busy_changed),
            (self._presenter.task_failed, self._on_task_failed),
        ):
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        self._listeners.clear()

    def _on_profiles_changed(self, profiles: object) -> None:
        if not isinstance(profiles, tuple) or not all(isinstance(item, CalibrationProfile) for item in profiles):
            return
        selected = self._state.selected if self._state.selected in profiles else None
        self._set_state(replace(self._state, profiles=profiles, selected=selected, applicability=None, error=None))

    def _on_applicability_changed(self, value: object) -> None:
        if isinstance(value, CalibrationApplicability):
            selected = self._state.selected
            if selected is not None and value.profile_id == selected.profile_id and value.profile_version == selected.profile_version:
                self._set_state(replace(self._state, applicability=value, error=None))

    def _on_busy_changed(self, busy: bool) -> None:
        self._set_state(replace(self._state, busy=bool(busy)))

    def _on_task_failed(self, error: object) -> None:
        message = str(error).strip()
        if message:
            self._set_state(replace(self._state, error=message))

    def _set_state(self, state: CalibrationProfileViewState) -> None:
        self._state = state
        for listener in tuple(self._listeners):
            listener(state)
