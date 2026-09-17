"""Standalone entry point for SDR Native Monitoring."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import tempfile
import types
from pathlib import Path
from collections.abc import Mapping

from ._version import __version__
from .activity_log import install_activity_file_logging, log_event

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
    verification.add_argument("--verify-native-artifact", action="store_true", help=argparse.SUPPRESS)
    verification.add_argument("--offscreen-shell-smoke", action="store_true", help=argparse.SUPPRESS)
    verification.add_argument("--offscreen-default-shell-smoke", action="store_true", help=argparse.SUPPRESS)
    verification.add_argument("--verify-packaged-libiio-runtime", action="store_true", help=argparse.SUPPRESS)
    verification.add_argument("--verify-packaged-tinysa-runtime", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _native_artifact_verdict() -> dict[str, object]:
    """Read packaged native identity/metadata without entering product Live."""

    native = importlib.import_module("sdr_monitor._sdr_native")
    schema_reader = getattr(native, "contract_schema", None)
    build_info_reader = getattr(native, "build_info", None)
    if not callable(schema_reader) or not callable(build_info_reader):
        raise RuntimeError("canonical native module lacks metadata readers")

    schema = dict(schema_reader())
    return {
        "native_module": str(native.__name__),
        "native_path": str(native.__file__),
        "schema": schema.get("schema"),
        "schema_version": schema.get("schema_version"),
        "cuda_compiled": bool(dict(build_info_reader()).get("cuda_compiled")),
    }


def _packaged_libiio_runtime_verdict() -> dict[str, object]:
    """Load only package-local libiio metadata; never create a receiver context."""

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


def _packaged_tinysa_runtime_verdict() -> dict[str, object]:
    """Import the frozen UI/serial closure and construct an inert backend only."""

    if not bool(getattr(sys, "frozen", False)):
        raise RuntimeError("packaged tinySA runtime verification requires a frozen executable")
    serial_module = importlib.import_module("serial")
    pyside_module = importlib.import_module("PySide6")
    from .services.tinysa_serial_source_backend import TinySaSerialSourceBackend

    backend = TinySaSerialSourceBackend()
    if not callable(getattr(backend, "discover_endpoints", None)):
        raise RuntimeError("packaged tinySA backend is incomplete")
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


def _offscreen_shell_verdict() -> dict[str, object]:
    """Create and close the AppShell through the R12-G no-device composition."""

    return _run_offscreen_shell(default_composition=False)


def _offscreen_default_shell_verdict() -> dict[str, object]:
    """Create and close the AppShell through normal inert native composition."""

    return _run_offscreen_shell(default_composition=True)


def _run_offscreen_shell(*, default_composition: bool) -> dict[str, object]:
    """Run one private AppShell lifecycle command without receiver work.

    The command is intentionally process-local: it rejects a preselected
    visible Qt platform or an existing application instance, disables discovery
    before importing the shell, uses private temporary storage, and returns
    before normal logging/AppShell startup can schedule product work.  R12-G
    injects no-device ports; R12-H uses the normal composition root but rejects
    any constructed native device or engine.
    """

    requested_platform = os.environ.get("QT_QPA_PLATFORM", "").strip().casefold()
    if requested_platform not in {"", "offscreen"}:
        raise RuntimeError("offscreen shell smoke refuses a non-offscreen Qt platform")
    previous_qt_platform = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is not None:
        if previous_qt_platform is None:
            os.environ.pop("QT_QPA_PLATFORM", None)
        else:
            os.environ["QT_QPA_PLATFORM"] = previous_qt_platform
        raise RuntimeError("offscreen shell smoke requires a fresh QApplication")

    previous_auto_discover = os.environ.get("SDR_AUTO_DISCOVER")
    previous_local_app_data = os.environ.get("LOCALAPPDATA")
    shell = None
    app = None
    services = None
    native_state: dict[str, object] = {}
    try:
        os.environ["SDR_AUTO_DISCOVER"] = "0"
        prefix = "sdr-r12h-" if default_composition else "sdr-r12g-"
        with tempfile.TemporaryDirectory(prefix=prefix) as temporary_root:
            os.environ["LOCALAPPDATA"] = temporary_root
            from .ui.v2_composition import build_v2_shell

            app = QApplication([sys.argv[0]])
            platform_name = app.platformName().casefold()
            if platform_name != "offscreen":
                raise RuntimeError("offscreen shell smoke did not obtain the offscreen Qt platform")
            app.setOrganizationName("SDR Native Monitoring")
            app.setOrganizationDomain("local.sdr-native-monitoring")
            app.setApplicationName("SDR Native Monitoring")
            if default_composition:
                from .services import build_default_sdr_services
                from .services.native_live import NativeLiveSessionService

                services = build_default_sdr_services()
                shell = build_v2_shell(services)
                live_service = services.live_sdr
                if not isinstance(live_service, NativeLiveSessionService):
                    raise RuntimeError("default offscreen shell did not admit native Live composition")
                native_info = dict(live_service._native.build_info())
                if not bool(native_info.get("pluto_compiled")):
                    raise RuntimeError("default offscreen shell native module lacks Pluto support")
                native_state = {
                    "native_device_constructed": live_service._native_device is not None,
                    "native_engine_constructed": live_service._engine is not None,
                    "pluto_compiled": True,
                }
            else:
                from .services import build_offscreen_smoke_sdr_services

                services = build_offscreen_smoke_sdr_services()
                shell = build_v2_shell(services)
            shell.show()
            app.processEvents()
            visible_after_show = shell.isVisible()
            active_workspace = shell.active_workspace_id
            window_title = shell.windowTitle()
            automatic_discovery_pending = bool(shell._context.automatic_discovery_enabled)
            live_running = bool(services.live_sdr.is_running())
            shell.close()
            app.processEvents()
            closed = not shell.isVisible()
            shell.deleteLater()
            app.processEvents()
            app.quit()
    finally:
        if previous_qt_platform is None:
            os.environ.pop("QT_QPA_PLATFORM", None)
        else:
            os.environ["QT_QPA_PLATFORM"] = previous_qt_platform
        if previous_auto_discover is None:
            os.environ.pop("SDR_AUTO_DISCOVER", None)
        else:
            os.environ["SDR_AUTO_DISCOVER"] = previous_auto_discover
        if previous_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous_local_app_data

    if shell is None or services is None or not visible_after_show or active_workspace != "analyzer":
        raise RuntimeError("offscreen shell smoke did not show the Analyzer AppShell")
    if automatic_discovery_pending or live_running or not closed:
        raise RuntimeError("offscreen shell smoke did not complete the no-device close lifecycle")
    if default_composition and (
        native_state.get("native_device_constructed") or native_state.get("native_engine_constructed")
    ):
        raise RuntimeError("default offscreen shell constructed a native receiver before Live")
    verdict: dict[str, object] = {
        "automatic_discovery_pending": automatic_discovery_pending,
        "closed": closed,
        "live_running": live_running,
        "live_service": type(services.live_sdr).__name__,
        "qt_platform": platform_name,
        "startup_visible": visible_after_show,
        "window_title": window_title,
        "workspace": active_workspace,
        "ui_mode": "v2",
        "shell_class": type(shell).__name__,
    }
    if default_composition:
        verdict.update(native_state)
    return verdict


def _size_window_to_work_area(window: object, app: object) -> None:
    """Fit the shell to ~80% of the primary work area (DPI-aware)."""
    from PySide6.QtWidgets import QApplication, QMainWindow

    screen = QApplication.primaryScreen() if QApplication.instance() else None
    if screen is None:
        return
    available = screen.availableGeometry()
    width = max(480, min(1280, int(available.width() * 0.8)))
    height = max(360, min(800, int(available.height() * 0.8)))
    if isinstance(window, QMainWindow):
        window.resize(width, height)


def _install_excepthook(logger: logging.Logger) -> None:
    """Route unhandled exceptions into the structured activity log."""

    def handle_exception(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_traceback: types.TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        logger.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = handle_exception


def _resolve_ui_mode(value: str) -> str:
    """UI V2 is the product default; standalone is an explicit rollback."""
    return "standalone" if value.strip().casefold() in {"standalone", "legacy"} else "v2"


def _requested_ui_mode(environment: Mapping[str, str]) -> str:
    return environment.get("SDR_UI_VERSION") or environment.get("SDR_UI_MODE") or "v2"


def _build_v2_shell():
    from .ui.v2_composition import build_v2_shell

    return build_v2_shell()


def main(argv: list[str] | None = None) -> int:
    """Start UI V2 on the current backend; DFL remains a separate product."""
    arguments = _arguments(list(sys.argv[1:] if argv is None else argv))
    if arguments.verify_native_artifact:
        print(json.dumps(_native_artifact_verdict(), sort_keys=True))
        return 0
    if arguments.offscreen_shell_smoke:
        print(json.dumps(_offscreen_shell_verdict(), sort_keys=True))
        return 0
    if arguments.offscreen_default_shell_smoke:
        print(json.dumps(_offscreen_default_shell_verdict(), sort_keys=True))
        return 0
    if arguments.verify_packaged_libiio_runtime:
        print(json.dumps(_packaged_libiio_runtime_verdict(), sort_keys=True))
        return 0
    if arguments.verify_packaged_tinysa_runtime:
        print(json.dumps(_packaged_tinysa_runtime_verdict(), sort_keys=True))
        return 0
    logger = _configure_logging()
    handler = install_activity_file_logging(logger)
    log_event(
        logger,
        "program",
        "activity_log_ready",
        path=str(handler.path),
        max_records=handler.max_records,
    )
    _install_excepthook(logger)
    requested_mode = _requested_ui_mode(os.environ)
    ui_mode = _resolve_ui_mode(requested_mode)

    from PySide6.QtWidgets import QApplication

    existing_app = QApplication.instance()
    app = existing_app if isinstance(existing_app, QApplication) else QApplication(sys.argv[:1])
    app.setOrganizationName("SDR Native Monitoring")
    app.setOrganizationDomain("local.sdr-native-monitoring")
    app.setApplicationName("SDR Native Monitoring")
    if ui_mode == "v2":
        shell = _build_v2_shell()
    else:
        from .ui.app_shell import SDRAppShell
        from .ui.design_tokens import ThemeId
        from .ui.themes import ThemeProvider

        ThemeProvider.apply(app, ThemeId.DARK)
        shell = SDRAppShell()
    _size_window_to_work_area(shell, app)
    shell.show()
    log_event(
        logger,
        "program",
        "application_started",
        mode=ui_mode,
        version=__version__,
        ui_mode=ui_mode,
    )
    logger.info("SDR Native Monitoring %s started", __version__)
    exit_code = app.exec()
    log_event(logger, "program", "application_stopped", exit_code=exit_code)
    handler.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
