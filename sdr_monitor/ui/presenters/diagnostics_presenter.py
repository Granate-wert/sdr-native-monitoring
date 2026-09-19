"""Bounded diagnostics presentation through Qt-free application use cases."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from ...application import DiagnosticsControlUseCases


class DiagnosticsPresenter(QObject):
    snapshot_changed = Signal(object)
    self_tests_changed = Signal(object)
    bundle_ready = Signal(object)
    task_failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, use_cases: DiagnosticsControlUseCases, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._use_cases = use_cases
        self._closed = False
        self._shutdown_complete = False
        self._task_pending = False
        self.refresh()

    def refresh(self) -> None:
        if not self._closed:
            self.snapshot_changed.emit(self._use_cases.snapshot())

    def run_self_tests(self) -> None:
        self._submit(self._use_cases.start_self_tests, self.self_tests_changed.emit)

    def run_rx_test(self, confirmed: bool) -> None:
        self._submit(lambda: self._use_cases.start_rx_test(confirmed), self.self_tests_changed.emit)

    def export_bundle(self, output_dir: Path) -> None:
        self._submit(lambda: self._use_cases.start_bundle_export(output_dir), self.bundle_ready.emit)

    def cancel(self) -> None:
        if not self._closed:
            self._use_cases.cancel()

    def report_error(self, summary: str, reason: str, recommendation: str, detail: str) -> None:
        if not self._closed:
            self.snapshot_changed.emit(self._use_cases.report_error(summary, reason, recommendation, detail))

    def prepare_shutdown(self) -> None:
        self._closed = True

    def shutdown(self) -> None:
        self.prepare_shutdown()
        self.finish_shutdown()

    def finish_shutdown(self) -> None:
        if self._shutdown_complete:
            return
        self._use_cases.shutdown()
        self._shutdown_complete = True

    def _submit(self, operation: Any, on_success: Any) -> None:
        if self._closed or self._task_pending:
            return
        self._task_pending = True
        self.busy_changed.emit(True)
        try:
            future = operation()
        except Exception as error:
            self.task_failed.emit(str(error))
            self.busy_changed.emit(False)
            return
        future.add_done_callback(lambda completed: self._complete(completed, on_success))

    def _complete(self, future: Future[Any], on_success: Any) -> None:
        try:
            value = future.result()
        except Exception as error:
            self.task_failed.emit(str(error))
        else:
            on_success(value)
            self.refresh()
        finally:
            self._task_pending = False
            self.busy_changed.emit(False)


__all__ = ["DiagnosticsPresenter"]
