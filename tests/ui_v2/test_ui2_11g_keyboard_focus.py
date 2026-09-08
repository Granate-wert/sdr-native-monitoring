"""UI2-11G automated keyboard-focus regression for admitted V2 pages only."""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAccessible
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QTableWidget, QWidget

from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.shell import AppShellV2
from sdr_monitor.ui.v2.view_models.replay_view_model import ReplayPresenterFactory
from sdr_monitor.ui.v2.view_models.tinysa_view_model import (
    TinySaAnalyzerBindingFactory,
    TinySaSourceActivationPresenterFactory,
)
from tests.ui_v2.test_live_product_composition import (
    FakeCalibrationPresenter,
    FakeDiagnosticsPresenter,
    FakePresenter,
    FakeSweepPresenter,
)


class Ui211GKeyboardFocusTests(unittest.TestCase):
    """Offscreen key traversal; it does not replace human focus-ring or screen-reader review."""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._settings_directory = tempfile.TemporaryDirectory()
        self.live = FakePresenter()
        self.sweep = FakeSweepPresenter()
        self.calibration = FakeCalibrationPresenter()
        self.diagnostics = FakeDiagnosticsPresenter()
        self.diagnostics_factory_calls = 0
        self.tinysa_factory_calls = 0
        self.replay_factory_calls = 0

        def diagnostics_factory() -> FakeDiagnosticsPresenter:
            self.diagnostics_factory_calls += 1
            return self.diagnostics

        def tinysa_activation_factory() -> object:
            self.tinysa_factory_calls += 1
            raise AssertionError("focus traversal must not create a tinySA presenter")

        def tinysa_analyzer_factory(_composed: object) -> object:
            raise AssertionError("focus traversal must not compose tinySA analyzer")

        def replay_factory() -> object:
            self.replay_factory_calls += 1
            raise AssertionError("focus traversal must not create a replay presenter")

        composition = compose_v2_live_product(
            self.live,
            sweep_presenter=self.sweep,
            calibration_presenter=self.calibration,
            diagnostics_presenter_factory=diagnostics_factory,
            replay_presenter_factory=cast(ReplayPresenterFactory, replay_factory),
            tinysa_activation_presenter_factory=cast(TinySaSourceActivationPresenterFactory, tinysa_activation_factory),
            tinysa_analyzer_binding_factory=cast(TinySaAnalyzerBindingFactory, tinysa_analyzer_factory),
            now_ns=lambda: 1,
        )
        self.shell = AppShellV2(
            context=composition.context,
            settings=QSettings(self._settings_directory.name + "/ui2.ini", QSettings.Format.IniFormat),
        )
        self.shell.resize(1366, 768)
        self.shell.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        if self.shell.isVisible():
            self.shell.close()
        self.shell.deleteLater()
        self.app.processEvents()
        self._settings_directory.cleanup()

    def test_admitted_interactions_are_focusable_named_and_tab_reachable_without_commands(self) -> None:
        targets: dict[str, QWidget] = {}
        for workspace_id in ("home", "live", "sweep", "calibration", "diagnostics", "replay", "tinysa"):
            self.shell.select_workspace(workspace_id)
            self.app.processEvents()
            page = self.shell._workspace_pages[workspace_id]
            self._assert_named_interactions(page)
            targets[workspace_id] = self._primary_target(page, workspace_id)

        for workspace_id, target in targets.items():
            self.shell.select_workspace(workspace_id)
            self.app.processEvents()
            self.assertTrue(self._reach_with_tab(target), workspace_id)

        self.assertEqual(self.diagnostics_factory_calls, 0)
        self.assertEqual(self.tinysa_factory_calls, 0)
        self.assertEqual(self.replay_factory_calls, 0)
        self.assertEqual(self.live.discover_calls, 0)
        self.assertEqual(self.live.start_calls, 0)
        self.assertEqual(self.sweep.plan_calls, 0)
        self.assertEqual(self.calibration.refresh_calls, 0)
        self.assertEqual(self.diagnostics.refresh_calls, 0)

    def test_all_normal_routes_publish_one_named_button_and_named_workspace_without_commands(self) -> None:
        workspace_ids = (
            "home",
            "live",
            "sweep",
            "calibration",
            "recording",
            "replay",
            "diagnostics",
            "tinysa",
        )
        self.assertEqual(tuple(self.shell._nav_buttons), workspace_ids)

        for workspace_id in workspace_ids:
            self.shell.select_workspace(workspace_id)
            self.app.processEvents()

            page = self.shell._workspace_pages[workspace_id]
            self.assertTrue(page.accessibleName().strip(), workspace_id)
            self._assert_named_interactions(page)

            active_items = [
                identifier
                for identifier, button in self.shell._nav_buttons.items()
                if button.is_active
            ]
            self.assertEqual(active_items, [workspace_id])
            for identifier, button in self.shell._nav_buttons.items():
                interface = QAccessible.queryAccessibleInterface(button)
                self.assertIsNotNone(interface, identifier)
                assert interface is not None
                self.assertEqual(interface.role(), QAccessible.Role.Button, identifier)
                self.assertFalse(button.isCheckable(), identifier)
                if identifier == workspace_id:
                    self.assertIn("текущая страница", button.accessibleName())
                    self.assertTrue(button.accessibleDescription().startswith("Текущая страница."))
                else:
                    self.assertNotIn("текущая страница", button.accessibleName())
                    self.assertTrue(button.accessibleDescription().strip())
                    self.assertFalse(button.accessibleDescription().startswith("Текущая страница."))

        self.assertEqual(self.diagnostics_factory_calls, 0)
        self.assertEqual(self.tinysa_factory_calls, 0)
        self.assertEqual(self.replay_factory_calls, 0)
        self.assertEqual(self.live.discover_calls, 0)
        self.assertEqual(self.live.start_calls, 0)
        self.assertEqual(self.sweep.plan_calls, 0)
        self.assertEqual(self.calibration.refresh_calls, 0)
        self.assertEqual(self.diagnostics.refresh_calls, 0)

    def test_all_normal_routes_rebuild_localized_uia_semantics_without_commands(self) -> None:
        """RU/EN V2-only rebuild retains one accessible current page per route."""

        workspace_ids = (
            "home",
            "live",
            "sweep",
            "calibration",
            "recording",
            "replay",
            "diagnostics",
            "tinysa",
        )
        for locale, marker, description_prefix in (
            (UiLocale.EN, "current page", "Current page."),
            (UiLocale.RU, "текущая страница", "Текущая страница."),
        ):
            self.shell.select_appearance_locale(locale)
            self.app.processEvents()
            self.assertEqual(self.shell.current_locale, locale)

            for workspace_id in workspace_ids:
                self.shell.select_workspace(workspace_id)
                self.app.processEvents()

                page = self.shell._workspace_pages[workspace_id]
                self.assertTrue(page.accessibleName().strip(), (locale, workspace_id))
                self._assert_named_interactions(page)

                for identifier, button in self.shell._nav_buttons.items():
                    interface = QAccessible.queryAccessibleInterface(button)
                    self.assertIsNotNone(interface, (locale, identifier))
                    assert interface is not None
                    self.assertEqual(interface.role(), QAccessible.Role.Button, (locale, identifier))
                    self.assertFalse(button.isCheckable(), (locale, identifier))
                    if identifier == workspace_id:
                        self.assertIn(marker, button.accessibleName(), (locale, identifier))
                        self.assertTrue(button.accessibleDescription().startswith(description_prefix), (locale, identifier))
                    else:
                        self.assertNotIn(marker, button.accessibleName(), (locale, identifier))
                        self.assertFalse(button.accessibleDescription().startswith(description_prefix), (locale, identifier))

        self.assertEqual(self.diagnostics_factory_calls, 0)
        self.assertEqual(self.tinysa_factory_calls, 0)
        self.assertEqual(self.replay_factory_calls, 0)
        self.assertEqual(self.live.discover_calls, 0)
        self.assertEqual(self.live.start_calls, 0)
        self.assertEqual(self.sweep.plan_calls, 0)
        self.assertEqual(self.calibration.refresh_calls, 0)
        self.assertEqual(self.diagnostics.refresh_calls, 0)

    def _assert_named_interactions(self, page: QWidget) -> None:
        for widget in page.findChildren(QWidget):
            if not widget.isVisible() or not widget.isEnabled() or widget.focusPolicy() == Qt.FocusPolicy.NoFocus:
                continue
            if isinstance(widget, QLineEdit) and isinstance(widget.parentWidget(), QAbstractSpinBox):
                continue  # Internal editor inherits the semantic name from its owning spin box.
            if isinstance(widget, QAbstractButton):
                self.assertTrue(widget.accessibleName().strip() or widget.text().strip(), type(widget).__name__)
            elif isinstance(widget, (QAbstractSpinBox, QComboBox, QLineEdit, QTableWidget)):
                self.assertTrue(widget.accessibleName().strip(), type(widget).__name__)

    def _primary_target(self, page: QWidget, workspace_id: str) -> QWidget:
        if workspace_id == "home":
            return getattr(page, "_live_card").button
        if workspace_id == "live":
            return getattr(page, "_device_selector")
        if workspace_id == "sweep":
            return getattr(page, "_mode")
        if workspace_id == "calibration":
            return getattr(page, "_refresh_button")
        if workspace_id == "tinysa":
            return getattr(page, "_discover")
        if workspace_id == "replay":
            return getattr(page, "_path")
        return getattr(page, "_load_button")

    def _reach_with_tab(self, target: QWidget) -> bool:
        self.shell._nav_buttons[self.shell.active_workspace_id].setFocus(Qt.FocusReason.OtherFocusReason)
        self.app.processEvents()
        for _ in range(96):
            source = self.app.focusWidget() or self.shell
            QTest.keyClick(source, Qt.Key.Key_Tab)
            self.app.processEvents()
            if self.app.focusWidget() is target:
                return True
        return False
