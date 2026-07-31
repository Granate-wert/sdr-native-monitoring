"""P16UI-01 typed presentation architecture and legacy bridge tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import sys
from typing import ClassVar
import unittest
from unittest.mock import patch
from collections.abc import Mapping

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl import gui as gui_module
from esw_dfl.gui import MainWindow
from esw_dfl.ui.commands import CommandRegistry, CommandSpec
from esw_dfl.ui.compatibility import _LEGACY_COMMANDS, LegacyMainWindowBridge
from esw_dfl.ui.presenters import PresenterCoordinator
from esw_dfl.ui.state import AppUiState, UiUpdateBatch, WorkspaceId
from heatmap_test_isolation import shutdown_window


class _FakeLegacyWindow:
    active_session_id: str | None = None

    def __init__(self) -> None:
        self._live_controllers: Mapping[str, object] = {}
        self.calls: list[str] = []
        self.audits: list[dict[str, object]] = []

    def _audit(self, category: str, event: str, **details: object) -> None:
        self.audits.append({"category": category, "event": event, **details})

    def __getattr__(self, name: str) -> object:
        if name in {binding.handler_name for binding in _LEGACY_COMMANDS}:
            return lambda: self.calls.append(name)
        raise AttributeError(name)


class _PresenterProbe:
    def __init__(self, name: str, log: list[str]) -> None:
        self._name = name
        self._log = log

    def activate(self) -> None:
        self._log.append(f"activate:{self._name}")

    def deactivate(self) -> None:
        self._log.append(f"deactivate:{self._name}")

    def close(self) -> None:
        self._log.append(f"close:{self._name}")

    def apply_state(self, state: AppUiState) -> None:
        self._log.append(f"state:{self._name}:{state.active_workspace}")


class P16UiArchitectureTests(unittest.TestCase):
    app: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self._windows:
            shutdown_window(window, self.app)

    def _window(self) -> MainWindow:
        with patch.object(gui_module, "QSettings", QSettings):
            window = MainWindow()
        self._windows.append(window)
        return window

    def test_app_state_is_frozen_and_update_batches_reject_unbounded_counts(self) -> None:
        state = AppUiState(active_workspace=WorkspaceId.LIVE_MONITOR)
        with self.assertRaises(FrozenInstanceError):
            state.active_session_id = "replacement"  # type: ignore[misc]
        self.assertEqual(UiUpdateBatch(sequence=3, waterfall_rows=2).waterfall_rows, 2)
        with self.assertRaises(ValueError):
            UiUpdateBatch(sequence=-1)
        with self.assertRaises(ValueError):
            UiUpdateBatch(sequence=1, waterfall_rows=-1)

    def test_registry_validates_ids_shortcuts_predicates_and_audit(self) -> None:
        calls: list[str] = []
        audits: list[tuple[str, bool]] = []
        registry = CommandRegistry(
            (
                CommandSpec(
                    "test.run",
                    "Run",
                    None,
                    "Ctrl+R",
                    lambda: calls.append("run"),
                    enabled=lambda state: state.active_session_id is not None,
                    checked=lambda state: state.active_workspace is WorkspaceId.LIVE_MONITOR,
                    audit_event="test.run",
                ),
            )
        )
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        action = registry.create_action(
            "test.run",
            parent,
            lambda: AppUiState(),
            audit=lambda specification, checked: audits.append((specification.audit_event, checked)),
        )
        self.assertFalse(action.isEnabled())
        self.assertFalse(action.isChecked())
        registry.refresh(AppUiState(active_workspace=WorkspaceId.LIVE_MONITOR, active_session_id="session"))
        self.assertTrue(action.isEnabled())
        self.assertTrue(action.isChecked())
        self.assertEqual(calls, [])  # Constructing/refreshing a widget never calls a service handler.
        action.trigger()
        self.assertEqual(calls, ["run"])
        self.assertEqual(audits, [("test.run", False)])
        with self.assertRaisesRegex(ValueError, "duplicate command_id"):
            registry.register(CommandSpec("test.run", "Again", None, None, lambda: None))
        with self.assertRaisesRegex(ValueError, "duplicate shortcut"):
            registry.register(CommandSpec("test.other", "Other", None, "Ctrl+R", lambda: None))

    def test_presenter_lifecycle_and_batch_delivery_are_ordered(self) -> None:
        log: list[str] = []
        coordinator = PresenterCoordinator((_PresenterProbe("one", log), _PresenterProbe("two", log)))
        coordinator.apply_state(AppUiState())
        coordinator.activate()
        coordinator.apply_batch(UiUpdateBatch(sequence=1, state=AppUiState(WorkspaceId.DIAGNOSTICS)))
        coordinator.close()
        self.assertEqual(
            log,
            [
                "activate:one",
                "activate:two",
                "state:one:diagnostics",
                "state:two:diagnostics",
                "deactivate:two",
                "deactivate:one",
                "close:two",
                "close:one",
            ],
        )
        with self.assertRaisesRegex(RuntimeError, "closed"):
            coordinator.activate()

    def test_bridge_preserves_legacy_action_attributes_handlers_and_shortcuts(self) -> None:
        legacy = _FakeLegacyWindow()
        bridge = LegacyMainWindowBridge(legacy)
        registry = bridge.command_registry()
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        actions = bridge.create_actions(parent, registry)
        self.assertEqual(set(actions), {binding.attribute_name for binding in _LEGACY_COMMANDS})
        for binding in _LEGACY_COMMANDS:
            action = actions[binding.attribute_name]
            self.assertEqual(action.text(), binding.text)
            self.assertEqual(action.shortcut().toString(), binding.shortcut or "")
        actions["open_action"].trigger()
        self.assertEqual(legacy.calls, ["open_files"])
        self.assertEqual(legacy.audits[0]["command_id"], "file.open_dfl")
        self.assertEqual(bridge.snapshot().active_workspace, WorkspaceId.START)
        legacy.active_session_id = "offline-1"
        self.assertEqual(bridge.snapshot().active_workspace, WorkspaceId.OFFLINE_DFL)

    def test_main_window_exposes_compatibility_facade_and_registry_actions(self) -> None:
        window = self._window()
        self.assertIs(window.application_services.repository, window.repository)
        self.assertIs(window.application_services.live_controllers, window._live_controllers)
        self.assertEqual(
            window.command_registry.specification("file.open_dfl").default_shortcut,
            window.open_action.shortcut().toString(),
        )
        self.assertEqual(
            window.command_registry.specification("playback.toggle").default_shortcut,
            window.play_action.shortcut().toString(),
        )


if __name__ == "__main__":
    unittest.main()
