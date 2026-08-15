"""Software/fake tests for the bounded R11-V tinySA trace collector."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from sdr_monitor.benchmarks.r11v_tinysa_trace_evidence import (
    R11V_TINYSA_CONFIRMATION,
    R11V_TINYSA_EVIDENCE_SCHEMA,
    R11V_TINYSA_PHYSICAL_EXECUTION_ENABLED,
    R11VTinySaProfile,
    build_r11v_tinysa_evidence,
    build_r11v_tinysa_preflight,
    validate_r11v_tinysa_preflight,
)
from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_serial_trace_collector import (
    MAX_TINYSA_SCANRAW_PREFIX_BYTES,
    TINYSA_PRODUCT_SCANRAW_POINTS_MAX,
    TinySaScanRawRequest,
    TinySaTraceCollectionCancelled,
    TinySaTraceCollectionError,
    collect_tinysa_scanraw_trace,
    parse_tinysa_zero_offset_response,
)
from sdr_monitor.services.tinysa_serial_version_probe import TinySaPnpObservation


def _frame(*codes: int) -> bytes:
    return b"{" + b"".join(b"x" + code.to_bytes(2, "little") for code in codes) + b"}"


def _zero_response(offset: int = 174) -> bytes:
    return f"zero ?\r\nusage: zero {{level}}\r\n{offset}dBm\r\nch> ".encode("ascii")


class _FakeSerial:
    def __init__(
        self,
        chunks: list[bytes],
        *,
        short_write: bool = False,
        close_failure: bool = False,
    ) -> None:
        self._chunks = list(chunks)
        self._short_write = short_write
        self._close_failure = close_failure
        self.is_open = False
        self.dtr = True
        self.rts = True
        self.calls: list[object] = []

    def open(self) -> None:
        self.calls.append("open")
        self.is_open = True

    def close(self) -> None:
        self.calls.append("close")
        if self._close_failure:
            raise OSError("fake close failure")
        self.is_open = False

    def reset_input_buffer(self) -> None:
        self.calls.append("reset_input_buffer")

    def write(self, data: bytes) -> int:
        self.calls.append(("write", data))
        return len(data) - 1 if self._short_write else len(data)

    def flush(self) -> None:
        self.calls.append("flush")

    def read(self, size: int = 1) -> bytes:
        self.calls.append(("read", size))
        return self._chunks.pop(0) if self._chunks else b""


class _Clock:
    def __init__(self, step_ns: int = 1_000_000) -> None:
        self.value = 0
        self.step_ns = step_ns

    def __call__(self) -> int:
        self.value += self.step_ns
        return self.value


@dataclass
class _PortInfo:
    device: str = "COM31"
    vid: int = 0x0483
    pid: int = 0x5740


class TinySaTraceCollectorTests(unittest.TestCase):
    def test_one_exact_non_continuous_command_parses_and_closes(self) -> None:
        request = TinySaScanRawRequest(
            model=TinySaModel.ULTRA,
            start_frequency_hz=87_500_000,
            stop_frequency_hz=108_000_000,
            points=3,
        )
        serial_port = _FakeSerial(
            [
                _zero_response(),
                b"scanraw 87500000 108000000 3 0\r\n",
                _frame(3200, 3232, 3264) + b"\r\nch> ",
            ]
        )

        collection = collect_tinysa_scanraw_trace(
            "COM31",
            request,
            serial_factory=lambda _port: serial_port,
        )

        self.assertEqual(
            [call for call in serial_port.calls if isinstance(call, tuple) and call[0] == "write"],
            [
                ("write", b"zero ?\r"),
                ("write", b"scanraw 87500000 108000000 3 0\r"),
            ],
        )
        self.assertEqual(serial_port.calls[-1], "close")
        self.assertTrue(collection.port_closed)
        self.assertEqual(collection.measurement_commands, 1)
        self.assertEqual(collection.device_scans_requested, 1)
        self.assertEqual(collection.retries, 0)
        self.assertEqual(collection.trace.values_dbm.tolist(), [-74.0, -73.0, -72.0])
        self.assertFalse(collection.trace.values_dbm.flags.writeable)
        self.assertFalse(serial_port.dtr)
        self.assertFalse(serial_port.rts)
        self.assertEqual(collection.scanraw_zero_offset_db, 174.0)
        self.assertEqual(collection.readback_commands, 1)

    def test_zero_query_parser_requires_one_bounded_prompted_offset(self) -> None:
        self.assertEqual(parse_tinysa_zero_offset_response(_zero_response(128)), 128.0)
        invalid = (
            b"174dBm\r\n",
            b"174dBm\r\n175dBm\r\nch> ",
            b"zero ?\r\nsecret\xff\r\n174dBm\r\nch> ",
            b"999dBm\r\nch> ",
        )
        for response in invalid:
            with self.subTest(response=response), self.assertRaises(TinySaTraceCollectionError):
                parse_tinysa_zero_offset_response(response)

    def test_zero_readback_controls_dbm_decode_and_failure_prevents_scan(self) -> None:
        request = TinySaScanRawRequest(
            model=TinySaModel.ULTRA,
            start_frequency_hz=100_000,
            stop_frequency_hz=200_000,
            points=2,
        )
        serial_port = _FakeSerial([_zero_response(128), _frame(3200, 3232)])
        collection = collect_tinysa_scanraw_trace(
            "COM31",
            request,
            serial_factory=lambda _port: serial_port,
        )
        self.assertEqual(collection.trace.values_dbm.tolist(), [-28.0, -27.0])
        self.assertEqual(collection.trace.scanraw_zero_offset_db, 128.0)

        invalid_port = _FakeSerial([b"zero ?\r\nno offset\r\nch> "])
        with self.assertRaises(TinySaTraceCollectionError):
            collect_tinysa_scanraw_trace(
                "COM31",
                request,
                serial_factory=lambda _port: invalid_port,
            )
        writes = [
            call
            for call in invalid_port.calls
            if isinstance(call, tuple) and call[0] == "write"
        ]
        self.assertEqual(writes, [("write", b"zero ?\r")])
        self.assertEqual(invalid_port.calls[-1], "close")

    def test_continuous_option_and_out_of_range_requests_fail_before_port(self) -> None:
        cases = (
            {"option": 2},
            {"start_frequency_hz": 99_999},
            {"stop_frequency_hz": 5_300_000_001},
            {"points": 1},
            {"points": TINYSA_PRODUCT_SCANRAW_POINTS_MAX + 1},
        )
        for change in cases:
            with self.subTest(change=change):
                values = {
                    "model": TinySaModel.ULTRA,
                    "start_frequency_hz": 100_000,
                    "stop_frequency_hz": 200_000,
                    "points": 2,
                }
                values.update(change)
                with self.assertRaises(ValueError):
                    TinySaScanRawRequest(**values)  # type: ignore[arg-type]

    def test_3100_points_are_admitted_without_using_the_screen_limit(self) -> None:
        request = TinySaScanRawRequest(
            model=TinySaModel.ULTRA,
            start_frequency_hz=87_500_000,
            stop_frequency_hz=108_000_000,
            points=3_100,
        )

        self.assertEqual(request.points, 3_100)
        self.assertEqual(request.expected_frame_bytes, 9_302)

        collection = collect_tinysa_scanraw_trace(
            "COM31",
            request,
            serial_factory=lambda _port: _FakeSerial(
                [_zero_response(), _frame(*(3200 for _ in range(3_100)))]
            ),
        )
        self.assertEqual(collection.trace.values_dbm.size, 3_100)
        self.assertEqual(float(collection.trace.frequencies_hz[0]), 87_500_000.0)
        self.assertEqual(float(collection.trace.frequencies_hz[-1]), 107_990_588.0)

    def test_10001_point_product_cap_has_a_30005_byte_frame(self) -> None:
        request = TinySaScanRawRequest(
            model=TinySaModel.ULTRA,
            start_frequency_hz=87_500_000,
            stop_frequency_hz=108_000_000,
            points=10_001,
            deadline_seconds=120.0,
        )

        self.assertEqual(request.points, 10_001)
        self.assertEqual(request.expected_frame_bytes, 30_005)

    def test_partial_frame_extra_binary_and_prefix_overflow_fail_closed(self) -> None:
        request = TinySaScanRawRequest(
            model=TinySaModel.BASIC,
            start_frequency_hz=100_000,
            stop_frequency_hz=200_000,
            points=2,
        )
        bad_responses = (
            [_zero_response(), b"{x\x00}"],
            [_zero_response(), _frame(1, 2) + b"{"],
            [_zero_response(), b"A" * (MAX_TINYSA_SCANRAW_PREFIX_BYTES + 1)],
        )
        for chunks in bad_responses:
            with self.subTest(chunks=chunks):
                serial_port = _FakeSerial(chunks)

                def serial_factory(_port: str, result: _FakeSerial = serial_port) -> _FakeSerial:
                    return result

                with self.assertRaises(TinySaTraceCollectionError):
                    collect_tinysa_scanraw_trace(
                        "COM31",
                        request,
                        serial_factory=serial_factory,
                        monotonic_ns=_Clock(),
                    )
                self.assertEqual(serial_port.calls[-1], "close")

    def test_cancellation_short_write_and_close_failure_are_visible(self) -> None:
        request = TinySaScanRawRequest(
            model=TinySaModel.BASIC,
            start_frequency_hz=100_000,
            stop_frequency_hz=200_000,
            points=2,
        )
        cancel_calls = 0

        def cancel() -> bool:
            nonlocal cancel_calls
            cancel_calls += 1
            return cancel_calls >= 2

        cancelled_port = _FakeSerial([_frame(1, 2)])
        with self.assertRaises(TinySaTraceCollectionCancelled):
            collect_tinysa_scanraw_trace(
                "COM31",
                request,
                serial_factory=lambda _port: cancelled_port,
                cancel_requested=cancel,
            )
        self.assertEqual(cancelled_port.calls[-1], "close")

        for serial_port in (
            _FakeSerial([], short_write=True),
            _FakeSerial([_zero_response(), _frame(1, 2)], close_failure=True),
        ):
            with self.subTest(port=serial_port):
                def serial_factory(_port: str, result: _FakeSerial = serial_port) -> _FakeSerial:
                    return result

                with self.assertRaises(TinySaTraceCollectionError):
                    collect_tinysa_scanraw_trace(
                        "COM31",
                        request,
                        serial_factory=serial_factory,
                    )

    def test_deadline_is_absolute_and_has_no_retry(self) -> None:
        request = TinySaScanRawRequest(
            model=TinySaModel.BASIC,
            start_frequency_hz=100_000,
            stop_frequency_hz=200_000,
            points=2,
            deadline_seconds=0.05,
        )
        serial_port = _FakeSerial([])
        with self.assertRaises(TinySaTraceCollectionError):
            collect_tinysa_scanraw_trace(
                "COM31",
                request,
                serial_factory=lambda _port: serial_port,
                monotonic_ns=_Clock(step_ns=20_000_000),
            )
        writes = [call for call in serial_port.calls if isinstance(call, tuple) and call[0] == "write"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(serial_port.calls[-1], "close")


class TinySaTraceEvidenceTests(unittest.TestCase):
    def _source_evidence(self, root: Path) -> Path:
        destination = root / "r11t.json"
        destination.write_text(
            json.dumps(
                {
                    "schema": "sdr-native-tinysa-version-evidence-v1",
                    "status": "OBSERVED",
                    "result": {
                        "model": "tinysa_ultra",
                        "firmware_version": "tinySA4_v1.4-200-g26fc821",
                    },
                    "hardware_actions": {
                        "version_commands": 1,
                        "measurement_commands": 0,
                        "retunes": 0,
                        "stream_starts": 0,
                        "firmware_writes": 0,
                        "resets": 0,
                        "retries": 0,
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return destination

    @patch("sdr_monitor.benchmarks.r11v_tinysa_trace_evidence.observe_tinysa_pnp")
    def test_preflight_is_fixed_route_free_and_source_bound(self, observe: object) -> None:
        observe.return_value = TinySaPnpObservation("COM31", 0x0483, 0x5740)  # type: ignore[attr-defined]
        with tempfile.TemporaryDirectory() as raw:
            source = self._source_evidence(Path(raw))
            preflight = build_r11v_tinysa_preflight("COM31", source)
            rendered = json.dumps(preflight, sort_keys=True).casefold()

            self.assertEqual(preflight["confirmation"], R11V_TINYSA_CONFIRMATION)
            self.assertFalse(R11V_TINYSA_PHYSICAL_EXECUTION_ENABLED)
            self.assertEqual(preflight["status"], "PHYSICAL_CELL_CONSUMED")
            self.assertFalse(preflight["physical_execution_enabled"])
            self.assertNotIn("com31", rendered)
            self.assertNotIn("values_dbm", rendered)
            validate_r11v_tinysa_preflight(preflight, "COM31", source)

            wrong_firmware = json.loads(source.read_text(encoding="utf-8"))
            wrong_firmware["result"]["firmware_version"] = "tinySA4_v1.4-201-gdifferent"
            source.write_text(json.dumps(wrong_firmware), encoding="utf-8")
            with self.assertRaises(ValueError):
                build_r11v_tinysa_preflight("COM31", source)

            source.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_r11v_tinysa_preflight(preflight, "COM31", source)

    def test_evidence_contains_scalars_without_trace_arrays_or_route(self) -> None:
        request = R11VTinySaProfile().request
        codes = tuple(3200 + index for index in range(request.points))
        serial_port = _FakeSerial([_zero_response(), _frame(*codes)])
        collection = collect_tinysa_scanraw_trace(
            "COM31",
            request,
            serial_factory=lambda _port: serial_port,
        )
        payload = build_r11v_tinysa_evidence(collection, preflight_sha256="a" * 64)
        rendered = json.dumps(payload, sort_keys=True).casefold()

        self.assertEqual(payload["schema"], R11V_TINYSA_EVIDENCE_SCHEMA)
        self.assertEqual(payload["status"], "OBSERVED_WITH_LIMITATIONS")
        self.assertNotIn("com31", rendered)
        self.assertNotIn("values_dbm", rendered)
        self.assertNotIn("frequencies_hz", rendered)
        self.assertEqual(payload["result"]["point_count"], 3_100)  # type: ignore[index]
        self.assertEqual(payload["hardware_actions"]["measurement_commands"], 1)  # type: ignore[index]
        self.assertEqual(payload["hardware_actions"]["readback_commands"], 1)  # type: ignore[index]
        self.assertEqual(payload["hardware_actions"]["retries"], 0)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
