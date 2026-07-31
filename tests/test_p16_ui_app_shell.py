"""P16UI-03 AppShell, navigation and layout preset tests."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
from typing import ClassVar
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl.ui.app_shell import (
    DEFAULT_GEOMETRY,
    MINIMUM_GEOMETRY,
    AppShell,
    ROLE_BOTTOM_TOOLS,
    ROLE_CONTEXT_INSPECTOR,
    ROLE_HEALTH_BAR,
    ROLE_NAVIGATION_RAIL,
    ROLE_SOURCE_NAVIGATOR,
    ROLE_WORKSPACE_HOST,
    build_shell_command_registry,
    shell_placeholder_widget,
)
from esw_dfl.ui.bootstrap import (
    BootstrapConfig,
    USE_APP_SHELL_ENV,
    build_application_window,
    resolve_bootstrap_config,
)
from esw_dfl.ui.identity import (
    CURRENT_IDENTITY,
    DEFAULT_LEGACY_SCOPE,
    LegacySettingsScope,
)
from esw_dfl.ui.i18n import LocaleId, Translator, validate_catalogs
from esw_dfl.ui.layout_presets import (
    LayoutPresetCatalog,
    LayoutPresetId,
)
from esw_dfl.ui.settings_migration import (
    FRAME_NAV_KEYS,
    THEME_KEY,
    apply_migration,
    read_legacy_settings,
)
from esw_dfl.ui.state import HealthItem, HealthStatus, HealthUiState, WorkspaceId
from esw_dfl.ui.workspace_registry import WorkspaceRegistry


class P16UiAppShellTests(unittest.TestCase):
    app: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="p16ui03-shell-")
        self._shell: AppShell | None = None

    def tearDown(self) -> None:
        if self._shell is not None:
            self._shell.close()
            self._shell.deleteLater()
        self._temporary.cleanup()

    def _build_shell(self) -> AppShell:
        self._shell = AppShell()
        # Show the shell so child dock widgets can report visibility
        # reliably. The shell is hidden in tearDown via close().
        self._shell.show()
        return self._shell

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    def test_neutral_identity_is_installed_by_default(self) -> None:
        shell = self._build_shell()
        self.assertEqual(shell.identity.organization_name, CURRENT_IDENTITY.organization_name)
        self.assertEqual(shell.identity.application_name, CURRENT_IDENTITY.application_name)
        self.assertEqual(shell.windowTitle(), CURRENT_IDENTITY.display_name)

    def test_default_geometry_and_minimum_size_are_explicit(self) -> None:
        shell = self._build_shell()
        self.assertEqual((shell.width(), shell.height()), DEFAULT_GEOMETRY)
        self.assertEqual(
            (shell.minimumWidth(), shell.minimumHeight()), MINIMUM_GEOMETRY
        )

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------
    def test_seven_workspace_placeholders_are_registered_and_navigable(self) -> None:
        shell = self._build_shell()
        ids = [descriptor.workspace_id for descriptor in shell.workspaces.descriptors()]
        self.assertEqual(len(ids), len(WorkspaceId))
        self.assertEqual(set(ids), set(WorkspaceId))
        self.assertEqual(shell.workspace_host.count(), len(WorkspaceId))

    def test_set_active_workspace_emits_signal_and_updates_host(self) -> None:
        shell = self._build_shell()
        events: list[WorkspaceId] = []
        shell.workspace_changed.connect(lambda value: events.append(value))
        # The initial workspace is START; calling set_active_workspace(START)
        # after init is a no-op and must not emit a redundant event.
        shell.set_active_workspace(WorkspaceId.START)
        self.assertEqual(events, [])
        for workspace_id in WorkspaceId:
            if workspace_id is WorkspaceId.START:
                continue
            shell.set_active_workspace(workspace_id)
            self.assertEqual(shell.active_workspace, workspace_id)
            placeholder = shell.workspaces.placeholder(workspace_id)
            self.assertIs(
                shell.workspace_host.currentWidget(), placeholder
            )
        expected = [item for item in WorkspaceId if item is not WorkspaceId.START]
        self.assertEqual(events, expected)

    def test_repeated_switch_does_not_recreate_placeholder_widgets(self) -> None:
        shell = self._build_shell()
        captured: dict[WorkspaceId, int] = {}
        for workspace_id in WorkspaceId:
            shell.set_active_workspace(workspace_id)
            placeholder = shell.workspaces.placeholder(workspace_id)
            captured[workspace_id] = id(placeholder)
        for workspace_id in WorkspaceId:
            shell.set_active_workspace(workspace_id)
            self.assertEqual(
                id(shell.workspaces.placeholder(workspace_id)),
                captured[workspace_id],
            )

    def test_navigation_rail_selection_updates_active_workspace(self) -> None:
        shell = self._build_shell()
        target = WorkspaceId.LIVE_MONITOR
        for index in range(shell.navigation_rail.count()):
            item = shell.navigation_rail.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == target.value:
                shell.navigation_rail.setCurrentItem(item)
                break
        self.assertEqual(shell.active_workspace, target)

    # ------------------------------------------------------------------
    # Layout presets
    # ------------------------------------------------------------------
    def test_apply_preset_toggles_dock_visibility(self) -> None:
        shell = self._build_shell()
        shell.apply_preset(LayoutPresetId.COMPACT)
        self.assertEqual(shell.active_preset_id, LayoutPresetId.COMPACT)
        compact_dock = shell.findChild(QWidget, "p16AppShell.dock.source_navigator")
        assert compact_dock is not None
        self.assertFalse(compact_dock.isVisible())
        self.assertFalse(compact_dock.toggleViewAction().isChecked())

        shell.apply_preset(LayoutPresetId.ANALYSIS)
        self.assertEqual(shell.active_preset_id, LayoutPresetId.ANALYSIS)
        analysis_dock = shell.findChild(QWidget, "p16AppShell.dock.source_navigator")
        assert analysis_dock is not None
        self.assertTrue(analysis_dock.toggleViewAction().isChecked())
        # Workspace host is the central widget and must stay visible regardless.
        self.assertTrue(shell.workspace_host.isVisible())

    def test_reset_layout_returns_to_default_preset(self) -> None:
        shell = self._build_shell()
        shell.apply_preset(LayoutPresetId.MONITORING)
        shell.reset_layout()
        self.assertEqual(shell.active_preset_id, LayoutPresetId.DEFAULT)

    def test_preset_catalog_covers_every_preset_id(self) -> None:
        catalog = LayoutPresetCatalog()
        self.assertEqual({preset.preset_id for preset in catalog.presets()}, set(LayoutPresetId))

    # ------------------------------------------------------------------
    # Shell commands
    # ------------------------------------------------------------------
    def test_shell_command_registry_exposes_reset_and_preset_actions(self) -> None:
        shell = self._build_shell()
        registry = build_shell_command_registry(shell)
        ids = registry.command_ids()
        self.assertIn("view.reset_layout", ids)
        self.assertIn("view.layout_preset.default", ids)
        self.assertIn("view.layout_preset.compact", ids)
        self.assertIn("view.layout_preset.analysis", ids)
        self.assertIn("view.layout_preset.monitoring", ids)

    # ------------------------------------------------------------------
    # Health bar / role object names
    # ------------------------------------------------------------------
    def test_role_object_names_are_present(self) -> None:
        shell = self._build_shell()
        for role in (
            ROLE_NAVIGATION_RAIL,
            ROLE_SOURCE_NAVIGATOR,
            ROLE_WORKSPACE_HOST,
            ROLE_CONTEXT_INSPECTOR,
            ROLE_BOTTOM_TOOLS,
            ROLE_HEALTH_BAR,
        ):
            self.assertIsNotNone(shell.findChild(QWidget, role), role)

    def test_set_health_state_updates_status_bar_label(self) -> None:
        shell = self._build_shell()
        shell.set_health_state(
            HealthUiState(
                overall=HealthStatus.WARNING,
                items=(HealthItem("device", HealthStatus.WARNING, "Device not connected"),),
            )
        )
        label = shell.findChild(QWidget, ROLE_HEALTH_BAR)
        self.assertIsNotNone(label)
        self.assertIn("Device not connected", label.text())

    # ------------------------------------------------------------------
    # Settings migration
    # ------------------------------------------------------------------
    def test_legacy_settings_round_trip_preserves_keys(self) -> None:
        legacy = QSettings(
            DEFAULT_LEGACY_SCOPE.organization_name,
            DEFAULT_LEGACY_SCOPE.application_name,
        )
        legacy.setValue(THEME_KEY, "Тёмная")
        for short_name, full_key in FRAME_NAV_KEYS.items():
            legacy.setValue(full_key, _LEGACY_VALUES[short_name])
        legacy.sync()
        view = read_legacy_settings()
        self.assertEqual(view.theme, "Тёмная")
        for short_name, full_key in FRAME_NAV_KEYS.items():
            self.assertIn(short_name, view.frame_navigation)
            self.assertEqual(view.frame_navigation[short_name], _LEGACY_VALUES[short_name])

        target = QSettings(
            CURRENT_IDENTITY.organization_name,
            CURRENT_IDENTITY.application_name,
        )
        result = apply_migration(target=target, legacy=view)
        self.assertEqual(result.migrated_theme, "dark")
        self.assertEqual(str(target.value(THEME_KEY)), "dark")
        for short_name, value in _LEGACY_VALUES.items():
            self.assertEqual(
                str(target.value(FRAME_NAV_KEYS[short_name], type=type(value))),
                str(value),
            )

        # Legacy storage must remain readable so the old MainWindow still works.
        original = read_legacy_settings()
        self.assertEqual(original.theme, "Тёмная")
        for short_name, value in _LEGACY_VALUES.items():
            self.assertEqual(original.frame_navigation[short_name], value)

    def test_settings_migration_is_idempotent(self) -> None:
        view = read_legacy_settings()
        target = QSettings(
            CURRENT_IDENTITY.organization_name,
            CURRENT_IDENTITY.application_name,
        )
        first = apply_migration(target=target, legacy=view)
        second = apply_migration(target=target, legacy=view)
        self.assertEqual(first.migrated_theme, second.migrated_theme)
        self.assertEqual(first.migrated_frame_navigation, second.migrated_frame_navigation)

    def test_legacy_scope_lookup_uses_fallback_scope(self) -> None:
        scope = LegacySettingsScope(
            organization_name="EmptyOrg",
            application_name="EmptyApp",
        )
        view = read_legacy_settings(scope=scope)
        self.assertEqual(view.theme, None)
        self.assertEqual(dict(view.frame_navigation), {})

    # ------------------------------------------------------------------
    # Bootstrap flag
    # ------------------------------------------------------------------
    def test_bootstrap_defaults_to_legacy_shell(self) -> None:
        config = resolve_bootstrap_config({})
        self.assertFalse(config.use_app_shell)

    def test_bootstrap_flag_selects_app_shell_when_set_to_false(self) -> None:
        config = resolve_bootstrap_config({USE_APP_SHELL_ENV: "0"})
        self.assertTrue(config.use_app_shell)

    def test_build_application_window_uses_supplied_factory(self) -> None:
        shell = AppShell()
        self.addCleanup(shell.deleteLater)
        legacy = QWidget()
        self.addCleanup(legacy.deleteLater)
        config = BootstrapConfig(use_app_shell=True, environment_value="0")

        def build_shell() -> AppShell:
            return shell

        def build_legacy() -> QWidget:
            return legacy

        chosen, returned_config = build_application_window(
            app_shell_factory=build_shell,
            legacy_main_window_factory=build_legacy,
            config=config,
        )
        self.assertIs(chosen, shell)
        self.assertIs(returned_config, config)

    def test_build_application_window_uses_legacy_factory_by_default(self) -> None:
        legacy = QWidget()
        self.addCleanup(legacy.deleteLater)

        def build_shell() -> AppShell:
            raise AssertionError("shell factory must not be called when AppShell is disabled")

        def build_legacy() -> QWidget:
            return legacy

        chosen, _ = build_application_window(
            app_shell_factory=build_shell,
            legacy_main_window_factory=build_legacy,
            config=BootstrapConfig(use_app_shell=False, environment_value=None),
        )
        self.assertIs(chosen, legacy)

    # ------------------------------------------------------------------
    # Translation keys
    # ------------------------------------------------------------------
    def test_translation_keys_for_workspaces_layouts_and_shell_are_complete(self) -> None:
        validate_catalogs()
        translator = Translator(LocaleId.RU)
        for key in (
            "workspace.start",
            "workspace.live_monitor",
            "workspace.wideband_sweep",
            "workspace.offline_dfl",
            "workspace.calibration",
            "workspace.recording_replay",
            "workspace.diagnostics",
            "layout.reset",
            "layout.preset.default",
            "shell.navigation",
            "shell.workspace_host",
        ):
            rendered = translator.text(key)
            self.assertTrue(rendered and rendered != key, msg=key)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def test_shell_placeholder_helper_sets_object_name(self) -> None:
        placeholder = shell_placeholder_widget(workspace_id=WorkspaceId.CALIBRATION)
        self.addCleanup(placeholder.deleteLater)
        self.assertTrue(placeholder.objectName().endswith("calibration"))


_LEGACY_VALUES: dict[str, object] = {
    "sequential_mode": True,
    "wheel_step": 5,
    "touchpad_threshold": 0.25,
    "fps": 30,
    "settle_delay_ms": 250,
}


class WorkspaceRegistryTests(unittest.TestCase):
    def test_registry_catalog_covers_every_workspace_id(self) -> None:
        registry = WorkspaceRegistry()
        ids = {descriptor.workspace_id for descriptor in registry.descriptors()}
        self.assertEqual(ids, set(WorkspaceId))
        self.assertEqual(len(registry.shortcuts()), len(WorkspaceId))

    def test_attach_placeholders_marks_widget_per_workspace(self) -> None:
        registry = WorkspaceRegistry()
        captured: list[WorkspaceId] = []

        def factory(workspace_id: WorkspaceId) -> QWidget:
            captured.append(workspace_id)
            widget = QWidget()
            widget.setObjectName(f"placeholder.{workspace_id.value}")
            return widget

        registry.attach_placeholders(factory)
        self.assertEqual(set(captured), set(WorkspaceId))
        for workspace_id in WorkspaceId:
            placeholder = registry.placeholder(workspace_id)
            self.assertTrue(placeholder.objectName().endswith(workspace_id.value))


if __name__ == "__main__":
    unittest.main()
