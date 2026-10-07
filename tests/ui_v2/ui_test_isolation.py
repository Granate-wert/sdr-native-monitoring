"""Explicit test-owned locale and real private INI dependencies; no global hook."""
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QSettings

from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale


def own_locale(test, locale: UiLocale = UiLocale.RU) -> None:
    """Register restoration before construction, including failed setup/tests."""
    incoming = current_locale()
    test.addCleanup(set_active_locale, incoming)
    set_active_locale(locale)


def flush_deferred_widgets() -> None:
    app = QCoreApplication.instance()
    if app is not None:
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


class PrivateUiSettings:
    """Per-test files with normal restore/write/sync/reopen, never operator stores.

    Only explicitly selected module-local default constructors are replaced.
    Explicit constructor arguments delegate to the original real Qt class.
    The caller closes its widgets before close(); patches remain through flush.
    """

    MODULES = {
        "shell": "sdr_monitor.ui.v2.shell.app_shell",
        "spectrum-view": "sdr_monitor.ui.v2.waterfall.spectrum_view",
        "waterfall-pane": "sdr_monitor.ui.v2.waterfall.pane",
    }

    def __init__(self, modules=()) -> None:
        self._folder = TemporaryDirectory(prefix="ui-test-isolation-")
        self.root = Path(self._folder.name)
        self._stack = ExitStack()
        self._instances: list[QSettings] = []
        self.default_calls: list[tuple[str, str]] = []
        self._closed = False
        try:
            for namespace in modules:
                self._stack.enter_context(patch(self.MODULES[namespace] + ".QSettings",
                                               self.factory(namespace)))
        except BaseException:
            self.close()
            raise

    def settings(self, namespace: str) -> QSettings:
        if self._closed or not namespace or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in namespace):
            raise ValueError("Private settings require an open fixture and simple namespace")
        settings = QSettings(str(self.root / (namespace + ".ini")), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        self._instances.append(settings)
        return settings

    def factory(self, namespace: str):
        def construct(*args, **kwargs):
            if args or kwargs:
                return QSettings(*args, **kwargs)
            settings = self.settings(namespace)
            self.default_calls.append((namespace, settings.fileName()))
            return settings
        return construct

    def close(self) -> None:
        if self._closed:
            return
        flush_deferred_widgets()
        for settings in self._instances:
            settings.sync()
        self._stack.close()
        self._instances.clear()
        self._folder.cleanup()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()
