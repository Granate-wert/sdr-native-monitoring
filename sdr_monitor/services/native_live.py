"""Optional native/libiio live RX bridge for the standalone product.

Discovery/selection keep the bounded S05 service port; ``start`` now owns one
``PlutoFixedBandEngine`` (P07 native pipeline), configures it from the applied
live configuration, and runs a bounded latest-wins poller thread that converts
native ``SpectrumFrame`` publications into immutable ``LiveSpectrumFrame``
domain values.  Cancellation joins both the engine and the poller thread, so
shutdown never leaves detached native or Python threads.
"""

from __future__ import annotations

import hashlib
from importlib import import_module
import json
import logging
import math
from pathlib import Path
import shutil
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, cast
from ..domain.live import LiveAdmissionRejected

from ..activity_log import log_event
from ..libiio_runtime import configure_frozen_libiio_runtime
from ..domain import (
    AppliedLiveConfiguration,
    BackendKind,
    ConfigurationGeneration,
    DeviceCapabilities,
    DeviceDescriptor,
    DeviceTransport,
    IqComponent,
    LossReason,
    LiveConfiguration,
    LiveErrorKind,
    LivePerformance,
    LivePersistenceFrame,
    LiveQuality,
    LiveSnapshot,
    LiveSpectrumFrame,
    RecordingHealth,
    RecordingOptions,
    RecordingState,
    SourceId,
    ReceiverChain,
    ReceiverTopologySnapshot,
    StreamScanElement,
    TimestampQuality,
    as_configuration_generation,
    as_frame_sequence,
    as_source_id,
    as_timestamp_ns,
)
from .live_session import InMemoryLiveSessionService
from .native_spectrum_provenance import native_spectrum_provenance, validate_absolute_unit

# P07 defaults mirrored from the legacy adapter contract.
_FFT_SIZE = 4096
_HOP_SIZE = 2048
# R10-D6 responsive UI default. This bounds only SpectrumFrame publication to
# Python; native DSP, persistence accumulation and continuous Sweep assembly
# remain upstream and process every admitted FFT.
_DEFAULT_SNAPSHOT_RATE_HZ = 240.0
_DEFAULT_DEVICE_BUFFER_SAMPLES = 262_144
_ACQUISITION_QUEUE_CAPACITY = 16
_SPECTRUM_QUEUE_CAPACITY = 4
_EVENT_QUEUE_CAPACITY = 64
_DISCARD_BLOCKS_AFTER_START = 2
# The bridge remains latest-wins but must observe high-rate native
# publications quickly enough for 120/144/240 Hz GUI modes.  This does not
# create a frame backlog: only the newest frame from each poll is retained.
_POLL_INTERVAL_S = 0.001
_METRICS_SAMPLE_INTERVAL_S = 0.25
_NATIVE_EVENT_SAMPLE_INTERVAL_S = 0.10
_PERFORMANCE_LOG_INTERVAL_S = 5.0
# When no spectrum frame arrives for this long while the engine claims to be
# healthy, the bridge reports a stalled stream instead of hanging in RUNNING.
_STALL_TIMEOUT_S = 2.0
_SCHEMA_VERSION = 5

_QUALITY_FLAG_BITS = {
    "IQ_DROPPED": 1 << 5,
    "FFT_DROPPED": 1 << 6,
    "TIMESTAMP_ESTIMATED": 1 << 13,
    "BACKEND_FALLBACK": 1 << 14,
    "BACKEND_DISCONTINUITY": 1 << 15,
}

_LOGGER = logging.getLogger("sdr_native_monitoring")


@dataclass(frozen=True, slots=True)
class _NativeRecordingRequest:
    """Control-plane-only request passed to the native writer at Live start."""

    options: RecordingOptions
    epoch: int
    armed_at_ns: int
    start_reason: str
    restart_gap_started_ns: int | None = None


def _native_spectrum_unit(value: Any) -> str:
    """Translate declared native units only; never infer calibration or density."""
    name = getattr(value, "name", value)
    units = {
        "dbfs_bin": "dBFS/bin", "dbfs_hz": "dBFS/Hz",
        "dbm_bin": "dBm/bin", "dbm_hz": "dBm/Hz", "dbm": "dBm",
        "dbfs/bin": "dBFS/bin", "dbfs/hz": "dBFS/Hz",
        "dbm/bin": "dBm/bin", "dbm/hz": "dBm/Hz",
    }
    if not isinstance(name, str) or name.casefold() not in units:
        raise ValueError("unsupported native spectrum unit")
    return units[name.casefold()]


def _native_quality_mask(native_module: Any, frame: Any, name: str) -> bool:
    """Read a stable pybind flag or use the frozen wire bit for old mocks."""

    try:
        flags = int(getattr(frame, "quality_flags", 0))
    except (TypeError, ValueError):
        flags = 0
    enum = getattr(native_module, "QualityFlag", None)
    try:
        bit = int(getattr(enum, name)) if enum is not None else _QUALITY_FLAG_BITS[name]
    except (AttributeError, TypeError, ValueError):
        bit = _QUALITY_FLAG_BITS[name]
    return bool(flags & bit)


def _native_frame_metadata(native_module: Any, frame: Any, fallback_source_id: str) -> tuple[
    SourceId,
    ConfigurationGeneration,
    TimestampQuality,
    tuple[LossReason, ...],
]:
    """Preserve native source/generation/quality metadata at the Python edge."""

    source = getattr(getattr(frame, "source", None), "source_id", fallback_source_id)
    source_id = as_source_id(source or fallback_source_id)
    generation = as_configuration_generation(getattr(frame, "config_generation", 0))
    timestamp_quality = (
        TimestampQuality.ESTIMATED
        if _native_quality_mask(native_module, frame, "TIMESTAMP_ESTIMATED")
        else TimestampQuality.UNKNOWN
    )
    reasons: list[LossReason] = []
    if int(getattr(frame, "dropped_samples_before", 0) or 0) > 0:
        reasons.append(LossReason.SOURCE)
    if int(getattr(frame, "dropped_iq_blocks_before", 0) or 0) > 0 or _native_quality_mask(native_module, frame, "IQ_DROPPED"):
        reasons.append(LossReason.ACQUISITION_QUEUE)
    if int(getattr(frame, "dropped_fft_frames_before", 0) or 0) > 0 or _native_quality_mask(native_module, frame, "FFT_DROPPED"):
        reasons.append(LossReason.DSP)
    return source_id, generation, timestamp_quality, tuple(reasons)


