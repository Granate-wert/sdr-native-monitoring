"""Accessible navigation primitive, deliberately independent of AppShell state."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton, QWidget

from ..design.icons import V2IconId, themed_icon
from ..design.tokens import ThemeId, tokens_for_theme


class NavigationItem(QToolButton):
    """One button-like navigation target with an explicit current-page state."""

    def __init__(
        self,
        label: str,
        *,
        icon: V2IconId = V2IconId.NAVIGATION,
        description: str = "",
        active_name: str = "",
        active_description: str = "",
        theme: ThemeId = ThemeId.DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme
        self._icon_id = icon
        self._label = label
        self._description = description or label
        self._active_name = active_name or label
        self._active_description = active_description or self._description
        self._active = False
        self.setProperty("ui2Role", "navigation-item")
        self.setProperty("ui2Active", False)
        self.setText(label)
        self._set_themed_icon()
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setToolTip(self._description)
        self.setAccessibleName(label)
        self.setAccessibleDescription(self._description)

    @property
    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        """Expose selection without turning the navigation button into a checkbox."""

        active = bool(active)
        if active == self._active:
            return
        self._active = active
        self.setProperty("ui2Active", active)
        self.setAccessibleName(self._active_name if active else self._label)
        self.setAccessibleDescription(self._active_description if active else self._description)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self._set_themed_icon()

    def _set_themed_icon(self) -> None:
        tokens = tokens_for_theme(self._theme)
        self.setIcon(themed_icon(self._icon_id, tokens.colors.secondary_text, size=18))
