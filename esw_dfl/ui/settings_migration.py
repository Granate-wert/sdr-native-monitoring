"""Read legacy ``QSettings`` values without rewriting them.

The migrator only **reads** the legacy storage scope and **writes** the
new keys into the neutral scope.  The legacy keys remain in place so any
rollback to the old ``MainWindow`` continues to read the original data.

Scope rules:

* ``legacy_settings()`` opens a temporary :class:`QSettings` against the
  legacy organization/application and exposes the recognised theme,
  frame-navigation and layout keys via typed accessors.
* ``MigratedSettings`` writes the new keys into the neutral scope and
  never deletes the legacy keys.
* ``apply_migration`` is idempotent: rerunning it with the same input
  must not duplicate or lose values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from PySide6.QtCore import QSettings

from .identity import CURRENT_IDENTITY, DEFAULT_LEGACY_SCOPE, LegacySettingsScope
from .themes import ThemeProvider


THEME_KEY: str = "theme"
FRAME_NAV_KEYS: Mapping[str, str] = {
    "sequential_mode": "frame_navigation/sequential_mode",
    "wheel_step": "frame_navigation/wheel_step",
    "touchpad_threshold": "frame_navigation/touchpad_threshold",
    "fps": "frame_navigation/fps",
    "settle_delay_ms": "frame_navigation/settle_delay_ms",
}


@dataclass(frozen=True, slots=True)
class LegacySettings:
    """A read-only view over legacy ``QSettings`` storage."""

    scope: LegacySettingsScope
    theme: str | None
    frame_navigation: Mapping[str, str | int | float | bool]
    raw_keys: frozenset[str]

    @property
    def has_theme(self) -> bool:
        return self.theme is not None

    def frame_navigation_value(self, name: str) -> str | int | float | bool | None:
        return self.frame_navigation.get(name)


def open_legacy_settings(scope: LegacySettingsScope | None = None) -> QSettings:
    """Create a ``QSettings`` instance bound to the legacy scope."""

    target = scope or DEFAULT_LEGACY_SCOPE
    return QSettings(target.organization_name, target.application_name)


def _read_typed_value(settings: QSettings, key: str) -> str | int | float | bool | None:
    if not settings.contains(key):
        return None
    value = settings.value(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        # QSettings in IniFormat stores numeric values as strings; try to
        # recover the original Python type so the migrated values look
        # the same as the ones written by the legacy MainWindow.
        lowered = value.strip().casefold()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value
    return str(value)


def read_legacy_settings(scope: LegacySettingsScope | None = None) -> LegacySettings:
    """Read the recognised legacy keys without mutating storage."""

    target = scope or DEFAULT_LEGACY_SCOPE
    settings = open_legacy_settings(target)
    try:
        frame_nav: dict[str, str | int | float | bool] = {}
        for short_name, full_key in FRAME_NAV_KEYS.items():
            value = _read_typed_value(settings, full_key)
            if value is not None:
                frame_nav[short_name] = value
        theme_value = _read_typed_value(settings, THEME_KEY)
        theme = theme_value if isinstance(theme_value, str) else None
        return LegacySettings(
            scope=target,
            theme=theme,
            frame_navigation=frame_nav,
            raw_keys=frozenset(settings.allKeys()),
        )
    finally:
        settings.sync()


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """Outcome of one migration pass; values written to the new scope."""

    migrated_theme: str | None
    migrated_frame_navigation: Mapping[str, str | int | float | bool]
    preserved_legacy_keys: frozenset[str]


def apply_migration(
    target: QSettings | None = None,
    *,
    legacy: LegacySettings | None = None,
    scope: LegacySettingsScope | None = None,
) -> MigrationResult:
    """Copy theme and frame-navigation settings into the new scope.

    The legacy storage is opened read-only; the new ``QSettings`` writes
    only the recognised keys.  Idempotency is guaranteed because the
    function uses ``QSettings.setValue`` (not append) and never touches
    the legacy scope.
    """

    legacy_view = legacy or read_legacy_settings(scope)
    new_settings = target or QSettings(
        CURRENT_IDENTITY.organization_name,
        CURRENT_IDENTITY.application_name,
    )

    migrated_theme: str | None = None
    if legacy_view.has_theme and isinstance(legacy_view.theme, str):
        resolved = ThemeProvider.resolve(legacy_view.theme).value
        new_settings.setValue(THEME_KEY, resolved)
        migrated_theme = resolved

    migrated_nav: dict[str, str | int | float | bool] = {}
    for short_name, value in legacy_view.frame_navigation.items():
        full_key = FRAME_NAV_KEYS[short_name]
        new_settings.setValue(full_key, value)
        migrated_nav[short_name] = value

    new_settings.sync()

    return MigrationResult(
        migrated_theme=migrated_theme,
        migrated_frame_navigation=migrated_nav,
        preserved_legacy_keys=legacy_view.raw_keys,
    )


def legacy_settings_are_readable(scope: LegacySettingsScope | None = None) -> bool:
    """Return ``True`` when the legacy ``QSettings`` file is readable."""

    target = scope or DEFAULT_LEGACY_SCOPE
    settings = open_legacy_settings(target)
    try:
        return settings.status() == QSettings.Status.NoError
    finally:
        settings.sync()


SCHEMA_VERSION_KEY: str = "schema_version"
CURRENT_SCHEMA_VERSION: int = 2


def schema_version(settings: QSettings) -> int:
    """Current persisted schema version; missing key means legacy v1."""

    value = _read_typed_value(settings, SCHEMA_VERSION_KEY)
    return int(value) if isinstance(value, (int, float)) else 1


def ensure_schema_version(
    settings: QSettings,
    *,
    supported: int = CURRENT_SCHEMA_VERSION,
) -> int:
    """Recover/reset helper: return the version, and bump legacy entries.

    If the stored version is newer than the code understands the whole scope
    is reset (recovery) so a future migration never reads stale keys; the
    caller can then re-run `apply_migration` from the untouched legacy scope.
    """

    version = schema_version(settings)
    if version > supported:
        settings.clear()
        settings.setValue(SCHEMA_VERSION_KEY, supported)
        settings.sync()
        return supported
    if version < supported:
        settings.setValue(SCHEMA_VERSION_KEY, supported)
        settings.sync()
        return supported
    return version


def reset_settings(settings: QSettings) -> None:
    """Bounded recovery: drop every key and pin the current schema version."""

    settings.clear()
    settings.setValue(SCHEMA_VERSION_KEY, CURRENT_SCHEMA_VERSION)
    settings.sync()


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "FRAME_NAV_KEYS",
    "LegacySettings",
    "MigrationResult",
    "SCHEMA_VERSION_KEY",
    "THEME_KEY",
    "apply_migration",
    "ensure_schema_version",
    "legacy_settings_are_readable",
    "open_legacy_settings",
    "read_legacy_settings",
    "reset_settings",
    "schema_version",
]
