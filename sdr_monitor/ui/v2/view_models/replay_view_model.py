"""Deferred spectrum-only replay adapter for UI V2.

The existing ReplayPresenter keeps file and reprocess work behind its public
worker boundary.  This view model does not construct it until the user asks to
open a recording, and it admits only immutable SpectrumFrame outputs to Qt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from sdr_monitor.domain import RecordingIndex, ReplayKind, ReplayPosition, ReprocessResult, SpectrumFrame


class _SignalPort(Protocol):
    def connect(self, callback: Callable[..., None]) -> object: ...

    def disconnect(self, callback: Callable[..., None]) -> object: ...


class ReplayPresenterPort(Protocol):
    """The frozen public replay presenter boundary admitted by UI2-10C."""

    index_ready: _SignalPort
    position_changed: _SignalPort
    frame_ready: _SignalPort
    reprocess_ready: _SignalPort
    task_failed: _SignalPort
    busy_changed: _SignalPort

    def open(self, path: Path, kind: ReplayKind = ReplayKind.ALL) -> None: ...

    def seek(self, fraction: float) -> None: ...

    def read_next(self) -> None: ...

    def reprocess(self, path: Path, backend: str) -> None: ...

    def cancel_reprocess(self) -> None: ...

    def shutdown(self) -> None: ...


ReplayPresenterFactory = Callable[[], ReplayPresenterPort]


@dataclass(frozen=True, slots=True)
class ReplayIndexView:
    """Route-redacted immutable index metadata for one selected recording."""

    filename: str
    spectrum_frames: int
    duration_ns: int
    source_size: int


@dataclass(frozen=True, slots=True)
class ReplayReprocessView:
    """Route-redacted result of an explicitly requested offline I/Q reprocess."""

    backend_requested: str
    backend_used: str
    status: str
    frames_processed: int
    output_filename: str | None
    warning: str


@dataclass(frozen=True, slots=True)
class ReplayViewState:
    """V2 replay state contains spectra and scalar metadata, never IQ blocks."""

    loaded: bool = False
    index: ReplayIndexView | None = None
    position: ReplayPosition = ReplayPosition(0, 0.0, 0)
    spectrum_frame: SpectrumFrame | None = None
    reprocess: ReplayReprocessView | None = None
    busy: bool = False
    error: str | None = None

    @property
    def can_close(self) -> bool:
        return not self.busy


class DeferredReplayViewModel:
    """Create the public ReplayPresenter only for an explicit visible command."""

    def __init__(self, presenter_factory: ReplayPresenterFactory) -> None:
        if not callable(presenter_factory):
            raise TypeError("UI2 replay requires an explicit presenter factory")
        self._presenter_factory = presenter_factory
        self._presenter: ReplayPresenterPort | None = None
        self._opened_path: Path | None = None
        self._state = ReplayViewState()
        self._listeners: list[Callable[[ReplayViewState], None]] = []
        self._disposed = False

    @property
    def state(self) -> ReplayViewState:
        return self._state

    def subscribe(self, listener: Callable[[ReplayViewState], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        listener(self._state)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def open_spectrum_recording(self, location: str) -> bool:
        """Open only spectrum entries after the user explicitly supplies a file."""

        text = location.strip()
        if not text:
            return False
        path = Path(text)
        presenter = self._ready_presenter()
        if presenter is None:
            return False
        self._opened_path = path
        presenter.open(path, ReplayKind.SPECTRUM)
        return True

    def seek(self, fraction: float) -> bool:
        presenter = self._operable_presenter()
        if presenter is None or self._state.index is None or not 0.0 <= fraction <= 1.0:
            return False
        presenter.seek(float(fraction))
        return True

    def read_next_spectrum(self) -> bool:
        presenter = self._operable_presenter()
        if presenter is None or self._state.index is None:
            return False
        presenter.read_next()
        return True

    def reprocess_iq(self, backend: str) -> bool:
        """Request legacy offline I/Q reprocessing; no I/Q payload is published to Qt."""

        presenter = self._operable_presenter()
        path = self._opened_path
        selected_backend = backend.strip().lower()
        if presenter is None or path is None or selected_backend not in {"cpu", "cuda"}:
            return False
        presenter.reprocess(path, selected_backend)
        return True

    def cancel_reprocess(self) -> bool:
        presenter = self._presenter
        if presenter is None or not self._state.busy or self._disposed:
            return False
        presenter.cancel_reprocess()
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

    def _ready_presenter(self) -> ReplayPresenterPort | None:
        if self._disposed or self._state.busy:
            return None
        if self._presenter is not None:
            return self._presenter
        try:
            presenter = self._presenter_factory()
        except Exception:  # noqa: BLE001 - factory detail may include a local route.
            self._set_state(replace(self._state, error="Replay presenter is unavailable"))
            return None
        self._presenter = presenter
        for signal, callback in self._connections(presenter):
            signal.connect(callback)
        self._set_state(replace(self._state, loaded=True, error=None))
        return presenter

    def _operable_presenter(self) -> ReplayPresenterPort | None:
        if self._disposed or self._state.busy:
            return None
        return self._presenter

    def _connections(
        self,
        presenter: ReplayPresenterPort,
    ) -> tuple[tuple[_SignalPort, Callable[..., None]], ...]:
        return (
            (presenter.index_ready, self._on_index_ready),
            (presenter.position_changed, self._on_position_changed),
            (presenter.frame_ready, self._on_frame_ready),
            (presenter.reprocess_ready, self._on_reprocess_ready),
            (presenter.task_failed, self._on_task_failed),
            (presenter.busy_changed, self._on_busy_changed),
        )

    def _on_index_ready(self, index: object) -> None:
        if not isinstance(index, RecordingIndex):
            self._set_state(replace(self._state, error="Replay index is invalid"))
            return
        spectrum_frames = len(index.entries_for(ReplayKind.SPECTRUM))
        if spectrum_frames == 0:
            self._set_state(replace(self._state, index=None, spectrum_frame=None, error="Recording has no spectrum frames"))
            return
        index_view = ReplayIndexView(
            filename=Path(index.path).name,
            spectrum_frames=spectrum_frames,
            duration_ns=index.duration_ns,
            source_size=index.source_size,
        )
        self._set_state(
            replace(
                self._state,
                index=index_view,
                position=ReplayPosition(0, 0.0, 0),
                spectrum_frame=None,
                reprocess=None,
                error=None,
            )
        )

    def _on_position_changed(self, position: object) -> None:
        if isinstance(position, ReplayPosition):
            self._set_state(replace(self._state, position=position))

    def _on_frame_ready(self, frame: object) -> None:
        if not isinstance(frame, SpectrumFrame):
            self._set_state(replace(self._state, error="UI V2 accepts replay spectra only"))
            return
        self._set_state(replace(self._state, spectrum_frame=frame, error=None))

    def _on_reprocess_ready(self, result: object) -> None:
        if not isinstance(result, ReprocessResult):
            self._set_state(replace(self._state, error="Replay reprocess result is invalid"))
            return
        self._set_state(
            replace(
                self._state,
                reprocess=ReplayReprocessView(
                    backend_requested=result.backend_requested,
                    backend_used=result.backend_used,
                    status=result.status,
                    frames_processed=result.frames_processed,
                    output_filename=None if result.output_path is None else Path(result.output_path).name,
                    warning=result.warning,
                ),
                error=None,
            )
        )

    def _on_task_failed(self, _error: object) -> None:
        # Do not echo a backend exception because it can contain an absolute user path.
        self._set_state(replace(self._state, error="Replay operation failed"))

    def _on_busy_changed(self, busy: object) -> None:
        self._set_state(replace(self._state, busy=bool(busy)))

    def _set_state(self, state: ReplayViewState) -> None:
        self._state = state
        for listener in tuple(self._listeners):
            listener(state)


__all__ = [
    "DeferredReplayViewModel",
    "ReplayIndexView",
    "ReplayPresenterFactory",
    "ReplayPresenterPort",
    "ReplayReprocessView",
    "ReplayViewState",
]