class NativeLiveSessionService(InMemoryLiveSessionService):
    """Own one native engine while preserving the bounded S05 service port."""

    _snapshot: LiveSnapshot

    def __init__(
        self,
        native_module: Any,
        *,
        timeout_ms: int = 3000,
        device_buffer_samples: int = _DEFAULT_DEVICE_BUFFER_SAMPLES,
        allow_nonstandard_evidence_buffer_geometry: bool = False,
    ) -> None:
        super().__init__()
        _validate_device_buffer_samples(
            device_buffer_samples,
            allow_nonstandard_evidence_buffer_geometry=allow_nonstandard_evidence_buffer_geometry,
        )
        self._native = native_module
        self._timeout_ms = timeout_ms
        # The product composition root keeps the established 262144-sample
        # default. R10-D6 passes a smaller, explicitly bounded geometry to
        # measure publication/UI cadence without changing native queue sizes
        # or handing raw I/Q to Python.
        self._device_buffer_samples = int(device_buffer_samples)
        self._allow_nonstandard_evidence_buffer_geometry = bool(
            allow_nonstandard_evidence_buffer_geometry
        )
        self._native_device: Any | None = None
        self._native_uri: str | None = None
        self._engine: Any | None = None
        self._poller: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._last_native_frame_sequence = -1
        self._last_metrics_sample_s = 0.0
        self._last_native_event_sample_s = 0.0
        self._last_metrics_log_s = 0.0
        self._last_metrics_frames = 0
        self._last_metrics_snapshots = 0
        self._last_metrics_samples = 0
        # R10-D6 scalar-only bridge counters. The native spectrum queue remains
        # bounded/latest-wins. Native latest-only draining reports any frames
        # it deliberately skips separately from analytical FFT loss.
        self._bridge_native_frames_polled = 0
        self._bridge_frames_coalesced = 0
        self._bridge_frames_published = 0
        # R08-C1 serializes only low-rate lifecycle commands.  It never
        # participates in the native I/Q, FFT or writer hot paths.
        self._recording_transaction_lock = threading.RLock()
        # A native sweep is a separate physical ownership mode.  Its lease is
        # low-rate control-plane state only; it never crosses the acquisition
        # or DSP hot paths and prevents an accidental second context/stream.
        self._sweep_lease_lock = threading.RLock()
        self._sweep_lease_active = False
        self._native_recording_armed: _NativeRecordingRequest | None = None
        self._native_recording_active: _NativeRecordingRequest | None = None
        self._native_recording_epoch = 0
        self._native_recording_started_monotonic_s: float | None = None
        self._native_recording_restart_gap_duration_ns: int | None = None
        self._native_recording_lifecycle_part: Path | None = None
        self._native_recording_lifecycle_handle: Any | None = None
        self._native_recording_lifecycle_error = ""
        self._last_native_recording_health = RecordingHealth(
            RecordingState.IDLE, 0, 0, 0, 0, 0, 0, 0, recording_mode="rtbw"
        )

    def discover_devices(self) -> tuple[DeviceDescriptor, ...]:
        return self._discover_devices("usb,ip")

    def discover_startup_devices(self) -> tuple[DeviceDescriptor, ...]:
        """Discover directly attached radios without a blocking IP broadcast.

        Network discovery remains available from the explicit device dialog.
        libiio's scan API has no cancellation handle, so starting an IP scan
        during Qt startup could make shell shutdown wait on an opaque network
        operation.  USB discovery preserves automatic plug-and-play without
        that uncontrolled startup latency.
        """
        return self._discover_devices("usb")

    def _discover_devices(self, transports: str) -> tuple[DeviceDescriptor, ...]:
        started = time.monotonic()
        try:
            contexts = tuple(self._native.scan_pluto_contexts(transports))
        except Exception as error:
            log_event(
                _LOGGER,
                "discovery",
                "pluto_discovery_failed",
                level=logging.ERROR,
                elapsed_ms=(time.monotonic() - started) * 1000.0,
                error=str(error),
                transports=transports,
            )
            raise RuntimeError(f"Pluto/libiio discovery failed: {error}") from error

        routes = tuple(self._descriptor_for_context(context) for context in contexts)
        devices = _merge_duplicate_pluto_routes(routes)
        self._devices = devices
        log_event(
            _LOGGER,
            "discovery",
            "pluto_discovery_completed",
            elapsed_ms=(time.monotonic() - started) * 1000.0,
            raw_contexts=len(contexts),
            logical_devices=len(devices),
            routes=[
                {
                    "transport": device.transport.value,
                    "uri": device.uri,
                    "alternate_uris": list(device.alternate_uris),
                    "identity": _safe_identity(device.identity_key),
                }
                for device in devices
            ],
            transports=transports,
        )
        return devices

    def select_device(self, device_id: str) -> LiveSnapshot:
        selected = next((device for device in self._devices if device.device_id == device_id), None)
        if selected is None:
            return self._fail(
                f"Unknown device: {device_id}",
                kind=LiveErrorKind.DEVICE_NOT_FOUND,
            )
        self._clear_selected_route()
        failures: list[dict[str, str]] = []
        for uri in _ordered_routes(selected):
            started = time.monotonic()
            try:
                # Probe with a temporary device and release its context
                # immediately.  Keeping the probe context open blocks the
                # fixed-band engine from opening the same USB device.
                temporary = self._native.PlutoDevice(uri, self._timeout_ms)
                try:
                    temporary.probe()
                finally:
                    try:
                        temporary.disconnect()
                    except Exception:
                        pass
                self._native_uri = uri
                selected = replace(
                    selected,
                    uri=uri,
                    transport=_transport_for_uri(uri),
                    alternate_uris=tuple(route for route in _ordered_routes(selected) if route != uri),
                )
                self._devices = tuple(
                    selected if item.device_id == device_id else item
                    for item in self._devices
                )
                log_event(
                    _LOGGER,
                    "device",
                    "pluto_route_selected",
                    uri=uri,
                    transport=selected.transport.value,
                    identity=_safe_identity(selected.identity_key),
                    elapsed_ms=(time.monotonic() - started) * 1000.0,
                    fallback_used=bool(failures),
                    failed_routes=failures,
                )
                return super().select_device(device_id)
            except Exception as error:
                failures.append({"uri": uri, "error": str(error)})
        self._clear_selected_route()
        return self._fail(
            f"Pluto connection failed for all discovered routes: {failures}",
            kind=LiveErrorKind.CONNECTION_FAILED,
        )

    def select_manual_uri(self, uri: str) -> LiveSnapshot:
        value = uri.strip()
        if not value:
            return self._fail(
                "Enter a USB or IP URI",
                kind=LiveErrorKind.DEVICE_NOT_FOUND,
            )
        try:
            descriptor = self._descriptor_for_uri(value, None)
        except Exception as error:
            return self._fail(
                f"Pluto connection failed for {value}: {error}",
                kind=LiveErrorKind.CONNECTION_FAILED,
            )
        self._devices = tuple(item for item in self._devices if item.device_id != descriptor.device_id) + (descriptor,)
        return self.select_device(descriptor.device_id)

    def apply_configuration(self, requested: LiveConfiguration) -> LiveSnapshot:
        log_event(
            _LOGGER,
            "configuration",
            "live_configuration_requested",
            device_id=self._snapshot.device.device_id if self._snapshot.device else None,
            uri=self._native_uri,
            center_hz=requested.center_hz,
            sample_rate_hz=requested.sample_rate_hz,
            analog_bandwidth_hz=requested.analog_bandwidth_hz,
            gain_db=requested.gain_db,
            fft_size=requested.fft_size,
            overlap_ratio=requested.overlap_ratio,
            window=requested.window,
            detector=requested.detector,
            averaging_frames=requested.averaging_frames,
            snapshot_rate_hz=requested.snapshot_rate_hz,
            backend=requested.backend.value,
        )
        snapshot = super().apply_configuration(requested)
        if snapshot.applied is not None:
            applied = snapshot.applied.applied
            log_event(
                _LOGGER,
                "configuration",
                "live_configuration_staged",
                device_id=snapshot.device.device_id if snapshot.device else None,
                uri=self._native_uri,
                requested_sample_rate_hz=requested.sample_rate_hz,
                applied_sample_rate_hz=applied.sample_rate_hz,
                requested_bandwidth_hz=requested.analog_bandwidth_hz,
                applied_bandwidth_hz=applied.analog_bandwidth_hz,
                requested_backend=requested.backend.value,
                staged_backend=applied.backend.value,
                adjustments=list(snapshot.applied.adjustments),
            )
        return snapshot

    def start_admitted(self) -> LiveSnapshot:
        """Atomically refuse an occupied engine before taking cleanup ownership.

        Uses the same authority as raw Start/Stop and Sweep lease acquisition.
        Unlike legacy Start, this path never recovers another error-state engine.
        Any exception after admission may own partial resources and needs Stop.
        """
        with self._recording_transaction_lock:
            with self._sweep_lease_lock:
                with self._lock:
                    if (self._sweep_lease_active or self._engine is not None
                            or self._snapshot.state is _running_state()):
                        raise LiveAdmissionRejected("Selected RX is already owned by another operation")
            return self._start_unlocked()

    def start(self) -> LiveSnapshot:
        """Start Live, applying a previously armed native recording request.

        The lock deliberately covers only user lifecycle commands.  It keeps a
        confirmed recording restart indivisible from a concurrent Start/Stop
        click, but no acquisition, DSP, native writer or poller operation
        waits on it.
        """

        with self._recording_transaction_lock:
            return self._start_unlocked()

    def _start_unlocked(self) -> LiveSnapshot:
        with self._sweep_lease_lock:
            if self._sweep_lease_active:
                return self._fail(
                    "Native Sweep owns the selected device; wait for Sweep to finish before starting Live",
                    kind=LiveErrorKind.INTERNAL,
                )
        # A native polling/configuration failure leaves an engine object that
        # must be joined and disconnected before retrying the same logical
        # receiver.  ERROR is therefore a recoverable restart boundary, not a
        # reason to reopen discovery.
        with self._lock:
            recover_error_engine = (
                self._engine is not None
                and self._snapshot.state is _error_state()
            )
        if recover_error_engine:
            self._release_stream(timeout_s=5.0)

        with self._lock:
            if self._native_uri is None:
                return self._fail(
                    "Select a device before starting a live session",
                    kind=LiveErrorKind.DEVICE_NOT_FOUND,
                )
            if self._snapshot.applied is None:
                return self._fail(
                    "Apply a live configuration before starting",
                    kind=LiveErrorKind.CONFIGURATION_REJECTED,
                )
            if self._engine is not None:
                return self._fail(
                    "Live session is already started",
                    kind=LiveErrorKind.INTERNAL,
                )
            requested = self._snapshot.applied.applied
            device = self._snapshot.device
            if device is None:
                return self._fail(
                    "Select a device before starting a live session",
                    kind=LiveErrorKind.DEVICE_NOT_FOUND,
                )
            routes = _start_routes(device, self._native_uri)
            recording_request = self._native_recording_armed
            log_event(
                _LOGGER,
                "stream",
                "live_start_requested",
                uri=self._native_uri,
                candidate_routes=list(routes),
                identity=_safe_identity(device.identity_key if device else None),
                center_hz=requested.center_hz,
                sample_rate_hz=requested.sample_rate_hz,
                analog_bandwidth_hz=requested.analog_bandwidth_hz,
                gain_db=requested.gain_db,
                fft_size=requested.fft_size,
                overlap_ratio=requested.overlap_ratio,
                window=requested.window,
                detector=requested.detector,
                averaging_frames=requested.averaging_frames,
                snapshot_rate_hz=requested.snapshot_rate_hz,
                backend=requested.backend.value,
            )

            engine = None
            applied = None
            engine_metrics = None
            successful_uri: str | None = None
            failures: list[dict[str, str]] = []
            for route in routes:
                candidate = None
                attempt_started = time.monotonic()
                try:
                    candidate = self._native.PlutoFixedBandEngine(route, self._timeout_ms)
                    candidate_applied = candidate.configure(
                        _native_fixed_band_config(
                            self._native,
                            requested,
                            route,
                            source_id=(device.device_id if device is not None else "native-live"),
                            recording_options=(
                                recording_request.options if recording_request is not None else None
                            ),
                            device_buffer_samples=self._device_buffer_samples,
                            allow_nonstandard_evidence_buffer_geometry=(
                                self._allow_nonstandard_evidence_buffer_geometry
                            ),
                        )
                    )
                    candidate.start()
                    try:
                        candidate_metrics = candidate.metrics()
                    except Exception:
                        candidate_metrics = candidate_applied
                except Exception as error:
                    if candidate is not None:
                        _shutdown_engine_instance(candidate)
                    message = str(error)
                    failures.append(
                        {
                            "uri": route,
                            "stage": _infer_failure_stage(message),
                            "error": message,
                        }
                    )
                    log_event(
                        _LOGGER,
                        "stream",
                        "live_route_start_failed",
                        level=logging.WARNING,
                        uri=route,
                        elapsed_ms=(time.monotonic() - attempt_started) * 1000.0,
                        stage=_infer_failure_stage(message),
                        error=message,
                    )
                    continue
                engine = candidate
                applied = candidate_applied
                engine_metrics = candidate_metrics
                successful_uri = route
                log_event(
                    _LOGGER,
                    "stream",
                    "live_route_start_succeeded",
                    uri=route,
                    elapsed_ms=(time.monotonic() - attempt_started) * 1000.0,
                    fallback_used=route != routes[0],
                )
                break

            if engine is None or applied is None or engine_metrics is None or successful_uri is None:
                message = failures[-1]["error"] if failures else "no usable Pluto route"
                error_kind = (
                    LiveErrorKind.CONFIGURATION_REJECTED
                    if any(
                        token in message.casefold()
                        for token in ("sampling_frequency", "bandwidth", "gain", "invalid argument")
                    )
                    else LiveErrorKind.CONNECTION_FAILED
                    if any(
                        item["stage"] == "open_context"
                        or "create_context" in item["error"].casefold()
                        for item in failures
                    )
                    else LiveErrorKind.STREAM_START_FAILED
                )
                log_event(
                    _LOGGER,
                    "stream",
                    "live_start_failed",
                    level=logging.ERROR,
                    uri=self._native_uri,
                    attempts=failures,
                    requested={
                        "center_hz": requested.center_hz,
                        "sample_rate_hz": requested.sample_rate_hz,
                        "analog_bandwidth_hz": requested.analog_bandwidth_hz,
                        "gain_db": requested.gain_db,
                        "fft_size": requested.fft_size,
                        "snapshot_rate_hz": requested.snapshot_rate_hz,
                        "backend": requested.backend.value,
                    },
                    error=message,
                )
                return self._fail(
                    f"Pluto RX start failed: {message}",
                    kind=error_kind,
                )

            self._native_uri = successful_uri
            if device is not None:
                all_routes = tuple(dict.fromkeys((successful_uri, *routes)))
                device = replace(
                    device,
                    uri=successful_uri,
                    transport=_transport_for_uri(successful_uri),
                    alternate_uris=tuple(route for route in all_routes if route != successful_uri),
                )
                self._devices = tuple(
                    device if item.device_id == device.device_id else item
                    for item in self._devices
                )
            self._engine = engine
            if recording_request is not None:
                self._native_recording_active = recording_request
                self._native_recording_armed = None
                self._native_recording_started_monotonic_s = time.monotonic()
                self._native_recording_restart_gap_duration_ns = (
                    max(0, time.time_ns() - recording_request.restart_gap_started_ns)
                    if recording_request.restart_gap_started_ns is not None
                    else None
                )
                self._open_native_recording_lifecycle(recording_request)
            self._last_native_frame_sequence = -1
            self._last_metrics_sample_s = time.monotonic()
            self._last_native_event_sample_s = self._last_metrics_sample_s
            self._last_metrics_log_s = self._last_metrics_sample_s
            self._last_metrics_frames = 0
            self._last_metrics_snapshots = 0
            self._last_metrics_samples = 0
            self._bridge_native_frames_polled = 0
            self._bridge_frames_coalesced = 0
            self._bridge_frames_published = 0
            self._generation += 1
            self._sequence = 0
            backend = _backend_kind(engine_metrics, fallback=applied)
            final_applied = _domain_applied_configuration(
                self._snapshot.applied,
                native_applied=applied,
                active_backend=backend,
            )
            fallback_reason = _backend_fallback_reason(engine_metrics, final_applied)
            self._snapshot = LiveSnapshot(
                generation=as_configuration_generation(self._generation),
                sequence=as_frame_sequence(0),
                state=_running_state(),
                device=device,
                applied=final_applied,
                quality=LiveQuality(
                    calibration=_calibration_quality(applied),
                    backend=backend,
                    fallback_reason=fallback_reason,
                ),
                unit=_spectrum_unit(applied),
                error=None,
                error_kind=None,
                session_id=self._session_id,
                active_source_id=as_source_id(device.device_id),
                acquisition_epoch=int(self._generation),
                clock_domain="unix_ns",
                active_config_generation=(
                    int(applied.config_generation)
                    if type(getattr(applied, "config_generation", None)) is int else None
                ),
            )
            self._stop_event.clear()
            self._poller = threading.Thread(
                target=self._poll_loop,
                name="sdr-native-live-poller",
                daemon=True,
            )
            self._poller.start()
            log_event(
                _LOGGER,
                "configuration",
                "live_configuration_applied",
                uri=self._native_uri,
                requested={
                    "center_hz": final_applied.requested.center_hz,
                    "sample_rate_hz": final_applied.requested.sample_rate_hz,
                    "analog_bandwidth_hz": final_applied.requested.analog_bandwidth_hz,
                    "gain_db": final_applied.requested.gain_db,
                    "backend": final_applied.requested.backend.value,
                },
                applied={
                    "center_hz": final_applied.applied.center_hz,
                    "sample_rate_hz": final_applied.applied.sample_rate_hz,
                    "analog_bandwidth_hz": final_applied.applied.analog_bandwidth_hz,
                    "gain_db": final_applied.applied.gain_db,
                    "backend": final_applied.applied.backend.value,
                },
                adjustments=list(final_applied.adjustments),
            )
            log_event(
                _LOGGER,
                "device",
                "live_started",
                uri=self._native_uri,
                backend=backend.value,
                fft_size=final_applied.applied.fft_size,
                hop_size=max(
                    1,
                    int(
                        round(
                            final_applied.applied.fft_size
                            * (1.0 - final_applied.applied.overlap_ratio)
                        )
                    ),
                ),
                snapshot_rate_hz=final_applied.applied.snapshot_rate_hz,
            )
            return self._snapshot

    def stop(self) -> LiveSnapshot:
        with self._recording_transaction_lock:
            return self._stop_unlocked()

    def _stop_unlocked(self) -> LiveSnapshot:
        with self._lock:
            stopped_uri = self._native_uri
        self._release_stream(timeout_s=5.0)
        with self._lock:
            snapshot = super().stop()
        # Keep the selected logical device and route. Stop only releases the
        # active stream/engine; clearing the URI here forced every subsequent
        # Start to reopen device discovery.
        log_event(
            _LOGGER,
            "stream",
            "live_stopped",
            uri=stopped_uri,
            device_id=snapshot.device.device_id if snapshot.device else None,
        )
        return snapshot

    def close_live(self) -> None:
        self.stop()
        with self._lock:
            self._clear_selected_route()

    def stop_and_wait(self, timeout_s: float) -> None:
        # Serialize the terminal cleanup with Start/Stop and Sweep admission.
        # Otherwise shutdown could observe no engine while a Start was still
        # constructing it, return, and let that Start publish a live engine
        # after the application had already completed its cleanup pass.
        with self._recording_transaction_lock:
            self._release_stream(timeout_s=max(0.0, timeout_s))
            with self._lock:
                super().stop()
                # Application shutdown may clear the route after all workers have
                # joined. A regular Stop intentionally preserves it.
                self._clear_selected_route()

    def _release_stream(self, *, timeout_s: float) -> None:
        """Stop/join/disconnect the engine without holding the snapshot lock."""

        with self._lock:
            self._stop_event.set()
            poller = self._poller
            engine = self._engine
            self._poller = None
            self._engine = None

        if engine is not None:
            try:
                engine.request_stop()
            except Exception:
                pass
        if poller is not None and poller is not threading.current_thread():
            poller.join(timeout=timeout_s)
        if engine is not None:
            try:
                engine.join()
            except Exception:
                pass
            # Join finalizes native writer manifests before this low-rate
            # metrics read.  Capture it before disconnect destroys the engine
            # object, then atomically finish the companion lifecycle evidence.
            self._capture_native_recording_completion(engine)
            try:
                engine.disconnect()
            except Exception:
                pass

    def poll_frames(self) -> list[LiveSnapshot]:
        with self._lock:
            return [self._snapshot] if self._snapshot.state is _running_state() else []

    def poll_live_metrics(self, timeout_s: float) -> LiveSnapshot:
        del timeout_s
        with self._lock:
            return self._snapshot

    def latest_snapshot(self) -> LiveSnapshot:
        with self._lock:
            return self._snapshot

    def is_running(self) -> bool:
        with self._lock:
            return self._snapshot.state is _running_state()

    # ---- R10-B exclusive native Sweep lease -----------------------------

    def acquire_native_sweep_lease(self) -> Any:
        """Lease the selected stopped CPU route for one NativeSweepService.

        The caller receives no engine, raw I/Q or mutable Live state.  It can
        only construct the R10-B adapter, whose completion releases this lease.
        Live/recording commands cannot quietly compete for the device while it
        exists.  The lease acquisition itself performs no hardware I/O.
        """

        from .native_sweep import NativeSweepLease, NativeSweepSource

        with self._recording_transaction_lock:
            with self._sweep_lease_lock:
                if self._sweep_lease_active:
                    raise RuntimeError("native sweep already owns the selected device")
                with self._lock:
                    snapshot = self._snapshot
                    uri = self._native_uri
                    recording_active = self._native_recording_active is not None
                    recording_armed = self._native_recording_armed is not None
                    engine_present = self._engine is not None
                if snapshot.state is _running_state() or engine_present:
                    raise RuntimeError("stop Live before acquiring the native sweep lease")
                if recording_active or recording_armed:
                    raise RuntimeError("native RTBW recording must be stopped/unarmed before Sweep")
                if uri is None or snapshot.device is None or snapshot.applied is None:
                    raise RuntimeError("select a device and apply a CPU Live configuration before Sweep")
                if snapshot.applied.applied.backend is not BackendKind.CPU:
                    raise RuntimeError("native Sweep currently requires an explicitly applied CPU Live configuration")
                source = NativeSweepSource(
                    uri,
                    f"native-sweep:{snapshot.device.device_id}",
                    snapshot.applied.applied,
                )
                self._sweep_lease_active = True
                return NativeSweepLease(
                    self._native,
                    source,
                    self._assert_native_sweep_lease,
                    self._release_native_sweep_lease,
                )

    def _assert_native_sweep_lease(self) -> None:
        with self._sweep_lease_lock:
            if not self._sweep_lease_active:
                raise RuntimeError("native sweep lease is no longer active")
            with self._lock:
                if self._engine is not None or self._snapshot.state is _running_state():
                    raise RuntimeError("Live owns the device; native sweep lease is invalid")

    def _release_native_sweep_lease(self) -> None:
        with self._sweep_lease_lock:
            self._sweep_lease_active = False

    # ---- R08-C1 native RTBW recording lifecycle -------------------------

    def arm_native_recording(self, options: RecordingOptions) -> RecordingHealth:
        """Arm native capture for a future Live start without touching Live.

        This is the safe default.  In particular, an already running RTBW
        engine retains its immutable configuration and keeps delivering data;
        no recorder is silently injected into its hot path.
        """

        with self._recording_transaction_lock:
            if self._native_recording_active is not None:
                raise RuntimeError("native recording is already active")
            self._native_recording_epoch += 1
            self._native_recording_armed = _NativeRecordingRequest(
                options=options,
                epoch=self._native_recording_epoch,
                armed_at_ns=time.time_ns(),
                start_reason="arm_next_live_start",
            )
            self._native_recording_restart_gap_duration_ns = None
            self._native_recording_lifecycle_error = ""
            log_event(
                _LOGGER,
                "recording",
                "native_recording_armed",
                output_uri=options.output_path,
                epoch=self._native_recording_epoch,
                live_running=self.is_running(),
                mode="rtbw",
            )
            return self.native_recording_health()

    def start_native_recording_now(self, options: RecordingOptions) -> RecordingHealth:
        """Explicitly restart RTBW Live and begin a new recorded epoch.

        The caller is responsible for user confirmation.  This method never
        runs as a side effect of ``arm_native_recording`` and emits both an
        activity event and a durable lifecycle sidecar describing the control
        transaction gap.
        """

        with self._recording_transaction_lock:
            if self._native_recording_active is not None:
                raise RuntimeError("native recording is already active")
            was_running = self.is_running()
            restart_started_ns = time.time_ns() if was_running else None
            self._native_recording_epoch += 1
            self._native_recording_armed = _NativeRecordingRequest(
                options=options,
                epoch=self._native_recording_epoch,
                armed_at_ns=time.time_ns(),
                start_reason=("controlled_live_restart" if was_running else "start_live_for_recording"),
                restart_gap_started_ns=restart_started_ns,
            )
            self._native_recording_restart_gap_duration_ns = None
            self._native_recording_lifecycle_error = ""
            if was_running:
                log_event(
                    _LOGGER,
                    "recording",
                    "native_recording_restart_requested",
                    output_uri=options.output_path,
                    epoch=self._native_recording_epoch,
                    mode="rtbw",
                )
                self._stop_unlocked()
            snapshot = self._start_unlocked()
            if snapshot.error is not None:
                raise RuntimeError(f"native recording start failed: {snapshot.error}")
            return self.native_recording_health()

    def stop_native_recording(self) -> RecordingHealth:
        """Finalize the active native capture by explicitly stopping Live.

        Current R08 writers are configuration-bound.  Therefore this action
        stops Live instead of pretending that the writer can be detached from
        an engine in place.  A later writer-only reconfigure action must be a
        separately confirmed UX decision.
        """

        with self._recording_transaction_lock:
            if self._native_recording_active is not None:
                self._stop_unlocked()
            elif self._native_recording_armed is not None:
                self._native_recording_armed = None
                self._last_native_recording_health = RecordingHealth(
                    RecordingState.IDLE, 0, 0, 0, 0, 0, 0, 0, recording_mode="rtbw"
                )
            return self.native_recording_health()

    def native_recording_health(self) -> RecordingHealth:
        """Return a low-rate health snapshot for the native RTBW writer."""

        with self._lock:
            active = self._native_recording_active
            armed = self._native_recording_armed
            engine = self._engine
        if active is not None and engine is not None:
            try:
                return self._native_recording_health_from_metrics(active, engine.metrics())
            except Exception as error:
                return RecordingHealth(
                    RecordingState.FAILED,
                    0,
                    active.options.queue_capacity,
                    0,
                    0,
                    0,
                    1,
                    0,
                    output_path=active.options.output_path,
                    error=f"native recording metrics unavailable: {error}",
                    recording_mode="rtbw",
                    epoch=active.epoch,
                    restart_gap_duration_ns=self._native_recording_restart_gap_duration_ns,
                )
        if armed is not None:
            return RecordingHealth(
                RecordingState.ARMED,
                0,
                armed.options.queue_capacity,
                0,
                0,
                0,
                0,
                0,
                output_path=armed.options.output_path,
                recording_mode="rtbw",
                epoch=armed.epoch,
            )
        return self._last_native_recording_health

    def _native_recording_health_from_metrics(
        self,
        request: _NativeRecordingRequest,
        metrics: Any,
        *,
        completed: bool = False,
    ) -> RecordingHealth:
        """Map separately-accounted native recorder counters to UI health."""

        queue = getattr(metrics, "recorder_queue", None)
        queue_depth = int(getattr(queue, "depth", 0) or 0)
        queue_capacity = int(getattr(queue, "capacity", request.options.queue_capacity) or request.options.queue_capacity)
        written_blocks = int(getattr(metrics, "recorder_writer_blocks_written", 0) or 0)
        written_samples = int(getattr(metrics, "recorder_writer_samples_written", 0) or 0)
        written_bytes = int(getattr(metrics, "recorder_writer_bytes_written", 0) or 0)
        dropped_blocks = (
            int(getattr(metrics, "recorder_queue_blocks_dropped", 0) or 0)
            + int(getattr(metrics, "recorder_writer_blocks_unavailable", 0) or 0)
            + int(getattr(metrics, "recorder_shutdown_blocks_discarded", 0) or 0)
        )
        dropped_samples = (
            int(getattr(metrics, "recorder_queue_samples_dropped", 0) or 0)
            + int(getattr(metrics, "recorder_writer_samples_unavailable", 0) or 0)
            + int(getattr(metrics, "recorder_shutdown_samples_discarded", 0) or 0)
        )
        spectrum_drops = (
            int(getattr(metrics, "spectrum_recorder_frames_dropped", 0) or 0)
            + int(getattr(metrics, "spectrum_recorder_frames_unavailable", 0) or 0)
            + int(getattr(metrics, "spectrum_recorder_shutdown_frames_discarded", 0) or 0)
        )
        writer_failed = bool(getattr(metrics, "recorder_writer_failed", False)) or bool(
            getattr(metrics, "spectrum_writer_failed", False)
        )
        elapsed = max(0.0, time.monotonic() - (self._native_recording_started_monotonic_s or time.monotonic()))
        average_rate = written_samples / elapsed if elapsed > 0.0 else 0.0
        attempted_samples = written_samples + dropped_samples
        loss_rate = dropped_samples / attempted_samples if attempted_samples else 0.0
        disk_free: int | None = None
        try:
            disk_free = shutil.disk_usage(Path(request.options.output_path).parent or Path.cwd()).free
        except OSError:
            pass
        if writer_failed:
            state = RecordingState.FAILED
        elif completed:
            state = RecordingState.COMPLETED
        else:
            state = RecordingState.RECORDING
        reasons: tuple[LossReason, ...] = ()
        if dropped_blocks or spectrum_drops:
            reasons = (LossReason.RECORDER,)
        error = self._native_recording_lifecycle_error
        if writer_failed:
            error = error or "native recorder writer failed; inspect partial artifacts"
        return RecordingHealth(
            state,
            queue_depth,
            queue_capacity,
            written_blocks,
            int(getattr(metrics, "spectrum_writer_frames_written", 0) or 0),
            dropped_blocks + spectrum_drops,
            (1 if self._native_recording_restart_gap_duration_ns is not None else 0),
            written_bytes + int(getattr(metrics, "spectrum_writer_bytes_written", 0) or 0),
            output_path=request.options.output_path,
            error=error,
            disk_free_bytes=disk_free,
            drop_reasons=reasons,
            recording_mode="rtbw",
            epoch=request.epoch,
            recorded_iq_samples=written_samples,
            average_iq_sample_rate_hz=average_rate,
            iq_loss_rate=loss_rate,
            restart_gap_duration_ns=self._native_recording_restart_gap_duration_ns,
        )

    def _capture_native_recording_completion(self, engine: Any) -> None:
        with self._lock:
            request = self._native_recording_active
        if request is None:
            return
        try:
            completed = self._native_recording_health_from_metrics(request, engine.metrics(), completed=True)
        except Exception as error:
            completed = RecordingHealth(
                RecordingState.FAILED, 0, request.options.queue_capacity, 0, 0, 0, 1, 0,
                output_path=request.options.output_path,
                error=f"native recording completion metrics unavailable: {error}",
                recording_mode="rtbw",
                epoch=request.epoch,
                restart_gap_duration_ns=self._native_recording_restart_gap_duration_ns,
            )
        with self._lock:
            self._last_native_recording_health = completed
            self._native_recording_active = None
            self._native_recording_started_monotonic_s = None
        self._finalize_native_recording_lifecycle(completed)

    def _open_native_recording_lifecycle(self, request: _NativeRecordingRequest) -> None:
        """Write one durable control-plane event, never raw I/Q, per epoch."""

        base = _native_recording_base_path(request.options.output_path)
        part = Path(f"{base}.sdr-lifecycle.jsonl.part")
        try:
            handle = part.open("x", encoding="utf-8", newline="\n")
            ended_ns = time.time_ns()
            payload = {
                "schema": "sdr-native-recording-lifecycle",
                "schema_version": 1,
                "type": "epoch_start",
                "epoch": request.epoch,
                "mode": "rtbw",
                "reason": request.start_reason,
                "armed_at_ns": request.armed_at_ns,
                "gap_scope": "live_control_transaction" if request.restart_gap_started_ns is not None else None,
                "gap_started_ns": request.restart_gap_started_ns,
                "gap_ended_ns": ended_ns if request.restart_gap_started_ns is not None else None,
                "gap_duration_ns": self._native_recording_restart_gap_duration_ns,
            }
            handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
            handle.flush()
            self._native_recording_lifecycle_part = part
            self._native_recording_lifecycle_handle = handle
        except OSError as error:
            self._native_recording_lifecycle_error = (
                f"native recording lifecycle evidence could not be written: {error}"
            )
            log_event(
                _LOGGER,
                "recording",
                "native_recording_lifecycle_write_failed",
                level=logging.ERROR,
                output_uri=request.options.output_path,
                epoch=request.epoch,
                error=str(error),
            )

    def _finalize_native_recording_lifecycle(self, health: RecordingHealth) -> None:
        handle = self._native_recording_lifecycle_handle
        part = self._native_recording_lifecycle_part
        self._native_recording_lifecycle_handle = None
        self._native_recording_lifecycle_part = None
        if handle is None or part is None:
            return
        try:
            handle.write(json.dumps({
                "schema": "sdr-native-recording-lifecycle",
                "schema_version": 1,
                "type": "epoch_end",
                "epoch": health.epoch,
                "ended_at_ns": time.time_ns(),
                "state": health.state.value,
                "recorded_iq_samples": health.recorded_iq_samples,
                "iq_loss_rate": health.iq_loss_rate,
            }, separators=(",", ":")) + "\n")
            handle.flush()
            handle.close()
            part.rename(part.with_suffix(""))
        except OSError as error:
            self._native_recording_lifecycle_error = (
                f"native recording lifecycle finalization failed: {error}"
            )
            try:
                handle.close()
            except OSError:
                pass

    def _poll_loop(self) -> None:
        engine = self._engine
        if engine is None:
            return
        last_frame_at = time.monotonic()
        while not self._stop_event.is_set():
            try:
                latest_drain = getattr(engine, "drain_latest_spectrum_frame", None)
                if latest_drain is not None:
                    drained = latest_drain()
                    frame = getattr(drained, "frame", None)
                    coalesced = int(getattr(drained, "coalesced_frames", 0) or 0)
                    if coalesced < 0:
                        raise RuntimeError("native latest-spectrum coalescing count is negative")
                    if frame is None and coalesced:
                        raise RuntimeError(
                            "native latest-spectrum drain returned coalesced frames without a frame"
                        )
                    if frame is None:
                        native_frames = 0
                    else:
                        native_frames = coalesced + 1
                else:
                    # Compatibility only for an already-built native module.
                    # Fresh builds must take the one-frame native path above.
                    frames = engine.poll_spectrum_frames(32)
                    frame = frames[-1] if frames else None
                    native_frames = len(frames)
            except Exception as error:
                self._publish_error(f"Pluto RX polling failed: {error}")
                return
            if frame is not None:
                self._record_bridge_batch(native_frames, self._publish_frame(frame, expected_engine=engine))
                last_frame_at = time.monotonic()
            elif self._engine_state_is_error(engine):
                self._publish_error("Pluto RX engine entered error state")
                return
            elif time.monotonic() - last_frame_at > _STALL_TIMEOUT_S:
                # No frame arrived for a while: the device/stream is stalled.
                # Surface it instead of leaving the UI stuck in RUNNING.
                self._publish_error("Pluto RX stream stalled (no frames)")
                return
            try:
                persistence = engine.poll_persistence_snapshots(2)
                if persistence:
                    self._publish_persistence(persistence[-1], expected_engine=engine)
            except Exception:
                # Persistence is best-effort: a failure must not kill the
                # spectrum path.
                pass
            now = time.monotonic()
            if now - self._last_native_event_sample_s >= _NATIVE_EVENT_SAMPLE_INTERVAL_S:
                self._drain_native_events(engine, now)
            if now - self._last_metrics_sample_s >= _METRICS_SAMPLE_INTERVAL_S:
                self._sample_performance(engine, now)
            self._stop_event.wait(_POLL_INTERVAL_S)

    def _drain_native_events(self, engine: Any, now_s: float) -> None:
        """Mirror bounded native diagnostics into the structured activity log."""

        self._last_native_event_sample_s = now_s
        poll = getattr(engine, "poll_events", None)
        if poll is None:
            return
        try:
            events = tuple(poll(_EVENT_QUEUE_CAPACITY))
        except Exception as error:
            log_event(
                _LOGGER,
                "native",
                "native_event_poll_failed",
                level=logging.WARNING,
                uri=self._native_uri,
                error=str(error),
            )
            return
        for event in events:
            severity = str(getattr(getattr(event, "severity", None), "name", "INFO")).upper()
            level = {
                "TRACE": logging.DEBUG,
                "DEBUG": logging.DEBUG,
                "INFO": logging.INFO,
                "WARNING": logging.WARNING,
                "WARN": logging.WARNING,
                "ERROR": logging.ERROR,
                "CRITICAL": logging.CRITICAL,
            }.get(severity, logging.INFO)
            log_event(
                _LOGGER,
                "native",
                "native_diagnostic_event",
                level=level,
                uri=self._native_uri,
                native_sequence=int(getattr(event, "sequence", 0) or 0),
                native_timestamp_ns=int(getattr(event, "timestamp_ns", 0) or 0),
                severity=severity,
                code=str(getattr(event, "code", "unknown")),
                message=str(getattr(event, "message", "")),
            )

    def _sample_performance(self, engine: Any, now_s: float) -> None:
        """Publish native analytical metrics without slowing the data plane."""
        try:
            metrics = engine.metrics()
            engine_metrics = getattr(metrics, "engine", metrics)
        except Exception as error:
            log_event(
                _LOGGER,
                "performance",
                "native_metrics_read_failed",
                level=logging.WARNING,
                error=str(error),
            )
            self._last_metrics_sample_s = now_s
            return

        elapsed = max(1e-9, now_s - self._last_metrics_sample_s)
        device_metrics = getattr(metrics, "device", None)
        spectrum_queue_metrics = getattr(metrics, "spectrum_queue", None)
        with self._lock:
            bridge_native_frames_polled = self._bridge_native_frames_polled
            bridge_frames_coalesced = self._bridge_frames_coalesced
            bridge_frames_published = self._bridge_frames_published
        fft_frames = int(getattr(engine_metrics, "fft_frames_computed", 0) or 0)
        snapshots = int(getattr(engine_metrics, "spectrum_snapshots_emitted", 0) or 0)
        samples = int(getattr(engine_metrics, "iq_samples_received", 0) or 0)
        snapshot_rate = max(0.0, (snapshots - self._last_metrics_snapshots) / elapsed)
        iq_rate = max(0.0, (samples - self._last_metrics_samples) / elapsed)
        analytical_rate = float(getattr(engine_metrics, "analytical_fft_rate", 0.0) or 0.0)
        performance = LivePerformance(
            rate_observation_interval_s=(elapsed if self._last_metrics_sample_s > 0
                and all(hasattr(engine_metrics, name) for name in (
                    "analytical_fft_rate", "spectrum_snapshots_emitted", "iq_samples_received"
                )) else None),
            analytical_fft_rate_hz=analytical_rate,
            spectrum_snapshot_rate_hz=snapshot_rate,
            iq_sample_rate_hz=iq_rate,
            fft_frames_computed=fft_frames,
            fft_frames_dropped=int(getattr(engine_metrics, "fft_frames_dropped", 0) or 0),
            iq_samples_dropped=int(getattr(engine_metrics, "iq_samples_dropped", 0) or 0),
            iq_blocks_dropped=int(getattr(engine_metrics, "iq_blocks_dropped", 0) or 0),
            source_samples_dropped=int(
                getattr(device_metrics, "estimated_dropped_samples", 0) or 0
            ),
            source_blocks_dropped=int(
                getattr(device_metrics, "output_blocks_dropped", 0) or 0
            ),
            acquisition_queue_samples_dropped=int(
                getattr(metrics, "acquisition_queue_samples_dropped", 0) or 0
            ),
            acquisition_queue_blocks_dropped=int(
                getattr(metrics, "acquisition_queue_blocks_dropped", 0) or 0
            ),
            snapshots_emitted=snapshots,
            snapshots_superseded=int(getattr(metrics, "spectrum_snapshots_superseded", 0) or 0),
            persistence_updates=int(getattr(engine_metrics, "persistence_updates", 0) or 0),
            persistence_snapshots_superseded=int(
                getattr(metrics, "persistence_snapshots_superseded", 0) or 0
            ),
            acquisition_queue_depth=int(getattr(engine_metrics, "acquisition_queue_depth", 0) or 0),
            spectrum_queue_depth=int(
                getattr(
                    spectrum_queue_metrics,
                    "depth",
                    getattr(engine_metrics, "dsp_queue_depth", 0),
                )
                or 0
            ),
            cpu_processing_ms=float(getattr(engine_metrics, "cpu_processing_ms", 0.0) or 0.0),
            gpu_processing_ms=float(getattr(engine_metrics, "gpu_processing_ms", 0.0) or 0.0),
            h2d_ms=float(getattr(engine_metrics, "h2d_ms", 0.0) or 0.0),
            d2h_ms=float(getattr(engine_metrics, "d2h_ms", 0.0) or 0.0),
            end_to_end_latency_ms=float(getattr(engine_metrics, "end_to_end_latency_ms", 0.0) or 0.0),
            stage_timing_mask=int(getattr(engine_metrics, "stage_timing_mask", 0) or 0),
            input_unpack_ms=float(getattr(engine_metrics, "input_unpack_ms", 0.0) or 0.0),
            window_ms=float(getattr(engine_metrics, "window_ms", 0.0) or 0.0),
            fft_ms=float(getattr(engine_metrics, "fft_ms", 0.0) or 0.0),
            detector_ms=float(getattr(engine_metrics, "detector_ms", 0.0) or 0.0),
            persistence_processing_ms=float(
                getattr(engine_metrics, "persistence_processing_ms", 0.0) or 0.0
            ),
            publication_processing_ms=float(
                getattr(engine_metrics, "publication_processing_ms", 0.0) or 0.0
            ),
            bridge_native_frames_polled=bridge_native_frames_polled,
            bridge_frames_coalesced=bridge_frames_coalesced,
            bridge_frames_published=bridge_frames_published,
            source_sequence_discontinuities=int(
                getattr(metrics, "source_sequence_discontinuities", 0) or 0
            ),
            source_sample_index_discontinuities=int(
                getattr(metrics, "source_sample_index_discontinuities", 0) or 0
            ),
            source_timestamp_regressions=int(
                getattr(metrics, "source_timestamp_regressions", 0) or 0
            ),
            source_estimated_timestamp_blocks=int(
                getattr(metrics, "source_estimated_timestamp_blocks", 0) or 0
            ),
            hardware_overflow_counter_available=bool(
                getattr(metrics, "hardware_overflow_counter_available", False)
            ),
            source_refill_wait_ms=float(
                getattr(device_metrics, "refill_wait_ns", 0) or 0
            ) / 1.0e6,
            source_canonicalization_ms=float(
                getattr(device_metrics, "canonicalization_ns", 0) or 0
            ) / 1.0e6,
            source_refill_calls=int(getattr(device_metrics, "refill_calls", 0) or 0),
            source_refill_wait_over_nominal_period=int(
                getattr(device_metrics, "refill_wait_over_nominal_period", 0) or 0
            ),
            source_refill_wait_over_two_nominal_periods=int(
                getattr(device_metrics, "refill_wait_over_two_nominal_periods", 0) or 0
            ),
            source_inter_refill_gap_ms=float(
                getattr(device_metrics, "inter_refill_gap_ns", 0) or 0
            ) / 1.0e6,
            source_inter_refill_gap_count=int(
                getattr(device_metrics, "inter_refill_gap_count", 0) or 0
            ),
        )
        with self._lock:
            self._snapshot = replace(self._snapshot, performance=performance)

        self._last_metrics_sample_s = now_s
        self._last_metrics_frames = fft_frames
        self._last_metrics_snapshots = snapshots
        self._last_metrics_samples = samples

        if now_s - self._last_metrics_log_s >= _PERFORMANCE_LOG_INTERVAL_S:
            log_event(
                _LOGGER,
                "performance",
                "live_performance_snapshot",
                uri=self._native_uri,
                analytical_fft_rate_hz=performance.analytical_fft_rate_hz,
                spectrum_snapshot_rate_hz=performance.spectrum_snapshot_rate_hz,
                iq_sample_rate_hz=performance.iq_sample_rate_hz,
                fft_frames_computed=performance.fft_frames_computed,
                fft_frames_dropped=performance.fft_frames_dropped,
                iq_samples_dropped=performance.iq_samples_dropped,
                iq_blocks_dropped=performance.iq_blocks_dropped,
                source_samples_dropped=performance.source_samples_dropped,
                source_blocks_dropped=performance.source_blocks_dropped,
                acquisition_queue_samples_dropped=(
                    performance.acquisition_queue_samples_dropped
                ),
                acquisition_queue_blocks_dropped=(
                    performance.acquisition_queue_blocks_dropped
                ),
                snapshots_emitted=performance.snapshots_emitted,
                snapshots_superseded=performance.snapshots_superseded,
                persistence_updates=performance.persistence_updates,
                persistence_snapshots_superseded=performance.persistence_snapshots_superseded,
                acquisition_queue_depth=performance.acquisition_queue_depth,
                spectrum_queue_depth=performance.spectrum_queue_depth,
                cpu_processing_ms=performance.cpu_processing_ms,
                gpu_processing_ms=performance.gpu_processing_ms,
                end_to_end_latency_ms=performance.end_to_end_latency_ms,
                stage_timing_mask=performance.stage_timing_mask,
                input_unpack_ms=performance.input_unpack_ms,
                window_ms=performance.window_ms,
                fft_ms=performance.fft_ms,
                detector_ms=performance.detector_ms,
                persistence_processing_ms=performance.persistence_processing_ms,
                publication_processing_ms=performance.publication_processing_ms,
            )
            self._last_metrics_log_s = now_s

    @staticmethod
    def _engine_state_is_error(engine: Any) -> bool:
        """True when the native engine reports ERROR state or has_error."""
        try:
            state_name = str(getattr(engine.state(), "name", "")).upper()
            if state_name == "ERROR":
                return True
            metrics = engine.metrics()
            return bool(getattr(metrics, "has_error", False))
        except Exception:
            return False

    def _record_bridge_batch(self, frame_count: int, published: bool) -> None:
        """Account the bridge's existing latest-wins choice without buffering.

        Native output-queue supersession is owned by native metrics.  This
        separate counter counts only frames returned in one bounded poll that
        the bridge intentionally replaces with the newest frame.
        """

        if frame_count < 1:
            return
        with self._lock:
            self._bridge_native_frames_polled += frame_count
            self._bridge_frames_coalesced += max(0, frame_count - 1)
            if published:
                self._bridge_frames_published += 1

    def _publish_frame(self, frame: Any, *, expected_engine: Any = None) -> bool:
        native_sequence = int(frame.frame_sequence)
        with self._lock:
            if expected_engine is not None and expected_engine is not self._engine:
                return False
            publication_context = self._snapshot
            if native_sequence <= self._last_native_frame_sequence:
                return False
            self._last_native_frame_sequence = native_sequence
        try:
            fallback_source = self._snapshot.device.device_id if self._snapshot.device is not None else "native-live"
            source_id, generation, timestamp_quality, loss_reasons = _native_frame_metadata(
                self._native, frame, fallback_source
            )
            backend_fallback = _native_quality_mask(self._native, frame, "BACKEND_FALLBACK")
            backend_discontinuity = _native_quality_mask(
                self._native, frame, "BACKEND_DISCONTINUITY"
            )
            provenance = native_spectrum_provenance(frame)
            unit = _native_spectrum_unit(frame.unit)
            validate_absolute_unit(unit, provenance)
            spectrum = LiveSpectrumFrame(
                sequence=as_frame_sequence(frame.frame_sequence),
                timestamp_ns=as_timestamp_ns(frame.timestamp_ns),
                center_frequency_hz=float(frame.center_frequency_hz),
                sample_rate_hz=float(frame.sample_rate_hz),
                fft_size=int(frame.fft_size),
                hop_size=int(frame.hop_size),
                frequencies_hz=frame.frequencies_hz,
                values=frame.values,
                unit=unit,
                dropped_samples_before=int(frame.dropped_samples_before),
                dropped_iq_blocks_before=int(frame.dropped_iq_blocks_before),
                dropped_fft_frames_before=int(frame.dropped_fft_frames_before),
                source_id=source_id,
                config_generation=generation,
                timestamp_quality=timestamp_quality,
                loss_reasons=loss_reasons,
                backend_fallback=backend_fallback,
                backend_discontinuity=backend_discontinuity,
                native_quality_flags=(int(frame.quality_flags)
                                      if getattr(frame, "quality_flags", None) is not None else None),
                acquisition_epoch=publication_context.acquisition_epoch,
                clock_domain=publication_context.clock_domain,
                numerical_provenance=provenance,
            )
        except Exception as error:
            self._publish_error(f"Pluto RX frame conversion failed: {error}")
            return False
        with self._lock:
            if expected_engine is not None and expected_engine is not self._engine:
                return False
            self._sequence += 1
            dropped = self._engine_drop_count()
            current_quality = self._snapshot.quality
            backend = current_quality.backend
            fallback_reason = current_quality.fallback_reason
            if backend_fallback:
                try:
                    backend_metrics = self._engine.metrics() if self._engine is not None else None
                    backend = _backend_kind(backend_metrics)
                    applied = self._snapshot.applied
                    if applied is not None:
                        fallback_reason = _backend_fallback_reason(backend_metrics, applied)
                except Exception:
                    # Native emits this marker only for the CUDA-to-CPU
                    # failover path.  A metrics read failure must not hide the
                    # transition from the application boundary.
                    backend = BackendKind.CPU
                if fallback_reason is None:
                    fallback_reason = "backend fallback"
            self._snapshot = LiveSnapshot(
                generation=self._snapshot.generation,
                sequence=as_frame_sequence(self._sequence),
                state=_running_state(),
                device=self._snapshot.device,
                applied=self._snapshot.applied,
                quality=LiveQuality(
                    calibration=_frame_calibration_quality(provenance.calibration_status),
                    backend=backend,
                    fallback_reason=fallback_reason,
                    backend_discontinuity=backend_discontinuity,
                    dropped_blocks=dropped,
                    loss_reasons=loss_reasons,
                ),
                unit=spectrum.unit,
                spectrum=spectrum,
                persistence=self._snapshot.persistence,
                performance=self._snapshot.performance,
                session_id=self._snapshot.session_id,
                active_source_id=self._snapshot.active_source_id,
                acquisition_epoch=self._snapshot.acquisition_epoch,
                clock_domain=self._snapshot.clock_domain,
                active_config_generation=self._snapshot.active_config_generation,
            )
        return True

    def _publish_persistence(self, value: Any, *, expected_engine: Any = None) -> None:
        with self._lock:
            if expected_engine is not None and expected_engine is not self._engine:
                return
            publication_context = self._snapshot
        try:
            source = getattr(value, "source_id", None)
            native_generation = getattr(value, "config_generation", None)
            native_unit = getattr(value, "unit", None)
            identity_available = source is not None and native_generation is not None
            source_id = as_source_id(source if source is not None else "unknown")
            generation = as_configuration_generation(native_generation if native_generation is not None else 0)
            timestamp_quality = TimestampQuality.UNKNOWN
            persistence = LivePersistenceFrame(
                update_sequence=as_frame_sequence(value.update_sequence),
                timestamp_ns=as_timestamp_ns(value.timestamp_ns),
                source_frame_sequence=as_frame_sequence(value.source_frame_sequence),
                power_min_db=float(value.power_min_db),
                power_max_db=float(value.power_max_db),
                power_bins=int(value.power_bins),
                frequency_bins=int(value.frequency_bins),
                processed_frames=int(value.processed_frames),
                exponential_decay=bool(value.exponential_decay),
                frequencies_hz=value.frequencies_hz,
                density=value.density,
                probability_scale=float(getattr(value, "probability_scale", 1.0)),
                count_scale=float(getattr(value, "count_scale", 1.0)),
                unit=(_native_spectrum_unit(native_unit) if native_unit is not None else None),
                producer_identity_available=identity_available,
                source_id=source_id,
                config_generation=generation,
                timestamp_quality=timestamp_quality,
                acquisition_epoch=publication_context.acquisition_epoch,
                clock_domain=publication_context.clock_domain,
            )
        except Exception as error:
            self._publish_error(f"Pluto persistence conversion failed: {error}")
            return
        with self._lock:
            if expected_engine is not None and expected_engine is not self._engine:
                return
            self._snapshot = LiveSnapshot(
                generation=self._snapshot.generation,
                sequence=as_frame_sequence(self._sequence),
                state=_running_state(),
                device=self._snapshot.device,
                applied=self._snapshot.applied,
                quality=self._snapshot.quality,
                unit=self._snapshot.unit,
                spectrum=self._snapshot.spectrum,
                persistence=persistence,
                performance=self._snapshot.performance,
                session_id=self._snapshot.session_id,
                active_source_id=self._snapshot.active_source_id,
                acquisition_epoch=self._snapshot.acquisition_epoch,
                clock_domain=self._snapshot.clock_domain,
                active_config_generation=self._snapshot.active_config_generation,
            )

    def _publish_error(self, message: str) -> None:
        kind = (
            LiveErrorKind.STREAM_STALLED
            if "stalled" in message.casefold() or "no frames" in message.casefold()
            else LiveErrorKind.INTERNAL
        )
        log_event(
            _LOGGER,
            "stream",
            "live_error",
            level=logging.ERROR,
            uri=self._native_uri,
            error_kind=kind.value,
            error=message,
        )
        with self._lock:
            self._snapshot = LiveSnapshot(
                generation=self._snapshot.generation,
                sequence=as_frame_sequence(self._sequence),
                state=_error_state(),
                device=self._snapshot.device,
                applied=self._snapshot.applied,
                quality=self._snapshot.quality,
                unit=self._snapshot.unit,
                error=message,
                error_kind=kind,
                spectrum=self._snapshot.spectrum,
                persistence=self._snapshot.persistence,
                performance=self._snapshot.performance,
                session_id=self._snapshot.session_id,
                active_source_id=self._snapshot.active_source_id,
                acquisition_epoch=self._snapshot.acquisition_epoch,
                clock_domain=self._snapshot.clock_domain,
                active_config_generation=self._snapshot.active_config_generation,
            )

    def _engine_drop_count(self) -> int:
        engine = self._engine
        if engine is None:
            return self._snapshot.quality.dropped_blocks
        try:
            metrics = engine.metrics()
        except Exception:
            return self._snapshot.quality.dropped_blocks
        stream = getattr(metrics, "device", None)
        dropped = 0
        if stream is not None:
            dropped += int(getattr(stream, "output_blocks_dropped", 0) or 0)
            dropped += int(getattr(stream, "short_reads", 0) or 0)
        queue = getattr(metrics, "spectrum_queue", None)
        if queue is not None:
            dropped += int(getattr(queue, "dropped", 0) or 0)
        return dropped

    def _descriptor_for_context(self, context: Any) -> DeviceDescriptor:
        uri = str(context.uri)
        return self._descriptor_for_uri(uri, str(getattr(context, "description", "")))

    def _descriptor_for_uri(self, uri: str, description: str | None) -> DeviceDescriptor:
        probe = None
        try:
            probe = self._native.probe_pluto_context(uri, self._timeout_ms)
        except Exception:
            # A context returned by libiio is still useful to show in the
            # dialog; selection performs the authoritative connection probe.
            pass

        native_capabilities = None
        temporary_device = None
        try:
            temporary_device = self._native.PlutoDevice(uri, self._timeout_ms)
            native_capabilities = temporary_device.capabilities()
        except Exception:
            native_capabilities = None
        finally:
            if temporary_device is not None:
                try:
                    temporary_device.disconnect()
                except Exception:
                    pass

        model = str(getattr(probe, "model", "") or getattr(native_capabilities, "model", "") or "PlutoSDR")
        serial = str(getattr(probe, "serial", "") or getattr(native_capabilities, "serial", "") or "").strip()
        firmware = str(getattr(probe, "firmware", "") or getattr(native_capabilities, "firmware", "") or "").strip()
        device_ids = tuple(str(value) for value in (getattr(probe, "device_ids", ()) or ()))
        identity_key = _physical_identity_key(
            uri=uri,
            model=model,
            serial=serial,
            firmware=firmware,
            device_ids=device_ids,
        )
        topology = _domain_receiver_topology(self._native, uri, probe, identity_key)
        description_text = (description or "").strip()
        model_prefix = model.split("(", 1)[0].strip().casefold()
        label = model if not description_text or model_prefix in description_text.casefold() else f"{model} — {description_text}"
        return DeviceDescriptor(
            device_id=f"pluto:{identity_key}",
            label=label,
            uri=uri,
            transport=_transport_for_uri(uri),
            capabilities=_domain_capabilities(self._native, native_capabilities, receiver_topology=topology),
            serial=serial or None,
            identity_key=identity_key,
        )

    def _fail(
        self,
        message: str,
        *,
        kind: LiveErrorKind = LiveErrorKind.INTERNAL,
    ) -> LiveSnapshot:
        snapshot = super()._fail(message, kind=kind)
        log_event(
            _LOGGER,
            "state",
            "live_operation_failed",
            level=logging.ERROR,
            uri=self._native_uri,
            device_id=snapshot.device.device_id if snapshot.device else None,
            error_kind=kind.value,
            error=message,
        )
        return snapshot

    def _clear_selected_route(self) -> None:
        device = self._native_device
        self._native_device = None
        self._native_uri = None
        if device is not None:
            try:
                device.disconnect()
            except Exception:
                pass


