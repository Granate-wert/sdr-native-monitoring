"""One presentation session for RTBW and Sweep; owns no receiver or worker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest

from ..state.live_view_state import LiveAction, LiveViewState
from ..state.prepared_sweep import PreparedSweepSnapshot
from .live_view_model import LiveViewModel, _SignalPort


class AnalyzerMode(StrEnum):
    RTBW = "rtbw"
    SWEEP = "sweep"


class SweepPresentationPort(Protocol):
    snapshot_ready: _SignalPort
    task_failed: _SignalPort
    running_changed: _SignalPort
    starting_changed: _SignalPort
    stopping_changed: _SignalPort

    def start(self, request: ContinuousSweepPlanRequest) -> None: ...
    def stop(self) -> None: ...
    def can_close(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class AnalyzerViewState:
    mode: AnalyzerMode
    live: LiveViewState
    bundle: AnalyzerFrameBundle | None
    starting: bool
    stopping: bool
    running: bool
    stop_required: bool
    error: str | None
    configuration_pending: bool = False
    sweep_snapshot: ContinuousSweepDisplaySnapshot | None = None
    prepared_sweep: PreparedSweepSnapshot | None = None

    @property
    def controls_locked(self) -> bool:
        return (self.live.busy or self.starting or self.stopping or self.running
                or self.stop_required or self.configuration_pending)


class AnalyzerViewModel:
    """Routes explicit commands to the APP-01 shared application owner.

    Mode selection is local intent, not a retune. All source/configuration
    commands are barred while either mode owns (or still must release) RX.
    Widgets subscribe to this object; rebuilding text must not recreate it.
    """

    def __init__(self, live: LiveViewModel, sweep: SweepPresentationPort) -> None:
        self.live = live
        self._sweep = sweep
        self._mode = AnalyzerMode.RTBW
        self._starting = self._stopping = self._running = False
        self._configuration_pending = False
        self._error: str | None = None
        self._bundle: AnalyzerFrameBundle | None = None
        self._sweep_snapshot: ContinuousSweepDisplaySnapshot | None = None
        self._prepared_sweep: PreparedSweepSnapshot | None = None
        self._expects_prepared = getattr(sweep, "prepares_snapshots", False) is True
        self._listeners: list[Callable[[AnalyzerViewState], None]] = []
        self._publishing = False
        self._publication_pending = False
        self._disposed = False
        self._live_identity: tuple[object, ...] | None = None
        self._connections = (
            (getattr(sweep, "prepared_snapshot_ready") if self._expects_prepared else sweep.snapshot_ready,
             self._on_sweep_snapshot),
            (sweep.task_failed, self._on_error),
            (sweep.starting_changed, self._on_starting),
            (sweep.stopping_changed, self._on_stopping),
            (sweep.running_changed, self._on_running),
        )
        for signal, callback in self._connections:
            signal.connect(callback)
        self._unsubscribe_live = live.subscribe(self._on_live)

    @property
    def state(self) -> AnalyzerViewState:
        live = self.live.state
        rtbw_running = live.primary_action is LiveAction.STOP
        return AnalyzerViewState(
            self._mode, live, self._bundle, self._starting, self._stopping,
            self._running or rtbw_running,
            not self._sweep.can_close() and not (self._running or self._starting or self._stopping),
            self._error or live.error_label,
            self._configuration_pending,
            self._sweep_snapshot,
            self._prepared_sweep,
        )

    def subscribe(self, callback: Callable[[AnalyzerViewState], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        callback(self.state)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def select_mode(self, mode: AnalyzerMode) -> bool:
        mode = AnalyzerMode(mode)
        if self._disposed or self.state.controls_locked:
            return False
        if mode is not self._mode:
            self._mode = mode
            self._bundle = None  # Never label a prior-mode frame as current.
            self._sweep_snapshot = None
            self._prepared_sweep = None
            self._error = None
            self._publish()
        return True

    def start(self, request: ContinuousSweepPlanRequest | None = None) -> bool:
        state = self.state
        if (self._disposed or state.controls_locked or
                state.live.primary_action is not LiveAction.START or
                not state.live.primary_action_enabled or
                not state.live.has_applied_configuration):
            return False
        self._error = None
        if self._mode is AnalyzerMode.RTBW:
            return self.live.execute_primary_action()
        if not isinstance(request, ContinuousSweepPlanRequest):
            return False
        # Latch before dispatch: reentrant callbacks cannot change the mode.
        self._starting = True
        self._publish()
        try:
            self._sweep.start(request)
        except Exception as error:
            self._starting = False
            self._on_error(str(error))
            return False
        return True

    def stop(self) -> bool:
        state = self.state
        if self._disposed or state.starting or state.stopping or state.live.busy:
            return False
        if self._mode is AnalyzerMode.SWEEP and (self._running or state.stop_required):
            self._sweep.stop()
            return True
        if state.live.primary_action is LiveAction.STOP:
            return self.live.execute_primary_action()
        return False

    def discover_devices(self) -> bool:
        return not self._disposed and not self.state.controls_locked and self.live.discover_devices()

    def select_device(self, identifier: str) -> bool:
        return not self._disposed and not self.state.controls_locked and self.live.select_device(identifier)

    def select_manual_uri(self, uri: str) -> bool:
        return not self._disposed and not self.state.controls_locked and self.live.select_manual_uri(uri)

    def apply_configuration(self, configuration: object) -> bool:
        if self._disposed or self.state.controls_locked:
            return False
        self._configuration_pending = True
        self._publish()
        try:
            accepted = self.live.apply_configuration(configuration)
        except Exception:
            self.resolve_configuration_request()
            raise
        if not accepted:
            self.resolve_configuration_request()
        return accepted

    def resolve_configuration_request(self) -> None:
        """Release presentation admission after the drawer resolves readback/error."""
        if self._configuration_pending:
            self._configuration_pending = False
            self._publish()

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._unsubscribe_live()
        for signal, callback in self._connections:
            signal.disconnect(callback)
        self._listeners.clear()

    def release_presentation_after_shutdown(self) -> None:
        """No notification/repaint: the composition's terminal close owns this."""
        if not self._disposed:
            raise RuntimeError("Analyzer presentation must be disconnected before terminal release")
        self._bundle = None
        self._sweep_snapshot = None
        self._prepared_sweep = None

    def _on_live(self, state: LiveViewState) -> None:
        if self._disposed:
            return
        snapshot = state.snapshot
        identity = (getattr(snapshot, "session_id", None), getattr(snapshot, "generation", None),
                    getattr(getattr(snapshot, "device", None), "device_id", None))
        if self._live_identity is not None and identity != self._live_identity:
            self._bundle = None
            self._sweep_snapshot = None
            self._prepared_sweep = None
        self._live_identity = identity
        if self._mode is AnalyzerMode.RTBW:
            self._bundle = state.analyzer_bundle
        self._publish()

    def _on_sweep_snapshot(self, value: object) -> None:
        if self._disposed:
            return
        if self._mode is not AnalyzerMode.SWEEP or not (self._running or self._stopping):
            return
        prepared = value if isinstance(value, PreparedSweepSnapshot) else None
        if prepared is not None:
            value = prepared.snapshot
        elif self._expects_prepared:
            self._bundle = None
            self._sweep_snapshot = None
            self._prepared_sweep = None
            self._on_error("Missing prepared Sweep presentation")
            return
        if not isinstance(value, ContinuousSweepDisplaySnapshot):
            self._bundle = None
            self._sweep_snapshot = None
            self._prepared_sweep = None
            self._on_error("Invalid Sweep Analyzer bundle")
            return
        bundle = prepared.analyzer_bundle if prepared is not None else value.analyzer_bundle
        if bundle is None and value.presentation_omission is None:
            return  # Counters alone must not erase the last measurement.
        self._bundle = bundle
        self._sweep_snapshot = value
        self._prepared_sweep = prepared
        self._publish()

    def _on_error(self, error: str) -> None:
        if self._disposed:
            return
        if self._mode is not AnalyzerMode.SWEEP:
            return  # A late other-strategy callback cannot relabel RTBW.
        self._error = str(error)
        self._publish()

    def _on_starting(self, value: bool) -> None:
        if self._disposed:
            return
        self._starting = bool(value)
        if value and self._mode is AnalyzerMode.SWEEP:
            # A new explicit acquisition has a new presentation history even
            # when a producer reuses its epoch/sequence scalars. Do not invent
            # replacement provenance; clear the old display at Start instead.
            self._bundle = None
            self._sweep_snapshot = None
            self._prepared_sweep = None
        self._publish()

    def _on_stopping(self, value: bool) -> None:
        if self._disposed:
            return
        self._stopping = bool(value)
        self._publish()

    def _on_running(self, value: bool) -> None:
        if self._disposed:
            return
        self._running = bool(value)
        if value:
            self._sweep_snapshot = None
            self._prepared_sweep = None
        if value and self._mode is not AnalyzerMode.SWEEP:
            # A successful externally issued command is applied state, unlike
            # a rejected/preflight-only Start. Reflect its actual strategy.
            self._mode = AnalyzerMode.SWEEP
            self._bundle = None
        self._publish()

    def _publish(self) -> None:
        if self._disposed:
            return
        self._publication_pending = True
        if self._publishing:
            return
        self._publishing = True
        try:
            while self._publication_pending and not self._disposed:
                self._publication_pending = False
                state = self.state
                for callback in tuple(self._listeners):
                    callback(state)
                    if self._publication_pending or self._disposed:
                        # A readback consumer may resolve Apply synchronously.
                        # Do not send the superseded pending state to later
                        # consumers after they have already seen confirmation.
                        break
        finally:
            self._publishing = False
