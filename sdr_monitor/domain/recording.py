"""Standalone recording contracts and bounded source-port types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import numpy as np


class RecordingKind(StrEnum):
    IQ = "iq"
    SPECTRUM = "spectrum"


class RecordingState(StrEnum):
    IDLE = "idle"
    RECORDING = "recording"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    STOP_TIMEOUT = "stop_timeout"


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


@dataclass(frozen=True, slots=True)
class IQBlock:
    sequence: int
    timestamp_ns: int
    samples: np.ndarray
    sample_rate_hz: float
    source_id: str = "live"
    config_generation: int = 0

    def __post_init__(self) -> None:
        data = np.asarray(self.samples)
        if data.ndim != 1 or data.size == 0:
            raise ValueError("IQ block must be a non-empty one-dimensional array")
        if not np.iscomplexobj(data):
            raise ValueError("IQ block must contain complex samples")
        object.__setattr__(self, "samples", data)
        if self.sequence < 0 or self.timestamp_ns < 0 or self.sample_rate_hz <= 0:
            raise ValueError("invalid IQ block metadata")


@dataclass(frozen=True, slots=True)
class SpectrumFrame:
    sequence: int
    timestamp_ns: int
    frequencies_hz: np.ndarray
    values: np.ndarray
    unit: str = "dBFS/bin"
    source_id: str = "live"
    config_generation: int = 0
    calibration_profile_id: str | None = None

    def __post_init__(self) -> None:
        frequencies = np.asarray(self.frequencies_hz, dtype=np.float64).reshape(-1)
        values = np.asarray(self.values, dtype=np.float64).reshape(-1)
        if frequencies.size == 0 or values.size != frequencies.size or not self.unit.strip():
            raise ValueError("spectrum frame requires equal non-empty arrays and a unit")
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "values", values)


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


class RecordingSourcePort(Protocol):
    """Live publication point; recording subscribes without blocking producer."""

    def add_recording_sink(self, sink: Any) -> None: ...
    def remove_recording_sink(self, sink: Any) -> None: ...


__all__ = [
    "IQBlock",
    "RecordingHealth",
    "RecordingKind",
    "RecordingOptions",
    "RecordingResult",
    "RecordingSourcePort",
    "RecordingState",
    "SpectrumFrame",
]