def _shutdown_engine_instance(engine: Any) -> None:
    """Best-effort cleanup for an engine that failed during start/configure."""

    try:
        engine.request_stop()
    except Exception:
        pass
    try:
        engine.join()
    except Exception:
        pass
    try:
        engine.disconnect()
    except Exception:
        pass


def _running_state() -> Any:
    from ..domain import LiveSessionState

    return LiveSessionState.RUNNING


def _error_state() -> Any:
    from ..domain import LiveSessionState

    return LiveSessionState.ERROR


def _native_fixed_band_config(
    native_module: Any,
    live: LiveConfiguration,
    context_uri: str,
    *,
    recording_options: RecordingOptions | None = None,
    source_id: str = "live",
    discard_blocks_after_start: int | None = None,
    device_buffer_samples: int = _DEFAULT_DEVICE_BUFFER_SAMPLES,
    snapshot_rate_hz: float | None = None,
    allow_r10d5_evidence_buffer_geometry: bool = False,
    allow_nonstandard_evidence_buffer_geometry: bool = False,
) -> Any:
    """Build the P07 fixed-band native config from the applied live config.

    ``analog_bandwidth_hz`` (RF passband, e.g. 0.2–56 MHz on Pluto) is kept
    independent from ``sample_rate_hz`` (ADC/decimation rate, up to 61.44
    MSPS): the AD936x samples at the master rate and decimates, while the
    bandwidth sets the analog filter passband. Defaults to the sample rate
    only when the caller did not choose a bandwidth explicitly.
    """
    standard_geometry = _is_standard_device_buffer_samples(device_buffer_samples)
    d5_geometry = (
        allow_r10d5_evidence_buffer_geometry and device_buffer_samples == 308_224
    )
    d7_geometry = (
        allow_nonstandard_evidence_buffer_geometry
        and device_buffer_samples in _NONSTANDARD_EVIDENCE_DEVICE_BUFFER_SAMPLES
    )
    evidence_geometry = d5_geometry or d7_geometry
    if not standard_geometry and not evidence_geometry:
        raise ValueError("native device buffer must be a power of two in [4096, 262144]")
    effective_snapshot_rate_hz = (
        float(live.snapshot_rate_hz or _DEFAULT_SNAPSHOT_RATE_HZ)
        if snapshot_rate_hz is None else float(snapshot_rate_hz)
    )
    if not math.isfinite(effective_snapshot_rate_hz) or not 1.0 <= effective_snapshot_rate_hz <= 2000.0:
        raise ValueError("native snapshot rate must be finite and in [1, 2000] Hz")
    bandwidth_hz = live.analog_bandwidth_hz if live.analog_bandwidth_hz is not None else live.sample_rate_hz
    device = native_module.DeviceConfig(
        source_id,
        context_uri,
        float(live.center_hz),
        float(live.sample_rate_hz),
        float(bandwidth_hz),
        native_module.GainMode.MANUAL,
        float(live.gain_db),
        0,
        int(device_buffer_samples),
        _SCHEMA_VERSION,
    )
    hop_size = max(1, int(round(live.fft_size * (1.0 - live.overlap_ratio))))
    dsp = native_module.DspConfig(
        int(live.fft_size),
        hop_size,
        _native_window(native_module, live.window),
        _native_detector(native_module, live.detector),
        native_module.SpectrumUnit.DBFS_BIN,
        native_module.PrecisionMode.ACCURATE_F32_F64_ACCUM,
        1,
        int(live.averaging_frames),
        8.6,
        native_module.CalibrationStatus.UNCALIBRATED,
        "",
        _SCHEMA_VERSION,
    )
    persistence = native_module.PersistenceConfig(
        bool(live.persistence_enabled),
        _native_persistence_mode(native_module, live.persistence_mode),
        int(live.persistence_window_frames),
        float(live.persistence_half_life_s),
        float(live.persistence_power_min_db),
        float(live.persistence_power_max_db),
        int(live.persistence_power_bins),
        float(live.persistence_snapshot_rate_hz),
        _SCHEMA_VERSION,
    )
    fixed_arguments = (
        device,
        dsp,
        _native_backend(native_module, live.backend),
        True,
        _ACQUISITION_QUEUE_CAPACITY,
        native_module.OverflowPolicy.DROP_NEWEST,
        _SPECTRUM_QUEUE_CAPACITY,
        _EVENT_QUEUE_CAPACITY,
        effective_snapshot_rate_hz,
        _DISCARD_BLOCKS_AFTER_START if discard_blocks_after_start is None else int(discard_blocks_after_start),
        False,
        persistence,
    )
    if recording_options is None:
        return native_module.FixedBandConfig(*fixed_arguments)
    recording_config = native_module.RecordingConfig(
        True,
        recording_options.output_path,
        recording_options.record_iq,
        recording_options.record_spectrum,
        1_048_576,
        recording_options.queue_capacity,
        False,
        _SCHEMA_VERSION,
    )
    return native_module.FixedBandConfig(*fixed_arguments, recording_config)


