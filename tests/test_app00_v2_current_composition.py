"""APP-00: real V2/presenters/use cases, with only infrastructure replaced."""

from __future__ import annotations

import os
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.application import (
    CalibrationControlApplicationService,
    DiagnosticsControlApplicationService,
    LiveSessionApplicationService,
    ReplayControlApplicationService,
    SweepControlApplicationService,
)
from sdr_monitor.main import _requested_ui_mode, _resolve_ui_mode, main
from sdr_monitor.services.live_session import InMemoryLiveSessionService
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2_composition import build_v2_shell


class CurrentV2CompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_default_and_explicit_rollback_selection(self):
        self.assertEqual(_resolve_ui_mode(_requested_ui_mode({})), "v2")
        self.assertEqual(_resolve_ui_mode("unknown"), "v2")
        self.assertEqual(_resolve_ui_mode("standalone"), "standalone")
        self.assertEqual(_requested_ui_mode({"SDR_UI_VERSION": "v2", "SDR_UI_MODE": "standalone"}), "v2")

    def test_real_presenters_receive_current_use_cases_and_navigation_is_inert(self):
        live = Mock(wraps=InMemoryLiveSessionService())
        services = SimpleNamespace(
            live_sdr=live, sweep=Mock(), calibration=Mock(),
            diagnostics=Mock(), replay=Mock(),
        )
        compositions = []

        def capture(*args, **kwargs):
            composition = compose_v2_live_product(*args, **kwargs)
            compositions.append((composition, args, kwargs))
            return composition

        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture), \
             patch("sdr_monitor.services.build_default_sdr_services", return_value=services) as factory:
            shell = build_v2_shell()
        composition, args, kwargs = compositions[0]
        deferred = []
        try:
            factory.assert_called_once_with()
            self.assertIsInstance(args[0]._use_cases, LiveSessionApplicationService)
            self.assertIs(args[0]._use_cases._port, live)
            self.assertIsInstance(kwargs["sweep_presenter"]._use_cases, SweepControlApplicationService)
            self.assertIsInstance(kwargs["calibration_presenter"]._use_cases, CalibrationControlApplicationService)
            self.assertEqual(shell.active_workspace_id, "analyzer")
            for route in ("analyzer", "calibration", "diagnostics", "replay", "analyzer"):
                shell.select_workspace(route)
                self.app.processEvents()
            for name in ("discover_devices", "discover_startup_devices", "select_device", "start", "apply_configuration"):
                getattr(live, name).assert_not_called()
            services.sweep.assert_not_called()
            self.assertEqual(services.sweep.method_calls, [])
            self.assertEqual(services.calibration.method_calls, [])
            self.assertEqual(services.diagnostics.method_calls, [])
            self.assertEqual(services.replay.method_calls, [])
            # Exercise actual deferred constructors as well, not fake presenters.
            for key, expected, port in (
                ("diagnostics_presenter_factory", DiagnosticsControlApplicationService, services.diagnostics),
                ("replay_presenter_factory", ReplayControlApplicationService, services.replay),
            ):
                presenter = kwargs[key]()
                deferred.append(presenter)
                self.assertIsInstance(presenter._use_cases, expected)
                self.assertIs(presenter._use_cases._port, port)
        finally:
            for presenter in deferred:
                presenter.shutdown()
            shell.close()
            deadline = monotonic() + 3
            while not shell._is_closed and monotonic() < deadline:
                self.app.processEvents()
                sleep(.001)
            self.assertTrue(shell._is_closed)
            composition.shutdown()
            shell.deleteLater()
            self.app.processEvents()
        live.stop_and_wait.assert_called_once()
        services.sweep.close.assert_called_once()

    def test_actual_entry_selects_v2_and_logs_only_successful_construction(self):
        for environment in ({}, {"SDR_UI_MODE": "unknown"},
                            {"SDR_UI_VERSION": "v2", "SDR_UI_MODE": "standalone"}):
            with self.subTest(environment=environment), \
                 patch.dict(os.environ, environment, clear=True), \
                 patch("sdr_monitor.main.install_activity_file_logging", return_value=Mock()), \
                 patch("sdr_monitor.main._install_excepthook"), \
                 patch("sdr_monitor.main.log_event") as events, \
                 patch("sdr_monitor.main._build_v2_shell", return_value=Mock()) as build, \
                 patch.object(QApplication, "exec", return_value=0):
                self.assertEqual(main([]), 0)
                build.assert_called_once_with()
                started = [call for call in events.call_args_list if call.args[2] == "application_started"]
                self.assertEqual(len(started), 1)
                self.assertEqual(started[0].kwargs["mode"], "v2")

        with patch.dict(os.environ, {}, clear=True), \
             patch("sdr_monitor.main.install_activity_file_logging", return_value=Mock()), \
             patch("sdr_monitor.main._install_excepthook"), \
             patch("sdr_monitor.main.log_event") as events, \
             patch("sdr_monitor.main._build_v2_shell", side_effect=RuntimeError("construction failed")):
            with self.assertRaisesRegex(RuntimeError, "construction failed"):
                main([])
            self.assertFalse(any(call.args[2] == "application_started" for call in events.call_args_list))


if __name__ == "__main__":
    unittest.main()
