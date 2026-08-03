"""Bounded standalone notifications with truthful overflow reporting."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum


class NotificationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class NotificationItem:
    notification_id: str
    message: str
    severity: NotificationSeverity = NotificationSeverity.INFO


class NotificationStore:
    def __init__(self, capacity: int = 100) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._items: deque[NotificationItem] = deque(maxlen=capacity)
        self.dropped_count = 0

    @property
    def items(self) -> tuple[NotificationItem, ...]:
        return tuple(self._items)

    def push(self, item: NotificationItem) -> bool:
        accepted_without_drop = len(self._items) < self._items.maxlen
        if not accepted_without_drop:
            self.dropped_count += 1
        self._items.append(item)
        return accepted_without_drop
