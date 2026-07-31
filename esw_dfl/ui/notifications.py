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
