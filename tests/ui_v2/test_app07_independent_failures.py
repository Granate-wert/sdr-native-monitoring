"""Actual three-family graphs/pump/Qt, injected failures instead of hardware resets."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QTimer
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneGraphPoolError, PaneProductGraphPool
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2

from tests.test_s15_live_rx_bridge import _make_frame
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as tinysa_fixture
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


class IndependentPaneFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate, timeout_s=5.0):
        deadline = monotonic() + timeout_s
        while monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            sleep(.005)
        self.fail("bounded independent-pane failure observation timed out")

    @contextmanager
    def product(self, *, configure_hackrf=None, configure_tinysa=None, ad_rate=20e6):
        native, ad = _ad_graph()
        hf = hackrf_fixture()
        ts = tinysa_fixture()
        if configure_hackrf is not None:
            configure_hackrf(hf)
        if configure_tinysa is not None:
            configure_tinysa(ts)
        graphs = (ad, build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf.live, device_catalog=hf.catalog, analyzer_hackrf=hf.hackrf)),
            build_v2_analyzer_application_graph(SimpleNamespace(
                live_sdr=ts.live, device_catalog=ts.catalog, analyzer_tinysa=ts.instrument)))
        choices = tuple(next(choice for choice in graph.live.discover(startup=True)
                             if choice.family is family)
                        for graph, family in zip(graphs, (
                            DeviceFamily.AD936X, DeviceFamily.HACKRF, DeviceFamily.TINYSA), strict=True))
        pool = PaneProductGraphPool(lambda resource: graphs[int(resource.rsplit("-", 1)[1]) - 1])
        prepared = None
        ui = None
        with TemporaryDirectory() as directory:
            try:
                prepared = prepare_user_pane_session((
                    PaneSlotDraft(1, choices[0].device_id, 100e6, 108e6, sample_rate_hz=ad_rate),
                    PaneSlotDraft(2, choices[1].device_id, 140e6, 148e6),
                    PaneSlotDraft(3, choices[2].device_id, 200e6, 210e6, points=3),
                    PaneSlotDraft(4),
                ), pool_factory=lambda: pool)
                apply_user_pane_session(prepared)
                settings = QSettings(str(Path(directory) / "panes.ini"), QSettings.Format.IniFormat)
                with patch("sdr_monitor.ui.v2.workspaces.independent_pane_board.QSettings", return_value=settings):
                    ui = IndependentPaneSessionV2(prepared.handle, close_layout=lambda _handle: None)
                ui.resize(1280, 750)
                ui.show()
                self.app.processEvents()
                yield SimpleNamespace(native=native, hf=hf, ts=ts, graphs=graphs,
                                      choices=choices, pool=pool, handle=prepared.handle, ui=ui)
            finally:
                hf.factory.fail = False
                for control in hf.factory.controls:
                    control.stop_fail = False
                for serial in ts.serials:
                    serial.close_error = False
                if prepared is not None and not prepared.handle.shutdown_complete:
                    for future in prepared.handle.pump.stop_all().values():
                        future.result(timeout=5)
                    prepared.handle.shutdown_after_stop()
                else:
                    pool.close()
                if ui is not None:
                    ui.release_presentation_after_shutdown()
                    ui.close()
                    # Mirror the real composition's terminal QObject retirement;
                    # close() alone leaves hidden top-level Qt/graphics objects
                    # until an unrelated later fixture processes their events.
                    ui.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                for graph in graphs:
                    graph.live.shutdown()
                self.assertEqual(pool.staged_resource_ids, ())
                self.assertEqual(pool.cleanup_pending_resource_ids, ())
                if prepared is not None:
                    self.assertEqual(prepared.handle.session.retained_resource_count, 0)
                    self.assertTrue(all(not worker._thread.is_alive()
                                        for worker in prepared.handle.pump._workers.values()))
                    self.assertEqual(prepared.handle.queue.pending_count, 0)
                self.assertTrue(all(graph.live._pane_control_claim is None for graph in graphs))

    @staticmethod
    def phases(product):
        return tuple(item.phase for item in product.handle.pump.snapshot())

    def publish_ad(self, product, sequence):
        snapshot = product.graphs[0].live.current_snapshot()
        rate = snapshot.applied.applied.sample_rate_hz
        frame = _make_frame(sequence, center_hz=snapshot.applied.applied.center_hz,
                            sample_rate_hz=rate, source_id=product.choices[0].device_id,
                            config_generation=snapshot.active_config_generation)
        frame.frequencies_hz = frame.center_frequency_hz + (np.arange(4096) - 2048) * (rate / 4096)
        product.native.engines[-1].frames.append(frame)
        self.wait(lambda: product.ui.board.pane(1).spectrum_scene.latest_frame is not None
                  and product.ui.board.pane(1).spectrum_scene.latest_frame.spectrum.sequence == sequence)

    def test_high_fs_ad936x_exact_stage_and_start_with_other_families(self):
        with self.product(ad_rate=61.44e6) as product:
            staged = product.graphs[0].live.current_snapshot().applied
            self.assertEqual(staged.requested.sample_rate_hz, 61.44e6)
            self.assertEqual(staged.requested.analog_bandwidth_hz, 40e6)
            self.assertEqual(staged.applied, staged.requested)
            self.assertEqual(product.native.engines, [])  # Apply is still not Start.
            profile = product.handle.layout.schedule.resources[0].jobs[0].profile
            self.assertEqual(profile.usable_capture_span_hz, 36e6)
            product.ui.start_all.click()
            self.wait(lambda: all(phase is PanePumpPhase.RUNNING for phase in self.phases(product)))
            self.publish_ad(product, 1)
            self.publish_ad(product, 2)
            self.wait(lambda: product.ui.board.pane(2).spectrum_scene.latest_frame is not None
                      and product.ui.board.pane(3).spectrum_scene.latest_frame is not None)
            applied = product.graphs[0].live.current_snapshot().applied
            self.assertEqual(applied.applied.sample_rate_hz, 61.44e6)
            self.assertEqual(applied.applied.analog_bandwidth_hz, 40e6)
            self.assertTrue({"center_hz", "sample_rate_hz", "analog_bandwidth_hz", "gain_db"}
                            <= set(applied.readback_fields))
            self.assertIsNone(product.ui.board.pane(4))

    def test_one_start_failure_does_not_start_again_or_stop_other_families(self):
        with self.product(configure_hackrf=lambda hf: setattr(hf.factory, "fail", True)) as product:
            product.ui.start_all.click()
            self.wait(lambda: self.phases(product) == (
                PanePumpPhase.RUNNING, PanePumpPhase.STOP_REQUIRED, PanePumpPhase.RUNNING))
            self.publish_ad(product, 1)
            self.wait(lambda: product.ui.board.pane(3).spectrum_scene.latest_frame is not None)
            self.assertEqual(product.ui.board.pane(3).last_bundle.unit, "dBm")
            self.assertIsNone(product.ui.board.pane(2).spectrum_scene.latest_frame)
            self.assertIsNone(product.ui.board.pane(4))
            self.assertEqual(product.handle.session.retained_resource_count, 3)
            self.assertFalse(product.handle.can_close())
            self.assertNotIn("PRIVATE", product.ui.error.text())
            product.hf.factory.fail = False
            product.ui.board.select_slot(2)
            self.assertFalse(product.ui.start_selected.isEnabled())
            product.ui.start_all.click()  # No IDLE resource: not an implicit failed Start retry.
            self.assertEqual(product.hf.factory.controls, [])
            product.ui.stop_selected.click()
            self.wait(lambda: self.phases(product)[1] is PanePumpPhase.STOPPED)
            self.assertEqual(product.handle.session.retained_resource_count, 2)
            self.publish_ad(product, 2)
            self.assertEqual(self.phases(product), (
                PanePumpPhase.RUNNING, PanePumpPhase.STOPPED, PanePumpPhase.RUNNING))

    def test_poll_disconnect_failure_quarantines_one_rx_and_keeps_neighbors_live(self):
        with self.product() as product:
            product.ui.start_all.click()
            self.wait(lambda: all(phase is PanePumpPhase.RUNNING for phase in self.phases(product)))
            self.publish_ad(product, 1)
            self.wait(lambda: product.ui.board.pane(2).spectrum_scene.latest_frame is not None
                      and product.ui.board.pane(3).spectrum_scene.latest_frame is not None)
            control = product.hf.factory.controls[0]
            control.drain_latest_spectrum_frame = lambda: SimpleNamespace(frame=None, coalesced_frames=1)
            self.wait(lambda: self.phases(product)[1] is PanePumpPhase.STOP_REQUIRED)
            self.assertIsNotNone(product.hf.live._external_analyzer_owner)
            self.assertEqual(control.stops, [])  # No hidden close/restart on poll failure.
            self.assertEqual(product.handle.session.retained_resource_count, 3)
            self.publish_ad(product, 2)
            retained = product.ui.board.pane(2).spectrum_scene.latest_frame
            self.assertIsNotNone(retained)
            self.assertNotIn("PRIVATE", product.ui.status.text())
            product.ui.board.select_slot(2)
            product.ui.stop_selected.click()
            self.wait(lambda: self.phases(product)[1] is PanePumpPhase.STOPPED)
            self.assertIsNone(product.hf.live._external_analyzer_owner)
            self.assertIs(product.ui.board.pane(2).spectrum_scene.latest_frame, retained)
            self.assertEqual(product.handle.session.retained_resource_count, 2)
            self.assertEqual(self.phases(product)[2], PanePumpPhase.RUNNING)

    def test_stop_all_failure_releases_peers_but_retains_failed_rx_until_explicit_retry(self):
        with self.product() as product:
            product.ui.start_all.click()
            self.wait(lambda: all(phase is PanePumpPhase.RUNNING for phase in self.phases(product)))
            control = product.hf.factory.controls[0]
            control.stop_fail = True
            product.ui.stop_all.click()
            self.wait(lambda: self.phases(product) == (
                PanePumpPhase.STOPPED, PanePumpPhase.STOP_REQUIRED, PanePumpPhase.STOPPED))
            self.assertEqual(product.handle.session.retained_resource_count, 1)
            self.assertIsNotNone(product.hf.live._external_analyzer_owner)
            self.assertFalse(product.ui.close_layout.isEnabled())
            with self.assertRaisesRegex(RuntimeError, "confirmed Stop"):
                product.handle.shutdown_after_stop()
            self.assertEqual(product.pool.staged_resource_ids,
                             ("pane-resource-1", "pane-resource-2", "pane-resource-3"))
            attempts = len(control.stops)
            product.ui._refresh()
            self.assertEqual(len(control.stops), attempts)
            control.stop_fail = False
            product.ui.stop_all.click()  # Explicit cleanup retry, not a second Start.
            self.wait(product.handle.can_close)
            self.assertEqual(product.handle.session.retained_resource_count, 0)
            self.assertEqual(len(product.hf.factory.controls), 1)

    def test_slow_consumed_tinysa_stop_keeps_qt_and_other_families_running(self):
        consumed, release = Event(), Event()

        def slow_serial(ts):
            factory = ts.provider._acquisition_factory

            def acquisition(*args):
                owner = factory(*args)
                serial_factory = owner._factory

                def serial(route):
                    port = serial_factory(route)
                    original_read = port.read
                    port.on_write = lambda data: consumed.set() if data.startswith(b"scanraw ") else None

                    def read(size):
                        if consumed.is_set() and not release.wait(5):
                            raise RuntimeError("PRIVATE bounded fake scan timeout")
                        return original_read(size)

                    port.read = read
                    return port

                owner._factory = serial
                return owner

            ts.provider._acquisition_factory = acquisition

        try:
            with self.product(configure_tinysa=slow_serial) as product:
                try:
                    product.ui.start_all.click()
                    self.wait(consumed.is_set)
                    self.wait(lambda: all(phase is PanePumpPhase.RUNNING for phase in self.phases(product)))
                    self.publish_ad(product, 1)
                    self.wait(lambda: product.ui.board.pane(2).spectrum_scene.latest_frame is not None)
                    hf_sequence = product.ui.board.pane(2).spectrum_scene.latest_frame.spectrum.sequence
                    product.ui.board.select_slot(3)
                    product.ui.stop_selected.click()
                    self.wait(lambda: self.phases(product)[2] is PanePumpPhase.STOPPING)
                    self.assertEqual(product.handle.session.retained_resource_count, 3)
                    self.assertIsNotNone(product.ts.live._external_analyzer_owner)
                    self.assertFalse(product.ui.close_layout.isEnabled())
                    ticks = []
                    heartbeat = QTimer(product.ui)
                    heartbeat.timeout.connect(lambda: ticks.append(True))
                    heartbeat.start(5)
                    self.publish_ad(product, 2)
                    self.wait(lambda: len(ticks) >= 3 and
                              product.ui.board.pane(2).spectrum_scene.latest_frame.spectrum.sequence > hf_sequence)
                    heartbeat.stop()
                    self.assertEqual(self.phases(product), (
                        PanePumpPhase.RUNNING, PanePumpPhase.RUNNING, PanePumpPhase.STOPPING))
                    release.set()  # Consume the exact same scan response through its prompt.
                    self.wait(lambda: self.phases(product)[2] is PanePumpPhase.STOPPED)
                    self.assertIsNone(product.ui.board.pane(3).spectrum_scene.latest_frame)
                    self.assertEqual(product.handle.session.retained_resource_count, 2)
                    self.assertEqual(len(product.ts.serials), 1)
                    port = product.ts.serials[0]
                    self.assertEqual(port.calls.count("close"), 1)
                    scans = [call for call in port.calls if isinstance(call, tuple)
                             and call[0] == "write" and call[1].startswith(b"scanraw ")]
                    self.assertEqual(len(scans), 1)
                finally:
                    release.set()
        finally:
            release.set()

    def test_graph_close_failure_retains_same_graph_for_explicit_terminal_retry(self):
        with self.product() as product:
            product.ui.stop_all.click()  # Unstarted resources: no receiver is opened.
            self.wait(product.handle.can_close)
            with patch.object(product.graphs[1].live, "shutdown",
                              side_effect=RuntimeError("PRIVATE graph shutdown")) as close:
                with self.assertRaisesRegex(PaneGraphPoolError, "did not confirm"):
                    product.handle.shutdown_after_stop()
                close.assert_called_once()
            self.assertFalse(product.handle.shutdown_complete)
            self.assertEqual(product.pool.staged_resource_ids, ("pane-resource-2",))
            self.assertEqual(product.pool.cleanup_pending_resource_ids, ("pane-resource-2",))
            self.assertIs(product.pool.graph_for("pane-resource-2"), product.graphs[1])
            with self.assertRaisesRegex(RuntimeError, "terminal shutdown"):
                product.ui.release_presentation_after_shutdown()
            product.handle.shutdown_after_stop()
            self.assertTrue(product.handle.shutdown_complete)
            self.assertEqual(product.pool.staged_resource_ids, ())
            self.assertEqual(product.pool.cleanup_pending_resource_ids, ())
            self.assertEqual(product.native.engines, [])
            self.assertEqual(product.hf.factory.controls, [])
            self.assertEqual(product.ts.serials, [])

    def test_stop_during_accepted_start_waits_for_same_owner_and_never_publishes_that_rx(self):
        entered, release = Event(), Event()

        def slow_start(hf):
            original_create = hf.factory.create

            def create(permit):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("PRIVATE bounded fake Start timeout")
                return original_create(permit)

            hf.factory.create = create

        with self.product(configure_hackrf=slow_start) as product:
            try:
                product.ui.start_all.click()
                self.wait(entered.is_set)
                self.wait(lambda: self.phases(product) == (
                    PanePumpPhase.RUNNING, PanePumpPhase.STARTING, PanePumpPhase.RUNNING))
                self.assertTrue(all(not future.cancel() for future in product.ui._futures))
                product.ui.board.select_slot(2)
                product.ui.stop_selected.click()
                self.assertEqual(product.handle.session.retained_resource_count, 3)
                self.assertFalse(product.handle.can_close())
                self.publish_ad(product, 1)
                self.wait(lambda: product.ui.board.pane(3).spectrum_scene.latest_frame is not None)
                self.assertIsNone(product.ui.board.pane(2).spectrum_scene.latest_frame)
                release.set()
                self.wait(lambda: self.phases(product)[1] is PanePumpPhase.STOPPED)
                self.assertEqual(product.handle.session.retained_resource_count, 2)
                self.assertEqual(len(product.hf.factory.controls), 1)
                self.assertEqual(len(product.hf.factory.controls[0].stops), 1)
                self.assertIsNone(product.ui.board.pane(2).spectrum_scene.latest_frame)
                self.assertIsNone(product.hf.live._external_analyzer_owner)
                self.assertIsNone(product.graphs[1].live._pane_control_claim)
            finally:
                release.set()

    def test_control_claim_release_failure_keeps_lease_without_repeating_hardware_stop(self):
        with self.product() as product:
            product.ui.start_all.click()
            self.wait(lambda: all(phase is PanePumpPhase.RUNNING for phase in self.phases(product)))
            product.ui.board.select_slot(2)
            with patch.object(product.graphs[1].live, "release_pane_control",
                              side_effect=RuntimeError("PRIVATE claim release")):
                product.ui.stop_selected.click()
                self.wait(lambda: self.phases(product)[1] is PanePumpPhase.STOP_REQUIRED)
            control = product.hf.factory.controls[0]
            self.assertIsNone(product.hf.live._external_analyzer_owner)  # RX itself stopped.
            self.assertIsNotNone(product.graphs[1].live._pane_control_claim)
            self.assertEqual(product.handle.session.retained_resource_count, 3)
            self.assertFalse(product.handle.can_close())
            self.assertEqual(len(control.stops), 1)
            with self.assertRaisesRegex(RuntimeError, "reserved by a pane capture"):
                product.graphs[1].live.discover(startup=True)
            self.publish_ad(product, 1)
            product.ui.stop_selected.click()
            self.wait(lambda: self.phases(product)[1] is PanePumpPhase.STOPPED)
            self.assertEqual(len(control.stops), 1)  # Retry only the remaining claim obligation.
            self.assertIsNone(product.graphs[1].live._pane_control_claim)
            self.assertEqual(product.handle.session.retained_resource_count, 2)


if __name__ == "__main__":
    unittest.main()
