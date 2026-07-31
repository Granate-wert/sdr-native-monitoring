"""Presenter lifecycle contracts independent from Qt widgets and device services."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from .state import AppUiState, UiUpdateBatch


class Presenter(Protocol):
    def activate(self) -> None: ...
    def deactivate(self) -> None: ...
    def close(self) -> None: ...
    def apply_state(self, state: AppUiState) -> None: ...


class PresenterCoordinator:
    """Owns presentation lifecycle without taking ownership of application services."""

    def __init__(self, presenters: Iterable[Presenter] = ()) -> None:
        self._presenters = tuple(presenters)
        self._active = False
        self._closed = False

    def activate(self) -> None:
        if self._closed:
            raise RuntimeError("presenter coordinator is closed")
        if self._active:
            return
        for presenter in self._presenters:
            presenter.activate()
        self._active = True

    def deactivate(self) -> None:
        if not self._active:
            return
        for presenter in reversed(self._presenters):
            presenter.deactivate()
        self._active = False

    def apply_state(self, state: AppUiState) -> None:
        if not self._active:
            return
        for presenter in self._presenters:
            presenter.apply_state(state)

    def apply_batch(self, batch: UiUpdateBatch) -> None:
        if batch.state is not None:
            self.apply_state(batch.state)

    def close(self) -> None:
        if self._closed:
            return
        self.deactivate()
        for presenter in reversed(self._presenters):
            presenter.close()
        self._closed = True
