"""Common V2 owner + actual presenter/canvas seams. Fake SDK; no physical RX."""

from __future__ import annotations

import os
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.application.analyzer_rtbw_router import AnalyzerRtbwRouter
from sdr_monitor.application.analyzer_session import AnalyzerSessionApplicationService
from sdr_monitor.application.analyzer_sources import AnalyzerSourceSelectionApplicationService
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.device_capabilities import (
    DeviceCapabilityBinding,
    DeviceFamily,
    build_device_capability_inventory,
)
from sdr_monitor.domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from sdr_monitor.domain.live import LiveAdmissionRejected, LiveConfiguration, LiveSessionState
from sdr_monitor.services.hackrf_activation_preflight import (
    HackrfActivationPreflightService,
    HackrfRuntimeIdentityProbe,
)
from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
from sdr_monitor.services.hackrf_capability_adapter import HACKRF_LIBHACKRF_ADAPTER_ID, HackrfBoardKind
from sdr_monitor.services.hackrf_product_live import HackrfProductLiveCoordinator
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.state.live_view_state import build_live_view_state
from sdr_monitor.ui.v2.workspaces.analyzer_hackrf_configuration import HackrfConfigurationBar
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.test_app01_analyzer_session import Sweep
from tests.test_app06_pluto_observation_catalog import _Native
from tests.test_app06_retained_capability_catalog import _Provider
from tests.test_r11m_hackrf_live_admission import _observed

SERIAL = (0, 0, 0x010961DC, 0x2B78454F)


class IdentityPort:
    def __init__(self, owner):
        self.owner = owner

    def probe(self):
        self.owner.probes += 1
        return HackrfRuntimeIdentityProbe(HackrfBoardKind.HACKRF_ONE, self.owner.serial)

    def close(self):
        self.owner.closes += 1
        if self.owner.close_fail:
            raise RuntimeError("PRIVATE SDK close")


class Control:
    def __init__(self, request):
        self.request = request
        self.sequence = 0
        self.stops = []
        self.stop_fail = False
        self.frame_change = None
        self.metrics_change = None
        self.metrics_thread_names = []

    def poll_spectrum_frames(self, _count):
        request = self.request
        self.sequence += 1
        frequencies = request.center_frequency_hz + (np.arange(request.fft_size) - request.fft_size / 2) * request.sample_rate_hz / request.fft_size
        frame = SimpleNamespace(source=SimpleNamespace(source_id=request.source_id),
            config_generation=request.configuration_generation, frame_sequence=self.sequence,
            timestamp_ns=time.monotonic_ns(), center_frequency_hz=request.center_frequency_hz,
            sample_rate_hz=request.sample_rate_hz, fft_size=request.fft_size, hop_size=request.hop_size,
            frequencies_hz=frequencies, values=np.full(request.fft_size, -80, dtype=np.float32),
            unit="DBFS_BIN", dropped_samples_before=0, dropped_iq_blocks_before=0,
            window=request.window, detector=request.detector, averaging_frames=request.averaging_frames,
            precision_mode="reference_f64", calibration_status="uncalibrated",
            dropped_fft_frames_before=0, quality_flags=1 << 13)
        if self.frame_change:
            self.frame_change(frame)
        return [frame]

    def metrics(self):
        import threading
        self.metrics_thread_names.append(threading.current_thread().name)
        metrics = SimpleNamespace(processing=SimpleNamespace(worker_failures=0,
            ingress=SimpleNamespace(samples_admitted=self.sequence * 131072, blocks_admitted=self.sequence,
                                    dropped_samples=0, loss_events=0, ready_depth=0),
            dsp=SimpleNamespace(dsp=SimpleNamespace(fft_frames_computed=self.sequence * 64, fft_frames_dropped=0),
                                source_sequence_discontinuities=0, source_sample_index_discontinuities=0,
                                source_timestamp_regressions=0, source_estimated_timestamp_blocks=self.sequence,
                                presentation=SimpleNamespace(dropped=0, pushed=self.sequence, depth=0))))
        if self.metrics_change:
            self.metrics_change(metrics)
        return metrics

    def stop(self, timeout):
        self.stops.append(timeout)
        return SimpleNamespace(complete=lambda: not self.stop_fail)


