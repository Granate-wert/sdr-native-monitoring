"""Actual V2 common-owner/renderer with fake serial, not physical RF proof."""

import os
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from sdr_monitor.application.analyzer_rtbw_router import AnalyzerRtbwRouter
from sdr_monitor.application.analyzer_session import AnalyzerPhase, AnalyzerSessionApplicationService
from sdr_monitor.application.analyzer_sources import AnalyzerSourceSelectionApplicationService
from sdr_monitor.application.analyzer_sweep_router import AnalyzerSweepRouter
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.analyzer import bundle_from_sweep
from sdr_monitor.domain.live import LiveAdmissionRejected, LiveConfiguration
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import TinySaCapabilityProvider
from sdr_monitor.services.tinysa_capability_adapter import TinySaModel, TinySaReadOnlyProbe
from sdr_monitor.services.tinysa_common_analyzer import TinySaCommonAnalyzerService
from sdr_monitor.services.tinysa_owned_acquisition import TinySaOwnedAcquisition
from sdr_monitor.services.tinysa_serial_source_backend import TinySaSerialSourceBackend
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.test_app06_pluto_observation_catalog import _Native
from tests.test_app06_tinysa_owned_acquisition import _pnp, _Serial


def graph():
    native = _Native()
    live = NativeLiveSessionService(native)
    pnp = [_pnp()]
    backend = TinySaSerialSourceBackend(inventory_provider=lambda: tuple(pnp))
    serials = []
    def serial_factory(_route):
        serial = _Serial()
        serials.append(serial)
        return serial
    class Probe:
        def __init__(self, endpoint):
            self.endpoint = endpoint
        def probe(self):
            return TinySaReadOnlyProbe(TinySaModel.ULTRA, self.endpoint.identity_key, "tinySA4 v1.4-fixture")
        def close(self):
            pass
    provider = TinySaCapabilityProvider(backend, port_factory=Probe,
        acquisition_factory=lambda b, e, o: TinySaOwnedAcquisition(b, e, o, serial_factory=serial_factory))
    catalog = SourceCapabilityCatalog((provider,), control_transaction=live.capability_control_transaction)
    instrument = TinySaCommonAnalyzerService(catalog, live)
    sources = AnalyzerSourceSelectionApplicationService(catalog, live,
        control_transaction=lambda: analyzer.idle_control_operation())
    rtbw = AnalyzerRtbwRouter(live, sources, None)
    native_sweep = Mock()
    sweep = AnalyzerSweepRouter(native_sweep, sources, instrument)
    analyzer = AnalyzerSessionApplicationService(rtbw, sweep, start_live=rtbw.start)
    application = LiveSessionApplicationService(live, analyzer=analyzer, sources=sources,
        rtbw=rtbw, catalog_close=catalog.close)
    return SimpleNamespace(native=native, live=live, pnp=pnp, serials=serials, provider=provider,
        catalog=catalog, instrument=instrument, sources=sources, analyzer=analyzer, application=application,
        sweep=sweep, native_sweep=native_sweep)


