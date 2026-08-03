"""Versioned standalone UI settings; future or legacy schemas reset safely."""

from __future__ import annotations

from PySide6.QtCore import QSettings

CURRENT_SCHEMA_VERSION = 2


def schema_version(settings: QSettings) -> int:
    value = settings.value("schema_version", 1)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def ensure_schema_version(settings: QSettings) -> None:
    if schema_version(settings) != CURRENT_SCHEMA_VERSION:
        settings.clear()
        settings.setValue("schema_version", CURRENT_SCHEMA_VERSION)
    settings.sync()
