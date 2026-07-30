from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np


class AcquisitionMode(StrEnum):
    REAL_TIME = "real_time"
    SWEPT = "swept"
    UNKNOWN = "unknown"


class TraceMode(StrEnum):
    CLEAR_WRITE = "clear_write"
    AVERAGE = "average"
    MAX_HOLD = "max_hold"
    MIN_HOLD = "min_hold"
    UNKNOWN = "unknown"


class MeasurementQuality(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    LIMITED = "limited"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class MeasurementWarning:
    code: str
    message: str
    context: dict[str, float | int | str | bool] = field(default_factory=dict)


@dataclass(slots=True)
class InstrumentInfo:
    device_type: str = "Unknown"
    firmware_version: str = "Unknown"
    system: str = "Unknown"
    channel_names: list[str] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TraceData:
    key: str
    title: str
    mode: str
    measurement: str
    measurement_type: str
    source_stream: str
    trace_index: int
    x: np.ndarray
    y: np.ndarray
    x_unit: str = ""
    y_unit: str = ""
    state: str = ""
    detector: str = ""
    update_mode: str = ""
    display_mode: str = ""
    active: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_data(self) -> bool:
        return bool(self.x.size and self.y.size and np.isfinite(self.y).any())


@dataclass(slots=True)
class SpectrogramInfo:
    key: str
    title: str
    mode: str
    measurement: str
    measurement_type: str
    source_stream: str
    line_count: int
    point_count: int
    start_hz: float
    stop_hz: float
    newest_timestamp: float | None = None
    oldest_timestamp: float | None = None
    y_unit: str = "dBm"
    history_depth: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def frequencies_hz(self) -> np.ndarray:
        if self.point_count <= 0:
            return np.empty(0, dtype=np.float64)
        return np.linspace(self.start_hz, self.stop_hz, self.point_count, dtype=np.float64)


@dataclass(slots=True)
class SpectrogramPreview:
    info: SpectrogramInfo
    line_indices: np.ndarray
    timestamps: np.ndarray
    values: np.ndarray

    @property
    def elapsed_seconds(self) -> np.ndarray:
        if not self.timestamps.size:
            return np.empty(0, dtype=np.float64)
        return self.timestamps - float(np.nanmin(self.timestamps))


@dataclass(slots=True)
class SettingValue:
    """One instrument setting read from a concrete XML group path."""

    group_path: str
    name: str
    unit_id: str | None
    raw_value: str | float | int | None
    si_value: float | None
    auto_mode: bool | None = None


@dataclass(slots=True)
class FramePeriodStatistics:
    """Statistics of positive timestamp deltas for a waterfall."""

    count: int = 0
    min_s: float | None = None
    median_s: float | None = None
    mean_s: float | None = None
    p95_s: float | None = None
    p99_s: float | None = None
    max_s: float | None = None


@dataclass(slots=True)
class AcquisitionTiming:
    """Mode-specific timing/bandwidth metadata for a DFL mode."""

    mode: str = ""
    measurement: str = ""
    point_count: int | None = None
    rbw_hz: float | None = None
    vbw_hz: float | None = None
    instrument_sweep_time_s: float | None = None
    recorded_period_statistics: FramePeriodStatistics | None = None
    deadline_source: str = "unknown"
    quality: MeasurementQuality = MeasurementQuality.APPROXIMATE
    warnings: list[MeasurementWarning] = field(default_factory=list)
    raw_settings: dict[str, SettingValue] = field(default_factory=dict)

    @property
    def t_recorded_s(self) -> float | None:
        """Offline playback period from recorded timestamps, if available."""
        if self.recorded_period_statistics is None:
            return None
        return self.recorded_period_statistics.median_s

    @property
    def t_deadline_s(self) -> float | None:
        """Strict processing deadline: min(instrument sweep, recorded period)."""
        candidates: list[float] = []
        if self.instrument_sweep_time_s is not None and self.instrument_sweep_time_s > 0:
            candidates.append(self.instrument_sweep_time_s)
        recorded = self.t_recorded_s
        if recorded is not None and recorded > 0:
            candidates.append(recorded)
        if not candidates:
            return None
        return min(candidates)

    @property
    def t_target_s(self) -> float | None:
        """Engineering target: keep processing under 80 % of the deadline."""
        deadline = self.t_deadline_s
        if deadline is None or deadline <= 0:
            return None
        return deadline * 0.8

    @property
    def required_point_rate(self) -> float | None:
        deadline = self.t_deadline_s
        if deadline is None or deadline <= 0 or self.point_count is None:
            return None
        return self.point_count / deadline


@dataclass(slots=True)
class DflDocument:
    path: Path
    instrument: InstrumentInfo
    traces: list[TraceData] = field(default_factory=list)
    spectrograms: list[SpectrogramInfo] = field(default_factory=list)
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)
    streams: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    acquisition_timing: dict[str, AcquisitionTiming] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "file": str(self.path),
            "file_size": self.path.stat().st_size,
            "instrument": {
                "device_type": self.instrument.device_type,
                "firmware_version": self.instrument.firmware_version,
                "system": self.instrument.system,
                "channels": self.instrument.channel_names,
                "modes": self.instrument.modes,
            },
            "trace_count": len(self.traces),
            "traces": [
                {
                    "title": trace.title,
                    "mode": trace.mode,
                    "points": int(min(trace.x.size, trace.y.size)),
                    "x_unit": trace.x_unit,
                    "y_unit": trace.y_unit,
                    "state": trace.state,
                    "detector": trace.detector,
                    "update_mode": trace.update_mode,
                    "active": trace.active,
                    "source_stream": trace.source_stream,
                }
                for trace in self.traces
            ],
            "spectrogram_count": len(self.spectrograms),
            "spectrograms": [
                {
                    "title": item.title,
                    "mode": item.mode,
                    "lines": item.line_count,
                    "points": item.point_count,
                    "start_hz": item.start_hz,
                    "stop_hz": item.stop_hz,
                    "oldest_timestamp": item.oldest_timestamp,
                    "newest_timestamp": item.newest_timestamp,
                    "source_stream": item.source_stream,
                }
                for item in self.spectrograms
            ],
            "warnings": self.warnings,
        }
