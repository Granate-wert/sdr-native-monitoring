"""Actual V2 composition, cached fake owner only; no SDK/RX qualification."""
import os
from dataclasses import replace
from pathlib import Path
import tempfile
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.application.analyzer_session import AnalyzerPhase
from sdr_monitor.services.calibration_service import CalibrationService
from sdr_monitor.services.calibration_store import CalibrationProfileStore
from sdr_monitor.services.receiver_calibration import ReceiverCalibrationRegistry
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2_composition import build_v2_shell
from sdr_monitor.ui.v2.i18n import current_locale, set_active_locale
from tests import test_current_frame_calibration as frame_fixtures
from tests.test_live_calibration_owner import SnapshotPort


class _LivePort(SnapshotPort):
    def __init__(self, current):
        super().__init__(current)
        self.actions = []

    def is_running(self):
        return self.current.state is LiveSessionState.RUNNING

    def poll_frames(self):
        return []

    def start(self):
        self.actions.append("start")
        self.current = replace(self.current, state=LiveSessionState.RUNNING)
        return self.current

    def stop_and_wait(self, timeout_s=5.0):
        self.actions.append("stop")
        self.current = replace(self.current, state=LiveSessionState.CONNECTED)
        return self.current

    def stop(self):
        return self.stop_and_wait()


class CalibrationCompositionFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.facts = frame_fixtures.CurrentFrameCalibrationTests()
        self.facts.setUp()
        self.port = _LivePort(self.facts.current)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        original_locale = current_locale()
        self.addCleanup(set_active_locale, original_locale)
        # Actual persisted presentation contracts, isolated to this fixture's
        # INI. Never inherit or write the operator's shell/Waterfall preferences.
        settings = QSettings(str(Path(self.folder.name) / "presentation.ini"), QSettings.Format.IniFormat)
        for target in ("sdr_monitor.ui.v2.shell.app_shell.QSettings",
                       "sdr_monitor.ui.v2.waterfall.pane.QSettings",
                       "sdr_monitor.ui.v2.waterfall.spectrum_view.QSettings"):
            settings_patch = patch(target, return_value=settings)
            settings_patch.start()
            self.addCleanup(settings_patch.stop)
        self.store = CalibrationProfileStore(Path(self.folder.name))
        self.calibration = CalibrationService(self.store)
        self.registry = ReceiverCalibrationRegistry(self.store)
        self.services = SimpleNamespace(live_sdr=self.port, sweep=Mock(), calibration=self.calibration,
                                        receiver_calibration=self.registry, diagnostics=Mock(), replay=Mock())
        compositions = []

        def capture(*args, **kwargs):
            value = compose_v2_live_product(*args, **kwargs)
            compositions.append(value)
            return value
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture):
            self.shell = build_v2_shell(self.services)
        self.composition = compositions[0]
        self.model = self.composition.live_calibration_view_model
        self.presenter = self.model.presenter
        self.worker = self.composition._presenter
        # The supplied cached RUNNING fake publication represents an already
        # admitted owner run, not a UI construction-time Start. Keep its actual
        # application lifecycle consistent so normal Stop owns true cleanup.
        analyzer = self.presenter.owner._analyzer
        analyzer._state = replace(analyzer.state, phase=AnalyzerPhase.RUNNING)
        analyzer._start_dispatched = True
        self.frontend = self.facts.facts.frontend

    def wait(self, predicate, timeout=3):
        deadline = monotonic() + timeout
        while not predicate() and monotonic() < deadline:
            self.app.processEvents()
            sleep(.001)
        self.assertTrue(predicate())

    def bind(self):
        self.model.bind(self.frontend)
        self.wait(lambda: self.model.state.current is not None or self.model.state.error is not None)
        self.assertIsNone(self.model.state.error)
        self.wait(lambda: self.presenter._active is None and self.worker.calibration_worker_ready)

    def select_profile(self):
        profile = self.facts.profile(self.model.state.binding.signature)
        self.model.preview(profile)
        self.wait(lambda: not self.model.state.busy)
        self.assertIsNotNone(self.model.state.preview)
        self.model.select()
        self.wait(lambda: self.model.state.current is not None and self.model.state.acknowledged == "select")
        self.wait(lambda: self.presenter._active is None and self.worker.calibration_worker_ready)
        return profile

    def tearDown(self):
        self.model.close_binding()
        self.worker.stop()
        self.wait(lambda: not self.worker._pending_commands)
        # Normal asynchronous full owner cleanup, never an early widget delete.
        self.composition.request_shutdown()
        self.wait(lambda: self.composition.poll_shutdown().phase == "complete")
        self.shell.close()
        self.shell.deleteLater()
        self.app.processEvents()


