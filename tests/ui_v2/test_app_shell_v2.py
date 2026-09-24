"""Offscreen UI2-03 evidence for the inert AppShellV2 composition boundary."""

from __future__ import annotations

import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QCloseEvent, QFontDatabase, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.design.icons import V2IconId
from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.shell import AppShellV2, ClosePort, V2ShellContext, WorkspaceDefinition


class AppShellV2Tests(unittest.TestCase):
    """Verify lazy navigation, contextual inspectors and idempotent closing."""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ui2-03-shell-")
        self._shells: list[AppShellV2] = []

    def tearDown(self) -> None:
        for shell in self._shells:
            if not shell._is_closed:
                shell.close()
            shell.deleteLater()
        self.app.processEvents()
        self._temporary.cleanup()

    def test_default_shell_is_inert_and_starts_only_home(self) -> None:
        shell = self._make_shell()
        self.assertEqual(shell.active_workspace_id, "home")
        self.assertEqual(shell.created_workspace_ids, frozenset({"home"}))
        self.assertEqual(shell.automatic_discovery_label, "Автопоиск: выключен")
        self.assertTrue(shell._nav_buttons["home"].is_active)
        self.assertFalse(shell._nav_buttons["live"].isCheckable())
        self.assertEqual(shell._nav_buttons["home"].accessibleName(), "Главная, текущая страница")
        self.assertTrue(shell._nav_buttons["home"].accessibleDescription().startswith("Текущая страница."))
        if os.name == "nt":
            self.assertTrue(QFontMetrics(QFontDatabase.font("Segoe UI", "", 13)).inFontUcs4(ord("П")))

    def test_one_hundred_navigation_cycles_keep_factories_lazy(self) -> None:
        calls: Counter[str] = Counter()
        shell = self._make_shell(calls=calls)
        for index in range(100):
            shell.select_workspace("live" if index % 2 else "sweep")
        self.assertEqual(calls, Counter({"home": 1, "live": 1, "sweep": 1}))
        self.assertEqual(shell.created_workspace_ids, frozenset({"home", "live", "sweep"}))

    def test_workspace_selection_replaces_context_inspector(self) -> None:
        shell = self._make_shell()
        shell.select_workspace("live")
        self.assertFalse(shell._nav_buttons["home"].is_active)
        self.assertTrue(shell._nav_buttons["live"].is_active)
        self.assertEqual(shell._nav_buttons["live"].accessibleName(), "Приём, текущая страница")
        first = shell._inspector_layout.itemAt(0).widget()
        assert first is not None
        self.assertEqual(shell.inspector_workspace_id, "live")
        self.assertEqual(first.accessibleName(), "Инспектор: Приём")
        shell.select_workspace("sweep")
        second = shell._inspector_layout.itemAt(0).widget()
        assert second is not None
        self.assertIsNot(first, second)
        self.assertEqual(shell.inspector_workspace_id, "sweep")

    def test_show_observes_new_context_and_previous_page_is_hidden_first(self) -> None:
        events = []
        selected = {}

        class Page(QWidget):
            def __init__(self, identifier):
                super().__init__()
                self.identifier = identifier

            def hideEvent(self, event):
                events.append(("hide", self.identifier))
                super().hideEvent(event)

            def showEvent(self, event):
                super().showEvent(event)
                shell = selected.get("shell")
                if shell is not None:
                    events.append(("show", self.identifier, shell.active_workspace_id,
                                   shell.inspector_workspace_id,
                                   shell._nav_buttons[self.identifier].is_active,
                                   shell._status_bar.isHidden(),
                                   shell.inspector_hidden_for_narrow_width))

        definitions = tuple(WorkspaceDefinition(
            workspace_id=name, label=name, description=name, icon=V2IconId.INFO,
            workspace_factory=lambda name=name: Page(name),
            inspector_factory=lambda name=name: QLabel(name))
            for name in ("home", "analyzer"))
        shell = self._make_shell(context=V2ShellContext(workspaces=definitions))
        selected["shell"] = shell
        shell.resize(1920, 1080)
        shell.show()
        self.app.processEvents()
        for _ in range(3):
            events.clear()
            shell.select_workspace("analyzer")
            self.app.processEvents()
            self.assertEqual(events[:2], [("hide", "home"),
                ("show", "analyzer", "analyzer", "analyzer", True, True, True)])
            events.clear()
            shell.select_workspace("home")
            self.app.processEvents()
            self.assertEqual(events[:2], [("hide", "analyzer"),
                ("show", "home", "home", "home", True, False, shell.width() < 1600)])

    def test_close_ports_are_once_only_and_blockers_fail_closed(self) -> None:
        calls: list[str] = []
        blocked = [True]
        context = self._context(
            close_ports=(
                ClosePort("blocked", lambda: not blocked[0], lambda: calls.append("blocked")),
                ClosePort("ready", lambda: True, lambda: calls.append("ready")),
            )
        )
        shell = self._make_shell(context=context)
        blocked_event = QCloseEvent()
        shell.closeEvent(blocked_event)
        self.assertFalse(blocked_event.isAccepted())
        self.assertEqual(calls, [])
        blocked[0] = False
        successful_event = QCloseEvent()
        shell.closeEvent(successful_event)
        self.assertTrue(successful_event.isAccepted())
        self.assertEqual(calls, ["blocked", "ready"])
        repeated_event = QCloseEvent()
        shell.closeEvent(repeated_event)
        self.assertTrue(repeated_event.isAccepted())
        self.assertEqual(calls, ["blocked", "ready"])

    def test_optional_workspace_is_absent_and_inert_until_explicit_registration(self) -> None:
        calls: Counter[str] = Counter()
        shell = self._make_shell(calls=calls)
        optional = self._definition("optional", "Дополнительно", calls, optional=True)
        self.assertNotIn("optional", shell._nav_buttons)
        shell.register_optional_workspace(optional)
        self.assertIn("optional", shell._nav_buttons)
        self.assertNotIn("optional", calls)
        shell.select_workspace("optional")
        self.assertEqual(calls["optional"], 1)

    def test_1280_geometry_and_narrow_inspector_policy(self) -> None:
        shell = self._make_shell()
        shell.resize(1280, 720)
        shell.show()
        self.app.processEvents()
        self.assertTrue(shell.inspector_hidden_for_narrow_width)
        self.assertFalse(shell._inspector.isVisible())
        self.assertTrue(shell._inspector_toggle.isEnabled())
        self.assertFalse(shell._narrow_inspector_drawer.isVisible())
        self.assertEqual(shell._narrow_inspector_drawer.property("ui2FocusRing"), True)
        shell._inspector_toggle.click()
        self.app.processEvents()
        self.assertTrue(shell._narrow_inspector_drawer.isVisible())
        self.assertTrue(shell._narrow_inspector_drawer_open)
        self.assertEqual(shell._narrow_inspector_drawer.width(), 320)
        self.assertEqual(shell._narrow_inspector_drawer.geometry().top(), shell._top_bar.height())
        self.assertLessEqual(
            shell._narrow_inspector_drawer.geometry().bottom(),
            shell._status_bar.geometry().top(),
        )
        drawer_content = shell._narrow_inspector_drawer._layout.itemAt(0).widget()
        assert drawer_content is not None
        self.assertEqual(drawer_content.accessibleName(), "Инспектор: Главная")
        shell.select_workspace("live")
        self.app.processEvents()
        updated_content = shell._narrow_inspector_drawer._layout.itemAt(0).widget()
        assert updated_content is not None
        self.assertEqual(updated_content.accessibleName(), "Инспектор: Приём")
        QTest.keyClick(shell._narrow_inspector_drawer, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(shell._narrow_inspector_drawer.isVisible())
        self.assertFalse(shell._narrow_inspector_drawer_open)
        shell.resize(1366, 768)
        self.app.processEvents()
        self.assertTrue(shell.inspector_hidden_for_narrow_width)
        self.assertFalse(shell._inspector.isVisible())
        shell.resize(1600, 900)
        self.app.processEvents()
        # A visible top-level window can be clamped by the real Windows work
        # area. Responsive state follows actual logical width, not resize intent.
        self.assertEqual(shell.inspector_hidden_for_narrow_width, shell.width() < 1600)
        self.assertEqual(shell._inspector.isVisible(), shell.width() >= 1600)

    def test_versioned_settings_restore_without_reusing_legacy_keys(self) -> None:
        settings = self._settings()
        first = self._make_shell(settings=settings)
        first.resize(1600, 900)
        first.show()
        self.app.processEvents()
        if self.app.platformName() == "offscreen":
            # Keep a definite wide/pinned-preference branch in the portable
            # suite even when the native desktop cannot show 1600 logical px.
            self.assertGreaterEqual(first.width(), 1600)
            self.assertFalse(first.inspector_hidden_for_narrow_width)
        initial_inspector_preference = first._inspector_requested
        first.toggle_navigation()
        first.toggle_inspector()
        expected_inspector_preference = (initial_inspector_preference
                                         if first.inspector_hidden_for_narrow_width
                                         else not initial_inspector_preference)
        self.assertEqual(first._inspector_requested, expected_inspector_preference)
        self.assertEqual(first._narrow_inspector_drawer_open,
                         first.inspector_hidden_for_narrow_width)
        first.closeEvent(QCloseEvent())
        restored = self._make_shell(settings=settings)
        self.assertTrue(restored.navigation_expanded)
        self.assertEqual(restored._inspector_requested, expected_inspector_preference)
        if self.app.platformName() == "offscreen":
            self.assertFalse(restored._inspector_requested)
        self.assertFalse(restored._narrow_inspector_drawer_open)
        self.assertEqual(str(settings.value("ui_v2/shell/v1/version")), "1")

    def test_discovery_policy_is_displayed_without_a_navigation_action(self) -> None:
        shell = self._make_shell(automatic_discovery_enabled=True)
        self.assertEqual(shell.automatic_discovery_label, "Автопоиск: включён")
        self.assertIn("Автопоиск: включён", shell._discovery_chip.accessibleName())

    def test_top_bar_toggles_use_the_semantic_utility_action_theme_role(self) -> None:
        shell = self._make_shell()
        self.assertEqual(shell._navigation_toggle.property("ui2Role"), "utility-action")
        self.assertEqual(shell._appearance_toggle.property("ui2Role"), "utility-action")
        self.assertEqual(shell._inspector_toggle.property("ui2Role"), "utility-action")

    def test_appearance_popover_persists_only_a_valid_v2_theme(self) -> None:
        settings = self._settings()
        shell = self._make_shell(settings=settings)
        shell.show()
        self.app.processEvents()
        shell._appearance_toggle.click()
        self.app.processEvents()
        popover = shell._appearance_popover
        assert popover is not None
        self.assertTrue(popover.isVisible())
        self.assertIn("Вид и компоновка", popover.accessibleName())
        shell._appearance_popover.theme_buttons[ThemeId.HIGH_CONTRAST].click()
        self.app.processEvents()
        self.assertEqual(shell.current_theme, ThemeId.HIGH_CONTRAST)
        self.assertEqual(str(settings.value("ui_v2/shell/theme")), ThemeId.HIGH_CONTRAST.value)
        self.assertTrue(shell._appearance_popover.theme_buttons[ThemeId.HIGH_CONTRAST].text().startswith("✓"))
        QTest.keyClick(popover, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(popover.isVisible())
        shell.closeEvent(QCloseEvent())
        restored = self._make_shell(settings=settings)
        self.assertEqual(restored.current_theme, ThemeId.HIGH_CONTRAST)

    def test_appearance_popover_switches_and_persists_only_the_v2_locale(self) -> None:
        settings = self._settings()
        shell = self._make_shell(settings=settings)
        shell.show()
        self.app.processEvents()
        shell._appearance_toggle.click()
        self.app.processEvents()
        popover = shell._appearance_popover
        assert popover is not None
        popover.locale_buttons[UiLocale.EN].click()
        self.app.processEvents()
        self.assertEqual(shell.current_locale, UiLocale.EN)
        self.assertEqual(str(settings.value("ui_v2/shell/locale")), "en")
        self.assertEqual(shell._nav_buttons["home"].accessibleName(), "Главная, current page")
        shell.select_workspace("live")
        self.assertEqual(shell._nav_buttons["live"].accessibleName(), "Приём, current page")
        self.assertTrue(shell._nav_buttons["live"].accessibleDescription().startswith("Current page."))
        restored = self._make_shell(settings=settings)
        self.assertEqual(restored.current_locale, UiLocale.EN)

    def test_theme_status_uses_the_shell_locale_after_locale_switch(self) -> None:
        shell = self._make_shell()
        shell.select_appearance_locale(UiLocale.EN)
        shell.select_appearance_theme(ThemeId.LIGHT)
        self.assertEqual(shell._status_message._value.text(), "UI V2 theme: Light")
        shell.select_appearance_theme(ThemeId.HIGH_CONTRAST)
        self.assertEqual(shell._status_message._value.text(), "UI V2 theme: High contrast")

        shell.select_appearance_locale(UiLocale.RU)
        shell.select_appearance_theme(ThemeId.DARK)
        self.assertEqual(shell._status_message._value.text(), "Тема UI V2: Тёмная")

    def test_shell_resets_preserve_non_shell_and_legacy_settings(self) -> None:
        settings = self._settings()
        settings.setValue("legacy/theme", "legacy-light")
        settings.setValue("ui_v2/live/spectrum/ref_level_db", -20)
        shell = self._make_shell(settings=settings)
        shell.resize(1600, 900)
        shell.show()
        self.app.processEvents()
        shell.toggle_navigation()
        shell.toggle_inspector()
        shell.select_appearance_theme(ThemeId.LIGHT)
        shell.reset_shell_layout()
        self.assertFalse(shell.navigation_expanded)
        self.assertTrue(shell._inspector_requested)
        self.assertEqual(shell.current_theme, ThemeId.LIGHT)
        self.assertEqual(settings.value("legacy/theme"), "legacy-light")
        self.assertEqual(settings.value("ui_v2/live/spectrum/ref_level_db"), -20)
        shell.reset_shell_settings()
        self.assertEqual(shell.current_theme, ThemeId.DARK)
        self.assertEqual(settings.value("legacy/theme"), "legacy-light")
        self.assertEqual(settings.value("ui_v2/live/spectrum/ref_level_db"), -20)

    def _make_shell(
        self,
        *,
        calls: Counter[str] | None = None,
        context: V2ShellContext | None = None,
        settings: QSettings | None = None,
        automatic_discovery_enabled: bool = False,
    ) -> AppShellV2:
        shell = AppShellV2(
            context or self._context(calls=calls, automatic_discovery_enabled=automatic_discovery_enabled),
            settings=settings or self._settings(),
        )
        self._shells.append(shell)
        return shell

    def _context(
        self,
        *,
        calls: Counter[str] | None = None,
        close_ports: tuple[ClosePort, ...] = (),
        automatic_discovery_enabled: bool = False,
    ) -> V2ShellContext:
        counts = calls if calls is not None else Counter()
        return V2ShellContext(
            workspaces=(
                self._definition("home", "Главная", counts),
                self._definition("live", "Приём", counts),
                self._definition("sweep", "Обзор", counts),
            ),
            close_ports=close_ports,
            automatic_discovery_enabled=automatic_discovery_enabled,
        )

    @staticmethod
    def _definition(
        workspace_id: str,
        label: str,
        calls: Counter[str],
        *,
        optional: bool = False,
    ) -> WorkspaceDefinition:
        def workspace_factory() -> QWidget:
            calls[workspace_id] += 1
            widget = QWidget()
            widget.setAccessibleName(label)
            return widget

        def inspector_factory() -> QWidget:
            inspector = QLabel(f"Инспектор: {label}")
            inspector.setAccessibleName(f"Инспектор: {label}")
            return inspector

        return WorkspaceDefinition(
            workspace_id=workspace_id,
            label=label,
            description=f"Описание: {label}",
            icon=V2IconId.INFO,
            workspace_factory=workspace_factory,
            inspector_factory=inspector_factory,
            optional=optional,
        )

    def _settings(self) -> QSettings:
        return QSettings(str(Path(self._temporary.name) / "ui2-v2.ini"), QSettings.Format.IniFormat)


if __name__ == "__main__":
    unittest.main()