def build_native_fixed_band_config(
    native_module: Any,
    live: LiveConfiguration,
    context_uri: str,
    *,
    source_id: str = "live",
    discard_blocks_after_start: int | None = None,
    device_buffer_samples: int = _DEFAULT_DEVICE_BUFFER_SAMPLES,
    snapshot_rate_hz: float | None = None,
    recording_options: RecordingOptions | None = None,
    allow_r10d5_evidence_buffer_geometry: bool = False,
    allow_nonstandard_evidence_buffer_geometry: bool = False,
) -> Any:
    """Build a native config for another bounded, explicitly-owned workflow.

    Raw I/Q remains inside the native writer when ``recording_options`` is
    supplied; no Python sample bridge is created. Sweep callers must leave it
    unset. R10-B uses this helper to make source identity and post-retune
    discard policy explicit while preserving the P07 admission
    limits and native-only data path.
    """

    return _native_fixed_band_config(
        native_module,
        live,
        context_uri,
        source_id=source_id,
        discard_blocks_after_start=discard_blocks_after_start,
        device_buffer_samples=device_buffer_samples,
        snapshot_rate_hz=snapshot_rate_hz,
        recording_options=recording_options,
        allow_r10d5_evidence_buffer_geometry=allow_r10d5_evidence_buffer_geometry,
        allow_nonstandard_evidence_buffer_geometry=allow_nonstandard_evidence_buffer_geometry,
    )


