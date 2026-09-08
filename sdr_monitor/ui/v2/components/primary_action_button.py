"""One explicit command button with a truthful non-modal busy state."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton, QWidget


class PrimaryActionButton(QPushButton):
    """Preserves its action label while a bounded operation is in progress."""

    def __init__(self, text: str, *, accessible_description: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setProperty("ui2Role", "primary-action")
        self._idle_text = text
        self.setAccessibleName(text)
        self.setAccessibleDescription(accessible_description)

    def set_busy(self, busy: bool, *, activity: str | None = None) -> None:
        self.setProperty("busy", busy)
        self.setDisabled(busy)
        self.setText(activity or f"{self._idle_text}…" if busy else self._idle_text)
        self.setAccessibleDescription(self.text())
        self.style().unpolish(self)
        self.style().polish(self)

    def set_action_text(self, text: str, *, accessible_description: str | None = None) -> None:
        self._idle_text = text
        if not self.property("busy"):
            self.setText(text)
        self.setAccessibleName(text)
        self.setAccessibleDescription(accessible_description or text)

    def set_preview_state(self, state: str | None) -> None:
        """Set a gallery-only static interaction sample without changing action state."""

        if state not in (None, "hover", "focus", "pressed"):
            raise ValueError(f"unsupported UI2 preview state: {state}")
        self.setProperty("ui2PreviewState", state)
        self.style().unpolish(self)
        self.style().polish(self)
