from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping
from uuid import uuid4

import numpy as np

from .models import AcquisitionTiming

if TYPE_CHECKING:
    from .sdr.contracts import SourceType


class MarkerType(StrEnum):
    MANUAL = "Manual"
    DELTA = "Delta"
    PEAK = "Peak"
    MINIMUM = "Minimum"
    BAND_CENTER = "Band Center"
    NOISE = "Noise"
    HARMONIC = "Harmonic"
    REFERENCE = "Reference"


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """Backward-compatible source identity for file, live and replay sessions."""

    source_type: SourceType
    source_id: str
    display_name: str
    uri: str | None = None
    device_serial: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    backend_id: str | None = None
    # Keep in sync with sdr.contracts.CONTRACT_SCHEMA_VERSION (imported lazily
    # in __post_init__ to avoid a circular import).
    schema_version: int = 3

    def __post_init__(self) -> None:
        from .sdr.contracts import (
            CONTRACT_SCHEMA_VERSION,
            ContractValidationError,
            SourceType,
            freeze_metadata,
        )

        if not isinstance(self.source_type, SourceType):
            raise ContractValidationError("source_type must be SourceType")
        if not self.source_id.strip():
            raise ContractValidationError("source_id must not be empty")
        if not self.display_name.strip():
            raise ContractValidationError("display_name must not be empty")
        if self.schema_version != CONTRACT_SCHEMA_VERSION:
            raise ContractValidationError(
                f"unsupported schema_version {self.schema_version}; expected {CONTRACT_SCHEMA_VERSION}"
            )
        object.__setattr__(self, "metadata", freeze_metadata(self.metadata))


@dataclass(slots=True)
class MeasurementMetadata:
    device_type: str = "Unknown"
    firmware_version: str = "Unknown"
    system: str = "Unknown"
    channel_names: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)
    streams: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SpectrumTrace:
    trace_id: str
    name: str
    start_frequency_hz: float
    stop_frequency_hz: float
    frequency_step_hz: float
    power_values: np.ndarray
    frequency_values: np.ndarray | None = None
    axis_values: np.ndarray | None = None
    axis_unit: str = "Hz"
    unit: str = "dBm"
    timestamp: float | None = None
    rbw_hz: float | None = None
    vbw_hz: float | None = None
    detector: str = ""
    trace_mode: str = "Clear/Write"
    reference_level_dbm: float | None = None
    attenuation_db: float | None = None
    preamplifier_enabled: bool | None = None
    source_stream: str = ""
    enabled: bool = True
    color: str = "#35c6ff"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.power_values = np.asarray(self.power_values, dtype=np.float32)
        if self.frequency_values is not None:
            self.frequency_values = np.asarray(self.frequency_values, dtype=np.float64)
        if self.axis_values is not None:
            self.axis_values = np.asarray(self.axis_values, dtype=np.float64)

    @property
    def point_count(self) -> int:
        return int(self.power_values.size)

    @property
    def is_frequency_trace(self) -> bool:
        return self.axis_unit == "Hz" and self.point_count > 0

    @property
    def frequencies_hz(self) -> np.ndarray:
        if not self.is_frequency_trace:
            return np.empty(0, dtype=np.float64)
        if self.frequency_values is not None:
            return self.frequency_values[: self.point_count]
        return self.start_frequency_hz + self.frequency_step_hz * np.arange(
            self.point_count, dtype=np.float64
        )

    @property
    def x_values(self) -> np.ndarray:
        return self.frequencies_hz if self.is_frequency_trace else (
            self.axis_values if self.axis_values is not None else np.arange(self.point_count)
        )


