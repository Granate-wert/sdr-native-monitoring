"""Asynchronous Qt adapter for the bounded Live profile use case."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from ...application import LiveProfileUseCases


class LiveProfilePresenter(QObject):
    """Moves profile-store reads and profile resolution off the GUI thread."""

    profiles_loaded = Signal(object)
    profile_selected = Signal(object)
    task_failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, use_cases: LiveProfileUseCases, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._use_cases = use_cases
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-live-profile")
        self._closed = False

    def load_profiles(self) -> None:
        self._submit(self._use_cases.list_profiles, self.profiles_loaded.emit)

    def select_profile(self, profile_id: str) -> None:
        self._submit(lambda: self._use_cases.select_profile(profile_id), self.profile_selected.emit)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _submit(self, operation: Callable[[], Any], on_success: Callable[[Any], None]) -> None:
        if self._closed:
            return
        self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(lambda completed: self._complete(completed, on_success))

    def _complete(self, future: Future[Any], on_success: Callable[[Any], None]) -> None:
        try:
            value = future.result()
        except Exception as error:
            self.task_failed.emit(str(error))
        else:
            on_success(value)
        finally:
            self.busy_changed.emit(False)


__all__ = ["LiveProfilePresenter"]
