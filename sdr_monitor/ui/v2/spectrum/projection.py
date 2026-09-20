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
from .persistence_projection import (
    PersistenceImageRequest, PreparedPersistenceImage,
    persistence_image_reserve, prepare_persistence_image,
)

DEFAULT_PROJECTION_BYTES = 256 * 1024 * 1024


def _density_policy_key(request: "ProjectionRequest") -> tuple | None:
    density = request.persistence
    return None if density is None else (density.policy, density.history_revision)


def _request_storage(request: "ProjectionRequest") -> dict[int, int]:
    return retained_arrays(*(view for _, view in request.traces), request.current,
                           request.previous, request.prepared, request.persistence)


def _output_reserve(request: "ProjectionRequest") -> int:
    # At most nine points per viewport column per envelope, with float64
    # coordinates and values. Coverage uses at most 2048 columns + edges.
    # This reserves final result arrays, NOT reducer scratch or Qt paint data.
    width = max(1, int(request.viewport[2]))
    traces = sum(9 * min(width, view.point_count) *
                 (max(8, view.frequencies_hz.dtype.itemsize) + max(8, view.values.dtype.itemsize))
                 for _, view in request.traces)
    density = 0 if request.persistence is None else persistence_image_reserve(request.persistence)
    return traces + (2048 * (9 * 16 + 9) + 8 if request.current is not None else 0) + density


@dataclass(frozen=True, slots=True)
class ProjectionRequest:
    owner: object
    generation: int
    viewport: tuple[float, float, int]
    traces: tuple[tuple[TraceKind, SpectrumFrameView], ...]
    current: SweepFrame | None = None
    previous: SweepLineFrame | None = None
    prepared: PreparedSpectrumFrame | None = None
    # Layout/show requests must wait for coherent preparation already in flight.
    # An ordinary new source on accepted geometry can keep the pipeline moving.
    requires_preparation_handoff: bool = True
    persistence: PersistenceImageRequest | None = None


@dataclass(frozen=True, slots=True)
class SpectrumProjection:
    request: ProjectionRequest
    traces: tuple[tuple[TraceKind, EnvelopeTrace], ...]
    coverage: CoverageProjection | None
    finite_extent: tuple[float, float] | None
    persistence: PreparedPersistenceImage | None = None


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
    density = (None if request.persistence is None else
               prepare_persistence_image(request.persistence, cancelled=cancelled))
    return SpectrumProjection(request, tuple(traces), coverage, extent, density)


class SpectrumProjector(QObject):
    """Application-owned single-flight projection; submit owns the worker."""

    ready = Signal(object)
    failed = Signal(object, str)
    retry_ready = Signal()
    commit_requested = Signal()
    work_active_changed = Signal(bool)
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
        self._preparation_in_flight = False
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
                                               request.current, request.previous, request.prepared,
                                               request.persistence)
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
                or active.generation != request.generation or active.viewport != request.viewport
                or _density_policy_key(active) != _density_policy_key(request)):
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

    def set_preparation_in_flight(self, active: bool) -> None:
        """Layout/show handoff from an existing Sweep poll, without new work.

        A resumed/changed viewport retains only the existing latest request.
        On GUI delivery, replace that request with the newly coherent scene
        before dispatch. Ordinary deliveries without a waiting request retain
        timer coalescing; control suspension is independent and still wins.
        Source-only refreshes on successfully displayed geometry may dispatch
        during preparation; waiting those would discard already prepared frames
        whenever the next poll beats the scene's zero-timer offer.
        """
        if self._closed or self._preparation_in_flight == bool(active):
            return
        if active:
            self._preparation_in_flight = True
            return
        if self._pending is not None:
            self.request_commit()
        self._preparation_in_flight = False
        self._dispatch()

    def dispose(self) -> None:
        self._closed = True
        self._pending = None
        self._pending_storage = {}
        self._pending_reserve = 0
        self._retry_capacity = False
        # Never join a worker from Qt. Its application owner performs cleanup.
        self._cancel_active()

    def release_presentation_after_shutdown(self) -> None:
        """Release a joined worker result even when its Qt callback is queued."""
        if not self._closed:
            raise RuntimeError("Projection must be disposed before terminal release")
        future = self._future
        if future is not None:
            if not future.done():
                raise RuntimeError("Projection worker has not acknowledged shutdown")
            self._finish(future)  # closed: no result emission or new work

    def _dispatch(self) -> None:
        if (self._closed or self._suspended or self._future is not None
                or self._pending is None):
            return
        if self._preparation_in_flight and self._pending.requires_preparation_handoff:
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
                        allocation.commit(*(trace for _, trace in result.traces), result.coverage,
                                          None if result.persistence is None else result.persistence.image)
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
                       tuple((kind, id(view.source_frame)) for kind, view in request.traces),
                       _density_policy_key(request),
                       None if request.persistence is None else id(request.persistence.view))
                alone = union_bytes(_request_storage(request)) + _output_reserve(request)
                if alone <= self.allocation_budget.limit_bytes and key != self._shared_retry_key:
                    self._shared_retry_key = key
                    QTimer.singleShot(0, lambda key=key: self._retry_shared(key))
            return
        self._future = future
        self.work_active_changed.emit(True)
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
            # A newer prepared source already waiting must precede another
            # preparation on the shared worker. Otherwise the two independent
            # slots can lock into a permanent extra-frame queue after resize.
            # Same-source viewport churn still releases one preparation first:
            # waiting for ALL viewports to drain would starve fresh frames.
            pending = self._pending
            old_view = None if request is None else dict(request.traces).get(TraceKind.CURRENT)
            new_view = None if pending is None else dict(pending.traces).get(TraceKind.CURRENT)
            newer_source = (old_view is not None and new_view is not None
                            and old_view.source_frame is not new_view.source_frame)
            if newer_source and not self._closed and not self._suspended:
                self._dispatch()
                if self._future is None:  # refusal/error must not latch backpressure
                    self.work_active_changed.emit(False)
            else:
                self.work_active_changed.emit(False)
                self._dispatch()
            if self._retry_capacity and not self._closed:
                self._retry_capacity = False
                self.retry_ready.emit()  # Scene reoffers latest, no retained backlog.

    @property
    def retained_bytes(self) -> int:
        """Exposed backing arrays + reserved results until GUI acknowledgement."""
        return (union_bytes(self._active_storage, self._pending_storage)
                + self._active_reserve + self._pending_reserve)
