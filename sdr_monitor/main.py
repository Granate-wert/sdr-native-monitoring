"""Standalone entry point for SDR Native Monitoring."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
from pathlib import Path

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
    verification = parser.add_mutually_exclusive_group()
    verification.add_argument("--verify-packaged-libiio-runtime", action="store_true", help=argparse.SUPPRESS)
    verification.add_argument("--verify-packaged-tinysa-runtime", action="store_true", help=argparse.SUPPRESS)
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


def _packaged_libiio_runtime_verdict() -> dict[str, object]:
    """Load package-local libiio metadata only; never scan or open a device."""

    native = importlib.import_module("sdr_monitor._sdr_native")
    build_info_reader = getattr(native, "build_info", None)
    runtime_info_reader = getattr(native, "pluto_runtime_info", None)
    if not callable(build_info_reader) or not callable(runtime_info_reader):
        raise RuntimeError("canonical native module lacks Pluto runtime metadata readers")
    if not bool(dict(build_info_reader()).get("pluto_compiled")):
        raise RuntimeError("canonical native module lacks Pluto support")

    from .libiio_runtime import configure_frozen_libiio_runtime

    components = configure_frozen_libiio_runtime(native)
    if not components:
        raise RuntimeError("packaged libiio runtime verification requires a frozen executable")
    runtime_info = runtime_info_reader()
    if not bool(getattr(runtime_info, "available", False)):
        raise RuntimeError("package-local libiio metadata load failed: " + str(getattr(runtime_info, "error", "")))
    loaded_path = Path(str(getattr(runtime_info, "library_path", ""))).resolve()
    if loaded_path != components[0].resolve():
        raise RuntimeError("libiio metadata loader escaped the package-local runtime")
    return {
        "libiio_available": True,
        "libiio_major": int(getattr(runtime_info, "major", -1)),
        "libiio_minor": int(getattr(runtime_info, "minor", -1)),
        "library_package_local": True,
        "pluto_compiled": True,
        "runtime_component_count": len(components),
    }


def main(argv: list[str] | None = None) -> int:
    """Start only the standalone AppShell; legacy DFL GUI remains a separate app."""
    arguments = _arguments(list(sys.argv[1:] if argv is None else argv))
    if arguments.verify_packaged_libiio_runtime:
        print(json.dumps(_packaged_libiio_runtime_verdict(), sort_keys=True))
        return 0
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
