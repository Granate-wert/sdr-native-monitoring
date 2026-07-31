"""Bootstrap helpers used by ``main.py`` and tests to pick a presentation shell.

The legacy ``MainWindow`` remains the production shell until P16UI-10 is
accepted.  During the strangler migration the AppShell can be requested
explicitly through one of:

* the ``SDR_USE_LEGACY_UI`` environment variable (set to ``0``, ``false``
  or ``no`` to disable the legacy shell);
* explicit injection of ``use_app_shell=True`` from a custom launcher.

The bootstrap never imports ``esw_dfl.gui`` at module import time; the
import is deferred until :func:`build_application_window` is invoked so
unit tests can import this module without paying the GUI startup cost.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtWidgets import QWidget

from .app_shell import AppShell
from .identity import CURRENT_IDENTITY


USE_APP_SHELL_ENV: str = "SDR_USE_LEGACY_UI"
TRUTHY_FALSE_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off", ""})


@dataclass(frozen=True, slots=True)
class BootstrapConfig:
    use_app_shell: bool
    environment_value: str | None


def resolve_bootstrap_config(environment: Mapping[str, str] | None = None) -> BootstrapConfig:
    """Return whether the bootstrap should use the new ``AppShell``.

    The legacy shell is selected when the environment variable is unset,
    empty, or set to a truthy value.  ``0`` / ``false`` / ``no`` / ``off``
    switch to the new shell.  Any other value falls back to the legacy
    shell to preserve current production behaviour.
    """

    mapping = environment if environment is not None else os.environ
    raw = mapping.get(USE_APP_SHELL_ENV)
    if raw is None:
        return BootstrapConfig(use_app_shell=False, environment_value=None)
    canonical = raw.strip().casefold()
    if canonical in TRUTHY_FALSE_VALUES:
        return BootstrapConfig(use_app_shell=True, environment_value=raw)
    return BootstrapConfig(use_app_shell=False, environment_value=raw)


class LegacyMainWindowFactory(Protocol):
    def __call__(self) -> QWidget: ...


def build_application_window(
    *,
    app_shell_factory: Callable[[], AppShell],
    legacy_main_window_factory: LegacyMainWindowFactory,
    config: BootstrapConfig | None = None,
) -> tuple[QWidget, BootstrapConfig]:
    """Construct the active presentation window for ``run_gui``.

    Returns both the window and the effective bootstrap configuration so
    callers can log or audit which shell was selected.
    """

    effective = config or resolve_bootstrap_config()
    if effective.use_app_shell:
        return app_shell_factory(), effective
    return legacy_main_window_factory(), effective


def configure_application_identity(app: object) -> None:
    """Install the neutral identity on a ``QCoreApplication`` subclass.

    The function is intentionally permissive about the type so tests can
    pass an unrelated mock that exposes only the ``set*`` methods.
    """

    set_org = getattr(app, "setOrganizationName", None)
    set_app = getattr(app, "setApplicationName", None)
    if callable(set_org):
        set_org(CURRENT_IDENTITY.organization_name)
    if callable(set_app):
        set_app(CURRENT_IDENTITY.application_name)


__all__ = [
    "BootstrapConfig",
    "TRUTHY_FALSE_VALUES",
    "USE_APP_SHELL_ENV",
    "build_application_window",
    "configure_application_identity",
    "resolve_bootstrap_config",
]