@dataclass(slots=True)
class WaterfallData:
    waterfall_id: str
    name: str
    line_count: int
    point_count: int
    start_frequency_hz: float
    stop_frequency_hz: float
    frequency_step_hz: float
    source_stream: str
    values: np.ndarray | None = None
    timestamps: np.ndarray | None = None
    line_indices: np.ndarray | None = None
    min_level: float | None = None
    max_level: float | None = None
    unit: str = "dBm"
    colormap: str = "Turbo"
    time_direction: str = "newest_at_top"
    missing_rows: np.ndarray | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def set_preview(
        self,
        values: np.ndarray,
        timestamps: np.ndarray,
        line_indices: np.ndarray,
    ) -> None:
        self.values = np.asarray(values, dtype=np.float32)
        self.timestamps = np.asarray(timestamps, dtype=np.float64)
        self.line_indices = np.asarray(line_indices, dtype=np.int64)
        finite = self.values[np.isfinite(self.values)]
        if finite.size:
            self.min_level = float(np.percentile(finite, 2.0))
            self.max_level = float(np.percentile(finite, 99.5))
        if self.line_indices.size > 1:
            expected = np.arange(self.line_indices[0], self.line_indices[-1] + 1)
            self.missing_rows = ~np.isin(expected, self.line_indices)

    @property
    def frequencies_hz(self) -> np.ndarray:
        if self.point_count <= 0:
            return np.empty(0, dtype=np.float64)
        return self.start_frequency_hz + self.frequency_step_hz * np.arange(
            self.point_count, dtype=np.float64
        )


@dataclass(slots=True)
class Marker:
    marker_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "M1"
    frequency_hz: float = 0.0
    power: float = float("nan")
    timestamp: float | None = None
    marker_type: MarkerType = MarkerType.MANUAL
    trace_id: str | None = None
    reference_marker_id: str | None = None
    enabled: bool = True
    color: str = "#ffd24a"
    locked: bool = False

    def __post_init__(self) -> None:
        # Peak markers are program-controlled and should not drag by default;
        # manual markers are meant to be moved by the user.
        if self.marker_type == MarkerType.PEAK:
            self.locked = True
        elif self.marker_type == MarkerType.MANUAL:
            self.locked = False


@dataclass(slots=True)
class FrequencyRegion:
    region_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Полоса 1"
    start_frequency_hz: float = 0.0
    stop_frequency_hz: float = 0.0
    region_type: str = "Channel Power"
    enabled: bool = True
    color: str = "#3ddc97"

    @property
    def center_frequency_hz(self) -> float:
        return (self.start_frequency_hz + self.stop_frequency_hz) / 2.0

    @property
    def bandwidth_hz(self) -> float:
        return abs(self.stop_frequency_hz - self.start_frequency_hz)


@dataclass(slots=True)
class TimeRegion:
    region_id: str = field(default_factory=lambda: str(uuid4()))
    name: str = "Интервал 1"
    start_time: float = 0.0
    stop_time: float = 0.0
    enabled: bool = True
    color: str = "#ff8c42"


@dataclass(slots=True)
class Viewport:
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None


@dataclass(slots=True)
class AnalysisResult:
    kind: str
    name: str
    values: dict[str, float | str | bool]
    trace_id: str | None = None
    region_id: str | None = None
    approximate: bool = False
    created_at: datetime = field(default_factory=datetime.now)
    enabled: bool = True
    result_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(slots=True)
class MeasurementSession:
    session_id: str
    source_path: Path
    name: str
    metadata: MeasurementMetadata
    traces: dict[str, SpectrumTrace] = field(default_factory=dict)
    waterfalls: dict[str, WaterfallData] = field(default_factory=dict)
    markers: list[Marker] = field(default_factory=list)
    frequency_regions: list[FrequencyRegion] = field(default_factory=list)
    time_regions: list[TimeRegion] = field(default_factory=list)
    analysis_results: list[AnalysisResult] = field(default_factory=list)
    comments: str = ""
    active_trace_id: str | None = None
    active_waterfall_id: str | None = None
    current_frame: int = 0
    display_state: dict[str, Any] = field(default_factory=dict)
    acquisition_timing: dict[str, AcquisitionTiming] = field(default_factory=dict)
    visible: bool = True
    source_descriptor: SourceDescriptor | None = None

    @property
    def start_time(self) -> float | None:
        times = [
            float(w.timestamps[0])
            for w in self.waterfalls.values()
            if w.timestamps is not None and w.timestamps.size
        ]
        return min(times) if times else None

    @property
    def end_time(self) -> float | None:
        times = [
            float(w.timestamps[-1])
            for w in self.waterfalls.values()
            if w.timestamps is not None and w.timestamps.size
        ]
        return max(times) if times else None
