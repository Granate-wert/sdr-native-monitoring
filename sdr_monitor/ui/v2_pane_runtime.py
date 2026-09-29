"""Explicit, per-resource APP-07 RX pump for the independent V2 Analyzer.

This object does not discover, select, apply or start a device in its
constructor. A caller must first preview/apply one PaneResourceSession and
then explicitly request each resource Start. One worker per independent
resource serializes its own Start/poll/retune/Stop; there is no per-FFT Qt
signal or process-global SDK arbiter. The worker prepares publications and
offers them to the bounded GUI handoff, never touching a QWidget.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, replace
from enum import StrEnum
import math
from threading import Condition, Thread
from time import monotonic

from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneLayout, ResourcePaneSchedule
from sdr_monitor.services.pane_resource_session import PaneActivation, PaneResourceSession

from .v2_pane_delivery_queue import PaneFairDeliveryQueue
from .v2_pane_presentation import PaneDeliveryPreparer


class PanePumpPhase(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOP_REQUIRED = "stop_required"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class PanePumpResourceState:
    physical_stream_resource_id: str
    phase: PanePumpPhase = PanePumpPhase.IDLE
    activation: PaneActivation | None = None
    prepared_publications: int = 0
    planned_slot_overrun: bool = False
    error: str | None = None


class _ResourceWorker:
    def __init__(self, resource: ResourcePaneSchedule, pane_ids: tuple[str, ...],
                 session: PaneResourceSession, preparer: PaneDeliveryPreparer,
                 queue: PaneFairDeliveryQueue, poll_interval_s: float) -> None:
        self.resource = resource
        self.pane_ids = pane_ids
        self.session = session
        self.preparer = preparer
        self.queue = queue
        self.poll_interval_s = poll_interval_s
        self._condition = Condition()
        self._pending_start: Future[PaneActivation] | None = None
        self._pending_stop: Future[None] | None = None
        self._active_stop: Future[None] | None = None
        self._state = PanePumpResourceState(resource.physical_stream_resource_id)
        self._frame_seen = False
        self._terminal_seen = False
        self._slot_deadline_s: float | None = None
        self._thread = Thread(target=self._run, name=f"pane-rx-{resource.physical_stream_resource_id}")
        self._launched = False

    def launch(self) -> None:
        self._thread.start()
        self._launched = True

    def snapshot(self) -> PanePumpResourceState:
        with self._condition:
            return self._state

    def request_start(self) -> Future[PaneActivation]:
        with self._condition:
            if (self._state.phase is not PanePumpPhase.IDLE
                    or self._pending_start is not None or self._pending_stop is not None):
                raise RuntimeError("pane resource is not idle for explicit Start")
            future: Future[PaneActivation] = Future()
            # Futures report completion; cancelling one must never detach an
            # accepted hardware command and strand its resource worker.
            future.set_running_or_notify_cancel()
            self._pending_start = future
            self._condition.notify()
            return future

    def request_stop(self) -> Future[None]:
        if not self._launched:
            # Activation may have failed after session.apply reserved this
            # lease but before its worker thread started. Apply is off Qt;
            # release that never-started resource on the same control worker.
            unlaunched_future: Future[None] = Future()
            unlaunched_future.set_running_or_notify_cancel()
            try:
                self.session.stop_resource(self.resource.physical_stream_resource_id)
            except Exception:
                with self._condition:
                    self._state = replace(self._state, phase=PanePumpPhase.STOP_REQUIRED,
                                          error="Receiver lease release failed; explicit Stop required")
                unlaunched_future.set_exception(RuntimeError("unlaunched pane receiver did not release"))
            else:
                with self._condition:
                    self._state = replace(self._state, phase=PanePumpPhase.STOPPED, error=None)
                unlaunched_future.set_result(None)
            return unlaunched_future
        with self._condition:
            if self._state.phase is PanePumpPhase.STOPPED:
                done: Future[None] = Future()
                done.set_result(None)
                return done
            if self._pending_stop is not None:
                return self._pending_stop
            if self._active_stop is not None:
                return self._active_stop
            future: Future[None] = Future()
            future.set_running_or_notify_cancel()
            self._pending_stop = future
            self._condition.notify()
            return future

    def join_after_stop(self, timeout_s: float | None) -> None:
        if self.snapshot().phase is not PanePumpPhase.STOPPED:
            raise RuntimeError("pane resource has not confirmed explicit Stop")
        if not self._launched:
            return
        self._thread.join(timeout_s)
        if self._thread.is_alive():
            raise TimeoutError("pane resource worker has not joined")

    def _run(self) -> None:
        while True:
            with self._condition:
                if self._pending_stop is not None:
                    command = "stop"
                    future_stop = self._pending_stop
                    self._pending_stop = None
                    self._active_stop = future_stop
                    # A Stop accepted before the worker begins Start must not
                    # open the device just to stop it again.
                    if self._pending_start is not None:
                        self._pending_start.set_exception(RuntimeError("pane Start cancelled before RX"))
                        self._pending_start = None
                    self._state = replace(self._state, phase=PanePumpPhase.STOPPING)
                elif self._pending_start is not None:
                    command = "start"
                    future_start = self._pending_start
                    self._pending_start = None
                    self._state = replace(self._state, phase=PanePumpPhase.STARTING)
                elif self._state.phase is PanePumpPhase.RUNNING:
                    self._condition.wait(self.poll_interval_s)
                    if self._pending_stop is not None:
                        continue
                    command = "poll"
                elif self._state.phase is PanePumpPhase.STOPPED:
                    return
                else:
                    self._condition.wait()
                    continue
            if command == "start":
                try:
                    activation = self.session.start_resource(self.resource.physical_stream_resource_id)
                except Exception:
                    with self._condition:
                        self._state = replace(self._state, phase=PanePumpPhase.STOP_REQUIRED,
                                              error="Receiver Start failed; explicit Stop required")
                    future_start.set_exception(RuntimeError("pane receiver Start did not confirm"))
                else:
                    self._activate(activation)
                    future_start.set_result(activation)
            elif command == "stop":
                try:
                    self.session.stop_resource(self.resource.physical_stream_resource_id)
                except Exception:
                    with self._condition:
                        self._state = replace(self._state, phase=PanePumpPhase.STOP_REQUIRED,
                                              activation=None, planned_slot_overrun=False,
                                              error="Receiver Stop failed; owner retained for explicit retry")
                        self._active_stop = None
                    future_stop.set_exception(RuntimeError("pane receiver Stop did not confirm"))
                else:
                    for pane_id in self.pane_ids:
                        self.queue.clear(pane_id)
                    with self._condition:
                        self._state = replace(self._state, phase=PanePumpPhase.STOPPED,
                                              activation=None, planned_slot_overrun=False, error=None)
                        self._active_stop = None
                    future_stop.set_result(None)
            else:
                try:
                    self._poll_and_advance()
                except Exception:
                    with self._condition:
                        self._state = replace(self._state, phase=PanePumpPhase.STOP_REQUIRED,
                                              activation=None, planned_slot_overrun=False,
                                              error="Receiver or pane publication failed; explicit Stop required")

    def _activate(self, activation: PaneActivation) -> None:
        slot = self.resource.slots[activation.slot_index]
        self._frame_seen = False
        self._terminal_seen = False
        self._slot_deadline_s = monotonic() + slot.active_duration_s
        with self._condition:
            self._state = replace(self._state, phase=PanePumpPhase.RUNNING,
                                  activation=activation, planned_slot_overrun=False, error=None)

    def _poll_and_advance(self) -> None:
        resource_id = self.resource.physical_stream_resource_id
        deliveries = self.session.poll_resource(resource_id)
        prepared_count = 0
        activation = self.snapshot().activation
        for delivery in deliveries:
            if activation is not None and delivery.capture_id == activation.capture_id:
                self._frame_seen = True
                if delivery.bundle.terminal_sweep:
                    self._terminal_seen = True
            self.queue.offer(self.preparer.prepare(delivery))
            prepared_count += 1
        if prepared_count:
            with self._condition:
                self._state = replace(self._state,
                                      prepared_publications=self._state.prepared_publications + prepared_count)
        if len(self.resource.slots) <= 1 or activation is None or self._slot_deadline_s is None:
            return
        if monotonic() < self._slot_deadline_s:
            return
        job = next(item for item in self.resource.jobs if item.capture_id == activation.capture_id)
        if (not self._frame_seen
                or job.profile.measurement_mode is not CaptureMeasurementMode.RTBW and not self._terminal_seen):
            with self._condition:
                if not self._state.planned_slot_overrun:
                    self._state = replace(self._state, planned_slot_overrun=True)
            return
        with self._condition:
            if self._pending_stop is not None:
                return  # Explicit Stop outranks a planned retune boundary.
        self._activate(self.session.advance_resource(resource_id))


class PaneResourcePump:
    """One serial worker per applied independent resource; no hidden Start."""

    def __init__(self, session: PaneResourceSession, layout: PaneLayout,
                 preparer: PaneDeliveryPreparer, queue: PaneFairDeliveryQueue, *,
                 poll_interval_s: float = 0.01) -> None:
        if (not isinstance(session, PaneResourceSession) or not isinstance(layout, PaneLayout)
                or layout.schedule is None or session.schedule is not layout.schedule
                or preparer.layout is not layout
                or not isinstance(queue, PaneFairDeliveryQueue)
                or isinstance(poll_interval_s, bool)
                or not isinstance(poll_interval_s, (int, float))
                or not math.isfinite(poll_interval_s)
                or not 0.004 <= poll_interval_s <= 1.0):
            raise ValueError("pane pump requires one exact compiled and prepared resource plan")
        pane_ids = tuple(slot.request.pane_id for slot in layout.slots if slot.request is not None)
        if queue.pane_ids != pane_ids:
            raise ValueError("pane pump queue differs from occupied layout order")
        resources = layout.schedule.resources
        self._session = session
        self._workers = {
            resource.physical_stream_resource_id: _ResourceWorker(
                resource, tuple(item.pane_id for item in layout.schedule.pane_revisits
                                if item.physical_stream_resource_id == resource.physical_stream_resource_id),
                session, preparer, queue, poll_interval_s)
            for resource in resources
        }
        self._pane_resources = {
            item.pane_id: item.physical_stream_resource_id
            for item in layout.schedule.pane_revisits
        }
        self._started = False

    @property
    def activated(self) -> bool:
        return self._started

    def activate(self) -> None:
        """Spawn workers only after external preview/apply reserved all leases."""
        if (self._started or self._session.retained_resource_count != len(self._workers)
                or self._session.active_resource_count != 0):
            raise RuntimeError("pane pump needs an applied session with all resource leases")
        self._started = True
        try:
            for worker in self._workers.values():
                worker.launch()
        except Exception:
            # No explicit Start has been exposed yet. Roll back all reserved
            # leases, including workers whose Thread.start failed, before
            # returning control to the caller. A failed Stop remains retained
            # and can be retried through stop_all; it is never silently freed.
            futures = tuple(worker.request_stop() for worker in self._workers.values())
            failures = False
            for future in futures:
                try:
                    future.result(timeout=5.0)
                except Exception:
                    failures = True
            if not failures:
                try:
                    self.join_after_stop(5.0)
                except Exception:
                    failures = True
            if failures:
                raise RuntimeError("pane worker activation failed; explicit Stop is required") from None
            raise RuntimeError("pane worker activation failed; all leases released") from None

    def snapshot(self) -> tuple[PanePumpResourceState, ...]:
        return tuple(worker.snapshot() for worker in self._workers.values())

    def start_resource(self, resource_id: str) -> Future[PaneActivation]:
        return self._worker(resource_id).request_start()

    def stop_resource(self, resource_id: str) -> Future[None]:
        return self._worker(resource_id).request_stop()

    def stop_selected(self, pane_id: str, *, acknowledge_shared: bool = False) -> tuple[tuple[str, ...], Future[None]]:
        impact = self._session.stop_impact(pane_id)
        if len(impact) > 1 and not acknowledge_shared:
            raise RuntimeError("selected pane shares RX; confirm stopping affected panes")
        return impact, self.stop_resource(self._pane_resources[pane_id])

    def stop_all(self) -> dict[str, Future[None]]:
        self._require_started()
        return {resource_id: worker.request_stop() for resource_id, worker in self._workers.items()}

    def join_after_stop(self, timeout_s: float | None = None) -> None:
        self._require_started()
        for worker in self._workers.values():
            worker.join_after_stop(timeout_s)

    def _worker(self, resource_id: str) -> _ResourceWorker:
        self._require_started()
        try:
            return self._workers[resource_id]
        except KeyError:
            raise ValueError("unknown physical pane resource") from None

    def _require_started(self) -> bool:
        if not self._started:
            raise RuntimeError("pane pump has not been activated")
        return True


__all__ = ["PanePumpPhase", "PanePumpResourceState", "PaneResourcePump"]
