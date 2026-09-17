"""Bounded physical Pluto sweep adapter for the standalone product.

The adapter owns one native engine only while an explicit caller-held device
lease is active.  It transfers reduced SpectrumFrame data in bounded polling
batches; raw I/Q never reaches Python or Qt.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

import numpy as np

from ..domain import (
    BackendKind,
    LiveConfiguration,
    SweepConfiguration,
    SweepExecutionMode,
    SweepPlan,
    SweepProgress,
    SweepQuality,
    SweepRateEvidence,
    SweepRateMetrics,
    SweepResult,
    SweepSegment,
    SweepSegmentEvidence,
    SweepSegmentSpectrum,
    SweepSegmentState,
    SweepState,
)
from .native_live import build_native_fixed_band_config
from .sweep_stitching import SweepStitchOptions, stitch_sweep_segments


_POLL_BATCH = 8
_POLL_INTERVAL_S = 0.01
_MAX_SEGMENTS = 10_000


@dataclass(frozen=True, slots=True)
class NativeSweepSource:
    """An explicit, externally leased Pluto route and applied CPU profile."""

    context_uri: str
    source_id: str
    live_configuration: LiveConfiguration

    def __post_init__(self) -> None:
        if not self.context_uri.strip() or not self.source_id.strip():
            raise ValueError("native sweep source route and identity must not be blank")
        if self.live_configuration.backend is not BackendKind.CPU:
            raise ValueError("native sweep currently requires the verified CPU backend")


@dataclass(frozen=True, slots=True)
class NativeSweepLease:
    """Exclusive control-plane permission supplied by the native Live owner."""

    native_module: Any
    source: NativeSweepSource
    assert_active: Callable[[], None]
    release: Callable[[], None]


@dataclass(frozen=True, slots=True)
class _SegmentMetricValues:
    """Typed native observation retained by one physical sweep segment."""

    transient_blocks_discarded: int
    source_blocks_dropped: int
    acquisition_blocks_dropped: int
    fft_frames_dropped: int
    analytical_fft_lps: float | None
    observed_device_iq_sample_rate_hz: float | None
    observed_device_iq_samples: int | None
    source_short_reads: int | None
    source_refill_errors: int | None
    source_output_pool_exhaustions: int | None
    source_estimated_dropped_samples: int | None
    acquisition_queue_high_water: int | None
    acquisition_queue_capacity: int | None
    spectrum_queue_high_water: int | None
    spectrum_queue_capacity: int | None
    end_to_end_latency_ms: float | None


class NativeSweepService:
    """Qt-free sequential sweep over one explicitly exclusive native route.

    ``assert_exclusive`` is called before engine creation and for every segment.
    The composition root must make it fail while a Live engine, recording epoch
    or another sweep owns the same device.  This adapter does not silently stop
    or restart Live.
    """

    def __init__(
        self,
        native_module: Any,
        source: NativeSweepSource,
        *,
        assert_exclusive: Callable[[], None],
        release_lease: Callable[[], None] | None = None,
        timeout_ms: int = 3000,
    ) -> None:
        if timeout_ms <= 0:
            raise ValueError("native sweep timeout must be positive")
        self._native = native_module
        self._source = source
        self._assert_exclusive = assert_exclusive
        self._release_lease = release_lease
        self._timeout_ms = timeout_ms
        self._cancel_requested = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        self._lease_released = False

    @classmethod
    def from_native_live(cls, live_service: Any) -> "NativeSweepService":
        """Acquire an explicit native-Live lease; never steal a Running Live stream."""

        lease = live_service.acquire_native_sweep_lease()
        return cls(
            lease.native_module,
            lease.source,
            assert_exclusive=lease.assert_active,
            release_lease=lease.release,
        )

    def plan(self, configuration: SweepConfiguration) -> SweepPlan:
        self._assert_open()
        base = self._source.live_configuration
        resolution_hz = base.sample_rate_hz / base.fft_size
        usable_width_hz = base.sample_rate_hz - 2.0 * configuration.dc_margin_hz
        if usable_width_hz < 2.0 * resolution_hz:
            raise ValueError("sweep DC/edge margin leaves fewer than two usable FFT bins")
        stride_hz = usable_width_hz * (1.0 - configuration.overlap_fraction)
        if stride_hz <= 0.0:
            raise ValueError("sweep usable stride must be positive")
        span_hz = configuration.stop_hz - configuration.start_hz
        count = max(1, math.ceil(max(0.0, span_hz - usable_width_hz) / stride_hz) + 1)
        if count > _MAX_SEGMENTS:
            raise ValueError(f"sweep plan exceeds bounded {_MAX_SEGMENTS}-segment limit")
        segments: list[SweepSegment] = []
        for index in range(count):
            usable_start = configuration.start_hz + index * stride_hz
            usable_stop = min(configuration.stop_hz, usable_start + usable_width_hz)
            centre = (usable_start + usable_stop) / 2.0
            capture_start = centre - base.sample_rate_hz / 2.0
            capture_stop = centre + base.sample_rate_hz / 2.0
            if centre <= 0.0:
                raise ValueError("native sweep capture centre must remain positive")
            segments.append(SweepSegment(index, capture_start, capture_stop, usable_start, usable_stop))
        estimate = count * (configuration.settling_s + configuration.dwell_s)
        return SweepPlan(configuration, tuple(segments), estimate, resolution_hz)

    def execute(self, configuration: SweepConfiguration, progress: Callable[[SweepProgress], None]) -> SweepResult:
        self._assert_open()
        if configuration.execution_mode is not SweepExecutionMode.NATIVE:
            raise ValueError("NativeSweepService requires explicit execution_mode=native")
        with self._lock:
            self._cancel_requested.clear()
            self._assert_exclusive()
            try:
                plan = self.plan(configuration)
            except Exception:
                self._release_exclusive_lease()
                raise
            started = time.monotonic()
            engine: Any | None = None
            spectra: list[SweepSegmentSpectrum] = []
            evidence: list[SweepSegmentEvidence] = []
            error_messages: list[str] = []
            cancelled = False
            try:
                engine = self._native.PlutoFixedBandEngine(self._source.context_uri, self._timeout_ms)
                for segment in plan.segments:
                    if self._cancel_requested.is_set():
                        cancelled = True
                        evidence.extend(self._unexecuted_evidence(plan.segments[segment.index:], SweepSegmentState.CANCELLED, "sweep cancelled"))
                        break
                    self._assert_exclusive()
                    progress(SweepProgress(SweepState.RUNNING, segment.index, len(plan.segments), segment.usable_start_hz, "retune/readback"))
                    segment_evidence, spectrum, terminal_error = self._execute_segment(
                        engine,
                        segment,
                        plan,
                        first_segment=segment.index == 0,
                    )
                    evidence.append(segment_evidence)
                    if spectrum is not None:
                        spectra.append(spectrum)
                    progress(SweepProgress(SweepState.RUNNING, segment.index + 1, len(plan.segments), segment.usable_stop_hz, "capture"))
                    if segment_evidence.error is not None and terminal_error != "sweep cancelled":
                        error_messages.append(f"segment {segment.index}: {segment_evidence.error}")
                    if terminal_error:
                        terminal_state = SweepSegmentState.MISSING
                        if terminal_error == "sweep cancelled":
                            cancelled = True
                            terminal_state = SweepSegmentState.CANCELLED
                        evidence.extend(self._unexecuted_evidence(plan.segments[segment.index + 1:], terminal_state, terminal_error))
                        break
            finally:
                self._shutdown_engine(engine)
                self._release_exclusive_lease()

            duration = time.monotonic() - started
            state = SweepState.CANCELLED if cancelled else SweepState.ERROR if error_messages else SweepState.COMPLETED
            grid = None
            if spectra:
                expected_generations = tuple((item.segment_index, item.config_generation) for item in spectra)
                grid = stitch_sweep_segments(
                    plan,
                    spectra,
                    SweepStitchOptions(
                        target_spacing_hz=plan.resolution_hz,
                        expected_generation_by_segment=expected_generations,
                    ),
                )
            missing_segments = len(plan.segments) if grid is None else len(grid.missing_segment_indices)
            loss_segments = sum(
                1
                for item in evidence
                if item.source_blocks_dropped or item.acquisition_blocks_dropped or item.fft_frames_dropped
            )
            rates = self._rates(plan, duration, state, evidence)
            quality = SweepQuality(
                missing_segments,
                max((item.after_p95_db for item in grid.seams), default=None) if grid is not None else None,
                None,
                self._quality_note(state, loss_segments),
                missing_bins=0 if grid is None else grid.missing_bin_count,
                seam_count=0 if grid is None else len(grid.seams),
            )
            progress(SweepProgress(state, len(evidence), len(plan.segments), configuration.stop_hz, "готово"))
            return SweepResult(
                state,
                plan,
                duration,
                quality,
                error="; ".join(error_messages) or None,
                stitched_grid=grid,
                rate_metrics=rates,
                segment_evidence=tuple(evidence),
            )

    def cancel(self) -> None:
        self._cancel_requested.set()

    def close(self) -> None:
        self._closed = True
        self.cancel()
        self._release_exclusive_lease()

    def export_result(self, result: SweepResult, output_path: Path) -> Path:
        payload = {
            "state": result.state.value,
            "duration_seconds": result.duration_seconds,
            "configuration": {
                "start_hz": result.plan.configuration.start_hz,
                "stop_hz": result.plan.configuration.stop_hz,
                "mode": result.plan.configuration.mode.value,
                "band_preset_id": result.plan.configuration.band_preset_id,
                "execution_mode": result.plan.configuration.execution_mode.value,
            },
            "quality": {
                "missing_segments": result.quality.missing_segments,
                "missing_bins": result.quality.missing_bins,
                "seam_p95_db": result.quality.seam_p95_db,
                "note": result.quality.note,
            },
            "rates": None if result.rate_metrics is None else {
                "evidence": result.rate_metrics.evidence.value,
                "megahertz_per_second": result.rate_metrics.megahertz_per_second,
                "sweeps_per_second": result.rate_metrics.sweeps_per_second,
                "fft_lps": result.rate_metrics.fft_lps,
            },
            "segments": [
                {
                    "index": item.segment_index,
                    "state": item.state.value,
                    "requested_center_hz": item.requested_center_hz,
                    "applied_center_hz": item.applied_center_hz,
                    "config_generation": item.config_generation,
                    "reconfigure_seconds": item.reconfigure_seconds,
                    "settling_seconds": item.settling_seconds,
                    "capture_seconds": item.capture_seconds,
                    "accepted_spectrum_frames": item.accepted_spectrum_frames,
                    "rejected_stale_frames": item.rejected_stale_frames,
                    "transient_blocks_discarded": item.transient_blocks_discarded,
                    "source_blocks_dropped": item.source_blocks_dropped,
                    "acquisition_blocks_dropped": item.acquisition_blocks_dropped,
                    "fft_frames_dropped": item.fft_frames_dropped,
                    "analytical_fft_lps": item.analytical_fft_lps,
                    "observed_device_iq_sample_rate_hz": item.observed_device_iq_sample_rate_hz,
                    "observed_device_iq_samples": item.observed_device_iq_samples,
                    "source_short_reads": item.source_short_reads,
                    "source_refill_errors": item.source_refill_errors,
                    "source_output_pool_exhaustions": item.source_output_pool_exhaustions,
                    "source_estimated_dropped_samples": item.source_estimated_dropped_samples,
                    "acquisition_queue_high_water": item.acquisition_queue_high_water,
                    "acquisition_queue_capacity": item.acquisition_queue_capacity,
                    "spectrum_queue_high_water": item.spectrum_queue_high_water,
                    "spectrum_queue_capacity": item.spectrum_queue_capacity,
                    "end_to_end_latency_ms": item.end_to_end_latency_ms,
                    "applied_sample_rate_hz": item.applied_sample_rate_hz,
                    "applied_analog_bandwidth_hz": item.applied_analog_bandwidth_hz,
                    "applied_gain_db": item.applied_gain_db,
                    "error": item.error,
                }
                for item in result.segment_evidence
            ],
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(output_path.suffix + ".part")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, output_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return output_path

    def _execute_segment(
        self,
        engine: Any,
        segment: SweepSegment,
        plan: SweepPlan,
        *,
        first_segment: bool,
    ) -> tuple[SweepSegmentEvidence, SweepSegmentSpectrum | None, str | None]:
        base = self._source.live_configuration
        requested = replace(
            base,
            center_hz=(segment.start_hz + segment.stop_hz) / 2.0,
            persistence_enabled=False,
            persistence_mode="disabled",
        )
        reconfigure_started = time.monotonic()
        segment_metrics_before = engine.metrics()
        applied: Any | None = None
        try:
            native_config = build_native_fixed_band_config(
                self._native,
                requested,
                self._source.context_uri,
                source_id=self._source.source_id,
                discard_blocks_after_start=plan.configuration.discard_blocks,
            )
            if first_segment:
                applied = engine.configure(native_config)
                engine.start()
            else:
                applied = engine.reconfigure(native_config)
        except Exception as error:
            elapsed = time.monotonic() - reconfigure_started
            return (
                SweepSegmentEvidence(
                    segment.index, SweepSegmentState.FAILED, requested.center_hz, None, None,
                    elapsed, 0.0, 0.0, 0, 0, 0, 0, 0, 0, None, str(error),
                ),
                None,
                f"native reconfigure failed: {error}",
            )
        reconfigure_seconds = time.monotonic() - reconfigure_started
        generation_value = getattr(applied, "config_generation", None)
        if generation_value is None:
            generation_value = engine.config_generation()
        generation = int(generation_value)
        applied_center = float(getattr(applied, "center_frequency_hz", requested.center_hz))
        applied_sample_rate = _optional_positive_float(applied, "sample_rate_hz")
        applied_bandwidth = _optional_positive_float(applied, "analog_bandwidth_hz")
        applied_gain = _optional_finite_float(applied, "manual_gain_db")
        settle_started = time.monotonic()
        if not self._wait_cancellable(plan.configuration.settling_s):
            return (
                SweepSegmentEvidence(
                    segment.index, SweepSegmentState.CANCELLED, requested.center_hz, applied_center, generation,
                    reconfigure_seconds, time.monotonic() - settle_started, 0.0, 0, 0, 0, 0, 0, 0, None, "sweep cancelled",
                ),
                None,
                "sweep cancelled",
            )
        settling_seconds = time.monotonic() - settle_started
        capture_started = time.monotonic()
        capture_metrics_before = engine.metrics()
        frame, accepted, stale = self._capture_latest_frame(engine, generation, plan.configuration.dwell_s)
        capture_seconds = time.monotonic() - capture_started
        metrics = engine.metrics()
        metric_values = _metric_values(
            metrics,
            segment_metrics_before,
            capture_metrics_before,
            capture_seconds,
        )
        if self._cancel_requested.is_set():
            return (
                _evidence_from_metrics(
                    segment.index, SweepSegmentState.CANCELLED, requested.center_hz, applied_center, generation,
                    reconfigure_seconds, settling_seconds, capture_seconds, accepted, stale,
                    metric_values, "sweep cancelled",
                    applied_sample_rate_hz=applied_sample_rate,
                    applied_analog_bandwidth_hz=applied_bandwidth,
                    applied_gain_db=applied_gain,
                ),
                None,
                "sweep cancelled",
            )
        if frame is None:
            message = "no post-settling spectrum frame with the applied configuration generation"
            return (
                _evidence_from_metrics(
                    segment.index, SweepSegmentState.MISSING, requested.center_hz, applied_center, generation,
                    reconfigure_seconds, settling_seconds, capture_seconds, accepted, stale,
                    metric_values, message,
                    applied_sample_rate_hz=applied_sample_rate,
                    applied_analog_bandwidth_hz=applied_bandwidth,
                    applied_gain_db=applied_gain,
                ),
                None,
                None,
            )
        spectrum = SweepSegmentSpectrum(
            segment.index,
            self._source.source_id,
            generation,
            np.asarray(frame.frequencies_hz),
            np.asarray(frame.values),
            _frame_unit(frame),
        )
        return (
            _evidence_from_metrics(
                segment.index, SweepSegmentState.COMPLETED, requested.center_hz, applied_center, generation,
                reconfigure_seconds, settling_seconds, capture_seconds, accepted, stale, metric_values,
                applied_sample_rate_hz=applied_sample_rate,
                applied_analog_bandwidth_hz=applied_bandwidth,
                applied_gain_db=applied_gain,
            ),
            spectrum,
            None,
        )

    def _capture_latest_frame(self, engine: Any, generation: int, dwell_s: float) -> tuple[Any | None, int, int]:
        deadline = time.monotonic() + dwell_s
        latest = None
        accepted = 0
        stale = 0
        while True:
            if self._cancel_requested.is_set():
                return None, accepted, stale
            for frame in tuple(engine.poll_spectrum_frames(_POLL_BATCH)):
                if int(getattr(frame, "config_generation", -1)) != generation:
                    stale += 1
                    continue
                latest = frame
                accepted += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return latest, accepted, stale
            self._cancel_requested.wait(min(_POLL_INTERVAL_S, remaining))

    def _wait_cancellable(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while True:
            if self._cancel_requested.is_set():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return True
            self._cancel_requested.wait(min(_POLL_INTERVAL_S, remaining))

    @staticmethod
    def _unexecuted_evidence(
        segments: tuple[SweepSegment, ...], state: SweepSegmentState, error: str
    ) -> list[SweepSegmentEvidence]:
        return [
            SweepSegmentEvidence(
                segment.index, state, (segment.start_hz + segment.stop_hz) / 2.0, None, None,
                0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, None, error,
            )
            for segment in segments
        ]

    @staticmethod
    def _shutdown_engine(engine: Any | None) -> None:
        if engine is None:
            return
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

    @staticmethod
    def _quality_note(state: SweepState, loss_segments: int) -> str:
        if state is SweepState.CANCELLED:
            return "Sweep cancelled; unexecuted segments remain explicit."
        if state is SweepState.ERROR:
            return "Sweep completed with failed or missing segment evidence."
        return "Native segment readback completed; calibration/dBm remains unverified." if loss_segments == 0 else f"Native sweep observed loss counters in {loss_segments} segment(s)."

    @staticmethod
    def _rates(
        plan: SweepPlan,
        duration: float,
        state: SweepState,
        evidence: list[SweepSegmentEvidence],
    ) -> SweepRateMetrics:
        if state is not SweepState.COMPLETED or duration <= 0.0:
            return SweepRateMetrics(SweepRateEvidence.NOT_MEASURED, duration, None, None, None)
        fft_rates = [item.analytical_fft_lps for item in evidence if item.analytical_fft_lps is not None]
        return SweepRateMetrics(
            SweepRateEvidence.MEASURED,
            duration,
            (plan.configuration.stop_hz - plan.configuration.start_hz) / duration / 1e6,
            1.0 / duration,
            sum(fft_rates) / len(fft_rates) if fft_rates else None,
        )

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("native sweep service is closed")

    def _release_exclusive_lease(self) -> None:
        if self._release_lease is not None and not self._lease_released:
            self._lease_released = True
            self._release_lease()


def _metric_values(
    metrics: Any,
    segment_before: Any,
    capture_before: Any,
    capture_seconds: float,
) -> _SegmentMetricValues:
    """Return deltas for one segment and bounded queue/latency observations.

    Native counters are monotonic for one engine.  A decreasing counter is a
    broken evidence run, not a value to clamp silently.  Queue high-water is
    intentionally the engine-cumulative maximum: it answers whether the fixed
    capacity was approached anywhere up to this segment.
    """

    engine = getattr(metrics, "engine", None)
    device = getattr(metrics, "device", None)
    before_engine = getattr(segment_before, "engine", None)
    before_device = getattr(segment_before, "device", None)
    capture_device = getattr(capture_before, "device", None)
    acquisition_high_water, acquisition_capacity = _queue_observation(metrics, "acquisition_queue")
    spectrum_high_water, spectrum_capacity = _queue_observation(metrics, "spectrum_queue")
    captured_samples = _optional_counter_delta(device, capture_device, "samples_received")
    return _SegmentMetricValues(
        transient_blocks_discarded=_counter_delta(
            metrics, segment_before, "transient_blocks_discarded"
        ),
        source_blocks_dropped=_counter_delta(device, before_device, "output_blocks_dropped"),
        acquisition_blocks_dropped=_counter_delta(
            metrics, segment_before, "acquisition_queue_blocks_dropped"
        ),
        fft_frames_dropped=_counter_delta(engine, before_engine, "fft_frames_dropped"),
        analytical_fft_lps=_optional_positive_float(engine, "analytical_fft_rate"),
        observed_device_iq_sample_rate_hz=(
            None if captured_samples is None else captured_samples / max(capture_seconds, 1.0e-9)
        ),
        observed_device_iq_samples=captured_samples,
        source_short_reads=_optional_counter_delta(device, before_device, "short_reads"),
        source_refill_errors=_optional_counter_delta(device, before_device, "refill_errors"),
        source_output_pool_exhaustions=_optional_counter_delta(
            device, before_device, "output_pool_exhaustions"
        ),
        source_estimated_dropped_samples=_optional_counter_delta(
            device, before_device, "estimated_dropped_samples"
        ),
        acquisition_queue_high_water=acquisition_high_water,
        acquisition_queue_capacity=acquisition_capacity,
        spectrum_queue_high_water=spectrum_high_water,
        spectrum_queue_capacity=spectrum_capacity,
        end_to_end_latency_ms=_optional_non_negative_float(engine, "end_to_end_latency_ms"),
    )


def _evidence_from_metrics(
    segment_index: int,
    state: SweepSegmentState,
    requested_center_hz: float,
    applied_center_hz: float | None,
    config_generation: int | None,
    reconfigure_seconds: float,
    settling_seconds: float,
    capture_seconds: float,
    accepted_spectrum_frames: int,
    rejected_stale_frames: int,
    metrics: _SegmentMetricValues,
    error: str | None = None,
    *,
    applied_sample_rate_hz: float | None = None,
    applied_analog_bandwidth_hz: float | None = None,
    applied_gain_db: float | None = None,
) -> SweepSegmentEvidence:
    return SweepSegmentEvidence(
        segment_index,
        state,
        requested_center_hz,
        applied_center_hz,
        config_generation,
        reconfigure_seconds,
        settling_seconds,
        capture_seconds,
        accepted_spectrum_frames,
        rejected_stale_frames,
        metrics.transient_blocks_discarded,
        metrics.source_blocks_dropped,
        metrics.acquisition_blocks_dropped,
        metrics.fft_frames_dropped,
        metrics.analytical_fft_lps,
        error,
        metrics.observed_device_iq_sample_rate_hz,
        metrics.observed_device_iq_samples,
        metrics.source_short_reads,
        metrics.source_refill_errors,
        metrics.source_output_pool_exhaustions,
        metrics.source_estimated_dropped_samples,
        metrics.acquisition_queue_high_water,
        metrics.acquisition_queue_capacity,
        metrics.spectrum_queue_high_water,
        metrics.spectrum_queue_capacity,
        metrics.end_to_end_latency_ms,
        applied_sample_rate_hz,
        applied_analog_bandwidth_hz,
        applied_gain_db,
    )


def _counter_delta(after: Any, before: Any, name: str) -> int:
    after_value = _counter(after, name)
    before_value = _counter(before, name)
    if after_value < before_value:
        raise RuntimeError(f"native sweep metric counter decreased: {name}")
    return after_value - before_value


def _optional_counter_delta(after: Any, before: Any, name: str) -> int | None:
    if not hasattr(after, name) or not hasattr(before, name):
        return None
    return _counter_delta(after, before, name)


def _counter(value: Any, name: str) -> int:
    numeric = int(getattr(value, name, 0) or 0)
    if numeric < 0:
        raise RuntimeError(f"native sweep metric counter is negative: {name}")
    return numeric


def _queue_observation(metrics: Any, name: str) -> tuple[int | None, int | None]:
    queue = getattr(metrics, name, None)
    if queue is None or not hasattr(queue, "high_water") or not hasattr(queue, "capacity"):
        return None, None
    high_water = _counter(queue, "high_water")
    capacity = _counter(queue, "capacity")
    if capacity == 0 or high_water > capacity:
        raise RuntimeError(f"native sweep {name} evidence is outside its bounded capacity")
    return high_water, capacity


def _optional_positive_float(value: Any, name: str) -> float | None:
    numeric = _optional_non_negative_float(value, name)
    return numeric if numeric is not None and numeric > 0.0 else None


def _optional_non_negative_float(value: Any, name: str) -> float | None:
    if not hasattr(value, name):
        return None
    numeric = float(getattr(value, name))
    if not math.isfinite(numeric) or numeric < 0.0:
        raise RuntimeError(f"native sweep metric is invalid: {name}")
    return numeric


def _optional_finite_float(value: Any, name: str) -> float | None:
    if not hasattr(value, name):
        return None
    numeric = float(getattr(value, name))
    if not math.isfinite(numeric):
        raise RuntimeError(f"native sweep metric is invalid: {name}")
    return numeric


def _frame_unit(frame: Any) -> str:
    value = getattr(frame, "unit", None)
    name = str(getattr(value, "name", value or "")).upper()
    if "DBFS" in name:
        return "dBFS/Hz" if "HZ" in name else "dBFS/bin"
    return "dBFS/bin"


class NativeLiveSweepService:
    """Explicit synthetic/native selector for a NativeLiveSessionService owner.

    Planning a native run acquires and immediately releases an exclusive lease;
    execution holds that lease until the engine is joined.  The configured
    source mode decides the path—there is no automatic hardware fallback.
    """

    def __init__(self, native_live_service: Any) -> None:
        self._native_live_service = native_live_service
        self._synthetic: Any | None = None
        self._active_native: NativeSweepService | None = None
        self._last_native_exporter: NativeSweepService | None = None
        self._lock = threading.Lock()
        self._closed = False

    def plan(self, configuration: SweepConfiguration) -> SweepPlan:
        self._assert_open()
        if configuration.execution_mode is SweepExecutionMode.SYNTHETIC:
            return self._synthetic_service().plan(configuration)
        service = NativeSweepService.from_native_live(self._native_live_service)
        try:
            return service.plan(configuration)
        finally:
            service.close()

    def execute(self, configuration: SweepConfiguration, progress: Callable[[SweepProgress], None]) -> SweepResult:
        self._assert_open()
        if configuration.execution_mode is SweepExecutionMode.SYNTHETIC:
            return self._synthetic_service().execute(configuration, progress)
        with self._lock:
            if self._active_native is not None:
                raise RuntimeError("native sweep is already running")
            service = NativeSweepService.from_native_live(self._native_live_service)
            self._active_native = service
        try:
            result = service.execute(configuration, progress)
            self._last_native_exporter = service
            return result
        finally:
            with self._lock:
                self._active_native = None
            service.close()

    def cancel(self) -> None:
        with self._lock:
            native = self._active_native
        if native is not None:
            native.cancel()
        if self._synthetic is not None:
            self._synthetic.cancel()

    def close(self) -> None:
        self._closed = True
        self.cancel()
        if self._synthetic is not None:
            self._synthetic.close()

    def export_result(self, result: SweepResult, output_path: Path) -> Path:
        if result.segment_evidence and self._last_native_exporter is not None:
            return self._last_native_exporter.export_result(result, output_path)
        return self._synthetic_service().export_result(result, output_path)

    def _synthetic_service(self) -> Any:
        if self._synthetic is None:
            from .sweep_session import InMemorySweepService

            self._synthetic = InMemorySweepService()
        return self._synthetic

    def _assert_open(self) -> None:
        if self._closed:
            raise RuntimeError("native Live sweep service is closed")


__all__ = ["NativeLiveSweepService", "NativeSweepLease", "NativeSweepService", "NativeSweepSource"]
