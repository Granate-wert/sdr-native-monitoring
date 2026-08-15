"""Bounded parser for an already captured tinySA ``scanraw`` trace.

This module deliberately owns no serial transport and emits no instrument
command.  A later explicitly-confirmed runtime adapter may provide bytes from
the tinySA USB-CDC ``scanraw`` command, but must pass the complete bounded
response into this parser.  The result remains a device-reported dBm trace;
it is neither SDR I/Q nor a dBFS-to-dBm conversion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .tinysa_capability_adapter import TinySaModel

TINYSA_SCANRAW_ENCODING = "tinysa-scanraw-x-u16le-v2"
TINYSA_TRACE_VALUE_PROVENANCE = "device_reported_trace"
TINYSA_TRACE_CALIBRATION_PROVENANCE = "device_reported_builtin"
MAX_TINYSA_TRACE_POINTS = 10_001
MAX_TINYSA_TRACE_PAYLOAD_BYTES = 30 * 1_024
_MAX_TIMESTAMP_NS = (1 << 63) - 1


class TinySaTraceParseError(ValueError):
    """Finite, route-free parse failure for a tinySA trace response."""


@dataclass(frozen=True, slots=True)
class TinySaSpectrumTrace:
    """One immutable analyzer trace with explicit device dBm semantics.

    ``host_timestamp_ns`` denotes bounded host observation time only.  It is
    not a hardware timestamp, and no continuity, sweep rate, or metrological
    accuracy inference can be made from it.
    """

    model: TinySaModel
    start_frequency_hz: float
    stop_frequency_hz: float
    host_timestamp_ns: int
    scanraw_zero_offset_db: float
    frequencies_hz: np.ndarray
    values_dbm: np.ndarray
    unit: str = "dBm"
    value_provenance: str = TINYSA_TRACE_VALUE_PROVENANCE
    calibration_provenance: str = TINYSA_TRACE_CALIBRATION_PROVENANCE
    encoding: str = TINYSA_SCANRAW_ENCODING
    raw_iq_available: bool = False
    dbfs_conversion_available: bool = False
    hardware_timestamp_available: bool = False
    external_correction_applied: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "model", TinySaModel(self.model))
        start = _finite_frequency(self.start_frequency_hz, "start frequency")
        stop = _finite_frequency(self.stop_frequency_hz, "stop frequency")
        if stop < start:
            raise ValueError("tinySA trace stop frequency must not precede start frequency")
        timestamp = _bounded_timestamp(self.host_timestamp_ns)
        zero_offset = _bounded_zero_offset(self.scanraw_zero_offset_db)
        # Own both arrays before marking them read-only. ``np.asarray`` could
        # otherwise retain a caller-owned alias that remains externally
        # mutable despite this frozen trace contract.
        frequencies = np.array(self.frequencies_hz, dtype=np.float64, copy=True).reshape(-1)
        values = np.array(self.values_dbm, dtype=np.float32, copy=True).reshape(-1)
        if not 1 <= frequencies.size <= MAX_TINYSA_TRACE_POINTS:
            raise ValueError("tinySA trace point count is outside the fixed bound")
        if values.size != frequencies.size:
            raise ValueError("tinySA trace axes must contain the same point count")
        if not np.isfinite(frequencies).all() or not np.isfinite(values).all():
            raise ValueError("tinySA trace values must be finite")
        expected_frequencies = _scanraw_frequency_axis(start, stop, frequencies.size)
        if not np.array_equal(frequencies, expected_frequencies):
            raise ValueError("tinySA trace frequency axis must match the firmware stop-exclusive grid")
        if self.unit != "dBm":
            raise ValueError("tinySA trace unit must remain device-reported dBm")
        if self.value_provenance != TINYSA_TRACE_VALUE_PROVENANCE:
            raise ValueError("tinySA trace must retain device-reported provenance")
        if self.calibration_provenance != TINYSA_TRACE_CALIBRATION_PROVENANCE:
            raise ValueError("tinySA trace must retain built-in calibration provenance")
        if self.encoding != TINYSA_SCANRAW_ENCODING:
            raise ValueError("tinySA trace encoding is not the admitted scanraw format")
        if (
            self.raw_iq_available
            or self.dbfs_conversion_available
            or self.hardware_timestamp_available
            or self.external_correction_applied
        ):
            raise ValueError("tinySA trace parser cannot imply I/Q, dBFS, hardware time, or correction")
        frequencies.setflags(write=False)
        values.setflags(write=False)
        object.__setattr__(self, "start_frequency_hz", start)
        object.__setattr__(self, "stop_frequency_hz", stop)
        object.__setattr__(self, "host_timestamp_ns", timestamp)
        object.__setattr__(self, "scanraw_zero_offset_db", zero_offset)
        object.__setattr__(self, "frequencies_hz", frequencies)
        object.__setattr__(self, "values_dbm", values)


def parse_tinysa_scanraw_trace(
    payload: bytes | bytearray | memoryview,
    *,
    model: TinySaModel,
    start_frequency_hz: float,
    stop_frequency_hz: float,
    host_timestamp_ns: int,
    scanraw_zero_offset_db: float | None = None,
) -> TinySaSpectrumTrace:
    """Decode one complete ``{(x LSB MSB)*points}`` scanraw response.

    The firmware implementation writes a literal ``x`` pad byte followed by
    the low and high bytes of one unsigned 16-bit level.  Levels are divided
    by 32 and then reduced by the device ``zero`` offset.  The model default
    (128 dB for Basic or 174 dB for Ultra) is used only when the caller has no
    retained readback; physical evidence must supply the observed offset.
    Echoed commands, prompts, partial reads and concatenated continuous
    responses are deliberately rejected.
    """

    normalized_model = TinySaModel(model)
    raw = _bounded_bytes(payload)
    if len(raw) < 5 or raw[0] != ord("{") or raw[-1] != ord("}"):
        raise TinySaTraceParseError("tinySA scanraw frame must be one complete brace-delimited payload")
    body = raw[1:-1]
    if not body or len(body) % 3 != 0:
        raise TinySaTraceParseError("tinySA scanraw payload has an invalid fixed record length")
    point_count = len(body) // 3
    if point_count > MAX_TINYSA_TRACE_POINTS:
        raise TinySaTraceParseError("tinySA scanraw point count exceeds the fixed bound")

    records = np.frombuffer(body, dtype=np.uint8).reshape(point_count, 3)
    if not np.all(records[:, 0] == ord("x")):
        raise TinySaTraceParseError("tinySA scanraw payload has an invalid record marker")
    encoded_levels = records[:, 1].astype(np.uint16) | (records[:, 2].astype(np.uint16) << 8)
    default_offset_db = 128.0 if normalized_model is TinySaModel.BASIC else 174.0
    offset_db = _bounded_zero_offset(
        default_offset_db if scanraw_zero_offset_db is None else scanraw_zero_offset_db
    )
    values_dbm = encoded_levels.astype(np.float32) / np.float32(32.0) - np.float32(offset_db)

    start = _finite_frequency(start_frequency_hz, "start frequency")
    stop = _finite_frequency(stop_frequency_hz, "stop frequency")
    if stop < start:
        raise TinySaTraceParseError("tinySA scanraw stop frequency must not precede start frequency")
    frequencies = _scanraw_frequency_axis(start, stop, point_count)
    try:
        return TinySaSpectrumTrace(
            model=normalized_model,
            start_frequency_hz=start,
            stop_frequency_hz=stop,
            host_timestamp_ns=host_timestamp_ns,
            scanraw_zero_offset_db=offset_db,
            frequencies_hz=frequencies,
            values_dbm=values_dbm,
        )
    except ValueError as error:
        raise TinySaTraceParseError("tinySA scanraw metadata is outside the admitted bounds") from error


def _bounded_bytes(value: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TinySaTraceParseError("tinySA scanraw payload must be bytes")
    raw = bytes(value)
    if len(raw) > MAX_TINYSA_TRACE_PAYLOAD_BYTES:
        raise TinySaTraceParseError("tinySA scanraw payload exceeds the fixed byte bound")
    return raw


def _finite_frequency(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TinySaTraceParseError(f"tinySA {label} must be finite")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise TinySaTraceParseError(f"tinySA {label} must be finite and non-negative")
    return normalized


def _bounded_timestamp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_TIMESTAMP_NS:
        raise TinySaTraceParseError("tinySA host timestamp is outside the fixed bound")
    return value


def _bounded_zero_offset(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TinySaTraceParseError("tinySA scanraw zero offset must be finite")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 512.0:
        raise TinySaTraceParseError("tinySA scanraw zero offset is outside the fixed bound")
    return normalized


def _scanraw_frequency_axis(start: float, stop: float, point_count: int) -> np.ndarray:
    """Reproduce the integer-Hz, stop-exclusive grid used by tinySA firmware."""

    if not start.is_integer() or not stop.is_integer():
        raise TinySaTraceParseError("tinySA scanraw frequencies must be integer Hz")
    if point_count <= 0:
        raise TinySaTraceParseError("tinySA scanraw point count must be positive")
    step_hz = (int(stop) - int(start)) // point_count
    if point_count > 1 and step_hz <= 0:
        raise TinySaTraceParseError("tinySA scanraw frequency step must be at least one hertz")
    return float(start) + np.arange(point_count, dtype=np.float64) * float(step_hz)


__all__ = [
    "MAX_TINYSA_TRACE_PAYLOAD_BYTES",
    "MAX_TINYSA_TRACE_POINTS",
    "TINYSA_SCANRAW_ENCODING",
    "TINYSA_TRACE_CALIBRATION_PROVENANCE",
    "TINYSA_TRACE_VALUE_PROVENANCE",
    "TinySaSpectrumTrace",
    "TinySaTraceParseError",
    "parse_tinysa_scanraw_trace",
]
