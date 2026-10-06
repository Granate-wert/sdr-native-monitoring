"""Bounded unique-source Qt paint cadence; no frame/array ownership or RF claim."""

from collections.abc import Callable
from collections import deque
import os
from time import perf_counter_ns

import pyqtgraph as pg
from PySide6.QtGui import QPaintEvent

from sdr_monitor.domain.host_clock import HostClockKind, HostClockScope
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryObligationRef
from sdr_monitor.domain.pane_paint_timing import PanePaintReturnReceipt

PaintKey = tuple[tuple[object, ...], tuple[int, object, object]]
PaintReturnCallback = Callable[[PanePaintReturnReceipt], object]


def paint_return_receipt(ref: PaneDeliveryObligationRef, before_paint_ns: int,
                         sampled_after_return_ns: int) -> PanePaintReturnReceipt | None:
    """Build one builtin-host receipt without inventing a clock mapping."""
    try:
        return PanePaintReturnReceipt(
            ref,
            HostClockScope(HostClockKind.PERF_COUNTER_NS, os.getpid()),
            before_paint_ns,
            sampled_after_return_ns,
        )
    except (TypeError, ValueError):
        return None


def spectrum_paint_key(frame: object) -> PaintKey | None:
    """Scalar publication identity, including partial revisions and terminal state."""
    publication = frame
    frame = getattr(publication, "spectrum", publication)
    identity = getattr(frame, "identity", getattr(publication, "identity", None))
    source = getattr(frame, "source_id", getattr(identity, "source_id", None))
    sequence = getattr(frame, "sequence", getattr(publication, "sequence", None))
    if not isinstance(source, str) or not source or type(sequence) is not int:
        return None
    epoch = getattr(frame, "epoch", getattr(identity, "acquisition_epoch",
                                              getattr(publication, "epoch", None)))
    generation = getattr(frame, "config_generation",
                         getattr(identity, "config_generation",
                                 getattr(publication, "config_generation", None)))
    receiver = getattr(identity, "receiver_id",
                       getattr(frame, "receiver_id", getattr(publication, "receiver_id", None)))
    session = getattr(identity, "session_id",
                      getattr(frame, "session_id", getattr(publication, "session_id", None)))
    activation = getattr(identity, "host_activation_serial",
                         getattr(frame, "host_activation_serial",
                                 getattr(publication, "host_activation_serial", None)))
    run_serial = getattr(identity, "host_run_serial",
                         getattr(frame, "host_run_serial",
                                 getattr(publication, "host_run_serial", None)))
    resource = getattr(identity, "physical_resource_id",
                       getattr(frame, "physical_resource_id",
                               getattr(publication, "physical_resource_id", None)))
    state = getattr(frame, "state", getattr(publication, "state", None))
    state = getattr(state, "value", state)
    revision = getattr(frame, "revision", getattr(publication, "revision", None))
    def scalar(value):
        return value if value is None or type(value) in (str, int, bool) else None

    scope = (source, scalar(resource), scalar(receiver), scalar(session), scalar(run_serial),
             scalar(activation), scalar(epoch), scalar(generation),
             scalar(getattr(frame, "unit", getattr(publication, "unit", None))),
             hasattr(frame, "epoch") or hasattr(publication, "epoch"))
    return scope, (sequence, scalar(revision), scalar(state))


