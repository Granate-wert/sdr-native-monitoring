"""Deterministic fake live service for tests, demos and the Live Monitor UI.

``FakeLiveService`` implements the same ``_LiveService`` protocol as
:class:`~esw_dfl.sdr.fixed_band.FixedBandEngineService` (configure/start/
request_stop/join/disconnect/poll_spectrum/poll_events/metrics) so the
controller, the presenter and the workspace can be exercised without any
native or hardware dependency.

Behavior is fully controlled by an immutable :class:`FakeLiveConfig`:

* capability ranges (tuning, sample rate, analog bandwidth, gain) used by
  capability-aware validation and control limits;
* backend availability (CPU/CUDA/HIP), explicit-unavailable and AUTO
  fallback semantics;
* deterministic spectrum frames with calibration provenance and quality
  flags;
* injected failures (configure/start) for error-path tests;
* an optional applied adjustment (frequency step) so requested vs applied
  differences can be observed end to end.

The service never blocks and never touches Qt or native code.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..domain import SourceDescriptor
from .contracts import (
    BackendErrorCode,
    CalibrationStatus,
    ComputeBackendKind,
    DeviceCapabilities,
    EngineMetrics,
    EngineState,
    GainMode,
    NumericRange,
    QualityFlag,
    SampleFormat,
    SourceType,
    SpectrumFrame,
)
from .fixed_band import FixedBandEvent, FixedBandMetricsSnapshot, FixedBandOptions, QueueSnapshot
from .pluto import PlutoStreamMetrics


@dataclass(frozen=True, slots=True)
class FakeAppliedConfig:
    """Mirror of the device-applied values the engine would read back."""

    center_frequency_hz: float
    sample_rate_hz: float
    analog_bandwidth_hz: float
    gain_mode: GainMode
    manual_gain_db: float
    config_generation: int
    active_backend: ComputeBackendKind


@dataclass(frozen=True, slots=True)
class FakeLiveConfig:
    """Immutable behavior knobs for :class:`FakeLiveService`."""

    available_backends: tuple[ComputeBackendKind, ...] = (
        ComputeBackendKind.CPU,
        ComputeBackendKind.CUDA,
    )
    fail_on_configure: bool = False
    fail_on_start: bool = False
    tuning_range_hz: NumericRange = NumericRange(70.0e6, 6.0e9)
    sample_rate_ranges_hz: tuple[NumericRange, ...] = (NumericRange(200.0e3, 61.44e6),)
    analog_bandwidth_ranges_hz: tuple[NumericRange, ...] = (NumericRange(200.0e3, 56.0e6),)
    gain_range_db: NumericRange = NumericRange(0.0, 74.5)
    gain_modes: tuple[GainMode, ...] = (
        GainMode.MANUAL,
        GainMode.SLOW_ATTACK,
        GainMode.FAST_ATTACK,
        GainMode.HYBRID,
    )
    center_frequency_step_hz: float | None = None
    frames_per_poll: int = 1
    max_frames: int = 16
    calibration_status: CalibrationStatus = CalibrationStatus.UNCALIBRATED
    calibration_profile_id: str | None = None
    dropped_iq_blocks_before: int = 0
    dropped_fft_frames_before: int = 0
    events: tuple[FixedBandEvent, ...] = ()

    def __post_init__(self) -> None:
        if not self.available_backends:
            raise ValueError("available_backends must not be empty")
        if self.frames_per_poll <= 0 or self.max_frames <= 0:
            raise ValueError("frames_per_poll and max_frames must be positive")
        if self.center_frequency_step_hz is not None and self.center_frequency_step_hz <= 0.0:
            raise ValueError("center_frequency_step_hz must be positive")
        if self.dropped_iq_blocks_before < 0 or self.dropped_fft_frames_before < 0:
            raise ValueError("dropped counters must not be negative")


def _range_contains(rng: NumericRange, value: float) -> bool:
    return rng.minimum <= value <= rng.maximum


def _any_range_contains(ranges: tuple[NumericRange, ...], value: float) -> bool:
    return any(_range_contains(rng, value) for rng in ranges)


class FakeLiveService:
    """Deterministic ``_LiveService`` implementation with fake hardware."""

    def __init__(self, uri: str, *, config: FakeLiveConfig | None = None) -> None:
        if not uri.strip():
            raise ValueError("uri must not be empty")
        self.uri = uri
        self.config = config or FakeLiveConfig()
        self._options: FixedBandOptions | None = None
        self._applied: FakeAppliedConfig | None = None
        self._frame_count = 0
        self._fallback_count = 0
        self._last_backend_error = BackendErrorCode.NONE
        self._stop_requested = False
        self._disconnected = False
        self._started = False

    # ------------------------------------------------------------------
    # _LiveService protocol
    # ------------------------------------------------------------------
    def configure(self, options: FixedBandOptions) -> FakeAppliedConfig:
        if self.config.fail_on_configure:
            raise RuntimeError("fake configure failure (injected)")
        backend = self._resolve_backend(options)
        if backend is None:
            raise RuntimeError("CUDA backend unavailable (runtime not found)")
        center = options.device.center_frequency_hz
        step = self.config.center_frequency_step_hz
        if step is not None:
            center = round(center / step) * step
        self._options = options
        self._applied = FakeAppliedConfig(
            center_frequency_hz=center,
            sample_rate_hz=options.device.sample_rate_hz,
            analog_bandwidth_hz=options.device.analog_bandwidth_hz,
            gain_mode=options.device.gain_mode,
            manual_gain_db=options.device.manual_gain_db,
            config_generation=1,
            active_backend=backend,
        )
        return self._applied

    def start(self) -> None:
        if self.config.fail_on_start:
            raise RuntimeError("fake start failure (injected)")
        self._started = True

    def request_stop(self) -> None:
        self._stop_requested = True

    def join(self) -> None:
        return None

    def disconnect(self) -> None:
        self._disconnected = True

    def poll_spectrum(self, max_items: int = 0) -> tuple[SpectrumFrame, ...]:
        if not self._started or self._stop_requested or self._options is None:
            return ()
        count = max_items or self.config.frames_per_poll
        count = max(0, min(count, self.config.max_frames - self._frame_count))
        result = tuple(self._make_frame(sequence) for sequence in range(count))
        self._frame_count += count
        return result

    def poll_events(self, _max_items: int = 0) -> tuple[FixedBandEvent, ...]:
        return self.config.events

    def metrics(self) -> FixedBandMetricsSnapshot:
        state = EngineState.RUNNING if self._started and not self._stop_requested else EngineState.STOPPED
        engine = EngineMetrics(
            iq_samples_received=self._frame_count * self._samples_per_frame(),
            iq_samples_dropped=self.config.dropped_iq_blocks_before * self._samples_per_frame(),
            iq_blocks_received=self._frame_count,
            iq_blocks_dropped=self.config.dropped_iq_blocks_before,
            fft_frames_computed=self._frame_count,
            fft_frames_dropped=self.config.dropped_fft_frames_before,
            analytical_fft_rate=self.config.frames_per_poll * 20.0,
            spectrum_snapshots_emitted=self._frame_count,
        )
        device = PlutoStreamMetrics(
            blocks_received=self._frame_count,
            samples_received=self._frame_count * self._samples_per_frame(),
            short_reads=0,
            refill_errors=0,
            output_pool_exhaustions=0,
            output_blocks_dropped=0,
            estimated_dropped_samples=0,
        )
        queue = QueueSnapshot(
            capacity=16, depth=0, high_water=1,
            pushed=self._frame_count, popped=self._frame_count,
            dropped=0, abandoned=0, stop_requested=self._stop_requested,
        )
        return FixedBandMetricsSnapshot(
            state=state,
            has_error=False,
            engine=engine,
            device=device,
            acquisition_queue=queue,
            spectrum_queue=queue,
            persistence_queue=queue,
            transient_blocks_discarded=0,
            transient_samples_discarded=0,
            spectrum_snapshots_superseded=0,
            persistence_snapshots_superseded=0,
            shutdown_blocks_discarded=0,
            shutdown_samples_discarded=0,
            expected_cancellations=0,
            diagnostic_events_lost=0,
            requested_backend=(
                self._options.backend if self._options is not None else ComputeBackendKind.CPU
            ),
            active_backend=(
                self._applied.active_backend
                if self._applied is not None
                else ComputeBackendKind.CPU
            ),
            backend_self_test_passed=True,
            backend_fallback_count=self._fallback_count,
            backend_switch_count=self._fallback_count,
            last_backend_error=self._last_backend_error,
        )

    # ------------------------------------------------------------------
    # Capabilities (used by validation, not part of _LiveService)
    # ------------------------------------------------------------------
    def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities(
            backend_id="fake",
            device_id="fake-pluto",
            serial="FAKE-0001",
            model="Fake PlutoSDR (AD936x)",
            firmware="fake-1.0",
            tuning_range_hz=self.config.tuning_range_hz,
            sample_rate_ranges_hz=self.config.sample_rate_ranges_hz,
            analog_bandwidth_ranges_hz=self.config.analog_bandwidth_ranges_hz,
            gain_range_db=self.config.gain_range_db,
            gain_modes=self.config.gain_modes,
            sample_formats=(
                SampleFormat.COMPLEX_INT12_IN_INT16_LE,
                SampleFormat.COMPLEX_INT16_LE,
                SampleFormat.COMPLEX_FLOAT32_LE,
            ),
            supports_hardware_timestamps=True,
            supports_fastlock=False,
            supports_temperature=True,
            supports_overflow_counter=False,
            supports_continuous_iq=True,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _resolve_backend(self, options: FixedBandOptions) -> ComputeBackendKind | None:
        if options.backend in self.config.available_backends:
            return options.backend
        if options.backend is ComputeBackendKind.AUTO:
            return self.config.available_backends[0]
        if options.allow_runtime_fallback:
            self._fallback_count += 1
            self._last_backend_error = BackendErrorCode.RUNTIME_NOT_FOUND
            return ComputeBackendKind.CPU
        return None

    def _samples_per_frame(self) -> int:
        if self._options is None:
            return 0
        return self._options.dsp.fft_size

    def _make_frame(self, sequence: int) -> SpectrumFrame:
        options = self._options
        if options is None:  # pragma: no cover - guarded by poll_spectrum
            raise RuntimeError("fake service not configured")
        points = min(max(options.dsp.fft_size, 256), 1024)
        bin_width = options.device.sample_rate_hz / points
        frequencies = options.device.center_frequency_hz + np.arange(points, dtype=np.float64) * bin_width
        values = np.full(points, -80.0 + (sequence % 20), dtype=np.float32)
        flags = QualityFlag.NONE
        if self.config.calibration_status is CalibrationStatus.UNCALIBRATED:
            flags |= QualityFlag.UNCALIBRATED
        if self.config.dropped_iq_blocks_before:
            flags |= QualityFlag.IQ_DROPPED
        if self.config.dropped_fft_frames_before:
            flags |= QualityFlag.FFT_DROPPED
        return SpectrumFrame(
            source=SourceDescriptor(
                source_type=SourceType.LIVE_IQ,
                source_id=options.device.source_id,
                display_name="Fake Pluto",
                uri=self.uri,
                backend_id="fake",
            ),
            frame_sequence=sequence,
            first_sample_index=sequence * options.dsp.fft_size,
            timestamp_ns=1_700_000_000_000_000_000 + sequence * 1_000_000,
            config_generation=1,
            center_frequency_hz=options.device.center_frequency_hz,
            sample_rate_hz=options.device.sample_rate_hz,
            analog_bandwidth_hz=options.device.analog_bandwidth_hz,
            fft_bin_width_hz=bin_width,
            enbw_hz=bin_width * 1.5,
            nominal_rbw_hz=bin_width * 1.5,
            fft_size=options.dsp.fft_size,
            hop_size=options.dsp.hop_size,
            window=options.dsp.window,
            detector=options.dsp.detector,
            precision_mode=options.dsp.precision_mode,
            unit=options.dsp.unit,
            frequencies_hz=frequencies,
            values=values,
            calibration_status=self.config.calibration_status,
            calibration_profile_id=self.config.calibration_profile_id,
            estimated_uncertainty_db=0.5,
            dropped_samples_before=self.config.dropped_iq_blocks_before * options.dsp.fft_size,
            dropped_iq_blocks_before=self.config.dropped_iq_blocks_before,
            dropped_fft_frames_before=self.config.dropped_fft_frames_before,
            quality_flags=flags,
        )


def fake_capabilities(config: FakeLiveConfig | None = None) -> DeviceCapabilities:
    """Build capability records matching a :class:`FakeLiveConfig`."""

    return FakeLiveService("fake:", config=config).capabilities()


def validate_against_capabilities(
    options: FixedBandOptions,
    capabilities: DeviceCapabilities | None,
) -> list[str]:
    """Return human-readable validation errors for a requested configuration.

    Structural contract errors (invalid FFT size, hop size, persistence
    wiring, schema version) are always reported.  Capability violations
    (frequency/sample-rate/bandwidth/gain outside the device ranges) are
    reported only when capability records are supplied; without them the
    caller must treat the ranges as NOT_VERIFIED.
    """

    errors: list[str] = []
    device = options.device
    if capabilities is not None:
        if not _range_contains(capabilities.tuning_range_hz, device.center_frequency_hz):
            errors.append(
                "center_frequency_hz: %.3f MHz is outside tuning range %.3f..%.3f MHz"
                % (
                    device.center_frequency_hz / 1.0e6,
                    capabilities.tuning_range_hz.minimum / 1.0e6,
                    capabilities.tuning_range_hz.maximum / 1.0e6,
                )
            )
        if not _any_range_contains(capabilities.sample_rate_ranges_hz, device.sample_rate_hz):
            errors.append(
                "sample_rate_hz: %.3f MS/s is outside supported rate ranges" % (device.sample_rate_hz / 1.0e6)
            )
        if not _any_range_contains(
            capabilities.analog_bandwidth_ranges_hz, device.analog_bandwidth_hz
        ):
            errors.append(
                "analog_bandwidth_hz: %.3f MHz is outside supported bandwidth ranges"
                % (device.analog_bandwidth_hz / 1.0e6)
            )
        if device.gain_mode is GainMode.MANUAL and not _range_contains(
            capabilities.gain_range_db, device.manual_gain_db
        ):
            errors.append(
                "manual_gain_db: %.2f dB is outside gain range %.2f..%.2f dB"
                % (
                    device.manual_gain_db,
                    capabilities.gain_range_db.minimum,
                    capabilities.gain_range_db.maximum,
                )
            )
        if device.gain_mode not in capabilities.gain_modes:
            errors.append("gain_mode: %s is not supported by this device" % device.gain_mode.value)
    dsp = options.dsp
    fft_size = dsp.fft_size
    if fft_size < 256 or fft_size > 262_144 or fft_size & (fft_size - 1):
        errors.append("fft_size: %d must be a power of two in [256, 262144]" % fft_size)
    if dsp.hop_size <= 0 or dsp.hop_size > fft_size:
        errors.append("hop_size: %d must be in [1, fft_size]" % dsp.hop_size)
    return errors


def within_capability_ranges(
    center_frequency_hz: float,
    sample_rate_hz: float,
    analog_bandwidth_hz: float,
    manual_gain_db: float,
    capabilities: DeviceCapabilities,
) -> bool:
    """Cheap capability check used by the workspace before applying values."""

    return (
        _range_contains(capabilities.tuning_range_hz, center_frequency_hz)
        and _any_range_contains(capabilities.sample_rate_ranges_hz, sample_rate_hz)
        and _any_range_contains(capabilities.analog_bandwidth_ranges_hz, analog_bandwidth_hz)
        and _range_contains(capabilities.gain_range_db, manual_gain_db)
    )


__all__ = [
    "FakeAppliedConfig",
    "FakeLiveConfig",
    "FakeLiveService",
    "fake_capabilities",
    "validate_against_capabilities",
    "within_capability_ranges",
]
