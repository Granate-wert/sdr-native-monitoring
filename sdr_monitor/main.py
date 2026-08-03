"""Standalone entry point for SDR Native Monitoring."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from ._version import __version__

_LOG_NAME = "sdr_native_monitoring"


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger(_LOG_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def _arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sdr-native-monitoring", description="Standalone SDR Native Monitoring")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Start only the standalone AppShell; legacy DFL GUI remains a separate app."""
    _arguments(list(sys.argv[1:] if argv is None else argv))
    logger = _configure_logging()
    requested_mode = os.environ.get("SDR_UI_MODE", "standalone").strip().casefold()
    if requested_mode not in {"", "standalone", "legacy"}:
        logger.warning("Unknown SDR_UI_MODE=%s; using standalone AppShell", requested_mode)
    elif requested_mode == "legacy":
        logger.warning("Legacy developer mode belongs to the separate DFL entry point; using standalone AppShell")

    from PySide6.QtWidgets import QApplication

    from .ui.app_shell import SDRAppShell
    from .ui.design_tokens import ThemeId
    from .ui.themes import ThemeProvider

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName("SDR Native Monitoring")
    app.setOrganizationDomain("local.sdr-native-monitoring")
    app.setApplicationName("SDR Native Monitoring")
    ThemeProvider.apply(app, ThemeId.DARK)
    shell = SDRAppShell()
    shell.show()
    logger.info("SDR Native Monitoring %s started", __version__)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
