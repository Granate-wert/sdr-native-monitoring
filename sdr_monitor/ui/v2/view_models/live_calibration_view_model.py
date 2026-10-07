"""Explicit operator commands and separate correction/display evidence."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import SignalInstance

from sdr_monitor.domain.calibration import CalibrationProfile
from sdr_monitor.services.captured_calibration import CalibrationCommandPreview
from sdr_monitor.services.current_calibration_binding import CurrentCalibrationBinding
from sdr_monitor.services.live_calibration_signature import CalibrationFrontendContext

from ..state.prepared_calibration import PreparedCalibratedCurrent

@dataclass(frozen=True, slots=True)
class LiveCalibrationState:
    available: bool = False
    phase: str = "unavailable"
    busy: bool = False
    binding: CurrentCalibrationBinding | None = None
    preview: CalibrationCommandPreview | None = None
    acknowledged: str | None = None
    current: PreparedCalibratedCurrent | None = None
    displayed: PreparedCalibratedCurrent | None = None
    error: str | None = None


class LiveCalibrationPresenterPort(Protocol):
    """Typed UI port; product assembly does not import a backend owner."""

    changed: SignalInstance
    state: LiveCalibrationState

    def bind(self, frontend: CalibrationFrontendContext) -> None: ...
    def invalidate(self) -> None: ...
    def preview(self, profile: CalibrationProfile | None) -> None: ...
    def select(self) -> None: ...
    def clear(self) -> None: ...
    def displayed(self, current: PreparedCalibratedCurrent) -> None: ...
    def display_invalidated(self) -> None: ...
    def is_valid(self, current: PreparedCalibratedCurrent) -> bool: ...
    def dispose(self) -> None: ...


class LiveCalibrationViewModel:
    def __init__(self, presenter: LiveCalibrationPresenterPort) -> None:
        self.presenter = presenter
        self._listeners: list[Callable[[LiveCalibrationState], None]] = []
        presenter.changed.connect(self._changed)

    @property
    def state(self) -> LiveCalibrationState:
        return self.presenter.state

    def subscribe(self, callback: Callable[[LiveCalibrationState], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        callback(self.state)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)
        return unsubscribe

    def bind(self, frontend: CalibrationFrontendContext) -> None:
        self.presenter.bind(frontend)

    def frontend_edited(self) -> None:
        self.presenter.invalidate()

    def preview(self, profile: CalibrationProfile | None) -> None:
        self.presenter.preview(profile)

    def select(self) -> None:
        self.presenter.select()

    def clear(self) -> None:
        self.presenter.clear()

    def close_binding(self) -> None:
        self.presenter.invalidate()

    def displayed(self, current: PreparedCalibratedCurrent) -> None:
        self.presenter.displayed(current)

    def display_invalidated(self) -> None:
        self.presenter.display_invalidated()

    def is_valid(self, current: PreparedCalibratedCurrent) -> bool:
        return self.presenter.is_valid(current)

    def dispose(self) -> None:
        self.presenter.changed.disconnect(self._changed)
        self._listeners.clear()
        self.presenter.dispose()

    def _changed(self, state: LiveCalibrationState) -> None:
        for callback in tuple(self._listeners):
            callback(state)
