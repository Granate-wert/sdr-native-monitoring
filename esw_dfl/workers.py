from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    progress = Signal(float, str)
    result = Signal(object)
    error = Signal(str, str)
    cancelled = Signal()
    finished = Signal()


class TaskWorker(QRunnable):
    """Cancellable QThreadPool task; worker functions never touch widgets."""

    def __init__(
        self,
        function: Callable[..., Any],
        *args: Any,
        pass_progress: bool = False,
        pass_cancel: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.pass_progress = pass_progress
        self.pass_cancel = pass_cancel
        self.cancel_event = threading.Event()
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self.cancel_event.set()

    @staticmethod
    def _emit(signal: Any, *args: Any) -> None:
        try:
            signal.emit(*args)
        except RuntimeError:
            # Qt receivers/signals may already be deleted during application
            # shutdown. The background function must still terminate cleanly.
            return

    @Slot()
    def run(self) -> None:
        try:
            if self.cancel_event.is_set():
                self._emit(self.signals.cancelled)
                return
            kwargs = dict(self.kwargs)
            if self.pass_progress:
                kwargs["progress"] = lambda *args: self._emit(self.signals.progress, *args)
            if self.pass_cancel:
                kwargs["cancel"] = self.cancel_event
            value = self.function(*self.args, **kwargs)
            if self.cancel_event.is_set():
                self._emit(self.signals.cancelled)
            else:
                self._emit(self.signals.result, value)
        except Exception as exc:
            if self.cancel_event.is_set():
                self._emit(self.signals.cancelled)
            else:
                logging.getLogger(__name__).exception("Фоновая операция завершилась ошибкой")
                self._emit(self.signals.error, str(exc), traceback.format_exc())
        finally:
            self._emit(self.signals.finished)
