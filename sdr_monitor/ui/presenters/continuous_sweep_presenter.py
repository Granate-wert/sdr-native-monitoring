"""Qt-side coalescer for native continuous-sweep snapshots.

It runs no SDR processing: the timer merely polls a finite native publication
queue. Widgets report their own completed render duration back to this class,
keeping completed-line LPS and actual render FPS distinct.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt

from ...services.native_continuous_sweep import (
    ContinuousSweepDisplayPort,
    ContinuousSweepDisplayMetrics,
    ContinuousSweepDisplaySnapshot,
)
from ..performance import BoundedRenderMetrics, RenderPerformanceSnapshot


class ContinuousSweepPresenter(QObject):
    """Bounded UI polling and telemetry for a running R10-D coordinator."""

    line_ready = Signal(object)
    analyzer_ready = Signal(object)
    metrics_ready = Signal(object)
    task_failed = Signal(str)
    running_changed = Signal(bool)
    stopping_changed = Signal(bool)
    _stop_completed = Signal(object)

    def __init__(
        self,
        service: ContinuousSweepDisplayPort,
        *,
        max_poll_hz: float = 60.0,
        render_budget_ms: float = 16.67,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if max_poll_hz <= 0.0 or render_budget_ms <= 0.0:
            raise ValueError("continuous sweep UI cadence and render budget must be positive")
        self._service = service
        self._render_budget_ms = float(render_budget_ms)
        self._render_metrics = BoundedRenderMetrics(capacity=512)
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, round(1000.0 / max_poll_hz)))
        self._timer.timeout.connect(self._poll)
        self._closed = False
        self._stop_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-sweep-stop")
        self._stop_future: Future[ContinuousSweepDisplaySnapshot] | None = None
        self._stop_failed = False
        self._stop_completed.connect(self._finish_stop, Qt.ConnectionType.QueuedConnection)

    @property
    def is_stopping(self) -> bool:
        """True until Qt has processed completion, not merely worker exit."""
        return self._stop_future is not None

    def can_close(self) -> bool:
        return (not self._timer.isActive() and not self.is_stopping
                and getattr(self._service, "stop_required", False) is not True)

    def start(self, native_config: Any) -> None:
        if self._closed or self._timer.isActive() or self._stop_future is not None or self._stop_failed:
            return
        try:
            self._service.start(native_config)
        except Exception as error:
            self.task_failed.emit(str(error))
            return
        self._timer.start()
        self.running_changed.emit(True)

    def stop(self) -> None:
        """Request stop without blocking Qt; Start stays barred until completion."""
        if (not self._timer.isActive() and getattr(self._service, "stop_required", False) is not True) or self._stop_future is not None:
            return
        self._timer.stop()
        future = self._stop_executor.submit(self._stop_and_snapshot)
        self._stop_future = future
        self.stopping_changed.emit(True)
        future.add_done_callback(self._stop_completed.emit)

    def _stop_and_snapshot(self) -> ContinuousSweepDisplaySnapshot:
        self._service.stop()
        return self._service.poll_latest()

    @Slot(object)
    def _finish_stop(self, future: Future[ContinuousSweepDisplaySnapshot]) -> None:
        if future is not self._stop_future:
            return
        try:
            self._emit_snapshot(future.result())
        except Exception as error:
            self._stop_failed = True
            self.task_failed.emit(str(error))
        self.running_changed.emit(False)
        self._stop_future = None
        self.stopping_changed.emit(False)

    def record_completed_render(self, duration_ms: float, *, now_s: float | None = None) -> None:
        """Called only after a widget has actually rendered a supplied line."""

        self._render_metrics.record(
            duration_ms,
            time.monotonic() if now_s is None else now_s,
            budget_ms=self._render_budget_ms,
        )

    def render_metrics(self, *, now_s: float | None = None) -> RenderPerformanceSnapshot:
        return self._render_metrics.snapshot(time.monotonic() if now_s is None else now_s)

    def shutdown(self) -> None:
        if self._closed:
            return
        self.stop()
        if self._stop_future is not None:
            # Legacy close is still synchronous; it must not close the service
            # concurrently with the already requested stop worker.
            future = self._stop_future
            try:
                future.result()
            except Exception:
                pass  # Reported exactly once by _finish_stop.
            self._finish_stop(future)
        self._closed = True
        try:
            self._service.close()
        finally:
            self._stop_executor.shutdown(wait=True)

    def _poll(self) -> None:
        if self._closed or self.is_stopping:
            return
        try:
            snapshot: ContinuousSweepDisplaySnapshot = self._service.poll_latest()
        except Exception as error:
            # A publication error does not mean acquisition has stopped.
            # Latch admission before any externally visible callback, then
            # use the same nonblocking stop path to release the receiver.
            self._stop_failed = True
            self.stop()
            self.task_failed.emit(str(error))
            return
        self._emit_snapshot(snapshot)

    def _emit_snapshot(self, snapshot: ContinuousSweepDisplaySnapshot) -> None:
        metrics: ContinuousSweepDisplayMetrics = snapshot.metrics
        self.metrics_ready.emit(metrics)
        if snapshot.line is not None:
            self.line_ready.emit(snapshot.line)
        bundle = snapshot.analyzer_bundle
        if bundle is not None:
            self.analyzer_ready.emit(bundle)


__all__ = ["ContinuousSweepPresenter"]
