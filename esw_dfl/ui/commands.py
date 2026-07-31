"""Single-source QAction registry for legacy and future presentation shells."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction

from .state import AppUiState

if TYPE_CHECKING:
    from collections.abc import Mapping


StatePredicate = Callable[[AppUiState], bool]
CommandHandler = Callable[[], None]
CommandAudit = Callable[["CommandSpec", bool], None]


def _always_enabled(_: AppUiState) -> bool:
    return True


@dataclass(frozen=True, slots=True)
class CommandSpec:
    command_id: str
    text_key: str
    icon_id: str | None
    default_shortcut: str | None
    handler: CommandHandler
    enabled: StatePredicate = _always_enabled
    checked: StatePredicate | None = None
    audit_event: str = "ui.command"


class CommandRegistry:
    """Validates command identity once and creates Qt actions on demand."""

    def __init__(self, specifications: Iterable[CommandSpec] = ()) -> None:
        self._specifications: dict[str, CommandSpec] = {}
        self._actions: dict[str, list[QAction]] = {}
        for specification in specifications:
            self.register(specification)

    @staticmethod
    def _shortcut_key(shortcut: str) -> str:
        return shortcut.replace(" ", "").casefold()

    def register(self, specification: CommandSpec) -> None:
        command_id = specification.command_id.strip()
        if not command_id:
            raise ValueError("command_id must not be empty")
        if command_id in self._specifications:
            raise ValueError(f"duplicate command_id: {command_id}")
        shortcut = specification.default_shortcut
        if shortcut:
            canonical = self._shortcut_key(shortcut)
            for existing in self._specifications.values():
                if existing.default_shortcut and self._shortcut_key(existing.default_shortcut) == canonical:
                    raise ValueError(f"duplicate shortcut: {shortcut}")
        self._specifications[command_id] = specification

    def specification(self, command_id: str) -> CommandSpec:
        return self._specifications[command_id]

    def command_ids(self) -> tuple[str, ...]:
        return tuple(self._specifications)

    def create_action(
        self,
        command_id: str,
        parent: QObject,
        state_supplier: Callable[[], AppUiState],
        *,
        audit: CommandAudit | None = None,
    ) -> QAction:
        specification = self.specification(command_id)
        action = QAction(specification.text_key, parent)
        if specification.default_shortcut:
            action.setShortcut(specification.default_shortcut)
        action.setCheckable(specification.checked is not None)
        self._apply_action_state(action, specification, state_supplier())

        def invoke(checked: bool = False) -> None:
            specification.handler()
            if audit is not None:
                audit(specification, bool(checked))

        action.triggered.connect(invoke)
        self._actions.setdefault(command_id, []).append(action)
        return action

    def refresh(self, state: AppUiState) -> None:
        for command_id, actions in self._actions.items():
            specification = self.specification(command_id)
            for action in actions:
                self._apply_action_state(action, specification, state)

    @staticmethod
    def _apply_action_state(action: QAction, specification: CommandSpec, state: AppUiState) -> None:
        action.setEnabled(specification.enabled(state))
        if specification.checked is not None:
            action.setChecked(specification.checked(state))

    def actions(self) -> Mapping[str, tuple[QAction, ...]]:
        return {command_id: tuple(actions) for command_id, actions in self._actions.items()}
