"""UI2-10B fake/offscreen tinySA V2 activation and analyzer coverage."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from dataclasses import dataclass
from typing import Callable, cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.application.tinysa_analyzer import TinySaAnalyzerApplicationService
from sdr_monitor.application.tinysa_source_activation import TinySaSourceActivationApplicationService
from sdr_monitor.domain import LiveSessionState
from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_serial_trace_collector import TinySaScanRawRequest, TinySaTraceCollection
from sdr_monitor.services.tinysa_serial_version_probe import TinySaVersionObservation
from sdr_monitor.services.tinysa_source_composition import (
    TinySaIdentityAssurance,
    TinySaSourceCompositionService,
    TinySaTransportEndpoint,
    endpoint_identity_key,
)
from sdr_monitor.services.tinysa_sweep_settings_controller import (
    TinySaSettingsApplyResult,
    TinySaSettingsApplyStatus,
    TinySaSweepSettingsPlan,
)
from sdr_monitor.services.tinysa_trace_parser import TinySaSpectrumTrace
from sdr_monitor.ui.presenters.tinysa_analyzer_presenter import TinySaAnalyzerPresenter
from sdr_monitor.ui.presenters.tinysa_source_activation_presenter import TinySaSourceActivationPresenter
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.shell import AppShellV2
from sdr_monitor.ui.v2.view_models.tinysa_view_model import TinySaAnalyzerBinding
from sdr_monitor.ui.v2.workspaces import TinySaActivationWorkspaceV2, TinySaAnalyzerWorkspaceV2


class _Signal:
    def __init__(self) -> None:
        self._callbacks: list[Callable[..., None]] = []

    def connect(self, callback: Callable[..., None]) -> None:
        self._callbacks.append(callback)

    def disconnect(self, callback: Callable[..., None]) -> None:
        self._callbacks.remove(callback)

    def emit(self, *args: object) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


class _LivePresenter:
    def __init__(self) -> None:
        self.devices_discovered = _Signal()
        self.snapshot_changed = _Signal()
        self.task_failed = _Signal()
        self.busy_changed = _Signal()
        self.render_ready = _Signal()
        self.shutdown_calls = 0
        self.command_calls = 0

    def discover_devices(self) -> None:
        self.command_calls += 1

    def select_device(self, device_id: str) -> None:
        del device_id
        self.command_calls += 1

    def select_manual_uri(self, uri: str) -> None:
        del uri
        self.command_calls += 1

    def apply_configuration(self, configuration: object) -> None:
        del configuration
        self.command_calls += 1

    def start(self) -> None:
        self.command_calls += 1

    def stop(self) -> None:
        self.command_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class _Collector:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def collect(self, request: TinySaScanRawRequest) -> TinySaTraceCollection:
        self._calls.append("collect")
        step = (request.stop_frequency_hz - request.start_frequency_hz) // request.points
        values: np.ndarray = np.full(request.points, -108.0, dtype=np.float32)
        values[request.points // 2] = -72.0
        trace = TinySaSpectrumTrace(
            model=request.model,
            start_frequency_hz=float(request.start_frequency_hz),
            stop_frequency_hz=float(request.stop_frequency_hz),
            host_timestamp_ns=1,
            scanraw_zero_offset_db=174.0,
            frequencies_hz=request.start_frequency_hz
            + np.arange(request.points, dtype=np.float64) * float(step),
            values_dbm=values,
        )
        return TinySaTraceCollection(
            trace=trace,
            command_bytes=len(request.command),
            response_bytes_read=request.expected_frame_bytes,
            discarded_prefix_bytes=0,
            read_calls=1,
            zero_response_bytes=16,
            zero_read_calls=1,
            scanraw_zero_offset_db=174.0,
            elapsed_seconds=2.0,
            port_closed=True,
        )


class _Settings:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def apply(
        self,
        plan: TinySaSweepSettingsPlan,
        *,
        confirmation: str,
    ) -> TinySaSettingsApplyResult:
        del confirmation
        self._calls.append("settings")
        return TinySaSettingsApplyResult(
            status=TinySaSettingsApplyStatus.ACKNOWLEDGED_UNVERIFIED,
            commands=plan.commands,
            warnings=plan.warnings,
            command_acknowledgements=len(plan.commands),
            port_factory_invoked=True,
            port_closed=True,
        )


class _SourceBackend:
    """Fake-only backend.  It records phases but contains no serial transport."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.endpoint = TinySaTransportEndpoint(
            "COM31",
            endpoint_identity_key("ui2-10b-fake"),
            "tinySA USB CDC 1234abcd",
            TinySaIdentityAssurance.USB_SERIAL,
        )

    def discover_endpoints(self) -> tuple[TinySaTransportEndpoint, ...]:
        self.calls.append("discover")
        return (self.endpoint,)

    def probe_version(self, endpoint: TinySaTransportEndpoint) -> TinySaVersionObservation:
        self._check_endpoint(endpoint)
        self.calls.append("probe")
        return TinySaVersionObservation(
            TinySaModel.ULTRA,
            "tinySA4_v1.4-test",
            "sha256:" + "f" * 64,
        )

    def make_collector(self, endpoint: TinySaTransportEndpoint, model: TinySaModel) -> _Collector:
        self._check_endpoint(endpoint)
        if model is not TinySaModel.ULTRA:
            raise AssertionError("source model changed")
        self.calls.append("make_collector")
        return _Collector(self.calls)

    def make_settings_executor(self, endpoint: TinySaTransportEndpoint) -> _Settings:
        self._check_endpoint(endpoint)
        self.calls.append("make_settings")
        return _Settings(self.calls)

    def _check_endpoint(self, endpoint: TinySaTransportEndpoint) -> None:
        if endpoint.identity_key != self.endpoint.identity_key:
            raise AssertionError("source identity changed")


