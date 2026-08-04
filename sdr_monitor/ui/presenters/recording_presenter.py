"""Asynchronous S08 recording orchestration."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from ...domain import RecordingOptions
from ...services.interfaces import RecordingSdrService


class RecordingPresenter(QObject):
    health_changed = Signal(object)
    result_ready = Signal(object)
    busy_changed = Signal(bool)
    task_failed = Signal(str)

    def __init__(self, service: RecordingSdrService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sdr-recording")
        self._closed = False

    def start(self, options: RecordingOptions) -> None:
        self._submit(lambda: self._service.start(options), lambda _value: self.health_changed.emit(self._service.health()))

    def stop(self) -> None:
        self._submit(self._service.stop, self.result_ready.emit)

    def refresh_health(self) -> None:
        self.health_changed.emit(self._service.health())

    def recover_partial(self, uri: Any) -> None:
        self._submit(lambda: self._service.recover_partial(uri), self.result_ready.emit)

    def shutdown(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        close = getattr(self._service, "close", None)
        if callable(close):
            close()

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


__all__ = ["RecordingPresenter"]
