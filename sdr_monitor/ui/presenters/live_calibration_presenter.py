"""One bounded analytics request on the SAME Live worker, not an RX owner."""
from concurrent.futures import Future
from dataclasses import dataclass, replace
from threading import Event
from typing import Protocol
from weakref import ref

from PySide6.QtCore import QObject, Qt, Signal, Slot

from sdr_monitor.domain.calibration import CalibrationProfile
from sdr_monitor.services.captured_calibration import CapturedCalibrationLane, CalibrationCommandPreview
from sdr_monitor.services.current_calibration_binding import CurrentCalibrationBinding
from sdr_monitor.services.live_calibration_signature import CalibrationFrontendContext
from sdr_monitor.services.receiver_calibration import ReceiverCalibrationRegistry

from ..v2.spectrum.allocation_budget import PresentationAllocationBudget
from ..v2.spectrum.cancellation import check_cancelled
from ..v2.state.prepared_calibration import PreparedCalibratedCurrent, prepare_calibrated_current
from ..v2.view_models.live_calibration_view_model import LiveCalibrationState


class CalibrationLiveOwner(Protocol):
    def current_calibration_binding(self, frontend: CalibrationFrontendContext) -> CurrentCalibrationBinding: ...
    def captured_bound_calibration_lane(self, registry: ReceiverCalibrationRegistry,
                                       binding: CurrentCalibrationBinding) -> CapturedCalibrationLane: ...


@dataclass(frozen=True, slots=True)
class _Request:
    generation: int
    phase: str
    frontend: CalibrationFrontendContext | None = None
    profile: CalibrationProfile | None = None
    preview: CalibrationCommandPreview | None = None


@dataclass(frozen=True, slots=True)
class _Completion:
    binding: CurrentCalibrationBinding | None = None
    lane: CapturedCalibrationLane | None = None
    value: object = None
    error: str | None = None


def _run(request: _Request, lane: CapturedCalibrationLane | None, owner: CalibrationLiveOwner,
         registry: ReceiverCalibrationRegistry, budget: PresentationAllocationBudget, cancel: Event) -> _Completion:
    """Short-lived worker frame; failures retain text, not exception tracebacks."""
    new_lane = None
    value: object
    try:
        check_cancelled(cancel.is_set)
        if request.phase == "bind":
            assert request.frontend is not None
            binding = owner.current_calibration_binding(request.frontend)
            new_lane = owner.captured_bound_calibration_lane(registry, binding)
            check_cancelled(cancel.is_set)
            return _Completion(binding, new_lane)
        if lane is None:
            raise RuntimeError("Explicit current Live binding required")
        if request.phase == "preview":
            value = lane.preview_selection(request.profile)
        elif request.phase == "select":
            assert request.preview is not None
            value = lane.apply_selection(request.preview)
        elif request.phase == "clear":
            value = lane.apply_selection(lane.preview_selection(None))
        else:
            value = prepare_calibrated_current(lane, budget, cancelled=cancel.is_set)
        check_cancelled(cancel.is_set)
        return _Completion(value=value)
    except Exception as error:
        if new_lane is not None:
            new_lane.close()
        return _Completion(error=str(error))


