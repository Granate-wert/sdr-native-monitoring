"""One-shot bounded USB-CDC collector for tinySA ``scanraw`` traces.

The collector owns exactly one non-continuous command and one serial-port
lifetime.  It does not configure persistent instrument state, retry a failed
measurement, expose the binary response, or reinterpret device-reported dBm as
SDR dBFS.  R11-U remains the only binary decoder.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from serial import EIGHTBITS, PARITY_NONE, STOPBITS_ONE, Serial

from .tinysa_capability_adapter import TinySaModel
from .tinysa_sweep_policy import (
    TINYSA_PRODUCT_SCANRAW_PAYLOAD_BYTES_MAX,
    TINYSA_PRODUCT_SCANRAW_POINTS_MAX,
)
from .tinysa_trace_parser import (
    TinySaSpectrumTrace,
    parse_tinysa_scanraw_trace,
)

TINYSA_SCANRAW_OPTION_SINGLE_BUFFERED = 0
TINYSA_ZERO_OFFSET_QUERY = b"zero ?\r"
MAX_TINYSA_SCANRAW_PREFIX_BYTES = 512
MAX_TINYSA_SCANRAW_READ_BYTES = 1_024
MAX_TINYSA_SCANRAW_DEADLINE_SECONDS = 120.0
MAX_TINYSA_ZERO_RESPONSE_BYTES = 512
TINYSA_ZERO_RESPONSE_DEADLINE_SECONDS = 2.0
TINYSA_SCANRAW_BAUD = 115_200
_PORT_PATTERN = re.compile(r"COM(?:[1-9]|[1-9][0-9]|[12][0-9]{2})", re.IGNORECASE)
_ZERO_OFFSET_LINE_PATTERN = re.compile(r"(?im)^\s*([0-9]{1,3})\s*dBm\s*$")
_MAX_TIMESTAMP_NS = (1 << 63) - 1
_MIN_FREQUENCY_HZ = 100_000
_MODEL_MAX_FREQUENCY_HZ = {
    TinySaModel.BASIC: 960_000_000,
    TinySaModel.ULTRA: 5_300_000_000,
}


class TinySaTraceCollectionError(RuntimeError):
    """Route-free finite collection failure."""


class TinySaTraceCollectionCancelled(TinySaTraceCollectionError):
    """Explicit caller cancellation before a complete frame."""


class TinySaTraceSerialPort(Protocol):
    is_open: bool
    dtr: bool
    rts: bool

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...

    def reset_input_buffer(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TinySaScanRawRequest:
    """Immutable one-shot command contract; continuous options are rejected."""

    model: TinySaModel
    start_frequency_hz: int
    stop_frequency_hz: int
    points: int
    deadline_seconds: float = 30.0
    option: int = TINYSA_SCANRAW_OPTION_SINGLE_BUFFERED

    def __post_init__(self) -> None:
        model = TinySaModel(self.model)
        start = _bounded_integer(self.start_frequency_hz, "start frequency")
        stop = _bounded_integer(self.stop_frequency_hz, "stop frequency")
        points = _bounded_integer(self.points, "point count")
        deadline = _finite_number(self.deadline_seconds, "response deadline")
        if not _MIN_FREQUENCY_HZ <= start < stop <= _MODEL_MAX_FREQUENCY_HZ[model]:
            raise ValueError("tinySA scanraw frequencies are outside the admitted model range")
        if not 2 <= points <= TINYSA_PRODUCT_SCANRAW_POINTS_MAX:
            raise ValueError("tinySA scanraw point count is outside the fixed bound")
        if 2 + 3 * points > TINYSA_PRODUCT_SCANRAW_PAYLOAD_BYTES_MAX:
            raise ValueError("tinySA scanraw frame exceeds the fixed byte bound")
        if (stop - start) // points <= 0:
            raise ValueError("tinySA scanraw frequency step must be at least one hertz")
        if self.option != TINYSA_SCANRAW_OPTION_SINGLE_BUFFERED:
            raise ValueError("tinySA collector admits only one buffered non-continuous scan")
        if not 0.05 <= deadline <= MAX_TINYSA_SCANRAW_DEADLINE_SECONDS:
            raise ValueError("tinySA scanraw deadline is outside the fixed bound")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "start_frequency_hz", start)
        object.__setattr__(self, "stop_frequency_hz", stop)
        object.__setattr__(self, "points", points)
        object.__setattr__(self, "deadline_seconds", deadline)

    @property
    def command(self) -> bytes:
        return (
            f"scanraw {self.start_frequency_hz} {self.stop_frequency_hz} "
            f"{self.points} {self.option}\r"
        ).encode("ascii")

    @property
    def expected_frame_bytes(self) -> int:
        return 2 + 3 * self.points


@dataclass(frozen=True, slots=True)
class TinySaTraceCollection:
    """Parsed trace plus bounded host-side collection scalars."""

    trace: TinySaSpectrumTrace
    command_bytes: int
    response_bytes_read: int
    discarded_prefix_bytes: int
    read_calls: int
    zero_response_bytes: int
    zero_read_calls: int
    scanraw_zero_offset_db: float
    elapsed_seconds: float
    port_closed: bool
    measurement_commands: int = 1
    readback_commands: int = 1
    device_scans_requested: int = 1
    retries: int = 0

    def __post_init__(self) -> None:
        if self.command_bytes <= 0:
            raise ValueError("tinySA collection command byte count must be positive")
        if self.response_bytes_read < self.trace.values_dbm.size * 3 + 2:
            raise ValueError("tinySA collection response count is shorter than the parsed frame")
        if not 0 <= self.discarded_prefix_bytes <= MAX_TINYSA_SCANRAW_PREFIX_BYTES:
            raise ValueError("tinySA collection prefix count is outside the fixed bound")
        if self.read_calls <= 0:
            raise ValueError("tinySA collection requires at least one bounded read")
        if self.zero_response_bytes <= 0 or self.zero_response_bytes > MAX_TINYSA_ZERO_RESPONSE_BYTES:
            raise ValueError("tinySA zero response byte count is outside the fixed bound")
        if self.zero_read_calls <= 0:
            raise ValueError("tinySA zero readback requires at least one bounded read")
        if self.scanraw_zero_offset_db != self.trace.scanraw_zero_offset_db:
            raise ValueError("tinySA zero readback must match the parsed trace offset")
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds < 0.0:
            raise ValueError("tinySA collection elapsed time must be finite")
        if not self.port_closed:
            raise ValueError("tinySA collection cannot complete with an open port")
        if (
            self.measurement_commands,
            self.readback_commands,
            self.device_scans_requested,
            self.retries,
        ) != (1, 1, 1, 0):
            raise ValueError("tinySA collection action accounting is invalid")


def collect_tinysa_scanraw_trace(
    port: str,
    request: TinySaScanRawRequest,
    *,
    serial_factory: Callable[[str], TinySaTraceSerialPort] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> TinySaTraceCollection:
    """Execute exactly one request, close the port, then return reduced ownership.

    Any transport, framing, parser or close failure is converted into one
    route-free error.  There is deliberately no retry path.
    """

    normalized_port = _validated_port(port)
    if not isinstance(request, TinySaScanRawRequest):
        raise TypeError("tinySA collection requires a validated immutable request")
    if cancel_requested is not None and not callable(cancel_requested):
        raise TypeError("tinySA cancellation source must be callable")
    if not callable(monotonic_ns):
        raise TypeError("tinySA monotonic clock must be callable")
    serial_port = (
        _make_serial(normalized_port)
        if serial_factory is None
        else serial_factory(normalized_port)
    )
    if not _is_serial_port(serial_port):
        raise ValueError("tinySA serial factory returned an invalid bounded port")

    started_ns = monotonic_ns()
    parsed_trace: TinySaSpectrumTrace | None = None
    response_bytes_read = 0
    discarded_prefix_bytes = 0
    read_calls = 0
    zero_response_bytes = 0
    zero_read_calls = 0
    zero_offset_db: float | None = None
    collection_error: Exception | None = None
    close_error: Exception | None = None
    try:
        serial_port.dtr = False
        serial_port.rts = False
        serial_port.open()
        serial_port.reset_input_buffer()
        if cancel_requested is not None and cancel_requested():
            raise TinySaTraceCollectionCancelled("tinySA trace collection was cancelled")
        zero_written = serial_port.write(TINYSA_ZERO_OFFSET_QUERY)
        if zero_written != len(TINYSA_ZERO_OFFSET_QUERY):
            raise TinySaTraceCollectionError("tinySA zero query was not written completely")
        serial_port.flush()
        zero_offset_db, zero_response_bytes, zero_read_calls = _read_zero_offset(
            serial_port,
            cancel_requested=cancel_requested,
            monotonic_ns=monotonic_ns,
        )
        if cancel_requested is not None and cancel_requested():
            raise TinySaTraceCollectionCancelled("tinySA trace collection was cancelled")
        written = serial_port.write(request.command)
        if written != len(request.command):
            raise TinySaTraceCollectionError("tinySA scanraw command was not written completely")
        serial_port.flush()
        scan_started_ns = monotonic_ns()
        frame, response_bytes_read, discarded_prefix_bytes, read_calls = _read_one_frame(
            serial_port,
            request,
            cancel_requested=cancel_requested,
            monotonic_ns=monotonic_ns,
            started_ns=scan_started_ns,
        )
        parsed_trace = parse_tinysa_scanraw_trace(
            frame,
            model=request.model,
            start_frequency_hz=request.start_frequency_hz,
            stop_frequency_hz=request.stop_frequency_hz,
            host_timestamp_ns=monotonic_ns(),
            scanraw_zero_offset_db=zero_offset_db,
        )
    except Exception as error:  # noqa: BLE001 - all ordinary port failures must fail closed.
        collection_error = error
    finally:
        if getattr(serial_port, "is_open", False):
            try:
                serial_port.close()
            except Exception as error:  # noqa: BLE001 - close failure must remain visible.
                close_error = error

    elapsed_seconds = max(0.0, (monotonic_ns() - started_ns) / 1_000_000_000.0)
    if close_error is not None:
        raise TinySaTraceCollectionError("tinySA trace port did not close cleanly") from None
    if collection_error is not None:
        if isinstance(collection_error, TinySaTraceCollectionCancelled):
            raise collection_error
        raise TinySaTraceCollectionError("tinySA trace collection failed closed") from None
    if parsed_trace is None or zero_offset_db is None or getattr(serial_port, "is_open", False):
        raise TinySaTraceCollectionError("tinySA trace collection did not reach a closed result")
    return TinySaTraceCollection(
        trace=parsed_trace,
        command_bytes=len(request.command),
        response_bytes_read=response_bytes_read,
        discarded_prefix_bytes=discarded_prefix_bytes,
        read_calls=read_calls,
        zero_response_bytes=zero_response_bytes,
        zero_read_calls=zero_read_calls,
        scanraw_zero_offset_db=zero_offset_db,
        elapsed_seconds=elapsed_seconds,
        port_closed=True,
    )


def parse_tinysa_zero_offset_response(response: bytes | bytearray | memoryview) -> float:
    """Parse one bounded read-only ``zero ?`` response and its prompt."""

    if not isinstance(response, (bytes, bytearray, memoryview)):
        raise TinySaTraceCollectionError("tinySA zero response must be bytes")
    raw = bytes(response)
    if not raw or len(raw) > MAX_TINYSA_ZERO_RESPONSE_BYTES or raw.count(b"ch> ") != 1:
        raise TinySaTraceCollectionError("tinySA zero response is incomplete or oversized")
    prompt_end = raw.rfind(b"ch> ") + len(b"ch> ")
    if any(value not in b"\t\r\n " for value in raw[prompt_end:]):
        raise TinySaTraceCollectionError("tinySA zero response contains trailing data")
    try:
        decoded = raw[:prompt_end].decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise TinySaTraceCollectionError("tinySA zero response is not bounded ASCII") from error
    matches = _ZERO_OFFSET_LINE_PATTERN.findall(decoded.replace("ch> ", ""))
    if len(matches) != 1:
        raise TinySaTraceCollectionError("tinySA zero response has no unique offset")
    offset = float(matches[0])
    if not 0.0 <= offset <= 512.0:
        raise TinySaTraceCollectionError("tinySA zero offset is outside the fixed bound")
    return offset


def _read_zero_offset(
    serial_port: TinySaTraceSerialPort,
    *,
    cancel_requested: Callable[[], bool] | None,
    monotonic_ns: Callable[[], int],
) -> tuple[float, int, int]:
    deadline_ns = monotonic_ns() + round(TINYSA_ZERO_RESPONSE_DEADLINE_SECONDS * 1_000_000_000)
    response = bytearray()
    read_calls = 0
    while monotonic_ns() < deadline_ns:
        if cancel_requested is not None and cancel_requested():
            raise TinySaTraceCollectionCancelled("tinySA trace collection was cancelled")
        chunk = serial_port.read(min(256, MAX_TINYSA_ZERO_RESPONSE_BYTES - len(response) + 1))
        read_calls += 1
        if not isinstance(chunk, bytes):
            raise TinySaTraceCollectionError("tinySA serial read returned a non-byte payload")
        response.extend(chunk)
        if len(response) > MAX_TINYSA_ZERO_RESPONSE_BYTES:
            raise TinySaTraceCollectionError("tinySA zero response exceeds the fixed bound")
        if b"ch> " in response:
            return parse_tinysa_zero_offset_response(response), len(response), read_calls
    raise TinySaTraceCollectionError("tinySA zero response exceeded the absolute deadline")


def _read_one_frame(
    serial_port: TinySaTraceSerialPort,
    request: TinySaScanRawRequest,
    *,
    cancel_requested: Callable[[], bool] | None,
    monotonic_ns: Callable[[], int],
    started_ns: int,
) -> tuple[bytes, int, int, int]:
    deadline_ns = started_ns + round(request.deadline_seconds * 1_000_000_000)
    frame = bytearray()
    response_bytes = 0
    prefix_bytes = 0
    read_calls = 0
    while monotonic_ns() < deadline_ns:
        if cancel_requested is not None and cancel_requested():
            raise TinySaTraceCollectionCancelled("tinySA trace collection was cancelled")
        chunk = serial_port.read(MAX_TINYSA_SCANRAW_READ_BYTES)
        read_calls += 1
        if not isinstance(chunk, bytes):
            raise TinySaTraceCollectionError("tinySA serial read returned a non-byte payload")
        response_bytes += len(chunk)
        for value in chunk:
            if not frame:
                if value == ord("{"):
                    frame.append(value)
                    continue
                if value not in b"\t\r\n " and not 0x20 <= value <= 0x7E:
                    raise TinySaTraceCollectionError("tinySA response prefix is not bounded ASCII")
                prefix_bytes += 1
                if prefix_bytes > MAX_TINYSA_SCANRAW_PREFIX_BYTES:
                    raise TinySaTraceCollectionError("tinySA response prefix exceeds the fixed bound")
                continue
            if len(frame) < request.expected_frame_bytes:
                frame.append(value)
                continue
            if value == ord("{") or (value not in b"\t\r\n " and not 0x20 <= value <= 0x7E):
                raise TinySaTraceCollectionError("tinySA returned extra binary data after one frame")
        if len(frame) == request.expected_frame_bytes:
            if frame[-1] != ord("}"):
                raise TinySaTraceCollectionError("tinySA scanraw frame lacks its fixed terminator")
            return bytes(frame), response_bytes, prefix_bytes, read_calls
        if len(frame) > request.expected_frame_bytes:
            raise TinySaTraceCollectionError("tinySA scanraw frame exceeds its fixed bound")
    raise TinySaTraceCollectionError("tinySA scanraw response exceeded the absolute deadline")


def _validated_port(port: str) -> str:
    if not isinstance(port, str) or not _PORT_PATTERN.fullmatch(port):
        raise ValueError("tinySA serial port must be one normalized COM name")
    return port.upper()


def _make_serial(port: str) -> TinySaTraceSerialPort:
    serial_port = Serial(
        port=None,
        baudrate=TINYSA_SCANRAW_BAUD,
        bytesize=EIGHTBITS,
        parity=PARITY_NONE,
        stopbits=STOPBITS_ONE,
        timeout=0.05,
        write_timeout=1.0,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )
    serial_port.dtr = False
    serial_port.rts = False
    serial_port.port = _validated_port(port)
    return serial_port


def _is_serial_port(value: object) -> bool:
    return all(
        hasattr(value, name)
        for name in ("open", "close", "read", "write", "flush", "reset_input_buffer")
    )


def _bounded_integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"tinySA {label} must be an integer")
    return value


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"tinySA {label} must be finite")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"tinySA {label} must be finite")
    return normalized


__all__ = [
    "MAX_TINYSA_SCANRAW_DEADLINE_SECONDS",
    "MAX_TINYSA_SCANRAW_PREFIX_BYTES",
    "MAX_TINYSA_ZERO_RESPONSE_BYTES",
    "TINYSA_PRODUCT_SCANRAW_PAYLOAD_BYTES_MAX",
    "TINYSA_PRODUCT_SCANRAW_POINTS_MAX",
    "TINYSA_SCANRAW_OPTION_SINGLE_BUFFERED",
    "TINYSA_ZERO_OFFSET_QUERY",
    "TinySaScanRawRequest",
    "TinySaTraceCollection",
    "TinySaTraceCollectionCancelled",
    "TinySaTraceCollectionError",
    "collect_tinysa_scanraw_trace",
    "parse_tinysa_zero_offset_response",
]
