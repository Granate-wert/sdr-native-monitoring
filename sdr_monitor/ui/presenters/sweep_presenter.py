"""Background orchestration for standalone wideband sweep work."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from ...domain import SweepConfiguration, SweepResult
from ...application import SweepControlUseCases


class SweepPresenter(QObject):
    """All service calls leave the GUI thread; cancel gets its own worker lane."""

    busy_changed = Signal(bool)
    plan_ready = Signal(object)
    progress_changed = Signal(object)
    result_ready = Signal(object)
    export_ready = Signal(object)
    task_failed = Signal(str)

    def __init__(self, use_cases: SweepControlUseCases, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._use_cases = use_cases
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sdr-sweep")
        self._closed = False
        self._closing = False
        self._control_pending = False
        self._run_pending = False
        self._cancel_pending = False
        self._shutdown_service_complete = False
        self._shutdown_executor_complete = False

    def plan(self, configuration: SweepConfiguration) -> None:
        self._submit_control(lambda: self._use_cases.plan(configuration), self.plan_ready.emit)

    def execute(self, configuration: SweepConfiguration) -> None:
        if self._closed or self._closing or self._control_pending or self._run_pending:
            return
        self._run_pending = True
        self.busy_changed.emit(True)
        future = self._executor.submit(lambda: self._use_cases.execute(configuration, self.progress_changed.emit))
        future.add_done_callback(lambda completed: self._complete_run(completed, self.result_ready.emit))

    def cancel(self) -> None:
        if self._closed or self._closing or not self._run_pending or self._cancel_pending:
            return
        self._cancel_pending = True
        future = self._executor.submit(self._use_cases.cancel)
        future.add_done_callback(self._complete_cancel)

    def export_result(self, result: SweepResult, output_path: Path) -> None:
        self._submit_control(lambda: self._use_cases.export_result(result, output_path), self.export_ready.emit)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closing = True
        # ``SweepControlApplicationService.shutdown`` owns cancel + close as
        # one retryable cleanup operation.  Do not mark the presenter closed
        # before it succeeds: the product composition must be able to retry a
        # failed receiver release instead of observing a false no-op success.
        if not self._shutdown_service_complete:
            self._executor.submit(self._use_cases.shutdown).result()
            self._shutdown_service_complete = True
        if not self._shutdown_executor_complete:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._shutdown_executor_complete = True
        self._closed = True

    def _submit_control(self, operation: Callable[[], Any], on_success: Callable[[Any], None]) -> None:
        if self._closed or self._closing or self._control_pending or self._run_pending:
            return
        self._control_pending = True
        self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(lambda completed: self._complete_control(completed, on_success))

    def _complete_control(self, future: Future[Any], on_success: Callable[[Any], None]) -> None:
        try:
            value = future.result()
        except Exception as error:  # pragma: no cover - hardware adapters own exceptional paths
            self.task_failed.emit(str(error))
        else:
            on_success(value)
        finally:
            self._control_pending = False
            self.busy_changed.emit(False)

    def _complete_run(self, future: Future[Any], on_success: Callable[[Any], None]) -> None:
        try:
            on_success(future.result())
        except Exception as error:
            self.task_failed.emit(str(error))
        finally:
            self._run_pending = False
            self._cancel_pending = False
            self.busy_changed.emit(False)

    def _complete_cancel(self, future: Future[Any]) -> None:
        try:
            future.result()
        except Exception as error:
            self.task_failed.emit(str(error))
        finally:
            self._cancel_pending = False
