"""Bounded, fair handoff from independent pane preparation workers to Qt.

Only presentation packets are superseded here; analytical FFT, device scan
and native detector work already happened upstream. A terminal Sweep packet
has its own slot so a newer progressive preview does not erase that pass.
Multiple terminal passes under sustained GUI pressure can still supersede one
another, and that fact is counted rather than misreported as RF/sample loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from sdr_monitor.domain.analyzer import AnalyzerPublicationKind
from sdr_monitor.domain.sweep_progress import SweepProgressFrame

from .v2_pane_presentation import PreparedPaneDelivery


@dataclass(frozen=True, slots=True)
class PaneQueueMetrics:
    offered: int = 0
    delivered: int = 0
    latest_superseded: int = 0
    terminal_superseded: int = 0
    stale_rejected: int = 0
    cleared_on_stop: int = 0


@dataclass(slots=True)
class _Pending:
    terminal: PreparedPaneDelivery | None = None
    latest: PreparedPaneDelivery | None = None


def _order(packet: PreparedPaneDelivery) -> tuple[int, int, int, int, int]:
    bundle = packet.bundle
    epoch = bundle.acquisition_epoch
    if type(epoch) is not int:
        raise ValueError("pane publication has no exact acquisition epoch")
    frame = bundle.spectrum
    terminal = bundle.publication_kind in {
        AnalyzerPublicationKind.SWEEP_COMPLETE, AnalyzerPublicationKind.SWEEP_GAP}
    revision = frame.revision if isinstance(frame, SweepProgressFrame) else 0
    return (packet.delivery.host_activation_serial, epoch, frame.sequence, int(terminal), revision)


class PaneFairDeliveryQueue:
    """At most one terminal plus one latest publication per occupied pane."""

    def __init__(self, pane_ids: tuple[str, ...]) -> None:
        panes = tuple(pane_ids)
        if (not 1 <= len(panes) <= 4 or len(set(panes)) != len(panes)
                or any(not isinstance(pane, str) or not pane.strip() for pane in panes)):
            raise ValueError("pane queue requires 1–4 distinct occupied pane identities")
        self._order = panes
        self._pending = {pane: _Pending() for pane in panes}
        self._last_drained: dict[str, tuple[int, int, int, int, int]] = {}
        self._next_index = 0
        self._metrics = PaneQueueMetrics()
        self._lock = Lock()

    @property
    def pane_ids(self) -> tuple[str, ...]:
        return self._order

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(int(item.terminal is not None) + int(item.latest is not None)
                       for item in self._pending.values())

    def metrics(self) -> PaneQueueMetrics:
        with self._lock:
            return self._metrics

    def offer(self, packet: PreparedPaneDelivery) -> bool:
        if not isinstance(packet, PreparedPaneDelivery) or packet.delivery.pane_id not in self._pending:
            raise ValueError("prepared publication belongs to no queued pane")
        pane_id = packet.delivery.pane_id
        order = _order(packet)
        terminal = bool(order[3])
        with self._lock:
            current = self._pending[pane_id]
            last = self._last_drained.get(pane_id)
            if (last is not None and order <= last
                    or current.terminal is not None and order <= _order(current.terminal)
                    or not terminal and current.latest is not None and order <= _order(current.latest)):
                self._metrics = self._replace_metrics(stale_rejected=1)
                return False
            if terminal:
                if current.terminal is not None:
                    self._metrics = self._replace_metrics(terminal_superseded=1)
                current.terminal = packet
                if current.latest is not None and _order(current.latest) <= order:
                    current.latest = None
                    self._metrics = self._replace_metrics(latest_superseded=1)
            else:
                if current.latest is not None:
                    self._metrics = self._replace_metrics(latest_superseded=1)
                current.latest = packet
            self._metrics = self._replace_metrics(offered=1)
            return True

    def drain(self, *, max_items: int = 4) -> tuple[PreparedPaneDelivery, ...]:
        """Take at most one packet per pane, rotating first service each tick."""
        if type(max_items) is not int or not 1 <= max_items <= 4:
            raise ValueError("pane UI batch must be between one and four")
        with self._lock:
            result: list[PreparedPaneDelivery] = []
            count = len(self._order)
            cursor = self._next_index
            for offset in range(count):
                index = (cursor + offset) % count
                pane_id = self._order[index]
                pending = self._pending[pane_id]
                packet = pending.terminal if pending.terminal is not None else pending.latest
                if packet is None:
                    continue
                if pending.terminal is packet:
                    pending.terminal = None
                else:
                    pending.latest = None
                order = _order(packet)
                self._last_drained[pane_id] = order
                if pending.latest is not None and _order(pending.latest) <= order:
                    pending.latest = None
                    self._metrics = self._replace_metrics(latest_superseded=1)
                result.append(packet)
                self._next_index = (index + 1) % count
                if len(result) >= max_items:
                    break
            if result:
                self._metrics = self._replace_metrics(delivered=len(result))
            return tuple(result)

    def clear(self, pane_id: str | None = None) -> None:
        """Drop queued UI data on an explicit Stop, without clearing graphs."""
        if pane_id is not None and pane_id not in self._pending:
            raise ValueError("unknown pane identity")
        with self._lock:
            targets = self._order if pane_id is None else (pane_id,)
            removed = 0
            for target in targets:
                pending = self._pending[target]
                removed += int(pending.terminal is not None) + int(pending.latest is not None)
                pending.terminal = pending.latest = None
            if removed:
                self._metrics = self._replace_metrics(cleared_on_stop=removed)

    def _replace_metrics(self, **increments: int) -> PaneQueueMetrics:
        old = self._metrics
        return PaneQueueMetrics(**{
            name: getattr(old, name) + increments.get(name, 0)
            for name in PaneQueueMetrics.__dataclass_fields__
        })


__all__ = ["PaneFairDeliveryQueue", "PaneQueueMetrics"]
