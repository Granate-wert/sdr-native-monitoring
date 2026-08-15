"""Offscreen normal AppShell tinySA hand-off with fake-only source ports."""

from __future__ import annotations

import os
import time
import unittest
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDR_AUTO_DISCOVER", "0")

from PySide6.QtWidgets import QApplication

from sdr_monitor.application.tinysa_source_activation import (
    TinySaSourceActivationApplicationService,
)
from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollection,
)
from sdr_monitor.services.tinysa_serial_version_probe import TinySaVersionObservation
from sdr_monitor.services.tinysa_source_composition import (
    TinySaIdentityAssurance,
    TinySaSourceCompositionService,
    TinySaTransportEndpoint,
    endpoint_identity_key,
)
from sdr_monitor.services.tinysa_sweep_settings_controller import (
    TinySaSettingsApplyResult,
    TinySaSweepSettingsPlan,
)
from sdr_monitor.ui.app_shell import OptionalWorkspaceId, SDRAppShell, WorkspaceId
from sdr_monitor.ui.dialogs.tinysa_source_activation import TinySaSourceActivationDialog
from sdr_monitor.ui.presenters.tinysa_source_activation_presenter import (
    TinySaSourceActivationPresenter,
)


def _app() -> QApplication:
    existing = QApplication.instance()
    return cast(QApplication, existing) if existing is not None else QApplication([])


def _wait_for(predicate: object, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _app().processEvents()
        if callable(predicate) and predicate():
            return
        time.sleep(0.002)
    raise AssertionError("timed out waiting for fake tinySA AppShell hand-off")


class _Collector:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def collect(self, request: TinySaScanRawRequest) -> TinySaTraceCollection:
        del request
        self.calls.append("collect")
        raise AssertionError("activation must not collect a tinySA trace")


class _Settings:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def apply(
        self,
        plan: TinySaSweepSettingsPlan,
        *,
        confirmation: str,
    ) -> TinySaSettingsApplyResult:
        del plan, confirmation
        self.calls.append("settings")
        raise AssertionError("activation must not apply tinySA settings")


class _Backend:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.endpoint = TinySaTransportEndpoint(
            "COM31",
            endpoint_identity_key("tinysa-appshell-test"),
            "tinySA USB CDC 1234abcd",
            TinySaIdentityAssurance.USB_SERIAL,
        )

    def discover_endpoints(self) -> tuple[TinySaTransportEndpoint, ...]:
        self.calls.append("discover")
        return (self.endpoint,)

    def probe_version(self, endpoint: TinySaTransportEndpoint) -> TinySaVersionObservation:
        self._same(endpoint)
        self.calls.append("probe")
        return TinySaVersionObservation(
            TinySaModel.ULTRA,
            "tinySA4_v1.4-test",
            "sha256:" + "a" * 64,
        )

    def make_collector(
        self,
        endpoint: TinySaTransportEndpoint,
        model: TinySaModel,
    ) -> _Collector:
        self._same(endpoint)
        if model is not TinySaModel.ULTRA:
            raise AssertionError("source model was not retained")
        self.calls.append("make_collector")
        return _Collector(self.calls)

    def make_settings_executor(self, endpoint: TinySaTransportEndpoint) -> _Settings:
        self._same(endpoint)
        self.calls.append("make_settings")
        return _Settings(self.calls)

    def _same(self, endpoint: TinySaTransportEndpoint) -> None:
        if endpoint.identity_key != self.endpoint.identity_key:
            raise AssertionError("source identity changed")


class _PresenterFactory:
    def __init__(self, backend: _Backend) -> None:
        self.backend = backend
        self.calls = 0
        self.presenters: list[TinySaSourceActivationPresenter] = []

    def __call__(self) -> TinySaSourceActivationPresenter:
        self.calls += 1
        presenter = TinySaSourceActivationPresenter(
            TinySaSourceActivationApplicationService(
                TinySaSourceCompositionService(self.backend)
            )
        )
        self.presenters.append(presenter)
        return presenter


class TinySaAppShellIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.backend = _Backend()
        self.factory = _PresenterFactory(self.backend)
        self.shell = SDRAppShell(tinysa_activation_presenter_factory=self.factory)

    def tearDown(self) -> None:
        self.shell.close()
        self.shell.deleteLater()
        for presenter in self.factory.presenters:
            presenter.shutdown()
            presenter.deleteLater()

    def test_normal_entry_is_lazy_and_only_composition_registers_the_workspace(self) -> None:
        self.shell.show()
        _app().processEvents()
        self.assertEqual(self.factory.calls, 0)
        self.assertEqual(self.backend.calls, [])
        self.assertFalse(self.shell.tinysa_analyzer_registered)
        self.assertEqual(self.shell.active_workspace, WorkspaceId.HOME)

        self.shell._tinysa_entry.click()
        _app().processEvents()
        dialog = cast(TinySaSourceActivationDialog, self.shell._tinysa_activation_dialog)
        self.assertIsInstance(dialog, TinySaSourceActivationDialog)
        self.assertEqual(self.factory.calls, 1)
        self.assertEqual(self.backend.calls, [])

        dialog._discover.click()
        _wait_for(lambda: dialog._candidates.count() == 1)
        dialog._select.click()
        _wait_for(lambda: dialog._verify.isEnabled())
        dialog._verify.click()
        _wait_for(lambda: dialog._compose.isEnabled())
        dialog._compose.click()
        _wait_for(lambda: self.shell.tinysa_analyzer_registered)

        self.assertEqual(
            self.backend.calls,
            ["discover", "probe", "make_collector", "make_settings"],
        )
        self.assertNotIn("collect", self.backend.calls)
        self.assertNotIn("settings", self.backend.calls)
        self.assertEqual(self.shell.active_workspace, OptionalWorkspaceId.TINYSA_ANALYZER)

        self.shell.set_active_workspace(WorkspaceId.HOME)
        self.shell._tinysa_entry.click()
        self.assertEqual(self.shell.active_workspace, OptionalWorkspaceId.TINYSA_ANALYZER)
        self.assertEqual(self.factory.calls, 1)

    def test_factory_failure_is_redacted_and_creates_no_dialog(self) -> None:
        self.shell.close()
        self.shell.deleteLater()

        def fail() -> TinySaSourceActivationPresenter:
            raise RuntimeError("secret COM route")

        self.shell = SDRAppShell(tinysa_activation_presenter_factory=fail)
        self.shell._tinysa_entry.click()

        self.assertIsNone(self.shell._tinysa_activation_dialog)
        message = self.shell.statusBar().currentMessage().casefold()
        self.assertIn("unavailable", message)
        self.assertNotIn("secret", message)
        self.assertNotIn("com", message)


if __name__ == "__main__":
    unittest.main()