class TinySaCommonOwnerTests(unittest.TestCase):
    def setUp(self):
        self.g = graph()
        choice = self.g.application.discover()[0]
        self.g.application.select_device(choice.device_id)
        self.selection = self.g.sources.current()
        self.request = TinySaSweepRequest(self.selection.selected, self.selection.revision,
                                         87_500_000, 108_000_000, 3)
        self.addCleanup(self.g.application.shutdown)

    def wait(self, predicate):
        until = time.monotonic() + 4
        while not predicate():
            if time.monotonic() >= until:
                self.fail("instrument worker timeout")
            time.sleep(.001)

    def test_same_owner_full_trace_and_graph_exclusion_until_stop(self):
        state = self.g.application.start_sweep(self.request)
        self.assertEqual(state.phase, AnalyzerPhase.RUNNING)
        self.wait(lambda: self.g.instrument.poll_latest().metrics.acquisition_finished)
        self.assertIs(self.g.live._external_analyzer_owner, self.g.instrument)
        self.assertTrue(self.g.application.current_snapshot().stop_required)
        for operation in (self.g.catalog.refresh, self.g.live.start_admitted,
                          lambda: self.g.live.apply_configuration(LiveConfiguration())):
            with self.assertRaises((RuntimeError, LiveAdmissionRejected)):
                operation()
        line = self.g.sweep.poll_latest().line
        self.assertEqual(line.instrument.selection_revision, self.selection.revision)
        bundle = bundle_from_sweep(line)
        self.assertEqual(bundle.unit, "dBm")
        self.assertIsNone(bundle.rtbw)
        self.assertEqual(bundle.identity.config_generation, 1)
        self.assertEqual(bundle.identity.clock_domain, "host_steady_completion")
        self.assertTrue(np.all(line.source_segment_indices == -1))
        self.assertEqual(line.physical_fft_size, 0)
        self.assertFalse(line.values_db.flags.writeable)
        self.g.native_sweep.start.assert_not_called()
        self.g.application.stop()
        self.assertFalse(self.g.application.current_snapshot().stop_required)
        self.assertIsNone(self.g.live._external_analyzer_owner)
        self.assertIsNone(self.g.instrument._thread)
        self.assertFalse(self.g.catalog.cleanup_pending)
        self.assertEqual(len(self.g.serials), 1)
        self.assertEqual(self.g.serials[0].calls.count("close"), 1)

    def test_points_deadline_range_type_and_stale_request_refuse_before_open(self):
        for args in ({"points": 10002}, {"points": True}, {"start_hz": 0},
                     {"timeout_s": float("nan")}, {"stop_hz": 87_500_001}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                replace(self.request, **args)
        self.g.application.select_device(self.request.source.device_id)
        with self.assertRaises(RuntimeError):
            self.g.application.start_sweep(self.request)
        self.assertEqual(self.g.serials, [])
        self.assertFalse(self.g.instrument.stop_required)

    def test_explicit_second_start_allocates_new_epoch_and_generation(self):
        self.g.application.start_sweep(self.request)
        self.wait(lambda: self.g.instrument.poll_latest().metrics.acquisition_finished)
        first = self.g.instrument.poll_latest().line
        self.g.application.stop()
        self.g.application.start_sweep(self.request)
        self.wait(lambda: self.g.instrument.poll_latest().metrics.acquisition_finished)
        second = self.g.instrument.poll_latest().line
        self.assertGreater(second.epoch, first.epoch)
        self.assertGreater(second.instrument.configuration_generation, first.instrument.configuration_generation)
        self.assertEqual(len(self.g.serials), 2)
        self.g.application.stop()

    def test_failed_close_retains_token_until_explicit_stop_and_no_poll_retry(self):
        factory = self.g.provider._acquisition_factory
        def failing(*args):
            owner = factory(*args)
            raw_factory = owner._factory
            def raw(route):
                serial = raw_factory(route)
                serial.close_error = True
                return serial
            owner._factory = raw
            return owner
        self.g.provider._acquisition_factory = failing
        self.g.application.start_sweep(self.request)
        self.wait(lambda: self.g.instrument.poll_latest().metrics.has_error)
        serial = self.g.serials[0]
        calls = list(serial.calls)
        for _ in range(5):
            self.g.instrument.poll_latest()
        self.assertEqual(serial.calls, calls)
        self.assertIs(self.g.live._external_analyzer_owner, self.g.instrument)
        self.assertTrue(self.g.catalog.cleanup_pending)
        stopped = self.g.application.stop()
        self.assertTrue(stopped.stop_required)
        self.assertIsNotNone(stopped.error)
        self.assertNotIn("PRIVATE", stopped.error)
        serial.close_error = False
        self.g.application.stop()
        self.assertIsNone(self.g.live._external_analyzer_owner)
        self.assertFalse(self.g.catalog.cleanup_pending)

    def test_foreign_unit_fft_quality_and_provenance_are_not_admitted(self):
        self.g.application.start_sweep(self.request)
        self.wait(lambda: self.g.instrument.poll_latest().metrics.acquisition_finished)
        line = self.g.instrument.poll_latest().line
        for args in ({"unit": "dBFS/bin"}, {"physical_fft_size": 4096},
                     {"quality_flags": np.ones(3, dtype=np.uint16)}, {"instrument": None},
                     {"frequencies_hz": line.frequencies_hz + 1}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                replace(line, **args)
        self.g.application.stop()

    def test_cancel_joins_before_release_and_publishes_no_invented_samples(self):
        entered, resume = threading.Event(), threading.Event()
        factory = self.g.provider._acquisition_factory
        def waiting(*args):
            owner = factory(*args)
            raw_factory = owner._factory
            def raw(route):
                serial = raw_factory(route)
                read = serial.read
                def blocked(size):
                    if serial.response.startswith(b"{"):
                        entered.set()
                        if not resume.wait(3):
                            raise RuntimeError("fixture read barrier")
                        return read(1)  # partial byte only; next read observes cancellation
                    return read(size)
                serial.read = blocked
                return serial
            owner._factory = raw
            return owner
        self.g.provider._acquisition_factory = waiting
        self.g.application.start_sweep(self.request)
        self.assertTrue(entered.wait(2))
        completed = []
        stop = threading.Thread(target=lambda: completed.append(self.g.application.stop()))
        stop.start()
        try:
            self.wait(lambda: self.g.instrument._owner._cancel.is_set())
            self.assertIs(self.g.live._external_analyzer_owner, self.g.instrument)
            self.assertTrue(stop.is_alive())
        finally:
            resume.set()
            stop.join(4)
        self.assertFalse(stop.is_alive())
        self.assertFalse(completed[0].stop_required)
        line = self.g.instrument.poll_latest().line
        self.assertFalse(line.is_complete)
        self.assertTrue(np.all(np.isnan(line.values_db)))
        self.assertIsNone(line.instrument.observed_zero_db)
        self.assertIsNone(self.g.live._external_analyzer_owner)

    def test_device_removed_and_firmware_changed_refuse_publication_without_reopen(self):
        for change in ("remove", "firmware"):
            with self.subTest(change=change):
                factory = self.g.provider._acquisition_factory
                def fault(*args, factory=factory, change=change):
                    owner = factory(*args)
                    raw_factory = owner._factory
                    def raw(route):
                        serial = raw_factory(route)
                        if change == "firmware":
                            serial.version = b"tinySA4 v1.5-new\rch> "
                        else:
                            serial.on_write = lambda data: self.g.pnp.clear() if data.startswith(b"scanraw ") else None
                        return serial
                    owner._factory = raw
                    return owner
                self.g.provider._acquisition_factory = fault
                count = len(self.g.serials)
                self.g.application.start_sweep(self.request)
                self.wait(lambda: self.g.instrument.poll_latest().metrics.has_error)
                self.assertIsNone(self.g.instrument.poll_latest().line)
                self.assertEqual(len(self.g.serials), count + 1)
                self.g.application.stop()
                self.g.pnp[:] = [_pnp()]
                self.g.provider._acquisition_factory = factory


class TinySaActualCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate):
        until = time.monotonic() + 5
        while not predicate():
            self.app.processEvents()
            if time.monotonic() >= until:
                self.fail("common V2 tinySA completion timeout")
            time.sleep(.001)
        self.app.processEvents()

    def test_actual_root_one_pass_same_canvas_auto_finish_no_second_owner_locale(self):
        g = graph()
        captures = []
        def capture(*args, **kwargs):
            c = compose_v2_live_product(*args, **kwargs)
            captures.append(c)
            return c
        services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog, analyzer_tinysa=g.instrument,
            analyzer_hackrf=None, sweep=Mock(), calibration=Mock(), diagnostics=Mock(), replay=Mock())
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture):
            shell = build_v2_shell(services)
        c = captures[0]
        page = shell._workspace_pages["analyzer"]
        errors = []
        previous = current_locale()
        try:
            with patch("sys.excepthook", side_effect=lambda *args: errors.append(args)):
                page.discover.click()
                self.wait(lambda: page.source.count() == 2 and not c.view_model.state.busy)
                page.source.setCurrentIndex(1)
                self.wait(lambda: c.analyzer_view_model.state.tinysa_controls_available and not c.view_model.state.busy)
                self.assertTrue(page.primary.isEnabled())
                self.assertFalse(page.tinysa_bar.isHidden())
                self.assertTrue(page.frequency_bar.isHidden())
                self.assertTrue(page.hackrf_bar.isHidden())
                self.assertIsNone(c._tinysa)
                self.assertFalse(c.analyzer_view_model.select_mode("rtbw"))
                page.tinysa_bar.points.setValue(10001)
                for locale in (UiLocale.EN, UiLocale.RU):
                    set_active_locale(locale)
                    page.tinysa_bar.set_locale()
                    self.assertEqual(page.tinysa_bar.points.value(), 10001)
                    self.assertEqual(page.tinysa_bar.start.value(), 87.5)
                page.primary.click()
                self.wait(lambda: c.analyzer_view_model.state.bundle is not None and
                          not c.analyzer_view_model.state.controls_locked)
                state = c.analyzer_view_model.state
                self.assertEqual(state.bundle.unit, "dBm")
                self.assertEqual(state.bundle.spectrum.values_db.size, 10001)
                self.assertIsNotNone(page.visualization.spectrum_scene.latest_frame)
                self.assertEqual(len(state.prepared_sweep.waterfall_rows), 1)
                self.assertFalse(page._sweep_waterfall_error)
                self.assertTrue(page.primary.isEnabled())
                self.assertIsNone(g.instrument._thread)
                self.assertIsNone(g.live._external_analyzer_owner)
                self.assertFalse(g.catalog.cleanup_pending)
                self.assertIn(text("tinysa.common.numerical"), page.applied.text())
                self.assertEqual(len(g.serials), 1)
                first = state.bundle.spectrum
                page.tinysa_bar.points.setValue(3)
                page.primary.click()
                self.wait(lambda: c.analyzer_view_model.state.bundle is not None and
                          c.analyzer_view_model.state.bundle.spectrum.epoch > first.epoch and
                          not c.analyzer_view_model.state.controls_locked)
                self.assertEqual(c.analyzer_view_model.state.bundle.spectrum.values_db.size, 3)
            self.assertEqual(errors, [])
        finally:
            set_active_locale(previous)
            shell.close()
            self.wait(lambda: shell._is_closed)
            c.shutdown()
            shell.deleteLater()
            self.app.processEvents()
        self.assertFalse(g.catalog.cleanup_pending)
        self.assertIsNone(g.live._external_analyzer_owner)


if __name__ == "__main__":
    unittest.main()
