"""Real V2 pane UI/owners with SDR Sweep publications, fake SDK only.

SDR Sweep has per-segment generations, never a single producer generation.
These fixtures preserve that absence through progressive and terminal frames.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneControlGapReason
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
from sdr_monitor.ui.v2.workspaces.rf_shift_dialog import RfImpactDialog

from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app06_hackrf_sweep_common_analyzer import FakeHackrfSweep
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as tinysa_fixture
from tests.ui_v2.test_app07_ad936x_sweep_pane_owner import ad_sweep_graph
from tests.ui_v2.test_app07_rf_gesture import middle_move, plot_points
from tests.ui_v2 import test_app07_independent_failures as rtbw_fixtures


class _HeldHackrfSweep(FakeHackrfSweep):
    hold_terminal = True

    def poll_latest(self):
        if self.hold_terminal and self._publications and self._publications[0].line is not None:
            return ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics())
        return super().poll_latest()


class SdrSweepRfUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate, timeout=5):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            sleep(.005)
        self.fail("bounded SDR Sweep RF UI observation timed out")

    @contextmanager
    def product(self, family, *, shared=False):
        if family == "ad936x":
            _native, fake, primary = ad_sweep_graph()
            fake.hold_terminal = True
            rate, fft = 61.44e6, 4096
        else:
            hf = hackrf_fixture()
            fake = _HeldHackrfSweep()
            primary = build_v2_analyzer_application_graph(SimpleNamespace(
                live_sdr=hf.live, device_catalog=hf.catalog,
                analyzer_hackrf=hf.hackrf, analyzer_hackrf_sweep=fake))
            rate, fft = 20e6, 2048
        ts = tinysa_fixture()
        peer = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=ts.live, device_catalog=ts.catalog, analyzer_tinysa=ts.instrument))
        source = next(choice.device_id for choice in primary.live.discover(startup=True)
                      if choice.family.value == family)
        peer_source = peer.live.discover(startup=True)[0].device_id
        first = PaneSlotDraft(1, source, 100e6, 220e6, sample_rate_hz=rate,
                             fft_size=fft, measurement_mode=CaptureMeasurementMode.SWEEP)
        drafts = (first, PaneSlotDraft(2, source, 100e6, 220e6,
                    sample_rate_hz=rate, fft_size=fft, measurement_mode=CaptureMeasurementMode.SWEEP)
                  if shared else PaneSlotDraft(2),
                  PaneSlotDraft(3, peer_source, 100e6, 300e6, points=3), PaneSlotDraft(4))
        pool = PaneProductGraphPool(lambda key: primary if key == "pane-resource-1" else peer)
        prepared = prepare_user_pane_session(drafts, pool_factory=lambda: pool)
        apply_user_pane_session(prepared)
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "sdr-sweep.ini"), QSettings.Format.IniFormat)
            with patch("sdr_monitor.ui.v2.workspaces.independent_pane_board.QSettings", return_value=settings):
                ui = IndependentPaneSessionV2(prepared.handle)
            ui.resize(1400, 850)
            ui.show()
            product = SimpleNamespace(fake=fake, primary=primary, peer=peer,
                                      handle=prepared.handle, ui=ui, serials=ts.serials)
            try:
                yield product
            finally:
                ui._cancel_rf_change()
                for future in prepared.handle.pump.stop_all().values():
                    future.result(timeout=5)
                self.wait(lambda: ui._rf_phase is None)
                prepared.handle.shutdown_after_stop()
                ui.release_presentation_after_shutdown()
                ui.close()
                ui.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                primary.live.shutdown()
                peer.live.shutdown()
                self.assertTrue(all(not worker._thread.is_alive()
                                    for worker in prepared.handle.pump._workers.values()))
                self.assertEqual(pool.staged_resource_ids, ())

    @staticmethod
    def state(product, resource="pane-resource-1"):
        return next(item for item in product.handle.pump.snapshot()
                    if item.physical_stream_resource_id == resource)

    def start(self, product):
        product.ui.start_all.click()
        self.wait(lambda: all(item.phase is PanePumpPhase.RUNNING
                              for item in product.handle.pump.snapshot())
                  and product.ui.board.pane(1).last_bundle is not None
                  and product.ui.board.pane(3).last_bundle is not None)

    def preview(self, product):
        self.wait(lambda: product.ui.rf_shift.isEnabled())
        product.ui._request_rf_shift(1, 5e6, product.ui.board.rf_anchor(1))
        self.wait(lambda: isinstance(product.ui._rf_dialog, RfImpactDialog))
        return product.ui._rf_dialog

    def test_progress_anchor_does_not_invent_sdr_configuration_generation(self):
        for family in ("ad936x", "hackrf"):
            with self.subTest(family=family), self.product(family) as product:
                self.assertIsNone(product.ui.board.rf_anchor(1))
                self.start(product)
                identity = product.ui.board.pane(1).last_bundle.identity
                self.assertIsNone(identity.config_generation)
                self.assertIsNone(identity.clock_domain)
                self.assertIsNotNone(product.ui.board.rf_anchor(1))
                self.wait(lambda: product.ui.rf_shift.isEnabled())
                self.assertIsNone(product.ui.board.rf_anchor(2))
                self.assertIsNone(product.ui.board.rf_anchor(4))

    def test_progress_to_terminal_keeps_anchor_and_segment_provenance(self):
        for family in ("ad936x", "hackrf"):
            with self.subTest(family=family), self.product(family) as product:
                self.start(product)
                old = product.ui.board.rf_anchor(1)
                progress = product.ui.board.pane(1).last_bundle
                self.assertEqual(progress.publication_kind.value, "sweep_progress")
                product.fake.hold_terminal = False
                self.wait(lambda: product.ui.board.pane(1).last_bundle.terminal_sweep)
                line = product.ui.board.pane(1).last_bundle
                self.assertIsNone(line.identity.config_generation)
                self.assertEqual(product.ui.board.rf_anchor(1), old)
                self.assertEqual(line.spectrum.segment_config_generations,
                                 product.fake.line.segment_config_generations if family == "ad936x"
                                 else ((0, product.fake.request.epoch), (1, product.fake.request.epoch)))

    def test_numeric_preview_default_cancel_is_inert_for_both_sdrs(self):
        for family in ("ad936x", "hackrf"):
            with self.subTest(family=family), self.product(family) as product:
                self.start(product)
                activation = self.state(product).activation
                starts = product.fake.events.count("start")
                dialog = self.preview(product)
                self.assertTrue(dialog.cancel_button.isDefault())
                QTest.keyClick(dialog, Qt.Key.Key_Return)
                self.wait(lambda: product.ui._rf_phase is None)
                self.assertEqual(self.state(product).activation, activation)
                self.assertEqual(product.fake.events.count("start"), starts)

    def test_actual_middle_input_on_sdr_spectrum_and_waterfall_opens_preview(self):
        for family in ("ad936x", "hackrf"):
            with self.subTest(family=family), self.product(family) as product:
                self.start(product)
                self.wait(lambda: product.ui.rf_shift.isEnabled())
                pane = product.ui.board.pane(1)
                for plot in (pane.spectrum_scene, pane.waterfall_pane):
                    graphics, first, last = plot_points(plot, -60)
                    QTest.mousePress(graphics.viewport(), Qt.MouseButton.MiddleButton, pos=first)
                    middle_move(graphics, last)
                    self.assertIsNone(product.ui._rf_phase)
                    QTest.mouseRelease(graphics.viewport(), Qt.MouseButton.MiddleButton, pos=last)
                    self.wait(lambda: isinstance(product.ui._rf_dialog, RfImpactDialog))
                    product.ui._rf_dialog.cancel_button.click()
                    self.wait(lambda: product.ui._rf_phase is None)

    def test_shared_approval_resets_both_target_histories_before_start_keeps_peer(self):
        for family in ("ad936x", "hackrf"):
            with self.subTest(family=family), self.product(family, shared=True) as product:
                self.start(product)
                self.wait(lambda: product.ui.board.pane(2).last_bundle is not None)
                old = self.state(product).activation
                peer = self.state(product, "pane-resource-2").activation
                peer_binding = product.handle.preparer.bindings["pane-3"]
                peer_rows = product.ui.board.pane(3).waterfall_pane.history_rows
                dialog = self.preview(product)
                self.assertEqual(product.ui._rf_preview.resource.affected_pane_ids, ("pane-1", "pane-2"))
                seen = []
                original = product.handle.pump.start_resource

                def start(resource):
                    seen.append((resource, tuple(product.ui.board.pane(n).last_bundle for n in (1, 2)),
                                 tuple(product.ui.board.pane(n).waterfall_pane.history_rows for n in (1, 2))))
                    return original(resource)

                with patch.object(product.handle.pump, "start_resource", side_effect=start):
                    dialog.confirm_button.click()
                    self.wait(lambda: product.ui._rf_phase is None)
                self.assertEqual(seen, [("pane-resource-1", (None, None), (0, 0))])
                self.assertGreater(self.state(product).activation.host_activation_serial, old.host_activation_serial)
                self.assertIs(self.state(product).activation.planned_control_gap.reason,
                              PaneControlGapReason.PROFILE_OR_RF_PLAN_CHANGE)
                self.assertEqual(tuple((draft.start_hz, draft.stop_hz)
                    for draft in product.handle.rf_context.drafts[:2]), ((105e6, 225e6), (100e6, 220e6)))
                self.assertEqual(self.state(product, "pane-resource-2").activation, peer)
                self.assertIs(product.handle.preparer.bindings["pane-3"], peer_binding)
                self.assertGreaterEqual(product.ui.board.pane(3).waterfall_pane.history_rows, peer_rows)

    def test_stopped_apply_arms_only_and_separate_start_has_a_new_anchor(self):
        for family in ("ad936x", "hackrf"):
            with self.subTest(family=family), self.product(family) as product:
                self.start(product)
                old = product.ui.board.rf_anchor(1)
                product.handle.pump.stop_resource("pane-resource-1").result(timeout=5)
                self.wait(lambda: product.ui.rf_shift.isEnabled())
                starts = product.fake.events.count("start")
                dialog = self.preview(product)
                self.assertFalse(product.ui._rf_preview.resource.restart_required)
                dialog.confirm_button.click()
                self.wait(lambda: product.ui._rf_phase is None)
                self.assertIs(self.state(product).phase, PanePumpPhase.STOPPED)
                self.assertEqual(product.fake.events.count("start"), starts)
                self.assertIsNone(product.ui.board.rf_anchor(1))
                product.ui.start_selected.click()
                self.wait(lambda: product.ui.board.rf_anchor(1) is not None)
                self.assertNotEqual(product.ui.board.rf_anchor(1), old)
                self.assertIsNone(product.ui.board.pane(1).last_bundle.identity.config_generation)

    def test_new_sweep_epoch_during_held_middle_cancels_the_stale_proposal(self):
        for family in ("ad936x", "hackrf"):
            with self.subTest(family=family), self.product(family) as product:
                self.start(product)
                self.wait(lambda: product.ui.rf_shift.isEnabled())
                pane = product.ui.board.pane(1)
                old = product.ui.board.rf_anchor(1)
                graphics, first, last = plot_points(pane.spectrum_scene, -60)
                QTest.mousePress(graphics.viewport(), Qt.MouseButton.MiddleButton, pos=first)
                middle_move(graphics, last)
                product.handle.pump.stop_resource("pane-resource-1").result(timeout=5)
                product.handle.pump.start_resource("pane-resource-1").result(timeout=5)
                self.wait(lambda: product.ui.board.rf_anchor(1) is not None
                          and product.ui.board.rf_anchor(1) != old)
                QTest.mouseRelease(graphics.viewport(), Qt.MouseButton.MiddleButton, pos=last)
                self.app.processEvents()
                self.assertIsNone(product.ui._rf_phase)
                self.assertIsNone(product.ui._rf_dialog)

    def test_missing_rtbw_generation_still_refuses_the_anchor(self):
        harness = rtbw_fixtures.IndependentPaneFailureTests()
        harness.app = self.app
        with harness.product() as product:
            product.ui.start_all.click()
            self.wait(lambda: all(item.phase is PanePumpPhase.RUNNING
                                  for item in product.handle.pump.snapshot()))
            harness.publish_ad(product, 1)
            pane = product.ui.board.pane(1)
            self.assertIsNotNone(product.ui.board.rf_anchor(1))
            with patch.object(pane, "_last_identity", replace(pane._last_identity, config_generation=None)):
                self.assertIsNone(product.ui.board.rf_anchor(1))
            self.assertIsNotNone(product.ui.board.rf_anchor(1))

    def test_instrument_trace_generation_guard_is_not_relaxed(self):
        with self.product("hackrf") as product:
            self.start(product)
            pane = product.ui.board.pane(3)
            self.assertIsNotNone(product.ui.board.rf_anchor(3))
            with patch.object(pane, "_last_identity", replace(pane._last_identity, config_generation=None)):
                self.assertIsNone(product.ui.board.rf_anchor(3))
            self.assertIsNotNone(product.ui.board.rf_anchor(3))


if __name__ == "__main__":
    unittest.main()