class LiveCalibrationPresenter(QObject):
    changed = Signal(object)
    _done = Signal(object)

    def __init__(self, owner: CalibrationLiveOwner, registry: ReceiverCalibrationRegistry | None,
                 live_worker, budget: PresentationAllocationBudget) -> None:
        super().__init__()
        self.owner: CalibrationLiveOwner | None = owner
        self.registry, self.worker, self.budget = registry, live_worker, budget
        self.state = LiveCalibrationState(available=registry is not None,
                                          phase="unbound" if registry is not None else "unavailable")
        self._generation = 0
        self._lane: CapturedCalibrationLane | None = None
        self._active: Future | None = None
        self._active_request: _Request | None = None
        self._cancel = Event()
        self._pending: _Request | None = None
        self._closed = False
        self._done.connect(self._finish, Qt.ConnectionType.QueuedConnection)
        live_worker.control_accepted.connect(self.invalidate)
        live_worker.calibration_worker_available.connect(self._dispatch)
        live_worker.render_ready.connect(self.request_current)

    def _set(self, **changes) -> None:
        self.state = replace(self.state, **changes)
        self.changed.emit(self.state)

    def invalidate(self) -> None:
        """GUI acceptance boundary; no waiting and no shared selection Clear."""
        self._generation += 1
        self._pending = None
        self._cancel.set()
        future = self._active
        if future is not None and future.done() and not future.cancelled():
            completion = future.result()  # Already complete, not a Qt wait.
            if isinstance(completion, _Completion) and completion.lane is not None:
                completion.lane.close()
        if self._lane is not None:
            self._lane.close()
        self._lane = None
        self._set(binding=None, preview=None, current=None, displayed=None, error=None,
                  phase="unbound" if self.state.available else "unavailable", busy=False)

    def _detach_current(self) -> None:
        self._generation += 1
        self._pending = None
        self._cancel.set()
        self._set(current=None, displayed=None, error=None)

    def bind(self, frontend: CalibrationFrontendContext) -> None:
        if self._closed or not self.state.available:
            return
        self.invalidate()
        self._offer(_Request(self._generation, "bind", frontend))

    def preview(self, profile: CalibrationProfile | None) -> None:
        if self._closed or self._lane is None or self.state.busy:
            return
        self._offer(_Request(self._generation, "preview", profile=profile))

    def select(self) -> None:
        preview = self.state.preview
        if self._closed or preview is None or self._lane is None or self.state.busy:
            return
        self._detach_current()
        self._set(preview=None)
        self._offer(_Request(self._generation, "select", preview=preview))

    def clear(self) -> None:
        if self._closed or self._lane is None or self.state.busy:
            return
        self._detach_current()
        self._set(preview=None)
        self._offer(_Request(self._generation, "clear"))

    def request_current(self, *_args) -> None:
        if self._closed or self._lane is None or self.state.busy:
            return
        self._offer(_Request(self._generation, "current"))

    def _offer(self, request: _Request) -> None:
        self._pending = request  # Array-free request ONLY; capture at dispatch.
        if request.phase != "current":
            self._set(busy=True, phase=request.phase, error=None)
        self._dispatch()

    @Slot()
    def _dispatch(self) -> None:
        if self._closed or self._active is not None or self._pending is None:
            return
        if not self.worker.calibration_worker_ready:
            return  # Existing control/preparation/projection has priority.
        request = self._pending
        self._pending = None
        if request.generation != self._generation:
            return
        self._cancel = Event()
        assert self.registry is not None
        assert self.owner is not None
        # Freeze dependencies now; never capture self in a retained Future task.
        lane, owner, registry, budget, cancel = self._lane, self.owner, self.registry, self.budget, self._cancel
        def operation():
            return _run(request, lane, owner, registry, budget, cancel)
        try:
            future = self.worker.submit_calibration_task(operation)
        except Exception as error:
            self._set(busy=False, phase="refused", error=str(error))
            return
        self._active, self._active_request = future, request
        presenter_ref = ref(self)
        generation = request.generation

        def complete(done: Future) -> None:
            presenter = presenter_ref()
            if presenter is not None and not presenter._closed:
                presenter._done.emit(generation)
            else:
                completion = done.result() if not done.cancelled() else None
                if isinstance(completion, _Completion) and completion.lane is not None:
                    completion.lane.close()
        future.add_done_callback(complete)

    @Slot(object)
    def _finish(self, generation: int) -> None:
        future = self._active
        if future is None or self._active_request is None or self._active_request.generation != generation:
            return
        request = self._active_request
        result = None if future.cancelled() else future.result()  # completed, never GUI wait
        try:
            if self._closed or request.generation != self._generation:
                if isinstance(result, _Completion) and result.lane is not None:
                    result.lane.close()
            elif isinstance(result, _Completion):
                if result.error is not None:
                    self._set(busy=False, phase="refused", current=None, displayed=None, error=result.error)
                elif request.phase == "bind":
                    self._lane = result.lane
                    self._set(busy=False, phase="bound", binding=result.binding)
                    self.request_current()
                elif request.phase == "preview":
                    self._set(busy=False, phase="bound", preview=result.value, error=None)
                elif request.phase in ("select", "clear"):
                    # None is the actual successful Clear acknowledgement, not failure.
                    self._set(busy=False, phase="bound", acknowledged=request.phase, error=None)
                    self.request_current()
                elif isinstance(result.value, PreparedCalibratedCurrent):
                    lane = self._lane
                    if lane is not None and lane.admit_delivery(result.value.frame.publication):
                        self._set(current=result.value, phase="bound", error=None)
                    else:
                        self._set(current=None, displayed=None, phase="refused")
        finally:
            # Reentrant GUI observers can offer only the single latest request
            # until ALL publication/command acknowledgement callbacks return.
            if self._active is future:
                self._active = self._active_request = None
        self._dispatch()

    def is_valid(self, current: PreparedCalibratedCurrent) -> bool:
        return (not self._closed and self._lane is not None
                and self._lane.is_valid(current.frame.publication))

    def displayed(self, current: PreparedCalibratedCurrent) -> None:
        if self.is_valid(current) and self.state.displayed is not current:
            self._set(displayed=current)

    def display_invalidated(self) -> None:
        current = self.state.current
        if current is not None and not self.is_valid(current):
            self._set(current=None, displayed=None, phase="refused")
        elif self.state.displayed is not None:
            self._set(displayed=None)

    def dispose(self) -> None:
        if self._closed:
            return
        self.invalidate()
        self._closed = True
        self.worker.control_accepted.disconnect(self.invalidate)
        self.worker.calibration_worker_available.disconnect(self._dispatch)
        self.worker.render_ready.disconnect(self.request_current)
        self._active = self._active_request = None
        self.owner = self.registry = self.worker = None
