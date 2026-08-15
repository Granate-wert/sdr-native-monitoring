"""R11-S offscreen explicit AppShell hand-off tests; no SDR/runtime action."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SDR_AUTO_DISCOVER", "0")

from PySide6.QtWidgets import QApplication

from sdr_monitor.application import (
    HackrfActivationApplicationSnapshot,
    HackrfActivationApplicationState,
)
from sdr_monitor.services import HackrfLiveActivationPlan, HackrfLiveRequest
from sdr_monitor.ui import HackrfActivationWorkspaceRegistration
from sdr_monitor.ui.app_shell import OptionalWorkspaceId, SDRAppShell, WorkspaceId
from sdr_monitor.ui.presenters import HackrfActivationPresenter
from sdr_monitor.ui.workspaces import HackrfActivationWorkspace


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION_SOURCE = ROOT / "sdr_monitor/ui/hackrf_activation_registration.py"


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _ready_snapshot() -> HackrfActivationApplicationSnapshot:
    return HackrfActivationApplicationSnapshot(
        HackrfActivationApplicationState.READY_FOR_PREFLIGHT,
        can_request_preflight=True,
        can_confirm_start=False,
        active=False,
    )


class _UseCases:
    def __init__(self) -> None:
        self.snapshot = _ready_snapshot()
        self.preflight_calls: list[object] = []

    def current(self) -> HackrfActivationApplicationSnapshot:
        return self.snapshot

    def request_preflight(self, plan: object) -> HackrfActivationApplicationSnapshot:
        self.preflight_calls.append(plan)
        self.snapshot = HackrfActivationApplicationSnapshot(
            HackrfActivationApplicationState.AWAITING_CONFIRMATION,
            can_request_preflight=False,
            can_confirm_start=True,
            active=False,
        )
        return self.snapshot

    def confirm_start(self, *, user_confirmed: bool) -> HackrfActivationApplicationSnapshot:
        del user_confirmed
        raise AssertionError("R11-S registration must not confirm start")

    def stop(self, timeout_ms: int) -> HackrfActivationApplicationSnapshot:
        del timeout_ms
        raise AssertionError("R11-S registration must not stop a receiver")

    def poll_spectrum_frames(self, max_items: int = 0) -> tuple[object, ...]:
        del max_items
        raise AssertionError("R11-S registration must not poll a receiver")

    def metrics(self) -> object | None:
        raise AssertionError("R11-S registration must not read native metrics")


def _issued_plan() -> HackrfLiveActivationPlan:
    request = HackrfLiveRequest(
        center_frequency_hz=100e6,
        sample_rate_hz=10e6,
        baseband_filter_hz=8_000_000,
        lna_gain_db=16,
        vga_gain_db=20,
    )
    return HackrfLiveActivationPlan._issue(
        device_id="opaque-hackrf",
        identity_key="opaque-identity",
        adapter_id="native.libhackrf.v1",
        request=request,
    )


def _wait_for(predicate: object, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _app().processEvents()
        if callable(predicate) and predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for explicit fake preflight")


class R11SHackrfAppShellRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.use_cases = _UseCases()
        self.presenter = HackrfActivationPresenter(self.use_cases)  # type: ignore[arg-type]
        self.shell = SDRAppShell()

    def tearDown(self) -> None:
        self.shell.close()
        self.shell.deleteLater()
        self.presenter.shutdown()
        self.presenter.deleteLater()

    def test_default_shell_has_no_optional_hackrf_entry_or_activation_action(self) -> None:
        self.assertFalse(self.shell.hackrf_activation_registered)
        self.assertNotIn(OptionalWorkspaceId.HACKRF_ACTIVATION, self.shell._nav_buttons)
        self.assertEqual(self.shell.active_workspace, WorkspaceId.HOME)
        self.assertEqual(self.use_cases.preflight_calls, [])

    def test_explicit_issued_plan_adds_inert_entry_without_selecting_or_preflighting(self) -> None:
        registration = HackrfActivationWorkspaceRegistration(_issued_plan(), self.presenter)

        self.shell.register_hackrf_activation_workspace(registration)

        self.assertTrue(self.shell.hackrf_activation_registered)
        self.assertIn(OptionalWorkspaceId.HACKRF_ACTIVATION, self.shell._nav_buttons)
        self.assertEqual(self.shell.active_workspace, WorkspaceId.HOME)
        self.assertEqual(self.use_cases.preflight_calls, [])
        self.assertNotIn(OptionalWorkspaceId.HACKRF_ACTIVATION, self.shell._workspace_pages)

        self.shell.set_active_workspace(OptionalWorkspaceId.HACKRF_ACTIVATION)
        page = self.shell._workspace_pages[OptionalWorkspaceId.HACKRF_ACTIVATION]
        self.assertIsInstance(page, HackrfActivationWorkspace)
        self.assertTrue(page._preflight.isEnabled())
        self.assertEqual(self.use_cases.preflight_calls, [])

        page._preflight.click()
        _wait_for(lambda: len(self.use_cases.preflight_calls) == 1)

    def test_registration_fails_closed_for_an_unissued_plan_and_cannot_replace_entry(self) -> None:
        with self.assertRaisesRegex(ValueError, "issued plan"):
            HackrfActivationWorkspaceRegistration(object(), self.presenter)  # type: ignore[arg-type]
        self.assertFalse(self.shell.hackrf_activation_registered)

        self.shell.register_hackrf_activation_workspace(
            HackrfActivationWorkspaceRegistration(_issued_plan(), self.presenter)
        )
        with self.assertRaisesRegex(RuntimeError, "already registered"):
            self.shell.register_hackrf_activation_workspace(
                HackrfActivationWorkspaceRegistration(_issued_plan(), self.presenter)
            )
        self.assertEqual(self.use_cases.preflight_calls, [])

    def test_active_or_transitional_owner_refuses_shell_close_without_implicit_stop(self) -> None:
        self.shell.register_hackrf_activation_workspace(
            HackrfActivationWorkspaceRegistration(_issued_plan(), self.presenter)
        )
        self.shell.set_active_workspace(OptionalWorkspaceId.HACKRF_ACTIVATION)
        self.presenter._last_snapshot = HackrfActivationApplicationSnapshot(  # noqa: SLF001
            HackrfActivationApplicationState.ACTIVE,
            can_request_preflight=False,
            can_confirm_start=False,
            active=True,
        )
        self.shell.show()

        self.assertFalse(self.shell.close())
        self.assertEqual(self.use_cases.preflight_calls, [])
        self.assertIn("Stop HackRF Live", self.shell.statusBar().currentMessage())

        self.presenter._last_snapshot = _ready_snapshot()  # noqa: SLF001
        self.assertTrue(self.shell.close())

    def test_registration_boundary_has_no_vendor_or_activation_path(self) -> None:
        source = REGISTRATION_SOURCE.read_text(encoding="utf-8").lower()
        for forbidden in (
            "ctypes",
            "hackrf.dll",
            "libhackrf",
            "_sdr_native",
            "build_default_sdr_services",
            "start_rx",
            "start_tx",
            "recording",
            "sweep",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