class _ActivationFactory:
    def __init__(self, backend: _SourceBackend) -> None:
        self.calls = 0
        self.presenters: list[TinySaSourceActivationPresenter] = []
        self._backend = backend

    def __call__(self) -> TinySaSourceActivationPresenter:
        self.calls += 1
        presenter = TinySaSourceActivationPresenter(
            TinySaSourceActivationApplicationService(
                TinySaSourceCompositionService(self._backend)
            )
        )
        self.presenters.append(presenter)
        return presenter


@dataclass(frozen=True, slots=True)
class _LiveSnapshot:
    state: LiveSessionState


class Ui2TinySaProductTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._settings_dir = tempfile.TemporaryDirectory()
        self.live = _LivePresenter()
        self.backend = _SourceBackend()
        self.activation_factory = _ActivationFactory(self.backend)
        self.analyzer_presenters: list[TinySaAnalyzerPresenter] = []

        def bind(composed: object) -> TinySaAnalyzerBinding:
            from sdr_monitor.services.tinysa_source_composition import TinySaComposedSource

            if not isinstance(composed, TinySaComposedSource):
                raise TypeError("test requires a composed tinySA source")
            presenter = TinySaAnalyzerPresenter(
                TinySaAnalyzerApplicationService(composed.collector, composed.settings_executor)
            )
            self.analyzer_presenters.append(presenter)
            return TinySaAnalyzerBinding(source=composed.verified, presenter=presenter)

        self.composition = compose_v2_live_product(
            self.live,
            tinysa_activation_presenter_factory=self.activation_factory,
            tinysa_analyzer_binding_factory=bind,
            now_ns=lambda: 1,
        )
        self.shell = AppShellV2(
            context=self.composition.context,
            settings=QSettings(
                os.path.join(self._settings_dir.name, "ui2.ini"),
                QSettings.Format.IniFormat,
            ),
        )
        self.shell.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        if self.shell.isVisible():
            self.shell.close()
        self.shell.deleteLater()
        self.app.processEvents()
        for presenter in (*self.activation_factory.presenters, *self.analyzer_presenters):
            presenter.shutdown()
            presenter.deleteLater()
        self._settings_dir.cleanup()

    def test_navigation_is_inert_then_explicit_four_phase_activation_registers_v2_analyzer(self) -> None:
        self.assertEqual(self.activation_factory.calls, 0)
        self.assertEqual(self.backend.calls, [])
        home = self.shell._workspace_pages["home"]
        home._tinysa_card.button.click()
        self.app.processEvents()
        workspace = cast(TinySaActivationWorkspaceV2, self.shell._workspace_pages["tinysa"])
        self.assertIsInstance(workspace, TinySaActivationWorkspaceV2)
        self.assertEqual(self.activation_factory.calls, 0)
        self.assertEqual(self.backend.calls, [])

        workspace._discover.click()
        self._wait(lambda: workspace._candidates.count() == 1)
        self.assertEqual(self.activation_factory.calls, 1)
        self.assertEqual(self.backend.calls, ["discover"])
        workspace._select.click()
        self._wait(lambda: workspace._verify.isEnabled())
        workspace._verify.click()
        self._wait(lambda: workspace._compose.isEnabled())
        self.assertEqual(self.backend.calls, ["discover", "probe"])
        workspace._compose.click()
        self._wait(lambda: self.shell.active_workspace_id == "tinysa-analyzer")

        analyzer = cast(TinySaAnalyzerWorkspaceV2, self.shell._workspace_pages["tinysa-analyzer"])
        self.assertIsInstance(analyzer, TinySaAnalyzerWorkspaceV2)
        self.assertEqual(self.backend.calls, ["discover", "probe", "make_collector", "make_settings"])
        self.assertEqual(len(self.analyzer_presenters), 1)
        self.assertNotIn("com31", workspace._identity.text().casefold())
        self.assertEqual(self.live.command_calls, 0)

    def test_analyzer_scan_and_settings_review_are_explicit_and_keep_device_dBm_provenance(self) -> None:
        self._complete_activation()
        analyzer = cast(TinySaAnalyzerWorkspaceV2, self.shell._workspace_pages["tinysa-analyzer"])
        self.assertEqual(self.backend.calls, ["discover", "probe", "make_collector", "make_settings"])
        analyzer._acquire.click()
        self._wait(lambda: analyzer._scene.latest_frame is not None)
        self.assertEqual(self.backend.calls.count("collect"), 1)
        frame = analyzer._scene.latest_frame
        self.assertEqual(getattr(frame, "unit"), "dBm")
        self.assertLessEqual(getattr(frame, "values").size, 2 * analyzer._scene.width())
        self.assertEqual(analyzer._view_model.state.trace_result.presentation.source_point_count, 10_001)
        self.assertIn("не LPS", analyzer._trace_summary.text())

        analyzer._accuracy.setCurrentIndex(analyzer._accuracy.findData("fast"))
        analyzer._review_settings.click()
        self._wait(lambda: analyzer._apply_settings.isEnabled())
        self.assertEqual(self.backend.calls.count("settings"), 0)
        self.assertIn("Состояние остаётся непроверенным", analyzer._settings_detail.text())
        self.assertTrue(analyzer._view_model.confirm_settings(user_confirmed=False))
        self._wait(lambda: not analyzer._view_model.state.busy)
        self.assertEqual(self.backend.calls.count("settings"), 0)

    def test_close_refuses_active_analyzer_operation_then_releases_all_deferred_presenters_once(self) -> None:
        self._complete_activation()
        analyzer = cast(TinySaAnalyzerWorkspaceV2, self.shell._workspace_pages["tinysa-analyzer"])
        analyzer._view_model._on_busy_changed(True)
        self.shell.close()
        self.assertTrue(self.shell.isVisible())
        self.assertEqual(self.live.shutdown_calls, 0)
        analyzer._view_model._on_busy_changed(False)
        self.shell.close()
        self.assertEqual(self.live.shutdown_calls, 1)
        self.assertEqual(len(self.activation_factory.presenters), 1)

    def _complete_activation(self) -> None:
        home = self.shell._workspace_pages["home"]
        home._tinysa_card.button.click()
        self.app.processEvents()
        workspace = cast(TinySaActivationWorkspaceV2, self.shell._workspace_pages["tinysa"])
        workspace._discover.click()
        self._wait(lambda: workspace._candidates.count() == 1)
        workspace._select.click()
        self._wait(lambda: workspace._verify.isEnabled())
        workspace._verify.click()
        self._wait(lambda: workspace._compose.isEnabled())
        workspace._compose.click()
        self._wait(lambda: self.shell.active_workspace_id == "tinysa-analyzer")

    def _wait(self, predicate: Callable[[], bool], timeout_s: float = 2.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.002)
        raise AssertionError("timed out waiting for UI2-10B fake operation")


if __name__ == "__main__":
    unittest.main()
