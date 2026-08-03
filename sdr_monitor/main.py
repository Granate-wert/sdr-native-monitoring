"""Standalone entry point for the SDR Native Monitoring product.

The module has three responsibilities:

1. Never touch ``esw_dfl`` at import time, so the DFL Analyzer package may
   legitimately be absent from a SDR-only distribution.
2. Delegate CLI usage to ``esw_dfl.sdr.cli`` when present (this keeps the
   P16UI CLI contract stable while the SDR tree is being repacked).
3. Launch the AppShell GUI through the existing, DFL-free bootstrap.  The
   ``esw_dfl.gui`` implementation is loaded lazily so the SDR package does
   not depend on Qt at import time.
"""

from __future__ import annotations

import logging
import os
import sys

from ._version import __version__  # re-exported for --version handlers

_LOG_NAME = "sdr_native_monitoring"


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger(_LOG_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def main() -> int:
    logger = _configure_logging()
    logger.info("SDR Native Monitoring %s starting", __version__)

    if len(sys.argv) > 1:
        from esw_dfl.sdr.cli import main as sdr_cli_main

        return sdr_cli_main(sys.argv[1:])

    # SDR UI mode: default to the new AppShell unless explicitly overridden.
    # Allowed values (env SDR_UI_MODE): "standalone" for AppShell (default),
    # "legacy" for the legacy DFL fallback shell (developer-only escape hatch).
    ui_mode = os.environ.get("SDR_UI_MODE", "standalone").strip().casefold()

    logger.info("SDR Native Monitoring GUI bootstrap (ui_mode=%s)", ui_mode)
    if ui_mode == "standalone":
        from PySide6.QtWidgets import QApplication
        from esw_dfl.ui.bootstrap import configure_application_identity

        from sdr_monitor.app_shell import SDRAppShell

        app = QApplication.instance() or QApplication(sys.argv[:1])
        configure_application_identity(app)
        shell = SDRAppShell()
        shell.show()
        return app.exec()

    # Legacy fallback for developers only.
    from esw_dfl.gui import run_gui

    run_gui()
    logger.info("SDR Native Monitoring stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