class UniquePaintCadence:
    """Mean period of distinct revisions painted during the last four seconds.

    Repainting the same source due to chrome/zoom/persistence does not count.
    A single scalar pending key describes the curve actually admitted by the
    renderer, not latest acquisition. Hidden views and new epochs start fresh.
    """
    def __init__(self) -> None:
        self.key: PaintKey | None = None
        self._last_key: PaintKey | None = None
        self._times: deque[int] = deque(maxlen=512)

    def clear(self) -> None:
        self.key = self._last_key = None
        self._times.clear()

    def admit(self, frame: object) -> None:
        key = spectrum_paint_key(frame)
        if key is None or self.key is not None and key[0] != self.key[0]:
            self.clear()
        self.key = key

    def painted(self, key: PaintKey | None, when_ns: int) -> None:
        if key is None or key != self.key or key == self._last_key:
            return
        if self._last_key is not None and key[1][0] < self._last_key[1][0]:
            return  # A previously superseded publication is not a fresh frame.
        if self._last_key is not None and key[1][0] == self._last_key[1][0]:
            revision, previous_revision = key[1][1], self._last_key[1][1]
            if (type(revision) is int and
                    (type(previous_revision) is int and revision < previous_revision
                     or self._last_key[1][2] in ("complete", "gap"))):
                return  # Neither a late partial nor terminal rollback is fresh.
        if self._times and when_ns <= self._times[-1]:
            self._times.clear()
        self._last_key = key
        self._times.append(when_ns)
        cutoff = when_ns - 4_000_000_000
        while len(self._times) > 1 and self._times[0] < cutoff:
            self._times.popleft()

    def period_ms(self, now_ns: int | None = None) -> float | None:
        now_ns = perf_counter_ns() if now_ns is None else now_ns
        if (len(self._times) < 2 or now_ns < self._times[-1]
                or now_ns - self._times[-1] > 1_000_000_000):
            return None
        return (self._times[-1] - self._times[0]) / (len(self._times) - 1) / 1e6


def cadence_graphics_widget(
    parent, cadence: UniquePaintCadence,
    paint_candidate: Callable[[QPaintEvent], PaintKey | None],
    custody_candidate: Callable[[QPaintEvent], object | None] | None = None,
    custody_returned: Callable[[object], None] | None = None,
    paint_returned: PaintReturnCallback | None = None,
):
    """Wrap the selected pg widget implementation, including opt-in observers.

    Per-widget callbacks stay on the widget instance rather than in the
    dynamically-created class method closure. A retained Qt paint class thus
    cannot retain its scene or source through bound custody callbacks. Looking
    up the base at construction preserves injected-widget seams; no global
    patch, timer, signal or worker is added.
    """
    class CadenceGraphics(pg.GraphicsLayoutWidget):
        paint_cadence: UniquePaintCadence
        paint_candidate: Callable[[QPaintEvent], PaintKey | None]
        custody_candidate: Callable[[QPaintEvent], object | None] | None
        custody_returned: Callable[[object], None] | None
        paint_returned: PaintReturnCallback | None

        def paintEvent(self, event):
            try:
                key = self.paint_candidate(event)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                key = None
            try:
                custody = (None if self.custody_candidate is None
                           else self.custody_candidate(event))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                custody = None
            before_paint_ns = None
            try:
                before_paint_ns = perf_counter_ns()
            except Exception:
                pass
            super().paintEvent(event)
            sampled_after_return_ns = None
            try:
                sampled_after_return_ns = perf_counter_ns()
            except Exception:
                pass
            if (before_paint_ns is not None and sampled_after_return_ns is not None
                    and custody is not None and self.paint_returned is not None):
                try:
                    receipt_value = custody
                    if isinstance(custody, tuple):
                        receipt_value = tuple(
                            receipt for item in custody
                            if isinstance(item, PaneDeliveryObligationRef)
                            and (receipt := paint_return_receipt(
                                item, before_paint_ns, sampled_after_return_ns)) is not None
                        )
                    elif isinstance(custody, PaneDeliveryObligationRef):
                        receipt_value = paint_return_receipt(
                            custody, before_paint_ns, sampled_after_return_ns)
                    if isinstance(receipt_value, tuple):
                        for receipt in receipt_value:
                            self.paint_returned(receipt)
                    elif isinstance(receipt_value, PanePaintReturnReceipt):
                        self.paint_returned(receipt_value)
                except Exception:
                    # Paint telemetry is optional and must never break rendering.
                    pass
            if key is not None and sampled_after_return_ns is not None:
                self.paint_cadence.painted(key, sampled_after_return_ns)
            if custody is not None and self.custody_returned is not None:
                self.custody_returned(custody)

    widget = CadenceGraphics(parent)
    widget.paint_cadence = cadence
    widget.paint_candidate = paint_candidate
    widget.custody_candidate = custody_candidate
    widget.custody_returned = custody_returned
    widget.paint_returned = paint_returned
    return widget
