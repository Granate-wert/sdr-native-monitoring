"""Single-worker presenter for explicit tinySA trace and settings actions."""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from ...application.tinysa_analyzer import (
    TinySaAnalyzerSnapshot,
    TinySaAnalyzerUseCases,
    TinySaSettingsReview,
    TinySaTraceApplicationResult,
)
from ...services.tinysa_serial_trace_collector import TinySaScanRawRequest
from ...services.tinysa_sweep_settings_controller import TinySaSweepSettingsPlan


@dataclass(frozen=True, slots=True)
class TinySaAnalyzerPresenterMetrics:
    operations_started: int
    operations_rejected_while_busy: int
    operation_failures: int
    completion_dispatches: int
    last_worker_to_gui_latency_ns: int
    maximum_worker_to_gui_latency_ns: int
    trace_operations_started: int
    settings_review_operations_started: int
    settings_confirmation_operations_started: int


class TinySaAnalyzerPresenter(QObject):
    """Keep serial-capable use cases off the GUI thread with one finite worker."""

    snapshot_changed = Signal(object)
    trace_ready = Signal(object)
    settings_review_ready = Signal(object)
    busy_changed = Signal(bool)
    task_failed = Signal(str)
    # Python's perf_counter_ns value exceeds a 32-bit Qt int, so retain it as
    # a Python object across the queued worker-to-GUI signal boundary.
    _operation_finished = Signal(str, object, object)

    def __init__(self, use_cases: TinySaAnalyzerUseCases, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if not callable(getattr(use_cases, "current", None)):
            raise TypeError("tinySA presenter requires application use cases")
        self._use_cases = use_cases
        self._snapshot = use_cases.current()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tinysa-analyzer")
        self._pending = False
        self._closed = False
        self._operations_started = 0
        self._operations_rejected = 0
        self._operation_failures = 0
        self._completion_dispatches = 0
        self._last_worker_to_gui_latency_ns = 0
        self._maximum_worker_to_gui_latency_ns = 0
        self._trace_operations_started = 0
        self._settings_review_operations_started = 0
        self._settings_confirmation_operations_started = 0
        self._operation_finished.connect(self._complete_operation)

    @property
    def current_snapshot(self) -> TinySaAnalyzerSnapshot:
        return self._snapshot

    @property
    def metrics(self) -> TinySaAnalyzerPresenterMetrics:
        return TinySaAnalyzerPresenterMetrics(
            operations_started=self._operations_started,
            operations_rejected_while_busy=self._operations_rejected,
            operation_failures=self._operation_failures,
            completion_dispatches=self._completion_dispatches,
            last_worker_to_gui_latency_ns=self._last_worker_to_gui_latency_ns,
            maximum_worker_to_gui_latency_ns=self._maximum_worker_to_gui_latency_ns,
            trace_operations_started=self._trace_operations_started,
            settings_review_operations_started=self._settings_review_operations_started,
            settings_confirmation_operations_started=self._settings_confirmation_operations_started,
        )

    def collect_trace(self, request: TinySaScanRawRequest, pixel_width: int) -> None:
        self._submit("trace", lambda: self._use_cases.collect_trace(request, pixel_width))

    def stage_settings(self, plan: TinySaSweepSettingsPlan) -> None:
        self._submit("settings_review", lambda: self._use_cases.stage_settings(plan))

    def confirm_settings(self, *, user_confirmed: bool) -> None:
        self._submit(
            "settings_confirmation",
            lambda: self._use_cases.confirm_settings(user_confirmed=user_confirmed),
        )

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _submit(self, kind: str, operation: object) -> None:
        if self._closed:
            return
        if self._pending:
            self._operations_rejected += 1
            return
        if not callable(operation):
            raise TypeError("tinySA presenter operation must be callable")
        self._pending = True
        self._operations_started += 1
        if kind == "trace":
            self._trace_operations_started += 1
        elif kind == "settings_review":
            self._settings_review_operations_started += 1
        elif kind == "settings_confirmation":
            self._settings_confirmation_operations_started += 1
        self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(
            lambda completed: self._operation_finished.emit(
                kind,
                completed,
                time.perf_counter_ns(),
            )
        )

    def _complete_operation(
        self,
        kind: str,
        future: Future[object],
        worker_completed_ns: int,
    ) -> None:
        dispatch_latency_ns = max(0, time.perf_counter_ns() - worker_completed_ns)
        self._completion_dispatches += 1
        self._last_worker_to_gui_latency_ns = dispatch_latency_ns
        self._maximum_worker_to_gui_latency_ns = max(
            self._maximum_worker_to_gui_latency_ns,
            dispatch_latency_ns,
        )
        try:
            result = future.result()
            snapshot = self._snapshot_from_result(kind, result)
        except Exception:  # noqa: BLE001 - the worker boundary must convert every operation failure to UI state.
            self._operation_failures += 1
            try:
                snapshot = self._use_cases.current()
            except Exception:  # noqa: BLE001 - the fallback snapshot is best-effort after a worker failure.
                snapshot = self._snapshot
            self.task_failed.emit("tinySA operation failed")
        else:
            if kind == "trace":
                self.trace_ready.emit(result)
            elif kind == "settings_review" and result is not None:
                self.settings_review_ready.emit(result)
        finally:
            self._snapshot = snapshot
            self.snapshot_changed.emit(snapshot)
            self._pending = False
            self.busy_changed.emit(False)

    def _snapshot_from_result(self, kind: str, result: object) -> TinySaAnalyzerSnapshot:
        if kind == "trace":
            if not isinstance(result, TinySaTraceApplicationResult):
                raise TypeError("tinySA trace operation returned an invalid result")
            return result.snapshot
        if kind == "settings_review":
            if result is not None and not isinstance(result, TinySaSettingsReview):
                raise TypeError("tinySA settings review returned an invalid result")
            return self._use_cases.current()
        if not isinstance(result, TinySaAnalyzerSnapshot):
            raise TypeError("tinySA settings confirmation returned an invalid snapshot")
        return result


__all__ = ["TinySaAnalyzerPresenter", "TinySaAnalyzerPresenterMetrics"]
