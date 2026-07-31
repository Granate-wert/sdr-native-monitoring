"""Neutral product identity used by the AppShell and the legacy bootstrap.

The values returned here are the only identity strings the new presentation
modules may install via ``QCoreApplication.setApplicationName`` /
``setOrganizationName``.  The migration policy is intentionally one-way at
the *identity* layer:

* New code consumes the neutral names from :data:`CURRENT_IDENTITY`.
* Old ``QSettings`` written under the legacy organization/application
  names remain readable through :data:`DEFAULT_LEGACY_SCOPE`.

The module does not touch ``QSettings`` storage directly.  Callers decide
when to read or migrate legacy values; the constant lives here so identity
strings stay testable and ``mypy --strict`` friendly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ProductIdentity:
    organization_name: str
    application_name: str
    executable_name: str
    display_name: str
    legacy_organization_names: tuple[str, ...]
    legacy_application_names: tuple[str, ...]


CURRENT_IDENTITY: Final[ProductIdentity] = ProductIdentity(
    organization_name="SDRNativeMonitoring",
    application_name="SDR Native Monitoring",
    executable_name="sdr_native_monitoring",
    display_name="SDR Native Monitoring",
    legacy_organization_names=("RohdeSchwarzTools",),
    legacy_application_names=("R&S DFL parcer", "ESW_DFL_Analyzer", "RS_DFL_Analyzer"),
)


@dataclass(frozen=True, slots=True)
class LegacySettingsScope:
    organization_name: str
    application_name: str


DEFAULT_LEGACY_SCOPE: Final[LegacySettingsScope] = LegacySettingsScope(
    organization_name="RohdeSchwarzTools",
    application_name="R&S DFLparer",
)


__all__ = [
    "CURRENT_IDENTITY",
    "DEFAULT_LEGACY_SCOPE",
    "LegacySettingsScope",
    "ProductIdentity",
]
