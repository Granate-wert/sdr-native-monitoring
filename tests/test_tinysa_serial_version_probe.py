"""Software-only tests for the one-command tinySA USB CDC version probe."""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import cast
from unittest.mock import patch

from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_serial_version_probe import (
    TINYSA_VERSION_CONFIRMATION,
    build_tinysa_version_preflight,
    probe_tinysa_version,
)


@dataclass
class _PortInfo:
    device: str
    vid: int | None
    pid: int | None


class _FakeSerial:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.is_open = False
        self.dtr = True
        self.rts = True
        self.calls: list[object] = []
        self.command_written = False
        self.response_returned = False

    def open(self) -> None:
        self.calls.append("open")
        self.is_open = True

    def close(self) -> None:
        self.calls.append("close")
        self.is_open = False

    def read(self, _size: int = 1) -> bytes:
        self.calls.append("read")
        if not self.command_written or self.response_returned:
            return b""
        self.response_returned = True
        return self.response

    def write(self, data: bytes) -> int:
        self.calls.append(("write", data))
        self.command_written = True
        return len(data)

    def flush(self) -> None:
        self.calls.append("flush")


class TinySaSerialVersionProbeTests(unittest.TestCase):
    @patch("sdr_monitor.services.tinysa_serial_version_probe.list_ports.comports")
    def test_preflight_is_one_command_no_measurement(self, comports: object) -> None:
        comports.return_value = [_PortInfo("COM31", 0x0483, 0x5740)]  # type: ignore[attr-defined]
        payload = build_tinysa_version_preflight("COM31")

        self.assertEqual(payload["command"], "version\\r")
        self.assertEqual(payload["command_count"], 1)
        self.assertEqual(payload["retry_count"], 0)
        self.assertEqual(payload["confirmation"], TINYSA_VERSION_CONFIRMATION)
        self.assertIn("scan", cast(list[str], payload["prohibited"]))

    def test_ultra_probe_writes_exactly_one_version_command_and_closes(self) -> None:
        serial_port = _FakeSerial(b"tinySA4_v1.4-194-gabcdef\r\nch> ")
        observation = probe_tinysa_version(
            "COM31", serial_factory=lambda _port: serial_port
        )

        self.assertIs(observation.model, TinySaModel.ULTRA)
        self.assertEqual(
            [call for call in serial_port.calls if isinstance(call, tuple)],
            [("write", b"version\r")],
        )
        self.assertEqual(serial_port.calls[-1], "close")
        self.assertFalse(serial_port.dtr)
        self.assertFalse(serial_port.rts)

    def test_unrecognized_version_fails_closed_and_still_closes(self) -> None:
        serial_port = _FakeSerial(b"unknown instrument\r\nch> ")
        with self.assertRaises(ValueError):
            probe_tinysa_version("COM31", serial_factory=lambda _port: serial_port)
        self.assertEqual(serial_port.calls[-1], "close")


if __name__ == "__main__":
    unittest.main()