class Factory:
    def __init__(self):
        self.controls = []
        self.fail = False
        self.invalid = None
        self.change = None

    def create(self, permit):
        if self.fail:
            raise RuntimeError("PRIVATE factory")
        if self.invalid is not None:
            return self.invalid
        control = Control(permit.plan.request)
        control.frame_change = self.change
        self.controls.append(control)
        return control


def graph():
    native = _Native()
    native.routes = ("usb:fixture",)
    live = NativeLiveSessionService(native)
    snapshot, identity, _ = _observed()
    provider = _Provider(HACKRF_LIBHACKRF_ADAPTER_ID, DeviceFamily.HACKRF)
    provider.value = build_device_capability_inventory((snapshot,),
        runtimes=(provider.runtime,), bindings=(DeviceCapabilityBinding("source-hackrf", DeviceFamily.HACKRF,
            provider.adapter_id, snapshot, identity),))
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(live), provider),
                                     control_transaction=live.capability_control_transaction)
    observation = SimpleNamespace(probes=0, closes=0, serial=SERIAL, close_fail=False)
    preflight = HackrfActivationPreflightService(lambda: IdentityPort(observation))
    factory = Factory()
    coordinator = HackrfProductLiveCoordinator(factory)
    hackrf = HackrfAnalyzerService(native, live, catalog.snapshot, preflight, coordinator)
    sources = AnalyzerSourceSelectionApplicationService(catalog, live,
        control_transaction=lambda: analyzer.idle_control_operation())
    router = AnalyzerRtbwRouter(live, sources, hackrf)
    analyzer = AnalyzerSessionApplicationService(router, Sweep([]), start_live=router.start)
    application = LiveSessionApplicationService(live, analyzer=analyzer, sources=sources,
                                               rtbw=router, catalog_close=catalog.close)
    return SimpleNamespace(native=native, live=live, provider=provider, catalog=catalog,
        observation=observation, preflight=preflight, factory=factory, coordinator=coordinator,
        hackrf=hackrf, sources=sources, router=router, analyzer=analyzer, application=application)


def stage(g):
    selection = g.application.current_source_selection()
    request = HackrfLiveRequest(100e6, 20e6, 15_000_000, 16, 20, source_id=selection.selected_id)
    return g.application.stage_hackrf_configuration(HackrfConfigurationPatch(request, selection.revision,
        g.application.current_snapshot().generation))


