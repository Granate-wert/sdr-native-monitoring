"""Discovery control feedback must not fabricate acquisition or empty results."""

from dataclasses import replace
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.state.analyzer_readouts import analyzer_status
from sdr_monitor.ui.v2.state.analyzer_status_cadence import AnalyzerStatusCadence
from sdr_monitor.ui.v2.view_models.live_view_model import LiveViewModel
from tests.ui_v2.test_app02_live_command_errors import Presenter
import tests.ui_v2.test_app02_analyzer_readouts as readouts_fixture
import tests.test_app02_analyzer_workspace_product as product_fixture


class DiscoveryFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_active_locale, current_locale())
        self.presenter = Presenter()
        self.model = LiveViewModel(self.presenter, now_ns=lambda: 10)
        self.addCleanup(self.model.dispose)

    def test_initial_and_malformed_results_are_not_empty_searches(self):
        self.assertIsNone(self.model.state.discovery_count)
        for payload in ("not a list", None, (object(),), (SimpleNamespace(device_id=""),)):
            self.presenter.devices_discovered.emit(payload)
            self.assertIsNone(self.model.state.discovery_count)
        self.presenter.devices_discovered.emit(())
        self.assertEqual(self.model.state.discovery_count, 0)

    def test_request_result_and_next_command_keep_measurement_identity(self):
        snapshot = readouts_fixture.AnalyzerReadoutTests().state().live.snapshot
        self.presenter.snapshot_changed.emit(snapshot)
        self.assertTrue(self.model.discover_devices())
        self.presenter.busy_changed.emit(True)
        self.assertTrue(self.model.state.discovery_pending)
        self.assertFalse(self.model.discover_devices())
        self.assertEqual(self.presenter.calls, ["discover"])
        self.presenter.devices_discovered.emit((SimpleNamespace(device_id="test-source"),))
        self.assertTrue(self.model.state.discovery_pending)
        self.presenter.busy_changed.emit(False)
        self.assertFalse(self.model.state.discovery_pending)
        self.assertEqual(self.model.state.discovery_count, 1)
        self.assertIs(self.model.state.snapshot, snapshot)
        self.model.refresh_presentation()
        self.assertEqual(self.model.state.discovery_count, 1)
        self.model.select_manual_uri("ip:test-source")
        self.assertIsNone(self.model.state.discovery_count)
        self.assertIs(self.model.state.snapshot, snapshot)

    def test_failed_or_aborted_request_does_not_report_zero_devices(self):
        self.model.discover_devices()
        self.presenter.busy_changed.emit(True)
        self.presenter.task_failed.emit("discovery failed")
        self.presenter.busy_changed.emit(False)
        self.assertFalse(self.model.state.discovery_pending)
        self.assertIsNone(self.model.state.discovery_count)
        self.assertEqual(self.model.state.error_label, "discovery failed")
        self.model.discover_devices()
        self.presenter.busy_changed.emit(True)
        self.presenter.busy_changed.emit(False)
        self.assertFalse(self.model.state.discovery_pending)
        self.assertIsNone(self.model.state.discovery_count)

    def test_synchronous_dispatch_failure_is_visible_and_does_not_stick(self):
        with patch.object(self.presenter, "discover_devices", side_effect=RuntimeError("closed")):
            self.assertFalse(self.model.discover_devices())
        self.assertEqual(self.model.state.error_label, "closed")
        self.assertFalse(self.model.state.discovery_pending)
        self.assertFalse(self.model.state.busy)

    def test_labels_and_urgent_cadence_preserve_retained_frame_and_error(self):
        base = replace(readouts_fixture.AnalyzerReadoutTests().state(), running=False)
        for locale in (UiLocale.RU, UiLocale.EN):
            set_active_locale(locale)
            searching = replace(base, live=replace(base.live, busy=True, discovery_pending=True))
            empty = replace(base, live=replace(base.live, discovery_count=0))
            found = replace(base, live=replace(base.live, discovery_count=2))
            generic = replace(base, live=replace(base.live, busy=True))
            for state, expected in ((searching, text("analyzer.discovering")),
                                    (empty, text("analyzer.discovery_empty")),
                                    (found, text("analyzer.discovery_found", count=2)),
                                    (generic, text("analyzer.busy"))):
                with self.subTest(locale=locale, expected=expected):
                    cadence = AnalyzerStatusCadence()
                    self.assertTrue(cadence.admit(base, 0))
                    self.assertTrue(cadence.admit(state, .001))
                    self.assertIn(expected, analyzer_status(state))
                    self.assertIs(state.bundle, base.bundle)
            self.assertNotIn(text("analyzer.stopped_last"), analyzer_status(searching))
            self.assertIn(text("analyzer.retained_frame"), analyzer_status(searching))
            failed = replace(searching, error="discovery failed")
            self.assertIn(text("analyzer.failed_last"), analyzer_status(failed))
            self.assertNotIn(text("analyzer.discovering"), analyzer_status(failed))


class ProductDiscoveryFeedbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_actual_presenter_delayed_search_keeps_ui_responsive_and_reports_empty(self):
        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        release = threading.Event()
        entered = threading.Event()
        calls = []

        def discover():
            calls.append("discover")
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test discovery barrier expired")
            return ()

        timer = QTimer()
        beats = []
        timer.setInterval(1)
        timer.timeout.connect(lambda: beats.append(1))
        try:
            with patch.object(fixture.live, "discover_devices", side_effect=discover):
                page = fixture.page
                initial_height = page.status.height()
                initial_width = page.primary.width()
                timer.start()
                page.discover.click()
                fixture.wait(lambda: entered.is_set() and len(beats) >= 3)
                self.assertEqual(calls, ["discover"])
                self.assertNotIn(text("analyzer.idle"), page.status.text())
                self.assertIn(text("analyzer.discovering"), page.status.text())
                self.assertFalse(page.discover.isEnabled())
                self.assertFalse(page.primary.isEnabled())
                self.assertFalse(page.model.discover_devices())
                self.assertIsNone(page.model.state.bundle)
                self.assertEqual(page.status.height(), initial_height)
                self.assertEqual(page.primary.width(), initial_width)
                release.set()
                fixture.wait(lambda: not page.model.state.live.busy)
                self.assertIn(text("analyzer.discovery_empty"), page.status.text())
                self.assertEqual(page.source.count(), 1)
                self.assertTrue(page.discover.isEnabled())
                self.assertFalse(page.primary.isEnabled())
                self.assertFalse(fixture.live.is_running())
        finally:
            release.set()
            timer.stop()
            fixture.wait(lambda: not fixture.page.model.state.live.busy)
            fixture.tearDown()
            fixture.doCleanups()
