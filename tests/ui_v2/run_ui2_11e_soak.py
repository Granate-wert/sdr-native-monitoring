"""Bounded synthetic UI2-11E soak runner for already admitted V2 workspaces."""

# ruff: noqa: E402 -- direct script mode needs the project root before V2 imports.

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.shell import AppShellV2
from sdr_monitor.ui.v2.view_models.replay_view_model import ReplayPresenterFactory
from sdr_monitor.ui.v2.view_models.tinysa_view_model import (
    TinySaAnalyzerBindingFactory,
    TinySaSourceActivationPresenterFactory,
)
from tests.ui_v2.test_live_product_composition import (
    FakeCalibrationPresenter,
    FakeDiagnosticsPresenter,
    FakePresenter,
    FakeSweepPresenter,
)

_WORKSPACE_IDS = ("home", "live", "sweep", "calibration", "diagnostics", "replay", "tinysa")


@dataclass(frozen=True, slots=True)
class SoakResult:
    """Host-local synthetic run counters, deliberately not a performance claim."""

    duration_seconds: float
    navigation_cycles: int
    rendered_frames: int
    maximum_loop_gap_ms: float


def run_soak(*, duration_seconds: float, navigation_hz: float, render_hz: float) -> SoakResult:
    """Exercise inert V2 navigation and offscreen paint without a device or presenter command."""

    if duration_seconds <= 0.0 or navigation_hz <= 0.0 or render_hz <= 0.0:
        raise ValueError("duration_seconds, navigation_hz and render_hz must be positive")
    app = QApplication.instance() or QApplication([])
    live = FakePresenter()
    sweep = FakeSweepPresenter()
    calibration = FakeCalibrationPresenter()
    diagnostics = FakeDiagnosticsPresenter()
    diagnostics_factory_calls = 0
    tinysa_activation_factory_calls = 0
    replay_factory_calls = 0

    def diagnostics_factory() -> FakeDiagnosticsPresenter:
        nonlocal diagnostics_factory_calls
        diagnostics_factory_calls += 1
        return diagnostics

    def tinysa_activation_factory() -> object:
        nonlocal tinysa_activation_factory_calls
        tinysa_activation_factory_calls += 1
        raise AssertionError("inert tinySA V2 activation page must not create a presenter")

    def tinysa_analyzer_factory(_composed: object) -> object:
        raise AssertionError("inert tinySA V2 activation page must not compose an analyzer")

    def replay_factory() -> object:
        nonlocal replay_factory_calls
        replay_factory_calls += 1
        raise AssertionError("inert replay V2 page must not create a presenter")

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
    with tempfile.TemporaryDirectory(prefix="ui2-11e-soak-") as temporary_directory:
        shell = AppShellV2(
            context=composition.context,
            settings=QSettings(temporary_directory + "/ui2.ini", QSettings.Format.IniFormat),
        )
        try:
            shell.resize(1366, 768)
            shell.show()
            deadline = time.monotonic() + duration_seconds
            next_navigation = time.monotonic()
            next_render = next_navigation
            last_loop = next_navigation
            maximum_loop_gap = 0.0
            navigation_cycles = 0
            rendered_frames = 0
            workspace_index = 0
            while time.monotonic() < deadline:
                now = time.monotonic()
                maximum_loop_gap = max(maximum_loop_gap, now - last_loop)
                last_loop = now
                if now >= next_navigation:
                    shell.select_workspace(_WORKSPACE_IDS[workspace_index])
                    workspace_index = (workspace_index + 1) % len(_WORKSPACE_IDS)
                    navigation_cycles += 1
                    next_navigation = now + 1.0 / navigation_hz
                if now >= next_render:
                    _process_qt_events(app)
                    if shell.grab().isNull():
                        raise RuntimeError("offscreen shell render returned a null pixmap")
                    rendered_frames += 1
                    next_render = now + 1.0 / render_hz
                _process_qt_events(app)
                time.sleep(0.005)
            _assert_no_presenter_command(
                live,
                sweep,
                calibration,
                diagnostics,
                diagnostics_factory_calls,
                tinysa_activation_factory_calls,
                replay_factory_calls,
            )
            shell.close()
            _assert_exactly_once_shutdown(
                live,
                sweep,
                calibration,
                diagnostics,
                diagnostics_factory_calls,
                tinysa_activation_factory_calls,
                replay_factory_calls,
            )
            return SoakResult(
                duration_seconds=duration_seconds,
                navigation_cycles=navigation_cycles,
                rendered_frames=rendered_frames,
                maximum_loop_gap_ms=maximum_loop_gap * 1000.0,
            )
        finally:
            if shell.isVisible():
                shell.close()
            shell.deleteLater()
            _process_qt_events(app)


def _process_qt_events(app: QApplication) -> None:
    """Drain deferred deletes just as a running Qt event loop does between turns."""

    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _assert_no_presenter_command(
    live: FakePresenter,
    sweep: FakeSweepPresenter,
    calibration: FakeCalibrationPresenter,
    diagnostics: FakeDiagnosticsPresenter,
    diagnostics_factory_calls: int,
    tinysa_activation_factory_calls: int,
    replay_factory_calls: int,
) -> None:
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
            calibration.compare_calls,
            diagnostics_factory_calls,
            tinysa_activation_factory_calls,
            replay_factory_calls,
            diagnostics.refresh_calls,
            diagnostics.self_test_calls,
            diagnostics.cancel_calls,
            bool(diagnostics.bundle_paths),
        )
    ):
        raise RuntimeError("inert navigation issued a presenter command")


def _assert_exactly_once_shutdown(
    live: FakePresenter,
    sweep: FakeSweepPresenter,
    calibration: FakeCalibrationPresenter,
    diagnostics: FakeDiagnosticsPresenter,
    diagnostics_factory_calls: int,
    tinysa_activation_factory_calls: int,
    replay_factory_calls: int,
) -> None:
    if (live.shutdown_calls, sweep.shutdown_calls, calibration.shutdown_calls, diagnostics.shutdown_calls) != (1, 1, 1, 0):
        raise RuntimeError("existing product presenters did not shut down exactly once or deferred Diagnostics was created")
    if diagnostics_factory_calls != 0:
        raise RuntimeError("inert navigation created the deferred Diagnostics presenter")
    if tinysa_activation_factory_calls != 0:
        raise RuntimeError("inert navigation created the deferred tinySA activation presenter")
    if replay_factory_calls != 0:
        raise RuntimeError("inert navigation created the deferred Replay presenter")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=1800.0)
    parser.add_argument("--navigation-hz", type=float, default=10.0)
    parser.add_argument("--render-hz", type=float, default=1.0)
    arguments = parser.parse_args()
    result = run_soak(
        duration_seconds=arguments.seconds,
        navigation_hz=arguments.navigation_hz,
        render_hz=arguments.render_hz,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
