"""Actual UI V2 root owns catalog cleanup; infrastructure is synthetic."""

from __future__ import annotations

import os
import unittest
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.services.live_session import InMemoryLiveSessionService
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.test_app06_retained_capability_catalog import _Provider


class CatalogShutdownTests(unittest.TestCase):
    def test_all_cleanup_runs_in_order_and_the_first_error_is_preserved(self):
        for failing in ("analyzer", "port", "catalog", None):
            with self.subTest(failing=failing):
                events = []
                expected = RuntimeError("first failure")
                def action(name, events=events, failing=failing, expected=expected):
                    def call(*args):
                        events.append(name)
                        if name == failing:
                            raise expected
                    return call
                analyzer = SimpleNamespace(stop=action("analyzer"))
                port = SimpleNamespace(stop_and_wait=action("port"))
                application = LiveSessionApplicationService(port, analyzer=analyzer, catalog_close=action("catalog"))
                if failing is None:
                    application.shutdown()
                else:
                    with self.assertRaises(RuntimeError) as error:
                        application.shutdown()
                    self.assertIs(error.exception, expected)
                self.assertEqual(events, ["analyzer", "port", "catalog"])

    def test_failed_catalog_release_is_retried_by_explicit_shutdown(self):
        port = Mock()
        close = Mock(side_effect=(RuntimeError("failed close"), None))
        application = LiveSessionApplicationService(port, catalog_close=close)
        with self.assertRaisesRegex(RuntimeError, "failed close"):
            application.shutdown(.5)
        application.shutdown(.5)
        self.assertEqual(port.stop_and_wait.call_count, 2)
        self.assertEqual(close.call_count, 2)


class CatalogV2CompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_v2_root_wires_same_retained_catalog_to_lifecycle_worker(self):
        from contextlib import nullcontext
        provider = _Provider()
        catalog = SourceCapabilityCatalog((provider,), control_transaction=nullcontext)
        live = Mock(wraps=InMemoryLiveSessionService())
        services = SimpleNamespace(live_sdr=live, sweep=Mock(), calibration=Mock(),
                                   diagnostics=Mock(), replay=Mock(), device_catalog=catalog)
        shell = build_v2_shell(services)
        composition = shell._context.close_ports[0].shutdown.__self__
        try:
            self.assertEqual(provider.calls, [])
            shell.close()
            deadline = monotonic() + 4
            while not shell._is_closed and monotonic() < deadline:
                self.app.processEvents()
                sleep(.001)
            self.assertTrue(shell._is_closed)
            self.assertEqual(provider.calls, ["close"])
            live.stop_and_wait.assert_called_once()
            composition.shutdown()
            self.assertEqual(provider.calls, ["close"])
        finally:
            composition.shutdown()
            shell.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
