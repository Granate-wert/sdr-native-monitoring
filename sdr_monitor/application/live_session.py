"""Application boundary for one bounded Live SDR session.

The port deliberately carries immutable domain values only.  It is implemented
by infrastructure services such as the in-memory and native Pluto adapters,
while presenters depend on the use-case interface rather than service modules.
"""

from __future__ import annotations

from typing import Protocol
from collections.abc import Callable
from dataclasses import replace

from .analyzer_session import (
    AnalyzerSessionApplicationService, AnalyzerSessionState, AnalyzerMode,
    AnalyzerPhase, AnalyzerLiveRejected,
)

from ..domain import DeviceDescriptor, LiveConfiguration, LiveSnapshot
from ..domain.live_configuration_patch import LiveConfigurationPatch
from ..domain.live import DEFAULT_LIVE_RESOURCE_BUDGET, LiveSessionState, LiveErrorKind
from ..domain.analyzer_resources import AnalyzerGeometryPreflight, estimate_analyzer_reduced
from ..domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..domain.analyzer import AnalyzerFrameBundle, bundle_from_live


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

    def discover(self, *, startup: bool = False) -> tuple[DeviceDescriptor, ...]: ...
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
    ] | None = None, analyzer: AnalyzerSessionApplicationService | None = None) -> None:
        self._port = port
        self._sweep_preflight = sweep_preflight
        self._analyzer = analyzer
        self._control_error: tuple[str, LiveErrorKind | None] | None = None

    @property
    def analyzer_state(self) -> AnalyzerSessionState | None:
        return self._analyzer.state if self._analyzer is not None else None

    def _configuration_admission(self, *, idle_only: bool = False) -> None:
        if self._analyzer is None:
            return
        state = self._analyzer.state
        if state.phase is AnalyzerPhase.IDLE:
            return
        if not idle_only and state.mode is AnalyzerMode.RTBW and state.phase is AnalyzerPhase.RUNNING:
            return
        raise RuntimeError("Stop the active analyzer operation before changing configuration")

    def _lifecycle_snapshot(self, snapshot: LiveSnapshot) -> LiveSnapshot:
        if self._analyzer is None:
            return snapshot
        required = self._analyzer.stop_required
        if self._analyzer.state.phase is AnalyzerPhase.ERROR and self._control_error is not None:
            message, kind = self._control_error
            snapshot = replace(snapshot, state=LiveSessionState.ERROR, error=message, error_kind=kind)
        return replace(snapshot, stop_required=required) if snapshot.stop_required != required else snapshot

    def _failed_lifecycle_snapshot(self, error: Exception) -> LiveSnapshot:
        snapshot = error.snapshot if isinstance(error, AnalyzerLiveRejected) else replace(
            self._port.latest_snapshot(), state=LiveSessionState.ERROR,
            error=str(error), error_kind=LiveErrorKind.INTERNAL,
        )
        if snapshot.error is None:
            snapshot = replace(snapshot, error=str(error), error_kind=LiveErrorKind.INTERNAL)
        self._control_error = (snapshot.error or str(error), snapshot.error_kind)
        return self._lifecycle_snapshot(snapshot)

    def discover(self, *, startup: bool = False) -> tuple[DeviceDescriptor, ...]:
        return self._port.discover_startup_devices() if startup else self._port.discover_devices()

    def select_device(self, device_id: str) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        return self._port.select_device(device_id)

    def select_manual_uri(self, uri: str) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        return self._port.select_manual_uri(uri)

    def current_snapshot(self) -> LiveSnapshot:
        return self._lifecycle_snapshot(self._port.latest_snapshot())

    def current_analyzer_bundle(self) -> AnalyzerFrameBundle | None:
        """Validated shared reduced contract, never a UI-specific frame format."""
        return self.analyzer_bundle_for_snapshot(self.current_snapshot())

    def analyzer_bundle_for_snapshot(self, snapshot: LiveSnapshot) -> AnalyzerFrameBundle | None:
        """Validate the exact delivered snapshot, never sample another revision."""
        return bundle_from_live(snapshot)

    def preflight_configuration(self, configuration: LiveConfiguration) -> AnalyzerGeometryPreflight:
        """Pure geometry/resource admission, never applied hardware readback."""
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
        if self._sweep_preflight is None:
            raise RuntimeError("Sweep preflight is unavailable in this composition")
        return self._sweep_preflight(configuration, request)

    def apply_configuration(self, configuration: LiveConfiguration | LiveConfigurationPatch) -> LiveSnapshot:
        self._configuration_admission()
        # The presenter invokes this inside its single control worker, not
        # while the UI is assembling a draft. A conflict has no port mutation.
        if isinstance(configuration, LiveConfigurationPatch):
            configuration = configuration.resolve(self._port.latest_snapshot())
        self.preflight_configuration(configuration)
        return self._lifecycle_snapshot(self._port.apply_configuration(configuration))

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

    def start_with_configuration(self, configuration: LiveConfiguration) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        snapshot = self.apply_configuration(configuration)
        return self.start() if snapshot.error is None else snapshot

    def start(self) -> LiveSnapshot:
        if self._analyzer is not None:
            self._analyzer.select_mode(AnalyzerMode.RTBW)
            self._control_error = None
            try:
                self._analyzer.start()
            except Exception as error:
                return self._failed_lifecycle_snapshot(error)
            return self.current_snapshot()
        return self._port.start()

    def start_sweep(self, request: ContinuousSweepPlanRequest) -> AnalyzerSessionState:
        if self._analyzer is None:
            raise RuntimeError("Shared analyzer is unavailable in this composition")
        self._configuration_admission(idle_only=True)
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

    def stop(self) -> LiveSnapshot:
        if self._analyzer is not None:
            try:
                self._analyzer.stop()
            except Exception as error:
                return self._failed_lifecycle_snapshot(error)
            return self.current_snapshot()
        return self._port.stop()

    def poll_published_snapshots(self) -> list[LiveSnapshot]:
        return [self._lifecycle_snapshot(snapshot) for snapshot in self._port.poll_frames()]

    def is_running(self) -> bool:
        return self._port.is_running()

    def shutdown(self, timeout_s: float = 5.0) -> None:
        try:
            if self._analyzer is not None:
                self._analyzer.stop()
        finally:
            # Pending/failed controller transitions must not suppress the
            # backend's cancellation/join path during application shutdown.
            self._port.stop_and_wait(timeout_s)


__all__ = ["LiveSessionApplicationService", "LiveSessionPort", "LiveSessionUseCases"]
