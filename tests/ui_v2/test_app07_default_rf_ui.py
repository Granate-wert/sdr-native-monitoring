"""Actual default V2 shell/presenters/owners; fake physical SDK/serial only."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import fields, replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.hackrf_live import HackrfConfigurationPatch
from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.domain.recording import RecordingState
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.workspaces.rf_shift_dialog import RfImpactDialog, RfShiftEntryDialog
from sdr_monitor.ui.v2_composition import build_v2_shell

from tests.test_s15_live_rx_bridge import _make_frame
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_graph
from tests.ui_v2.test_app06_hackrf_sweep_common_analyzer import FakeHackrfSweep
from tests.ui_v2.test_app06_hackrf_persistence import enable_fake_density
from tests.ui_v2.test_app06_tinysa_runtime_settings import settings_graph, full_plan
from sdr_monitor.domain.tinysa_settings import TinySaInputMode
from tests.ui_v2.test_app07_ad936x_sweep_pane_owner import ad_sweep_graph
import tests.ui_v2.test_app07_default_rf_profile as profiles
from tests.ui_v2.test_app07_rf_gesture import middle_move, plot_points


class DefaultRfUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate, timeout=6):
        until = monotonic() + timeout
        while monotonic() < until:
            self.app.processEvents()
            if predicate():
                return
            sleep(.003)
        self.fail("bounded default Analyzer RF observation timed out")

    @contextmanager
    def product(self, family="ad"):
        if family == "ad":
            native, fake, g = ad_sweep_graph()
            services = g.services
        elif family == "hf":
            g = hackrf_graph()
            g.native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = 1
            enable_fake_density(g)
            native, fake = g.native, FakeHackrfSweep()
            fake.preflight = Mock()  # Inert fake SDK capability boundary.
            services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog,
                analyzer_hackrf=g.hackrf, analyzer_hackrf_sweep=fake)
        else:
            g = settings_graph()
            native, fake = g.native, g.instrument
            services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog, analyzer_tinysa=g.instrument)
        services.sweep = Mock()
        services.calibration = Mock()
        services.diagnostics = Mock()
        services.replay = Mock()
        captured, qt_errors = [], []

        def capture(*args, **kwargs):
            composition = compose_v2_live_product(*args, **kwargs)
            captured.append(composition)
            return composition

        previous_locale = current_locale()
        with TemporaryDirectory() as temporary, patch("sys.excepthook", side_effect=lambda *args: qt_errors.append(args)), \
             patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture), \
             patch("sdr_monitor.ui.v2.shell.app_shell.QSettings", return_value=QSettings(
                 str(Path(temporary) / "rf.ini"), QSettings.Format.IniFormat)), \
             patch("sdr_monitor.ui.v2.waterfall.spectrum_view.QSettings", return_value=QSettings(
                 str(Path(temporary) / "rf.ini"), QSettings.Format.IniFormat)):
            shell = build_v2_shell(services)
            shell.resize(1366, 900)
            shell.show()
            c = captured[0]
            page = shell._workspace_pages["analyzer"]
            product = SimpleNamespace(shell=shell, c=c, page=page, native=native, fake=fake, g=g,
                                      application=c._presenter._use_cases, controller=page.model.rf_controller)
            try:
                page.discover.click()
                self.wait(lambda: page.source.count() > 1 and not c.view_model.state.busy)
                choice = next(item for item in c.view_model.source_selection.choices
                              if item.family.value == {"ad": "ad936x", "hf": "hackrf", "ts": "tinysa"}[family])
                page.source.setCurrentIndex(page.source.findData(choice.device_id))
                self.wait(lambda: c.view_model.source_selection.selected_id == choice.device_id
                          and not c.view_model.state.busy)
                if family == "ad":
                    page.model.apply_configuration(profiles.DefaultRfProfileTests.rich_ad(None))
                    self.wait(lambda: c.view_model.state.has_applied_configuration and not c.view_model.state.busy)
                    page.model.resolve_configuration_request()  # Full injected profile readback ACK.
                elif family == "hf":
                    request = profiles.DefaultRfProfileTests.rich_hf(SimpleNamespace(sources={"hf": c.view_model.source_selection}))
                    page.model.stage_hackrf_configuration(HackrfConfigurationPatch(
                        request, c.view_model.source_selection.revision, c.view_model.state.snapshot.generation))
                    self.wait(lambda: page.model.state.rtbw_profile_ready and not c.view_model.state.busy)
                self.assertIsNotNone(product.controller)
                yield product
            finally:
                page.model.stop()
                self.wait(lambda: not c.view_model.state.busy and not c.analyzer_presenter.is_starting
                          and not c.analyzer_presenter.is_stopping and not product.controller.pending)
                if page.model.state.running or page.model.state.stop_required:
                    page.model.stop()
                    self.wait(lambda: not page.model.state.running and not page.model.state.stop_required
                              and not c.view_model.state.busy and not product.controller.pending)
                self.assertFalse(product.controller.fault)
                shell.close()
                self.wait(lambda: shell._is_closed)
                c.shutdown()
                shell.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.app.processEvents()
                set_active_locale(previous_locale)
                self.assertEqual(qt_errors, [], "Qt callback exceptions cannot be hidden by an RF test")

    def publish_ad(self, p, sequence):
        snapshot = p.application.current_snapshot()
        configuration = snapshot.applied.applied
        fft = configuration.fft_size
        frame = _make_frame(sequence, fft_size=fft, hop_size=round(fft * (1 - configuration.overlap_ratio)),
            center_hz=configuration.center_hz, sample_rate_hz=configuration.sample_rate_hz,
            source_id=snapshot.device.device_id, config_generation=snapshot.active_config_generation)
        frame.frequencies_hz = configuration.center_hz + (np.arange(fft) - fft / 2) * configuration.sample_rate_hz / fft
        p.native.engines[-1].frames.append(frame)
        self.wait(lambda: all(pane.last_bundle is not None and pane.last_bundle.spectrum.sequence == sequence
                             for pane in p.page._panes[:p.page.shared_views.currentData()]))

    def start_rtbw(self, p, *, views=1):
        p.page.shared_views.setCurrentIndex(p.page.shared_views.findData(views))
        self.assertTrue(p.page.model.start())
        self.wait(lambda: p.page.model.state.running and not p.c.view_model.state.busy)
        if p.page.model.state.ad936x_controls_available:
            self.publish_ad(p, 1)
        else:
            self.wait(lambda: p.page.rf_shift.isEnabled())
        self.wait(lambda: p.page.rf_shift.isEnabled())

    def preview(self, p, delta=5e6):
        ui = p.page._rf_controls
        pane = p.page._selected_pane()
        completions = []
        p.c._presenter.rf_command_ready.connect(completions.append)
        try:
            ui.request(pane, delta, ui.raw_anchor(pane))
            try:
                self.wait(lambda: isinstance(ui.dialog, RfImpactDialog))
            except AssertionError:
                self.fail(f"RF preview unavailable: phase={p.controller.phase}, "
                          f"error={p.controller.error}, anchor={ui.raw_anchor(pane)}, completions={completions}")
        finally:
            p.c._presenter.rf_command_ready.disconnect(completions.append)
        return ui.dialog

    def assert_preserved(self, before, after, changed):
        for field in fields(before):
            if field.name not in changed:
                self.assertEqual(getattr(before, field.name), getattr(after, field.name), field.name)

    def test_common_middle_release_and_numeric_default_cancel_are_inert(self):
        with self.product() as p:
            self.start_rtbw(p)
            before = p.application.capture_rf_context()
            for plot in (p.page.visualization.spectrum_scene, p.page.visualization.waterfall_pane):
                graphics, first, last = plot_points(plot, -60)
                QTest.mousePress(graphics.viewport(), Qt.MouseButton.MiddleButton, pos=first)
                middle_move(graphics, last)
                self.assertIsNone(p.page._rf_controls.dialog)
                self.publish_ad(p, 2)  # A new sequence/array must NOT cancel the held RF intent.
                QTest.mouseRelease(graphics.viewport(), Qt.MouseButton.MiddleButton, pos=last)
                self.wait(lambda: isinstance(p.page._rf_controls.dialog, RfImpactDialog))
                dialog = p.page._rf_controls.dialog
                self.assertTrue(dialog.cancel_button.isDefault())
                self.assertFalse(dialog.confirm_button.autoDefault())
                QTest.keyClick(dialog, Qt.Key.Key_Return)
                self.wait(lambda: not p.controller.pending)
                self.assertTrue(before.matches(p.application.capture_rf_context()))
            p.page.rf_shift.click()
            self.assertIsInstance(p.page._rf_controls.dialog, RfShiftEntryDialog)
            p.page._rf_controls.dialog.offset.setValue(5)
            p.page._rf_controls.dialog.accept()
            self.wait(lambda: isinstance(p.page._rf_controls.dialog, RfImpactDialog))
            p.page._rf_controls.dialog.reject()
            self.wait(lambda: not p.controller.pending)
            self.assertTrue(before.matches(p.application.capture_rf_context()))

    def test_four_view_receipt_clears_all_histories_before_actual_rtbw_start(self):
        with self.product() as p:
            self.start_rtbw(p, views=4)
            before = p.application.capture_rf_context()
            observed = []
            original = p.application.start_rf_rtbw

            def start(receipt, **kwargs):
                observed.append(tuple((pane.last_bundle, pane._last_waterfall, pane._last_persistence,
                                       pane.spectrum_scene.latest_frame) for pane in p.page._panes))
                return original(receipt, **kwargs)

            dialog = self.preview(p)
            self.assertIn("1, 2, 3, 4", dialog.details.toPlainText())
            self.assertFalse(p.page.shared_views.isEnabled())
            with patch.object(p.application, "start_rf_rtbw", side_effect=start):
                dialog.confirm_button.click()
                self.wait(lambda: not p.controller.pending and p.page.model.state.running)
            self.assertEqual(observed, [((None, None, None, None),) * 4])
            after = p.application.capture_rf_context()
            self.assertGreater(after.acquisition_epoch, before.acquisition_epoch)
            self.assert_preserved(before.request, after.request, {"center_hz"})
            self.assertEqual(after.request.center_hz, before.request.center_hz + 5e6)
            self.assertIn("PROFILE_OR_RF_PLAN_CHANGE", p.page.rf_shift.toolTip())
            self.publish_ad(p, 3)

    def test_stopped_apply_only_arms_exact_request_and_retires_old_live_layers(self):
        with self.product() as p:
            self.start_rtbw(p, views=2)
            old_state = p.c.view_model.state
            p.page.primary.click()
            self.wait(lambda: not p.page.model.state.running and not p.c.view_model.state.busy)
            starts = sum(engine.start_calls for engine in p.native.engines)
            dialog = self.preview(p)
            self.assertEqual(dialog.confirm_button.text(), text("analyzer.rf.confirm_apply"))
            dialog.confirm_button.click()
            self.wait(lambda: p.controller.armed and not p.c.view_model.state.busy)
            self.assertEqual(sum(engine.start_calls for engine in p.native.engines), starts)
            p.c.view_model._on_prepared_snapshot(replace(old_state, snapshot=p.c.view_model.state.snapshot))
            self.assertIsNone(p.page.model.state.bundle)
            self.assertTrue(all(pane._last_waterfall is None and pane.last_bundle is None for pane in p.page._panes))
            self.assertTrue(p.page.primary.isEnabled())
            p.page.primary.click()
            self.wait(lambda: not p.controller.pending and p.page.model.state.running)
            self.assertEqual(sum(engine.start_calls for engine in p.native.engines), starts + 1)

    def test_hackrf_running_change_keeps_both_gains_and_full_non_ui_profile(self):
        with self.product("hf") as p:
            self.start_rtbw(p, views=2)
            before = p.application.capture_rf_context()
            self.preview(p, 3e6).confirm_button.click()
            self.wait(lambda: not p.controller.pending and p.page.model.state.running)
            after = p.application.capture_rf_context()
            self.assertEqual(after.request.center_frequency_hz, before.request.center_frequency_hz + 3e6)
            self.assert_preserved(before.request, after.request, {"center_frequency_hz", "configuration_generation"})
            self.assertGreater(after.acquisition_epoch, before.acquisition_epoch)

    def test_all_three_sweep_families_deliver_stop_before_gui_receipt_and_new_start(self):
        for family in ("ad", "hf", "ts"):
            with self.subTest(family=family), self.product(family) as p:
                page = p.page
                page.shared_views.setCurrentIndex(page.shared_views.findData(2))
                if family != "ts":
                    self.assertTrue(page.model.select_mode("sweep"))
                selection = page.model.state.source_selection
                if family == "ad":
                    request = ContinuousSweepPlanRequest(100e6, 220e6, analysis_bins_per_usable_window=4096,
                        acquisition_buffer_samples=32768, segment_frame_timeout_ms=2345, line_snapshot_rate_hz=75)
                elif family == "hf":
                    request = HackrfSweepRequest(selection.selected, selection.revision,
                                                100_000_000, 220_000_000, 2048, 16, 24, 37)
                else:
                    request = TinySaSweepRequest(selection.selected, selection.revision,
                        100_000_000, 300_000_000, points=3, timeout_s=91, repeat_until_stop=True, interval_s=.75,
                        settings=full_plan(), input_mode=TinySaInputMode.LOW, readback_settings=True, frontend_chain="coax-A")
                self.assertTrue(page.model.start(request))
                self.wait(lambda: page.rf_shift.isEnabled())
                before = p.application.capture_rf_context()
                order = []

                def terminal_gui(_snapshot):
                    if p.c.analyzer_presenter.is_stopping:
                        order.append("terminal Qt")

                original = p.c.analyzer_presenter._service.start_rf

                def start(receipt, **kwargs):
                    order.append("new Start")
                    self.assertTrue(all(pane.last_bundle is None and pane._last_sweep_snapshot is None
                                        and pane._last_waterfall is None for pane in page._panes))
                    return original(receipt, **kwargs)

                p.c.analyzer_presenter.prepared_snapshot_ready.connect(terminal_gui)
                with patch.object(p.c.analyzer_presenter._service, "start_rf", side_effect=start):
                    dialog = self.preview(p)
                    if family == "ts":
                        self.assertIn("settings.rbw_hz: 10000", dialog.details.toPlainText())
                        self.assertIn("input_mode: low", dialog.details.toPlainText())
                    dialog.confirm_button.click()
                    self.wait(lambda: not p.controller.pending and page.model.state.running)
                p.c.analyzer_presenter.prepared_snapshot_ready.disconnect(terminal_gui)
                self.assertEqual(order[-1], "new Start")
                self.assertIn("terminal Qt", order[:-1])
                after = p.application.capture_rf_context()
                self.assertEqual(after.request.start_hz, before.request.start_hz + 5_000_000)
                self.assertEqual(after.request.stop_hz, before.request.stop_hz + 5_000_000)
                self.assertGreater(after.request.epoch, before.request.epoch)
                self.assert_preserved(before.request, after.request, {"start_hz", "stop_hz", "epoch"})
                if family == "ad":
                    self.assertEqual(after.applied_live, before.applied_live)
                self.wait(lambda: all(pane.last_bundle is not None for pane in page._panes))

    def test_failure_of_any_view_receipt_bars_start_until_explicit_stop(self):
        with self.product() as p:
            self.start_rtbw(p, views=2)
            starts = sum(engine.start_calls for engine in p.native.engines)
            with patch.object(p.page._panes[1], "clear_shared_view", side_effect=RuntimeError("synthetic receipt failure")):
                self.preview(p).confirm_button.click()
                self.wait(lambda: p.controller.fault and not p.c.view_model.state.busy)
            self.assertEqual(sum(engine.start_calls for engine in p.native.engines), starts)
            self.assertFalse(p.page.model.start())
            self.assertFalse(p.c.can_close())
            self.assertTrue(p.page.primary.isEnabled())
            self.assertEqual(p.page.primary.text(), text("analyzer.stop"))
            p.page.primary.click()
            self.wait(lambda: not p.controller.pending and not p.controller.fault and not p.c.view_model.state.busy)

    def test_tinysa_active_preflight_is_same_healthy_full_profile_only_not_new_start_admission(self):
        with self.product("ts") as p:
            selection = p.page.model.state.source_selection
            request = TinySaSweepRequest(selection.selected, selection.revision,
                100_000_000, 300_000_000, points=3, repeat_until_stop=True, interval_s=.75, settings=full_plan(),
                input_mode=TinySaInputMode.LOW, readback_settings=True)
            p.page.model.start(request)
            self.wait(lambda: p.page.rf_shift.isEnabled())
            accepted = p.fake.instrument_run_identity.request
            shifted = replace(accepted, start_hz=105_000_000, stop_hz=305_000_000)
            with patch.object(p.g.catalog, "snapshot", side_effect=AssertionError("active RF cannot refresh catalog")):
                self.assertEqual(p.fake.preflight(shifted, selection).start_frequency_hz, 105_000_000)
                for changed in (replace(shifted, points=4), replace(shifted, timeout_s=92),
                        replace(shifted, epoch=shifted.epoch + 1), replace(shifted, frontend_chain="another-chain"),
                        replace(shifted, settings=replace(shifted.settings, rbw_hz=20_000))):
                    with self.subTest(changed=changed), self.assertRaises(LiveAdmissionRejected):
                        p.fake.preflight(changed, selection)
                with patch.object(p.fake, "_claimed", False), self.assertRaises(LiveAdmissionRejected):
                    p.fake.preflight(shifted, selection)
                publication = p.fake.poll_latest()
                with patch.object(p.fake, "poll_latest", return_value=replace(publication,
                        metrics=replace(publication.metrics, has_error=True, error="synthetic owner failure"))), \
                        self.assertRaises(LiveAdmissionRejected):
                    p.fake.preflight(shifted, selection)
                with self.assertRaises(LiveAdmissionRejected):
                    p.fake.start(shifted, selection)  # Healthy retained facts are NOT a second Start permit.
            self.assertEqual(len(p.g.serials), 1)
            p.page.model.stop()
            self.wait(lambda: not p.page.model.state.running and not p.page.model.state.stopping)
            with patch.object(p.g.catalog, "snapshot", wraps=p.g.catalog.snapshot) as snapshot:
                p.fake.preflight(shifted, selection)
                snapshot.assert_called_once_with()

    def test_submission_failure_finishes_each_gui_phase_and_keeps_explicit_stop_available(self):
        for phase, method in (("preview", "preview_rf_shift"), ("apply", "apply_rf_shift"),
                              ("ack", "acknowledge_rf_apply"), ("start", "start_rf_rtbw")):
            with self.subTest(phase=phase), self.product() as p:
                self.start_rtbw(p)
                starts = sum(engine.start_calls for engine in p.native.engines)
                dialog = None if phase == "preview" else self.preview(p)
                with patch.object(p.c._presenter, method, side_effect=RuntimeError("synthetic submission failure")):
                    if dialog is None:
                        ui = p.page._rf_controls
                        pane = p.page._selected_pane()
                        ui.request(pane, 5e6, ui.raw_anchor(pane))
                    else:
                        dialog.confirm_button.click()
                    self.wait(lambda: not p.controller.pending and not p.c.view_model.state.busy)
                self.assertEqual(sum(engine.start_calls for engine in p.native.engines), starts)
                self.assertEqual(p.controller.fault, phase != "preview")
                self.assertTrue(p.page.primary.isEnabled())
                self.assertEqual(p.page.primary.text(), text("analyzer.stop"))
                p.page.primary.click()
                self.wait(lambda: not p.controller.pending and not p.controller.fault and not p.c.view_model.state.busy)

    def test_user_stop_cancels_apply_ack_and_queued_start_without_late_restart(self):
        for method in ("apply_rf_shift", "acknowledge_rf_apply", "start_rf_rtbw"):
            with self.subTest(method=method), self.product() as p:
                self.start_rtbw(p)
                starts = sum(engine.start_calls for engine in p.native.engines)
                entered, release = Event(), Event()
                original = getattr(p.application, method)

                def delayed(*args, **kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("synthetic RF command barrier")
                    return original(*args, **kwargs)

                with patch.object(p.application, method, side_effect=delayed):
                    self.preview(p).confirm_button.click()
                    self.wait(entered.is_set)
                    try:
                        self.assertTrue(p.page.primary.isEnabled())
                        p.page.primary.click()
                    finally:
                        release.set()
                    self.wait(lambda: not p.controller.pending and not p.c.view_model.state.busy)
                self.assertEqual(sum(engine.start_calls for engine in p.native.engines), starts)
                self.assertFalse(p.controller.fault)
                self.assertFalse(p.page.model.state.running)

    def test_actual_executor_submission_and_snapshot_preparation_failure_never_strand_rf(self):
        for boundary in ("submit", "prepare"):
            with self.subTest(boundary=boundary), self.product() as p:
                self.start_rtbw(p)
                presenter = p.c._presenter
                starts = sum(engine.start_calls for engine in p.native.engines)
                if boundary == "submit":
                    ui, pane = p.page._rf_controls, p.page._selected_pane()
                    with patch.object(presenter._executor, "submit", side_effect=RuntimeError("synthetic executor failure")):
                        ui.request(pane, 5e6, ui.raw_anchor(pane))
                        self.assertFalse(p.controller.pending)
                        self.assertFalse(p.c.view_model.state.busy)
                else:
                    dialog = self.preview(p)
                    with patch.object(presenter, "_prepare", side_effect=RuntimeError("synthetic preparation failure")):
                        dialog.confirm_button.click()
                        self.wait(lambda: not p.controller.pending and not p.c.view_model.state.busy)
                    self.assertTrue(p.controller.fault)
                self.assertEqual(sum(engine.start_calls for engine in p.native.engines), starts)
                self.assertTrue(p.page.primary.isEnabled())
                p.page.primary.click()
                self.wait(lambda: not p.controller.pending and not p.controller.fault and not p.c.view_model.state.busy)

    def test_stopped_sweep_apply_arms_complete_host_intent_until_explicit_start(self):
        with self.product() as p:
            p.page.model.select_mode("sweep")
            request = ContinuousSweepPlanRequest(100e6, 220e6, analysis_bins_per_usable_window=4096,
                acquisition_buffer_samples=32768, segment_frame_timeout_ms=2345, line_snapshot_rate_hz=75)
            p.page.model.start(request)
            self.wait(lambda: p.page.rf_shift.isEnabled())
            p.page.primary.click()
            self.wait(lambda: not p.page.model.state.running and not p.page.model.state.stopping)
            before = p.application.capture_rf_context()
            with patch.object(p.c.analyzer_presenter._service, "start_rf",
                              wraps=p.c.analyzer_presenter._service.start_rf) as start:
                self.preview(p).confirm_button.click()
                self.wait(lambda: p.controller.armed and not p.c.view_model.state.busy)
                start.assert_not_called()
                self.assertFalse(p.page._rf_controls.disarm.isHidden())
                p.page.primary.click()
                self.wait(lambda: not p.controller.pending and p.page.model.state.running)
                start.assert_called_once()
            after = p.application.capture_rf_context()
            self.assertEqual(after.request.start_hz, 105e6)
            self.assert_preserved(before.request, after.request, {"start_hz", "stop_hz", "epoch"})

    def test_recording_race_refuses_before_stop_and_does_not_restart_rx(self):
        with self.product() as p:
            self.start_rtbw(p)
            before = p.application.capture_rf_context()
            dialog = self.preview(p)
            with patch.object(p.g.services.live_sdr, "native_recording_health",
                              return_value=SimpleNamespace(state=RecordingState.RECORDING)):
                dialog.confirm_button.click()
                self.wait(lambda: not p.controller.pending and not p.c.view_model.state.busy)
            self.assertTrue(before.matches(p.application.capture_rf_context()))
            self.assertEqual(sum(engine.request_stop_calls for engine in p.native.engines), 0)

    def test_stop_and_close_cancel_delayed_stop_without_late_apply_or_start(self):
        for action in ("stop", "close"):
            with self.subTest(action=action), self.product() as p:
                self.start_rtbw(p)
                entered, release = Event(), Event()
                original = p.application.stop_rf_rtbw
                starts = sum(engine.start_calls for engine in p.native.engines)
                ticks = []
                heartbeat = QTimer()
                heartbeat.setInterval(5)
                heartbeat.timeout.connect(lambda: ticks.append(monotonic()))

                def delayed(*args, **kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("synthetic Stop barrier")
                    return original(*args, **kwargs)

                with patch.object(p.application, "stop_rf_rtbw", side_effect=delayed):
                    self.preview(p).confirm_button.click()
                    self.wait(entered.is_set)
                    heartbeat.start()
                    self.wait(lambda: len(ticks) >= 3)
                    self.assertTrue(p.page.primary.isEnabled())
                    try:
                        p.page.primary.click() if action == "stop" else p.shell.close()
                        self.assertFalse(p.shell._is_closed)
                    finally:
                        release.set()
                    self.wait(lambda: not p.controller.pending and not p.c.view_model.state.busy)
                heartbeat.stop()
                self.assertEqual(sum(engine.start_calls for engine in p.native.engines), starts)
                self.assertFalse(p.page.model.state.running)
                self.assertFalse(p.controller.fault)

    def test_locale_and_navigation_cancel_inert_preview_without_rx_actions(self):
        with self.product() as p:
            self.start_rtbw(p)
            before = p.application.capture_rf_context()
            for locale in (UiLocale.EN, UiLocale.RU):
                dialog = self.preview(p)
                set_active_locale(locale)
                p.page.set_locale()
                self.assertIsNone(p.page._rf_controls.dialog)
                self.assertFalse(p.controller.pending)
                self.assertFalse(dialog.isVisible())
                self.assertTrue(before.matches(p.application.capture_rf_context()))
            self.preview(p)
            p.page.hide()
            self.assertFalse(p.controller.pending)
            self.assertTrue(before.matches(p.application.capture_rf_context()))
            p.page.show()


if __name__ == "__main__":
    unittest.main()
