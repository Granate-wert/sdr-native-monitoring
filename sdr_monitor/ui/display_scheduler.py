"""Bounded GUI-clock coalescing for immutable Live snapshots.

The scheduler deliberately owns no service or device reference.  Producers may
publish faster than a monitor can paint; only the newest snapshot is retained
until the next GUI clock tick.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from ..domain import LiveSnapshot


@dataclass(frozen=True, slots=True)
class DisplaySchedulerMetrics:
    """Scalar accounting for the bounded presenter-to-renderer boundary."""

    offered: int = 0
    emitted: int = 0
    superseded: int = 0
    stale_ignored: int = 0
    pending: bool = False
    requested_fps: int = 120
    timer_ticks: int = 0
    timer_early_rearms: int = 0
    timer_lateness_ns: int = 0
    timer_max_lateness_ns: int = 0
    preparation_replacements: int = 0


class DisplayScheduler(QObject):
    """Latest-wins snapshot scheduler, bounded to one pending publication."""

    frame_ready = Signal(object)

    _SUPPORTED_FPS = frozenset((15, 30, 60, 120, 144, 240))

    def __init__(self, *, fps: int = 120, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pending: LiveSnapshot | None = None
        self._generation = -1
        self._fps = 120
        self._period_s = 1.0 / self._fps
        self._next_deadline_s: float | None = None
        self._superseded = 0
        self._offered = 0
        self._emitted = 0
        self._stale_ignored = 0
        # R10-D6 scalar-only scheduler observability. These counters reveal
        # timer cadence independently from frame production and paint cost;
        # they retain neither snapshots nor unbounded timing samples.
        self._timer_ticks = 0
        self._timer_early_rearms = 0
        self._timer_lateness_ns = 0
        self._timer_max_lateness_ns = 0
        self._preparation_replacements = 0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timer)
        self.set_fps(fps)
        self._arm_timer()

    @property
    def fps(self) -> int:
        return self._fps

    @property
    def superseded(self) -> int:
        return self._superseded

    @property
    def pending(self) -> bool:
        """Whether exactly one immutable snapshot awaits a GUI clock tick."""

        return self._pending is not None

    @property
    def metrics(self) -> DisplaySchedulerMetrics:
        """Return bounded presentation counters; no frame payload is retained."""

        return DisplaySchedulerMetrics(
            offered=self._offered,
            emitted=self._emitted,
            superseded=self._superseded,
            stale_ignored=self._stale_ignored,
            pending=self.pending,
            requested_fps=self._fps,
            timer_ticks=self._timer_ticks,
            timer_early_rearms=self._timer_early_rearms,
            timer_lateness_ns=self._timer_lateness_ns,
            timer_max_lateness_ns=self._timer_max_lateness_ns,
            preparation_replacements=self._preparation_replacements,
        )

    def take_pending_replacement(self, admitted: LiveSnapshot) -> LiveSnapshot | None:
        """Refresh ONE already cadence-admitted, not-yet-dispatched render.

        GUI-owner only. This does not grant a new render slot, emit a signal,
        move the FPS deadline or touch an active preparation. Configuration
        generations never cross this hand-off. Separate accounting avoids
        counting the replacement as another timer emission or analytical loss.
        """
        pending = self._pending
        if (pending is None or pending.generation != admitted.generation
                or pending.sequence < admitted.sequence):
            return None
        self._pending = None
        self._preparation_replacements += 1
        return pending

    def set_fps(self, fps: int) -> None:
        value = int(fps)
        if value not in self._SUPPORTED_FPS:
            raise ValueError("display FPS must be 15, 30, 60, 120, 144 or 240")
        self._fps = value
        self._period_s = 1.0 / value
        self._next_deadline_s = time.monotonic() + self._period_s
        if self._timer.isActive():
            self._arm_timer()

    def offer(self, snapshot: LiveSnapshot) -> None:
        """Retain a newer snapshot without creating a producer-rate backlog."""

        if snapshot.generation < self._generation:
            self._stale_ignored += 1
            return
        self._offered += 1
        if self._pending is not None:
            self._superseded += 1
        self._pending = snapshot

    def shutdown(self) -> None:
        self._timer.stop()
        self._pending = None
        self._next_deadline_s = None

    def reset_metrics(self) -> None:
        """Reset scalar observability at a controlled measurement boundary.

        Pending/latest frame state and generation ordering remain untouched;
        this deliberately cannot turn a measurement reset into a hidden
        presentation backlog or a replay of an old frame.
        """

        self._superseded = 0
        self._offered = 0
        self._emitted = 0
        self._stale_ignored = 0
        self._timer_ticks = 0
        self._timer_early_rearms = 0
        self._timer_lateness_ns = 0
        self._timer_max_lateness_ns = 0
        self._preparation_replacements = 0

    def _on_timer(self) -> None:
        """Emit at the requested average cadence despite integer Qt intervals.

        QTimer accepts whole milliseconds.  A fixed 8-ms interval turns an
        advertised 120-Hz display mode into approximately 125 Hz.  The next
        deadline is therefore fractional.  Round upward to the next event-loop
        millisecond: it favours a stable, bounded Qt loop over a zero-delay
        timer spin when the remaining deadline is sub-millisecond.
        """

        now = time.monotonic()
        self._timer_ticks += 1
        deadline = self._next_deadline_s
        if deadline is None:
            self._next_deadline_s = now + self._period_s
            self._arm_timer()
            return
        if now < deadline:
            self._timer_early_rearms += 1
            self._arm_timer()
            return
        lateness_ns = max(0, round((now - deadline) * 1_000_000_000.0))
        self._timer_lateness_ns += lateness_ns
        self._timer_max_lateness_ns = max(self._timer_max_lateness_ns, lateness_ns)
        self._flush()
        next_deadline = deadline + self._period_s
        if next_deadline <= now:
            next_deadline = now + self._period_s
        self._next_deadline_s = next_deadline
        self._arm_timer()

    def _arm_timer(self) -> None:
        deadline = self._next_deadline_s
        if deadline is None:
            deadline = time.monotonic() + self._period_s
            self._next_deadline_s = deadline
        remaining_s = deadline - time.monotonic()
        remaining_ms = max(1, math.ceil(remaining_s * 1000.0))
        self._timer.start(remaining_ms)

    def _flush(self) -> None:
        snapshot = self._pending
        self._pending = None
        if snapshot is None or snapshot.generation < self._generation:
            if snapshot is not None:
                self._stale_ignored += 1
            return
        self._generation = snapshot.generation
        self._emitted += 1
        self.frame_ready.emit(snapshot)


__all__ = ["DisplayScheduler", "DisplaySchedulerMetrics"]
