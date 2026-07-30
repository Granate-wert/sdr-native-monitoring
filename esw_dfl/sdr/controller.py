"""Bounded live SDR session controller for the PySide6 integration.

The controller is deliberately Qt-free.  Native lifecycle and polling run on
one worker thread; the GUI only drains a small latest-wins update queue from a
timer.  No raw I/Q data crosses this boundary and no Python callback is made
for an individual FFT.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
import threading
import time
from typing import Any, Callable, Protocol

from .contracts import SpectrumFrame
from .fixed_band import FixedBandEngineService, FixedBandEvent, FixedBandMetricsSnapshot, FixedBandOptions


class LiveControllerState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    CLOSED = "closed"


class _LiveService(Protocol):
    def configure(self, options: FixedBandOptions) -> Any: ...
    def start(self) -> None: ...
    def request_stop(self) -> None: ...
    def join(self) -> None: ...
    def disconnect(self) -> None: ...
    def poll_spectrum(self, max_items: int = 0) -> tuple[SpectrumFrame, ...]: ...
    def poll_events(self, max_items: int = 0) -> tuple[FixedBandEvent, ...]: ...
    def metrics(self) -> FixedBandMetricsSnapshot: ...


@dataclass(frozen=True, slots=True)
class LiveSessionConfig:
    """Low-rate identity and fixed-band options for one live session."""

    source_id: str
    display_name: str
    uri: str
    options: FixedBandOptions

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id must not be empty")
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")
        if not self.uri.strip():
            raise ValueError("uri must not be empty")
        if not isinstance(self.options, FixedBandOptions):
            raise TypeError("options must be FixedBandOptions")


@dataclass(frozen=True, slots=True)
class LiveControllerUpdate:
    """A bounded, coarse-grained publication consumed by the GUI timer."""

    generation: int
    state: LiveControllerState
    spectrum_frames: tuple[SpectrumFrame, ...] = ()
    events: tuple[FixedBandEvent, ...] = ()
    metrics: FixedBandMetricsSnapshot | None = None
    applied_config: Any | None = None
    persistence_snapshots: tuple[Any, ...] = ()
    error: str | None = None
    emitted_at: float = 0.0


ServiceFactory = Callable[[str], _LiveService]


class LiveSdrController:
    """Own one fixed-band service and its bounded UI publication queue.

    ``start`` is non-blocking.  The worker performs configure/start/poll/stop
    and all service calls, keeping device access away from the GUI thread.
    ``poll_latest`` is intended for a QTimer and never returns more than one
    update, so a slow UI cannot create an unbounded backlog.
    """

    def __init__(
        self,
        config: LiveSessionConfig,
        *,
        service_factory: ServiceFactory = FixedBandEngineService,
        poll_interval_s: float = 0.05,
        spectrum_batch_size: int = 8,
        event_batch_size: int = 16,
        update_queue_capacity: int = 4,
    ) -> None:
        if poll_interval_s <= 0.0:
            raise ValueError("poll_interval_s must be positive")
        if spectrum_batch_size <= 0 or event_batch_size <= 0:
            raise ValueError("batch sizes must be positive")
        if update_queue_capacity <= 0:
            raise ValueError("update_queue_capacity must be positive")
        self.config = config
        self._service_factory = service_factory
        self._poll_interval_s = float(poll_interval_s)
        self._spectrum_batch_size = int(spectrum_batch_size)
        self._event_batch_size = int(event_batch_size)
        self._updates: deque[LiveControllerUpdate] = deque(maxlen=int(update_queue_capacity))
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._service: _LiveService | None = None
        self._state = LiveControllerState.CREATED
        self._generation = 0
        self._applied_config: Any | None = None
        self._last_error: str | None = None
        self._closed = False

    @property
    def state(self) -> LiveControllerState:
        with self._condition:
            return self._state

    @property
    def generation(self) -> int:
        with self._condition:
            return self._generation

    @property
    def applied_config(self) -> Any | None:
        with self._condition:
            return self._applied_config

    @property
    def last_error(self) -> str | None:
        with self._condition:
            return self._last_error

    def start(self) -> int:
        with self._condition:
            if self._closed:
                raise RuntimeError("live controller is closed")
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("live controller is already running")
            if self._state not in (LiveControllerState.CREATED, LiveControllerState.STOPPED):
                raise RuntimeError(f"cannot start controller from {self._state}")
            self._generation += 1
            generation = self._generation
            self._state = LiveControllerState.STARTING
            self._last_error = None
            self._applied_config = None
            self._stop.clear()
            worker = threading.Thread(
                target=self._run,
                args=(generation,),
                name=f"live-sdr-{self.config.source_id}",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return generation

    def request_stop(self) -> None:
        """Request shutdown without making a native call on the GUI thread."""

        self._stop.set()
        with self._condition:
            if self._state in (LiveControllerState.STARTING, LiveControllerState.RUNNING):
                self._state = LiveControllerState.STOPPING
                self._publish_locked(LiveControllerUpdate(
                    generation=self._generation,
                    state=LiveControllerState.STOPPING,
                    applied_config=self._applied_config,
                    emitted_at=time.monotonic(),
                ))
            self._condition.notify_all()

    def close(self, *, wait: bool = True, timeout_s: float = 2.0) -> None:
        self.request_stop()
        worker = self._worker
        if wait and worker is not None and worker is not threading.current_thread():
            worker.join(max(0.0, float(timeout_s)))
        with self._condition:
            self._closed = True
            if self._state in (LiveControllerState.CREATED, LiveControllerState.STOPPED):
                self._state = LiveControllerState.CLOSED
            self._condition.notify_all()

    def poll_updates(self, max_items: int = 0) -> tuple[LiveControllerUpdate, ...]:
        if max_items < 0:
            raise ValueError("max_items must not be negative")
        with self._condition:
            if max_items == 0:
                max_items = len(self._updates)
            result = tuple(self._updates.popleft() for _ in range(min(max_items, len(self._updates))))
            return result

    def poll_latest(self) -> LiveControllerUpdate | None:
        with self._condition:
            if not self._updates:
                return None
            latest = self._updates[-1]
            self._updates.clear()
            return latest

    def wait_for_state(self, state: LiveControllerState, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        with self._condition:
            while self._state is not state:
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
            return True

    def _set_state(self, state: LiveControllerState) -> None:
        with self._condition:
            self._state = state
            self._condition.notify_all()

    def _publish_locked(self, update: LiveControllerUpdate) -> None:
        self._updates.append(update)
        self._condition.notify_all()

    def _publish(self, update: LiveControllerUpdate) -> None:
        with self._condition:
            self._publish_locked(update)

    def _run(self, generation: int) -> None:
        service: _LiveService | None = None
        error: str | None = None
        failed = False
        try:
            service = self._service_factory(self.config.uri)
            with self._condition:
                self._service = service
            applied = service.configure(self.config.options)
            with self._condition:
                self._applied_config = applied
                self._state = LiveControllerState.RUNNING
                self._publish_locked(LiveControllerUpdate(
                    generation=generation,
                    state=LiveControllerState.RUNNING,
                    applied_config=applied,
                    emitted_at=time.monotonic(),
                ))
            service.start()
            next_metrics = time.monotonic()
            while not self._stop.wait(self._poll_interval_s):
                frames = tuple(service.poll_spectrum(self._spectrum_batch_size))
                events = tuple(service.poll_events(self._event_batch_size))
                persistence = tuple(
                    service.poll_persistence(self._spectrum_batch_size)
                ) if hasattr(service, "poll_persistence") else ()
                now = time.monotonic()
                metrics = None
                if frames or events or persistence or now >= next_metrics:
                    metrics = service.metrics()
                    next_metrics = now + max(0.1, self._poll_interval_s * 4.0)
                if frames or events or persistence or metrics is not None:
                    self._publish(LiveControllerUpdate(
                        generation=generation,
                        state=LiveControllerState.RUNNING,
                        spectrum_frames=frames,
                        events=events,
                        metrics=metrics,
                        applied_config=self._applied_config,
                        persistence_snapshots=persistence,
                        emitted_at=now,
                    ))
        except Exception as exc:  # native/device errors become diagnostics, not GUI crashes
            failed = True
            error = f"{type(exc).__name__}: {exc}"
            with self._condition:
                self._last_error = error
                self._state = LiveControllerState.ERROR
                self._publish_locked(LiveControllerUpdate(
                    generation=generation,
                    state=LiveControllerState.ERROR,
                    applied_config=self._applied_config,
                    error=error,
                    emitted_at=time.monotonic(),
                ))
        finally:
            if service is not None:
                try:
                    service.request_stop()
                    service.join()
                except Exception as exc:
                    if error is None:
                        error = f"shutdown {type(exc).__name__}: {exc}"
                finally:
                    try:
                        service.disconnect()
                    except Exception as exc:
                        if error is None:
                            error = f"disconnect {type(exc).__name__}: {exc}"
            with self._condition:
                self._service = None
                if self._closed:
                    final = LiveControllerState.CLOSED
                elif failed or error is not None:
                    final = LiveControllerState.ERROR
                else:
                    final = LiveControllerState.STOPPED
                self._state = final
                self._publish_locked(LiveControllerUpdate(
                    generation=generation,
                    state=final,
                    applied_config=self._applied_config,
                    error=error,
                    emitted_at=time.monotonic(),
                ))


__all__ = [
    "LiveControllerState",
    "LiveControllerUpdate",
    "LiveSdrController",
    "LiveSessionConfig",
]
