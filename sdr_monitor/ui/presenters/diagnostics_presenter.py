"""Asynchronous diagnostics presenter."""

from __future__ import annotations

from concurrent.futures import Future
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from ...services.diagnostics_session import DiagnosticsService


class DiagnosticsPresenter(QObject):
    snapshot_changed = Signal(object)
    self_tests_changed = Signal(object)
    bundle_ready = Signal(object)
    task_failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, service: DiagnosticsService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._closed = False
        self.refresh()

    def refresh(self) -> None:
        if not self._closed:
            self.snapshot_changed.emit(self._service.collect_snapshot())

    def run_self_tests(self) -> None:
        self._submit(lambda cancel: self._service.run_self_tests(cancel), self.self_tests_changed.emit)

    def run_rx_test(self, confirmed: bool) -> None:
        self._submit(lambda cancel: [self._service.run_controlled_rx_test(confirmed, cancel)], self.self_tests_changed.emit)

    def export_bundle(self, output_dir: Path) -> None:
        self._submit(lambda _cancel: self._service.export_support_bundle(output_dir), self.bundle_ready.emit)

    def cancel(self) -> None:
        self._service.supervisor.cancel()

    def report_error(self, summary: str, reason: str, recommendation: str, detail: str) -> None:
        self._service.report_error(summary, reason, recommendation, detail)
        self.refresh()

    def shutdown(self) -> None:
        self._closed = True
        self._service.shutdown()

    def _submit(self, operation: Any, on_success: Any) -> None:
        if self._closed:
            return
        self.busy_changed.emit(True)
        try:
            future = self._service.supervisor.submit(operation)
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
            self.busy_changed.emit(False)


__all__ = ["DiagnosticsPresenter"]
