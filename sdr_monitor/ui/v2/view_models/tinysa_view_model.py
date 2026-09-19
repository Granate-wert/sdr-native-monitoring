"""Public-presenter adapters for the deferred UI V2 tinySA route.

The module retains only route-redacted identity, immutable analyzer snapshots
and the already bounded presentation trace.  It neither owns a serial port nor
constructs a service, collector, settings executor or presenter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

import numpy as np

from sdr_monitor.application.tinysa_analyzer import (
    TinySaAnalyzerSnapshot,
    TinySaSettingsReview,
    TinySaTraceApplicationResult,
)
from sdr_monitor.services.tinysa_serial_trace_collector import TinySaScanRawRequest
from sdr_monitor.services.tinysa_source_composition import (
    TinySaComposedSource,
    TinySaSourceSnapshot,
    TinySaVerifiedSource,
)
from sdr_monitor.services.tinysa_sweep_settings_controller import TinySaSweepSettingsPlan


class _SignalPort(Protocol):
    def connect(self, callback: Callable[..., None]) -> object: ...

    def disconnect(self, callback: Callable[..., None]) -> object: ...


class TinySaSourceActivationPresenterPort(Protocol):
    """The existing source presenter boundary admitted by UI2-10B."""

    snapshot_changed: _SignalPort
    composition_ready: _SignalPort
    busy_changed: _SignalPort
    task_failed: _SignalPort

    @property
    def current_snapshot(self) -> TinySaSourceSnapshot: ...

    @property
    def busy(self) -> bool: ...

    def discover(self) -> None: ...

    def select(self, source_id: str) -> None: ...

    def verify_selected(self) -> None: ...

    def compose_selected(self) -> None: ...

    def shutdown(self) -> None: ...


class TinySaAnalyzerPresenterPort(Protocol):
    """The existing analyzer presenter boundary; all device work stays there."""

    snapshot_changed: _SignalPort
    trace_ready: _SignalPort
    settings_review_ready: _SignalPort
    busy_changed: _SignalPort
    task_failed: _SignalPort

    @property
    def current_snapshot(self) -> TinySaAnalyzerSnapshot: ...

    def collect_trace(self, request: TinySaScanRawRequest, pixel_width: int) -> None: ...

    def stage_settings(self, plan: TinySaSweepSettingsPlan) -> None: ...

    def confirm_settings(self, *, user_confirmed: bool) -> None: ...

    def shutdown(self) -> None: ...


TinySaSourceActivationPresenterFactory = Callable[[], TinySaSourceActivationPresenterPort]


@dataclass(frozen=True, slots=True)
class TinySaAnalyzerBinding:
    """Opaque hand-off of an already-composed source to a V2 analyzer page."""

    source: TinySaVerifiedSource
    presenter: TinySaAnalyzerPresenterPort

    def __post_init__(self) -> None:
        if not isinstance(self.source, TinySaVerifiedSource):
            raise TypeError("tinySA V2 binding requires a verified source")
        required = (
            "collect_trace",
            "stage_settings",
            "confirm_settings",
            "shutdown",
        )
        if any(not callable(getattr(self.presenter, name, None)) for name in required):
            raise TypeError("tinySA V2 binding requires a complete analyzer presenter")


TinySaAnalyzerBindingFactory = Callable[[TinySaComposedSource], TinySaAnalyzerBinding]


@dataclass(frozen=True, slots=True)
class TinySaSourceActivationViewState:
    """Presentation state for explicit Discover → Select → Verify → Compose."""

    prepared: bool = False
    snapshot: TinySaSourceSnapshot | None = None
    busy: bool = False
    error: str | None = None
    analyzer_binding: TinySaAnalyzerBinding | None = None

    @property
    def can_close(self) -> bool:
        return not self.busy


class DeferredTinySaSourceActivationViewModel:
    """Create a serial-capable source presenter only for a visible command."""

    def __init__(
        self,
        presenter_factory: TinySaSourceActivationPresenterFactory,
        analyzer_binding_factory: TinySaAnalyzerBindingFactory,
    ) -> None:
        if not callable(presenter_factory) or not callable(analyzer_binding_factory):
            raise TypeError("tinySA V2 activation requires explicit factories")
        self._presenter_factory = presenter_factory
        self._analyzer_binding_factory = analyzer_binding_factory
        self._presenter: TinySaSourceActivationPresenterPort | None = None
        self._state = TinySaSourceActivationViewState()
        self._listeners: list[Callable[[TinySaSourceActivationViewState], None]] = []
        self._binding_listeners: list[Callable[[TinySaAnalyzerBinding], None]] = []
        self._disposed = False

    @property
    def state(self) -> TinySaSourceActivationViewState:
        return self._state

    def subscribe(
        self,
        listener: Callable[[TinySaSourceActivationViewState], None],
    ) -> Callable[[], None]:
        self._listeners.append(listener)
        listener(self._state)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def subscribe_analyzer_ready(
        self,
        listener: Callable[[TinySaAnalyzerBinding], None],
    ) -> Callable[[], None]:
        self._binding_listeners.append(listener)
        binding = self._state.analyzer_binding
        if binding is not None:
            listener(binding)

        def unsubscribe() -> None:
            if listener in self._binding_listeners:
                self._binding_listeners.remove(listener)

        return unsubscribe

    def discover(self) -> bool:
        presenter = self._ready_presenter()
        if presenter is None:
            return False
        presenter.discover()
        return True

    def select(self, source_id: str) -> bool:
        presenter = self._operable_presenter()
        identifier = source_id.strip()
        if presenter is None or not identifier:
            return False
        presenter.select(identifier)
        return True

    def verify_selected(self) -> bool:
        presenter = self._operable_presenter()
        if presenter is None:
            return False
        presenter.verify_selected()
        return True

    def compose_selected(self) -> bool:
        presenter = self._operable_presenter()
        if presenter is None:
            return False
        presenter.compose_selected()
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
        self._binding_listeners.clear()

    def _ready_presenter(self) -> TinySaSourceActivationPresenterPort | None:
        if self._disposed or self._state.busy or self._state.analyzer_binding is not None:
            return None
        if self._presenter is not None:
            return self._presenter
        try:
            presenter = self._presenter_factory()
            snapshot = presenter.current_snapshot
            if not isinstance(snapshot, TinySaSourceSnapshot):
                raise TypeError("tinySA activation presenter returned an invalid snapshot")
        except Exception:  # noqa: BLE001 - construction details can contain a route and must remain redacted.
            self._set_state(replace(self._state, error="tinySA source activation is unavailable"))
            return None
        self._presenter = presenter
        for signal, callback in self._connections(presenter):
            signal.connect(callback)
        self._set_state(
            TinySaSourceActivationViewState(
                prepared=True,
                snapshot=snapshot,
                busy=bool(presenter.busy),
            )
        )
        return presenter

    def _operable_presenter(self) -> TinySaSourceActivationPresenterPort | None:
        if self._disposed or self._state.busy or self._state.analyzer_binding is not None:
            return None
        return self._presenter

    def _connections(
        self,
        presenter: TinySaSourceActivationPresenterPort,
    ) -> tuple[tuple[_SignalPort, Callable[..., None]], ...]:
        return (
            (presenter.snapshot_changed, self._on_snapshot_changed),
            (presenter.composition_ready, self._on_composition_ready),
            (presenter.busy_changed, self._on_busy_changed),
            (presenter.task_failed, self._on_task_failed),
        )

    def _on_snapshot_changed(self, snapshot: object) -> None:
        if isinstance(snapshot, TinySaSourceSnapshot):
            self._set_state(replace(self._state, snapshot=snapshot, error=None))

    def _on_composition_ready(self, composed: object) -> None:
        if self._disposed or self._state.analyzer_binding is not None:
            return
        if not isinstance(composed, TinySaComposedSource):
            self._set_state(replace(self._state, error="tinySA source composition is invalid"))
            return
        try:
            binding = self._analyzer_binding_factory(composed)
        except Exception:  # noqa: BLE001 - registration details can contain endpoint implementation data.
            self._set_state(replace(self._state, error="tinySA V2 analyzer registration failed"))
            return
        self._set_state(replace(self._state, analyzer_binding=binding, error=None))
        for listener in tuple(self._binding_listeners):
            listener(binding)

    def _on_busy_changed(self, busy: object) -> None:
        self._set_state(replace(self._state, busy=bool(busy)))

    def _on_task_failed(self, error: object) -> None:
        self._set_state(replace(self._state, error=_error_text(error)))

    def _set_state(self, state: TinySaSourceActivationViewState) -> None:
        self._state = state
        for listener in tuple(self._listeners):
            listener(state)


@dataclass(frozen=True, slots=True)
class TinySaTraceViewFrame:
    """One immutable display-only dBm trace suitable for the V2 spectrum scene."""

    frequencies_hz: np.ndarray
    values: np.ndarray
    unit: str = "dBm"

    def __post_init__(self) -> None:
        frequencies = np.asarray(self.frequencies_hz, dtype=np.float64).reshape(-1)
        values = np.asarray(self.values, dtype=np.float32).reshape(-1)
        if frequencies.size < 2 or frequencies.size != values.size:
            raise ValueError("tinySA V2 trace frame axes are invalid")
        if not np.isfinite(frequencies).all() or not np.isfinite(values).all():
            raise ValueError("tinySA V2 trace frame must be finite")
        if np.any(np.diff(frequencies) <= 0.0) or self.unit != "dBm":
            raise ValueError("tinySA V2 trace frame has invalid provenance")
        frequencies.setflags(write=False)
        values.setflags(write=False)
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "values", values)


@dataclass(frozen=True, slots=True)
class TinySaAnalyzerViewState:
    """Immutable public analyzer outputs, never the full analytical trace."""

    source: TinySaVerifiedSource
    snapshot: TinySaAnalyzerSnapshot
    trace_frame: TinySaTraceViewFrame | None = None
    trace_result: TinySaTraceApplicationResult | None = None
    settings_review: TinySaSettingsReview | None = None
    busy: bool = False
    error: str | None = None

    @property
    def can_close(self) -> bool:
        return not self.busy


class TinySaAnalyzerViewModel:
    """Subscribe to one externally composed analyzer presenter without I/O ownership."""

    def __init__(self, binding: TinySaAnalyzerBinding) -> None:
        if not isinstance(binding, TinySaAnalyzerBinding):
            raise TypeError("tinySA analyzer view model requires a V2 binding")
        snapshot = binding.presenter.current_snapshot
        if not isinstance(snapshot, TinySaAnalyzerSnapshot):
            raise TypeError("tinySA analyzer presenter returned an invalid snapshot")
        self._binding = binding
        self._state = TinySaAnalyzerViewState(source=binding.source, snapshot=snapshot)
        self._listeners: list[Callable[[TinySaAnalyzerViewState], None]] = []
        self._disposed = False
        for signal, callback in self._connections(binding.presenter):
            signal.connect(callback)

    @property
    def state(self) -> TinySaAnalyzerViewState:
        return self._state

    def subscribe(self, listener: Callable[[TinySaAnalyzerViewState], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        listener(self._state)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def collect_trace(self, request: TinySaScanRawRequest, pixel_width: int) -> bool:
        if self._disposed or self._state.busy or not self._state.snapshot.can_collect:
            return False
        self._binding.presenter.collect_trace(request, pixel_width)
        return True

    def stage_settings(self, plan: TinySaSweepSettingsPlan) -> bool:
        if self._disposed or self._state.busy or not self._state.snapshot.can_review_settings:
            return False
        self._binding.presenter.stage_settings(plan)
        return True

    def confirm_settings(self, *, user_confirmed: bool) -> bool:
        if self._disposed or self._state.busy or not self._state.snapshot.can_confirm_settings:
            return False
        self._binding.presenter.confirm_settings(user_confirmed=user_confirmed)
        return True

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        for signal, callback in self._connections(self._binding.presenter):
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError, ValueError):
                pass
        self._listeners.clear()

    def shutdown_presenter(self) -> None:
        """Invoke the external presenter's explicit shutdown once composition closes."""

        self._binding.presenter.shutdown()

    def prepare_shutdown(self):
        """Return cleanup of the bound owner without any serial operation."""
        presenter = self._binding.presenter
        prepare = getattr(presenter, "prepare_shutdown", None)
        finish = getattr(presenter, "finish_shutdown", None)
        if not callable(prepare) or not callable(finish):
            raise TypeError("tinySA analyzer has no split shutdown contract")
        prepare()
        self.dispose()
        return finish

    def _connections(
        self,
        presenter: TinySaAnalyzerPresenterPort,
    ) -> tuple[tuple[_SignalPort, Callable[..., None]], ...]:
        return (
            (presenter.snapshot_changed, self._on_snapshot_changed),
            (presenter.trace_ready, self._on_trace_ready),
            (presenter.settings_review_ready, self._on_settings_review_ready),
            (presenter.busy_changed, self._on_busy_changed),
            (presenter.task_failed, self._on_task_failed),
        )

    def _on_snapshot_changed(self, snapshot: object) -> None:
        if isinstance(snapshot, TinySaAnalyzerSnapshot):
            self._set_state(replace(self._state, snapshot=snapshot, error=None))

    def _on_trace_ready(self, result: object) -> None:
        if not isinstance(result, TinySaTraceApplicationResult):
            self._set_state(replace(self._state, error="tinySA trace result is invalid"))
            return
        presentation = result.presentation
        try:
            frame = TinySaTraceViewFrame(presentation.frequencies_hz, presentation.values_dbm)
        except (TypeError, ValueError) as error:
            self._set_state(replace(self._state, error=_error_text(error)))
            return
        self._set_state(
            replace(
                self._state,
                trace_frame=frame,
                trace_result=result,
                settings_review=None,
                error=None,
            )
        )

    def _on_settings_review_ready(self, review: object) -> None:
        if isinstance(review, TinySaSettingsReview):
            self._set_state(replace(self._state, settings_review=review, error=None))

    def _on_busy_changed(self, busy: object) -> None:
        self._set_state(replace(self._state, busy=bool(busy)))

    def _on_task_failed(self, error: object) -> None:
        self._set_state(replace(self._state, error=_error_text(error)))

    def _set_state(self, state: TinySaAnalyzerViewState) -> None:
        self._state = state
        for listener in tuple(self._listeners):
            listener(state)


def _error_text(error: object) -> str:
    text = str(error).strip()
    return text or type(error).__name__


__all__ = [
    "DeferredTinySaSourceActivationViewModel",
    "TinySaAnalyzerBinding",
    "TinySaAnalyzerBindingFactory",
    "TinySaAnalyzerPresenterPort",
    "TinySaAnalyzerViewModel",
    "TinySaAnalyzerViewState",
    "TinySaSourceActivationPresenterFactory",
    "TinySaSourceActivationPresenterPort",
    "TinySaSourceActivationViewState",
    "TinySaTraceViewFrame",
]
