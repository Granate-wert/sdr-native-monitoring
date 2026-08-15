"""Static R11-E concrete-port allowlist tests; no DLL or device access."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sdr_monitor" / "services" / "libhackrf_read_only.py"


class R11ELibhackrfReadOnlySourceTests(unittest.TestCase):
    def test_only_declared_libhackrf_symbols_are_referenced(self) -> None:
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        symbols = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr.startswith("hackrf_")
        }
        self.assertEqual(
            symbols,
            {
                "hackrf_init",
                "hackrf_exit",
                "hackrf_device_list",
                "hackrf_device_list_open",
                "hackrf_device_list_free",
                "hackrf_close",
                "hackrf_board_id_read",
                "hackrf_board_partid_serialno_read",
                "hackrf_version_string_read",
                "hackrf_usb_api_version_read",
            },
        )

    def test_source_contains_no_stream_control_or_firmware_symbol(self) -> None:
        text = SOURCE.read_text(encoding="utf-8")
        for symbol in (
            "hackrf_start_rx",
            "hackrf_start_tx",
            "hackrf_start_rx_sweep",
            "hackrf_set_freq",
            "hackrf_set_sample_rate",
            "hackrf_set_lna_gain",
            "hackrf_set_vga_gain",
            "hackrf_spiflash",
            "hackrf_cpld_write",
        ):
            self.assertNotIn(symbol, text)


if __name__ == "__main__":
    unittest.main()
