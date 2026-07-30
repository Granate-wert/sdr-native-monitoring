"""Headless P07 fixed-band control plane.

The native engine owns the Pluto stream, bounded queues, and CPU DSP threads.
Python only performs coarse lifecycle calls and polls immutable, rate-limited
spectrum snapshots. It is never called once per I/Q block or analytical FFT.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
from typing import Any

from .contracts import (
    CONTRACT_SCHEMA_VERSION,
    DeviceConfig,
    DspConfig,
    EngineMetrics,
    EngineState,
    EventSeverity,
    GainMode,
    OverflowPolicy,
    SampleFormat,
    SpectrumFrame,
    config_to_native,
)
from .native_api import require_native
from .pluto import PlutoAppliedConfig, PlutoSampleLayout, PlutoStreamMetrics


_UINT32_MAX = (1 << 32) - 1


def _require_uint32(value: object, name: str, *, positive: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    minimum = 1 if positive else 0
    if value < minimum or value > _UINT32_MAX:
        qualifier = "positive " if positive else ""
        raise ValueError(f"{name} must be a {qualifier}uint32")
    return value


@dataclass(frozen=True, slots=True)
class FixedBandOptions:
    """Bounded transport and publication settings for one Pluto stream."""

    device: DeviceConfig
    dsp: DspConfig
    acquisition_queue_capacity: int = 16
    acquisition_overflow: OverflowPolicy = OverflowPolicy.DROP_NEWEST
    spectrum_queue_capacity: int = 4
    event_queue_capacity: int = 64
    snapshot_rate_hz: float = 60.0
    discard_blocks_after_start: int = 2
    dc_removal_block_mean: bool = False
    schema_version: int = CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONTRACT_SCHEMA_VERSION:
            raise ValueError("unsupported fixed-band schema_version")
        for name in (
            "acquisition_queue_capacity",
            "spectrum_queue_capacity",
            "event_queue_capacity",
        ):
            _require_uint32(getattr(self, name), name, positive=True)
        if (
            isinstance(self.snapshot_rate_hz, bool)
            or not isinstance(self.snapshot_rate_hz, (int, float))
            or not math.isfinite(self.snapshot_rate_hz)
            or self.snapshot_rate_hz <= 0.0
        ):
            raise ValueError("snapshot_rate_hz must be finite and positive")
        _require_uint32(
            self.discard_blocks_after_start,
            "discard_blocks_after_start",
            positive=False,
        )


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    capacity: int
    depth: int
    high_water: int
    pushed: int
    popped: int
    dropped: int
    abandoned: int
    stop_requested: bool


@dataclass(frozen=True, slots=True)
class FixedBandMetricsSnapshot:
    state: EngineState
    has_error: bool
    engine: EngineMetrics
    device: PlutoStreamMetrics
    acquisition_queue: QueueSnapshot
    spectrum_queue: QueueSnapshot
    transient_blocks_discarded: int
    transient_samples_discarded: int
    spectrum_snapshots_superseded: int
    shutdown_blocks_discarded: int
    shutdown_samples_discarded: int
    expected_cancellations: int
    diagnostic_events_lost: int

    @property
    def healthy(self) -> bool:
        """True when the analytical path has no unexplained loss/error."""

        unexpected_refill_errors = max(
            0,
            self.device.refill_errors - self.expected_cancellations,
        )
        unexpected_iq_drops = max(
            0,
            self.engine.iq_blocks_dropped - self.shutdown_blocks_discarded,
        )
        return (
            self.state is not EngineState.ERROR
            and not self.has_error
            and unexpected_refill_errors == 0
            and unexpected_iq_drops == 0
            and self.device.estimated_dropped_samples == 0
            and self.device.output_blocks_dropped == 0
            and self.engine.fft_frames_dropped == 0
            and self.diagnostic_events_lost == 0
        )



@dataclass(frozen=True, slots=True)
class FixedBandEvent:
    severity: EventSeverity
    code: str
    message: str
    timestamp_ns: int
    sequence: int


def _enum(enum_type: type[Any], native_value: Any) -> Any:
    return enum_type[native_value.name]


def _applied(value: Any) -> PlutoAppliedConfig:
    layout = value.sample_layout
    return PlutoAppliedConfig(
        center_frequency_hz=float(value.center_frequency_hz),
        sample_rate_hz=float(value.sample_rate_hz),
        analog_bandwidth_hz=float(value.analog_bandwidth_hz),
        gain_mode=_enum(GainMode, value.gain_mode),
        manual_gain_db=float(value.manual_gain_db),
        config_generation=int(value.config_generation),
        sample_layout=PlutoSampleLayout(
            storage_bits=int(layout.storage_bits),
            significant_bits=int(layout.significant_bits),
            shift=int(layout.shift),
            is_signed=bool(layout.is_signed),
            is_big_endian=bool(layout.is_big_endian),
            repeat=int(layout.repeat),
            stride_bytes=int(layout.stride_bytes),
            output_format=_enum(SampleFormat, layout.output_format),
        ),
    )


def _queue(value: Any) -> QueueSnapshot:
    return QueueSnapshot(
        capacity=int(value.capacity),
        depth=int(value.depth),
        high_water=int(value.high_water),
        pushed=int(value.pushed),
        popped=int(value.popped),
        dropped=int(value.dropped),
        abandoned=int(value.abandoned),
        stop_requested=bool(value.stop_requested),
    )


def _engine_metrics(value: Any) -> EngineMetrics:
    return EngineMetrics(
        **{field.name: getattr(value, field.name) for field in fields(EngineMetrics)}
    )


def _stream_metrics(value: Any) -> PlutoStreamMetrics:
    return PlutoStreamMetrics(
        blocks_received=int(value.blocks_received),
        samples_received=int(value.samples_received),
        short_reads=int(value.short_reads),
        refill_errors=int(value.refill_errors),
        output_pool_exhaustions=int(value.output_pool_exhaustions),
        output_blocks_dropped=int(value.output_blocks_dropped),
        estimated_dropped_samples=int(value.estimated_dropped_samples),
    )


class FixedBandEngineService:
    """Own exactly one native fixed-band engine and one Pluto device."""

    def __init__(self, uri: str, *, timeout_ms: int = 3000) -> None:
        self._native = require_native()
        self._engine = self._native.PlutoFixedBandEngine(uri, timeout_ms)

    @property
    def connected(self) -> bool:
        return bool(self._engine.connected)

    @property
    def streaming(self) -> bool:
        return bool(self._engine.streaming)

    @property
    def state(self) -> EngineState:
        return _enum(EngineState, self._engine.state())

    @property
    def config_generation(self) -> int:
        return int(self._engine.config_generation())

    def _native_config(self, options: FixedBandOptions) -> Any:
        return self._native.FixedBandConfig(
            config_to_native(options.device),
            config_to_native(options.dsp),
            options.acquisition_queue_capacity,
            getattr(self._native.OverflowPolicy, options.acquisition_overflow.name),
            options.spectrum_queue_capacity,
            options.event_queue_capacity,
            float(options.snapshot_rate_hz),
            options.discard_blocks_after_start,
            bool(options.dc_removal_block_mean),
        )

    def configure(self, options: FixedBandOptions) -> PlutoAppliedConfig:
        return _applied(self._engine.configure(self._native_config(options)))

    def reconfigure(self, options: FixedBandOptions) -> PlutoAppliedConfig:
        return _applied(self._engine.reconfigure(self._native_config(options)))

    def applied_config(self) -> PlutoAppliedConfig:
        return _applied(self._engine.applied_config())

    def start(self) -> None:
        self._engine.start()

    def request_stop(self) -> None:
        self._engine.request_stop()

    def join(self) -> None:
        self._engine.join()

    def stop(self) -> None:
        self._engine.stop()

    def disconnect(self) -> None:
        self._engine.disconnect()

    def poll_spectrum(self, max_items: int = 0) -> tuple[SpectrumFrame, ...]:
        if max_items < 0:
            raise ValueError("max_items must not be negative")
        return tuple(
            SpectrumFrame.from_native(value)
            for value in self._engine.poll_spectrum_frames(max_items)
        )

    def poll_events(self, max_items: int = 0) -> tuple[FixedBandEvent, ...]:
        if max_items < 0:
            raise ValueError("max_items must not be negative")
        return tuple(
            FixedBandEvent(
                severity=_enum(EventSeverity, value.severity),
                code=str(value.code),
                message=str(value.message),
                timestamp_ns=int(value.timestamp_ns),
                sequence=int(value.sequence),
            )
            for value in self._engine.poll_events(max_items)
        )

    def metrics(self) -> FixedBandMetricsSnapshot:
        value = self._engine.metrics()
        return FixedBandMetricsSnapshot(
            state=_enum(EngineState, value.state),
            has_error=bool(value.has_error),
            engine=_engine_metrics(value.engine),
            device=_stream_metrics(value.device),
            acquisition_queue=_queue(value.acquisition_queue),
            spectrum_queue=_queue(value.spectrum_queue),
            transient_blocks_discarded=int(value.transient_blocks_discarded),
            transient_samples_discarded=int(value.transient_samples_discarded),
            spectrum_snapshots_superseded=int(value.spectrum_snapshots_superseded),
            shutdown_blocks_discarded=int(value.shutdown_blocks_discarded),
            shutdown_samples_discarded=int(value.shutdown_samples_discarded),
            expected_cancellations=int(value.expected_cancellations),
            diagnostic_events_lost=int(value.diagnostic_events_lost),
        )

    def __enter__(self) -> "FixedBandEngineService":
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> None:
        self.disconnect()


__all__ = [
    "FixedBandEngineService",
    "FixedBandEvent",
    "FixedBandMetricsSnapshot",
    "FixedBandOptions",
    "QueueSnapshot",
]
