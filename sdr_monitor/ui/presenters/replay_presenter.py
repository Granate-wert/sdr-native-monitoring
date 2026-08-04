"""S09 replay presenter; all index/read/reprocess operations are worker-bound."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from ...domain import ReplayKind
from ...services.replay_session import ReplayService


class ReplayPresenter(QObject):
    index_ready = Signal(object)
    position_changed = Signal(object)
    frame_ready = Signal(object)
    reprocess_progress = Signal(float)
    reprocess_ready = Signal(object)
    task_failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, service: ReplayService | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.service = service or ReplayService()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sdr-replay")
        self._closed = False

    def open(self, path: Path, kind: ReplayKind = ReplayKind.ALL) -> None:
        self._submit(lambda: self.service.open(path, kind=kind), self.index_ready.emit)

    def seek(self, fraction: float) -> None:
        self._submit(lambda: self.service.seek(fraction), self.position_changed.emit)

    def play(self) -> None:
        self.service.play()
        self.position_changed.emit(self.service.position)

    def pause(self) -> None:
        self.service.pause()
        self.position_changed.emit(self.service.position)

    def set_speed(self, speed: float) -> None:
        self.service.set_speed(speed)

    def read_next(self) -> None:
        self._submit(self.service.read_next, self._frame_or_position)

    def reprocess(self, path: Path, backend: str) -> None:
        self._submit(lambda: self.service.reprocess_iq(path, backend).result(), self.reprocess_ready.emit)

    def cancel_reprocess(self) -> None:
        self.service.cancel_reprocess()

    def shutdown(self) -> None:
        self._closed = True
        self.service.close()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _frame_or_position(self, frame: Any) -> None:
        if frame is not None:
            self.frame_ready.emit(frame)
        self.position_changed.emit(self.service.position)

    def _submit(self, operation: Any, on_success: Any) -> None:
        if self._closed:
            return
        self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(lambda completed: self._complete(completed, on_success))

    def _complete(self, future: Future[Any], on_success: Any) -> None:
        try:
            value = future.result()
        except Exception as error:
            self.task_failed.emit(str(error))
        else:
            on_success(value)
        finally:
            self.busy_changed.emit(False)


__all__ = ["ReplayPresenter"]
