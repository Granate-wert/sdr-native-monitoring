"""Qt-widget-independent presenter for the Live Monitor workspace.

The presenter owns one :class:`LiveSdrController` lifecycle, validates
requested configurations against device capabilities before connecting,
and converts bounded controller publications into immutable
:class:`~esw_dfl.ui.live_state.LiveMonitorSnapshot` objects that the
workspace renders from a GUI timer.

Thread discipline:

* the presenter never calls native/device code on the GUI thread —
  :meth:`LiveMonitorPresenter.poll` only drains the bounded update queue
  published by the controller worker;
* generation guards reject updates that belong to a previous controller
  instance (stale updates are surfaced with ``stale=True``, never applied);
* diagnostics are rate-limited: when no new publication arrived, ``poll``
  returns the last snapshot without rebuilding anything, so a 60 Hz GUI
  timer costs one deque peek per tick.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from ..sdr.contracts import (
    CalibrationStatus,
    ComputeBackendKind,
    DeviceCapabilities,
)
from ..sdr.controller import (
    LiveControllerState,
    LiveControllerUpdate,
    LiveSdrController,
    LiveSessionConfig,
    ServiceFactory,
)
from ..sdr.fake_live_service import validate_against_capabilities
from ..sdr.fixed_band import FixedBandEngineService, FixedBandOptions
from .live_state import (
    BackendBadge,
    CalibrationBadge,
    LiveMonitorSnapshot,
    QualityFlagItem,
    RecordingHookState,
    RequestedAppliedValue,
)
from .units import format_frequency_hz, format_level

BackendAvailability = Callable[[ComputeBackendKind], bool]


def default_backend_availability(backend: ComputeBackendKind) -> bool:
    """Default availability probe: only the CPU reference backend is known.

    CUDA/HIP support is reported as NOT_VERIFIED until a runtime probe is
    supplied by the integration layer (P08 hardware lane).
    """

    return backend is ComputeBackendKind.CPU


class LiveMonitorPresenter:
    """Own one fixed-band session and publish rate-limited snapshots."""

    def __init__(
        self,
        *,
        service_factory: ServiceFactory = FixedBandEngineService,
        capabilities_provider: Callable[[str], DeviceCapabilities | None] | None = None,
        backend_availability: BackendAvailability = default_backend_availability,
        poll_interval_s: float = 0.05,
        spectrum_batch_size: int = 8,
        event_batch_size: int = 16,
        update_queue_capacity: int = 4,
    ) -> None:
        if poll_interval_s <= 0.0:
            raise ValueError("poll_interval_s must be positive")
        if update_queue_capacity <= 0:
            raise ValueError("update_queue_capacity must be positive")
        self._service_factory = service_factory
        self._capabilities_provider = capabilities_provider
        self._backend_availability = backend_availability
        self._poll_interval_s = float(poll_interval_s)
        self._spectrum_batch_size = int(spectrum_batch_size)
        self._event_batch_size = int(event_batch_size)
        self._update_queue_capacity = int(update_queue_capacity)

        self._controller: LiveSdrController | None = None
        self._requested: FixedBandOptions | None = None
        self._expected_generation: int | None = None
        self._last_frame_sequence: int | None = None
        self._recording_active = False
        self._snapshot: LiveMonitorSnapshot = self._idle_snapshot()
        self._last_error: str | None = None
        self._capabilities: DeviceCapabilities | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def snapshot(self) -> LiveMonitorSnapshot:
        """The most recent snapshot (never None)."""

        return self._snapshot

    @property
    def connected(self) -> bool:
        return self._controller is not None

    @property
    def controller_state(self) -> LiveControllerState:
        if self._controller is None:
            return LiveControllerState.CREATED
        return self._controller.state

    @property
    def requested_options(self) -> FixedBandOptions | None:
        return self._requested

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def capabilities(self) -> DeviceCapabilities | None:
        """Device capabilities of the last validated session, if known."""

        return self._capabilities

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def connect(self, *, source_id: str, display_name: str, uri: str, options: FixedBandOptions) -> list[str]:
        """Validate the requested configuration, then create the controller.

        Returns a list of human-readable validation errors.  An empty list
        means the configuration was accepted and the controller was
        created (not yet started).  Nothing touches native code on this
        call when capabilities are supplied by the provider.
        """

        errors = self.validate_options(options)
        if errors:
            return errors
        config = LiveSessionConfig(
            source_id=source_id,
            display_name=display_name,
            uri=uri,
            options=options,
        )
        self._controller = LiveSdrController(
            config,
            service_factory=self._service_factory,
            poll_interval_s=self._poll_interval_s,
            spectrum_batch_size=self._spectrum_batch_size,
            event_batch_size=self._event_batch_size,
            update_queue_capacity=self._update_queue_capacity,
        )
        self._requested = options
        self._last_error = None
        self._expected_generation = None
        self._last_frame_sequence = None
        self._snapshot = replace(
            self._idle_snapshot(),
            generation=0,
            state=LiveControllerState.CREATED,
            requested_applied=self._build_requested_applied(None, None),
        )
        return []

    def start(self) -> None:
        """Start the session worker thread (non-blocking)."""

        if self._controller is None:
            raise RuntimeError("no session connected")
        generation = self._controller.start()
        self._expected_generation = generation

    def stop(self) -> None:
        """Request a graceful stop without blocking the GUI thread."""

        if self._controller is not None:
            self._controller.request_stop()

    def apply_requested(self, options: FixedBandOptions) -> list[str]:
        """Validate and restart the session with the given options.

        The live controller configures its service once at start; a
        requested change therefore performs a bounded restart: stop the
        current controller, create a new one with the new options and
        start it again.  Generation guards make publications from the old
        controller stale for the new session.
        """

        errors = self.validate_options(options)
        if errors:
            return errors
        if self._controller is None:
            return self.connect(
                source_id=options.device.source_id,
                display_name="Live Monitor",
                uri=options.device.context_uri,
                options=options,
            )
        was_running = self._controller.state in (
            LiveControllerState.STARTING,
            LiveControllerState.RUNNING,
        )
        self._controller.close(wait=True, timeout_s=2.0)
        self._controller = None
        errors = self.connect(
            source_id=options.device.source_id,
            display_name="Live Monitor",
            uri=options.device.context_uri,
            options=options,
        )
        if errors:
            return errors
        if was_running:
            self.start()
        return []

    def disconnect(self) -> None:
        """Close the current session and reset presenter state."""

        if self._controller is not None:
            self._controller.close(wait=True, timeout_s=2.0)
        self._controller = None
        self._requested = None
        self._expected_generation = None
        self._last_frame_sequence = None
        self._last_error = None
        self._capabilities = None
        self._snapshot = self._idle_snapshot()

    def close(self) -> None:
        """Alias of :meth:`disconnect` used by workspace teardown."""

        self.disconnect()

    # ------------------------------------------------------------------
    # Recording action hook (no recorder UI in this package)
    # ------------------------------------------------------------------
    def request_recording(self, active: bool) -> None:
        """Toggle the recording action hook state.

        This package intentionally does not implement the recorder UI;
        the hook only reflects the requested action so the workspace can
        show its state honestly.
        """

        self._recording_active = bool(active)
        previous = self._snapshot.recording
        self._snapshot = replace(
            self._snapshot,
            recording=RecordingHookState(
                supported=True,
                active=self._recording_active,
                detail=previous.detail if previous is not None else None,
            ),
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate_options(self, options: FixedBandOptions) -> list[str]:
        """Return validation errors for the requested configuration."""

        if self._capabilities_provider is not None and options.device.context_uri:
            try:
                self._capabilities = self._capabilities_provider(options.device.context_uri)
            except Exception as error:  # provider failure must not crash the workspace
                return [f"capabilities probe failed: {type(error).__name__}: {error}"]
        return validate_against_capabilities(options, self._capabilities)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def poll(self) -> LiveMonitorSnapshot:
        """Drain controller publications and rebuild the snapshot if needed.

        Cheap when idle: with no new update the previous snapshot is
        returned unchanged, keeping the 60 Hz GUI budget flat.
        """

        controller = self._controller
        if controller is None:
            return self._snapshot
        updates = controller.poll_updates()
        if not updates:
            return self._snapshot
        latest = updates[-1]
        if latest.generation != self._expected_generation:
            self._snapshot = replace(
                self._snapshot,
                generation=latest.generation,
                state=latest.state,
                error=latest.error or self._snapshot.error,
                stale=True,
            )
            return self._snapshot
        self._snapshot = self._build_snapshot(latest)
        return self._snapshot

    # ------------------------------------------------------------------
    # Snapshot building
    # ------------------------------------------------------------------
    def _idle_snapshot(self) -> LiveMonitorSnapshot:
        return LiveMonitorSnapshot(
            generation=0,
            state=LiveControllerState.CREATED,
            requested_applied=(),
            backend=None,
            calibration=None,
            quality=(),
            recording=RecordingHookState(supported=True, active=False),
            frame_rate_hz=0.0,
            error=None,
            stale=False,
        )

    def _build_snapshot(self, update: LiveControllerUpdate) -> LiveMonitorSnapshot:
        applied = update.applied_config
        frame = update.spectrum_frames[-1] if update.spectrum_frames else None
        if frame is not None:
            self._last_frame_sequence = int(frame.frame_sequence)
        metrics = update.metrics
        requested_applied = self._build_requested_applied(applied, frame)
        backend = self._build_backend_badge(applied, metrics)
        calibration = self._build_calibration_badge(frame)
        if calibration is None and self._snapshot.calibration is not None:
            # Metrics-only publications carry no spectrum frame: keep the
            # last confirmed calibration status instead of blinking to
            # "unknown" on every update cycle.
            calibration = self._snapshot.calibration
        quality = self._build_quality_items(metrics, frame)
        frame_rate = 0.0
        if metrics is not None:
            engine = getattr(metrics, "engine", None)
            frame_rate = float(getattr(engine, "analytical_fft_rate", 0.0) or 0.0)
        return LiveMonitorSnapshot(
            generation=update.generation,
            state=update.state,
            requested_applied=requested_applied,
            backend=backend,
            calibration=calibration,
            quality=quality,
            recording=RecordingHookState(supported=True, active=self._recording_active),
            frame_rate_hz=frame_rate,
            error=update.error,
            stale=False,
        )

    def _build_requested_applied(
        self,
        applied: object | None,
        frame: object | None,
    ) -> tuple[RequestedAppliedValue, ...]:
        if self._requested is None:
            return ()
        requested = self._requested
        device = requested.device
        dsp = requested.dsp

        def applied_attr(name: str) -> float | str | None:
            if applied is None:
                return None
            value = getattr(applied, name, None)
            return value

        rows: list[RequestedAppliedValue] = []

        center_applied = applied_attr("center_frequency_hz")
        center_pending = center_applied is None or float(center_applied) != device.center_frequency_hz
        rows.append(
            RequestedAppliedValue(
                field="center_frequency_hz",
                requested=format_frequency_hz(device.center_frequency_hz),
                applied=(
                    format_frequency_hz(float(center_applied)) if center_applied is not None else None
                ),
                pending=center_pending,
            )
        )

        rate_applied = applied_attr("sample_rate_hz")
        rows.append(
            RequestedAppliedValue(
                field="sample_rate_hz",
                requested=format_frequency_hz(device.sample_rate_hz),
                applied=(
                    format_frequency_hz(float(rate_applied)) if rate_applied is not None else None
                ),
                pending=rate_applied is None or float(rate_applied) != device.sample_rate_hz,
            )
        )

        bw_applied = applied_attr("analog_bandwidth_hz")
        rows.append(
            RequestedAppliedValue(
                field="analog_bandwidth_hz",
                requested=format_frequency_hz(device.analog_bandwidth_hz),
                applied=(
                    format_frequency_hz(float(bw_applied)) if bw_applied is not None else None
                ),
                pending=bw_applied is None or float(bw_applied) != device.analog_bandwidth_hz,
            )
        )

        gain_mode_applied = applied_attr("gain_mode")
        rows.append(
            RequestedAppliedValue(
                field="gain_mode",
                requested=str(device.gain_mode.value),
                applied=(
                    getattr(gain_mode_applied, "value", str(gain_mode_applied))
                    if gain_mode_applied is not None
                    else None
                ),
                pending=gain_mode_applied is None
                or getattr(gain_mode_applied, "value", gain_mode_applied) != device.gain_mode.value,
            )
        )

        gain_applied = applied_attr("manual_gain_db")
        rows.append(
            RequestedAppliedValue(
                field="manual_gain_db",
                requested=format_level(device.manual_gain_db, "dB"),
                applied=(
                    format_level(float(gain_applied), "dB") if gain_applied is not None else None
                ),
                pending=gain_applied is None or float(gain_applied) != device.manual_gain_db,
            )
        )

        fft_applied = getattr(frame, "fft_size", None) if frame is not None else None
        rows.append(
            RequestedAppliedValue(
                field="fft_size",
                requested=str(dsp.fft_size),
                applied=str(fft_applied) if fft_applied is not None else None,
                pending=fft_applied is None or int(fft_applied) != dsp.fft_size,
            )
        )

        hop_applied = getattr(frame, "hop_size", None) if frame is not None else None
        rows.append(
            RequestedAppliedValue(
                field="hop_size",
                requested=str(dsp.hop_size),
                applied=str(hop_applied) if hop_applied is not None else None,
                pending=hop_applied is None or int(hop_applied) != dsp.hop_size,
            )
        )

        window_applied = getattr(frame, "window", None) if frame is not None else None
        rows.append(
            RequestedAppliedValue(
                field="window",
                requested=dsp.window.value,
                applied=(
                    getattr(window_applied, "value", str(window_applied))
                    if window_applied is not None
                    else None
                ),
                pending=window_applied is None
                or getattr(window_applied, "value", window_applied) != dsp.window.value,
            )
        )

        detector_applied = getattr(frame, "detector", None) if frame is not None else None
        rows.append(
            RequestedAppliedValue(
                field="detector",
                requested=dsp.detector.value,
                applied=(
                    getattr(detector_applied, "value", str(detector_applied))
                    if detector_applied is not None
                    else None
                ),
                pending=detector_applied is None
                or getattr(detector_applied, "value", detector_applied) != dsp.detector.value,
            )
        )

        backend_applied = applied_attr("active_backend")
        backend_applied_value = (
            getattr(backend_applied, "value", str(backend_applied))
            if backend_applied is not None
            else None
        )
        rows.append(
            RequestedAppliedValue(
                field="backend",
                requested=requested.backend.value,
                applied=backend_applied_value,
                pending=(
                    backend_applied is None
                    or (
                        requested.backend is not ComputeBackendKind.AUTO
                        and backend_applied_value != requested.backend.value
                    )
                ),
            )
        )

        return tuple(rows)

    def _build_backend_badge(
        self,
        applied: object | None,
        metrics: object | None,
    ) -> BackendBadge | None:
        if self._requested is None:
            return None
        requested = self._requested.backend
        active: ComputeBackendKind | None = None
        fallback_count = 0
        if applied is not None:
            candidate = getattr(applied, "active_backend", None)
            if candidate is not None:
                active = (
                    candidate if isinstance(candidate, ComputeBackendKind) else ComputeBackendKind(str(candidate))
                )
        if metrics is not None:
            candidate = getattr(metrics, "active_backend", None)
            if candidate is not None and active is None:
                active = (
                    candidate if isinstance(candidate, ComputeBackendKind) else ComputeBackendKind(str(candidate))
                )
            fallback_count = int(getattr(metrics, "backend_fallback_count", 0) or 0)
        available = tuple(
            kind for kind in ComputeBackendKind if kind is not ComputeBackendKind.AUTO and self._backend_availability(kind)
        )
        note = None
        if requested is ComputeBackendKind.AUTO and active is not None:
            note = "auto selected %s" % active.value
        elif active is not None and active is not requested and requested is not ComputeBackendKind.AUTO:
            note = "fallback from %s to %s" % (requested.value, active.value)
        return BackendBadge(
            requested=requested,
            active=active,
            available=available,
            fallback_count=fallback_count,
            note=note,
        )

    def _build_calibration_badge(self, frame: object | None) -> CalibrationBadge | None:
        if frame is None:
            return None
        status = getattr(frame, "calibration_status", None)
        profile_id = getattr(frame, "calibration_profile_id", None)
        if status is None:
            return None
        status_value = (
            status if isinstance(status, CalibrationStatus) else CalibrationStatus(str(status))
        )
        return CalibrationBadge(
            status=status_value,
            profile_id=str(profile_id) if profile_id else None,
            applicable=bool(profile_id),
        )

    def _build_quality_items(self, metrics: object | None, frame: object | None) -> tuple[QualityFlagItem, ...]:
        items: list[QualityFlagItem] = []
        if metrics is not None:
            engine = getattr(metrics, "engine", None)
            device = getattr(metrics, "device", None)
            iq_dropped = int(getattr(engine, "iq_blocks_dropped", 0) or 0)
            fft_dropped = int(getattr(engine, "fft_frames_dropped", 0) or 0)
            superseded = int(getattr(metrics, "spectrum_snapshots_superseded", 0) or 0)
            events_lost = int(getattr(metrics, "diagnostic_events_lost", 0) or 0)
            fallbacks = int(getattr(metrics, "backend_fallback_count", 0) or 0)
            estimated_drops = int(getattr(device, "estimated_dropped_samples", 0) or 0)
            healthy = bool(getattr(metrics, "healthy", False))
            if iq_dropped:
                items.append(QualityFlagItem("IQ blocks dropped", str(iq_dropped), "warn"))
            if fft_dropped:
                items.append(QualityFlagItem("FFT frames dropped", str(fft_dropped), "warn"))
            if superseded:
                items.append(QualityFlagItem("Snapshots superseded", str(superseded), "warn"))
            if events_lost:
                items.append(QualityFlagItem("Diagnostic events lost", str(events_lost), "error"))
            if fallbacks:
                items.append(QualityFlagItem("Backend fallbacks", str(fallbacks), "warn"))
            if estimated_drops:
                items.append(QualityFlagItem("Estimated sample drops", str(estimated_drops), "error"))
            items.append(QualityFlagItem("Engine health", "ok" if healthy else "degraded", "ok" if healthy else "error"))
        if frame is not None:
            flags = int(getattr(frame, "quality_flags", 0) or 0)
            from ..sdr.contracts import QualityFlag

            if flags & int(QualityFlag.ADC_OVERLOAD):
                items.append(QualityFlagItem("ADC overload", "flagged", "error"))
            if flags & int(QualityFlag.IQ_DROPPED):
                items.append(QualityFlagItem("I/Q drop flag", "flagged", "warn"))
            if flags & int(QualityFlag.FFT_DROPPED):
                items.append(QualityFlagItem("FFT drop flag", "flagged", "warn"))
        return tuple(items)


__all__ = [
    "BackendAvailability",
    "LiveMonitorPresenter",
    "default_backend_availability",
]
