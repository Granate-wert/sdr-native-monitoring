"""Bounded replay presentation through the Qt-free application use cases."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from ...application import ReplayControlUseCases
from ...domain import ReplayKind, ReplayPosition, ReprocessResult


class ReplayPresenter(QObject):
    index_ready = Signal(object)
    position_changed = Signal(object)
    frame_ready = Signal(object)
    reprocess_progress = Signal(float)
    reprocess_ready = Signal(object)
    task_failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, use_cases: ReplayControlUseCases, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._use_cases = use_cases
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-replay")
        self._closed = False
        self._control_pending = False
        self._reprocess_pending = False
        self._cancel_pending = False
        self._control_generation = 0
        self._reprocess_generation = 0
        self._busy = False

    def open(self, path: Path, kind: ReplayKind = ReplayKind.ALL) -> None:
        self._submit_control(lambda: self._use_cases.open(path, kind), self.index_ready.emit)

    def seek(self, fraction: float) -> None:
        self._submit_control(lambda: self._use_cases.seek(fraction), self.position_changed.emit)

    def play(self) -> None:
        self._submit_control(self._use_cases.play, self.position_changed.emit)

    def pause(self) -> None:
        self._submit_control(self._use_cases.pause, self.position_changed.emit)

    def set_speed(self, speed: float) -> None:
        self._submit_control(lambda: self._use_cases.set_speed(speed), self.position_changed.emit)

    def read_next(self) -> None:
        self._submit_control(self._use_cases.read_next, self._frame_or_position)

    def reprocess(self, path: Path, backend: str) -> None:
        if self._closed or self._control_pending or self._reprocess_pending:
            return
        self._reprocess_pending = True
        self._reprocess_generation += 1
        generation = self._reprocess_generation
        self._update_busy()
        future = self._executor.submit(lambda: self._use_cases.start_reprocess(path, backend))
        future.add_done_callback(lambda completed: self._reprocess_started(completed, generation))

    def cancel_reprocess(self) -> None:
        if self._closed or not self._reprocess_pending or self._cancel_pending:
            return
        self._cancel_pending = True
        # Cancellation is an atomic signal to the native worker, not a second
        # queued job behind that worker.  Queueing it on this single control
        # executor used to make a long reprocess uncancellable from the UI.
        try:
            self._use_cases.cancel_reprocess()
        except Exception as error:
            self.task_failed.emit(str(error))
        finally:
            self._cancel_pending = False

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        shutdown = self._executor.submit(self._use_cases.shutdown)
        shutdown.result()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _frame_or_position(self, result: tuple[Any | None, ReplayPosition]) -> None:
        frame, position = result
        if frame is not None:
            self.frame_ready.emit(frame)
        self.position_changed.emit(position)

    def _submit_control(self, operation: Any, on_success: Any) -> None:
        if self._closed or self._control_pending:
            return
        self._control_pending = True
        self._control_generation += 1
        generation = self._control_generation
        self._update_busy()
        future = self._executor.submit(operation)
        future.add_done_callback(
            lambda completed: self._complete_control(completed, on_success, generation)
        )

    def _complete_control(self, future: Future[Any], on_success: Any, generation: int) -> None:
        try:
            value = future.result()
        except Exception as error:
            if not self._closed and generation == self._control_generation:
                self.task_failed.emit(str(error))
        else:
            if not self._closed and generation == self._control_generation:
                on_success(value)
        finally:
            self._control_pending = False
            self._update_busy()

    def _reprocess_started(self, future: Future[Future[ReprocessResult]], generation: int) -> None:
        try:
            result = future.result()
        except Exception as error:
            if not self._closed and generation == self._reprocess_generation:
                self.task_failed.emit(str(error))
            self._reprocess_pending = False
            self._update_busy()
        else:
            result.add_done_callback(
                lambda completed: self._complete_reprocess(completed, generation)
            )

    def _complete_reprocess(self, future: Future[ReprocessResult], generation: int) -> None:
        try:
            result = future.result()
        except Exception as error:
            if not self._closed and generation == self._reprocess_generation:
                self.task_failed.emit(str(error))
        else:
            if not self._closed and generation == self._reprocess_generation:
                self.reprocess_ready.emit(result)
        finally:
            if generation == self._reprocess_generation:
                self._reprocess_pending = False
                self._cancel_pending = False
                self._update_busy()

    def _update_busy(self) -> None:
        busy = self._control_pending or self._reprocess_pending
        if busy != self._busy:
            self._busy = busy
            self.busy_changed.emit(busy)


__all__ = ["ReplayPresenter"]
