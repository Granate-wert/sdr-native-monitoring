"""Same common owner/presenter/canvas density path; injected RX, no hardware."""

import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer import bundle_from_live
from sdr_monitor.domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from sdr_monitor.domain.identity import TimestampQuality
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.state.live_view_state import PersistencePresentation, build_live_view_state
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph


def enable_fake_density(g):
    g.native.HACKRF_PERSISTENCE_CONTRACT_VERSION = 1
    original = g.factory.create
    g.density_change = None
    def create(permit):
        control = original(permit)
        control.latest = None
        def remember(frame):
            control.latest = frame
        control.frame_change = remember
        def poll(_count):
            if control.latest is None:
                return []
            request, frame = control.request, control.latest
            density = np.zeros((request.persistence_power_bins, request.fft_size), np.float32)
            density[2, :] = 1
            value = SimpleNamespace(source_id=request.source_id, config_generation=request.configuration_generation,
                update_sequence=frame.frame_sequence, source_frame_sequence=frame.frame_sequence,
                timestamp_ns=frame.timestamp_ns, power_min_db=request.persistence_power_min_db,
                power_max_db=request.persistence_power_max_db, power_bins=request.persistence_power_bins,
                frequency_bins=request.fft_size, processed_frames=frame.frame_sequence,
                exponential_decay=request.persistence_mode == "exponential-decay",
                frequencies_hz=frame.frequencies_hz, density=density, probability_scale=1., count_scale=1.,
                unit="DBFS_BIN", quality_flags=frame.quality_flags)
            if g.density_change:
                g.density_change(value)
            return [value]
        control.poll_persistence_snapshots = poll
        def metrics(value):
            value.processing.dsp.persistence_updates = control.sequence
            value.processing.dsp.persistence = SimpleNamespace(dropped=0)
        control.metrics_change = metrics
        return control
    g.factory.create = create


def stage_density(g, **changes):
    selection = g.application.current_source_selection()
    request = HackrfLiveRequest(100e6, 20e6, 15_000_000, 16, 20, fft_size=256, hop_size=128,
        source_id=selection.selected_id, persistence_enabled=True, persistence_mode="exponential-decay",
        persistence_power_bins=16)
    request = replace(request, **changes)
    return g.application.stage_hackrf_configuration(HackrfConfigurationPatch(request, selection.revision,
        g.application.current_snapshot().generation))


