"""Deferred, presentation-only diagnostics adapter for UI V2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from sdr_monitor.domain import DiagnosticsSnapshot, SelfTestResult, SupportBundleResult


class _SignalPort(Protocol):
    def connect(self, callback: Callable[..., None]) -> object: ...

    def disconnect(self, callback: Callable[..., None]) -> object: ...


class DiagnosticsPresenterPort(Protocol):
    """The frozen public presenter boundary admitted by UI2-10A."""

    snapshot_changed: _SignalPort
    self_tests_changed: _SignalPort
    bundle_ready: _SignalPort
    task_failed: _SignalPort
    busy_changed: _SignalPort

    def refresh(self) -> None: ...

    def run_self_tests(self) -> None: ...

    def cancel(self) -> None: ...

    def export_bundle(self, output_dir: Path) -> None: ...

    def shutdown(self) -> None: ...


DiagnosticsPresenterFactory = Callable[[], DiagnosticsPresenterPort]


@dataclass(frozen=True, slots=True)
class DiagnosticsViewState:
    """Only immutable diagnostics outputs, never device or raw-I/Q state."""

    loaded: bool = False
    snapshot: DiagnosticsSnapshot | None = None
    self_tests: tuple[SelfTestResult, ...] = ()
    bundle: SupportBundleResult | None = None
    busy: bool = False
    error: str | None = None

    @property
    def can_close(self) -> bool:
        return not self.busy


class DeferredDiagnosticsViewModel:
    """Construct the side-effecting frozen presenter only after an explicit command."""

    def __init__(self, presenter_factory: DiagnosticsPresenterFactory) -> None:
        self._presenter_factory = presenter_factory
        self._presenter: DiagnosticsPresenterPort | None = None
        self._state = DiagnosticsViewState()
        self._listeners: list[Callable[[DiagnosticsViewState], None]] = []
        self._disposed = False

    @property
    def state(self) -> DiagnosticsViewState:
        return self._state

    def subscribe(self, listener: Callable[[DiagnosticsViewState], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        listener(self._state)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def load(self) -> bool:
        """Create and subscribe the external presenter after a visible user action."""

        if self._disposed or self._state.busy:
            return False
        if self._presenter is None:
            try:
                presenter = self._presenter_factory()
            except Exception as error:  # Boundary failures are visible, not retried implicitly.
                self._set_state(replace(self._state, error=str(error).strip() or type(error).__name__))
                return False
            self._presenter = presenter
            self._connect(presenter)
            self._set_state(replace(self._state, loaded=True, error=None))
        self._presenter.refresh()
        return True

    def refresh(self) -> bool:
        return self.load()

    def run_self_tests(self) -> bool:
        presenter = self._ready_presenter()
        if presenter is None:
            return False
        presenter.run_self_tests()
        return True

    def export_bundle(self, output_dir: str) -> bool:
        presenter = self._ready_presenter()
        location = output_dir.strip()
        if presenter is None or not location:
            return False
        presenter.export_bundle(Path(location))
        return True

    def cancel(self) -> bool:
        presenter = self._presenter
        if presenter is None or not self._state.busy or self._disposed:
            return False
        presenter.cancel()
        return True

    def prepare_shutdown(self):
        """Quiesce an already-created presenter; never instantiate on close."""
        presenter = self._presenter
        if presenter is None:
            self.dispose(shutdown_presenter=False)
            return None
        prepare = getattr(presenter, "prepare_shutdown", None)
        finish = getattr(presenter, "finish_shutdown", None)
        if not callable(prepare) or not callable(finish):
            raise TypeError("presenter has no split shutdown contract")
        prepare()
        self.dispose(shutdown_presenter=False)
        return finish

    def dispose(self, *, shutdown_presenter: bool = True) -> None:
        if self._disposed:
            return
        self._disposed = True
        presenter = self._presenter
        if presenter is not None:
            for signal, callback in self._connections(presenter):
                try:
                    signal.disconnect(callback)
                except (RuntimeError, TypeError, ValueError):
                    pass
            if shutdown_presenter:
                presenter.shutdown()
        self._listeners.clear()

    def _ready_presenter(self) -> DiagnosticsPresenterPort | None:
        if self._disposed or self._state.busy:
            return None
        return self._presenter

    def _connect(self, presenter: DiagnosticsPresenterPort) -> None:
        for signal, callback in self._connections(presenter):
            signal.connect(callback)

    def _connections(self, presenter: DiagnosticsPresenterPort) -> tuple[tuple[_SignalPort, Callable[..., None]], ...]:
        return (
            (presenter.snapshot_changed, self._on_snapshot_changed),
            (presenter.self_tests_changed, self._on_self_tests_changed),
            (presenter.bundle_ready, self._on_bundle_ready),
            (presenter.task_failed, self._on_task_failed),
            (presenter.busy_changed, self._on_busy_changed),
        )

    def _on_snapshot_changed(self, snapshot: object) -> None:
        if isinstance(snapshot, DiagnosticsSnapshot):
            self._set_state(replace(self._state, snapshot=snapshot, error=None))

    def _on_self_tests_changed(self, values: object) -> None:
        if isinstance(values, (tuple, list)) and all(isinstance(item, SelfTestResult) for item in values):
            self._set_state(replace(self._state, self_tests=tuple(values), error=None))

    def _on_bundle_ready(self, result: object) -> None:
        if isinstance(result, SupportBundleResult):
            self._set_state(replace(self._state, bundle=result, error=None))

    def _on_task_failed(self, error: object) -> None:
        message = str(error).strip()
        if message:
            self._set_state(replace(self._state, error=message))

    def _on_busy_changed(self, busy: bool) -> None:
        self._set_state(replace(self._state, busy=bool(busy)))

    def _set_state(self, state: DiagnosticsViewState) -> None:
        self._state = state
        for listener in tuple(self._listeners):
            listener(state)


__all__ = [
    "DeferredDiagnosticsViewModel",
    "DiagnosticsPresenterFactory",
    "DiagnosticsPresenterPort",
    "DiagnosticsViewState",
]
