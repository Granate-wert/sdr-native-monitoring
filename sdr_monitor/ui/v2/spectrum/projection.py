"""Bounded viewport work on an externally owned executor, never on a widget.

One job remains occupied through GUI acknowledgement, plus one latest request.
Requests retain immutable source references; no I/Q, device, Qt graphics or
executor ownership crosses this boundary.
"""
from collections.abc import Callable
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from threading import Event

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal, Slot

from .contracts import EnvelopeTrace, PreparedSpectrumFrame, SpectrumFrameView, TraceKind, finite_value_extent
from .envelope import peak_preserving_envelope
from .sweep_coverage import CoverageProjection, SweepCoverageState, SweepFrame
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from .cancellation import CancelCheck, check_cancelled


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    owner: object
    generation: int
    viewport: tuple[float, float, int]
    traces: tuple[tuple[TraceKind, SpectrumFrameView], ...]
    current: SweepFrame | None = None
    previous: SweepLineFrame | None = None
    prepared: PreparedSpectrumFrame | None = None


@dataclass(frozen=True, slots=True)
class SpectrumProjection:
    request: ProjectionRequest
    traces: tuple[tuple[TraceKind, EnvelopeTrace], ...]
    coverage: CoverageProjection | None
    finite_extent: tuple[float, float] | None


def project_spectrum(request: ProjectionRequest, *, cancelled: CancelCheck = None) -> SpectrumProjection:
    check_cancelled(cancelled)
    left, right, width = request.viewport
    traces = []
    extent = None
    for kind, view in request.traces:
        check_cancelled(cancelled)
        if view.frequencies_hz.flags.writeable or view.values.flags.writeable:
            raise ValueError("viewport projection requires immutable spectrum arrays")
        if kind is TraceKind.CURRENT:
            if request.prepared is not None:
                if request.prepared.view.source_frame is not view.source_frame:
                    raise ValueError("viewport preparation belongs to another spectrum")
                extent = request.prepared.finite_extent
            else:
                extent = finite_value_extent(view.values, cancelled=cancelled)
        start = max(0, int(np.searchsorted(view.frequencies_hz, left)) - 1)
        stop = min(view.point_count, int(np.searchsorted(view.frequencies_hz, right, side="right")) + 1)
        if stop <= start:
            start, stop = 0, view.point_count
        visible = SpectrumFrameView(view.source_frame, view.frequencies_hz[start:stop],
                                    view.values[start:stop], view.unit_label)
        envelope = peak_preserving_envelope(visible, width, cancelled=cancelled)
        envelope.frequencies_hz.setflags(write=False)
        envelope.values.setflags(write=False)
        traces.append((kind, envelope))
    coverage = None
    if request.current is not None:
        # A worker-local state, not the mutable GUI cache. Domain frames are
        # immutable and their compatibility was already admitted by accept().
        state = SweepCoverageState()
        state.current, state.previous = request.current, request.previous
        coverage = state.project(left, right, width, cancelled=cancelled)
    check_cancelled(cancelled)
    return SpectrumProjection(request, tuple(traces), coverage, extent)


class SpectrumProjector(QObject):
    """Application-owned single-flight projection; submit owns the worker."""

    ready = Signal(object)
    failed = Signal(object, str)
    _done = Signal(object)

    def __init__(self, submit: Callable[[Callable[[], SpectrumProjection]], Future]) -> None:
        super().__init__()
        self._submit = submit
        self._future: Future | None = None
        self._active: ProjectionRequest | None = None
        self._pending: ProjectionRequest | None = None
        self._closed = False
        self._suspended = False
        self._cancel = Event()
        self.superseded = 0
        self.completed = 0
        self.cancelled = 0
        self._done.connect(self._finish, Qt.ConnectionType.QueuedConnection)

    def offer(self, request: ProjectionRequest) -> None:
        if self._closed:
            return
        if self._pending is not None:
            self.superseded += 1
        self._pending = request
        active = self._active
        if active is not None and (active.owner is not request.owner
                or active.generation != request.generation or active.viewport != request.viewport):
            self._cancel_active()
        self._dispatch()

    def cancel_pending(self, owner: object) -> None:
        if self._pending is not None and self._pending.owner is owner:
            self._pending = None
        if self._active is not None and self._active.owner is owner:
            self._cancel_active()

    def set_suspended(self, suspended: bool) -> None:
        """Let accepted RF/control operations precede optional paint work.

        Retain just the latest request for resumption (Stop may retain a last
        measurement). This is not a stop command and owns no device state.
        """
        if self._closed or self._suspended == bool(suspended):
            return
        self._suspended = bool(suspended)
        if self._suspended:
            if self._pending is None and self._active is not None:
                self._pending = self._active
            self._cancel_active()
        else:
            self._dispatch()

    def _cancel_active(self) -> None:
        self._cancel.set()
        if self._future is not None:
            self._future.cancel()  # Removes queued work; running work observes Event.

    def dispose(self) -> None:
        self._closed = True
        self._pending = None
        # Never join a worker from Qt. Its application owner performs cleanup.
        self._cancel_active()

    def _dispatch(self) -> None:
        if self._closed or self._suspended or self._future is not None or self._pending is None:
            return
        request, self._pending = self._pending, None
        self._active = request
        cancel = self._cancel = Event()
        try:
            future = self._submit(lambda: project_spectrum(request, cancelled=cancel.is_set))
        except Exception as error:
            self._active = None
            self.failed.emit(request, str(error))
            return
        self._future = future
        future.add_done_callback(self._done.emit)

    @Slot(object)
    def _finish(self, future: Future) -> None:
        if future is not self._future:
            return
        request = self._active
        try:
            if self._cancel.is_set() or future.cancelled():
                self.cancelled += 1
            elif not self._closed:
                result = future.result()  # Already done; never a GUI wait.
                self.completed += 1
                self.ready.emit(result)
        except CancelledError:
            self.cancelled += 1
        except Exception as error:
            if not self._closed:
                self.failed.emit(request, str(error))
        finally:
            self._future = self._active = None
            self._dispatch()
