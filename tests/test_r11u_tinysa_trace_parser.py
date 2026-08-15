"""Software/fake-only contract tests for the bounded tinySA scanraw parser."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import numpy as np

from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_trace_parser import (
    MAX_TINYSA_TRACE_PAYLOAD_BYTES,
    MAX_TINYSA_TRACE_POINTS,
    TINYSA_TRACE_CALIBRATION_PROVENANCE,
    TINYSA_TRACE_VALUE_PROVENANCE,
    TinySaSpectrumTrace,
    TinySaTraceParseError,
    parse_tinysa_scanraw_trace,
)


def _frame(*codes: int) -> bytes:
    return b"{" + b"".join(b"x" + value.to_bytes(2, "little") for value in codes) + b"}"


class TinySaTraceParserTests(unittest.TestCase):
    def test_product_cap_is_10001_points_inside_30_kib(self) -> None:
        self.assertEqual(MAX_TINYSA_TRACE_POINTS, 10_001)
        self.assertEqual(MAX_TINYSA_TRACE_PAYLOAD_BYTES, 30 * 1_024)
        payload = b"{" + b"x\x00\x00" * MAX_TINYSA_TRACE_POINTS + b"}"

        trace = parse_tinysa_scanraw_trace(
            payload,
            model=TinySaModel.ULTRA,
            start_frequency_hz=87_500_000.0,
            stop_frequency_hz=108_000_000.0,
            host_timestamp_ns=1,
            scanraw_zero_offset_db=174.0,
        )

        self.assertEqual(trace.values_dbm.size, 10_001)
        self.assertEqual(float(trace.frequencies_hz[-1]), 107_990_000.0)

    def test_ultra_records_decode_to_device_reported_dbm_with_firmware_axis(self) -> None:
        trace = parse_tinysa_scanraw_trace(
            _frame(0, 32, 5_568),
            model=TinySaModel.ULTRA,
            start_frequency_hz=100_000.0,
            stop_frequency_hz=100_200.0,
            host_timestamp_ns=123_456,
        )

        np.testing.assert_array_equal(trace.frequencies_hz, [100_000.0, 100_066.0, 100_132.0])
        np.testing.assert_allclose(trace.values_dbm, [-174.0, -173.0, 0.0], rtol=0.0, atol=1e-6)
        self.assertEqual(trace.unit, "dBm")
        self.assertEqual(trace.value_provenance, TINYSA_TRACE_VALUE_PROVENANCE)
        self.assertEqual(trace.calibration_provenance, TINYSA_TRACE_CALIBRATION_PROVENANCE)
        self.assertFalse(trace.raw_iq_available)
        self.assertFalse(trace.dbfs_conversion_available)
        self.assertFalse(trace.hardware_timestamp_available)
        self.assertFalse(trace.external_correction_applied)
        self.assertEqual(trace.scanraw_zero_offset_db, 174.0)
        self.assertFalse(trace.frequencies_hz.flags.writeable)
        self.assertFalse(trace.values_dbm.flags.writeable)

    def test_basic_and_ultra_use_distinct_vendor_offsets(self) -> None:
        payload = _frame(4_096)
        basic = parse_tinysa_scanraw_trace(
            payload,
            model=TinySaModel.BASIC,
            start_frequency_hz=1_000_000.0,
            stop_frequency_hz=1_000_000.0,
            host_timestamp_ns=1,
        )
        ultra = parse_tinysa_scanraw_trace(
            payload,
            model=TinySaModel.ULTRA,
            start_frequency_hz=1_000_000.0,
            stop_frequency_hz=1_000_000.0,
            host_timestamp_ns=1,
        )

        self.assertEqual(float(basic.values_dbm[0]), 0.0)
        self.assertEqual(float(ultra.values_dbm[0]), -46.0)

    def test_frozen_trace_owns_arrays_instead_of_retaining_mutable_aliases(self) -> None:
        frequencies = np.array([1.0, 3.0], dtype=np.float64)
        values = np.array([-80.0, -70.0], dtype=np.float32)
        trace = TinySaSpectrumTrace(
            model=TinySaModel.ULTRA,
            start_frequency_hz=1.0,
            stop_frequency_hz=5.0,
            host_timestamp_ns=1,
            scanraw_zero_offset_db=174.0,
            frequencies_hz=frequencies,
            values_dbm=values,
        )

        frequencies[:] = 99.0
        values[:] = 99.0
        np.testing.assert_array_equal(trace.frequencies_hz, [1.0, 3.0])
        np.testing.assert_array_equal(trace.values_dbm, [-80.0, -70.0])

    def test_rejects_prompt_echo_partial_frame_and_invalid_marker(self) -> None:
        invalid = (
            b"scanraw 1 2 1\r\n" + _frame(1),
            _frame(1) + b"ch> ",
            b"{x\x00",
            b"{y\x00}",
        )
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(TinySaTraceParseError):
                parse_tinysa_scanraw_trace(
                    payload,
                    model=TinySaModel.ULTRA,
                    start_frequency_hz=1.0,
                    stop_frequency_hz=1.0,
                    host_timestamp_ns=1,
                )

    def test_rejects_unbounded_points_bytes_and_time_metadata(self) -> None:
        oversized = b"{" + b"x\x00\x00" * (MAX_TINYSA_TRACE_POINTS + 1) + b"}"
        cases = (
            (oversized, 1.0, 1.0, 1),
            (_frame(1), 2.0, 1.0, 1),
            (_frame(1, 2), 1.0, 2.0, 1),
            (_frame(1), 1.0, 1.0, -1),
        )
        for payload, start, stop, timestamp in cases:
            with self.subTest(start=start, stop=stop, timestamp=timestamp), self.assertRaises(
                TinySaTraceParseError
            ):
                parse_tinysa_scanraw_trace(
                    payload,
                    model=TinySaModel.BASIC,
                    start_frequency_hz=start,
                    stop_frequency_hz=stop,
                    host_timestamp_ns=timestamp,
                )

    def test_does_not_import_or_open_serial_transport(self) -> None:
        tree = ast.parse(
            Path("sdr_monitor/services/tinysa_trace_parser.py").read_text(encoding="utf-8")
        )
        imports = [
            name.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for name in node.names
        ]
        self.assertNotIn("serial", imports)
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"open", "write", "flush"}
                for node in ast.walk(tree)
            )
        )


if __name__ == "__main__":
    unittest.main()
