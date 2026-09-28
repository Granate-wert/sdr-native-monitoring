"""One presentation session for RTBW and Sweep; owns no receiver or worker."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.analyzer_sources import AnalyzerSourceSelection
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.device_capabilities import AdapterRuntimeAvailability, DeviceFamily
from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest, TinySaSweepRunIdentity
from sdr_monitor.domain.tinysa_correction import tinysa_correction_signature

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

    def start(self, request: ContinuousSweepPlanRequest | TinySaSweepRequest | HackrfSweepRequest) -> None: ...
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
    source_selection: AnalyzerSourceSelection | None = None
    hackrf_sweep_composed: bool = False
    hackrf_sweep_request: HackrfSweepRequest | None = None

    @property
    def ad936x_controls_available(self) -> bool:
        return self.source_selection is None or self.source_selection.ad936x_controls_available

    @property
    def hackrf_controls_available(self) -> bool:
        selection = self.source_selection
        return bool(selection is not None and selection.selected is not None
                    and selection.selected.family is DeviceFamily.HACKRF and not selection.release_pending
                    and getattr(self.live.snapshot, "source_choice", None) is selection.selected
                    and getattr(self.live.snapshot, "selection_revision", None) == selection.revision)

    @property
    def hackrf_sweep_controls_available(self) -> bool:
        return self.hackrf_sweep_composed and self.hackrf_controls_available

    @property
    def rtbw_profile_ready(self) -> bool:
        return ((self.ad936x_controls_available and self.live.has_applied_configuration)
                or (self.hackrf_controls_available and getattr(self.live.snapshot, "hackrf_request", None) is not None))

    @property
    def tinysa_controls_available(self) -> bool:
        selection = self.source_selection
        source = selection.selected if selection is not None else None
        return bool(source is not None and source.family is DeviceFamily.TINYSA
                    and selection is not None and not selection.release_pending
                    and source.binding.snapshot is not None and source.binding.calibration_identity is not None
                    and source.runtime is not None
                    and source.runtime.availability is AdapterRuntimeAvailability.AVAILABLE
                    and source.binding.snapshot.model_id in {"tinysa_basic", "tinysa_ultra"})

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

    def __init__(self, live: LiveViewModel, sweep: SweepPresentationPort, *,
                 hackrf_sweep_composed: bool = False) -> None:
        self.live = live
        self._sweep = sweep
        self._hackrf_sweep_composed = bool(hackrf_sweep_composed)
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
        self._mode_source_choice: object | None = None
        self._mode_source_revision: int | None = None
        self._instrument_request: TinySaSweepRequest | None = None
        self._instrument_epoch: int | None = None
        self._instrument_sequence: int | None = None
        self._hackrf_sweep_request: HackrfSweepRequest | None = None
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
        selection = getattr(self.live, "source_selection", None)
        rtbw_running = live.primary_action is LiveAction.STOP
        return AnalyzerViewState(
            self._mode, live, self._bundle, self._starting, self._stopping,
            self._running or rtbw_running,
            (not self._sweep.can_close() and not (self._running or self._starting or self._stopping))
            or bool(selection and selection.release_pending),
            self._error or live.error_label,
            self._configuration_pending,
            self._sweep_snapshot,
            self._prepared_sweep,
            selection,
            self._hackrf_sweep_composed,
            self._hackrf_sweep_request if self._mode is AnalyzerMode.SWEEP else None,
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
        selection = getattr(self.live, "source_selection", None)
        if (selection is not None and selection.selected is not None
                and selection.selected.family is DeviceFamily.TINYSA and mode is not AnalyzerMode.SWEEP):
            return False
        if (selection is not None and selection.selected is not None
                and selection.selected.family is DeviceFamily.HACKRF and mode is AnalyzerMode.SWEEP
                and not self.state.hackrf_sweep_controls_available):
            return False
        if mode is not self._mode:
            self._mode = mode
            self._bundle = None  # Never label a prior-mode frame as current.
            self._sweep_snapshot = None
            self._prepared_sweep = None
            self._error = None
            self._hackrf_sweep_request = None
            self._publish()
        return True

    def start(self, request: ContinuousSweepPlanRequest | TinySaSweepRequest | HackrfSweepRequest | None = None) -> bool:
        state = self.state
        instrument = state.tinysa_controls_available and self._mode is AnalyzerMode.SWEEP
        hackrf_sweep = state.hackrf_sweep_controls_available and self._mode is AnalyzerMode.SWEEP
        if self._disposed or state.controls_locked:
            return False
        self._error = None
        if self._mode is AnalyzerMode.RTBW:
            if (not state.rtbw_profile_ready or state.live.primary_action is not LiveAction.START
                    or not state.live.primary_action_enabled):
                return False
            return self.live.execute_primary_action()
        if instrument:
            selection = state.source_selection
            if (not isinstance(request, TinySaSweepRequest) or selection is None
                    or request.source is not selection.selected or request.selection_revision != selection.revision):
                return False
        elif hackrf_sweep:
            selection = state.source_selection
            if (not isinstance(request, HackrfSweepRequest) or selection is None
                    or request.source is not selection.selected or request.selection_revision != selection.revision
                    or state.live.primary_action is not LiveAction.START or not state.live.primary_action_enabled):
                return False
        else:
            if (not state.ad936x_controls_available or not state.rtbw_profile_ready
                    or state.live.primary_action is not LiveAction.START or not state.live.primary_action_enabled
                    or not isinstance(request, ContinuousSweepPlanRequest)):
                return False
        self._instrument_request = request if isinstance(request, TinySaSweepRequest) else None
        self._instrument_epoch = None
        self._instrument_sequence = None
        self._hackrf_sweep_request = request if isinstance(request, HackrfSweepRequest) else None
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
        if state.source_selection is not None and state.source_selection.release_pending:
            return self.live.release_source()
        if self._mode is AnalyzerMode.SWEEP and (self._running or state.stop_required):
            self._sweep.stop()
            return True
        if state.live.primary_action is LiveAction.STOP:
            return self.live.execute_primary_action()
        return False

    def discover_devices(self, *, local_only: bool = False) -> bool:
        return (not self._disposed and not self.state.controls_locked
                and self.live.discover_devices(local_only=local_only))

    def select_device(self, identifier: str) -> bool:
        return not self._disposed and not self.state.controls_locked and self.live.select_device(identifier)

    def select_manual_uri(self, uri: str) -> bool:
        return not self._disposed and not self.state.controls_locked and self.live.select_manual_uri(uri)

    def apply_configuration(self, configuration: object) -> bool:
        if self._disposed or self.state.controls_locked or not self.state.ad936x_controls_available:
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

    def stage_hackrf_configuration(self, patch: object) -> bool:
        if self._disposed or self.state.controls_locked or not self.state.hackrf_controls_available:
            return False
        return self.live.stage_hackrf_configuration(patch)

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
        selection = getattr(self.live, "source_selection", None)
        selected = selection.selected if selection is not None else None
        revision = selection.revision if selection is not None else None
        if selected is not self._mode_source_choice or revision != self._mode_source_revision:
            self._mode_source_choice, self._mode_source_revision = selected, revision
            if selected is not None and selected.family is DeviceFamily.TINYSA:
                self._mode = AnalyzerMode.SWEEP  # Local supported-mode intent, no retune.
            elif selected is not None and selected.family is DeviceFamily.HACKRF:
                self._mode = AnalyzerMode.RTBW
        identity = (getattr(snapshot, "session_id", None), getattr(snapshot, "generation", None),
                    getattr(getattr(snapshot, "device", None), "device_id", None),
                    selection.revision if selection is not None else None)
        if self._live_identity is not None and identity != self._live_identity:
            self._bundle = None
            self._sweep_snapshot = None
            self._prepared_sweep = None
        self._live_identity = identity
        if self._mode is AnalyzerMode.RTBW and (self.state.ad936x_controls_available or self.state.hackrf_controls_available):
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
        if self._hackrf_sweep_request is not None:
            hackrf_request = self._hackrf_sweep_request
            selection = self.state.source_selection
            run_epoch = getattr(self._sweep, "sweep_run_epoch", None)
            if (selection is None or selection.selected is not hackrf_request.source
                    or selection.revision != hackrf_request.selection_revision
                    or type(run_epoch) is not int or run_epoch < 1
                    or any(frame is not None and (
                        frame.source_id != hackrf_request.source.device_id or frame.epoch != run_epoch
                        or frame.unit != "dBFS/bin"
                    ) for frame in (value.line, value.progress))):
                self._on_error("Rejected stale/foreign HackRF Sweep publication")
                return
        bundle = prepared.analyzer_bundle if prepared is not None else value.analyzer_bundle
        if bundle is not None:
            from sdr_monitor.domain.sweep_lines import SweepLineFrame
            frame = bundle.spectrum
            provenance = frame.instrument if isinstance(frame, SweepLineFrame) else None
            request = self._instrument_request
            if self.state.tinysa_controls_available:
                selection = self.state.source_selection
                run = getattr(self._sweep, "instrument_run_identity", None)
                if (request is None or provenance is None or selection is None or not isinstance(frame, SweepLineFrame)
                        or not isinstance(run, TinySaSweepRunIdentity)
                        or run.request.source is not request.source
                        or (run.request.settings, run.request.input_mode, run.request.readback_settings) != (
                            request.settings, request.input_mode, request.readback_settings)
                        or run.request.external_correction is not request.external_correction
                        or (run.request.frontend_chain, run.request.allow_correction_extrapolation) != (
                            request.frontend_chain, request.allow_correction_extrapolation)
                        or frame.epoch != run.request.epoch
                        or provenance.configuration_generation != run.configuration_generation
                        or selection.selected is not request.source
                        or provenance.selection_revision != selection.revision
                        or frame.source_id != request.source.device_id or frame.unit != "dBm"
                        or provenance.device_identity_key != request.source.binding.identity_key
                        or request.source.binding.calibration_identity is None
                        or provenance.firmware_fingerprint != request.source.binding.calibration_identity.firmware_fingerprint
                        or frame.epoch < request.epoch
                        or (provenance.start_hz, provenance.stop_hz, provenance.points) !=
                           (request.start_hz, request.stop_hz, request.points)
                        or provenance.settings is not None and (
                            provenance.settings.plan != request.settings or provenance.settings.input_mode != request.input_mode)
                        or provenance.settings is not None and not (
                            request.settings.commands or request.input_mode.value != "preserve" or request.readback_settings)
                        or frame.is_complete and (request.settings.commands or request.input_mode.value != "preserve"
                            or request.readback_settings) and provenance.settings is None
                        or frame.is_complete and request.readback_settings and provenance.settings is not None and any(
                            value is None for value in (provenance.settings.actual_rbw_hz,
                                provenance.settings.actual_attenuation_db, provenance.settings.screen_sweep_time_s))
                        or frame.is_complete and (provenance.external_correction is None) != (request.external_correction is None)
                        or provenance.external_correction is not None and (
                            provenance.external_correction.profile is not request.external_correction
                            or provenance.settings is None
                            or provenance.settings.actual_rbw_hz is None
                            or provenance.settings.actual_attenuation_db is None
                            or provenance.external_correction.current_signature != tinysa_correction_signature(
                                request, rbw_hz=provenance.settings.actual_rbw_hz,
                                attenuation_db=provenance.settings.actual_attenuation_db))
                        or provenance.gap_value_context not in (
                            None, "external:" + request.external_correction.fingerprint if request.external_correction else None)
                        or self._instrument_epoch is not None and frame.epoch != self._instrument_epoch
                        or self._instrument_sequence is not None and frame.sequence < self._instrument_sequence):
                    self._on_error("Rejected stale/foreign instrument Sweep publication")
                    return
                self._instrument_epoch = frame.epoch
                self._instrument_sequence = frame.sequence
            elif provenance is not None:
                self._on_error("Rejected instrument publication for another source")
                return
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