def _is_standard_device_buffer_samples(value: int) -> bool:
    return not (
        value < 4096
        or value > _DEFAULT_DEVICE_BUFFER_SAMPLES
        or value & (value - 1)
    )


_NONSTANDARD_EVIDENCE_DEVICE_BUFFER_SAMPLES = frozenset((100_352, 308_224))


def _validate_device_buffer_samples(
    value: int,
    *,
    allow_nonstandard_evidence_buffer_geometry: bool = False,
) -> None:
    numeric = int(value)
    if _is_standard_device_buffer_samples(numeric):
        return
    if (
        allow_nonstandard_evidence_buffer_geometry
        and numeric in _NONSTANDARD_EVIDENCE_DEVICE_BUFFER_SAMPLES
    ):
        return
    if not _is_standard_device_buffer_samples(numeric):
        raise ValueError("native device buffer must be a power of two in [4096, 262144]")


def _native_recording_base_path(output_path: str) -> Path:
    """Mirror the native writer's suffix normalization for lifecycle sidecars."""

    value = str(output_path)
    if value.endswith(".part"):
        value = value[:-5]
    for suffix in (
        ".sigmf-meta",
        ".sigmf-index.jsonl",
        ".sigmf-gaps.jsonl",
        ".sdr-spectrum.meta",
        ".sdr-spectrum.bin",
        ".sdr-spectrum-index.jsonl",
    ):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    return Path(value)


