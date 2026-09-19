"""Actual V2 deferred commands traverse current application ports, never hardware."""

import os
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain import DiagnosticsSnapshot, ReplayKind
from sdr_monitor.services.live_session import InMemoryLiveSessionService
from sdr_monitor.services.sweep_session import InMemorySweepService
from sdr_monitor.services.tinysa_serial_source_backend import TinySaSerialSourceBackend
from sdr_monitor.ui.v2_composition import build_v2_shell


class DeferredCurrentCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_idle(self, predicate):
        deadline = monotonic() + 3
        while not predicate() and monotonic() < deadline:
            self.app.processEvents()
            sleep(0.005)
        self.assertTrue(predicate(), "deferred current presenter did not finish")

    def test_explicit_commands_create_current_presenters_and_cleanup_once(self):
        diagnostics, replay = Mock(), Mock()
        diagnostics.collect_snapshot.return_value = DiagnosticsSnapshot({}, (), ())
        replay.open.side_effect = RuntimeError("offline replay failure")
        services = SimpleNamespace(
            live_sdr=InMemoryLiveSessionService(), sweep=InMemorySweepService(),
            calibration=Mock(), diagnostics=diagnostics, replay=replay,
        )
        serial_backend = Mock(spec=TinySaSerialSourceBackend)
        serial_backend.discover_endpoints.return_value = ()
        with patch("sdr_monitor.services.tinysa_serial_source_backend.TinySaSerialSourceBackend",
                   return_value=serial_backend) as serial_factory:
            shell = build_v2_shell(services)
            composition = shell._context.close_ports[0].shutdown.__self__
            diag_vm = composition.diagnostics_view_model
            replay_vm = composition.replay_view_model
            tiny_vm = composition._tinysa.activation_view_model
            try:
                for route in ("diagnostics", "replay", "tinysa", "analyzer"):
                    self.assertIn(route, shell._definitions)
                    shell.select_workspace(route)
                self.assertIsNone(diag_vm._presenter)
                self.assertIsNone(replay_vm._presenter)
                self.assertIsNone(tiny_vm._presenter)
                serial_factory.assert_not_called()
                self.assertFalse(replay_vm.open_spectrum_recording(""))
                self.assertTrue(diag_vm.load())
                self.assertTrue(replay_vm.open_spectrum_recording("offline-test.sdrrec"))
                self.assertTrue(tiny_vm.discover())
                self.wait_idle(lambda: not diag_vm.state.busy and not replay_vm.state.busy
                               and tiny_vm.state.can_close)
                self.assertEqual(diagnostics.collect_snapshot.call_count, 2)  # constructor + explicit refresh
                replay.open.assert_called_once_with(Path("offline-test.sdrrec"), kind=ReplayKind.SPECTRUM)
                serial_factory.assert_called_once_with()
                serial_backend.discover_endpoints.assert_called_once_with()
                self.assertIsNotNone(diag_vm._presenter)
                self.assertIsNotNone(replay_vm._presenter)
                self.assertIsNotNone(tiny_vm._presenter)
            finally:
                shell.close()
                self.wait_idle(lambda: shell._is_closed)
                composition.shutdown()
                composition.shutdown()
                shell.deleteLater()
                self.app.processEvents()
        diagnostics.shutdown.assert_called_once_with()
        replay.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
