"""Thread-agnostic Python control plane for the native Pluto/libiio RX backend.

The service performs no GUI work and never enables TX. Blocking native calls
release the GIL in the pybind layer, so callers should run streaming loops in a
worker thread and use :meth:`cancel` to interrupt a pending refill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .contracts import (
    DeviceCapabilities,
    DeviceConfig,
    GainMode,
    IqBlock,
    NumericRange,
    QualityFlag,
    SampleFormat,
    config_to_native,
)
from .native_api import require_native


@dataclass(frozen=True, slots=True)
class PlutoContextSummary:
    uri: str
    description: str


@dataclass(frozen=True, slots=True)
class PlutoSampleLayout:
    storage_bits: int
    significant_bits: int
    shift: int
    is_signed: bool
    is_big_endian: bool
    repeat: int
    stride_bytes: int
    output_format: SampleFormat


@dataclass(frozen=True, slots=True)
class PlutoAppliedConfig:
    center_frequency_hz: float
    sample_rate_hz: float
    analog_bandwidth_hz: float
    gain_mode: GainMode
    manual_gain_db: float
    config_generation: int
    sample_layout: PlutoSampleLayout


@dataclass(frozen=True, slots=True)
class PlutoStreamMetrics:
    blocks_received: int
    samples_received: int
    short_reads: int
    refill_errors: int
    output_pool_exhaustions: int
    output_blocks_dropped: int
    estimated_dropped_samples: int


def _enum(enum_type: type[Any], native_value: Any) -> Any:
    return enum_type[native_value.name]


def _range(value: Any) -> NumericRange:
    return NumericRange(float(value.minimum), float(value.maximum), None if value.step is None else float(value.step))


def discover_pluto(filter: str = "usb,ip") -> tuple[PlutoContextSummary, ...]:
    """Return immutable Pluto-only discovery results for USB/IP backends."""

    native = require_native()
    return tuple(PlutoContextSummary(str(item.uri), str(item.description)) for item in native.scan_pluto_contexts(filter))


class PlutoDeviceService:
    """Own one native libiio context and its non-cyclic RX buffer."""

    def __init__(self, uri: str, *, timeout_ms: int = 3000) -> None:
        self._native = require_native()
        self._device = self._native.PlutoDevice(uri, timeout_ms)

    @property
    def connected(self) -> bool:
        return bool(self._device.connected)

    @property
    def streaming(self) -> bool:
        return bool(self._device.streaming)

    @property
    def uri(self) -> str:
        return str(self._device.uri)

    def capabilities(self) -> DeviceCapabilities:
        value = self._device.capabilities()
        return DeviceCapabilities(
            backend_id=str(value.backend_id),
            device_id=str(value.device_id),
            serial=str(value.serial),
            model=str(value.model),
            firmware=str(value.firmware),
            tuning_range_hz=_range(value.tuning_range_hz),
            sample_rate_ranges_hz=tuple(_range(item) for item in value.sample_rate_ranges_hz),
            analog_bandwidth_ranges_hz=tuple(_range(item) for item in value.analog_bandwidth_ranges_hz),
            gain_range_db=_range(value.gain_range_db),
            gain_modes=tuple(_enum(GainMode, item) for item in value.gain_modes),
            sample_formats=tuple(_enum(SampleFormat, item) for item in value.sample_formats),
            supports_hardware_timestamps=bool(value.supports_hardware_timestamps),
            supports_fastlock=bool(value.supports_fastlock),
            supports_temperature=bool(value.supports_temperature),
            supports_overflow_counter=bool(value.supports_overflow_counter),
            supports_continuous_iq=bool(value.supports_continuous_iq),
            schema_version=int(value.schema_version),
        )

    def configure(self, config: DeviceConfig) -> PlutoAppliedConfig:
        value = self._device.configure(config_to_native(config))
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

    def start(self) -> None:
        self._device.start_stream()

    def read_block(self) -> IqBlock:
        value = self._device.refill()
        return IqBlock(
            source_sequence=int(value.source_sequence),
            first_sample_index=int(value.first_sample_index),
            timestamp_ns=int(value.timestamp_ns),
            center_frequency_hz=float(value.center_frequency_hz),
            sample_rate_hz=float(value.sample_rate_hz),
            sample_format=_enum(SampleFormat, value.sample_format),
            sample_count=int(value.sample_count),
            flags=QualityFlag(int(value.flags)),
            samples=np.asarray(value.samples, dtype=np.uint8),
            config_generation=int(value.config_generation),
        )

    def metrics(self) -> PlutoStreamMetrics:
        value = self._device.metrics()
        return PlutoStreamMetrics(
            blocks_received=int(value.blocks_received),
            samples_received=int(value.samples_received),
            short_reads=int(value.short_reads),
            refill_errors=int(value.refill_errors),
            output_pool_exhaustions=int(value.output_pool_exhaustions),
            output_blocks_dropped=int(value.output_blocks_dropped),
            estimated_dropped_samples=int(value.estimated_dropped_samples),
        )

    def cancel(self) -> None:
        self._device.cancel()

    def stop(self) -> None:
        self._device.stop_stream()

    def disconnect(self) -> None:
        self._device.disconnect()

    def __enter__(self) -> "PlutoDeviceService":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.disconnect()