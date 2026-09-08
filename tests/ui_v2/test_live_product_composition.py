"""Offscreen UI2-07 product composition over a fake existing presenter."""

from __future__ import annotations

from dataclasses import dataclass
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractSpinBox, QComboBox, QPushButton

from sdr_monitor.domain import (
    AppliedLiveConfiguration,
    DiagnosticCard,
    DiagnosticStatus,
    DiagnosticsSnapshot,
    LiveConfiguration,
    LiveSessionState,
    SweepProgress,
    SweepState,
)
from sdr_monitor.ui.v2.design import ThemeId, stylesheet_for_theme
from sdr_monitor.ui.v2.i18n import UiLocale, text
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.shell import AppShellV2
from sdr_monitor.ui.v2.workspaces import (
    CalibrationProfilesWorkspaceV2,
    DiagnosticsWorkspaceV2,
    HomeWorkspaceV2,
    LiveWorkspaceV2,
    SweepWorkspaceV2,
)


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback: object) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback: object) -> None:
        self.callbacks.remove(callback)

    def emit(self, *args: object) -> None:
        for callback in tuple(self.callbacks):
            callback(*args)


class FakePresenter:
    def __init__(self) -> None:
        self.devices_discovered = FakeSignal()
        self.snapshot_changed = FakeSignal()
        self.task_failed = FakeSignal()
        self.busy_changed = FakeSignal()
        self.render_ready = FakeSignal()
        self.shutdown_calls = 0
        self.discover_calls = 0
        self.select_device_calls = 0
        self.select_manual_uri_calls = 0
        self.apply_configuration_calls = 0
        self.start_calls = 0
        self.stop_calls = 0

    def discover_devices(self) -> None:
        self.discover_calls += 1

    def select_device(self, device_id: str) -> None:
        del device_id
        self.select_device_calls += 1

    def select_manual_uri(self, uri: str) -> None:
        del uri
        self.select_manual_uri_calls += 1

    def apply_configuration(self, configuration: object) -> None:
        del configuration
        self.apply_configuration_calls += 1

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class FakeSweepPresenter:
    def __init__(self) -> None:
        self.busy_changed = FakeSignal()
        self.plan_ready = FakeSignal()
        self.progress_changed = FakeSignal()
        self.result_ready = FakeSignal()
        self.export_ready = FakeSignal()
        self.task_failed = FakeSignal()
        self.shutdown_calls = 0
        self.plan_calls = 0
        self.execute_calls = 0
        self.cancel_calls = 0
        self.export_calls = 0

    def plan(self, configuration: object) -> None:
        del configuration
        self.plan_calls += 1

    def execute(self, configuration: object) -> None:
        del configuration
        self.execute_calls += 1

    def cancel(self) -> None:
        self.cancel_calls += 1

    def export_result(self, result: object, output_path: object) -> None:
        del result, output_path
        self.export_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class FakeCalibrationPresenter:
    def __init__(self) -> None:
        self.profiles_changed = FakeSignal()
        self.applicability_changed = FakeSignal()
        self.busy_changed = FakeSignal()
        self.task_failed = FakeSignal()
        self.refresh_calls = 0
        self.compare_calls = 0
        self.shutdown_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1

    def compare(self, profile: object) -> None:
        del profile
        self.compare_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class FakeDiagnosticsPresenter:
    def __init__(self) -> None:
        self.snapshot_changed = FakeSignal()
        self.self_tests_changed = FakeSignal()
        self.bundle_ready = FakeSignal()
        self.task_failed = FakeSignal()
        self.busy_changed = FakeSignal()
        self.refresh_calls = 0
        self.self_test_calls = 0
        self.cancel_calls = 0
        self.bundle_paths: list[object] = []
        self.shutdown_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1

    def run_self_tests(self) -> None:
        self.self_test_calls += 1

    def cancel(self) -> None:
        self.cancel_calls += 1

    def export_bundle(self, output_path: object) -> None:
        self.bundle_paths.append(output_path)

    def shutdown(self) -> None:
        self.shutdown_calls += 1


@dataclass(frozen=True, slots=True)
class FakeSnapshot:
    state: LiveSessionState
    unit: str = "dBFS/bin"
    device: object | None = None
    applied: object | None = None
    quality: object | None = None


class LiveProductCompositionTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._settings_directory = tempfile.TemporaryDirectory()
        self.presenter = FakePresenter()
        self.composition = compose_v2_live_product(self.presenter, now_ns=lambda: 1)
        self.shell = AppShellV2(
            context=self.composition.context,
            settings=QSettings(
                os.path.join(self._settings_directory.name, "ui2.ini"),
                QSettings.Format.IniFormat,
            ),
        )
        self.shell.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        if self.shell.isVisible():
            self.shell.close()
        self.shell.deleteLater()
        self.app.processEvents()
        self._settings_directory.cleanup()

    def test_live_replaces_only_the_v2_placeholder_and_stays_lazy(self) -> None:
        self.assertEqual(self.shell.active_workspace_id, "home")
        self.assertIsInstance(self.shell._workspace_pages["home"], HomeWorkspaceV2)
        self.assertNotIn("live", self.shell.created_workspace_ids)
        self.shell.select_workspace("live")
        self.assertIsInstance(self.shell._workspace_pages["live"], LiveWorkspaceV2)
        self.assertEqual(self.presenter.shutdown_calls, 0)

    def test_home_quick_start_navigates_to_live_without_presenter_command(self) -> None:
        home = self.shell._workspace_pages["home"]
        assert isinstance(home, HomeWorkspaceV2)
        home._live_card.button.click()
        self.assertEqual(self.shell.active_workspace_id, "live")
        self.assertIsInstance(self.shell._workspace_pages["live"], LiveWorkspaceV2)
        self.assertEqual(self.presenter.shutdown_calls, 0)

    def test_complete_fake_live_lifecycle_is_keyboard_only_and_explicit(self) -> None:
        home = self.shell._workspace_pages["home"]
        assert isinstance(home, HomeWorkspaceV2)
        home._live_card.button.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(home._live_card.button, Qt.Key.Key_Space)
        self.app.processEvents()
        workspace = self.shell._workspace_pages["live"]
        assert isinstance(workspace, LiveWorkspaceV2)

        discover = next(
            button
            for button in workspace.findChildren(QPushButton)
            if button.accessibleName() == text("live.discover.name")
        )
        discover.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(discover, Qt.Key.Key_Space)
        self.assertEqual(self.presenter.discover_calls, 1)

        device = type("Device", (), {"device_id": "keyboard-device", "label": "Keyboard test SDR"})()
        self.presenter.devices_discovered.emit((device,))
        self.app.processEvents()
        workspace._device_selector.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(workspace._device_selector, Qt.Key.Key_Down)
        self.app.processEvents()
        self.assertEqual(self.presenter.select_device_calls, 1)

        self.presenter.snapshot_changed.emit(FakeSnapshot(state=LiveSessionState.CONNECTED, device=device))
        self.app.processEvents()
        workspace._primary_action.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(workspace._primary_action, Qt.Key.Key_Space)
        self.assertEqual(self.presenter.apply_configuration_calls, 1)
        self.assertEqual(self.presenter.start_calls, 0)

        configuration = LiveConfiguration()
        applied = AppliedLiveConfiguration(requested=configuration, applied=configuration)
        self.presenter.snapshot_changed.emit(
            FakeSnapshot(state=LiveSessionState.CONNECTED, device=device, applied=applied)
        )
        self.app.processEvents()
        scene = workspace.visualization.spectrum_scene
        scene.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(scene, Qt.Key.Key_Space)
        self.assertEqual(self.presenter.start_calls, 1)

        self.presenter.snapshot_changed.emit(
            FakeSnapshot(state=LiveSessionState.RUNNING, device=device, applied=applied)
        )
        self.app.processEvents()
        scene.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(scene, Qt.Key.Key_Space)
        self.assertEqual(self.presenter.stop_calls, 1)
        self.assertEqual(self.presenter.select_manual_uri_calls, 0)

        self.presenter.snapshot_changed.emit(
            FakeSnapshot(state=LiveSessionState.CONNECTED, device=device, applied=applied)
        )
        self.app.processEvents()

    def test_locale_switch_rebuilds_live_widgets_without_a_presenter_or_receiver_command(self) -> None:
        self.shell.select_workspace("live")
        old_page = self.shell._workspace_pages["live"]
        self.shell.select_appearance_locale(UiLocale.EN)
        self.app.processEvents()
        page = self.shell._workspace_pages["live"]
        self.assertIsNot(page, old_page)
        self.assertEqual(self.shell.active_workspace_id, "live")
        self.assertEqual(self.shell.current_locale, UiLocale.EN)
        self.assertEqual(self.shell._nav_buttons["live"].text(), text("live.workspace.label", UiLocale.EN))
        self.assertEqual(self.shell._nav_buttons["live"].accessibleName(), "Receive, current page")
        self.assertTrue(self.shell._nav_buttons["live"].accessibleDescription().startswith("Current page."))
        self.assertEqual(page._connection_chip.text, text("live_state.acquisition.unstarted", UiLocale.EN))
        self.assertEqual(self.presenter.discover_calls, 0)
        self.assertEqual(self.presenter.select_device_calls, 0)
        self.assertEqual(self.presenter.select_manual_uri_calls, 0)
        self.assertEqual(self.presenter.apply_configuration_calls, 0)
        self.assertEqual(self.presenter.start_calls, 0)
        self.assertEqual(self.presenter.stop_calls, 0)
        self.assertEqual(self.presenter.shutdown_calls, 0)

    def test_close_releases_one_presenter_only_after_live_is_not_running(self) -> None:
        self.shell.select_workspace("live")
        self.presenter.snapshot_changed.emit(FakeSnapshot(state=LiveSessionState.RUNNING))
        self.app.processEvents()
        self.shell.close()
        self.assertTrue(self.shell.isVisible())
        self.assertEqual(self.presenter.shutdown_calls, 0)
        self.presenter.snapshot_changed.emit(FakeSnapshot(state=LiveSessionState.CONNECTED))
        self.app.processEvents()
        self.shell.close()
        self.assertEqual(self.presenter.shutdown_calls, 1)

    def test_optional_sweep_replaces_its_placeholder_and_home_navigation_stays_inert(self) -> None:
        sweep = FakeSweepPresenter()
        composition = compose_v2_live_product(self.presenter, sweep_presenter=sweep, now_ns=lambda: 1)
        shell = AppShellV2(context=composition.context)
        try:
            shell.show()
            self.app.processEvents()
            home = shell._workspace_pages["home"]
            assert isinstance(home, HomeWorkspaceV2)
            home._sweep_card.button.click()
            self.assertEqual(shell.active_workspace_id, "sweep")
            self.assertIsInstance(shell._workspace_pages["sweep"], SweepWorkspaceV2)
            self.assertEqual(sweep.shutdown_calls, 0)
            sweep.busy_changed.emit(True)
            sweep.progress_changed.emit(SweepProgress(SweepState.RUNNING, 0, 1))
            self.app.processEvents()
            shell.close()
            self.assertTrue(shell.isVisible())
            self.assertEqual(sweep.shutdown_calls, 0)
            sweep.busy_changed.emit(False)
            sweep.progress_changed.emit(SweepProgress(SweepState.CANCELLED, 0, 1))
            self.app.processEvents()
            shell.close()
            self.assertEqual(sweep.shutdown_calls, 1)
        finally:
            if shell.isVisible():
                shell.close()
            shell.deleteLater()
            self.app.processEvents()

    def test_optional_calibration_replaces_its_placeholder_without_profile_io(self) -> None:
        calibration = FakeCalibrationPresenter()
        composition = compose_v2_live_product(self.presenter, calibration_presenter=calibration, now_ns=lambda: 1)
        shell = AppShellV2(context=composition.context)
        try:
            shell.show()
            self.app.processEvents()
            home = shell._workspace_pages["home"]
            assert isinstance(home, HomeWorkspaceV2)
            home._calibration_card.button.click()
            self.assertEqual(shell.active_workspace_id, "calibration")
            self.assertIsInstance(shell._workspace_pages["calibration"], CalibrationProfilesWorkspaceV2)
            self.assertEqual(calibration.refresh_calls, 0)
            self.assertEqual(calibration.compare_calls, 0)
            calibration.busy_changed.emit(True)
            self.app.processEvents()
            shell.close()
            self.assertTrue(shell.isVisible())
            self.assertEqual(calibration.shutdown_calls, 0)
            calibration.busy_changed.emit(False)
            self.app.processEvents()
            shell.close()
            self.assertEqual(calibration.shutdown_calls, 1)
        finally:
            if shell.isVisible():
                shell.close()
            shell.deleteLater()
            self.app.processEvents()

    def test_deferred_diagnostics_navigation_stays_inert_until_load_and_rx_has_no_control(self) -> None:
        diagnostics = FakeDiagnosticsPresenter()
        factory_calls = 0

        def factory() -> FakeDiagnosticsPresenter:
            nonlocal factory_calls
            factory_calls += 1
            return diagnostics

        composition = compose_v2_live_product(
            self.presenter,
            diagnostics_presenter_factory=factory,
            now_ns=lambda: 1,
        )
        shell = AppShellV2(context=composition.context)
        try:
            shell.show()
            self.app.processEvents()
            home = shell._workspace_pages["home"]
            assert isinstance(home, HomeWorkspaceV2)
            home._diagnostics_card.button.click()
            self.assertEqual(shell.active_workspace_id, "diagnostics")
            self.assertIsInstance(shell._workspace_pages["diagnostics"], DiagnosticsWorkspaceV2)
            self.assertEqual(factory_calls, 0)
            workspace = shell._workspace_pages["diagnostics"]
            assert isinstance(workspace, DiagnosticsWorkspaceV2)
            workspace._load_button.click()
            self.assertEqual(factory_calls, 1)
            self.assertEqual(diagnostics.refresh_calls, 1)
            diagnostics.snapshot_changed.emit(
                DiagnosticsSnapshot(
                    platform={"os": "Windows"},
                    cards=(
                        DiagnosticCard(
                            "cpu",
                            "CPU backend",
                            DiagnosticStatus.PASS,
                            "portable",
                            "not run",
                            "Reference CPU path available",
                            "Run self-test",
                        ),
                    ),
                    errors=(),
                )
            )
            self.app.processEvents()
            self.assertEqual(workspace._cards_table.rowCount(), 1)
            self.assertEqual(diagnostics.self_test_calls, 0)
            self.assertEqual(diagnostics.cancel_calls, 0)
            diagnostics.busy_changed.emit(True)
            self.app.processEvents()
            shell.close()
            self.assertTrue(shell.isVisible())
            self.assertEqual(diagnostics.shutdown_calls, 0)
            diagnostics.busy_changed.emit(False)
            self.app.processEvents()
            shell.close()
            self.assertEqual(diagnostics.shutdown_calls, 1)
        finally:
            if shell.isVisible():
                shell.close()
            shell.deleteLater()
            self.app.processEvents()

    def test_shell_theme_reaches_existing_and_lazily_created_v2_workspaces(self) -> None:
        sweep = FakeSweepPresenter()
        calibration = FakeCalibrationPresenter()
        composition = compose_v2_live_product(
            self.presenter,
            sweep_presenter=sweep,
            calibration_presenter=calibration,
            now_ns=lambda: 1,
        )
        shell = AppShellV2(context=composition.context, theme=ThemeId.LIGHT)
        try:
            shell.show()
            self.app.processEvents()
            self.assertEqual(shell._workspace_pages["home"].styleSheet(), stylesheet_for_theme(ThemeId.LIGHT))
            for workspace_id in ("live", "sweep", "calibration"):
                shell.select_workspace(workspace_id)
                self.assertEqual(
                    shell._workspace_pages[workspace_id].styleSheet(),
                    stylesheet_for_theme(ThemeId.LIGHT),
                )
            shell.set_theme(ThemeId.HIGH_CONTRAST)
            for page in shell._workspace_pages.values():
                self.assertEqual(page.styleSheet(), stylesheet_for_theme(ThemeId.HIGH_CONTRAST))
        finally:
            shell.close()
            shell.deleteLater()
            self.app.processEvents()

    def test_sweep_semantic_controls_have_explicit_accessible_names(self) -> None:
        sweep = FakeSweepPresenter()
        composition = compose_v2_live_product(self.presenter, sweep_presenter=sweep, now_ns=lambda: 1)
        shell = AppShellV2(context=composition.context)
        try:
            shell.show()
            shell.select_workspace("sweep")
            self.app.processEvents()
            workspace = shell._workspace_pages["sweep"]
            controls = [
                *workspace.findChildren(QComboBox),
                *workspace.findChildren(QAbstractSpinBox),
            ]
            self.assertEqual(len(controls), 8)
            self.assertTrue(all(control.accessibleName().strip() for control in controls))
        finally:
            shell.close()
            shell.deleteLater()
            self.app.processEvents()
