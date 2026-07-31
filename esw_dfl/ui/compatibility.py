"""Compatibility bridge that binds typed presentation contracts to MainWindow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction

from .commands import CommandRegistry, CommandSpec
from .state import AppUiState, ConnectionStatus, ConnectionUiState, HealthStatus, HealthUiState, WorkspaceId


class LegacyMainWindowPort(Protocol):
    active_session_id: str | None
    _live_controllers: Mapping[str, object]

    def _audit(self, category: str, event: str, **details: object) -> None: ...


@dataclass(frozen=True, slots=True)
class LegacyCommandBinding:
    attribute_name: str
    command_id: str
    text: str
    shortcut: str | None
    handler_name: str


_LEGACY_COMMANDS: tuple[LegacyCommandBinding, ...] = (
    LegacyCommandBinding("open_action", "file.open_dfl", "Открыть DFL…", "Ctrl+O", "open_files"),
    LegacyCommandBinding("open_live_action", "file.open_live_sdr", "Открыть Live SDR…", "Ctrl+L", "open_live_sdr"),
    LegacyCommandBinding("close_action", "file.close_session", "Закрыть сессию", None, "close_active_session"),
    LegacyCommandBinding(
        "open_workspace_action", "file.open_workspace", "Открыть workspace…", "Ctrl+Shift+O", "open_workspace"
    ),
    LegacyCommandBinding(
        "save_workspace_action", "file.save_workspace", "Сохранить workspace", "Ctrl+S", "save_workspace"
    ),
    LegacyCommandBinding(
        "save_workspace_as_action", "file.save_workspace_as", "Сохранить workspace как…", None, "_save_workspace_as"
    ),
    LegacyCommandBinding("exit_action", "file.exit", "Выход", "Alt+F4", "close"),
    LegacyCommandBinding("add_marker_action", "spectrum.add_marker", "Добавить маркер", "M", "add_marker"),
    LegacyCommandBinding("peak_action", "spectrum.peak_search", "Peak Search", "P", "add_peak_marker"),
    LegacyCommandBinding("delta_action", "spectrum.delta_marker", "Delta Marker", None, "add_delta_marker"),
    LegacyCommandBinding("region_action", "spectrum.select_region", "Выделить полосу", None, "_update_region"),
    LegacyCommandBinding(
        "clear_tools_action",
        "spectrum.clear_analysis",
        "Очистить инструменты анализа",
        None,
        "clear_all_analysis_tools",
    ),
    LegacyCommandBinding(
        "cancel_operations_action",
        "operations.cancel_active",
        "Отменить активные операции",
        None,
        "cancel_active_operations",
    ),
    LegacyCommandBinding("auto_scale_action", "view.auto_scale", "Auto Scale", "A", "_auto_scale"),
    LegacyCommandBinding("reset_zoom_action", "view.reset_zoom", "Reset Zoom", "R", "_reset_zoom"),
    LegacyCommandBinding("view_settings_action", "view.range_settings", "Zoom / диапазон…", "Z", "_show_view_settings"),
    LegacyCommandBinding(
        "frame_navigation_settings_action",
        "view.frame_navigation_settings",
        "Настройки навигации…",
        None,
        "_show_frame_navigation_settings",
    ),
    LegacyCommandBinding("play_action", "playback.toggle", "Воспроизведение", "Space", "_toggle_play"),
)


class LegacyMainWindowBridge:
    """Builds registry-owned actions while keeping legacy slots and ownership."""

    def __init__(self, window: LegacyMainWindowPort) -> None:
        self._window = window

    def snapshot(self) -> AppUiState:
        active_session_id = self._window.active_session_id
        has_live_controller = bool(self._window._live_controllers)
        return AppUiState(
            active_workspace=WorkspaceId.OFFLINE_DFL if active_session_id else WorkspaceId.START,
            active_session_id=active_session_id,
            connection=ConnectionUiState(
                status=ConnectionStatus.CONNECTED if has_live_controller else ConnectionStatus.DISCONNECTED
            ),
            health=HealthUiState(overall=HealthStatus.OK if has_live_controller else HealthStatus.DISCONNECTED),
        )

    def command_registry(self) -> CommandRegistry:
        specifications: list[CommandSpec] = []
        for binding in _LEGACY_COMMANDS:
            handler = cast(Callable[[], None], getattr(self._window, binding.handler_name))
            specifications.append(
                CommandSpec(
                    command_id=binding.command_id,
                    text_key=binding.text,
                    icon_id=None,
                    default_shortcut=binding.shortcut,
                    handler=handler,
                    audit_event="legacy.action_triggered",
                )
            )
        return CommandRegistry(specifications)

    def create_actions(self, parent: QObject, registry: CommandRegistry) -> dict[str, QAction]:
        return {
            binding.attribute_name: registry.create_action(
                binding.command_id,
                parent,
                self.snapshot,
                audit=self._audit_action,
            )
            for binding in _LEGACY_COMMANDS
        }

    def _audit_action(self, specification: CommandSpec, checked: bool) -> None:
        self._window._audit(
            "user",
            "action_triggered",
            action=specification.text_key,
            command_id=specification.command_id,
            audit_event=specification.audit_event,
            checked=checked,
        )
