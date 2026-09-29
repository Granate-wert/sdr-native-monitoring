"""Application boundary for one bounded Live SDR session.

The port deliberately carries immutable domain values only.  It is implemented
by infrastructure services such as the in-memory and native Pluto adapters,
while presenters depend on the use-case interface rather than service modules.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
from threading import RLock, local
from typing import Concatenate, Iterator, ParamSpec, Protocol, TypeVar
from collections.abc import Callable
from dataclasses import replace

from .analyzer_session import (
    AnalyzerSessionApplicationService, AnalyzerSessionState, AnalyzerMode,
    AnalyzerPhase, AnalyzerLiveRejected,
)
from .analyzer_sources import AnalyzerSourceSelectionApplicationService
from .analyzer_rtbw_router import AnalyzerRtbwRouter
from ..domain.hackrf_live import HackrfConfigurationPatch
from ..domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection

from ..domain import DeviceDescriptor, LiveConfiguration, LiveSnapshot, ConfigurationGeneration, FrameSequence
from ..domain.live_configuration_patch import LiveConfigurationPatch
from ..domain.live import DEFAULT_LIVE_RESOURCE_BUDGET, LiveSessionState, LiveErrorKind
from ..domain.analyzer_resources import AnalyzerGeometryPreflight, estimate_analyzer_reduced
from ..domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..domain.hackrf_sweep import HackrfSweepRequest
from ..domain.analyzer import AnalyzerFrameBundle, bundle_from_live
from ..domain.recording import RecordingState
from ..domain.tinysa_analyzer import TinySaSweepRequest


_Args = ParamSpec("_Args")
_Result = TypeVar("_Result")


def _pane_exclusive_command(method: Callable[Concatenate[LiveSessionApplicationService, _Args], _Result]
                            ) -> Callable[Concatenate[LiveSessionApplicationService, _Args], _Result]:
    """Keep ordinary V2 controls outside an APP-07 pane-owned Live graph."""
    @wraps(method)
    def guarded(self: LiveSessionApplicationService, *args: _Args.args,
                **kwargs: _Args.kwargs) -> _Result:
        if not self._pane_application_lock.acquire(blocking=False):
            raise RuntimeError("Another receiver control operation is pending")
        try:
            claim = self._pane_control_claim
            if claim is not None and getattr(self._pane_control_thread, "claim", None) is not claim:
                raise RuntimeError("Receiver is reserved by a pane capture; use its explicit Stop")
            return method(self, *args, **kwargs)
        finally:
            self._pane_application_lock.release()

    return guarded


class LiveSessionPort(Protocol):
    """Infrastructure operations required by the bounded Live use case."""

    def discover_devices(self) -> tuple[DeviceDescriptor, ...]: ...
    def discover_startup_devices(self) -> tuple[DeviceDescriptor, ...]: ...
    def select_device(self, device_id: str) -> LiveSnapshot: ...
    def select_manual_uri(self, uri: str) -> LiveSnapshot: ...
    def apply_configuration(self, requested: LiveConfiguration) -> LiveSnapshot: ...
    def start(self) -> LiveSnapshot: ...
    def stop(self) -> LiveSnapshot: ...
    def latest_snapshot(self) -> LiveSnapshot: ...
    def poll_frames(self) -> list[LiveSnapshot]: ...
    def is_running(self) -> bool: ...
    def stop_and_wait(self, timeout_s: float) -> None: ...


class LiveSessionUseCases(Protocol):
    """Application operations consumed by the Qt presenter."""

    def discover(self, *, startup: bool = False) -> tuple[DeviceDescriptor | AnalyzerSourceChoice, ...]: ...
    def select_device(self, device_id: str) -> LiveSnapshot: ...
    def select_manual_uri(self, uri: str) -> LiveSnapshot: ...
    def current_snapshot(self) -> LiveSnapshot: ...
    def analyzer_bundle_for_snapshot(self, snapshot: LiveSnapshot) -> AnalyzerFrameBundle | None: ...
    def preflight_configuration(self, configuration: LiveConfiguration) -> AnalyzerGeometryPreflight: ...
    def preflight_sweep(self, configuration: LiveConfiguration, request: ContinuousSweepPlanRequest) -> AnalyzerGeometryPreflight: ...
    def apply_configuration(self, configuration: LiveConfiguration | LiveConfigurationPatch) -> LiveSnapshot: ...
    def reconfigure(self, configuration: LiveConfiguration, *, restart: bool = True) -> LiveSnapshot: ...
    def start_with_configuration(self, configuration: LiveConfiguration) -> LiveSnapshot: ...
    def start(self) -> LiveSnapshot: ...
    def stop(self) -> LiveSnapshot: ...
    def poll_published_snapshots(self) -> list[LiveSnapshot]: ...
    def is_running(self) -> bool: ...
    def shutdown(self, timeout_s: float = 5.0) -> None: ...


class LiveSessionApplicationService:
    """Coordinates one Live session without Qt, native bindings or widgets."""

    def __init__(self, port: LiveSessionPort, *, sweep_preflight: Callable[
        [LiveConfiguration, ContinuousSweepPlanRequest], AnalyzerGeometryPreflight,
    ] | None = None, analyzer: AnalyzerSessionApplicationService | None = None,
                 catalog_close: Callable[[], None] | None = None,
                 sources: AnalyzerSourceSelectionApplicationService | None = None,
                 rtbw: AnalyzerRtbwRouter | None = None) -> None:
        self._port = port
        self._sweep_preflight = sweep_preflight
        self._analyzer = analyzer
        self._catalog_close = catalog_close
        self._sources = sources
        self._rtbw = rtbw
        self._empty_source_snapshot: tuple[AnalyzerSourceSelection, LiveSnapshot] | None = None
        self._control_error: tuple[str, LiveErrorKind | None] | None = None
        self._pane_application_lock = RLock()
        self._pane_control_thread = local()
        self._pane_control_claim: object | None = None

    @property
    def analyzer_state(self) -> AnalyzerSessionState | None:
        return self._analyzer.state if self._analyzer is not None else None

    def _configuration_admission(self, *, idle_only: bool = False) -> None:
        if self._analyzer is None:
            return
        state = self._analyzer.state
        if state.phase is AnalyzerPhase.IDLE and not self._analyzer.control_busy:
            return
        if not idle_only and state.mode is AnalyzerMode.RTBW and state.phase is AnalyzerPhase.RUNNING:
            return
        raise RuntimeError("Stop the active analyzer operation before changing configuration")

    def _lifecycle_snapshot(self, snapshot: LiveSnapshot) -> LiveSnapshot:
        if self._analyzer is None:
            return snapshot
        required = self._analyzer.stop_required or bool(self._sources and self._sources.current().release_pending)
        if self._analyzer.state.phase is AnalyzerPhase.ERROR and self._control_error is not None:
            message, kind = self._control_error
            snapshot = replace(snapshot, state=LiveSessionState.ERROR, error=message, error_kind=kind)
        return replace(snapshot, stop_required=required) if snapshot.stop_required != required else snapshot

    def _failed_lifecycle_snapshot(self, error: Exception) -> LiveSnapshot:
        snapshot = error.snapshot if isinstance(error, AnalyzerLiveRejected) else replace(
            self.current_snapshot(), state=LiveSessionState.ERROR,
            error=str(error), error_kind=LiveErrorKind.INTERNAL,
        )
        if snapshot.error is None:
            snapshot = replace(snapshot, error=str(error), error_kind=LiveErrorKind.INTERNAL)
        self._control_error = (snapshot.error or str(error), snapshot.error_kind)
        return self._lifecycle_snapshot(snapshot)

    def current_source_selection(self) -> AnalyzerSourceSelection | None:
        """Immutable low-rate control metadata; no SDK/catalog rebuild."""
        return self._sources.current() if self._sources is not None else None

    @contextmanager
    def pane_control_transaction(self, claim: object | None = None) -> Iterator[None]:
        """Use this graph's native recorder/receiver exclusion for APP-07.

        Inert/non-native graphs do not silently provide a no-op transaction.
        The transaction is acquired by the caller around the entire staged
        configuration and Start or Stop, not around the FFT hot path.
        """
        transaction = getattr(self._port, "pane_capture_control_transaction", None)
        if not callable(transaction):
            raise RuntimeError("Pane capture requires the native recording/control owner")
        if not self._pane_application_lock.acquire(blocking=False):
            raise RuntimeError("Another receiver control operation is pending")
        try:
            with transaction():
                if claim is None:
                    if self._pane_control_claim is not None:
                        raise RuntimeError("Receiver is reserved by another pane capture")
                elif self._pane_control_claim is None:
                    self._pane_control_claim = claim
                elif self._pane_control_claim is not claim:
                    raise RuntimeError("Receiver is reserved by another pane capture")
                prior = getattr(self._pane_control_thread, "claim", None)
                self._pane_control_thread.claim = claim
                try:
                    yield
                finally:
                    self._pane_control_thread.claim = prior
        finally:
            self._pane_application_lock.release()

    def release_pane_control(self, claim: object) -> None:
        """Release only this owner after its explicit Stop confirmed release."""
        with self._pane_application_lock:
            if self._pane_control_claim is None:
                return
            if (self._pane_control_claim is not claim
                    or getattr(self._pane_control_thread, "claim", None) is not claim):
                raise RuntimeError("Foreign pane cannot release receiver control")
            self._pane_control_claim = None

    def pane_recording_conflict(self) -> bool:
        """Unknown native recorder state is a refusal, never assumed idle."""
        getter = getattr(self._port, "native_recording_health", None)
        if not callable(getter):
            raise RuntimeError("Pane capture requires native recording state")
        state = getattr(getter(), "state", None)
        if not isinstance(state, RecordingState):
            raise RuntimeError("Pane capture could not verify native recording state")
        return state not in (RecordingState.IDLE, RecordingState.COMPLETED)

    def _require_native_family(self) -> None:
        if self._sources is not None:
            self._sources.require_ad936x_controls()

    @_pane_exclusive_command
    def discover(self, *, startup: bool = False) -> tuple[DeviceDescriptor | AnalyzerSourceChoice, ...]:
        if self._sources is not None:
            try:
                return self._sources.discover(startup=startup)
            finally:
                if self._rtbw is not None:
                    self._rtbw.refresh_selection()
        return self._port.discover_startup_devices() if startup else self._port.discover_devices()

    @_pane_exclusive_command
    def select_device(self, device_id: str) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        if self._sources is not None:
            try:
                self._sources.select(device_id)
            finally:
                if self._rtbw is not None:
                    self._rtbw.refresh_selection()
            return self.current_snapshot()
        return self._port.select_device(device_id)

    @_pane_exclusive_command
    def select_manual_uri(self, uri: str) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        if self._sources is not None:
            try:
                return self._sources.select_manual_uri(uri)
            finally:
                if self._rtbw is not None:
                    self._rtbw.refresh_selection()
        return self._port.select_manual_uri(uri)

    def current_snapshot(self) -> LiveSnapshot:
        if self._rtbw is not None and self._rtbw.hackrf_selected:
            return self._lifecycle_snapshot(self._rtbw.current_snapshot())
        selection = self.current_source_selection()
        if selection is not None and not selection.ad936x_controls_available:
            # This is an EMPTY AD936x publication, not a fabricated HackRF/
            # instrument LiveSnapshot. Typed family control state is separate.
            if self._empty_source_snapshot is None or self._empty_source_snapshot[0] is not selection:
                snapshot = LiveSnapshot(ConfigurationGeneration(0), FrameSequence(0),
                    LiveSessionState.ERROR if selection.refusal else LiveSessionState.DISCONNECTED,
                    unit="unavailable", error="Source observation failed closed" if selection.refusal else None,
                    error_kind=LiveErrorKind.CONNECTION_FAILED if selection.refusal else None,
                    stop_required=selection.release_pending)
                self._empty_source_snapshot = selection, snapshot
            return self._lifecycle_snapshot(self._empty_source_snapshot[1])
        self._empty_source_snapshot = None
        return self._lifecycle_snapshot(self._port.latest_snapshot())

    def current_analyzer_bundle(self) -> AnalyzerFrameBundle | None:
        """Validated shared reduced contract, never a UI-specific frame format."""
        return self.analyzer_bundle_for_snapshot(self.current_snapshot())

    def analyzer_bundle_for_snapshot(self, snapshot: LiveSnapshot) -> AnalyzerFrameBundle | None:
        """Validate the exact delivered snapshot, never sample another revision."""
        return bundle_from_live(snapshot)

    def preflight_configuration(self, configuration: LiveConfiguration) -> AnalyzerGeometryPreflight:
        """Pure geometry/resource admission, never applied hardware readback."""
        self._require_native_family()
        resources = DEFAULT_LIVE_RESOURCE_BUDGET.validate(configuration)
        retained = resources.spectrum_backlog_bytes // (configuration.fft_size * 16)
        reduced = estimate_analyzer_reduced("rtbw", configuration.fft_size, retained)
        return AnalyzerGeometryPreflight(
            "rtbw", configuration.sample_rate_hz, configuration.fft_size,
            configuration.sample_rate_hz / configuration.fft_size, 1, 0.0, reduced,
            usable_window_hz=configuration.sample_rate_hz,
            physical_bin_spacing_hz=configuration.sample_rate_hz / configuration.fft_size,
            fft_averaging_frames=configuration.averaging_frames,
            minimum_samples_per_spectrum=configuration.fft_size + (configuration.averaging_frames - 1) *
                max(1, int(round(configuration.fft_size * (1.0 - configuration.overlap_ratio)))),
        )

    def preflight_sweep(
        self, configuration: LiveConfiguration, request: ContinuousSweepPlanRequest,
    ) -> AnalyzerGeometryPreflight:
        self._require_native_family()
        if self._sweep_preflight is None:
            raise RuntimeError("Sweep preflight is unavailable in this composition")
        return self._sweep_preflight(configuration, request)

    @_pane_exclusive_command
    def apply_configuration(self, configuration: LiveConfiguration | LiveConfigurationPatch) -> LiveSnapshot:
        self._require_native_family()
        self._configuration_admission()
        # The presenter invokes this inside its single control worker, not
        # while the UI is assembling a draft. A conflict has no port mutation.
        if isinstance(configuration, LiveConfigurationPatch):
            configuration = configuration.resolve(self._port.latest_snapshot())
        self.preflight_configuration(configuration)
        return self._lifecycle_snapshot(self._port.apply_configuration(configuration))

    @_pane_exclusive_command
    def reconfigure(self, configuration: LiveConfiguration, *, restart: bool = True) -> LiveSnapshot:
        """Preserve the stop → apply → optional-resume session transaction."""

        # Reject an inadmissible draft before disturbing an existing stream.
        self._configuration_admission()
        self.preflight_configuration(configuration)
        was_running = self._port.is_running()
        if was_running:
            stopped = self.stop()
            if stopped.error is not None or self._port.is_running():
                return stopped
        snapshot = self.apply_configuration(configuration)
        if restart and was_running and snapshot.error is None:
            return self.start()
        return snapshot

    @_pane_exclusive_command
    def start_with_configuration(self, configuration: LiveConfiguration) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        snapshot = self.apply_configuration(configuration)
        return self.start() if snapshot.error is None else snapshot

    @_pane_exclusive_command
    def start(self) -> LiveSnapshot:
        if self._rtbw is None or not self._rtbw.hackrf_selected:
            self._require_native_family()
        if self._analyzer is not None:
            self._analyzer.select_mode(AnalyzerMode.RTBW)
            self._control_error = None
            try:
                self._analyzer.start()
            except Exception as error:
                return self._failed_lifecycle_snapshot(error)
            return self.current_snapshot()
        return self._port.start()

    @_pane_exclusive_command
    def stage_hackrf_configuration(self, patch: HackrfConfigurationPatch) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        if self._rtbw is None or self._analyzer is None:
            raise RuntimeError("Common HackRF RTBW is unavailable")
        with self._analyzer.idle_control_operation():
            return self._lifecycle_snapshot(self._rtbw.stage_hackrf(patch))

    @_pane_exclusive_command
    def start_sweep(self, request: ContinuousSweepPlanRequest | TinySaSweepRequest | HackrfSweepRequest) -> AnalyzerSessionState:
        if self._analyzer is None:
            raise RuntimeError("Shared analyzer is unavailable in this composition")
        self._configuration_admission(idle_only=True)
        if isinstance(request, (TinySaSweepRequest, HackrfSweepRequest)):
            selection = self.current_source_selection()
            if (selection is None or selection.selected is not request.source
                    or selection.revision != request.selection_revision or selection.release_pending):
                raise RuntimeError("Sweep source selection changed")
        else:
            self._require_native_family()
            snapshot = self._port.latest_snapshot()
            if snapshot.applied is None:
                raise RuntimeError("Sweep requires an applied Live profile")
            self.preflight_sweep(snapshot.applied.applied, request)
        self._analyzer.select_mode(AnalyzerMode.SWEEP)
        self._control_error = None
        try:
            return self._analyzer.start(request)
        except Exception as error:
            self._control_error = (str(error), LiveErrorKind.INTERNAL)
            raise

    @_pane_exclusive_command
    def stop(self) -> LiveSnapshot:
        if self._analyzer is not None:
            try:
                self._analyzer.stop()
            except Exception as error:
                return self._failed_lifecycle_snapshot(error)
            if self._sources is not None:
                self._sources.release_pending()
            return self.current_snapshot()
        return self._port.stop()

    def poll_published_snapshots(self) -> list[LiveSnapshot]:
        if self._rtbw is not None:
            return [self._lifecycle_snapshot(value) for value in self._rtbw.poll_frames()]
        selection = self.current_source_selection()
        if selection is not None and not selection.ad936x_controls_available:
            return []
        return [self._lifecycle_snapshot(snapshot) for snapshot in self._port.poll_frames()]

    def is_running(self) -> bool:
        if self._rtbw is not None:
            return self._rtbw.is_running()
        selection = self.current_source_selection()
        if selection is not None and not selection.ad936x_controls_available:
            return False
        return self._port.is_running()

    @_pane_exclusive_command
    def shutdown(self, timeout_s: float = 5.0) -> None:
        errors: list[Exception] = []
        try:
            if self._analyzer is not None:
                self._analyzer.stop()
        except Exception as error:  # noqa: BLE001 - independent owner cleanup must still run.
            errors.append(error)
        try:
            self._port.stop_and_wait(timeout_s)
        except Exception as error:  # noqa: BLE001 - preserve failure and attempt independent catalog release.
            errors.append(error)
        try:
            # Runs on the presenter's lifecycle worker, after RX cancellation.
            # Its shared control gate refuses unresolved stream ownership; it
            # never steals that owner merely to close a read-only provider.
            if self._catalog_close is not None:
                self._catalog_close()
        except Exception as error:  # noqa: BLE001 - preserve the same retained provider for explicit retry.
            errors.append(error)
        if errors:
            raise errors[0]  # Explicit subsequent shutdown can resume cleanup.


__all__ = ["LiveSessionApplicationService", "LiveSessionPort", "LiveSessionUseCases"]