def _native_backend(native_module: Any, backend: BackendKind) -> Any:
    """Map the user's backend policy to the native enum without silent AUTO."""
    enum_type = native_module.ComputeBackendKind
    names = {
        BackendKind.AUTO: ("AUTO", "Auto"),
        BackendKind.CPU: ("CPU", "Cpu"),
        BackendKind.CUDA: ("CUDA", "Cuda"),
        BackendKind.HIP: ("HIP", "Hip"),
    }[backend]
    for name in names:
        if hasattr(enum_type, name):
            return getattr(enum_type, name)
    if backend is BackendKind.AUTO:
        return getattr(enum_type, "AUTO")
    raise RuntimeError(f"Requested backend is not compiled into the native module: {backend.value}")


def _native_persistence_mode(native_module: Any, mode: str) -> Any:
    value = (mode or "exponential-decay").strip().casefold().replace("_", "-")
    enum_type = native_module.PersistenceMode
    mapping = {
        "disabled": ("DISABLED", "Disabled"),
        "rolling-exact": ("ROLLING_EXACT", "RollingExact"),
        "exponential-decay": ("EXPONENTIAL_DECAY", "ExponentialDecay"),
    }
    for name in mapping.get(value, mapping["exponential-decay"]):
        if hasattr(enum_type, name):
            return getattr(enum_type, name)
    return getattr(enum_type, "EXPONENTIAL_DECAY")


