"""Qt-side coalescer for native continuous-sweep snapshots.

It runs no SDR processing: the timer requests one worker-side drain of a finite
native publication queue. Widgets report completed render duration to this class,
keeping completed-line LPS and actual render FPS distinct.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal, Slot, Qt

from ...services.native_continuous_sweep import (
    ContinuousSweepDisplayPort,
    ContinuousSweepDisplayMetrics,
    ContinuousSweepDisplaySnapshot,
)
from ..performance import BoundedRenderMetrics, RenderPerformanceSnapshot
from ...domain.analyzer import AnalyzerFrameBundle


@dataclass(frozen=True, slots=True)
class _PreparedPublication:
    snapshot: ContinuousSweepDisplaySnapshot
    bundle: AnalyzerFrameBundle | None
    presentation: object


class ContinuousSweepPresenter(QObject):
    """Bounded UI polling and telemetry for a running R10-D coordinator."""

    line_ready = Signal(object)
    analyzer_ready = Signal(object)
    snapshot_ready = Signal(object)
    prepared_snapshot_ready = Signal(object)
    metrics_ready = Signal(object)
    task_failed = Signal(str)
    running_changed = Signal(bool)
    starting_changed = Signal(bool)
    stopping_changed = Signal(bool)
    _start_completed = Signal(object)
    _stop_completed = Signal(object)
    _poll_completed = Signal(object)

    def __init__(
        self,
        service: ContinuousSweepDisplayPort,
        *,
        max_poll_hz: float = 60.0,
        render_budget_ms: float = 16.67,
        snapshot_preparer: Callable[[ContinuousSweepDisplaySnapshot, AnalyzerFrameBundle | None], object] | None = None,
        snapshot_admitter: Callable[[ContinuousSweepDisplaySnapshot], ContinuousSweepDisplaySnapshot] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if max_poll_hz <= 0.0 or render_budget_ms <= 0.0:
            raise ValueError("continuous sweep UI cadence and render budget must be positive")
        self._service = service
        self._snapshot_preparer = snapshot_preparer
        self._snapshot_admitter = snapshot_admitter
        self._render_budget_ms = float(render_budget_ms)
        self._render_metrics = BoundedRenderMetrics(capacity=512)
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, round(1000.0 / max_poll_hz)))
        self._timer.timeout.connect(self._poll)
        self._closed = False
        self._closing = False
        self._stop_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdr-sweep-stop")
        self._start_future: Future[None] | None = None
        self._stop_future: Future[ContinuousSweepDisplaySnapshot | _PreparedPublication] | None = None
        self._poll_future: Future[ContinuousSweepDisplaySnapshot | _PreparedPublication] | None = None
        self._stop_failed = False
        self._start_completed.connect(self._finish_start, Qt.ConnectionType.QueuedConnection)
        self._stop_completed.connect(self._finish_stop, Qt.ConnectionType.QueuedConnection)
        self._poll_completed.connect(self._finish_poll, Qt.ConnectionType.QueuedConnection)

    @property
    def prepares_snapshots(self) -> bool:
        """Composition-selected presentation channel; immutable for this owner."""
        return self._snapshot_preparer is not None

    @property
    def is_starting(self) -> bool:
        """True until Qt has processed Start completion."""
        return self._start_future is not None

    @property
    def is_stopping(self) -> bool:
        """True until Qt has processed completion, not merely worker exit."""
        return self._stop_future is not None

    def can_close(self) -> bool:
        return (not self._timer.isActive() and not self.is_starting and not self.is_stopping
                and self._poll_future is None
                and not self._stop_failed
                and getattr(self._service, "stop_required", False) is not True)

    def start(self, native_config: Any) -> None:
        if self._closed or self._closing:
            raise RuntimeError("continuous Sweep presenter is closing")
        if self._timer.isActive() or self.is_starting or self.is_stopping or self._poll_future is not None:
            return
        if self._stop_failed:
            raise RuntimeError("previous continuous Sweep cleanup is unresolved; retry Stop")
        future = self._stop_executor.submit(self._service.start, native_config)
        self._start_future = future
        self.starting_changed.emit(True)
        future.add_done_callback(self._start_completed.emit)

    @Slot(object)
    def _finish_start(self, future: Future[None]) -> None:
        if future is not self._start_future:
            return
        error: Exception | None = None
        try:
            future.result()
        except Exception as caught:
            error = caught
        if error is None and not self._closing:
            # Publish Running while Starting still holds the observer lock.
            # No synchronous listener can see an unlocked idle gap between
            # successful admission and the running state.
            self._timer.start()
            self.running_changed.emit(True)
            self._start_future = None
            self.starting_changed.emit(False)
            return
        if error is not None:
            self.task_failed.emit(str(error))
        # The application facade distinguishes a rejected admission (no
        # cleanup authority) from a partially owned failed Start. Only the
        # latter is allowed to dispatch Stop for this operation.
        self._start_future = None
        if error is not None and getattr(self._service, "stop_required", False) is True and not self._closing:
            # Establish Stopping before releasing Starting, so a partially
            # owned failed admission never exposes an actionable idle gap.
            self.stop()
        self.starting_changed.emit(False)

    def stop(self) -> None:
        """Request stop without blocking Qt; Start stays barred until completion."""
        if self._closed or self._closing or self.is_starting:
            return
        if (not self._timer.isActive() and not self._stop_failed
                and getattr(self._service, "stop_required", False) is not True) or self._stop_future is not None:
            return
        self._timer.stop()
        future = self._stop_executor.submit(self._stop_and_snapshot)
        self._stop_future = future
        self.stopping_changed.emit(True)
        future.add_done_callback(self._stop_completed.emit)

    def _poll_and_prepare(self) -> ContinuousSweepDisplaySnapshot | _PreparedPublication:
        snapshot = self._service.poll_latest()
        if self._snapshot_admitter is not None:
            snapshot = self._snapshot_admitter(snapshot)
        if self._snapshot_preparer is None:
            return snapshot
        # Build the immutable coherent bundle once, on this same worker. The
        # property validates large arrays; consumers must not reconstruct it
        # repeatedly on the GUI thread merely to obtain the same packet.
        bundle = snapshot.analyzer_bundle
        return _PreparedPublication(snapshot, bundle, self._snapshot_preparer(snapshot, bundle))

    def _stop_and_snapshot(self) -> ContinuousSweepDisplaySnapshot | _PreparedPublication:
        self._service.stop()
        return self._poll_and_prepare()

    @Slot(object)
    def _finish_stop(self, future: Future[ContinuousSweepDisplaySnapshot | _PreparedPublication]) -> None:
        if future is not self._stop_future:
            return
        # Both jobs use the same executor. Preserve a terminal publication
        # already drained by the in-flight poll, before delivering the Stop
        # snapshot. Qt callback arrival order must not reverse these packets.
        if self._poll_future is not None:
            self._finish_poll(self._poll_future)
        try:
            self._emit_snapshot(future.result())
        except Exception as error:
            self._stop_failed = True
            self.task_failed.emit(str(error))
        else:
            self._stop_failed = False
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

    def prepare_shutdown(self) -> None:
        """V2 closes only after explicit Stop and its terminal acknowledgement."""
        if self._closed:
            return
        if not self.can_close():
            raise RuntimeError("continuous Sweep must acknowledge Stop before close")
        self._closing = True
        self._timer.stop()

    def finish_shutdown(self) -> None:
        """Idle, quiesced close; no Qt timer or finish handler on this worker."""
        if self._closed:
            return
        if not self._closing:
            raise RuntimeError("Sweep must be quiesced before cleanup")
        try:
            self._service.close()
        finally:
            self._stop_executor.shutdown(wait=True)
        self._closed = True

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closing = True
        if self._start_future is not None:
            start_future = self._start_future
            try:
                start_future.result()
            except Exception:
                pass  # Reported exactly once by _finish_start.
            self._finish_start(start_future)
        was_running = self._timer.isActive()
        self._timer.stop()
        if self._poll_future is not None:
            poll_future = self._poll_future
            try:
                poll_future.result()
            except Exception:
                pass  # Reported exactly once by _finish_poll.
            self._finish_poll(poll_future)
        # Shutdown owns cleanup after Start resolution; regular Stop remains
        # barred while ``_closing`` prevents any competing UI command.
        if (self._stop_future is None
                and (was_running or self._stop_failed
                     or getattr(self._service, "stop_required", False) is True)):
            self._timer.stop()
            stop_future = self._stop_executor.submit(self._stop_and_snapshot)
            self._stop_future = stop_future
            self.stopping_changed.emit(True)
            stop_future.add_done_callback(self._stop_completed.emit)
        if self._stop_future is not None:
            # Legacy close is still synchronous; it must not close the service
            # concurrently with the already requested stop worker.
            stop_future = self._stop_future
            try:
                stop_future.result()
            except Exception:
                pass  # Reported exactly once by _finish_stop.
            self._finish_stop(stop_future)
        try:
            self._service.close()
        finally:
            self._stop_executor.shutdown(wait=True)
        self._closed = True

    def _poll(self) -> None:
        if (self._closed or self._closing or self.is_starting or self.is_stopping
                or not self._timer.isActive() or self._poll_future is not None):
            return
        # Single-flight until GUI delivery, not merely until worker exit. Timer
        # ticks never accumulate executor jobs or queued snapshots. Stop joins
        # behind at most this one finite drain; no parallel service access.
        future = self._stop_executor.submit(self._poll_and_prepare)
        self._poll_future = future
        future.add_done_callback(self._poll_completed.emit)

    @Slot(object)
    def _finish_poll(self, future: Future[ContinuousSweepDisplaySnapshot | _PreparedPublication]) -> None:
        if future is not self._poll_future or not future.done():
            return
        try:
            self._emit_snapshot(future.result())
        except Exception as error:
            # A publication error does not mean acquisition has stopped.
            # Latch admission before any externally visible callback, then
            # use the same nonblocking stop path to release the receiver.
            self._stop_failed = True
            self.stop()
            self.task_failed.emit(str(error))
        finally:
            self._poll_future = None

    def _emit_snapshot(self, publication: ContinuousSweepDisplaySnapshot | _PreparedPublication) -> None:
        # Validate/convert before any consumer sees part of a rejected packet.
        # A conversion failure follows the same owned Stop path as poll failure.
        if isinstance(publication, _PreparedPublication):
            snapshot = publication.snapshot
        else:
            if self.prepares_snapshots:
                raise ValueError("Configured Sweep presentation requires worker preparation")
            snapshot = publication
        bundle = publication.bundle if isinstance(publication, _PreparedPublication) else snapshot.analyzer_bundle
        metrics: ContinuousSweepDisplayMetrics = snapshot.metrics
        self.metrics_ready.emit(metrics)
        if snapshot.line is not None:
            self.line_ready.emit(snapshot.line)
        if bundle is not None:
            self.analyzer_ready.emit(bundle)
        if isinstance(publication, _PreparedPublication):
            self.prepared_snapshot_ready.emit(publication.presentation)
        self.snapshot_ready.emit(snapshot)


__all__ = ["ContinuousSweepPresenter"]