class LiveCalibrationCompositionTests(CalibrationCompositionFixture):
    def test_construction_same_owner_registry_store_budget_worker_and_inert_browser(self):
        self.assertEqual(self.port.actions, [])
        self.assertEqual(self.port.reads, 0)
        self.assertIs(self.presenter.owner, self.worker._use_cases)
        self.assertIs(self.presenter.registry, self.registry)
        self.assertIs(self.presenter.registry.store, self.services.calibration.store)
        self.assertIs(self.presenter.budget, self.composition.allocation_budget)
        self.assertIs(self.presenter.worker, self.worker)
        self.shell.select_workspace("calibration")
        self.app.processEvents()
        browser = self.composition.calibration_view_model
        profile = self.facts.profile(self.facts.signature())
        self.store.save(profile)
        browser.refresh()
        self.wait(lambda: bool(browser.state.profiles) and not browser.state.busy)
        browser.select(profile)
        self.wait(lambda: not browser.state.busy)
        self.assertIsNone(self.model.state.binding)
        self.assertEqual(self.port.actions, [])

    def test_explicit_bind_preview_select_clear_none_and_raw_identity(self):
        self.bind()
        binding = self.model.state.binding
        self.assertEqual(binding.endpoint.selection.value, "rx1")
        self.assertEqual(self.model.state.current.frame.unit, "dBFS/bin")
        service = self.registry.for_device(self.facts.facts.device, binding.endpoint)
        receipt = service.selection_receipt()
        profile = self.facts.profile(binding.signature)
        self.model.preview(profile)
        self.wait(lambda: not self.model.state.busy)
        self.assertEqual(service.selection_receipt(), receipt)
        self.assertIsNone(self.model.state.acknowledged)  # Preview is not Select.
        self.model.select()
        self.wait(lambda: self.model.state.current is not None and self.model.state.acknowledged == "select")
        result = self.model.state.current.frame.publication.analytical
        self.assertEqual(result.result.unit, "dBm/bin")
        self.assertIs(result.raw, self.facts.frame)
        self.assertIs(self.port.current, self.facts.current)
        self.model.clear()
        self.wait(lambda: self.model.state.current is not None and self.model.state.acknowledged == "clear")
        self.assertEqual(self.model.state.current.frame.unit, "dBFS/bin")
        self.assertEqual(self.port.actions, [])

    def test_rx2_actual_metadata_not_misleading_caption_and_unavailable_store(self):
        frame = replace(self.facts.frame, receiver_id="RX2", source_id="actual-second-producer")
        self.port.current = replace(self.facts.current, spectrum=frame, receiver_id="RX2",
                                    active_source_id=frame.source_id,
                                    device=replace(self.facts.current.device, label="RX1 misleading caption"))
        self.bind()
        self.assertEqual(self.model.state.binding.endpoint.selection.value, "rx2")
        self.model.close_binding()
        # A custom protocol store never inherits a global/legacy store.
        self.services.calibration = Mock()
        captured = []
        def capture(*args, **kwargs):
            item = compose_v2_live_product(*args, **kwargs)
            captured.append(item)
            return item
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture):
            shell = build_v2_shell(self.services)
        other = captured[0]
        try:
            self.assertFalse(other.live_calibration_view_model.state.available)
            self.assertIsNone(other.live_calibration_view_model.presenter.registry)
            other.request_shutdown()
            self.wait(lambda: other.poll_shutdown().phase == "complete")
        finally:
            shell.close()
            shell.deleteLater()
            self.app.processEvents()