def _domain_applied_configuration(
    provisional: AppliedLiveConfiguration,
    *,
    native_applied: Any,
    active_backend: BackendKind,
) -> AppliedLiveConfiguration:
    """Merge native readback into the immutable requested/applied model."""
    requested = provisional.requested
    base = provisional.applied
    center = float(getattr(native_applied, "center_frequency_hz", base.center_hz))
    sample_rate = float(getattr(native_applied, "sample_rate_hz", base.sample_rate_hz))
    bandwidth = float(getattr(native_applied, "analog_bandwidth_hz", base.analog_bandwidth_hz or base.sample_rate_hz))
    gain = float(getattr(native_applied, "manual_gain_db", base.gain_db))
    applied = replace(
        base,
        center_hz=center,
        sample_rate_hz=sample_rate,
        analog_bandwidth_hz=bandwidth,
        gain_db=gain,
        backend=active_backend,
    )
    adjustments = list(provisional.adjustments)
    comparisons = (
        ("center frequency adjusted by device", requested.center_hz, center),
        ("sample rate adjusted by device", requested.sample_rate_hz, sample_rate),
        ("bandwidth adjusted by device", requested.analog_bandwidth_hz, bandwidth),
        ("gain adjusted by device", requested.gain_db, gain),
    )
    for message, expected, actual in comparisons:
        if expected is not None and abs(float(expected) - float(actual)) > max(1e-6, abs(float(expected)) * 1e-9):
            if message not in adjustments:
                adjustments.append(message)
    if requested.backend not in (BackendKind.AUTO, active_backend):
        adjustments.append(f"requested backend {requested.backend.value}; active {active_backend.value}")
    readback_fields = tuple(
        domain_name for native_name, domain_name in (
            ("center_frequency_hz", "center_hz"), ("sample_rate_hz", "sample_rate_hz"),
            ("analog_bandwidth_hz", "analog_bandwidth_hz"), ("manual_gain_db", "gain_db"),
        )
        if isinstance(getattr(native_applied, native_name, None), (int, float))
        and not isinstance(getattr(native_applied, native_name, None), bool)
        and math.isfinite(getattr(native_applied, native_name))
    )
    return AppliedLiveConfiguration(requested=requested, applied=applied,
                                    adjustments=tuple(adjustments), readback_fields=readback_fields)


def _backend_fallback_reason(metrics: Any, applied: AppliedLiveConfiguration) -> str | None:
    requested = applied.requested.backend
    active = applied.applied.backend
    count = int(getattr(metrics, "backend_fallback_count", 0) or 0)
    error = getattr(metrics, "last_backend_error", None)
    error_name = getattr(error, "name", None)
    if requested not in (BackendKind.AUTO, active):
        return f"requested {requested.value}; active {active.value}"
    if count > 0:
        suffix = f": {str(error_name).casefold()}" if error_name and str(error_name).upper() != "NONE" else ""
        return f"backend fallback {count}{suffix}"
    return None


def _native_window(native_module: Any, window: str) -> Any:
    """Map a domain window name to the native WindowType enum."""
    name = (window or "hann").strip().casefold().replace("_", "-").replace(" ", "-")
    mapping = {
        "rectangular": "RECTANGULAR",
        "hann": "HANN",
        "hanning": "HANN",
        "blackman-harris": "BLACKMAN_HARRIS_4TERM",
        "blackmanharris": "BLACKMAN_HARRIS_4TERM",
        "flattop": "FLAT_TOP",
        "flat-top": "FLAT_TOP",
        "nuttall": "NUTTALL",
        "kaiser": "KAISER",
    }
    enum_name = mapping.get(name, "HANN")
    return getattr(native_module.WindowType, enum_name, native_module.WindowType.HANN)


def _native_detector(native_module: Any, detector: str) -> Any:
    """Map a domain detector name to the native DetectorType enum."""
    name = (detector or "sample").strip().casefold().replace("_", "-")
    mapping = {
        "sample": "SAMPLE",
        "peak": "PEAK",
        "positive-peak": "PEAK",
        "negative-peak": "NEGATIVE_PEAK",
        "rms": "RMS",
        "average": "AVERAGE_POWER",
        "average-power": "AVERAGE_POWER",
    }
    enum_name = mapping.get(name, "SAMPLE")
    return getattr(native_module.DetectorType, enum_name, native_module.DetectorType.SAMPLE)


def _backend_kind(applied: Any, *, fallback: Any | None = None) -> BackendKind:
    active = getattr(applied, "active_backend", None)
    if active is None and fallback is not None:
        active = getattr(fallback, "active_backend", None)
    name = getattr(active, "name", None)
    if name is not None:
        try:
            return BackendKind(name.casefold())
        except ValueError:
            pass
    return BackendKind.CPU


def _calibration_quality(applied: Any) -> Any:
    from ..domain import CalibrationQuality

    return CalibrationQuality.UNCALIBRATED


def _frame_calibration_quality(status: str | None) -> Any:
    from ..domain import CalibrationQuality

    if status in {"applied", "interpolated"}:
        return CalibrationQuality.CALIBRATED
    if status in {"invalid", "extrapolated"}:
        return CalibrationQuality.MISMATCH
    return CalibrationQuality.UNCALIBRATED


def _spectrum_unit(applied: Any) -> str:
    return "dBFS/bin"


