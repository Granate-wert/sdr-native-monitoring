"""Standalone recording contracts and bounded source-port types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import json
from typing import Any, Protocol, TypeAlias

import numpy as np

from .identity import (
    ConfigurationGeneration,
    FrameSequence,
    LossReason,
    SourceId,
    TimestampNs,
    TimestampQuality,
    as_configuration_generation,
    as_frame_sequence,
    as_source_id,
    as_timestamp_ns,
    as_timestamp_quality,
    normalize_loss_reasons,
)


_MEBIBYTE = 1024 * 1024


class RecordingKind(StrEnum):
    IQ = "iq"
    SPECTRUM = "spectrum"


class RecordingState(StrEnum):
    IDLE = "idle"
    ARMED = "armed"
    RECORDING = "recording"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    STOP_TIMEOUT = "stop_timeout"


@dataclass(frozen=True, slots=True)
class RecordingResourceEstimate:
    """Maximum resident input queue footprint for one recording session."""

    queue_capacity: int
    max_item_bytes: int
    max_queue_bytes: int
    metadata_bytes: int


@dataclass(frozen=True, slots=True)
class RecordingResourceBudget:
    """Bound recording submissions by count *and* byte size.

    The queue remains intentionally simple and non-blocking. These limits
    ensure an otherwise small queue cannot retain arbitrarily large NumPy
    arrays through a slow disk writer.
    """

    max_queue_capacity: int = 64
    max_iq_block_bytes: int = 4 * _MEBIBYTE
    max_spectrum_frame_bytes: int = 4 * _MEBIBYTE
    max_metadata_bytes: int = 64 * 1024
    max_queue_bytes: int = 256 * _MEBIBYTE

    def estimate(self, options: "RecordingOptions") -> RecordingResourceEstimate:
        item_bytes = max(
            self.max_iq_block_bytes if options.record_iq else 0,
            self.max_spectrum_frame_bytes if options.record_spectrum else 0,
        )
        metadata_bytes = len(_json_bytes(options.metadata))
        return RecordingResourceEstimate(
            queue_capacity=options.queue_capacity,
            max_item_bytes=item_bytes,
            max_queue_bytes=options.queue_capacity * item_bytes,
            metadata_bytes=metadata_bytes,
        )

    def validate_options(self, options: "RecordingOptions") -> RecordingResourceEstimate:
        estimate = self.estimate(options)
        if options.queue_capacity > self.max_queue_capacity:
            raise ValueError(
                f"recording queue capacity exceeds budget: {options.queue_capacity} > {self.max_queue_capacity}"
            )
        if estimate.metadata_bytes > self.max_metadata_bytes:
            raise ValueError(
                f"recording metadata exceeds budget: {estimate.metadata_bytes} > {self.max_metadata_bytes} bytes"
            )
        if estimate.max_queue_bytes > self.max_queue_bytes:
            raise ValueError(
                f"recording queue memory exceeds budget: {estimate.max_queue_bytes / _MEBIBYTE:.1f} MiB "
                f"> {self.max_queue_bytes / _MEBIBYTE:.1f} MiB"
            )
        return estimate

    def validate_submission(self, kind: str, value: object) -> None:
        if kind == RecordingKind.IQ.value:
            if not isinstance(value, IQBlock):
                raise ValueError("IQ recording submission has an invalid type")
            size = int(value.samples.nbytes)
            limit = self.max_iq_block_bytes
        elif kind == RecordingKind.SPECTRUM.value:
            if not isinstance(value, SpectrumFrame):
                raise ValueError("spectrum recording submission has an invalid type")
            size = int(value.frequencies_hz.nbytes + value.values.nbytes)
            limit = self.max_spectrum_frame_bytes
        elif kind == "metadata":
            size = len(_json_bytes(value))
            limit = self.max_metadata_bytes
        else:
            raise ValueError(f"unsupported recording submission kind: {kind}")
        if size > limit:
            raise ValueError(f"{kind} recording submission exceeds budget: {size} > {limit} bytes")


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError(f"recording metadata must be JSON serializable: {error}") from error


DEFAULT_RECORDING_RESOURCE_BUDGET = RecordingResourceBudget()


@dataclass(frozen=True, slots=True)
class RecordingOptions:
    output_path: str
    record_iq: bool = True
    record_spectrum: bool = False
    sample_rate_hz: float = 1e6
    center_frequency_hz: float = 0.0
    queue_capacity: int = 64
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.output_path.strip():
            raise ValueError("recording output path must not be empty")
        if not self.record_iq and not self.record_spectrum:
            raise ValueError("enable IQ and/or spectrum recording")
        if self.sample_rate_hz <= 0 or self.queue_capacity <= 0:
            raise ValueError("sample rate and queue capacity must be positive")
        if self.center_frequency_hz < 0:
            raise ValueError("center frequency must not be negative")
        DEFAULT_RECORDING_RESOURCE_BUDGET.validate_options(self)


@dataclass(frozen=True, slots=True)
class IQBlock:
    sequence: FrameSequence
    timestamp_ns: TimestampNs
    samples: np.ndarray
    sample_rate_hz: float
    source_id: SourceId = SourceId("live")
    config_generation: ConfigurationGeneration = ConfigurationGeneration(0)
    timestamp_quality: TimestampQuality = TimestampQuality.UNKNOWN
    loss_reasons: tuple[LossReason, ...] = ()

    def __post_init__(self) -> None:
        data = np.asarray(self.samples)
        if data.ndim != 1 or data.size == 0:
            raise ValueError("IQ block must be a non-empty one-dimensional array")
        if not np.iscomplexobj(data):
            raise ValueError("IQ block must contain complex samples")
        object.__setattr__(self, "samples", data)
        object.__setattr__(self, "sequence", as_frame_sequence(self.sequence))
        object.__setattr__(self, "timestamp_ns", as_timestamp_ns(self.timestamp_ns))
        object.__setattr__(self, "source_id", as_source_id(self.source_id))
        object.__setattr__(self, "config_generation", as_configuration_generation(self.config_generation))
        object.__setattr__(self, "timestamp_quality", as_timestamp_quality(self.timestamp_quality))
        object.__setattr__(self, "loss_reasons", normalize_loss_reasons(self.loss_reasons))
        if self.sample_rate_hz <= 0:
            raise ValueError("invalid IQ block metadata")


@dataclass(frozen=True, slots=True)
class SpectrumFrame:
    sequence: FrameSequence
    timestamp_ns: TimestampNs
    frequencies_hz: np.ndarray
    values: np.ndarray
    unit: str = "dBFS/bin"
    source_id: SourceId = SourceId("live")
    config_generation: ConfigurationGeneration = ConfigurationGeneration(0)
    calibration_profile_id: str | None = None
    timestamp_quality: TimestampQuality = TimestampQuality.UNKNOWN
    loss_reasons: tuple[LossReason, ...] = ()

    def __post_init__(self) -> None:
        frequencies = np.asarray(self.frequencies_hz, dtype=np.float64).reshape(-1)
        values = np.asarray(self.values, dtype=np.float64).reshape(-1)
        if frequencies.size == 0 or values.size != frequencies.size or not self.unit.strip():
            raise ValueError("spectrum frame requires equal non-empty arrays and a unit")
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "sequence", as_frame_sequence(self.sequence))
        object.__setattr__(self, "timestamp_ns", as_timestamp_ns(self.timestamp_ns))
        object.__setattr__(self, "source_id", as_source_id(self.source_id))
        object.__setattr__(self, "config_generation", as_configuration_generation(self.config_generation))
        object.__setattr__(self, "timestamp_quality", as_timestamp_quality(self.timestamp_quality))
        object.__setattr__(self, "loss_reasons", normalize_loss_reasons(self.loss_reasons))


@dataclass(frozen=True, slots=True)
class RecordingHealth:
    state: RecordingState
    queue_depth: int
    queue_capacity: int
    iq_blocks: int
    spectrum_frames: int
    drops: int
    gaps: int
    bytes_written: int
    output_path: str | None = None
    error: str = ""
    disk_free_bytes: int | None = None
    drop_reasons: tuple[LossReason, ...] = ()
    # R08-C1 native RTBW recorder telemetry.  These fields are deliberately
    # appended so the legacy framed Python writer keeps its public contract.
    recording_mode: str = ""
    epoch: int = 0
    recorded_iq_samples: int = 0
    average_iq_sample_rate_hz: float = 0.0
    iq_loss_rate: float = 0.0
    restart_gap_duration_ns: int | None = None


@dataclass(frozen=True, slots=True)
class RecordingResult:
    output_path: str
    state: RecordingState
    iq_blocks: int
    spectrum_frames: int
    drops: int
    gaps: int
    bytes_written: int
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    drop_reasons: tuple[LossReason, ...] = ()


RecordedFrame: TypeAlias = IQBlock | SpectrumFrame


class RecordingSourcePort(Protocol):
    """Live publication point; recording subscribes without blocking producer."""

    def add_recording_sink(self, sink: Any) -> None: ...
    def remove_recording_sink(self, sink: Any) -> None: ...


__all__ = [
    "IQBlock",
    "DEFAULT_RECORDING_RESOURCE_BUDGET",
    "RecordingHealth",
    "RecordingKind",
    "RecordingOptions",
    "RecordingResourceBudget",
    "RecordingResourceEstimate",
    "RecordingResult",
    "RecordingSourcePort",
    "RecordingState",
    "RecordedFrame",
    "SpectrumFrame",
]
