"""Responsive Qt presenter for the explicit R11-Q HackRF workflow.

All application calls execute through one bounded worker.  The GUI observes
only immutable redacted snapshots and one latest reduced frame per poll; it
never imports a vendor library or owns a receiver.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from ...application import HackrfActivationApplicationSnapshot, HackrfActivationUseCases
from ...services import HackrfLiveActivationPlan


_POLL_INTERVAL_MS = 33
_POLL_MAX_ITEMS = 8


@dataclass(frozen=True, slots=True)
class HackrfActivationPresenterMetrics:
    """Bounded scalar UI-side accounting, distinct from native loss metrics."""

    poll_requests: int
    poll_superseded_frames: int
    poll_failures: int
    operation_rejected_while_busy: int


class HackrfActivationPresenter(QObject):
    """Serialize explicit activation operations and coalesce reduced polling."""

    snapshot_changed = Signal(object)
    latest_frame_ready = Signal(object)
    metrics_ready = Signal(object)
    task_failed = Signal(str)
    busy_changed = Signal(bool)
    _operation_finished = Signal(object)
    _poll_finished = Signal(object)

    def __init__(self, use_cases: HackrfActivationUseCases, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if not callable(getattr(use_cases, "current", None)):
            raise ValueError("HackRF activation use cases must provide current")
        self._use_cases = use_cases
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hackrf-activation")
        self._closed = False
        self._operation_pending = False
        self._poll_pending = False
        self._last_snapshot = use_cases.current()
        self._poll_requests = 0
        self._poll_superseded_frames = 0
        self._poll_failures = 0
        self._operation_rejected_while_busy = 0
        self._poll_timer = QTimer(self)
        self._poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.poll_once)
        self._operation_finished.connect(self._complete_operation)
        self._poll_finished.connect(self._complete_poll)

    @property
    def metrics(self) -> HackrfActivationPresenterMetrics:
        return HackrfActivationPresenterMetrics(
            poll_requests=self._poll_requests,
            poll_superseded_frames=self._poll_superseded_frames,
            poll_failures=self._poll_failures,
            operation_rejected_while_busy=self._operation_rejected_while_busy,
        )

    @property
    def current_snapshot(self) -> HackrfActivationApplicationSnapshot:
        return self._last_snapshot

    def refresh(self) -> None:
        self._submit_operation(self._use_cases.current)

    def request_preflight(self, plan: HackrfLiveActivationPlan) -> None:
        self._submit_operation(lambda: self._use_cases.request_preflight(plan))

    def confirm_start(self, *, user_confirmed: bool) -> None:
        self._submit_operation(
            lambda: self._use_cases.confirm_start(user_confirmed=user_confirmed)
        )

    def stop(self, timeout_ms: int = 1_000) -> None:
        self._submit_operation(lambda: self._use_cases.stop(timeout_ms))

    def poll_once(self) -> None:
        """Request at most one latest-only reduced frame batch at a time."""

        if self._closed or self._operation_pending or self._poll_pending or not self._last_snapshot.active:
            return
        self._poll_pending = True
        self._poll_requests += 1
        future = self._executor.submit(
            lambda: (
                self._use_cases.poll_spectrum_frames(_POLL_MAX_ITEMS),
                self._use_cases.metrics(),
                self._use_cases.current(),
            )
        )
        future.add_done_callback(self._poll_finished.emit)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._poll_timer.stop()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _submit_operation(self, operation: Callable[[], HackrfActivationApplicationSnapshot]) -> None:
        if self._closed:
            return
        if self._operation_pending or self._poll_pending:
            self._operation_rejected_while_busy += 1
            return
        self._operation_pending = True
        self.busy_changed.emit(True)
        future = self._executor.submit(operation)
        future.add_done_callback(self._operation_finished.emit)

    def _complete_operation(self, future: Future[HackrfActivationApplicationSnapshot]) -> None:
        try:
            snapshot = future.result()
            if not isinstance(snapshot, HackrfActivationApplicationSnapshot):
                raise TypeError("activation application returned an invalid snapshot")
        except Exception:
            self.task_failed.emit("HackRF activation operation failed")
        else:
            self._publish_snapshot(snapshot)
        finally:
            self._operation_pending = False
            self.busy_changed.emit(False)

    def _complete_poll(
        self,
        future: Future[tuple[tuple[object, ...], object | None, HackrfActivationApplicationSnapshot]],
    ) -> None:
        try:
            frames, metrics, snapshot = future.result()
            if not isinstance(snapshot, HackrfActivationApplicationSnapshot):
                raise TypeError("activation application returned an invalid snapshot")
        except Exception:
            self._poll_failures += 1
        else:
            if frames:
                self._poll_superseded_frames += max(0, len(frames) - 1)
                self.latest_frame_ready.emit(frames[-1])
            if metrics is not None:
                self.metrics_ready.emit(metrics)
            self._publish_snapshot(snapshot)
        finally:
            self._poll_pending = False

    def _publish_snapshot(self, snapshot: HackrfActivationApplicationSnapshot) -> None:
        self._last_snapshot = snapshot
        if snapshot.active and not self._poll_timer.isActive():
            self._poll_timer.start()
        elif not snapshot.active and self._poll_timer.isActive():
            self._poll_timer.stop()
        self.snapshot_changed.emit(snapshot)


__all__ = ["HackrfActivationPresenter", "HackrfActivationPresenterMetrics"]
