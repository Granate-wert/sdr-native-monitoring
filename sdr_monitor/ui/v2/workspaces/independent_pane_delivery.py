"""A fixed-cadence Qt consumer for the bounded APP-07 pane handoff.

Worker threads only touch PaneFairDeliveryQueue. No per-FFT Qt signal is
posted: one GUI timer takes at most one ready packet from each pane per tick.
This limits presentation work and queued source roots independently of native
FFT/output rate; supersession counters must not be called ADC or RF loss.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage

from .independent_pane_board import IndependentPaneBoardV2


class IndependentPaneDeliveryPort(QObject):
    """Drain a 1–4-pane queue on the board's Qt thread at a bounded cadence."""

    rendered = Signal(str)
    render_failed = Signal(str, str)

    def __init__(self, board: IndependentPaneBoardV2, queue: PaneFairDeliveryQueue, *,
                 interval_ms: int = 16, parent: QObject | None = None) -> None:
        if not isinstance(board, IndependentPaneBoardV2) or not isinstance(queue, PaneFairDeliveryQueue):
            raise TypeError("pane delivery needs an independent board and fair queue")
        expected = tuple(slot.request.pane_id for slot in board._preparer.layout.slots
                         if slot.request is not None)
        if queue.pane_ids != expected:
            raise ValueError("pane queue order differs from the occupied board slots")
        if type(interval_ms) is not int or not 8 <= interval_ms <= 1000:
            raise ValueError("pane UI interval must be in [8, 1000] ms")
        super().__init__(parent or board)
        if self.thread() is not board.thread():
            raise ValueError("pane delivery and board must have one Qt thread")
        self._board = board
        self._queue = queue
        self._failed_panes: set[str] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.tick_once)

    @property
    def interval_ms(self) -> int:
        return self._timer.interval()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if QThread.currentThread() is not self.thread():
            raise RuntimeError("pane Qt timer must start on its owning thread")
        self._timer.start()

    def stop(self) -> None:
        if QThread.currentThread() is not self.thread():
            raise RuntimeError("pane Qt timer must stop on its owning thread")
        self._timer.stop()

    def tick_once(self) -> None:
        """At most four GUI applications, no worker computation or RF call."""
        if QThread.currentThread() is not self.thread():
            raise RuntimeError("pane delivery must render on the Qt thread")
        batch = self._queue.drain(max_items=4)
        for index, prepared in enumerate(batch):
            pane_id = prepared.delivery.pane_id
            if pane_id in self._failed_panes:
                self._report(prepared, PaneDeliveryStage.UI_REJECTED)
                continue
            try:
                applied = self._board.apply_prepared(prepared)
            except Exception:
                # The source remains owned until the controller's explicit
                # Stop. Do not retry, reopen, retune or silently mask a bad
                # cross-pane publication inside the UI timer.
                self._failed_panes.add(pane_id)
                self._queue.clear(pane_id)
                self._reject_uncommitted(prepared)
                self.render_failed.emit(pane_id, "Pane presentation failed; explicit Stop required")
            except BaseException:
                self._reject_uncommitted(prepared)
                for unattempted in batch[index + 1:]:
                    self._report(unattempted, PaneDeliveryStage.UI_REJECTED)
                raise
            else:
                if applied:
                    self.rendered.emit(pane_id)
                else:
                    self._report(prepared, PaneDeliveryStage.UI_REJECTED)

    def _report(self, prepared, stage: PaneDeliveryStage) -> None:
        ref = prepared.delivery.obligation_ref
        callback = getattr(self._board, "_stage_callback", None)
        if ref is not None and callback is not None:
            try:
                callback(ref, stage)
            except Exception:
                pass

    def _reject_uncommitted(self, prepared) -> None:
        ref = prepared.delivery.obligation_ref
        pane = self._board.pane(prepared.binding.slot_number)
        if ref is not None and (pane is None or pane.spectrum_scene.delivery_requires_ui_rejection(ref)):
            self._report(prepared, PaneDeliveryStage.UI_REJECTED)

    def failed_panes(self) -> tuple[str, ...]:
        return tuple(pane for pane in self._queue.pane_ids if pane in self._failed_panes)


__all__ = ["IndependentPaneDeliveryPort"]
