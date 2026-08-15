"""Standalone entry point for SDR Native Monitoring."""

from __future__ import annotations

import argparse
import importlib
import json
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
    parser.add_argument("--verify-packaged-tinysa-runtime", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _packaged_tinysa_runtime_verdict() -> dict[str, object]:
    """Check frozen PySide6/pyserial closure without discovery or serial I/O."""

    if not bool(getattr(sys, "frozen", False)):
        raise RuntimeError("packaged tinySA runtime verification requires a frozen executable")
    serial_module = importlib.import_module("serial")
    pyside_module = importlib.import_module("PySide6")
    from .services.tinysa_serial_source_backend import TinySaSerialSourceBackend

    backend = TinySaSerialSourceBackend()
    if not callable(getattr(backend, "discover_endpoints", None)):
        raise RuntimeError("packaged tinySA backend is incomplete")  # noqa: TRY004 - integrity, not input type.
    serial_version = str(
        getattr(serial_module, "__version__", getattr(serial_module, "VERSION", ""))
    )
    pyside_version = str(getattr(pyside_module, "__version__", ""))
    if not serial_version or not pyside_version:
        raise RuntimeError("packaged tinySA runtime versions are unavailable")
    return {
        "backend_constructed": True,
        "device_discovery_invoked": False,
        "pyside6_available": True,
        "pyside6_version": pyside_version,
        "pyserial_available": True,
        "pyserial_version": serial_version,
        "serial_port_opened": False,
    }


def main(argv: list[str] | None = None) -> int:
    """Start only the standalone AppShell; legacy DFL GUI remains a separate app."""
    arguments = _arguments(list(sys.argv[1:] if argv is None else argv))
    if arguments.verify_packaged_tinysa_runtime:
        print(json.dumps(_packaged_tinysa_runtime_verdict(), sort_keys=True))
        return 0
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

    existing_app = QApplication.instance()
    app = existing_app if isinstance(existing_app, QApplication) else QApplication(sys.argv[:1])
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