class HackrfPersistenceOwnerTests(unittest.TestCase):
    def setUp(self):
        self.g = graph()
        enable_fake_density(self.g)
        self.g.application.discover()
        self.g.application.select_device("source-hackrf")

    def tearDown(self):
        self.g.application.shutdown()

    def wait(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("bounded common persistence publication timeout")
            time.sleep(.001)

    def test_native_density_reaches_same_bundle_without_fake_pluto_profile(self):
        stage_density(self.g)
        self.assertEqual(self.g.observation.probes, 0)
        self.g.application.start()
        self.wait(lambda: self.g.application.current_snapshot().persistence is not None)
        snapshot = self.g.application.current_snapshot()
        density = snapshot.persistence
        self.assertIsNone(snapshot.applied)
        self.assertIsNone(snapshot.device)
        self.assertEqual(density.source_id, snapshot.spectrum.source_id)
        self.assertEqual(density.acquisition_epoch, snapshot.acquisition_epoch)
        self.assertEqual(density.accumulation_id, snapshot.session_id)
        self.assertEqual(density.timestamp_quality, TimestampQuality.ESTIMATED)
        self.assertFalse(density.density.flags.writeable)
        self.assertIs(bundle_from_live(snapshot).persistence, density)
        view = build_live_view_state(snapshot)
        self.assertEqual(view.persistence, PersistencePresentation.ACTIVE)
        self.assertEqual(view.persistence_frame.level_unit, "dBFS/bin")
        self.assertIsNone(self.g.application.stop().error)
        self.assertIsNone(self.g.live._external_analyzer_owner)

    def test_new_explicit_start_clears_old_density_and_changes_epoch(self):
        stage_density(self.g)
        self.g.application.start()
        self.wait(lambda: self.g.application.current_snapshot().persistence is not None)
        previous = self.g.application.stop()
        self.assertIsNotNone(previous.persistence)
        stage_density(self.g, persistence_enabled=False, persistence_mode="disabled")
        self.assertIsNone(self.g.application.current_snapshot().persistence)
        self.g.application.start()
        self.wait(lambda: self.g.application.current_snapshot().spectrum is not None)
        current = self.g.application.current_snapshot()
        self.assertIsNone(current.persistence)
        self.assertGreater(current.acquisition_epoch, previous.acquisition_epoch)
        self.assertEqual(build_live_view_state(current).persistence, PersistencePresentation.DISABLED)

    def test_changed_or_missing_runtime_refuses_before_identity_probe(self):
        original = stage_density(self.g)
        selection = self.g.application.current_source_selection()
        for version in (None, True, 0, 2, "1"):
            with self.subTest(version=version):
                self.g.native.HACKRF_PERSISTENCE_CONTRACT_VERSION = version
                with self.assertRaises(LiveAdmissionRejected):
                    self.g.application.stage_hackrf_configuration(HackrfConfigurationPatch(
                        original.hackrf_request, selection.revision, original.generation))
                self.assertEqual(self.g.observation.probes, 0)
                self.assertEqual(self.g.factory.controls, [])
        self.g.native.HACKRF_PERSISTENCE_CONTRACT_VERSION = 1

    def test_start_rechecks_runtime_without_touching_identity_or_sdk(self):
        stage_density(self.g)
        self.g.native.HACKRF_PERSISTENCE_CONTRACT_VERSION = None
        refused = self.g.application.start()
        self.assertIsNotNone(refused.error)
        self.assertFalse(refused.stop_required)
        self.assertIsNone(self.g.live._external_analyzer_owner)
        self.assertEqual((self.g.observation.probes, self.g.factory.controls), (0, []))

    def test_bad_producer_density_fails_closed_until_explicit_stop(self):
        changes = (
            lambda value: setattr(value, "source_id", "foreign"),
            lambda value: setattr(value, "config_generation", 0),
            lambda value: setattr(value, "unit", "DBM_BIN"),
            lambda value: setattr(value, "quality_flags", None),
            lambda value: setattr(value, "power_min_db", -120.),
            lambda value: setattr(value, "exponential_decay", False),
            lambda value: setattr(value, "probability_scale", float("nan")),
            lambda value: setattr(value, "frequencies_hz", value.frequencies_hz + 1))
        for change in changes:
            with self.subTest(change=change):
                stage_density(self.g)
                self.g.density_change = change
                self.g.application.start()
                self.wait(lambda: self.g.application.current_snapshot().error is not None)
                self.assertTrue(self.g.application.current_snapshot().stop_required)
                self.assertIsNotNone(self.g.live._external_analyzer_owner)
                self.assertIsNone(self.g.application.stop().error)
                self.assertIsNone(self.g.live._external_analyzer_owner)

    def test_foreign_epoch_clock_accumulation_and_unit_cannot_be_joined(self):
        stage_density(self.g)
        self.g.application.start()
        self.wait(lambda: self.g.application.current_snapshot().persistence is not None)
        snapshot = self.g.application.stop()
        for changes in ({"acquisition_epoch": snapshot.acquisition_epoch + 1},
                        {"clock_domain": "foreign"}, {"accumulation_id": "foreign"},
                        {"producer_identity_available": False}, {"unit": "dBm"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(snapshot, persistence=replace(snapshot.persistence, **changes))
        future = replace(snapshot, persistence=replace(snapshot.persistence,
            source_frame_sequence=snapshot.spectrum.sequence + 1))
        bundle = bundle_from_live(future)
        self.assertIsNone(bundle.persistence)
        self.assertIn("persistence_pending", bundle.coherence_issues)


class HackrfPersistenceActualRootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("bounded actual root persistence timeout")
            time.sleep(.001)
        self.app.processEvents()

    def test_actual_root_native_density_on_common_canvas_and_locked_controls(self):
        g = graph()
        enable_fake_density(g)
        captures = []
        def capture(*args, **kwargs):
            result = compose_v2_live_product(*args, **kwargs)
            captures.append(result)
            return result
        services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog, analyzer_hackrf=g.hackrf,
            sweep=Mock(), calibration=Mock(), diagnostics=Mock(), replay=Mock())
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture):
            shell = build_v2_shell(services)
        composition, page = captures[0], shell._workspace_pages["analyzer"]
        try:
            page.discover.click()
            self.wait(lambda: page.source.count() == 3 and not composition.view_model.state.busy)
            page.source.setCurrentIndex(page.source.findData("source-hackrf"))
            self.wait(lambda: composition.analyzer_view_model.state.hackrf_controls_available
                and not composition.view_model.state.busy)
            bar = page.hackrf_bar
            self.assertFalse(bar.dsp_toggle.isChecked())
            self.assertTrue(bar.persistence_mode.isEnabled())
            bar.persistence_mode.setCurrentIndex(bar.persistence_mode.findData("exponential-decay"))
            bar.persistence_bins.setValue(16)
            self.assertTrue(bar.dirty)
            self.assertEqual(g.observation.probes, 0)
            bar.stage.click()
            self.wait(lambda: composition.analyzer_view_model.state.rtbw_profile_ready
                and not composition.view_model.state.busy)
            self.assertEqual(g.observation.probes, 0)
            page.primary.click()
            self.wait(lambda: page.visualization.spectrum_scene.latest_frame is not None
                and page._last_persistence is not None and not composition.view_model.state.busy)
            self.assertEqual(composition.view_model.state.persistence, PersistencePresentation.ACTIVE)
            self.assertFalse(bar.persistence_mode.isEnabled())
            self.assertFalse(bar.persistence_bins.isEnabled())
            page.primary.click()
            self.wait(lambda: not composition.analyzer_view_model.state.running
                and not composition.view_model.state.busy)
            self.assertEqual(bar.persistence_mode.currentData(), "exponential-decay")
            self.assertTrue(bar.persistence_mode.isEnabled())
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
