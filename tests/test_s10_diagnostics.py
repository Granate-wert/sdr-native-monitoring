"""S10 diagnostics and support privacy tests."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from sdr_monitor.domain import DiagnosticStatus, SupportBundleOptions
from sdr_monitor.services.diagnostics_session import DiagnosticsService


class S10DiagnosticsTests(unittest.TestCase):
    def test_self_tests_are_safe_and_cuda_is_explicitly_unavailable(self) -> None:
        service = DiagnosticsService()
        results = service.run_self_tests()
        self.assertEqual({item.name for item in results}, {"environment", "cpu", "cuda", "pluto"})
        cuda = next(item for item in results if item.name == "cuda")
        self.assertEqual(cuda.status, DiagnosticStatus.UNAVAILABLE)
        service.shutdown()

    def test_cancellation_and_rx_confirmation(self) -> None:
        service = DiagnosticsService()
        cancel = threading.Event()
        cancel.set()
        results = service.run_self_tests(cancel)
        self.assertEqual(results[0].status, DiagnosticStatus.CANCELLED)
        with self.assertRaises(PermissionError):
            service.run_controlled_rx_test(False)
        self.assertEqual(service.run_controlled_rx_test(True).status, DiagnosticStatus.UNAVAILABLE)
        service.shutdown()

    def test_error_center_and_bounded_log(self) -> None:
        service = DiagnosticsService(log_capacity=2)
        for index in range(5):
            service.report_error(f"error {index}", "reason", "recommendation", f"C:\\private\\IQ{index}.bin")
        self.assertEqual(len(service.errors()), 2)
        self.assertEqual(len(service._log.items()), 2)
        service.shutdown()

    def test_support_bundle_redacts_paths_and_raw_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = DiagnosticsService()
            service.report_error("Failure", "path leaked", "remove path", "C:\\Users\\posta\\secret\\calibration.json")
            result = service.export_support_bundle(Path(temporary))
            self.assertTrue(result.redacted)
            path = Path(result.path)
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("C:\\\\Users\\posta", text)
            self.assertNotIn("calibration.json", text)
            self.assertEqual(payload["schema"], "sdr-support-bundle")
            with self.assertRaises(ValueError):
                service.export_support_bundle(Path(temporary) / "raw", SupportBundleOptions(include_raw_data=True))
            service.shutdown()


if __name__ == "__main__":
    unittest.main()
