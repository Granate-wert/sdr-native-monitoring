"""Immutable, non-blocking notification contracts for presentation code."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class NotificationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class NotificationItem:
    """A user-facing notification with text alternatives to colour-only status."""

    notification_id: str
    message: str
    severity: NotificationSeverity
    reason_code: str | None = None
    technical_details: str | None = None
    recommended_action: str | None = None
    dismissible: bool = True


class NotificationStore:
    """Bounded, observable notification queue (project invariant: bounded).

    New items land at the end.  When capacity is reached the oldest item is
    evicted and the drop counter is incremented so overflow is never silent.
    """

    def __init__(self, *, capacity: int = 64) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._items: list[NotificationItem] = []
        self._dropped = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def items(self) -> tuple[NotificationItem, ...]:
        return tuple(self._items)

    @property
    def dropped_count(self) -> int:
        return self._dropped

    def push(self, item: NotificationItem) -> bool:
        """Enqueue; returns True when accepted without dropping an existing item."""

        if not isinstance(item, NotificationItem):
            raise TypeError("item must be NotificationItem")
        dropped = self._dropped
        while len(self._items) >= self._capacity:
            self._items.pop(0)
            self._dropped += 1
        self._items.append(item)
        return self._dropped == dropped

    def dismiss(self, notification_id: str) -> bool:
        """Remove by id; returns True when an item was removed."""

        before = len(self._items)
        self._items = [i for i in self._items if i.notification_id != notification_id]
        return len(self._items) != before

    def clear(self) -> None:
        self._items.clear()


__all__ = [
    "NotificationItem",
    "NotificationSeverity",
    "NotificationStore",
]
