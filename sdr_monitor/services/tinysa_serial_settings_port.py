"""Finite USB-CDC command port for explicitly confirmed tinySA runtime settings."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Protocol

from serial import EIGHTBITS, PARITY_NONE, STOPBITS_ONE, Serial

from .tinysa_sweep_settings_controller import MAX_TINYSA_SETTINGS_RESPONSE_BYTES

TINYSA_SETTINGS_RESPONSE_DEADLINE_SECONDS = 2.0
TINYSA_SETTINGS_BAUD = 115_200
_PORT_PATTERN = re.compile(r"COM(?:[1-9]|[1-9][0-9]|[12][0-9]{2})", re.IGNORECASE)
_COMMAND_PATTERN = re.compile(
    rb"(?:"
    rb"attenuate (?:auto|[0-9]{1,2})|"
    rb"lna (?:on|off)|"
    rb"spur (?:on|off|auto)|"
    rb"rbw (?:auto|[0-9]+(?:\.[0-9]+)?)|"
    rb"repeat [0-9]{1,4}|"
    rb"sweeptime [0-9]+(?:\.[0-9]+)?|"
    rb"sweep (?:normal|precise|fast|noise)"
    rb")\r"
)


class TinySaSettingsSerialPort(Protocol):
    is_open: bool
    dtr: bool
    rts: bool

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...

    def reset_input_buffer(self) -> None: ...


class TinySaSerialSettingsCommandPort:
    """Open lazily on the first admitted command and never retry."""

    def __init__(
        self,
        route: str,
        *,
        serial_factory: Callable[[str], TinySaSettingsSerialPort] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._route = _validated_port(route)
        self._serial_factory = serial_factory
        self._monotonic = monotonic
        self._port: TinySaSettingsSerialPort | None = None
        self._closed = False

    def command(self, command: bytes) -> bytes:
        if self._closed:
            raise RuntimeError("tinySA settings command port is closed")
        if not isinstance(command, bytes) or not _is_admitted_command(command):
            raise ValueError("tinySA settings command is outside the admitted allowlist")
        port = self._ensure_open()
        written = port.write(command)
        if written != len(command):
            raise RuntimeError("tinySA settings command write was incomplete")
        port.flush()
        deadline = self._monotonic() + TINYSA_SETTINGS_RESPONSE_DEADLINE_SECONDS
        response = bytearray()
        while self._monotonic() < deadline:
            chunk = port.read(min(128, MAX_TINYSA_SETTINGS_RESPONSE_BYTES - len(response) + 1))
            if chunk:
                response.extend(chunk)
                if len(response) > MAX_TINYSA_SETTINGS_RESPONSE_BYTES:
                    raise RuntimeError("tinySA settings response exceeded the fixed bound")
                if b"ch> " in response:
                    return bytes(response)
            else:
                time.sleep(0.005)
        raise RuntimeError("tinySA settings response deadline expired")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        port = self._port
        self._port = None
        if port is not None and getattr(port, "is_open", False):
            port.close()

    def _ensure_open(self) -> TinySaSettingsSerialPort:
        if self._port is not None:
            return self._port
        port = (
            _make_serial(self._route)
            if self._serial_factory is None
            else self._serial_factory(self._route)
        )
        if not _is_serial_port(port):
            raise ValueError("tinySA settings serial factory returned an invalid port")
        port.dtr = False
        port.rts = False
        port.open()
        port.reset_input_buffer()
        self._port = port
        return port


def _validated_port(value: object) -> str:
    if not isinstance(value, str) or not _PORT_PATTERN.fullmatch(value):
        raise ValueError("tinySA serial route must be one normalized COM name")
    return value.upper()


def _is_admitted_command(command: bytes) -> bool:
    if not _COMMAND_PATTERN.fullmatch(command):
        return False
    text = command[:-1].decode("ascii")
    name, value = text.split(" ", maxsplit=1)
    if name == "attenuate" and value != "auto":
        return value.isdigit() and 0 <= int(value) <= 31
    if name == "repeat":
        return value.isdigit() and 1 <= int(value) <= 1_000
    if name == "rbw" and value != "auto":
        return _bounded_decimal(value, Decimal("0.2"), Decimal(850), Decimal("0.1"))
    if name == "sweeptime":
        return _bounded_decimal(value, Decimal("0.003"), Decimal(60), Decimal("0.001"))
    return True


def _bounded_decimal(
    value: str,
    minimum: Decimal,
    maximum: Decimal,
    quantum: Decimal,
) -> bool:
    try:
        normalized = Decimal(value)
    except InvalidOperation:
        return False
    return minimum <= normalized <= maximum and normalized % quantum == 0


def _make_serial(route: str) -> TinySaSettingsSerialPort:
    port = Serial(
        port=None,
        baudrate=TINYSA_SETTINGS_BAUD,
        bytesize=EIGHTBITS,
        parity=PARITY_NONE,
        stopbits=STOPBITS_ONE,
        timeout=0.05,
        write_timeout=1.0,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )
    port.dtr = False
    port.rts = False
    port.port = route
    return port


def _is_serial_port(value: object) -> bool:
    return all(
        hasattr(value, name)
        for name in (
            "is_open",
            "dtr",
            "rts",
            "open",
            "close",
            "read",
            "write",
            "flush",
            "reset_input_buffer",
        )
    )


__all__ = [
    "TINYSA_SETTINGS_BAUD",
    "TINYSA_SETTINGS_RESPONSE_DEADLINE_SECONDS",
    "TinySaSerialSettingsCommandPort",
    "TinySaSettingsSerialPort",
]
