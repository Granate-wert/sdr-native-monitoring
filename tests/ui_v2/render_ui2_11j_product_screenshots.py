"""Render reproducible synthetic screenshots of every inert UI V2 workspace.

The output deliberately proves only source/offscreen presentation geometry.  It
uses injected fake presenters and rejects deferred factory use, so it neither
opens hardware nor stands in for visible Windows, real-frame or SDR evidence.
"""

# ruff: noqa: E402 -- direct execution must establish the repository import path first.

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, QSettings  # noqa: E402 - direct script bootstrap.
from PySide6.QtWidgets import QApplication  # noqa: E402 - direct script bootstrap.

from sdr_monitor.ui.v2.design import ThemeId  # noqa: E402 - direct script bootstrap.
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale  # noqa: E402 - direct script bootstrap.
from sdr_monitor.ui.v2.product_live import compose_v2_live_product  # noqa: E402 - direct script bootstrap.
from sdr_monitor.ui.v2.shell import AppShellV2  # noqa: E402 - direct script bootstrap.
from sdr_monitor.ui.v2.view_models.replay_view_model import ReplayPresenterFactory  # noqa: E402 - direct script bootstrap.
from sdr_monitor.ui.v2.view_models.tinysa_view_model import (
    TinySaAnalyzerBindingFactory,
    TinySaSourceActivationPresenterFactory,
)  # noqa: E402 - direct script bootstrap.
from tests.ui_v2.test_live_product_composition import (
    FakeCalibrationPresenter,
    FakeDiagnosticsPresenter,
    FakePresenter,
    FakeSweepPresenter,
)  # noqa: E402 - direct script bootstrap.


_WORKSPACE_IDS = ("home", "live", "sweep", "calibration", "diagnostics", "replay", "tinysa")
_LOGICAL_SIZES = ((1280, 720), (1366, 768), (1920, 1080))
_LOCALES = tuple(UiLocale)


def render_matrix(output_directory: Path) -> dict[str, object]:
    """Write the fixed fake/offscreen image matrix and return its immutable manifest."""

    output_directory.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    captures: list[dict[str, object]] = []
    previous_locale = current_locale()
    try:
        with tempfile.TemporaryDirectory(prefix="ui2-11j-settings-") as settings_directory:
            for locale in _LOCALES:
                for theme in ThemeId:
                    for width, height in _LOGICAL_SIZES:
                        shell, close_assertion = _make_inert_shell(locale, theme, settings_directory, width, height)
                        try:
                            shell.show()
                            _process_qt_events(app)
                            for workspace_id in _WORKSPACE_IDS:
                                shell.select_workspace(workspace_id)
                                _process_qt_events(app)
                                image = shell.grab().toImage()
                                target = (
                                    output_directory
                                    / f"ui2-v2-{locale.value}-{theme.value}-{width}x{height}-{workspace_id}.png"
                                )
                                if image.isNull() or (image.width(), image.height()) != (width, height):
                                    raise RuntimeError(f"invalid offscreen screenshot: {target.name}")
                                if not image.save(str(target)) or target.stat().st_size <= 1024:
                                    raise RuntimeError(f"could not persist offscreen screenshot: {target.name}")
                                captures.append(
                                    {
                                        "file": target.name,
                                        "locale": locale.value,
                                        "theme": theme.value,
                                        "logical_size": [width, height],
                                        "workspace": workspace_id,
                                        "inspector_hidden_for_narrow_width": shell.inspector_hidden_for_narrow_width,
                                        "bytes": target.stat().st_size,
                                    }
                                )
                        finally:
                            shell.close()
                            shell.deleteLater()
                            _process_qt_events(app)
                        close_assertion()
    finally:
        set_active_locale(previous_locale)

    manifest: dict[str, object] = {
        "evidence_kind": "synthetic_offscreen_ui_only",
        "claim_exclusions": [
            "visible_windows_dpi",
            "screen_reader",
            "real_spectrum_or_waterfall_frame",
            "recording",
            "performance_or_fps",
            "sdr_or_serial_hardware",
        ],
        "locales": [locale.value for locale in _LOCALES],
        "themes": [theme.value for theme in ThemeId],
        "logical_sizes": [list(size) for size in _LOGICAL_SIZES],
        "workspace_ids": list(_WORKSPACE_IDS),
        "capture_count": len(captures),
        "captures": captures,
    }
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _make_inert_shell(
    locale: UiLocale,
    theme: ThemeId,
    settings_directory: str,
    width: int,
    height: int,
) -> tuple[AppShellV2, Callable[[], None]]:
    live = FakePresenter()
    sweep = FakeSweepPresenter()
    calibration = FakeCalibrationPresenter()
    diagnostics = FakeDiagnosticsPresenter()
    factory_calls = {"diagnostics": 0, "tinysa": 0, "replay": 0}

    def diagnostics_factory() -> FakeDiagnosticsPresenter:
        factory_calls["diagnostics"] += 1
        return diagnostics

    def tinysa_activation_factory() -> object:
        factory_calls["tinysa"] += 1
        raise AssertionError("screenshot capture must not create a tinySA presenter")

    def tinysa_analyzer_factory(_composed: object) -> object:
        raise AssertionError("screenshot capture must not compose a tinySA analyzer")

    def replay_factory() -> object:
        factory_calls["replay"] += 1
        raise AssertionError("screenshot capture must not create a Replay presenter")

    composition = compose_v2_live_product(
        live,
        sweep_presenter=sweep,
        calibration_presenter=calibration,
        diagnostics_presenter_factory=diagnostics_factory,
        replay_presenter_factory=cast(ReplayPresenterFactory, replay_factory),
        tinysa_activation_presenter_factory=cast(TinySaSourceActivationPresenterFactory, tinysa_activation_factory),
        tinysa_analyzer_binding_factory=cast(TinySaAnalyzerBindingFactory, tinysa_analyzer_factory),
        now_ns=lambda: 1,
    )
    settings = QSettings(
        str(Path(settings_directory) / f"{locale.value}-{theme.value}-{width}x{height}.ini"),
        QSettings.Format.IniFormat,
    )
    settings.setValue("ui_v2/shell/locale", locale.value)
    shell = AppShellV2(
        context=composition.context,
        settings=settings,
        theme=theme,
    )
    shell.resize(width, height)

    def assert_clean_close() -> None:
        if factory_calls != {"diagnostics": 0, "tinysa": 0, "replay": 0}:
            raise AssertionError(f"deferred presenter factory called: {factory_calls}")
        if (live.shutdown_calls, sweep.shutdown_calls, calibration.shutdown_calls, diagnostics.shutdown_calls) != (1, 1, 1, 0):
            raise AssertionError("inert screenshot shell did not close owned presenters exactly once")
        if any(
            (
                live.discover_calls,
                live.select_device_calls,
                live.select_manual_uri_calls,
                live.apply_configuration_calls,
                live.start_calls,
                live.stop_calls,
                sweep.plan_calls,
                sweep.execute_calls,
                sweep.cancel_calls,
                sweep.export_calls,
                calibration.refresh_calls,
            )
        ):
            raise AssertionError("inert screenshot capture issued a public presenter command")

    return shell, assert_clean_close


def _process_qt_events(app: QApplication) -> None:
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", type=Path)
    arguments = parser.parse_args()
    manifest = render_matrix(arguments.output_directory)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
