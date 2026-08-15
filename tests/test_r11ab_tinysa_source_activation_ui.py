"""R11-AB application/presenter/dialog flow with a fake tinySA backend."""

from __future__ import annotations

import os
import time
import unittest
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDR_AUTO_DISCOVER", "0")

from PySide6.QtWidgets import QApplication, QPushButton

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
    TinySaSourcePhase,
    TinySaTransportEndpoint,
    endpoint_identity_key,
)
from sdr_monitor.services.tinysa_sweep_settings_controller import (
    TinySaSettingsApplyResult,
    TinySaSweepSettingsPlan,
)
from sdr_monitor.ui.dialogs.tinysa_source_activation import (
    TinySaSourceActivationDialog,
)
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
    raise AssertionError("timed out waiting for fake tinySA activation")


class _Collector:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def collect(self, request: TinySaScanRawRequest) -> TinySaTraceCollection:
        del request
        self.calls.append("collect")
        raise AssertionError("activation flow must not acquire a trace")


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
        raise AssertionError("activation flow must not apply settings")


class _Backend:
    def __init__(self, *, fail_probe: bool = False) -> None:
        self.fail_probe = fail_probe
        self.calls: list[str] = []
        self.endpoint = TinySaTransportEndpoint(
            "COM31",
            endpoint_identity_key("r11ab-source"),
            "tinySA USB CDC 1234abcd",
            TinySaIdentityAssurance.USB_SERIAL,
        )

    def discover_endpoints(self) -> tuple[TinySaTransportEndpoint, ...]:
        self.calls.append("discover")
        return (self.endpoint,)

    def probe_version(self, endpoint: TinySaTransportEndpoint) -> TinySaVersionObservation:
        self._same(endpoint)
        self.calls.append("probe")
        if self.fail_probe:
            raise RuntimeError("secret COM31 failure")
        return TinySaVersionObservation(
            TinySaModel.ULTRA,
            "tinySA4_v1.4-test",
            "sha256:" + "f" * 64,
        )

    def make_collector(
        self,
        endpoint: TinySaTransportEndpoint,
        model: TinySaModel,
    ) -> _Collector:
        self._same(endpoint)
        if model is not TinySaModel.ULTRA:
            raise AssertionError("wrong model")
        self.calls.append("make_collector")
        return _Collector(self.calls)

    def make_settings_executor(self, endpoint: TinySaTransportEndpoint) -> _Settings:
        self._same(endpoint)
        self.calls.append("make_settings")
        return _Settings(self.calls)

    def _same(self, endpoint: TinySaTransportEndpoint) -> None:
        if endpoint.identity_key != self.endpoint.identity_key:
            raise AssertionError("source mismatch")


def _use_cases(backend: _Backend) -> TinySaSourceActivationApplicationService:
    composition = TinySaSourceCompositionService(backend)
    return TinySaSourceActivationApplicationService(composition)


class TinySaSourceActivationApplicationTests(unittest.TestCase):
    def test_constructor_is_inert_and_four_transitions_remain_distinct(self) -> None:
        backend = _Backend()
        use_cases = _use_cases(backend)
        self.assertEqual(backend.calls, [])

        discovered = use_cases.discover()
        self.assertIs(discovered.phase, TinySaSourcePhase.DISCOVERED)
        self.assertEqual(backend.calls, ["discover"])
        selected = use_cases.select(discovered.candidates[0].source_id)
        self.assertIs(selected.phase, TinySaSourcePhase.SELECTED)
        self.assertEqual(backend.calls, ["discover"])
        use_cases.verify_selected()
        self.assertEqual(backend.calls, ["discover", "probe"])
        use_cases.compose_selected()
        self.assertEqual(
            backend.calls,
            ["discover", "probe", "make_collector", "make_settings"],
        )


class TinySaSourceActivationUiTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.backend = _Backend()
        self.presenter = TinySaSourceActivationPresenter(_use_cases(self.backend))
        self.dialog = TinySaSourceActivationDialog(self.presenter)

    def tearDown(self) -> None:
        self.dialog.close()
        self.dialog.deleteLater()
        self.presenter.shutdown()
        self.presenter.deleteLater()

    def _complete_flow(self) -> list[object]:
        registrations: list[object] = []
        self.dialog.registration_ready.connect(registrations.append)
        self.dialog._discover.click()
        _wait_for(lambda: self.dialog._candidates.count() == 1)
        self.dialog._select.click()
        _wait_for(lambda: self.dialog._verify.isEnabled())
        self.dialog._verify.click()
        _wait_for(lambda: self.dialog._compose.isEnabled())
        self.dialog._compose.click()
        _wait_for(lambda: len(registrations) == 1)
        return registrations

    def test_open_is_inert_and_accessible_four_step_flow_composes_without_scan(self) -> None:
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(self.dialog._empty.isVisibleTo(self.dialog))
        for button in self.dialog.findChildren(QPushButton):
            self.assertTrue(button.accessibleDescription(), button.text())

        registrations = self._complete_flow()

        self.assertEqual(len(registrations), 1)
        self.assertEqual(
            self.backend.calls,
            ["discover", "probe", "make_collector", "make_settings"],
        )
        self.assertNotIn("collect", self.backend.calls)
        self.assertNotIn("settings", self.backend.calls)
        self.assertNotIn("com31", self.dialog._identity.text().casefold())

    def test_probe_failure_is_redacted_and_requires_fresh_discovery(self) -> None:
        self.dialog.close()
        self.presenter.shutdown()
        self.backend = _Backend(fail_probe=True)
        self.presenter = TinySaSourceActivationPresenter(_use_cases(self.backend))
        self.dialog = TinySaSourceActivationDialog(self.presenter)

        self.dialog._discover.click()
        _wait_for(lambda: self.dialog._candidates.count() == 1)
        self.dialog._select.click()
        _wait_for(lambda: self.dialog._verify.isEnabled())
        self.dialog._verify.click()
        _wait_for(lambda: bool(self.dialog._error.text()))

        error = self.dialog._error.text().casefold()
        self.assertNotIn("secret", error)
        self.assertNotIn("com31", error)
        self.assertTrue(self.dialog._discover.isEnabled())
        self.assertFalse(self.dialog._verify.isEnabled())


if __name__ == "__main__":
    unittest.main()
