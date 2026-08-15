"""R11-R offscreen Qt boundary tests; no preflight, factory or SDR runtime."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

from sdr_monitor.application import (
    HackrfActivationApplicationSnapshot,
    HackrfActivationApplicationState,
)
from sdr_monitor.ui.presenters import HackrfActivationPresenter
from sdr_monitor.ui.workspaces import HackrfActivationWorkspace


ROOT = Path(__file__).resolve().parents[1]
PRESENTER_SOURCE = ROOT / "sdr_monitor/ui/presenters/hackrf_activation_presenter.py"
WORKSPACE_SOURCE = ROOT / "sdr_monitor/ui/workspaces/hackrf_activation.py"


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _snapshot(state: HackrfActivationApplicationState) -> HackrfActivationApplicationSnapshot:
    flags = {
        HackrfActivationApplicationState.READY_FOR_PREFLIGHT: (True, False, False),
        HackrfActivationApplicationState.AWAITING_CONFIRMATION: (False, True, False),
        HackrfActivationApplicationState.BUSY: (False, False, False),
        HackrfActivationApplicationState.ACTIVE: (False, False, True),
        HackrfActivationApplicationState.FAULTED: (False, False, False),
    }[state]
    return HackrfActivationApplicationSnapshot(state, *flags)


class _UseCases:
    def __init__(self) -> None:
        self.snapshot = _snapshot(HackrfActivationApplicationState.READY_FOR_PREFLIGHT)
        self.preflight_calls: list[object] = []
        self.confirmations: list[bool] = []
        self.stop_calls: list[int] = []
        self.poll_calls: list[int] = []

    def current(self) -> HackrfActivationApplicationSnapshot:
        return self.snapshot

    def request_preflight(self, plan: object) -> HackrfActivationApplicationSnapshot:
        self.preflight_calls.append(plan)
        self.snapshot = _snapshot(HackrfActivationApplicationState.AWAITING_CONFIRMATION)
        return self.snapshot

    def confirm_start(self, *, user_confirmed: bool) -> HackrfActivationApplicationSnapshot:
        self.confirmations.append(user_confirmed)
        if user_confirmed:
            self.snapshot = _snapshot(HackrfActivationApplicationState.ACTIVE)
        return self.snapshot

    def stop(self, timeout_ms: int) -> HackrfActivationApplicationSnapshot:
        self.stop_calls.append(timeout_ms)
        self.snapshot = _snapshot(HackrfActivationApplicationState.READY_FOR_PREFLIGHT)
        return self.snapshot

    def poll_spectrum_frames(self, max_items: int = 0) -> tuple[object, ...]:
        self.poll_calls.append(max_items)
        return ("older-reduced-frame", "latest-reduced-frame")

    def metrics(self) -> object | None:
        return {"scalar": True}


def _wait(predicate: object, timeout_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _app().processEvents()
        if callable(predicate) and predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for queued presenter result")


class R11RHackrfActivationPresenterTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.use_cases = _UseCases()
        self.presenter = HackrfActivationPresenter(self.use_cases)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        self.presenter.shutdown()
        self.presenter.deleteLater()

    def test_serial_worker_requires_intent_and_coalesces_latest_reduced_frame(self) -> None:
        frames: list[object] = []
        metrics: list[object] = []
        self.presenter.latest_frame_ready.connect(frames.append)
        self.presenter.metrics_ready.connect(metrics.append)
        self.assertEqual(self.use_cases.preflight_calls, [])
        self.assertEqual(self.use_cases.confirmations, [])
        self.assertEqual(self.use_cases.poll_calls, [])

        self.presenter.request_preflight(object())  # type: ignore[arg-type]
        _wait(lambda: self.presenter.current_snapshot.state is HackrfActivationApplicationState.AWAITING_CONFIRMATION)
        self.assertEqual(len(self.use_cases.preflight_calls), 1)
        self.presenter.confirm_start(user_confirmed=True)
        _wait(lambda: self.presenter.current_snapshot.state is HackrfActivationApplicationState.ACTIVE)
        self.assertEqual(self.use_cases.confirmations, [True])

        self.presenter.poll_once()
        self.presenter.poll_once()
        _wait(lambda: bool(frames))
        self.assertEqual(frames, ["latest-reduced-frame"])
        self.assertEqual(metrics, [{"scalar": True}])
        self.assertEqual(self.presenter.metrics.poll_requests, 1)
        self.assertEqual(self.presenter.metrics.poll_superseded_frames, 1)

        self.presenter.stop()
        _wait(lambda: self.presenter.current_snapshot.state is HackrfActivationApplicationState.READY_FOR_PREFLIGHT)
        self.assertEqual(self.use_cases.stop_calls, [1_000])


class R11RHackrfActivationWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.use_cases = _UseCases()
        self.presenter = HackrfActivationPresenter(self.use_cases)  # type: ignore[arg-type]
        self.workspace = HackrfActivationWorkspace(self.presenter)

    def tearDown(self) -> None:
        self.workspace.shutdown()
        self.workspace.close()
        self.workspace.deleteLater()

    def test_workspace_is_inert_until_an_external_plan_and_preflight_click(self) -> None:
        self.assertFalse(self.workspace._preflight.isEnabled())
        self.assertFalse(self.workspace._start.isEnabled())
        self.assertFalse(self.workspace._stop.isEnabled())
        self.assertEqual(self.use_cases.preflight_calls, [])
        buttons = self.workspace.findChildren(QPushButton)
        self.assertEqual(tuple(button.text() for button in buttons), (
            "Check current HackRF identity", "Start Live", "Stop Live"
        ))

        self.workspace.set_activation_plan(object())  # type: ignore[arg-type]
        self.assertTrue(self.workspace._preflight.isEnabled())
        self.assertEqual(self.use_cases.preflight_calls, [])
        self.workspace._preflight.click()
        _wait(lambda: self.presenter.current_snapshot.state is HackrfActivationApplicationState.AWAITING_CONFIRMATION)
        self.assertEqual(len(self.use_cases.preflight_calls), 1)
        self.assertTrue(self.workspace._start.isEnabled())
        self.assertFalse(self.workspace._stop.isEnabled())

    def test_ui_sources_do_not_import_vendor_or_default_live_paths(self) -> None:
        for source in (PRESENTER_SOURCE.read_text(encoding="utf-8").lower(), WORKSPACE_SOURCE.read_text(encoding="utf-8").lower()):
            for forbidden in (
                "ctypes",
                "hackrf.h",
                "_sdr_native",
                "native_live",
                "sdrapplicationservices",
                "start_rx",
                "start_tx",
                "raw_iq",
                "recording",
                "sweep",
            ):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
