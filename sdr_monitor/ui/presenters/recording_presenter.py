"""Asynchronous recording-control presentation without raw service access."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from ...application import RecordingControlUseCases
from ...domain import RecordingOptions


class RecordingPresenter(QObject):
    health_changed = Signal(object)
    result_ready = Signal(object)
    busy_changed = Signal(bool)
    task_failed = Signal(str)

    def __init__(self, use_cases: RecordingControlUseCases, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._use_cases = use_cases
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-recording")
        self._closed = False
        self._control_pending = False
        self._health_pending = False

    def start(self, options: RecordingOptions) -> None:
        self._submit_control(lambda: self._use_cases.start(options), self.health_changed.emit)

    def start_now(self, options: RecordingOptions) -> None:
        self._submit_control(lambda: self._use_cases.start_now(options), self.health_changed.emit)

    def stop(self) -> None:
        self._submit_control(self._use_cases.stop, self.result_ready.emit)

    def refresh_health(self) -> None:
        self._submit_health(self._use_cases.health, self.health_changed.emit)

    def recover_partial(self, uri: Any) -> None:
        self._submit_control(lambda: self._use_cases.recover_partial(uri), self.result_ready.emit)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        shutdown = self._executor.submit(self._use_cases.shutdown)
        shutdown.result()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _submit_control(self, operation: Callable[[], Any], on_success: Callable[[Any], None]) -> None:
        """Admit one user operation, even when one health query is running."""

        if self._closed or self._control_pending:
            return
        self._control_pending = True
        self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(lambda completed: self._complete_control(completed, on_success))

    def _submit_health(self, operation: Callable[[], Any], on_success: Callable[[Any], None]) -> None:
        """Keep health latest-wins: never queue it behind health or control work."""

        if self._closed or self._health_pending or self._control_pending:
            return
        self._health_pending = True
        future = self._executor.submit(operation)
        future.add_done_callback(lambda completed: self._complete_health(completed, on_success))

    def _complete_control(self, future: Future[Any], on_success: Callable[[Any], None]) -> None:
        try:
            value = future.result()
        except Exception as error:
            self.task_failed.emit(str(error))
        else:
            on_success(value)
        finally:
            self._control_pending = False
            self.busy_changed.emit(False)

    def _complete_health(self, future: Future[Any], on_success: Callable[[Any], None]) -> None:
        try:
            value = future.result()
        except Exception as error:
            self.task_failed.emit(str(error))
        else:
            on_success(value)
        finally:
            self._health_pending = False


__all__ = ["RecordingPresenter"]
