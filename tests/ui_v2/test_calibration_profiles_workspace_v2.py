"""Offscreen UI2-09A tests over a fake public Calibration presenter only."""

from __future__ import annotations

from collections.abc import Callable
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain import (
    ApplicabilityRow,
    CalibrationApplicability,
    CalibrationPoint,
    CalibrationProfile,
    CalibrationSignature,
    CalibrationStatus,
)
from sdr_monitor.ui.v2.view_models.calibration_view_model import CalibrationProfileViewModel
from sdr_monitor.ui.v2.workspaces import CalibrationProfilesWorkspaceV2, calibration_profiles_workspace_definition


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[Callable[..., object]] = []

    def connect(self, callback: Callable[..., object]) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback: Callable[..., object]) -> None:
        self.callbacks.remove(callback)

    def emit(self, *args: object) -> None:
        for callback in tuple(self.callbacks):
            callback(*args)


class FakeCalibrationPresenter:
    def __init__(self) -> None:
        self.profiles_changed = FakeSignal()
        self.applicability_changed = FakeSignal()
        self.busy_changed = FakeSignal()
        self.task_failed = FakeSignal()
        self.refresh_calls = 0
        self.compared: list[CalibrationProfile] = []
        self.shutdown_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1

    def compare(self, profile: CalibrationProfile) -> None:
        self.compared.append(profile)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _profile() -> CalibrationProfile:
    return CalibrationProfile(
        profile_id="lab-profile",
        profile_version=2,
        signature=CalibrationSignature(),
        points=(
            CalibrationPoint(100_000_000.0, 1.2, 0.1),
            CalibrationPoint(200_000_000.0, 1.8, 0.2),
        ),
        reference_equipment="reference source",
    )


class CalibrationProfilesWorkspaceV2Tests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.presenter = FakeCalibrationPresenter()
        self.model = CalibrationProfileViewModel(self.presenter)
        self.workspace = CalibrationProfilesWorkspaceV2(self.model)
        self.workspace.resize(1366, 768)
        self.workspace.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.workspace.close()
        self.workspace.deleteLater()
        self.model.dispose()
        self.app.processEvents()

    def test_navigation_is_inert_and_refresh_is_explicit(self) -> None:
        self.assertEqual(self.presenter.refresh_calls, 0)
        self.assertEqual(self.workspace._profiles.property("ui2Role"), "data-list")
        self.assertEqual(self.workspace._applicability.property("ui2Role"), "data-table")
        self.assertFalse(self.workspace._applicability.verticalHeader().isVisible())
        self.workspace._refresh_button.click()
        self.assertEqual(self.presenter.refresh_calls, 1)

    def test_profile_selection_delegates_only_public_compare_and_keeps_active_claim_blocked(self) -> None:
        profile = _profile()
        self.presenter.profiles_changed.emit((profile,))
        self.workspace._profiles.setCurrentRow(0)
        self.assertEqual(self.presenter.compared, [profile])
        self.assertIs(self.workspace._plot._profile, profile)
        self.assertIn("reference source", self.workspace._profile_detail.text())
        self.assertIn("активный статус/dBm", self.workspace._state_chip.text)
        self.assertTrue(all("Актив" not in button.text() for button in self.workspace.findChildren(type(self.workspace._refresh_button))))

    def test_only_matching_immutable_applicability_is_rendered(self) -> None:
        profile = _profile()
        self.presenter.profiles_changed.emit((profile,))
        self.workspace._profiles.setCurrentRow(0)
        applicability = CalibrationApplicability(
            CalibrationStatus.CALIBRATED,
            "Профиль применим",
            (ApplicabilityRow("RF path", "rx", "rx", True),),
            profile.profile_id,
            profile.profile_version,
        )
        self.presenter.applicability_changed.emit(applicability)
        self.assertEqual(self.workspace._applicability.rowCount(), 1)
        self.assertEqual(self.workspace._applicability.item(0, 3).text(), "OK")

    def test_error_is_visible_without_recovery_or_activation_action(self) -> None:
        self.presenter.task_failed.emit("profile list failed")
        self.assertTrue(self.workspace._error_banner.isVisible())
        self.assertIn("profile list failed", self.workspace._error_banner.accessibleDescription())
        self.assertFalse(self.workspace._error_banner._action.isVisible())

    def test_definition_is_external_and_lazy(self) -> None:
        definition = calibration_profiles_workspace_definition(self.model)
        self.assertEqual(definition.workspace_id, "calibration")
        page = definition.workspace_factory()
        self.assertIsInstance(page, CalibrationProfilesWorkspaceV2)
        page.close()
        page.deleteLater()
