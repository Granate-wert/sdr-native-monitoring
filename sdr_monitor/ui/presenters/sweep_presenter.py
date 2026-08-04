"""Background orchestration for standalone wideband sweep work."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from ...domain import SweepConfiguration, SweepResult
from ...services.interfaces import SweepSdrService


class SweepPresenter(QObject):
    """All service calls leave the GUI thread; cancel gets its own worker lane."""

    busy_changed = Signal(bool)
    plan_ready = Signal(object)
    progress_changed = Signal(object)
    result_ready = Signal(object)
    export_ready = Signal(object)
    task_failed = Signal(str)

    def __init__(self, service: SweepSdrService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sdr-sweep")
        self._closed = False

    def plan(self, configuration: SweepConfiguration) -> None:
        self._submit(lambda: self._service.plan(configuration), self.plan_ready.emit)

    def execute(self, configuration: SweepConfiguration) -> None:
        self._submit(
            lambda: self._service.execute(configuration, self.progress_changed.emit),
            self.result_ready.emit,
        )

    def cancel(self) -> None:
        self._submit(self._service.cancel, lambda _unused: None, report_busy=False)

    def export_result(self, result: SweepResult, output_path: Path) -> None:
        self._submit(lambda: self._service.export_result(result, output_path), self.export_ready.emit)

    def shutdown(self) -> None:
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._service.close()

    def _submit(self, operation: Callable[[], Any], on_success: Callable[[Any], None], *, report_busy: bool = True) -> None:
        if self._closed:
            return
        if report_busy:
            self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(lambda completed: self._complete(completed, on_success, report_busy))

    def _complete(self, future: Future[Any], on_success: Callable[[Any], None], report_busy: bool) -> None:
        try:
            value = future.result()
        except Exception as error:  # pragma: no cover - hardware adapters own exceptional paths
            self.task_failed.emit(str(error))
        else:
            on_success(value)
        finally:
            if report_busy:
                self.busy_changed.emit(False)
