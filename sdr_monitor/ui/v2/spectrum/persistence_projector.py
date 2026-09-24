"""Bounded optional persistence-image owner with an opt-in resumable mode."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from threading import Event

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .allocation_budget import AllocationReservation, PresentationAllocationBudget
from .persistence_projection import (
    PersistenceImageRequest,
    PersistenceImageSteps,
    PreparedPersistenceImage,
    persistence_image_reserve,
    prepare_persistence_image,
)
from .retained_bytes import retained_arrays, union_bytes

DEFAULT_PERSISTENCE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PersistenceWork:
    owner: object
    request: PersistenceImageRequest


@dataclass(frozen=True, slots=True)
class PersistenceDelivery:
    owner: object
    request: PersistenceImageRequest
    result: PreparedPersistenceImage | None = None
    error: str | None = None


class PersistenceProjector(QObject):
    """Run one optional density transform plus one replaceable latest request.

    The caller owns the executor and its shutdown. This port keeps at most one
    submitted Future and one pending request; it never waits on the GUI thread.
    """

    ready = Signal(object)
    settled = Signal(object)
    _done = Signal(object)

    def __init__(
        self,
        submit: Callable[[Callable[[], object]], Future],
        *,
        allocation_budget: PresentationAllocationBudget | None = None,
        max_retained_bytes: int = DEFAULT_PERSISTENCE_BYTES,
        resumable: bool = False,
        max_chunks: int = 8,
    ) -> None:
        super().__init__()
        if isinstance(max_retained_bytes, bool) or not isinstance(max_retained_bytes, int) or max_retained_bytes < 1:
            raise ValueError("persistence projection budget must be a positive integer")
        if not isinstance(resumable, bool) or isinstance(max_chunks, bool) or not isinstance(max_chunks, int) or max_chunks < 1:
            raise ValueError("resumable mode and a positive chunk quota must be explicit")
        self._submit = submit
        self._resumable = resumable
        self._max_chunks = max_chunks
        self._steps: PersistenceImageSteps | None = None
        self._dispatch_queued = False
        self.allocation_budget = allocation_budget
        self.max_retained_bytes = max_retained_bytes
        self._active: PersistenceWork | None = None
        self._pending: PersistenceWork | None = None
        self._active_storage: dict[int, int] = {}
        self._pending_storage: dict[int, int] = {}
        self._active_reserve = self._pending_reserve = 0
        self._future: Future | None = None
        self._cancel = Event()
        self._reservation: AllocationReservation | None = None
        self._closed = False
        self._suspended = False
        self.superseded = 0
        self.cancelled = 0
        self.completed = 0
        self.byte_rejections = 0
        self.peak_retained_bytes = 0
        self._done.connect(self._finish, Qt.ConnectionType.QueuedConnection)

    @property
    def has_active(self) -> bool:
        return self._active is not None

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    @property
    def retained_bytes(self) -> int:
        return (union_bytes(self._active_storage, self._pending_storage)
                + self._active_reserve + self._pending_reserve)

    def offer(self, owner: object, request: PersistenceImageRequest) -> None:
        if self._closed or self._suspended:
            return
        work = PersistenceWork(owner, request)
        if self._active is not None and self._active.owner is owner and self._active.request is request:
            return
        if self._pending is not None:
            self.superseded += 1
        self._pending = work
        self._pending_storage = retained_arrays(request)
        self._pending_reserve = persistence_image_reserve(request)
        active_storage = self._active_storage
        proposed = (union_bytes(active_storage, self._pending_storage)
                    + self._active_reserve + self._pending_reserve)
        if proposed > self.max_retained_bytes:
            self._pending = None
            self._pending_storage = {}
            self._pending_reserve = 0
            self.byte_rejections += 1
            self.ready.emit(PersistenceDelivery(owner, request,
                error="Persistence projection retained-byte budget exceeded"))
            return
        active = self._active
        if active is not None and (active.owner is not owner
                or active.request.policy != request.policy
                or active.request.history_revision != request.history_revision):
            self._cancel_active()
        self.peak_retained_bytes = max(self.peak_retained_bytes, self.retained_bytes)
        if self._resumable:
            self._queue_dispatch()
        else:
            self._dispatch()

    def cancel(self, owner: object) -> None:
        pending = self._pending
        if pending is not None and pending.owner is owner:
            self._pending = None
            self._pending_storage = {}
            self._pending_reserve = 0
            self.settled.emit(pending)
        if self._active is not None and self._active.owner is owner:
            self._cancel_active()

    def set_suspended(self, suspended: bool) -> None:
        if self._closed or self._suspended == bool(suspended):
            return
        self._suspended = bool(suspended)
        if self._suspended:
            active = self._active
            if active is not None:
                if self._pending is None:
                    self._pending = active
                    self._pending_storage = self._active_storage
                    self._pending_reserve = 0
                self._cancel_active()
        else:
            if self._resumable:
                self._queue_dispatch()
            else:
                self._dispatch()

    def dispose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pending = None
        self._pending_storage = {}
        self._pending_reserve = 0
        self._cancel_active()

    def release_after_shutdown(self) -> None:
        if not self._closed:
            raise RuntimeError("Persistence projection must be disposed before terminal release")
        future = self._future
        if future is not None:
            if not future.done():
                raise RuntimeError("Persistence projection worker has not acknowledged shutdown")
            self._finish(future)
        if self._resumable and self._active is not None:
            # A continuation may be between chunks when shutdown begins.
            self._retire_resumable()
        if self._future is not None:
            raise RuntimeError("Persistence projection release requires a joined worker")
        self._active = self._pending = None
        self._active_storage = self._pending_storage = {}
        self._active_reserve = self._pending_reserve = 0

    def _cancel_active(self) -> None:
        self._cancel.set()
        if self._future is not None:
            self._future.cancel()
        elif self._resumable and self._active is not None:
            self.cancelled += 1
            self._retire_resumable()

    def _queue_dispatch(self) -> None:
        if self._closed or self._dispatch_queued:
            return
        self._dispatch_queued = True
        QTimer.singleShot(0, self._flush_queued_dispatch)

    def _flush_queued_dispatch(self) -> None:
        self._dispatch_queued = False
        self._dispatch()

    def _dispatch(self) -> None:
        if self._resumable:
            self._dispatch_resumable()
            return
        if self._closed or self._suspended or self._future is not None or self._pending is None:
            return
        work, self._pending = self._pending, None
        self._active = work
        self._active_storage, self._pending_storage = self._pending_storage, {}
        self._active_reserve, self._pending_reserve = self._pending_reserve, 0
        self._cancel = Event()
        try:
            if self.allocation_budget is not None:
                self._reservation = self.allocation_budget.reserve(self._active_reserve, work.request)
            reservation = self._reservation
            cancel = self._cancel

            def run() -> PreparedPersistenceImage:
                try:
                    result = prepare_persistence_image(work.request, cancelled=cancel.is_set)
                    if reservation is not None:
                        reservation.commit(result.image)
                    return result
                finally:
                    if reservation is not None:
                        reservation.close()

            future = self._submit(run)
        except Exception as error:
            if self._reservation is not None:
                self._reservation.close()
            self._reservation = None
            self._active = None
            self._active_storage = {}
            self._active_reserve = 0
            self.ready.emit(PersistenceDelivery(work.owner, work.request, error=str(error)))
            self.settled.emit(work)
            self._dispatch()
            return
        self._future = future
        future.add_done_callback(self._done.emit)

    def _dispatch_resumable(self) -> None:
        if self._closed or self._suspended or self._future is not None:
            return
        if self._active is not None and self._cancel.is_set():
            self.cancelled += 1
            self._retire_resumable()
            return
        if self._active is None:
            if self._pending is None:
                return
            work, self._pending = self._pending, None
            self._active = work
            self._active_storage, self._pending_storage = self._pending_storage, {}
            # Suspend may retain a pending intent with zero temporary reserve.
            self._active_reserve = persistence_image_reserve(work.request)
            self._pending_reserve = 0
            self._cancel = Event()
            try:
                if self.allocation_budget is not None:
                    self._reservation = self.allocation_budget.reserve(self._active_reserve, work.request)
                self._steps = PersistenceImageSteps(work.request)
            except Exception as error:
                self.ready.emit(PersistenceDelivery(work.owner, work.request, error=str(error)))
                self._retire_resumable()
                return
        work = self._active
        steps = self._steps
        assert work is not None and steps is not None
        reservation = self._reservation
        cancel = self._cancel

        def run() -> PreparedPersistenceImage | None:
            result = steps.advance(max_chunks=self._max_chunks, cancelled=cancel.is_set)
            if result is not None and reservation is not None:
                reservation.commit(result.image)
            return result

        try:
            future = self._submit(run)
        except Exception as error:
            self.ready.emit(PersistenceDelivery(work.owner, work.request, error=str(error)))
            self._retire_resumable()
            return
        self._future = future
        future.add_done_callback(self._done.emit)

    def _retire_resumable(self) -> None:
        work = self._active
        if self._steps is not None:
            self._steps.close()  # Only after Future ACK or with no submitted Future.
            self._steps = None
        if self._reservation is not None:
            self._reservation.close()
            self._reservation = None
        self._future = None
        self._active = None
        self._active_storage = {}
        self._active_reserve = 0
        if work is not None and not self._closed:
            self.settled.emit(work)
            self._queue_dispatch()

    def _finish(self, future: Future) -> None:
        if self._resumable:
            self._finish_resumable(future)
            return
        if future is not self._future:
            return
        work = self._active
        try:
            if self._cancel.is_set() or future.cancelled():
                self.cancelled += 1
            elif not self._closed and work is not None:
                try:
                    result = future.result()
                except CancelledError:
                    self.cancelled += 1
                except Exception as error:
                    self.ready.emit(PersistenceDelivery(work.owner, work.request, error=str(error)))
                else:
                    self.completed += 1
                    self.ready.emit(PersistenceDelivery(work.owner, work.request, result=result))
        finally:
            if self._reservation is not None:
                self._reservation.close()
                self._reservation = None
            self._future = None
            self._active = None
            self._active_storage = {}
            self._active_reserve = 0
            if work is not None and not self._closed:
                self.settled.emit(work)
            self._dispatch()

    def _finish_resumable(self, future: Future) -> None:
        if future is not self._future:
            return
        work = self._active
        if self._closed or self._cancel.is_set() or future.cancelled():
            self.cancelled += 1
            self._retire_resumable()
            return
        assert work is not None
        try:
            result = future.result()
        except CancelledError:
            self.cancelled += 1
        except Exception as error:
            self.ready.emit(PersistenceDelivery(work.owner, work.request, error=str(error)))
        else:
            if result is None:
                # Keep the same input/history/reservation, but submit no next
                # chunk until this GUI ACK returns to the Qt event loop.
                self._future = None
                self._queue_dispatch()
                return
            self.completed += 1
            self.ready.emit(PersistenceDelivery(work.owner, work.request, result=result))
        self._retire_resumable()
