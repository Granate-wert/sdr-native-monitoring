"""UI2-11A offscreen stability checks for every currently admitted V2 workspace."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.design import ThemeId
from tests.ui_v2.test_live_product_composition import (
    FakeCalibrationPresenter,
    FakeDiagnosticsPresenter,
    FakePresenter,
    FakeSweepPresenter,
)
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.shell import AppShellV2
from sdr_monitor.ui.v2.view_models.replay_view_model import ReplayPresenterFactory
from sdr_monitor.ui.v2.view_models.tinysa_view_model import (
    TinySaAnalyzerBindingFactory,
    TinySaSourceActivationPresenterFactory,
)


class AdmittedWorkspaceStabilityTests(unittest.TestCase):
    """Bounded fake stress, not hardware, frame-rate or visible-DPI evidence."""

    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_two_hundred_navigation_cycles_are_lazy_and_issue_no_presenter_command(self) -> None:
        live = FakePresenter()
        sweep = FakeSweepPresenter()
        calibration = FakeCalibrationPresenter()
        diagnostics = FakeDiagnosticsPresenter()
        factory_calls = 0
        tinysa_factory_calls = 0
        replay_factory_calls = 0

        def diagnostics_factory() -> FakeDiagnosticsPresenter:
            nonlocal factory_calls
            factory_calls += 1
            return diagnostics

        def tinysa_activation_factory() -> object:
            nonlocal tinysa_factory_calls
            tinysa_factory_calls += 1
            raise AssertionError("inert tinySA page must not create its activation presenter")

        def tinysa_analyzer_factory(_composed: object) -> object:
            raise AssertionError("inert tinySA page must not compose an analyzer")

        def replay_factory() -> object:
            nonlocal replay_factory_calls
            replay_factory_calls += 1
            raise AssertionError("inert replay V2 page must not create its presenter")

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
        with tempfile.TemporaryDirectory() as temporary_directory:
            shell = AppShellV2(
                context=composition.context,
                settings=QSettings(temporary_directory + "/ui2.ini", QSettings.Format.IniFormat),
            )
            try:
                shell.resize(1366, 768)
                shell.show()
                for _ in range(200):
                    for workspace_id in ("home", "live", "sweep", "calibration", "diagnostics", "replay", "tinysa"):
                        shell.select_workspace(workspace_id)
                self.app.processEvents()
                self.assertEqual(
                    shell.created_workspace_ids,
                    frozenset(("home", "live", "sweep", "calibration", "diagnostics", "replay", "tinysa")),
                )
                self.assertEqual(live.shutdown_calls, 0)
                self.assertEqual(live.discover_calls, 0)
                self.assertEqual(live.select_device_calls, 0)
                self.assertEqual(live.select_manual_uri_calls, 0)
                self.assertEqual(live.apply_configuration_calls, 0)
                self.assertEqual(live.start_calls, 0)
                self.assertEqual(live.stop_calls, 0)
                self.assertEqual(sweep.shutdown_calls, 0)
                self.assertEqual(calibration.shutdown_calls, 0)
                self.assertEqual(sweep.plan_calls, 0)
                self.assertEqual(sweep.execute_calls, 0)
                self.assertEqual(sweep.cancel_calls, 0)
                self.assertEqual(sweep.export_calls, 0)
                self.assertEqual(calibration.refresh_calls, 0)
                self.assertEqual(calibration.compare_calls, 0)
                self.assertEqual(factory_calls, 0)
                self.assertEqual(tinysa_factory_calls, 0)
                self.assertEqual(replay_factory_calls, 0)
                self.assertEqual(diagnostics.refresh_calls, 0)
                self.assertEqual(diagnostics.self_test_calls, 0)
                self.assertEqual(diagnostics.cancel_calls, 0)
                self.assertEqual(diagnostics.bundle_paths, [])
                self.assertTrue(all(button.accessibleName() for button in shell._nav_buttons.values()))
                shell.close()
                self.assertEqual(live.shutdown_calls, 1)
                self.assertEqual(sweep.shutdown_calls, 1)
                self.assertEqual(calibration.shutdown_calls, 1)
                self.assertEqual(diagnostics.shutdown_calls, 0)
                shell.close()
                self.assertEqual(live.shutdown_calls, 1)
                self.assertEqual(sweep.shutdown_calls, 1)
                self.assertEqual(calibration.shutdown_calls, 1)
                self.assertEqual(diagnostics.shutdown_calls, 0)
            finally:
                if shell.isVisible():
                    shell.close()
                shell.deleteLater()
                self.app.processEvents()

    def test_admitted_workspaces_render_offscreen_at_required_size_and_theme_matrix(self) -> None:
        """Smoke-render product pages; this is not visible-DPI or frame-rate evidence."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            evidence_directory = Path(temporary_directory)
            for theme in ThemeId:
                for width, height in ((1366, 768), (1920, 1080)):
                    live = FakePresenter()
                    sweep = FakeSweepPresenter()
                    calibration = FakeCalibrationPresenter()
                    diagnostics = FakeDiagnosticsPresenter()
                    tinysa_factory_calls = 0
                    replay_factory_calls = 0

                    def tinysa_activation_factory() -> object:
                        nonlocal tinysa_factory_calls
                        tinysa_factory_calls += 1
                        raise AssertionError("rendering tinySA V2 activation must stay inert")

                    def tinysa_analyzer_factory(_composed: object) -> object:
                        raise AssertionError("rendering tinySA V2 activation must not compose an analyzer")

                    def replay_factory() -> object:
                        nonlocal replay_factory_calls
                        replay_factory_calls += 1
                        raise AssertionError("rendering replay V2 page must stay inert")

                    composition = compose_v2_live_product(
                        live,
                        sweep_presenter=sweep,
                        calibration_presenter=calibration,
                        diagnostics_presenter_factory=lambda: diagnostics,
                        replay_presenter_factory=cast(ReplayPresenterFactory, replay_factory),
                        tinysa_activation_presenter_factory=cast(TinySaSourceActivationPresenterFactory, tinysa_activation_factory),
                        tinysa_analyzer_binding_factory=cast(TinySaAnalyzerBindingFactory, tinysa_analyzer_factory),
                        now_ns=lambda: 1,
                    )
                    shell = AppShellV2(
                        context=composition.context,
                        settings=QSettings(
                            f"{temporary_directory}/{theme.value}-{width}x{height}.ini",
                            QSettings.Format.IniFormat,
                        ),
                        theme=theme,
                    )
                    try:
                        shell.resize(width, height)
                        shell.show()
                        for workspace_id in ("home", "live", "sweep", "calibration", "diagnostics", "replay", "tinysa"):
                            shell.select_workspace(workspace_id)
                            self.app.processEvents()
                            image = shell.grab().toImage()
                            target = evidence_directory / f"{theme.value}-{width}x{height}-{workspace_id}.png"
                            self.assertFalse(image.isNull(), target.name)
                            self.assertEqual((image.width(), image.height()), (width, height), target.name)
                            self.assertTrue(image.save(str(target)), target.name)
                            self.assertGreater(target.stat().st_size, 1024, target.name)
                        self.assertEqual(tinysa_factory_calls, 0)
                        self.assertEqual(replay_factory_calls, 0)
                    finally:
                        shell.close()
                        shell.deleteLater()
                        self.app.processEvents()
