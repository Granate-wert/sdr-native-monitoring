"""Shared strategy admission over existing Live and continuous-Sweep owners.

Calls may block on their existing ports and must run on a presenter worker.
This controller never constructs a device, acquires a lease or restarts Live.
"""
from dataclasses import dataclass, replace
from enum import StrEnum
import threading
from typing import Protocol
from collections.abc import Callable
from contextlib import contextmanager
from collections.abc import Iterator

from ..domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..domain.live import LiveSnapshot, LiveSessionState, LiveAdmissionRejected


class AnalyzerMode(StrEnum):
    RTBW = "rtbw"
    SWEEP = "sweep"


class AnalyzerPhase(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AnalyzerSessionState:
    mode: AnalyzerMode = AnalyzerMode.RTBW
    phase: AnalyzerPhase = AnalyzerPhase.IDLE
    error: str | None = None
    operation_id: int = 0
    # Application-assigned continuous-Sweep acquisition epoch, NOT RTBW's
    # backend epoch or a bounded-tool epoch. Retained after Stop for provenance.
    sweep_epoch: int | None = None


class AnalyzerLivePort(Protocol):
    def start(self) -> LiveSnapshot: ...
    def stop(self) -> LiveSnapshot: ...
    def is_running(self) -> bool: ...


class AnalyzerSweepPort(Protocol):
    def start(self, request: ContinuousSweepPlanRequest) -> None: ...
    def stop(self) -> None: ...


class AnalyzerLiveRejected(RuntimeError):
    """Keep the producer's exact error category and immutable snapshot."""

    def __init__(self, snapshot: LiveSnapshot, fallback: str) -> None:
        super().__init__(snapshot.error or fallback)
        self.snapshot = snapshot


class AnalyzerSessionApplicationService:
    def __init__(self, live: AnalyzerLivePort, sweep: AnalyzerSweepPort, *,
                 start_live: Callable[[], LiveSnapshot] | None = None) -> None:
        self._live = live
        self._sweep = sweep
        # Production must inject atomic backend admission, not a check-then-act
        # wrapper. The default keeps existing isolated fake-port callers usable.
        self._start_live = start_live if start_live is not None else live.start
        self._lock = threading.Lock()
        self._state = AnalyzerSessionState()
        self._start_dispatched = False
        self._bounded_sweep_active = False
        self._next_operation_id = 0
        self._last_sweep_epoch = -1

    @contextmanager
    def bounded_sweep_operation(self) -> Iterator[None]:
        """Serialize the retained plan/result tool with Analyzer strategies.

        The bounded tool owns its own cancel/join inside this scope; controller
        Stop must never dispatch to the unrelated continuous-Sweep port.
        Native admission remains the final authority after a tool error.
        """
        with self._lock:
            if self._state.phase is not AnalyzerPhase.IDLE:
                raise RuntimeError("Stop the current Analyzer before running the Sweep tool")
            self._bounded_sweep_active = True
            self._next_operation_id += 1
            self._state = AnalyzerSessionState(mode=AnalyzerMode.SWEEP, phase=AnalyzerPhase.RUNNING,
                                               operation_id=self._next_operation_id)
        try:
            yield
        finally:
            with self._lock:
                self._bounded_sweep_active = False
                self._state = replace(self._state, phase=AnalyzerPhase.IDLE)

    @property
    def state(self) -> AnalyzerSessionState:
        with self._lock:
            return self._state

    @property
    def stop_required(self) -> bool:
        with self._lock:
            return self._start_dispatched

    def select_mode(self, mode: AnalyzerMode) -> AnalyzerSessionState:
        mode = AnalyzerMode(mode)
        with self._lock:
            if self._state.phase is not AnalyzerPhase.IDLE:
                raise RuntimeError("stop the current analyzer strategy before switching mode")
            self._state = replace(self._state, mode=mode)
            return self._state

    def start(self, request: ContinuousSweepPlanRequest | None = None) -> AnalyzerSessionState:
        with self._lock:
            if self._state.phase is not AnalyzerPhase.IDLE:
                raise RuntimeError("analyzer is not idle")
            mode = self._state.mode
            if (mode is AnalyzerMode.SWEEP) != (request is not None):
                raise ValueError("Sweep requires its request; RTBW uses the applied profile")
            sweep_epoch = None
            if request is not None:
                if not isinstance(request, ContinuousSweepPlanRequest):
                    raise TypeError("Sweep requires an immutable plan request")
                if type(request.epoch) is not int or not 0 <= request.epoch <= (1 << 64) - 1:
                    raise ValueError("Sweep epoch must fit an unsigned 64-bit integer")
                sweep_epoch = max(request.epoch, self._last_sweep_epoch + 1)
                if sweep_epoch > (1 << 64) - 1:
                    raise OverflowError("Sweep epoch space exhausted; no new acquisition was dispatched")
                request = replace(request, epoch=sweep_epoch)
                # Never recycle even a rejected/partially started attempt.
                self._last_sweep_epoch = sweep_epoch
            self._next_operation_id += 1
            self._state = replace(self._state, phase=AnalyzerPhase.STARTING, error=None,
                                  operation_id=self._next_operation_id, sweep_epoch=sweep_epoch)
            self._start_dispatched = False
        try:
            if self._live.is_running():
                raise RuntimeError("Live is already running outside this analyzer operation")
            with self._lock:
                self._start_dispatched = True
            if mode is AnalyzerMode.RTBW:
                snapshot = self._start_live()
                if snapshot.error is not None or snapshot.state is not LiveSessionState.RUNNING:
                    raise AnalyzerLiveRejected(snapshot, "Live did not confirm RUNNING")
            else:
                assert request is not None
                self._sweep.start(request)
        except LiveAdmissionRejected as error:
            with self._lock:
                self._start_dispatched = False
            self._fail(error)
            raise
        except Exception as error:
            self._fail(error)
            raise
        with self._lock:
            self._state = replace(self._state, phase=AnalyzerPhase.RUNNING)
            return self._state

    def stop(self) -> AnalyzerSessionState:
        with self._lock:
            if self._bounded_sweep_active:
                raise RuntimeError("Cancel the bounded Sweep tool and wait for its completion")
            if self._state.phase is AnalyzerPhase.IDLE:
                return self._state
            if self._state.phase not in (AnalyzerPhase.RUNNING, AnalyzerPhase.ERROR):
                raise RuntimeError("analyzer lifecycle operation is pending")
            if not self._start_dispatched:
                # Admission failed before touching either strategy. Reset only
                # our error state, never stop a session owned by another caller.
                self._state = replace(self._state, phase=AnalyzerPhase.IDLE, error=None)
                return self._state
            mode = self._state.mode
            self._state = replace(self._state, phase=AnalyzerPhase.STOPPING)
        try:
            if mode is AnalyzerMode.RTBW:
                snapshot = self._live.stop()
                if snapshot.error is not None or self._live.is_running():
                    raise AnalyzerLiveRejected(snapshot, "Live stop was not confirmed")
            else:
                self._sweep.stop()
        except Exception as error:
            self._fail(error)
            raise
        with self._lock:
            self._state = replace(self._state, phase=AnalyzerPhase.IDLE, error=None)
            self._start_dispatched = False
            return self._state

    def _fail(self, error: Exception) -> None:
        with self._lock:
            self._state = replace(self._state, phase=AnalyzerPhase.ERROR, error=str(error))
