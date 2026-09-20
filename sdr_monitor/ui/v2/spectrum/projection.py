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
from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

from .contracts import EnvelopeTrace, PreparedSpectrumFrame, SpectrumFrameView, TraceKind, finite_value_extent
from .envelope import peak_preserving_envelope
from .sweep_coverage import CoverageProjection, SweepCoverageState, SweepFrame
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from .cancellation import CancelCheck, check_cancelled
from .retained_bytes import retained_arrays, union_bytes
from .allocation_budget import AllocationReservation, PresentationAllocationBudget, PresentationBudgetExceeded

DEFAULT_PROJECTION_BYTES = 256 * 1024 * 1024


def _request_storage(request: "ProjectionRequest") -> dict[int, int]:
    return retained_arrays(*(view for _, view in request.traces), request.current,
                           request.previous, request.prepared)


def _output_reserve(request: "ProjectionRequest") -> int:
    # At most nine points per viewport column per envelope, with float64
    # coordinates and values. Coverage uses at most 2048 columns + edges.
    # This reserves final result arrays, NOT reducer scratch or Qt paint data.
    width = max(1, int(request.viewport[2]))
    traces = sum(9 * min(width, view.point_count) *
                 (max(8, view.frequencies_hz.dtype.itemsize) + max(8, view.values.dtype.itemsize))
                 for _, view in request.traces)
    return traces + (2048 * (9 * 16 + 9) + 8 if request.current is not None else 0)


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
    retry_ready = Signal()
    commit_requested = Signal()
    _done = Signal(object)

    def __init__(self, submit: Callable[[Callable[[], SpectrumProjection]], Future],
                 *, max_retained_bytes: int = DEFAULT_PROJECTION_BYTES,
                 allocation_budget: PresentationAllocationBudget | None = None) -> None:
        super().__init__()
        self.allocation_budget = allocation_budget
        self._allocation: AllocationReservation | None = None
        self._shared_retry_key: tuple | None = None
        if isinstance(max_retained_bytes, bool) or not isinstance(max_retained_bytes, int) or max_retained_bytes < 1:
            raise ValueError("projection byte budget must be a positive integer")
        self.max_retained_bytes = max_retained_bytes
        self._active_storage: dict[int, int] = {}
        self._pending_storage: dict[int, int] = {}
        self._active_reserve = self._pending_reserve = 0
        self._retry_capacity = False
        self.byte_rejections = 0
        self.peak_retained_bytes = 0
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
        try:
            if self.allocation_budget is not None:
                self.allocation_budget.observe(*(view for _, view in request.traces),
                                               request.current, request.previous, request.prepared)
            storage, reserve = _request_storage(request), _output_reserve(request)
        except (TypeError, ValueError) as error:
            self.failed.emit(request, str(error))
            return
        alone = union_bytes(storage) + reserve
        proposed = union_bytes(self._active_storage, storage) + self._active_reserve + reserve
        if proposed > self.max_retained_bytes:
            self.byte_rejections += 1
            # Discard an older queued request as well, never render it as the
            # rejected latest source. Do not retain the rejected publication.
            self._pending = None
            self._pending_storage = {}
            self._pending_reserve = 0
            self._retry_capacity = alone <= self.max_retained_bytes and self._active is not None
            self.failed.emit(request, "Spectrum projection retained-byte budget exceeded")
            return
        if self._pending is not None:
            self.superseded += 1
        self._pending = request
        self._retry_capacity = False
        self._pending_storage, self._pending_reserve = storage, reserve
        self.peak_retained_bytes = max(self.peak_retained_bytes, self.retained_bytes)
        active = self._active
        if active is not None and (active.owner is not request.owner
                or active.generation != request.generation or active.viewport != request.viewport):
            self._cancel_active()
        self._dispatch()

    def request_commit(self) -> None:
        """The owning preparation boundary completed its synchronous delivery."""
        if not self._closed:
            self.commit_requested.emit()

    def cancel_pending(self, owner: object) -> None:
        if self._pending is not None and self._pending.owner is owner:
            self._pending = None
            self._pending_storage = {}
            self._pending_reserve = 0
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
                self._pending_storage = self._active_storage
                # No second result is reserved until the cancelled job acks.
                self._pending_reserve = 0
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
        self._pending_storage = {}
        self._pending_reserve = 0
        self._retry_capacity = False
        # Never join a worker from Qt. Its application owner performs cleanup.
        self._cancel_active()

    def _dispatch(self) -> None:
        if self._closed or self._suspended or self._future is not None or self._pending is None:
            return
        request, self._pending = self._pending, None
        self._active_storage, self._pending_storage = self._pending_storage, {}
        self._active_reserve, self._pending_reserve = _output_reserve(request), 0
        self._active = request
        cancel = self._cancel = Event()
        try:
            allocation = (None if self.allocation_budget is None else
                          self.allocation_budget.reserve(self._active_reserve))
            self._allocation = allocation
            self._shared_retry_key = None

            def project():
                try:
                    result = project_spectrum(request, cancelled=cancel.is_set)
                    if allocation is not None:
                        allocation.commit(*(trace for _, trace in result.traces), result.coverage)
                    return result
                finally:
                    if allocation is not None:
                        allocation.close()

            future = self._submit(project)
        except Exception as error:
            if self._allocation is not None:
                self._allocation.close()
                self._allocation = None
            self._active = None
            self._active_storage = {}
            self._active_reserve = 0
            self.failed.emit(request, str(error))
            if isinstance(error, PresentationBudgetExceeded) and self.allocation_budget is not None:
                # One queued reoffer allows the just-acknowledged Future/stack
                # roots to die first. Never retain its payload or spin on an
                # intrinsically oversized / persistently unavailable request.
                key = (id(request.owner), request.generation, request.viewport,
                       tuple((kind, id(view.source_frame)) for kind, view in request.traces))
                alone = union_bytes(_request_storage(request)) + _output_reserve(request)
                if alone <= self.allocation_budget.limit_bytes and key != self._shared_retry_key:
                    self._shared_retry_key = key
                    QTimer.singleShot(0, lambda key=key: self._retry_shared(key))
            return
        self._future = future
        future.add_done_callback(self._done.emit)

    def _retry_shared(self, key: tuple) -> None:
        if not self._closed and self._shared_retry_key == key:
            self.retry_ready.emit()  # Actual scene supplies its latest, not a stored rejected frame.

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
            if self._allocation is not None:
                self._allocation.close()  # Also covers cancellation before worker execution.
                self._allocation = None
            self._future = self._active = None
            self._active_storage = {}
            self._active_reserve = 0
            self._dispatch()
            if self._retry_capacity and not self._closed:
                self._retry_capacity = False
                self.retry_ready.emit()  # Scene reoffers latest, no retained backlog.

    @property
    def retained_bytes(self) -> int:
        """Exposed backing arrays + reserved results until GUI acknowledgement."""
        return (union_bytes(self._active_storage, self._pending_storage)
                + self._active_reserve + self._pending_reserve)
