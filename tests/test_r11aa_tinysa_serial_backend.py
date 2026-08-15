"""R11-AA fake-PySerial backend and finite settings-port tests."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_serial_settings_port import (
    TinySaSerialSettingsCommandPort,
)
from sdr_monitor.services.tinysa_serial_source_backend import TinySaSerialSourceBackend
from sdr_monitor.services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollection,
)
from sdr_monitor.services.tinysa_serial_version_probe import TinySaVersionObservation
from sdr_monitor.services.tinysa_source_composition import (
    TinySaIdentityAssurance,
    TinySaSourceCompositionService,
)
from sdr_monitor.services.tinysa_sweep_settings_controller import (
    R11W_TINYSA_SETTINGS_CONFIRMATION,
    TinySaSettingsApplyStatus,
    TinySaSweepSettingsPlan,
    TinySaSwitchPolicy,
)
from sdr_monitor.services.tinysa_trace_parser import parse_tinysa_scanraw_trace


@dataclass
class _PortInfo:
    device: str
    vid: int = 0x0483
    pid: int = 0x5740
    serial_number: str | None = "UNIT-A"
    location: str | None = "1-2"


class _Inventory:
    def __init__(self) -> None:
        self.calls = 0
        self.route = "COM31"

    def __call__(self) -> tuple[_PortInfo, ...]:
        self.calls += 1
        return (_PortInfo(self.route),)


class _CommandPort:
    def __init__(self, route: str, calls: list[object]) -> None:
        self.route = route
        self.calls = calls

    def command(self, command: bytes) -> bytes:
        self.calls.append(("settings_command", self.route, command))
        return command + b"\nch> "

    def close(self) -> None:
        self.calls.append(("settings_close", self.route))


def _collection(request: TinySaScanRawRequest) -> TinySaTraceCollection:
    frame = b"{" + b"x\x80\x0c" * request.points + b"}"
    trace = parse_tinysa_scanraw_trace(
        frame,
        model=request.model,
        start_frequency_hz=request.start_frequency_hz,
        stop_frequency_hz=request.stop_frequency_hz,
        host_timestamp_ns=1,
        scanraw_zero_offset_db=174.0,
    )
    return TinySaTraceCollection(
        trace=trace,
        command_bytes=len(request.command),
        response_bytes_read=len(frame),
        discarded_prefix_bytes=0,
        read_calls=1,
        zero_response_bytes=16,
        zero_read_calls=1,
        scanraw_zero_offset_db=174.0,
        elapsed_seconds=1.0,
        port_closed=True,
    )


class TinySaSerialSourceBackendTests(unittest.TestCase):
    def test_discovery_is_pnp_only_and_effecting_adapters_revalidate_identity(self) -> None:
        inventory = _Inventory()
        calls: list[object] = []

        def probe(route: str) -> TinySaVersionObservation:
            calls.append(("probe", route))
            return TinySaVersionObservation(
                TinySaModel.ULTRA,
                "tinySA4_v1.4-test",
                "sha256:" + "b" * 64,
            )

        def collect(route: str, request: TinySaScanRawRequest) -> TinySaTraceCollection:
            calls.append(("collect", route, request.points))
            return _collection(request)

        backend = TinySaSerialSourceBackend(
            inventory_provider=inventory,
            version_probe=probe,
            trace_collector=collect,
            settings_port_factory=lambda route: _CommandPort(route, calls),
        )
        service = TinySaSourceCompositionService(backend)

        discovered = service.discover()
        self.assertEqual(inventory.calls, 1)
        self.assertEqual(calls, [])
        self.assertNotIn("com31", repr(discovered).casefold())
        self.assertIs(
            discovered.candidates[0].identity_assurance,
            TinySaIdentityAssurance.USB_SERIAL,
        )

        service.select(discovered.candidates[0].source_id)
        inventory.route = "COM32"
        service.verify_selected()
        self.assertEqual(calls, [("probe", "COM32")])
        composed = service.compose_selected()
        self.assertEqual(calls, [("probe", "COM32")])

        request = TinySaScanRawRequest(
            TinySaModel.ULTRA,
            87_500_000,
            108_000_000,
            2,
        )
        result = composed.collector.collect(request)
        self.assertEqual(result.trace.values_dbm.size, 2)
        self.assertEqual(calls[-1], ("collect", "COM32", 2))

        settings = composed.settings_executor.apply(
            TinySaSweepSettingsPlan(lna=TinySaSwitchPolicy.ON),
            confirmation=R11W_TINYSA_SETTINGS_CONFIRMATION,
        )
        self.assertIs(settings.status, TinySaSettingsApplyStatus.ACKNOWLEDGED_UNVERIFIED)
        self.assertEqual(calls[-2:], [
            ("settings_command", "COM32", b"lna on\r"),
            ("settings_close", "COM32"),
        ])

    def test_unchanged_settings_do_not_revalidate_or_open_a_port(self) -> None:
        inventory = _Inventory()
        backend = TinySaSerialSourceBackend(
            inventory_provider=inventory,
            version_probe=lambda _route: TinySaVersionObservation(
                TinySaModel.ULTRA,
                "tinySA4_v1.4-test",
                "sha256:" + "c" * 64,
            ),
            settings_port_factory=lambda _route: (_ for _ in ()).throw(
                AssertionError("unchanged plan must not create a settings port")
            ),
        )
        service = TinySaSourceCompositionService(backend)
        candidate = service.discover().candidates[0]
        service.select(candidate.source_id)
        service.verify_selected()
        composed = service.compose_selected()
        calls_before = inventory.calls

        result = composed.settings_executor.apply(
            TinySaSweepSettingsPlan(),
            confirmation="",
        )

        self.assertIs(result.status, TinySaSettingsApplyStatus.UNCHANGED)
        self.assertEqual(inventory.calls, calls_before)

    def test_source_model_mismatch_fails_before_revalidation(self) -> None:
        inventory = _Inventory()
        backend = TinySaSerialSourceBackend(
            inventory_provider=inventory,
            version_probe=lambda _route: TinySaVersionObservation(
                TinySaModel.ULTRA,
                "tinySA4_v1.4-test",
                "sha256:" + "d" * 64,
            ),
        )
        service = TinySaSourceCompositionService(backend)
        candidate = service.discover().candidates[0]
        service.select(candidate.source_id)
        service.verify_selected()
        composed = service.compose_selected()
        calls_before = inventory.calls

        with self.assertRaisesRegex(ValueError, "verified source model"):
            composed.collector.collect(
                TinySaScanRawRequest(TinySaModel.BASIC, 100_000, 200_000, 2)
            )
        self.assertEqual(inventory.calls, calls_before)


class _FakeSerial:
    def __init__(self, response: bytes, *, short_write: bool = False) -> None:
        self.response = response
        self.short_write = short_write
        self.is_open = False
        self.dtr = True
        self.rts = True
        self.calls: list[object] = []

    def open(self) -> None:
        self.calls.append("open")
        self.is_open = True

    def close(self) -> None:
        self.calls.append("close")
        self.is_open = False

    def reset_input_buffer(self) -> None:
        self.calls.append("reset_input_buffer")

    def write(self, data: bytes) -> int:
        self.calls.append(("write", data))
        return len(data) - 1 if self.short_write else len(data)

    def flush(self) -> None:
        self.calls.append("flush")

    def read(self, size: int = 1) -> bytes:
        self.calls.append(("read", size))
        response, self.response = self.response, b""
        return response


class TinySaSerialSettingsCommandPortTests(unittest.TestCase):
    def test_construction_is_inert_allowlisted_command_opens_once_and_closes(self) -> None:
        serial = _FakeSerial(b"lna on\r\nch> ")
        port = TinySaSerialSettingsCommandPort(
            "COM31",
            serial_factory=lambda _route: serial,
        )
        self.assertEqual(serial.calls, [])

        response = port.command(b"lna on\r")
        port.close()

        self.assertEqual(response, b"lna on\r\nch> ")
        self.assertEqual(serial.calls[0:2], ["open", "reset_input_buffer"])
        self.assertEqual(serial.calls[-1], "close")
        self.assertFalse(serial.dtr)
        self.assertFalse(serial.rts)

    def test_reset_firmware_and_persistence_commands_are_rejected_before_open(self) -> None:
        for command in (
            b"reset\r",
            b"saveconfig\r",
            b"dfu\r",
            b"scanraw 1 2 3 0\r",
            b"attenuate 32\r",
            b"repeat 1001\r",
            b"rbw 850.1\r",
            b"sweeptime 60.001\r",
        ):
            with self.subTest(command=command):
                serial = _FakeSerial(b"ch> ")
                port = TinySaSerialSettingsCommandPort(
                    "COM31",
                    serial_factory=lambda _route, serial=serial: serial,
                )
                with self.assertRaisesRegex(ValueError, "allowlist"):
                    port.command(command)
                self.assertEqual(serial.calls, [])

    def test_short_write_and_oversized_response_are_finite_and_visible(self) -> None:
        cases = (
            _FakeSerial(b"", short_write=True),
            _FakeSerial(b"A" * 513),
        )
        for serial in cases:
            with self.subTest(serial=serial):
                port = TinySaSerialSettingsCommandPort(
                    "COM31",
                    serial_factory=lambda _route, serial=serial: serial,
                )
                with self.assertRaises(RuntimeError):
                    port.command(b"lna on\r")
                port.close()
                self.assertEqual(serial.calls[-1], "close")


if __name__ == "__main__":
    unittest.main()