def build_optional_native_live_service() -> NativeLiveSessionService | None:
    """Return the native service when the packaged extension is available."""

    try:
        native_module = cast(Any, import_module("sdr_monitor._sdr_native"))
        info = native_module.build_info()
        if not info.get("pluto_compiled", False):
            return None
        configure_frozen_libiio_runtime(native_module)
    except (ImportError, ModuleNotFoundError, OSError, AttributeError):
        return None
    return NativeLiveSessionService(native_module)


def _transport_for_uri(uri: str) -> DeviceTransport:
    value = uri.casefold()
    if value.startswith("usb:"):
        return DeviceTransport.USB
    if value.startswith("ip:"):
        return DeviceTransport.IP
    return DeviceTransport.MANUAL


def _valid_serial(value: str | None) -> bool:
    serial = (value or "").strip()
    return bool(serial and serial not in {"-", "—", "unknown", "n/a", "none"})


def _physical_identity_key(
    *,
    uri: str,
    model: str,
    serial: str,
    firmware: str,
    device_ids: tuple[str, ...],
) -> str:
    """Return a stable, non-sensitive logical identity.

    A serial is hashed before it enters UI state/logging.  When libiio does
    not expose a serial, the route remains distinct until the conservative
    USB/IP duplicate heuristic below can prove that there is only one
    candidate radio.
    """

    if _valid_serial(serial):
        payload = f"serial|{serial.casefold()}".encode("utf-8", "replace")
        return f"serial-{hashlib.sha256(payload).hexdigest()[:16]}"
    payload = "|".join(
        (
            "route",
            model.casefold().strip(),
            firmware.casefold().strip(),
            ",".join(sorted(value.casefold() for value in device_ids)),
            uri.casefold().strip(),
        )
    ).encode("utf-8", "replace")
    return f"route-{hashlib.sha256(payload).hexdigest()[:16]}"


def _safe_identity(identity: str | None) -> str | None:
    if not identity:
        return None
    return hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()[:12]


def _route_priority(uri: str) -> tuple[int, str]:
    transport = _transport_for_uri(uri)
    order = {
        DeviceTransport.USB: 0,
        DeviceTransport.IP: 1,
        DeviceTransport.MANUAL: 2,
    }[transport]
    return order, uri.casefold()


def _ordered_routes(device: DeviceDescriptor) -> tuple[str, ...]:
    routes = tuple(dict.fromkeys((device.uri, *device.alternate_uris)))
    return tuple(sorted(routes, key=_route_priority))


def _start_routes(
    device: DeviceDescriptor | None,
    current_uri: str,
) -> tuple[str, ...]:
    """Try the active route first, then bounded logical-device failovers."""

    if device is None:
        return (current_uri,)
    ordered = _ordered_routes(device)
    return tuple(dict.fromkeys((current_uri, *ordered)))


def _normalized_model(label: str) -> str:
    value = label.casefold()
    for token in ("—", "(", "["):
        value = value.split(token, 1)[0]
    return " ".join(value.split())


def _is_default_pluto_ip(uri: str) -> bool:
    value = uri.casefold().strip()
    return value in {
        "ip:pluto.local",
        "ip:192.168.2.1",
        "ip:192.168.3.1",
    }


def _merge_device_group(group: list[DeviceDescriptor]) -> DeviceDescriptor:
    ordered = sorted(group, key=lambda item: _route_priority(item.uri))
    preferred = ordered[0]
    routes = tuple(item.uri for item in ordered)
    transports = ", ".join(item.transport.value.upper() for item in ordered)
    label = preferred.label
    # Avoid duplicating verbose USB descriptor text in the row.  The dialog
    # has a dedicated route/detail line after this refactor.
    if len(ordered) > 1:
        label = f"{_normalized_model(preferred.label).title()} ({transports})"
    identity = next((item.identity_key for item in ordered if item.identity_key and item.identity_key.startswith("serial-")), None)
    if identity is None:
        payload = "|".join(sorted(routes)).encode("utf-8", "replace")
        identity = f"logical-{hashlib.sha256(payload).hexdigest()[:16]}"
    return replace(
        preferred,
        device_id=f"pluto:{identity}",
        label=label,
        alternate_uris=tuple(routes[1:]),
        identity_key=identity,
        serial=next((item.serial for item in ordered if _valid_serial(item.serial)), None),
    )


def _merge_duplicate_pluto_routes(
    routes: tuple[DeviceDescriptor, ...],
) -> tuple[DeviceDescriptor, ...]:
    """Collapse transport aliases without hiding multiple physical radios.

    Serial matches are authoritative.  When libiio omits the serial on the IP
    route, exactly one serial-less USB radio may absorb one or more default
    Pluto network aliases (``pluto.local``/USB-gadget addresses) with the same
    normalized model.  The heuristic is disabled as soon as more than one USB
    candidate exists.
    """

    if not routes:
        return ()
    serial_groups: dict[str, list[DeviceDescriptor]] = {}
    ungrouped: list[DeviceDescriptor] = []
    for route in routes:
        if _valid_serial(route.serial):
            serial_groups.setdefault(route.identity_key or route.device_id, []).append(route)
        else:
            ungrouped.append(route)
    merged = [_merge_device_group(group) for group in serial_groups.values()]

    usb = [item for item in ungrouped if item.transport is DeviceTransport.USB]
    default_ip = [
        item
        for item in ungrouped
        if item.transport is DeviceTransport.IP and _is_default_pluto_ip(item.uri)
    ]
    other = [item for item in ungrouped if item not in usb and item not in default_ip]
    if (
        len(usb) == 1
        and default_ip
        and not any(item.transport is DeviceTransport.IP for item in other)
        and all(
            _normalized_model(item.label) == _normalized_model(usb[0].label)
            for item in default_ip
        )
    ):
        merged.append(_merge_device_group([usb[0], *default_ip]))
        merged.extend(other)
    else:
        merged.extend(ungrouped)
    return tuple(sorted(merged, key=lambda item: _route_priority(item.uri)))


def _infer_failure_stage(message: str) -> str:
    value = message.casefold()
    for needle, stage in (
        ("sampling_frequency", "write_sampling_frequency"),
        ("rf_bandwidth", "write_rf_bandwidth"),
        ("bandwidth", "write_rf_bandwidth"),
        ("hardwaregain", "write_gain"),
        ("gain", "write_gain"),
        ("frequency", "write_center_frequency"),
        ("cuda", "backend_cuda"),
        ("context", "open_context"),
        ("refill", "stream_refill"),
    ):
        if needle in value:
            return stage
    return "engine_start"


def _domain_receiver_topology(
    native_module: Any,
    uri: str,
    context_probe: Any | None,
    physical_identity_key: str,
) -> ReceiverTopologySnapshot | None:
    """Map the optional native E0 topology probe without creating a stream.

    Older native modules and test doubles intentionally remain compatible:
    absence/failure of the optional read-only probe publishes no topology,
    never an inferred single- or dual-RX capability.
    """

    probe_topology = getattr(native_module, "probe_pluto_receiver_topology", None)
    if not callable(probe_topology):
        return None
    try:
        observation = probe_topology(uri, 3000)
        context = getattr(observation, "context", None) or context_probe
        stream_id = str(getattr(context, "rx_stream_device_id", "") or "").strip()
        if not stream_id:
            return None
        elements: list[StreamScanElement] = []
        for index, element in enumerate(getattr(observation, "input_scan_elements", ()) or ()):
            element_id = str(getattr(element, "id", "") or "").strip()
            chain_component = _receiver_chain_component(element_id)
            if not element_id or chain_component is None:
                continue
            chain, component = chain_component
            elements.append(
                StreamScanElement(
                    element_id,
                    chain,
                    component,
                    int(getattr(element, "device_channel_index", index)),
                    int(getattr(element, "storage_bits", 0)),
                    int(getattr(element, "significant_bits", 0)),
                    int(getattr(element, "shift", 0)),
                    bool(getattr(element, "is_signed", False)),
                    bool(getattr(element, "is_big_endian", False)),
                )
            )
        if not elements:
            return None
        model = str(getattr(context, "model", "") or "").strip() or None
        return ReceiverTopologySnapshot(
            f"pluto:{physical_identity_key}|{stream_id}",
            None,
            model,
            tuple(str(value) for value in (getattr(observation, "phy_rx_channel_ids", ()) or ())),
            tuple(elements),
        )
    except Exception:
        return None


def _receiver_chain_component(element_id: str) -> tuple[ReceiverChain, IqComponent] | None:
    """Map canonical AD936x scan IDs only; unknown names remain unadmitted."""

    normalized = element_id.strip().casefold()
    mapping = {
        "voltage0": (ReceiverChain.RX1, IqComponent.IN_PHASE),
        "voltage1": (ReceiverChain.RX1, IqComponent.QUADRATURE),
        "voltage2": (ReceiverChain.RX2, IqComponent.IN_PHASE),
        "voltage3": (ReceiverChain.RX2, IqComponent.QUADRATURE),
    }
    return mapping.get(normalized)


def _domain_capabilities(
    native_module: Any,
    native_capabilities: Any | None,
    *,
    receiver_topology: ReceiverTopologySnapshot | None = None,
) -> DeviceCapabilities:
    if native_capabilities is None:
        rates: tuple[float, ...] = (2e6, 10e6, 20e6)
        bandwidths: tuple[float, ...] = (2e6, 10e6, 20e6, 40e6, 56e6)
        gain_range = (0.0, 73.0)
    else:
        rates = _representative_rates(native_capabilities.sample_rate_ranges_hz)
        ranges = getattr(native_capabilities, "analog_bandwidth_ranges_hz", None)
        bandwidths = _representative_rates(ranges) if ranges else (2e6, 10e6, 20e6, 40e6, 56e6)
        gain = native_capabilities.gain_range_db
        gain_range = (float(gain.minimum), float(gain.maximum))

    build_info = native_module.build_info()
    backends = [BackendKind.AUTO, BackendKind.CPU]
    if build_info.get("cuda_compiled", False):
        backends.append(BackendKind.CUDA)
    return DeviceCapabilities(
        sample_rates_hz=rates,
        gain_range_db=gain_range,
        analog_bandwidths_hz=bandwidths,
        supported_backends=tuple(backends),
        receiver_topology=receiver_topology,
        sample_rate_ranges_hz=tuple(
            (float(item.minimum), float(item.maximum), float(getattr(item, "step", 0) or 0))
            for item in getattr(native_capabilities, "sample_rate_ranges_hz", ())
        ),
    )


def _representative_rates(ranges: Any) -> tuple[float, ...]:
    """Build stable presets from native min/max/step capability ranges."""

    values: set[float] = set()
    preferred = (
        0.2e6,
        2.083333e6,
        2.4e6,
        5e6,
        10e6,
        20e6,
        30.72e6,
        40e6,
        50e6,
        56e6,
        61.44e6,
    )
    for rate_range in ranges:
        minimum = float(rate_range.minimum)
        maximum = float(rate_range.maximum)
        step_value = getattr(rate_range, "step", None)
        step = float(step_value) if step_value is not None else 0.0

        def admit(candidate: float) -> None:
            value = float(candidate)
            if value < minimum - 1e-6 or value > maximum + 1e-6:
                return
            if step > 0.0 and math.isfinite(step):
                value = minimum + round((value - minimum) / step) * step
                value = min(max(value, minimum), maximum)
            values.add(round(value, 6))

        admit(minimum)
        admit(maximum)
        for candidate in preferred:
            admit(candidate)
    return tuple(sorted(values)) or (2.083333e6, 2.4e6, 10e6, 20e6, 61.44e6)


__all__ = ["NativeLiveSessionService", "build_optional_native_live_service"]
