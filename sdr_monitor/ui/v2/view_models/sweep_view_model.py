"""Public-signal Sweep adapter for UI V2; no planner, executor or service ownership."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from sdr_monitor.domain import SweepConfiguration, SweepPlan, SweepProgress, SweepResult, SweepState


class _SignalPort(Protocol):
    def connect(self, callback: Callable[..., None]) -> object: ...

    def disconnect(self, callback: Callable[..., None]) -> object: ...


class SweepPresenterPort(Protocol):
    """The complete frozen Sweep boundary UI V2 is permitted to call."""

    busy_changed: _SignalPort
    plan_ready: _SignalPort
    progress_changed: _SignalPort
    result_ready: _SignalPort
    export_ready: _SignalPort
    task_failed: _SignalPort

    def plan(self, configuration: SweepConfiguration) -> None: ...

    def execute(self, configuration: SweepConfiguration) -> None: ...

    def cancel(self) -> None: ...

    def export_result(self, result: SweepResult, output_path: Path) -> None: ...


@dataclass(frozen=True, slots=True)
class SweepViewState:
    """Immutable presentation state; result spectra are never fabricated here."""

    busy: bool = False
    plan: SweepPlan | None = None
    progress: SweepProgress | None = None
    result: SweepResult | None = None
    error: str | None = None
    exported_path: Path | None = None

    @property
    def can_cancel(self) -> bool:
        return self.busy and self.progress is not None and self.progress.state is SweepState.RUNNING

    @property
    def can_close(self) -> bool:
        return not self.busy and (self.progress is None or self.progress.state is not SweepState.RUNNING)


class SweepViewModel:
    """Own subscriptions only; all potentially blocking work remains in SweepPresenter."""

    def __init__(self, presenter: SweepPresenterPort) -> None:
        self._presenter = presenter
        self._listeners: list[Callable[[SweepViewState], None]] = []
        self._state = SweepViewState()
        presenter.busy_changed.connect(self._on_busy_changed)
        presenter.plan_ready.connect(self._on_plan_ready)
        presenter.progress_changed.connect(self._on_progress_changed)
        presenter.result_ready.connect(self._on_result_ready)
        presenter.export_ready.connect(self._on_export_ready)
        presenter.task_failed.connect(self._on_task_failed)

    @property
    def state(self) -> SweepViewState:
        return self._state

    def subscribe(self, listener: Callable[[SweepViewState], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        listener(self._state)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def plan(self, configuration: SweepConfiguration) -> bool:
        if self._state.busy:
            return False
        self._presenter.plan(configuration)
        return True

    def execute(self, configuration: SweepConfiguration) -> bool:
        if self._state.busy or self._state.plan is None or self._state.plan.configuration != configuration:
            return False
        self._presenter.execute(configuration)
        return True

    def cancel(self) -> bool:
        if not self._state.can_cancel:
            return False
        self._presenter.cancel()
        return True

    def export_result(self, output_path: Path) -> bool:
        if self._state.busy or self._state.result is None:
            return False
        self._presenter.export_result(self._state.result, output_path)
        return True

    def dispose(self) -> None:
        for signal, callback in (
            (self._presenter.busy_changed, self._on_busy_changed),
            (self._presenter.plan_ready, self._on_plan_ready),
            (self._presenter.progress_changed, self._on_progress_changed),
            (self._presenter.result_ready, self._on_result_ready),
            (self._presenter.export_ready, self._on_export_ready),
            (self._presenter.task_failed, self._on_task_failed),
        ):
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        self._listeners.clear()

    def _on_busy_changed(self, busy: bool) -> None:
        self._set_state(replace(self._state, busy=bool(busy)))

    def _on_plan_ready(self, plan: object) -> None:
        if isinstance(plan, SweepPlan):
            self._set_state(
                replace(self._state, plan=plan, progress=None, result=None, error=None, exported_path=None)
            )

    def _on_progress_changed(self, progress: object) -> None:
        if isinstance(progress, SweepProgress):
            self._set_state(replace(self._state, progress=progress, error=None))

    def _on_result_ready(self, result: object) -> None:
        if isinstance(result, SweepResult):
            self._set_state(replace(self._state, result=result, error=result.error))

    def _on_export_ready(self, output_path: object) -> None:
        if isinstance(output_path, Path):
            self._set_state(replace(self._state, exported_path=output_path))

    def _on_task_failed(self, error: object) -> None:
        message = str(error).strip()
        if message:
            self._set_state(replace(self._state, error=message))

    def _set_state(self, state: SweepViewState) -> None:
        self._state = state
        for listener in tuple(self._listeners):
            listener(self._state)
