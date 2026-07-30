"""Bounded IQ/spectrum recording and replay for the P14 package.

The file formats are intentionally open: IQ uses SigMF-compatible raw data and
metadata plus a line-oriented index/gap sidecar; spectrum uses a versioned
JSONL stream with little-endian base64 numeric arrays.  Writers never retain the
recording in memory and finalize each artifact through a ``.part`` file.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import json
import math
import os
from pathlib import Path
import queue
import shutil
import threading
from typing import Any, TypeVar

import numpy as np

from ..domain import SourceDescriptor
from .contracts import (
    CalibrationStatus,
    DetectorType,
    IqBlock,
    PrecisionMode,
    QualityFlag,
    SampleFormat,
    SourceType,
    SpectrumFrame,
    SpectrumUnit,
    WindowType,
    config_to_native,
)


RECORDING_SCHEMA_NAME = "sdr-native-recording"
RECORDING_SCHEMA_VERSION = 1
SPECTRUM_SCHEMA_NAME = "sdr-native-spectrum"
SPECTRUM_SCHEMA_VERSION = 1

_SAMPLE_WIDTH = {
    SampleFormat.COMPLEX_INT8_INTERLEAVED: 2,
    SampleFormat.COMPLEX_INT12_IN_INT16_LE: 4,
    SampleFormat.COMPLEX_INT16_LE: 4,
    SampleFormat.COMPLEX_FLOAT32_LE: 8,
}
_SIGMF_DATATYPE = {
    SampleFormat.COMPLEX_INT8_INTERLEAVED: "ci8_le",
    SampleFormat.COMPLEX_INT12_IN_INT16_LE: "ci12_le",
    SampleFormat.COMPLEX_INT16_LE: "ci16_le",
    SampleFormat.COMPLEX_FLOAT32_LE: "cf32_le",
}


class RecordingError(RuntimeError):
    """Base error for recording and replay failures."""


class RecordingCorruptionError(RecordingError):
    """Raised when a finalized or recoverable stream is malformed."""


class RecordingOverflowError(RecordingError):
    """Raised when the configured recorder policy stops on queue overflow."""


class InsufficientStorageError(RecordingError):
    """Raised when a preflight reserve cannot be satisfied."""


class RecordingQueuePolicy(StrEnum):
    BLOCK = "block"
    DROP_NEWEST = "drop_newest"
    DROP_OLDEST = "drop_oldest"


@dataclass(frozen=True, slots=True)
class RecordingOptions:
    output_uri: str | Path
    record_iq: bool = False
    record_spectrum: bool = False
    queue_capacity: int = 8
    overflow_policy: RecordingQueuePolicy = RecordingQueuePolicy.DROP_NEWEST
    stop_on_overflow: bool = False
    chunk_samples: int = 1_048_576
    free_space_reserve_bytes: int = 0

    def __post_init__(self) -> None:
        if not str(self.output_uri).strip():
            raise ValueError("output_uri must not be empty")
        if not self.record_iq and not self.record_spectrum:
            raise ValueError("at least one recording stream must be enabled")
        if isinstance(self.queue_capacity, bool) or self.queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if not isinstance(self.overflow_policy, RecordingQueuePolicy):
            raise TypeError("overflow_policy must be RecordingQueuePolicy")
        if not isinstance(self.stop_on_overflow, bool):
            raise TypeError("stop_on_overflow must be bool")
        if isinstance(self.chunk_samples, bool) or self.chunk_samples <= 0:
            raise ValueError("chunk_samples must be positive")
        if isinstance(self.free_space_reserve_bytes, bool) or self.free_space_reserve_bytes < 0:
            raise ValueError("free_space_reserve_bytes must be non-negative")


@dataclass(frozen=True, slots=True)
class StorageForecast:
    iq_bytes_per_second: int
    spectrum_bytes_per_frame: int
    estimated_bytes: int
    free_bytes: int | None
    reserve_bytes: int
    sufficient: bool | None


@dataclass(frozen=True, slots=True)
class RecordingStats:
    enqueued_items: int = 0
    written_iq_blocks: int = 0
    written_iq_samples: int = 0
    written_spectrum_frames: int = 0
    dropped_items: int = 0
    dropped_iq_blocks: int = 0
    dropped_iq_samples: int = 0
    dropped_spectrum_frames: int = 0
    gap_count: int = 0
    gap_samples: int = 0
    abandoned_items: int = 0
    queue_depth: int = 0
    queue_high_water: int = 0
    stopped_on_overflow: bool = False
    cancelled: bool = False
    finalized: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RecordingRecoveryResult:
    base_path: Path
    truncated_bytes: int
    retained_iq_blocks: int
    finalized: bool


@dataclass(frozen=True, slots=True)
class _RecordingGap:
    stream: str
    reason: str
    first_sample_index: int | None = None
    sample_count: int = 0
    frame_sequence: int | None = None
    timestamp_ns: int | None = None
    flags: int = 0


T = TypeVar("T")


def _base_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    for suffix in (".sigmf-data", ".sigmf-meta", ".spectrum.jsonl", ".spectrum.json"):
        if path.name.endswith(suffix):
            return path.with_name(path.name[: -len(suffix)])
    return path


def _part(path: Path) -> Path:
    return Path(str(path) + ".part")


def _json_safe(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    raise TypeError(f"unsupported metadata value: {type(value).__name__}")


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    temporary = Path(str(path) + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(_json_safe(payload), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _flush_fsync(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _source_payload(source: SourceDescriptor) -> dict[str, object]:
    return {
        "source_type": source.source_type.value,
        "source_id": source.source_id,
        "display_name": source.display_name,
        "uri": source.uri,
        "device_serial": source.device_serial,
        "backend_id": source.backend_id,
        "schema_version": source.schema_version,
        "metadata": dict(source.metadata),
    }


def _source_from_payload(payload: Mapping[str, object]) -> SourceDescriptor:
    return SourceDescriptor(
        source_type=SourceType(str(payload["source_type"])),
        source_id=str(payload["source_id"]),
        display_name=str(payload["display_name"]),
        uri=None if payload.get("uri") is None else str(payload["uri"]),
        device_serial=None if payload.get("device_serial") is None else str(payload["device_serial"]),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {},
        backend_id=None if payload.get("backend_id") is None else str(payload["backend_id"]),
        schema_version=int(payload.get("schema_version", 5)),
    )


def _iso_timestamp(timestamp_ns: int) -> str:
    return datetime.fromtimestamp(timestamp_ns / 1.0e9, timezone.utc).isoformat()


def estimate_storage(
    *,
    sample_rate_hz: float,
    duration_seconds: float,
    sample_format: SampleFormat = SampleFormat.COMPLEX_FLOAT32_LE,
    spectrum_frames_per_second: float = 0.0,
    spectrum_bins: int = 0,
    record_iq: bool = True,
    record_spectrum: bool = False,
    output_uri: str | Path | None = None,
    reserve_bytes: int = 0,
) -> StorageForecast:
    """Estimate bytes without allocating or opening a recording."""

    if not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0.0:
        raise ValueError("sample_rate_hz must be finite and positive")
    if not math.isfinite(duration_seconds) or duration_seconds < 0.0:
        raise ValueError("duration_seconds must be finite and non-negative")
    if not math.isfinite(spectrum_frames_per_second) or spectrum_frames_per_second < 0.0:
        raise ValueError("spectrum_frames_per_second must be finite and non-negative")
    if isinstance(spectrum_bins, bool) or spectrum_bins < 0:
        raise ValueError("spectrum_bins must be non-negative")
    if reserve_bytes < 0:
        raise ValueError("reserve_bytes must be non-negative")
    iq_rate = int(math.ceil(sample_rate_hz * _SAMPLE_WIDTH[sample_format])) if record_iq else 0
    # Values and frequencies are base64 encoded in JSONL; include a conservative
    # 4/3 encoding factor and fixed metadata/drop-line overhead.
    spectrum_frame_bytes = (
        int(math.ceil((spectrum_bins * 4 + spectrum_bins * 8) * 4.0 / 3.0 + 512.0)) if record_spectrum else 0
    )
    estimated = int(math.ceil(duration_seconds * (iq_rate + spectrum_frames_per_second * spectrum_frame_bytes)))
    free: int | None = None
    if output_uri is not None:
        try:
            free = int(shutil.disk_usage(_base_path(output_uri).parent or Path.cwd()).free)
        except OSError:
            free = None
    sufficient = None if free is None else free >= estimated + reserve_bytes
    return StorageForecast(iq_rate, spectrum_frame_bytes, estimated, free, reserve_bytes, sufficient)


def preflight_storage(forecast: StorageForecast) -> None:
    if forecast.sufficient is False:
        raise InsufficientStorageError(
            f"insufficient free space: need {forecast.estimated_bytes + forecast.reserve_bytes} bytes, "
            f"have {forecast.free_bytes}"
        )


class IqRecordingWriter:
    """Streaming SigMF-compatible writer with an index and gap sidecars."""

    def __init__(self, output_uri: str | Path, *, source_metadata: Mapping[str, object] | None = None) -> None:
        self.base_path = _base_path(output_uri)
        self.data_path = self.base_path.with_name(self.base_path.name + ".sigmf-data")
        self.meta_path = self.base_path.with_name(self.base_path.name + ".sigmf-meta")
        self.index_path = self.base_path.with_name(self.base_path.name + ".sigmf-index")
        self.gaps_path = self.base_path.with_name(self.base_path.name + ".sigmf-gaps")
        self.data_part = _part(self.data_path)
        self.meta_part = _part(self.meta_path)
        self.index_part = _part(self.index_path)
        self.gaps_part = _part(self.gaps_path)
        self.source_metadata = dict(source_metadata or {})
        self._data: Any | None = None
        self._index: Any | None = None
        self._gaps: Any | None = None
        self._lock = threading.RLock()
        self._started = False
        self._closed = False
        self._completed = False
        self._abort_reason: str | None = None
        self._sample_format: SampleFormat | None = None
        self._sample_rate_hz: float | None = None
        self._center_frequency_hz: float | None = None
        self._expected_sample_index: int | None = None
        self._data_offset = 0
        self._block_count = 0
        self._sample_count = 0
        self._gap_count = 0
        self._gap_samples = 0
        self._captures: list[dict[str, object]] = []
        self._first_timestamp_ns: int | None = None
        self._last_timestamp_ns: int | None = None

    @property
    def started(self) -> bool:
        return self._started and not self._closed

    @property
    def written_blocks(self) -> int:
        return self._block_count

    @property
    def written_samples(self) -> int:
        return self._sample_count

    @property
    def gap_count(self) -> int:
        return self._gap_count

    @property
    def gap_samples(self) -> int:
        return self._gap_samples

    def start(self) -> "IqRecordingWriter":
        with self._lock:
            if self._started:
                raise RecordingError("IQ writer is already started")
            for path in (
                self.data_path,
                self.meta_path,
                self.index_path,
                self.gaps_path,
                self.data_part,
                self.meta_part,
                self.index_part,
                self.gaps_part,
            ):
                if path.exists():
                    raise RecordingError(f"recording target already exists: {path}")
            self.base_path.parent.mkdir(parents=True, exist_ok=True)
            self._data = self.data_part.open("wb")
            self._index = self.index_part.open("w", encoding="utf-8", newline="\n")
            self._gaps = self.gaps_part.open("w", encoding="utf-8", newline="\n")
            self._started = True
            self._write_metadata()
            return self

    def _require_started(self) -> None:
        if not self._started or self._closed or self._data is None or self._index is None or self._gaps is None:
            raise RecordingError("IQ writer is not active")

    def _add_capture(self, block: IqBlock) -> None:
        changed = (
            self._sample_format is None
            or self._sample_rate_hz != float(block.sample_rate_hz)
            or self._center_frequency_hz != float(block.center_frequency_hz)
        )
        if not changed:
            return
        if self._sample_format is not None and block.sample_format is not self._sample_format:
            raise RecordingError("IQ sample format cannot change within one SigMF data file")
        self._sample_format = block.sample_format
        self._sample_rate_hz = float(block.sample_rate_hz)
        self._center_frequency_hz = float(block.center_frequency_hz)
        self._captures.append(
            {
                "core:sample_start": int(block.first_sample_index),
                "core:frequency": self._center_frequency_hz,
                "sdr:sample_rate_hz": self._sample_rate_hz,
                "sdr:timestamp_ns": int(block.timestamp_ns),
                "sdr:config_generation": int(block.config_generation),
            }
        )

    def record_gap(self, gap: _RecordingGap) -> None:
        with self._lock:
            self._require_started()
            payload = {
                "schema": RECORDING_SCHEMA_NAME,
                "schema_version": RECORDING_SCHEMA_VERSION,
                "stream": gap.stream,
                "reason": gap.reason,
                "first_sample_index": gap.first_sample_index,
                "sample_count": gap.sample_count,
                "frame_sequence": gap.frame_sequence,
                "timestamp_ns": gap.timestamp_ns,
                "flags": gap.flags,
            }
            self._gaps.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._gap_count += 1
            self._gap_samples += max(0, int(gap.sample_count))

    def write_block(self, block: IqBlock) -> None:
        if not isinstance(block, IqBlock):
            raise TypeError("block must be IqBlock")
        with self._lock:
            self._require_started()
            self._add_capture(block)
            if self._expected_sample_index is not None:
                if block.first_sample_index < self._expected_sample_index:
                    raise RecordingError("IQ sample indexes overlap or move backwards")
                if block.first_sample_index > self._expected_sample_index:
                    self.record_gap(
                        _RecordingGap(
                            stream="iq",
                            reason="sample_index_gap",
                            first_sample_index=int(self._expected_sample_index),
                            sample_count=int(block.first_sample_index - self._expected_sample_index),
                            timestamp_ns=int(block.timestamp_ns),
                        )
                    )
            raw = np.asarray(block.samples, dtype=np.uint8)
            expected_bytes = int(block.sample_count) * _SAMPLE_WIDTH[block.sample_format]
            if raw.ndim != 1 or raw.size != expected_bytes:
                raise RecordingError("IqBlock samples do not match sample format/count")
            offset = self._data_offset
            payload = raw.tobytes(order="C")
            assert self._data is not None and self._index is not None
            self._data.write(payload)
            self._index.write(
                json.dumps(
                    {
                        "offset": offset,
                        "byte_count": len(payload),
                        "sample_count": int(block.sample_count),
                        "first_sample_index": int(block.first_sample_index),
                        "timestamp_ns": int(block.timestamp_ns),
                        "source_sequence": int(block.source_sequence),
                        "config_generation": int(block.config_generation),
                        "sample_format": block.sample_format.value,
                        "flags": int(block.flags),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
            self._data_offset += len(payload)
            self._block_count += 1
            self._sample_count += int(block.sample_count)
            self._expected_sample_index = int(block.first_sample_index) + int(block.sample_count)
            self._first_timestamp_ns = (
                int(block.timestamp_ns) if self._first_timestamp_ns is None else self._first_timestamp_ns
            )
            self._last_timestamp_ns = int(block.timestamp_ns)
            if block.flags & QualityFlag.IQ_DROPPED:
                self.record_gap(
                    _RecordingGap(
                        stream="iq",
                        reason="source_quality_flag_iq_dropped",
                        first_sample_index=int(block.first_sample_index),
                        sample_count=0,
                        timestamp_ns=int(block.timestamp_ns),
                        flags=int(block.flags),
                    )
                )
            if self._block_count == 1 or self._block_count % 16 == 0:
                _flush_fsync(self._data)
                _flush_fsync(self._index)
                _flush_fsync(self._gaps)
                self._write_metadata()

    def _metadata(self, *, completed: bool) -> dict[str, object]:
        datatype = None if self._sample_format is None else _SIGMF_DATATYPE[self._sample_format]
        return {
            "schema": RECORDING_SCHEMA_NAME,
            "schema_version": RECORDING_SCHEMA_VERSION,
            "recording_type": "iq",
            "sigmf": {
                "global": {
                    "core:datatype": datatype,
                    "core:sample_rate": self._sample_rate_hz,
                    "core:frequency": self._center_frequency_hz,
                    "core:sample_count": self._sample_count,
                    "core:version": "1.0.0",
                },
                "captures": self._captures,
                "annotations": [],
            },
            "sdr": {
                "source": self.source_metadata,
                "sample_format": None if self._sample_format is None else self._sample_format.value,
                "data_file": self.data_path.name,
                "index_file": self.index_path.name,
                "gaps_file": self.gaps_path.name,
                "completed": completed,
                "abort_reason": self._abort_reason,
                "block_count": self._block_count,
                "sample_count": self._sample_count,
                "gap_count": self._gap_count,
                "gap_samples": self._gap_samples,
                "first_timestamp_ns": self._first_timestamp_ns,
                "last_timestamp_ns": self._last_timestamp_ns,
            },
        }

    def _write_metadata(self, *, completed: bool | None = None) -> None:
        value = self._completed if completed is None else completed
        _write_json_atomic(self.meta_part, self._metadata(completed=value))

    def finalize(self) -> Path:
        with self._lock:
            self._require_started()
            self._completed = True
            assert self._data is not None and self._index is not None and self._gaps is not None
            _flush_fsync(self._data)
            _flush_fsync(self._index)
            _flush_fsync(self._gaps)
            self._write_metadata(completed=True)
            self._data.close()
            self._index.close()
            self._gaps.close()
            self._data = self._index = self._gaps = None
            os.replace(self.data_part, self.data_path)
            os.replace(self.index_part, self.index_path)
            os.replace(self.gaps_part, self.gaps_path)
            os.replace(self.meta_part, self.meta_path)
            self._closed = True
            return self.meta_path

    def abort(self, reason: str = "cancelled") -> Path:
        with self._lock:
            self._require_started()
            self._abort_reason = reason
            assert self._data is not None and self._index is not None and self._gaps is not None
            _flush_fsync(self._data)
            _flush_fsync(self._index)
            _flush_fsync(self._gaps)
            self._write_metadata(completed=False)
            self._data.close()
            self._index.close()
            self._gaps.close()
            self._data = self._index = self._gaps = None
            self._closed = True
            return self.meta_part

    def close(self) -> None:
        if self.started:
            self.abort("closed_without_finalize")


class SpectrumRecordingWriter:
    """Streaming versioned JSONL writer for immutable SpectrumFrame values."""

    def __init__(self, output_uri: str | Path, *, source_metadata: Mapping[str, object] | None = None) -> None:
        self.base_path = _base_path(output_uri)
        self.data_path = self.base_path.with_name(self.base_path.name + ".spectrum.jsonl")
        self.meta_path = self.base_path.with_name(self.base_path.name + ".spectrum.json")
        self.data_part = _part(self.data_path)
        self.meta_part = _part(self.meta_path)
        self.source_metadata = dict(source_metadata or {})
        self._data: Any | None = None
        self._lock = threading.RLock()
        self._started = False
        self._closed = False
        self._completed = False
        self._abort_reason: str | None = None
        self._frame_count = 0
        self._dropped_frames = 0
        self._gap_count = 0
        self._first_timestamp_ns: int | None = None
        self._last_timestamp_ns: int | None = None

    @property
    def started(self) -> bool:
        return self._started and not self._closed

    @property
    def written_frames(self) -> int:
        return self._frame_count

    @property
    def dropped_frames(self) -> int:
        return self._dropped_frames

    @property
    def gap_count(self) -> int:
        return self._gap_count

    def start(self) -> "SpectrumRecordingWriter":
        with self._lock:
            if self._started:
                raise RecordingError("spectrum writer is already started")
            for path in (self.data_path, self.meta_path, self.data_part, self.meta_part):
                if path.exists():
                    raise RecordingError(f"recording target already exists: {path}")
            self.base_path.parent.mkdir(parents=True, exist_ok=True)
            self._data = self.data_part.open("w", encoding="utf-8", newline="\n")
            self._data.write(
                json.dumps(
                    {
                        "schema": SPECTRUM_SCHEMA_NAME,
                        "schema_version": SPECTRUM_SCHEMA_VERSION,
                        "record_type": "header",
                        "encoding": "base64",
                        "frequency_dtype": "<f8",
                        "value_dtype": "<f4",
                        "source": _json_safe(self.source_metadata),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            self._started = True
            self._write_metadata()
            return self

    def _require_started(self) -> None:
        if not self._started or self._closed or self._data is None:
            raise RecordingError("spectrum writer is not active")

    @staticmethod
    def _array_payload(values: np.ndarray, dtype: str) -> str:
        array = np.asarray(values, dtype=np.dtype(dtype), order="C")
        if array.ndim != 1:
            raise RecordingError("spectrum arrays must be one-dimensional")
        return base64.b64encode(array.tobytes(order="C")).decode("ascii")

    @staticmethod
    def _frame_payload(frame: SpectrumFrame) -> dict[str, object]:
        uncertainty = float(frame.estimated_uncertainty_db)
        return {
            "source": _source_payload(frame.source),
            "frame_sequence": int(frame.frame_sequence),
            "first_sample_index": int(frame.first_sample_index),
            "timestamp_ns": int(frame.timestamp_ns),
            "config_generation": int(frame.config_generation),
            "center_frequency_hz": float(frame.center_frequency_hz),
            "sample_rate_hz": float(frame.sample_rate_hz),
            "analog_bandwidth_hz": float(frame.analog_bandwidth_hz),
            "fft_bin_width_hz": float(frame.fft_bin_width_hz),
            "enbw_hz": float(frame.enbw_hz),
            "nominal_rbw_hz": float(frame.nominal_rbw_hz),
            "fft_size": int(frame.fft_size),
            "hop_size": int(frame.hop_size),
            "window": frame.window.value,
            "detector": frame.detector.value,
            "precision_mode": frame.precision_mode.value,
            "unit": frame.unit.value,
            "calibration_status": frame.calibration_status.value,
            "calibration_profile_id": frame.calibration_profile_id,
            "estimated_uncertainty_db": None if math.isnan(uncertainty) else uncertainty,
            "dropped_samples_before": int(frame.dropped_samples_before),
            "dropped_iq_blocks_before": int(frame.dropped_iq_blocks_before),
            "dropped_fft_frames_before": int(frame.dropped_fft_frames_before),
            "quality_flags": int(frame.quality_flags),
            "frequencies_hz": SpectrumRecordingWriter._array_payload(frame.frequencies_hz, "<f8"),
            "values": SpectrumRecordingWriter._array_payload(frame.values, "<f4"),
        }

    def record_gap(self, *, reason: str, frame_sequence: int | None = None, timestamp_ns: int | None = None) -> None:
        with self._lock:
            self._require_started()
            self._data.write(
                json.dumps(
                    {
                        "schema": SPECTRUM_SCHEMA_NAME,
                        "schema_version": SPECTRUM_SCHEMA_VERSION,
                        "record_type": "gap",
                        "reason": reason,
                        "frame_sequence": frame_sequence,
                        "timestamp_ns": timestamp_ns,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            self._dropped_frames += 1
            self._gap_count += 1

    def write_frame(self, frame: SpectrumFrame) -> None:
        if not isinstance(frame, SpectrumFrame):
            raise TypeError("frame must be SpectrumFrame")
        with self._lock:
            self._require_started()
            assert self._data is not None
            self._data.write(
                json.dumps(
                    {
                        "schema": SPECTRUM_SCHEMA_NAME,
                        "schema_version": SPECTRUM_SCHEMA_VERSION,
                        "record_type": "frame",
                        "frame": self._frame_payload(frame),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            self._frame_count += 1
            self._first_timestamp_ns = (
                int(frame.timestamp_ns) if self._first_timestamp_ns is None else self._first_timestamp_ns
            )
            self._last_timestamp_ns = int(frame.timestamp_ns)
            if self._frame_count == 1 or self._frame_count % 16 == 0:
                _flush_fsync(self._data)
                self._write_metadata()

    def _metadata(self, *, completed: bool) -> dict[str, object]:
        return {
            "schema": SPECTRUM_SCHEMA_NAME,
            "schema_version": SPECTRUM_SCHEMA_VERSION,
            "recording_type": "spectrum",
            "data_file": self.data_path.name,
            "source": self.source_metadata,
            "completed": completed,
            "abort_reason": self._abort_reason,
            "frame_count": self._frame_count,
            "dropped_frames": self._dropped_frames,
            "gap_count": self._gap_count,
            "first_timestamp_ns": self._first_timestamp_ns,
            "last_timestamp_ns": self._last_timestamp_ns,
        }

    def _write_metadata(self, *, completed: bool | None = None) -> None:
        value = self._completed if completed is None else completed
        _write_json_atomic(self.meta_part, self._metadata(completed=value))

    def finalize(self) -> Path:
        with self._lock:
            self._require_started()
            self._completed = True
            assert self._data is not None
            _flush_fsync(self._data)
            self._write_metadata(completed=True)
            self._data.close()
            self._data = None
            os.replace(self.data_part, self.data_path)
            os.replace(self.meta_part, self.meta_path)
            self._closed = True
            return self.meta_path

    def abort(self, reason: str = "cancelled") -> Path:
        with self._lock:
            self._require_started()
            self._abort_reason = reason
            assert self._data is not None
            _flush_fsync(self._data)
            self._write_metadata(completed=False)
            self._data.close()
            self._data = None
            self._closed = True
            return self.meta_part

    def close(self) -> None:
        if self.started:
            self.abort("closed_without_finalize")


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecordingCorruptionError(f"invalid recording metadata {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RecordingCorruptionError(f"recording metadata root is not an object: {path}")
    return value


def recover_iq_recording(output_uri: str | Path, *, finalize: bool = False) -> RecordingRecoveryResult:
    """Validate a crashed IQ prefix and optionally finalize the safe prefix."""

    base = _base_path(output_uri)
    data_path = base.with_name(base.name + ".sigmf-data")
    meta_path = base.with_name(base.name + ".sigmf-meta")
    index_path = base.with_name(base.name + ".sigmf-index")
    gaps_path = base.with_name(base.name + ".sigmf-gaps")
    data_part, meta_part = _part(data_path), _part(meta_path)
    index_part, gaps_part = _part(index_path), _part(gaps_path)
    if not data_part.exists() or not index_part.exists() or not meta_part.exists():
        raise RecordingCorruptionError("IQ .part set is incomplete")
    metadata = _read_json(meta_part)
    sdr = metadata.get("sdr") if isinstance(metadata.get("sdr"), Mapping) else {}
    format_value = sdr.get("sample_format")
    if not isinstance(format_value, str):
        raise RecordingCorruptionError("IQ .part metadata has no sample_format")
    try:
        sample_format = SampleFormat(format_value)
    except ValueError as exc:
        raise RecordingCorruptionError(f"unknown IQ sample format in metadata: {format_value}") from exc
    width = _SAMPLE_WIDTH[sample_format]
    original_size = data_part.stat().st_size
    safe_size = original_size - (original_size % width)
    truncated = original_size - safe_size
    if truncated:
        with data_part.open("r+b") as handle:
            handle.truncate(safe_size)
    retained: list[dict[str, object]] = []
    with index_part.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                item = json.loads(line)
                if not isinstance(item, Mapping):
                    raise TypeError("index line is not an object")
                end = int(item["offset"]) + int(item["byte_count"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise RecordingCorruptionError(f"invalid IQ index line {line_number}: {exc}") from exc
            if end <= safe_size:
                retained.append(dict(item))
            else:
                break
    temporary = Path(str(index_part) + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for item in retained:
            handle.write(json.dumps(item, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, index_part)
    metadata.setdefault("sdr", {})["recovered"] = True
    metadata["sdr"]["recovery_truncated_bytes"] = truncated
    metadata["sdr"]["block_count"] = len(retained)
    metadata["sdr"]["sample_count"] = sum(int(item["sample_count"]) for item in retained)
    metadata["sdr"]["completed"] = False
    _write_json_atomic(meta_part, metadata)
    if not finalize:
        return RecordingRecoveryResult(base, truncated, len(retained), False)
    if data_path.exists() or meta_path.exists() or index_path.exists() or gaps_path.exists():
        raise RecordingError("cannot finalize recovery over an existing recording")
    os.replace(data_part, data_path)
    os.replace(index_part, index_path)
    if gaps_part.exists():
        os.replace(gaps_part, gaps_path)
    metadata["sdr"]["completed"] = True
    _write_json_atomic(meta_part, metadata)
    os.replace(meta_part, meta_path)
    return RecordingRecoveryResult(base, truncated, len(retained), True)


def _decode_array(encoded: object, dtype: str, *, expected: int) -> np.ndarray:
    if not isinstance(encoded, str):
        raise RecordingCorruptionError("numeric array is not base64 text")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise RecordingCorruptionError(f"invalid base64 numeric array: {exc}") from exc
    array = np.frombuffer(raw, dtype=np.dtype(dtype))
    if array.size != expected:
        raise RecordingCorruptionError("numeric array length disagrees with frame metadata")
    result = np.array(array, copy=True, order="C")
    result.setflags(write=False)
    return result


class SpectrumReplay:
    """Bounded line-by-line replay of recorded SpectrumFrame values."""

    def __init__(self, output_uri: str | Path, *, allow_partial: bool = False) -> None:
        base = _base_path(output_uri)
        self.meta_path = base.with_name(base.name + ".spectrum.json")
        self.data_path = base.with_name(base.name + ".spectrum.jsonl")
        if allow_partial:
            if not self.meta_path.exists():
                self.meta_path = _part(self.meta_path)
            if not self.data_path.exists():
                self.data_path = _part(self.data_path)
        if not self.meta_path.exists() or not self.data_path.exists():
            raise FileNotFoundError(f"spectrum recording is incomplete: {base}")
        self.metadata = _read_json(self.meta_path)
        if self.metadata.get("schema") != SPECTRUM_SCHEMA_NAME:
            raise RecordingCorruptionError("unknown spectrum recording schema")

    @staticmethod
    def _frame_from_payload(payload: Mapping[str, object]) -> SpectrumFrame:
        source_value = payload.get("source")
        if not isinstance(source_value, Mapping):
            raise RecordingCorruptionError("spectrum frame has no source")
        fft_size = int(payload["fft_size"])
        frequencies = _decode_array(payload["frequencies_hz"], "<f8", expected=fft_size)
        values = _decode_array(payload["values"], "<f4", expected=fft_size)
        uncertainty = payload.get("estimated_uncertainty_db")
        return SpectrumFrame(
            source=_source_from_payload(source_value),
            frame_sequence=int(payload["frame_sequence"]),
            first_sample_index=int(payload["first_sample_index"]),
            timestamp_ns=int(payload["timestamp_ns"]),
            config_generation=int(payload["config_generation"]),
            center_frequency_hz=float(payload["center_frequency_hz"]),
            sample_rate_hz=float(payload["sample_rate_hz"]),
            analog_bandwidth_hz=float(payload["analog_bandwidth_hz"]),
            fft_bin_width_hz=float(payload["fft_bin_width_hz"]),
            enbw_hz=float(payload["enbw_hz"]),
            nominal_rbw_hz=float(payload["nominal_rbw_hz"]),
            fft_size=fft_size,
            hop_size=int(payload["hop_size"]),
            window=WindowType(str(payload["window"])),
            detector=DetectorType(str(payload["detector"])),
            precision_mode=PrecisionMode(str(payload["precision_mode"])),
            unit=SpectrumUnit(str(payload["unit"])),
            frequencies_hz=frequencies,
            values=values,
            calibration_status=CalibrationStatus(str(payload["calibration_status"])),
            calibration_profile_id=None
            if payload.get("calibration_profile_id") is None
            else str(payload["calibration_profile_id"]),
            estimated_uncertainty_db=float("nan") if uncertainty is None else float(uncertainty),
            dropped_samples_before=int(payload["dropped_samples_before"]),
            dropped_iq_blocks_before=int(payload["dropped_iq_blocks_before"]),
            dropped_fft_frames_before=int(payload["dropped_fft_frames_before"]),
            quality_flags=QualityFlag(int(payload["quality_flags"])),
        )

    def iter_frames(self, *, cancel: threading.Event | None = None) -> Iterator[SpectrumFrame]:
        with self.data_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if cancel is not None and cancel.is_set():
                    return
                try:
                    item = json.loads(line)
                    if not isinstance(item, Mapping):
                        raise TypeError("record is not an object")
                except (json.JSONDecodeError, TypeError) as exc:
                    raise RecordingCorruptionError(f"invalid spectrum line {line_number}: {exc}") from exc
                if item.get("record_type") != "frame":
                    continue
                payload = item.get("frame")
                if not isinstance(payload, Mapping):
                    raise RecordingCorruptionError(f"spectrum frame line {line_number} has no frame object")
                yield self._frame_from_payload(payload)

    def __iter__(self) -> Iterator[SpectrumFrame]:
        return self.iter_frames()


class IqReplay:
    """Bounded replay of raw IQ blocks using the streaming index sidecar."""

    def __init__(self, output_uri: str | Path, *, allow_partial: bool = False) -> None:
        base = _base_path(output_uri)
        self.meta_path = base.with_name(base.name + ".sigmf-meta")
        self.data_path = base.with_name(base.name + ".sigmf-data")
        self.index_path = base.with_name(base.name + ".sigmf-index")
        if allow_partial:
            if not self.meta_path.exists():
                self.meta_path = _part(self.meta_path)
            if not self.data_path.exists():
                self.data_path = _part(self.data_path)
            if not self.index_path.exists():
                self.index_path = _part(self.index_path)
        if not self.meta_path.exists() or not self.data_path.exists() or not self.index_path.exists():
            raise FileNotFoundError(f"IQ recording is incomplete: {base}")
        self.metadata = _read_json(self.meta_path)
        if self.metadata.get("schema") != RECORDING_SCHEMA_NAME:
            raise RecordingCorruptionError("unknown IQ recording schema")

    def iter_blocks(self, *, cancel: threading.Event | None = None) -> Iterator[IqBlock]:
        with self.data_path.open("rb") as data, self.index_path.open("r", encoding="utf-8") as index:
            for line_number, line in enumerate(index, 1):
                if cancel is not None and cancel.is_set():
                    return
                try:
                    item = json.loads(line)
                    offset = int(item["offset"])
                    byte_count = int(item["byte_count"])
                    sample_count = int(item["sample_count"])
                    sample_format = SampleFormat(str(item["sample_format"]))
                    raw = self._read_at(data, offset, byte_count)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError, RecordingCorruptionError) as exc:
                    if isinstance(exc, RecordingCorruptionError):
                        raise
                    raise RecordingCorruptionError(f"invalid IQ index line {line_number}: {exc}") from exc
                expected = sample_count * _SAMPLE_WIDTH[sample_format]
                if len(raw) != expected:
                    raise RecordingCorruptionError(f"IQ data length mismatch at index line {line_number}")
                samples = np.frombuffer(raw, dtype=np.uint8)
                yield IqBlock(
                    source_sequence=int(item["source_sequence"]),
                    first_sample_index=int(item["first_sample_index"]),
                    timestamp_ns=int(item["timestamp_ns"]),
                    center_frequency_hz=float(self._capture_value(item, "center_frequency_hz")),
                    sample_rate_hz=float(self._capture_value(item, "sample_rate_hz")),
                    sample_format=sample_format,
                    sample_count=sample_count,
                    flags=QualityFlag(int(item["flags"])),
                    samples=samples,
                    config_generation=int(item["config_generation"]),
                )

    @staticmethod
    def _read_at(handle: Any, offset: int, byte_count: int) -> bytes:
        if offset < 0 or byte_count < 0:
            raise RecordingCorruptionError("IQ index has negative offset/length")
        handle.seek(offset)
        raw = handle.read(byte_count)
        if len(raw) != byte_count:
            raise RecordingCorruptionError("IQ data ends before indexed chunk")
        return raw

    def _capture_value(self, item: Mapping[str, object], name: str) -> float:
        captures = (
            self.metadata.get("sigmf", {}).get("captures", [])
            if isinstance(self.metadata.get("sigmf"), Mapping)
            else []
        )
        if captures:
            selected = captures[0]
            for capture in captures:
                if isinstance(capture, Mapping) and int(capture.get("core:sample_start", 0)) <= int(
                    item["first_sample_index"]
                ):
                    selected = capture
            if name == "center_frequency_hz":
                return float(selected.get("core:frequency", 0.0))
            return float(selected.get("sdr:sample_rate_hz", 0.0))
        if name == "center_frequency_hz":
            return float(self.metadata.get("sigmf", {}).get("global", {}).get("core:frequency", 0.0))
        return float(self.metadata.get("sigmf", {}).get("global", {}).get("core:sample_rate", 0.0))

    def iter_complex_samples(self, *, cancel: threading.Event | None = None) -> Iterator[tuple[IqBlock, np.ndarray]]:
        for block in self.iter_blocks(cancel=cancel):
            values = _iq_bytes_to_complex(block.samples, block.sample_format, block.sample_count)
            yield block, values

    def __iter__(self) -> Iterator[IqBlock]:
        return self.iter_blocks()


def _iq_bytes_to_complex(samples: np.ndarray, sample_format: SampleFormat, sample_count: int) -> np.ndarray:
    raw = np.asarray(samples, dtype=np.uint8)
    if sample_format is SampleFormat.COMPLEX_FLOAT32_LE:
        values = raw.view("<f4").reshape(sample_count, 2)
    elif sample_format is SampleFormat.COMPLEX_INT8_INTERLEAVED:
        values = raw.view("i1").reshape(sample_count, 2).astype(np.float32)
    else:
        values = raw.view("<i2").reshape(sample_count, 2).astype(np.float32)
    result = np.asarray(values[:, 0] + 1j * values[:, 1], dtype=np.complex64, order="C")
    result.setflags(write=False)
    return result


@dataclass(frozen=True, slots=True)
class _QueuedItem:
    stream: str
    value: object


class RecordingService:
    """Bounded asynchronous tee for raw IQ and/or SpectrumFrame streams."""

    def __init__(self, options: RecordingOptions, *, source_metadata: Mapping[str, object] | None = None) -> None:
        self.options = options
        self.source_metadata = dict(source_metadata or {})
        self._queue: queue.Queue[_QueuedItem | None] = queue.Queue(maxsize=options.queue_capacity)
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._cancelled = False
        self._stopped_on_overflow = False
        self._error: BaseException | None = None
        self._enqueued = 0
        self._dropped_items = 0
        self._dropped_iq_blocks = 0
        self._dropped_iq_samples = 0
        self._dropped_spectrum_frames = 0
        self._abandoned = 0
        self._high_water = 0
        self._iq = (
            IqRecordingWriter(options.output_uri, source_metadata=self.source_metadata) if options.record_iq else None
        )
        self._spectrum = (
            SpectrumRecordingWriter(options.output_uri, source_metadata=self.source_metadata)
            if options.record_spectrum
            else None
        )

    @property
    def iq_writer(self) -> IqRecordingWriter | None:
        return self._iq

    @property
    def spectrum_writer(self) -> SpectrumRecordingWriter | None:
        return self._spectrum

    def start(self, *, forecast: StorageForecast | None = None) -> "RecordingService":
        with self._condition:
            if self._running:
                raise RecordingError("recording service is already running")
            if forecast is not None:
                preflight_storage(forecast)
            try:
                if self._iq is not None:
                    self._iq.start()
                if self._spectrum is not None:
                    self._spectrum.start()
            except Exception:
                if self._iq is not None and self._iq.started:
                    self._iq.abort("start_failed")
                if self._spectrum is not None and self._spectrum.started:
                    self._spectrum.abort("start_failed")
                raise
            self._stop.clear()
            self._cancelled = False
            self._error = None
            self._running = True
            self._thread = threading.Thread(target=self._run, name="sdr-recorder", daemon=True)
            self._thread.start()
            return self

    def _require_running(self) -> None:
        if not self._running:
            raise RecordingError("recording service is not running")
        if self._error is not None:
            raise RecordingError(f"recording worker failed: {self._error}")

    def submit_iq(self, block: IqBlock, *, timeout_s: float | None = None) -> bool:
        if not isinstance(block, IqBlock):
            raise TypeError("block must be IqBlock")
        return self._submit(_QueuedItem("iq", block), timeout_s=timeout_s)

    def submit_spectrum(self, frame: SpectrumFrame, *, timeout_s: float | None = None) -> bool:
        if not isinstance(frame, SpectrumFrame):
            raise TypeError("frame must be SpectrumFrame")
        return self._submit(_QueuedItem("spectrum", frame), timeout_s=timeout_s)

    def _submit(self, item: _QueuedItem, *, timeout_s: float | None) -> bool:
        with self._condition:
            self._require_running()
            if self._stop.is_set():
                raise RecordingError("recording service is stopping")
            policy = self.options.overflow_policy
            if policy is RecordingQueuePolicy.BLOCK:
                while True:
                    try:
                        self._queue.put(item, timeout=0.05 if timeout_s is None else timeout_s)
                        break
                    except queue.Full:
                        if timeout_s is not None:
                            self._record_drop(item, "queue_overflow_timeout")
                            if self.options.stop_on_overflow:
                                self._stopped_on_overflow = True
                                self._stop.set()
                                raise RecordingOverflowError("recording queue overflow")
                            return False
                        if self._stop.is_set():
                            self._record_drop(item, "queue_stopping")
                            return False
            else:
                try:
                    self._queue.put_nowait(item)
                except queue.Full:
                    if policy is RecordingQueuePolicy.DROP_OLDEST:
                        try:
                            old = self._queue.get_nowait()
                        except queue.Empty:
                            old = None
                        if old is not None:
                            self._record_drop(old, "queue_overflow_drop_oldest")
                        try:
                            self._queue.put_nowait(item)
                        except queue.Full:
                            self._record_drop(item, "queue_overflow_drop_newest")
                            return False
                    else:
                        self._record_drop(item, "queue_overflow_drop_newest")
                        if self.options.stop_on_overflow:
                            self._stopped_on_overflow = True
                            self._stop.set()
                            raise RecordingOverflowError("recording queue overflow")
                        return False
            self._enqueued += 1
            self._high_water = max(self._high_water, self._queue.qsize())
            return True

    def _record_drop(self, item: _QueuedItem, reason: str) -> None:
        self._dropped_items += 1
        if item.stream == "iq" and isinstance(item.value, IqBlock):
            self._dropped_iq_blocks += 1
            self._dropped_iq_samples += int(item.value.sample_count)
            if self._iq is not None and self._iq.started:
                self._iq.record_gap(
                    _RecordingGap(
                        stream="iq",
                        reason=reason,
                        first_sample_index=int(item.value.first_sample_index),
                        sample_count=int(item.value.sample_count),
                        timestamp_ns=int(item.value.timestamp_ns),
                        flags=int(item.value.flags) | int(QualityFlag.IQ_DROPPED),
                    )
                )
        elif item.stream == "spectrum" and isinstance(item.value, SpectrumFrame):
            self._dropped_spectrum_frames += 1
            if self._spectrum is not None and self._spectrum.started:
                self._spectrum.record_gap(
                    reason=reason,
                    frame_sequence=int(item.value.frame_sequence),
                    timestamp_ns=int(item.value.timestamp_ns),
                )

    def _run(self) -> None:
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    self._queue.task_done()
                    break
                try:
                    if item.stream == "iq":
                        assert self._iq is not None
                        self._iq.write_block(item.value)  # type: ignore[arg-type]
                    else:
                        assert self._spectrum is not None
                        self._spectrum.write_frame(item.value)  # type: ignore[arg-type]
                finally:
                    self._queue.task_done()
        except BaseException as exc:  # worker errors are delivered by stop()
            self._error = exc
            self._stop.set()

    def stats(self) -> RecordingStats:
        return RecordingStats(
            enqueued_items=self._enqueued,
            written_iq_blocks=0 if self._iq is None else self._iq.written_blocks,
            written_iq_samples=0 if self._iq is None else self._iq.written_samples,
            written_spectrum_frames=0 if self._spectrum is None else self._spectrum.written_frames,
            dropped_items=self._dropped_items,
            dropped_iq_blocks=self._dropped_iq_blocks,
            dropped_iq_samples=self._dropped_iq_samples,
            dropped_spectrum_frames=self._dropped_spectrum_frames,
            gap_count=(0 if self._iq is None else self._iq.gap_count)
            + (0 if self._spectrum is None else self._spectrum.gap_count),
            gap_samples=0 if self._iq is None else self._iq.gap_samples,
            abandoned_items=self._abandoned,
            queue_depth=self._queue.qsize(),
            queue_high_water=self._high_water,
            stopped_on_overflow=self._stopped_on_overflow,
            cancelled=self._cancelled,
            finalized=(self._iq is None or not self._iq.started)
            and (self._spectrum is None or not self._spectrum.started)
            and not self._cancelled
            and self._error is None,
            error=None if self._error is None else str(self._error),
        )

    def stop(self, *, finalize: bool = True, cancel: bool = False) -> RecordingStats:
        with self._condition:
            if not self._running:
                return self.stats()
            self._cancelled = bool(cancel)
            if cancel or self._error is not None:
                self._stop.set()
                reason = "recording_cancelled_abandoned" if cancel else "worker_error_abandoned"
                while True:
                    try:
                        old = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    if old is not None:
                        self._abandoned += 1
                        self._record_drop(old, reason)
                    self._queue.task_done()
            self._queue.put(None)
        assert self._thread is not None
        self._thread.join()
        self._running = False
        if self._error is not None:
            finalize = False
        try:
            if finalize and not cancel and self._error is None:
                if self._iq is not None and self._iq.started:
                    self._iq.finalize()
                if self._spectrum is not None and self._spectrum.started:
                    self._spectrum.finalize()
            else:
                reason = "worker_error" if self._error is not None else "cancelled"
                if self._iq is not None and self._iq.started:
                    self._iq.abort(reason)
                if self._spectrum is not None and self._spectrum.started:
                    self._spectrum.abort(reason)
        except Exception as exc:
            self._error = exc
            if self._iq is not None and self._iq.started:
                self._iq.abort("finalize_failed")
            if self._spectrum is not None and self._spectrum.started:
                self._spectrum.abort("finalize_failed")
        if self._error is not None:
            raise RecordingError(str(self._error)) from self._error
        return self.stats()

    def cancel(self) -> RecordingStats:
        return self.stop(finalize=False, cancel=True)

    def close(self) -> None:
        if self._running:
            self.cancel()

    def __enter__(self) -> "RecordingService":
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.stop()
        else:
            self.cancel()


def replay_iq_through_native(
    replay: IqReplay,
    dsp_config: Any,
    *,
    backend: str = "cpu",
    allow_runtime_fallback: bool = True,
    cancel: threading.Event | None = None,
) -> Iterator[SpectrumFrame]:
    """Feed recorded IQ chunks through the existing native DSP boundary."""

    from .native_api import require_native

    native = require_native()
    native_config = config_to_native(dsp_config)
    backend_name = backend.casefold()
    if backend_name == "cpu":
        processor = native.CpuDspBackend()
    else:
        preference = getattr(native.ComputeBackendKind, backend_name.upper())
        selection = native.DspBackendSelectionOptions(
            preference=preference,
            allow_runtime_fallback=allow_runtime_fallback,
        )
        processor = native.make_dsp_backend(selection)
    processor.configure(native_config)
    source_payload = (
        replay.metadata.get("sdr", {}).get("source", {}) if isinstance(replay.metadata.get("sdr"), Mapping) else {}
    )
    if isinstance(source_payload, Mapping):
        source = _source_from_payload(
            {
                **dict(source_payload),
                "source_type": SourceType.RECORDED_IQ.value,
                "source_id": str(source_payload.get("source_id", "recorded-iq")),
                "display_name": str(source_payload.get("display_name", "Recorded IQ")),
            }
        )
    else:
        source = SourceDescriptor(SourceType.RECORDED_IQ, "recorded-iq", "Recorded IQ")

    def poll() -> Iterator[SpectrumFrame]:
        for native_frame in processor.poll_spectrum(0, True):
            yield SpectrumFrame.from_native(native_frame)

    for block, samples in replay.iter_complex_samples(cancel=cancel):
        if cancel is not None and cancel.is_set():
            return
        processor.push_samples(
            np.asarray(samples, dtype=np.complex64, order="C"),
            float(block.sample_rate_hz),
            float(block.center_frequency_hz),
            int(block.first_sample_index),
        )
        for frame in poll():
            yield replace(frame, source=source)
    for frame in poll():
        yield replace(frame, source=source)


def recover_spectrum_recording(output_uri: str | Path, *, finalize: bool = False) -> int:
    """Drop an incomplete final JSONL line and optionally finalize the prefix."""

    base = _base_path(output_uri)
    data_path = base.with_name(base.name + ".spectrum.jsonl")
    meta_path = base.with_name(base.name + ".spectrum.json")
    data_part, meta_part = _part(data_path), _part(meta_path)
    if not data_part.exists() or not meta_part.exists():
        raise RecordingCorruptionError("spectrum .part set is incomplete")
    retained: list[str] = []
    dropped = 0
    with data_part.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                item = json.loads(line)
                if not isinstance(item, Mapping) or item.get("schema") != SPECTRUM_SCHEMA_NAME:
                    raise ValueError("unknown schema")
                retained.append(line if line.endswith("\n") else line + "\n")
            except (json.JSONDecodeError, TypeError, ValueError):
                dropped += 1
                break
    temporary = Path(str(data_part) + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(retained)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, data_part)
    metadata = _read_json(meta_part)
    metadata["recovered"] = True
    metadata["recovery_dropped_lines"] = dropped
    metadata["completed"] = bool(finalize)
    _write_json_atomic(meta_part, metadata)
    if finalize:
        if data_path.exists() or meta_path.exists():
            raise RecordingError("cannot finalize recovery over existing spectrum output")
        os.replace(data_part, data_path)
        os.replace(meta_part, meta_path)
    return dropped


__all__ = [
    "IqRecordingWriter",
    "IqReplay",
    "InsufficientStorageError",
    "RecordingCorruptionError",
    "RecordingError",
    "RecordingOptions",
    "RecordingOverflowError",
    "RecordingQueuePolicy",
    "RecordingRecoveryResult",
    "RecordingService",
    "RecordingStats",
    "SPECTRUM_SCHEMA_NAME",
    "SPECTRUM_SCHEMA_VERSION",
    "SpectrumRecordingWriter",
    "SpectrumReplay",
    "StorageForecast",
    "estimate_storage",
    "preflight_storage",
    "recover_iq_recording",
    "recover_spectrum_recording",
    "replay_iq_through_native",
]