class HackrfCommonOwnerTests(unittest.TestCase):
    def setUp(self):
        self.g = graph()
        self.g.application.discover()
        self.g.application.select_device("source-hackrf")

    def tearDown(self):
        self.g.observation.close_fail = False
        for control in self.g.factory.controls:
            control.stop_fail = False
        if self.g.coordinator._quarantined_control is None:
            self.g.application.shutdown()

    def wait(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("bounded HackRF completion timeout")
            time.sleep(.001)

    def test_stage_is_no_io_and_keeps_separate_gains_and_exact_binding(self):
        snapshot = stage(self.g)
        self.assertEqual((snapshot.hackrf_request.lna_gain_db, snapshot.hackrf_request.vga_gain_db), (16, 20))
        self.assertIs(snapshot.source_choice, self.g.sources.current().selected)
        self.assertIsNone(snapshot.device)
        self.assertIsNone(snapshot.applied)
        self.assertEqual(self.g.observation.probes, 0)
        self.assertEqual(self.g.factory.controls, [])
        self.assertEqual(snapshot.hackrf_request.sample_rate_hz, 20e6)

    def test_start_publishes_common_bundle_waterfall_and_explicit_stop_same_owner(self):
        stage(self.g)
        self.assertIs(self.g.application.start().state, LiveSessionState.RUNNING)
        self.wait(lambda: self.g.application.current_snapshot().spectrum is not None)
        snapshot = self.g.application.current_snapshot()
        state = build_live_view_state(snapshot)
        self.assertIs(state.analyzer_bundle.spectrum, snapshot.spectrum)
        self.assertIsNotNone(state.waterfall_line)
        self.assertFalse(state.waterfall_line.timestamp_known)
        self.assertIsNone(state.data_age_ms)
        self.assertEqual(snapshot.clock_domain, "host_steady_ns")
        self.assertIsNone(snapshot.receiver_id)
        self.assertEqual(snapshot.unit, "dBFS/bin")
        self.assertEqual((self.g.observation.probes, self.g.observation.closes), (1, 1))
        self.assertFalse(state.has_applied_configuration)
        self.wait(lambda: len(self.g.factory.controls[0].metrics_thread_names) > 0)
        self.assertEqual(set(self.g.factory.controls[0].metrics_thread_names), {"sdr-hackrf-analyzer"})
        stopped = self.g.application.stop()
        self.assertIsNone(stopped.error)
        self.assertFalse(stopped.stop_required)
        self.assertEqual(self.g.factory.controls[0].stops, [5000])
        self.assertIsNone(self.g.live._external_analyzer_owner)
        self.assertIsNone(self.g.hackrf._poller)

    def test_all_native_controls_and_catalog_block_without_mutation_while_foreign_owned(self):
        stage(self.g)
        self.g.application.start()
        before = self.g.live.latest_snapshot()
        commands = (self.g.live.start_admitted, self.g.live.start,
            lambda: self.g.live.apply_configuration(LiveConfiguration(100e6, 10e6)),
            self.g.live.acquire_native_sweep_lease,
            lambda: self.g.live.select_device("usb:fixture"), self.g.catalog.refresh,
            self.g.catalog.close, lambda: self.g.live.arm_native_recording(Mock()),
            lambda: self.g.live.start_native_recording_now(Mock()), self.g.application.discover,
            lambda: self.g.application.select_device(self.g.sources.current().choices[0].device_id))
        for command in commands:
            with self.subTest(command=command), self.assertRaises(RuntimeError):
                command()
        self.assertIs(self.g.live.latest_snapshot(), before)
        self.assertIsNone(self.g.live._native_recording_armed)

    def test_native_publication_rate_and_clock_counters_are_not_fft_drops_or_qt_fps(self):
        stage(self.g)
        self.g.application.start()
        control = self.g.factory.controls[0]
        def alter(metrics):
            dsp = metrics.processing.dsp
            dsp.presentation.dropped = 7
            dsp.source_sequence_discontinuities = 2
            dsp.source_sample_index_discontinuities = 3
            dsp.source_timestamp_regressions = 4
        control.metrics_change = alter
        self.wait(lambda: self.g.application.current_snapshot().performance.rate_observation_interval_s is not None)
        performance = self.g.application.current_snapshot().performance
        self.assertGreater(performance.spectrum_snapshot_rate_hz, 0)
        self.assertAlmostEqual(performance.analytical_fft_rate_hz, 64 * performance.spectrum_snapshot_rate_hz)
        self.assertAlmostEqual(performance.iq_sample_rate_hz, 131072 * performance.spectrum_snapshot_rate_hz)
        self.assertEqual(performance.snapshots_superseded, 7)
        self.assertEqual(performance.fft_frames_dropped, 0)
        self.assertEqual(performance.iq_samples_dropped, 0)
        self.assertEqual((performance.source_sequence_discontinuities,
                          performance.source_sample_index_discontinuities,
                          performance.source_timestamp_regressions), (2, 3, 4))
        self.assertGreater(performance.source_estimated_timestamp_blocks, 0)
        self.assertFalse(performance.hardware_overflow_counter_available)
        self.assertEqual(set(control.metrics_thread_names), {"sdr-hackrf-analyzer"})

    def test_stop_failure_keeps_exact_owner_and_explicit_retry_only(self):
        stage(self.g)
        self.g.application.start()
        control = self.g.factory.controls[0]
        control.stop_fail = True
        failed = self.g.application.stop()
        self.assertIs(failed.state, LiveSessionState.ERROR)
        self.assertTrue(failed.stop_required)
        token = self.g.live._external_analyzer_owner
        self.assertIsNotNone(token)
        with self.assertRaises(RuntimeError):
            self.g.catalog.refresh()
        with self.assertRaises(RuntimeError):
            self.g.application.start()
        self.assertEqual(len(self.g.factory.controls), 1)
        control.stop_fail = False
        self.assertIsNone(self.g.application.stop().error)
        self.assertEqual(control.stops, [5000, 5000])
        self.assertIsNone(self.g.live._external_analyzer_owner)

    def test_failed_identity_close_retains_lease_and_same_observer(self):
        stage(self.g)
        self.g.observation.close_fail = True
        failed = self.g.application.start()
        self.assertTrue(failed.stop_required)
        self.assertTrue(self.g.preflight.cleanup_pending)
        self.assertEqual(self.g.factory.controls, [])
        self.assertTrue(self.g.application.stop().stop_required)
        self.g.observation.close_fail = False
        self.assertIsNone(self.g.application.stop().error)
        self.assertEqual(self.g.observation.probes, 1)

    def test_mismatched_serial_never_calls_factory_and_requires_explicit_stop(self):
        stage(self.g)
        self.g.observation.serial = (0, 0, 1, 2)
        failed = self.g.application.start()
        self.assertTrue(failed.stop_required)
        self.assertNotIn("PRIVATE", failed.error)
        self.assertEqual(self.g.factory.controls, [])
        self.assertIsNone(self.g.application.stop().error)

    def test_factory_failure_no_automatic_retry(self):
        stage(self.g)
        self.g.factory.fail = True
        self.assertTrue(self.g.application.start().stop_required)
        self.assertEqual(self.g.observation.probes, 1)
        self.assertIsNone(self.g.application.stop().error)
        self.assertIsNone(self.g.live._external_analyzer_owner)

    def test_invalid_control_is_retained_and_quarantined_without_guessed_close(self):
        stage(self.g)
        bad = SimpleNamespace(close=Mock())
        self.g.factory.invalid = bad
        self.assertTrue(self.g.application.start().stop_required)
        self.assertIs(self.g.coordinator._quarantined_control, bad)
        self.assertTrue(self.g.application.stop().stop_required)
        bad.close.assert_not_called()
        self.assertIsNotNone(self.g.live._external_analyzer_owner)

    def test_old_draft_refuses_before_identity_or_factory_and_reselection_clears_profile(self):
        snapshot = stage(self.g)
        selection = self.g.sources.current()
        old = HackrfConfigurationPatch(snapshot.hackrf_request, selection.revision, 0)
        with self.assertRaises(LiveAdmissionRejected):
            self.g.application.stage_hackrf_configuration(old)
        self.g.application.select_device("source-hackrf")
        self.assertIsNone(self.g.application.current_snapshot().hackrf_request)
        with self.assertRaises(LiveAdmissionRejected):
            self.g.application.stage_hackrf_configuration(replace(old, expected_generation=0))
        self.assertEqual(self.g.observation.probes, 0)

    def test_restart_allocates_new_generation_and_epoch_not_reused_frame(self):
        stage(self.g)
        self.g.application.start()
        self.wait(lambda: self.g.application.current_snapshot().spectrum is not None)
        previous = self.g.application.current_snapshot()
        self.g.application.stop()
        current = self.g.application.start()
        self.assertGreater(current.generation, previous.generation)
        self.assertGreater(current.acquisition_epoch, previous.acquisition_epoch)
        self.assertIsNone(current.spectrum)

    def test_frame_source_generation_geometry_unit_mismatch_fails_closed(self):
        changes = (lambda frame: setattr(frame.source, "source_id", "foreign"),
                   lambda frame: setattr(frame, "config_generation", 0),
                   lambda frame: setattr(frame, "sample_rate_hz", 10e6),
                   lambda frame: setattr(frame, "unit", "DBM_BIN"))
        for change in changes:
            with self.subTest(change=change):
                stage(self.g)
                self.g.factory.change = change
                self.g.application.start()
                self.wait(lambda: self.g.application.current_snapshot().error is not None)
                snapshot = self.g.application.current_snapshot()
                self.assertIsNone(snapshot.spectrum)
                self.assertTrue(snapshot.stop_required)
                self.assertIsNone(self.g.application.stop().error)


class HackrfActualCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("bounded V2 common HackRF completion timeout")
            time.sleep(.001)
        self.app.processEvents()

    def test_hackrf_units_switch_locale_without_changing_numeric_draft_or_starting_io(self):
        previous_locale = current_locale()
        bar = HackrfConfigurationBar(SimpleNamespace(state=SimpleNamespace(controls_locked=False)))
        try:
            bar._reset()
            for locale, mhz, db in ((UiLocale.RU, " МГц", " дБ"), (UiLocale.EN, " MHz", " dB")):
                set_active_locale(locale)
                bar.set_locale()
                self.assertEqual(bar.center.suffix(), mhz)
                self.assertEqual((bar.lna.suffix(), bar.vga.suffix()), (db, db))
                self.assertTrue(bar.bandwidth.currentText().endswith(mhz))
                self.assertEqual((bar.center.value(), bar.lna.value(), bar.vga.value()), (100, 16, 20))
                self.assertEqual((bar.rate.currentData(), bar.bandwidth.currentData()), (20e6, 15_000_000))
                self.assertFalse(bar.dirty)
                self.assertIsNone(bar._base)
        finally:
            set_active_locale(previous_locale)
            bar.deleteLater()
            self.app.processEvents()

    def test_actual_root_stage_start_same_canvas_stop_foreign_late_ack_and_draft_reset(self):
        g = graph()
        captures = []
        def capture(*args, **kwargs):
            composition = compose_v2_live_product(*args, **kwargs)
            captures.append(composition)
            return composition
        services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog, analyzer_hackrf=g.hackrf,
                                   sweep=Mock(), calibration=Mock(), diagnostics=Mock(), replay=Mock())
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture):
            shell = build_v2_shell(services)
        composition = captures[0]
        page = shell._workspace_pages["analyzer"]
        errors = []
        try:
            with patch("sys.excepthook", side_effect=lambda *args: errors.append(args)):
                page.discover.click()
                self.wait(lambda: page.source.count() == 3 and not composition.view_model.state.busy)
                page.source.setCurrentIndex(page.source.findData("source-hackrf"))
                self.wait(lambda: composition.analyzer_view_model.state.hackrf_controls_available and not composition.view_model.state.busy)
                self.assertFalse(page.primary.isEnabled())
                self.assertFalse(page.hackrf_bar.isHidden())
                self.assertTrue(page.frequency_bar.isHidden())
                self.assertNotIn(text("analyzer.source.family_path_pending"), page.source_summary.text())
                self.assertFalse(composition.analyzer_view_model.select_mode("sweep"))
                page.hackrf_bar.stage.click()
                self.wait(lambda: composition.analyzer_view_model.state.rtbw_profile_ready and not composition.view_model.state.busy)
                self.assertEqual(g.observation.probes, 0)
                self.assertTrue(page.primary.isEnabled())
                page.primary.click()
                self.wait(lambda: composition.analyzer_view_model.state.bundle is not None and not composition.view_model.state.busy)
                snapshot = composition.view_model.state.snapshot
                self.assertIsNotNone(page.visualization.spectrum_scene.latest_frame)
                self.assertEqual(composition.analyzer_view_model.state.bundle.unit, "dBFS/bin")
                self.assertFalse(page.hackrf_bar.stage.isEnabled())
                self.assertFalse(page.source.isEnabled())
                page.primary.click()
                self.wait(lambda: not composition.analyzer_view_model.state.running and not composition.view_model.state.busy)
                page.hackrf_bar.lna.setValue(24)
                self.assertTrue(page.hackrf_bar.dirty)
                self.assertFalse(page.primary.isEnabled())
                native_id = next(choice.device_id for choice in composition.view_model.devices if choice.family is DeviceFamily.AD936X)
                page.source.setCurrentIndex(page.source.findData(native_id))
                self.wait(lambda: composition.analyzer_view_model.state.ad936x_controls_available and not composition.view_model.state.busy)
                composition.view_model._on_snapshot(snapshot)
                self.assertIsNot(composition.view_model.state.snapshot, snapshot)
                page.source.setCurrentIndex(page.source.findData("source-hackrf"))
                self.wait(lambda: composition.analyzer_view_model.state.hackrf_controls_available and not composition.view_model.state.busy)
                self.assertIsNone(composition.view_model.state.snapshot.hackrf_request)
                self.assertEqual(page.hackrf_bar.lna.value(), 16)
                self.assertFalse(page.hackrf_bar.dirty)
                self.assertFalse(page.primary.isEnabled())
            self.assertEqual(errors, [])
        finally:
            shell.close()
            self.wait(lambda: shell._is_closed)
            composition.shutdown()
            shell.deleteLater()
            self.app.processEvents()
        self.assertIsNone(g.live._external_analyzer_owner)
        self.assertIsNone(g.hackrf._poller)


if __name__ == "__main__":
    unittest.main()
