"""Real Qt view-specific custody receipts, separate from RF/FFT evidence."""
from __future__ import annotations

from types import SimpleNamespace
import os
import unittest

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analytical_journal import OwnerJournalScope
from sdr_monitor.domain.analytical_ready import ReadyClockMapping
from sdr_monitor.domain.analyzer import AnalyzerPublicationKind
from sdr_monitor.domain.layer_journal import LayerCreationEvent
from sdr_monitor.domain.layer_ready import DensityLayerIdentity, LayerReadyKind, LayerReadyReceipt
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage as Stage
from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView as View, PaneLayerAnalyticalIdentity
from sdr_monitor.domain.identity import TimestampQuality
from sdr_monitor.services.pane_delivery_ledger import PaneDeliveryLedger
from sdr_monitor.ui.v2.spectrum.persistence_contracts import DensityValueMode, PersistenceDensityFrame
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_spectrum
from sdr_monitor.ui.v2.waterfall.pane import WaterfallPane
from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.ui.v2_pane_presentation import PreparedPaneDelivery
from tests.test_app07_pane_delivery_obligations import identity as spectrum_identity


def live_frame(sequence: int = 1) -> LiveSpectrumFrame:
    return LiveSpectrumFrame(
        sequence=sequence, timestamp_ns=sequence * 1_000_000_000,
        center_frequency_hz=100.0, sample_rate_hz=4.0, fft_size=4, hop_size=2,
        frequencies_hz=np.array((98.5, 99.5, 100.5, 101.5)),
        values=np.array((-90.0, -80.0, -70.0, -60.0), dtype=np.float32), unit="dBm",
        source_id="device:source", config_generation=1, receiver_id="RX1",
            acquisition_epoch=1, clock_domain="unix_ns",
        timestamp_quality=TimestampQuality.HARDWARE,
    )


def density_frame(sequence: int = 1) -> PersistenceDensityFrame:
    density = np.array(((0.1, 0.4), (0.6, 0.9)), dtype=np.float32)
    frequencies = np.array((98.0, 100.0, 102.0))
    levels = np.array((-100.0, -70.0, -40.0))
    density.setflags(write=False)
    frequencies.setflags(write=False)
    levels.setflags(write=False)
    return PersistenceDensityFrame(
        density=density,
        frequency_edges_hz=frequencies,
        level_edges=levels,
        value_mode=DensityValueMode.PROBABILITY, level_unit="probability",
    )


def persistence_identity(sequence: int = 1) -> PaneLayerAnalyticalIdentity:
    scope = OwnerJournalScope("clock", 123, "owner", "device:source", "RX1",
                              "session", 1, 1)
    ready_identity = DensityLayerIdentity(
        "device:source", 1, sequence, sequence, "session", "RX1", 1, sequence,
    )
    ready = LayerReadyReceipt(
        LayerReadyKind.DENSITY, ready_identity, "clock", 123, 41, sequence,
        150 + sequence, ReadyClockMapping.OUTSIDE_SAMPLES,
        owner_run_id="owner", session_id="session",
    )
    event = LayerCreationEvent(LayerReadyKind.DENSITY, 41, sequence, 150 + sequence,
                               False, 0, 0, 0, 1, sequence, sequence, sequence)
    return PaneLayerAnalyticalIdentity(scope, ready, event, "resource", "capture", "rx",
                                       "one", 1, 1)


def packet(refs, pane_id="one", sequence=1):
    primary = next(ref for ref in refs if ref.view is View.SPECTRUM)
    bundle = SimpleNamespace(acquisition_epoch=1, publication_kind=AnalyzerPublicationKind.RTBW_FRAME,
                             spectrum=SimpleNamespace(sequence=sequence, revision=0))
    delivery = SimpleNamespace(pane_id=pane_id, host_activation_serial=1,
        obligation_ref=primary, layer_obligation_refs=tuple(ref for ref in refs if ref is not primary),
        bundle=bundle)
    prepared = object.__new__(PreparedPaneDelivery)
    object.__setattr__(prepared, "delivery", delivery)
    object.__setattr__(prepared, "active_obligation_refs", tuple(refs))
    object.__setattr__(prepared, "binding", None)
    object.__setattr__(prepared, "spectrum", None)
    object.__setattr__(prepared, "waterfall", None)
    object.__setattr__(prepared, "persistence", None)
    object.__setattr__(prepared, "producer_source_id", None)
    return prepared


class AllViewCustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_rtbw_queue_and_each_actual_canvas_paint_return(self):
        ledger = PaneDeliveryLedger(("one",))
        base = spectrum_identity()
        spectrum_ref = ledger.admit("one", base, view=View.SPECTRUM)
        waterfall_ref = ledger.admit("one", base, view=View.WATERFALL)
        persistence_ref = ledger.admit("one", persistence_identity(), view=View.PERSISTENCE)
        refs = (spectrum_ref, waterfall_ref, persistence_ref)
        self.assertTrue(all(ref is not None for ref in refs))
        for ref in refs:
            self.assertTrue(ledger.note(ref, Stage.PREPARING))
            self.assertTrue(ledger.note(ref, Stage.PREPARED))

        queue = PaneFairDeliveryQueue(("one",), stage_callback=ledger.note)
        self.assertTrue(queue.offer(packet(refs)))
        queued = queue.drain()
        self.assertEqual(len(queued), 1)
        self.assertEqual(ledger.snapshot().panes[0].pending, 3)

        scene = SpectrumScene()
        waterfall = WaterfallPane()
        scene.set_delivery_stage_callback(ledger.note)
        waterfall.set_delivery_stage_callback(ledger.note)
        frame = live_frame()
        density = density_frame()
        line = waterfall_line_from_spectrum(frame)
        scene.resize(740, 440)
        waterfall.resize(740, 250)
        scene.show()
        waterfall.show()

        self.assertTrue(ledger.note(spectrum_ref, Stage.UI_ADMITTED))
        self.assertTrue(ledger.note(waterfall_ref, Stage.UI_ADMITTED))
        self.assertTrue(ledger.note(persistence_ref, Stage.UI_ADMITTED))
        scene.set_frame(frame, obligation_ref=spectrum_ref)
        waterfall.set_line(line, obligation_ref=waterfall_ref)
        scene.set_persistence_delivery_ref(persistence_ref, density)
        scene.set_persistence_frame(density)
        for _ in range(5):
            self.app.processEvents()
            scene._graphics.viewport().repaint()
            waterfall._graphics.viewport().repaint()
        self.app.processEvents()

        snapshot = ledger.snapshot()
        states = {record.ref: record.stage for record in snapshot.records}
        self.assertEqual(states, {ref: Stage.PAINT_RETURNED for ref in refs})
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 0)
        self.assertEqual({item.view: item.counters.terminal for item in snapshot.views},
                         {View.SPECTRUM: 1, View.WATERFALL: 1, View.PERSISTENCE: 1})
        scene._graphics.viewport().repaint()
        waterfall._graphics.viewport().repaint()
        self.app.processEvents()
        self.assertEqual(sum(event.stage is Stage.PAINT_RETURNED for event in ledger.snapshot().events), 3)
        scene.stop_delivery_custody(refs)
        waterfall.stop_delivery_custody(refs)
        scene.release_graphics_after_shutdown()
        waterfall.release_presentation_after_shutdown()
        scene.close()
        waterfall.close()
        scene.deleteLater()
        waterfall.deleteLater()
        self.app.processEvents()

    def test_sweep_progress_has_no_waterfall_and_terminal_gap_keeps_both_refs(self):
        from tests.test_app07_pane_layer_custody import bind, packet as sweep_packet
        from tests.test_app01_sweep_progress import frame as progress_frame
        from tests.test_app07_layer_ready_admission import terminal_frame

        ledger = PaneDeliveryLedger(("one",))
        progress, progress_snapshot = sweep_packet(progress_frame())
        progress_identity = bind(progress, (progress_snapshot,))
        progress_ref = ledger.admit("one", progress_identity, view=View.SPECTRUM)
        self.assertIsNotNone(progress_ref)
        progress_delivery = SimpleNamespace(obligation_ref=None, layer_obligation_refs=(progress_ref,))
        self.assertEqual(tuple(ref.view for ref in __import__(
            "sdr_monitor.ui.v2_pane_obligation_refs", fromlist=["delivery_obligation_refs"]
        ).delivery_obligation_refs(progress_delivery)), (View.SPECTRUM,))
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUE_REJECTED):
            self.assertTrue(ledger.note(progress_ref, stage))

        terminal, terminal_snapshot = sweep_packet(terminal_frame(gap=True), creation=2)
        terminal_identity = bind(terminal, (terminal_snapshot,))
        terminal_spectrum = ledger.admit("one", terminal_identity, view=View.SPECTRUM)
        terminal_waterfall = ledger.admit("one", terminal_identity, view=View.WATERFALL)
        self.assertEqual(terminal.state.value, "gap")
        self.assertEqual((terminal_spectrum.view, terminal_waterfall.view),
                         (View.SPECTRUM, View.WATERFALL))
        for ref in (terminal_spectrum, terminal_waterfall):
            for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUE_REJECTED):
                self.assertTrue(ledger.note(ref, stage))
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 0)

    def test_unprepared_views_cancel_at_prep_and_stop_clear_is_pane_scoped(self):
        ledger = PaneDeliveryLedger(("one", "peer"))
        identity = spectrum_identity()
        spectrum = ledger.admit("one", identity, view=View.SPECTRUM)
        waterfall = ledger.admit("one", identity, view=View.WATERFALL)
        persistence = ledger.admit("one", persistence_identity(), view=View.PERSISTENCE)
        peer = ledger.admit("peer", spectrum_identity("peer"), view=View.SPECTRUM)
        for ref in (spectrum, waterfall, persistence, peer):
            self.assertTrue(ledger.note(ref, Stage.PREPARING))
        self.assertTrue(ledger.note(spectrum, Stage.PREPARED))
        self.assertTrue(ledger.note(waterfall, Stage.PREPARATION_CANCELLED))
        self.assertTrue(ledger.note(persistence, Stage.PREPARATION_CANCELLED))
        self.assertTrue(ledger.note(peer, Stage.PREPARED))

        queue = PaneFairDeliveryQueue(("one", "peer"), stage_callback=ledger.note)
        self.assertTrue(queue.offer(packet((spectrum,))))
        self.assertTrue(queue.offer(packet((peer,), pane_id="peer")))
        one, other = ledger.snapshot().panes
        self.assertEqual((one.pending, other.pending), (1, 1))
        queue.clear("one")
        self.assertEqual(queue.pending_count, 1)
        self.assertTrue(ledger.note(peer, Stage.QUEUE_DRAINED))
        self.assertTrue(ledger.note(peer, Stage.UI_REJECTED))
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(sum(item.pending for item in snapshot.panes), 0)
        self.assertEqual({record.ref: record.stage for record in snapshot.records}, {
            spectrum: Stage.STOP_CLEARED,
            waterfall: Stage.PREPARATION_CANCELLED,
            persistence: Stage.PREPARATION_CANCELLED,
            peer: Stage.UI_REJECTED,
        })

    def test_paired_two_pane_stop_cancels_only_paired_refs_and_preserves_peer(self):
        ledger = PaneDeliveryLedger(("rx1-pane", "rx2-pane", "peer"))
        rx1 = tuple(ledger.admit("rx1-pane", spectrum_identity("rx1-pane", offer=3), view=view)
                    for view in (View.SPECTRUM, View.WATERFALL))
        rx2 = tuple(ledger.admit("rx2-pane", spectrum_identity("rx2-pane", offer=3), view=view)
                    for view in (View.SPECTRUM, View.WATERFALL))
        peer = ledger.admit("peer", spectrum_identity(
            "peer", offer=9, physical_stream_resource_id="peer-resource"), view=View.SPECTRUM)
        refs = (*rx1, *rx2, peer)
        self.assertTrue(all(ref is not None for ref in refs))
        self.assertTrue(all(ref.identity.physical_stream_resource_id == "resource"
                            for ref in (*rx1, *rx2)))
        self.assertNotEqual(peer.identity.physical_stream_resource_id,
                            rx1[0].identity.physical_stream_resource_id)
        ledger.cancel_unclaimed("resource")
        stages = {record.ref: record.stage for record in ledger.snapshot().records}
        self.assertEqual({stages[ref] for ref in (*rx1, *rx2)}, {Stage.ADMISSION_CANCELLED})
        self.assertEqual(stages[peer], Stage.ADMITTED)
        self.assertEqual(ledger.snapshot().accounting_failures, 0)
        self.assertEqual(sum(pane.pending for pane in ledger.snapshot().panes), 1)

    def test_queue_supersession_fans_out_all_views_without_disturbing_peer(self):
        ledger = PaneDeliveryLedger(("one", "peer"))
        first_identity = spectrum_identity(offer=1)
        second_identity = spectrum_identity(offer=2)
        first = tuple(ledger.admit("one", first_identity, view=view) for view in
                      (View.SPECTRUM, View.WATERFALL)) + (
                          ledger.admit("one", persistence_identity(1), view=View.PERSISTENCE),)
        second = tuple(ledger.admit("one", second_identity, view=view) for view in
                       (View.SPECTRUM, View.WATERFALL)) + (
                           ledger.admit("one", persistence_identity(2), view=View.PERSISTENCE),)
        peer = ledger.admit("peer", spectrum_identity("peer"), view=View.SPECTRUM)
        for ref in (*first, *second, peer):
            self.assertTrue(ledger.note(ref, Stage.PREPARING))
            self.assertTrue(ledger.note(ref, Stage.PREPARED))
        queue = PaneFairDeliveryQueue(("one", "peer"), stage_callback=ledger.note)
        self.assertTrue(queue.offer(packet(first, sequence=1)))
        self.assertTrue(queue.offer(packet((peer,), pane_id="peer", sequence=1)))
        self.assertTrue(queue.offer(packet(second, sequence=2)))
        stages = {record.ref: record.stage for record in ledger.snapshot().records}
        self.assertEqual({stages[ref] for ref in first}, {Stage.QUEUE_SUPERSEDED})
        self.assertEqual({stages[ref] for ref in second}, {Stage.QUEUED})
        self.assertEqual(stages[peer], Stage.QUEUED)
        drained = queue.drain()
        self.assertEqual(len(drained), 2)
        for packet_ in drained:
            for ref in packet_.active_obligation_refs:
                self.assertTrue(ledger.note(ref, Stage.UI_REJECTED))
        snapshot = ledger.snapshot()
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(sum(item.pending for item in snapshot.panes), 0)

    def test_persistence_ref_waits_for_its_async_density_upload(self):
        from tests.ui_v2.test_app05_viewport_projection import ManualWorker
        from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector

        ledger = PaneDeliveryLedger(("one",))
        old_ref = ledger.admit("one", persistence_identity(1), view=View.PERSISTENCE)
        ref = ledger.admit("one", persistence_identity(2), view=View.PERSISTENCE)
        hidden_ref = ledger.admit("one", persistence_identity(3), view=View.PERSISTENCE)
        error_ref = ledger.admit("one", persistence_identity(4), view=View.PERSISTENCE)
        stop_ref = ledger.admit("one", persistence_identity(5), view=View.PERSISTENCE)
        none_ref = ledger.admit("one", persistence_identity(6), view=View.PERSISTENCE)
        for item in (old_ref, ref, hidden_ref, error_ref, stop_ref, none_ref):
            for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                          Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
                self.assertTrue(ledger.note(item, stage))

        spectrum_worker, persistence_worker = ManualWorker(), ManualWorker()
        projector = SpectrumProjector(spectrum_worker.submit,
                                      persistence_submit=persistence_worker.submit)
        scene = SpectrumScene()
        scene.set_projection_port(projector)
        scene.resize(740, 440)
        scene.show()
        self.app.processEvents()
        scene.set_frame(live_frame())
        scene.commit_projection()
        self.assertTrue(spectrum_worker.jobs)
        spectrum_worker.finish()
        self.app.processEvents()

        old_density = density_frame(1)
        scene.set_delivery_stage_callback(ledger.note)
        scene.set_persistence_delivery_ref(old_ref, old_density)
        scene.set_persistence_frame(old_density)
        scene.commit_projection()
        self.assertTrue(persistence_worker.jobs)
        self.assertFalse(scene._persistence.image_item.isVisible())
        scene._graphics.viewport().repaint()
        self.app.processEvents()
        stages = {record.ref: record.stage for record in ledger.snapshot().records}
        self.assertEqual(stages[old_ref], Stage.UI_ADMITTED)
        persistence_worker.finish()
        self.app.processEvents()
        scene._graphics.viewport().repaint()
        self.app.processEvents()
        self.assertTrue(scene._persistence.image_item.isVisible())
        self.assertEqual(ledger.snapshot().records[0].stage, Stage.PAINT_RETURNED)

        new_density = density_frame(2)
        scene.set_persistence_delivery_ref(ref, new_density)
        last_upload_ns = scene._persistence._last_upload_ns
        scene.set_persistence_frame(new_density, now_ns=last_upload_ns + 1)
        scene.commit_projection()
        self.assertFalse(persistence_worker.jobs)
        self.assertTrue(scene.persistence_delivery_waiting_for_upload(ref))
        self.assertTrue(scene._persistence.image_item.isVisible())
        # An unrelated repaint with the old image still visible is not proof
        # that the cadence-deferred new density was uploaded or painted.
        scene._graphics.viewport().repaint()
        self.app.processEvents()
        stages = {record.ref: record.stage for record in ledger.snapshot().records}
        self.assertEqual(stages[ref], Stage.UI_ADMITTED)
        scene._persistence.flush_pending(now_ns=last_upload_ns + scene._persistence._interval_ns + 1)
        scene.commit_projection()
        self.assertTrue(persistence_worker.jobs)

        persistence_worker.finish()
        self.app.processEvents()
        scene._graphics.viewport().repaint()
        self.app.processEvents()
        error_density = density_frame(4)
        scene.set_persistence_delivery_ref(error_ref, error_density)
        scene.set_persistence_frame(
            error_density, now_ns=scene._persistence._last_upload_ns + 2_000_000_000)
        scene.commit_projection()
        self.assertTrue(persistence_worker.jobs)
        persistence_worker.finish(error=RuntimeError("injected density projection failure"))
        self.app.processEvents()
        snapshot = ledger.snapshot()
        self.assertEqual({record.ref: record.stage for record in snapshot.records}, {
            old_ref: Stage.PAINT_RETURNED, ref: Stage.PAINT_RETURNED,
            hidden_ref: Stage.UI_ADMITTED,
            error_ref: Stage.UI_REJECTED,
            stop_ref: Stage.UI_ADMITTED,
            none_ref: Stage.UI_ADMITTED,
        })
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 3)

        none_density = density_frame(6)
        scene.set_persistence_delivery_ref(none_ref, none_density)
        scene.set_persistence_frame(
            none_density, now_ns=scene._persistence._last_upload_ns + 2_000_000_000)
        scene.commit_projection()
        self.assertTrue(persistence_worker.jobs)
        none_future, _operation = persistence_worker.jobs.pop(0)
        self.assertTrue(none_future.set_running_or_notify_cancel())
        none_future.set_result(None)
        self.app.processEvents()
        snapshot = ledger.snapshot()
        self.assertEqual({record.ref: record.stage for record in snapshot.records}[none_ref],
                         Stage.UI_REJECTED)
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 2)

        hidden_density = density_frame(3)
        scene.set_persistence_delivery_ref(hidden_ref, hidden_density)
        scene.set_persistence_frame(hidden_density,
                                    now_ns=scene._persistence._last_upload_ns + 1)
        self.assertTrue(scene.persistence_delivery_waiting_for_upload(hidden_ref))
        scene.set_persistence_visible(False)
        snapshot = ledger.snapshot()
        self.assertEqual({record.ref: record.stage for record in snapshot.records}, {
            old_ref: Stage.PAINT_RETURNED,
            ref: Stage.PAINT_RETURNED,
            hidden_ref: Stage.PAINT_SUPERSEDED,
            error_ref: Stage.UI_REJECTED,
            stop_ref: Stage.UI_ADMITTED,
            none_ref: Stage.UI_REJECTED,
        })
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 1)

        stopped_density = density_frame(5)
        scene.set_persistence_delivery_ref(stop_ref, stopped_density)
        scene.set_persistence_frame(stopped_density,
                                    now_ns=scene._persistence._last_upload_ns + 1)
        self.assertTrue(scene.persistence_delivery_waiting_for_upload(stop_ref))
        scene.stop_delivery_custody((stop_ref,))
        snapshot = ledger.snapshot()
        self.assertEqual({record.ref: record.stage for record in snapshot.records}[stop_ref],
                         Stage.STOP_CLEARED)
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 0)

        projector.dispose()
        scene.release_graphics_after_shutdown()
        scene.close()
        scene.deleteLater()
        self.app.processEvents()

    def test_older_density_none_or_error_terminalizes_exact_discarded_latest_ref(self):
        from tests.ui_v2.test_app05_viewport_projection import ManualWorker
        from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector

        for outcome in ("none", "error"):
            with self.subTest(outcome=outcome):
                ledger = PaneDeliveryLedger(("one",))
                old_ref = ledger.admit("one", persistence_identity(1), view=View.PERSISTENCE)
                new_ref = ledger.admit("one", persistence_identity(2), view=View.PERSISTENCE)
                for item in (old_ref, new_ref):
                    for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                                  Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
                        self.assertTrue(ledger.note(item, stage))

                spectrum_worker, persistence_worker = ManualWorker(), ManualWorker()
                projector = SpectrumProjector(spectrum_worker.submit,
                                              persistence_submit=persistence_worker.submit)
                scene = SpectrumScene()
                scene.set_projection_port(projector)
                scene.set_delivery_stage_callback(ledger.note)
                scene.set_frame(live_frame())
                scene.commit_projection()
                self.assertTrue(spectrum_worker.jobs)
                spectrum_worker.finish()
                self.app.processEvents()
                old_density, new_density = density_frame(1), density_frame(2)
                scene.set_persistence_delivery_ref(old_ref, old_density)
                scene.set_persistence_frame(old_density)
                scene.commit_projection()
                self.assertEqual(len(persistence_worker.jobs), 1)
                scene.set_persistence_delivery_ref(new_ref, new_density)
                scene.set_persistence_frame(new_density,
                    now_ns=scene._persistence._last_upload_ns or 1)
                self.assertTrue(scene._persistence.upload_pending_for(new_density))

                if outcome == "none":
                    future, _operation = persistence_worker.jobs.pop(0)
                    self.assertTrue(future.set_running_or_notify_cancel())
                    future.set_result(None)
                else:
                    persistence_worker.finish(error=RuntimeError("injected older density failure"))
                for _ in range(5):
                    self.app.processEvents()

                snapshot = ledger.snapshot()
                stages = {record.ref: record.stage for record in snapshot.records}
                self.assertEqual(stages[old_ref], Stage.PAINT_SUPERSEDED)
                self.assertEqual(stages[new_ref], Stage.UI_REJECTED)
                self.assertEqual(snapshot.accounting_failures, 0)
                self.assertEqual(snapshot.panes[0].pending, 0)
                self.assertIsNone(scene._persistence_delivery_ref)

                projector.dispose()
                scene.release_graphics_after_shutdown()
                scene.close()
                scene.deleteLater()
                self.app.processEvents()

    def test_persistence_clear_toolbar_cancels_exact_pending_ref_against_late_worker(self):
        from tests.ui_v2.test_app05_viewport_projection import ManualWorker
        from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjector

        ledger = PaneDeliveryLedger(("one",))
        ref = ledger.admit("one", persistence_identity(1), view=View.PERSISTENCE)
        self.assertIsNotNone(ref)
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                      Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
            self.assertTrue(ledger.note(ref, stage))

        spectrum_worker, persistence_worker = ManualWorker(), ManualWorker()
        projector = SpectrumProjector(spectrum_worker.submit,
                                      persistence_submit=persistence_worker.submit)
        scene = SpectrumScene()
        scene.set_projection_port(projector)
        scene.set_delivery_stage_callback(ledger.note)
        scene.resize(740, 440)
        scene.show()
        self.app.processEvents()
        scene.set_frame(live_frame())
        scene.commit_projection()
        self.assertTrue(spectrum_worker.jobs)
        spectrum_worker.finish()
        self.app.processEvents()
        first_density, pending_density = density_frame(1), density_frame(2)
        scene.set_persistence_frame(first_density)
        scene.commit_projection()
        self.assertEqual(len(persistence_worker.jobs), 1)
        persistence_worker.finish()
        self.app.processEvents()
        self.assertTrue(scene._persistence.image_item.isVisible())

        scene.set_persistence_delivery_ref(ref, pending_density)
        scene.set_persistence_frame(pending_density,
            now_ns=scene._persistence._last_upload_ns + scene._persistence._interval_ns + 1)
        scene.commit_projection()
        self.assertEqual(len(persistence_worker.jobs), 1)
        self.assertTrue(scene._persistence.image_item.isVisible())  # old pixels remain, not new proof
        scene._persistence_clear.click()
        snapshot = ledger.snapshot()
        self.assertEqual({record.ref: record.stage for record in snapshot.records}[ref],
                         Stage.PAINT_SUPERSEDED)
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 0)
        self.assertIsNone(scene._persistence_delivery_ref)

        # Late completion and unrelated repaint cannot resurrect the cleared ref.
        persistence_worker.finish()
        for _ in range(5):
            self.app.processEvents()
            scene._graphics.viewport().repaint()
        snapshot = ledger.snapshot()
        self.assertEqual({record.ref: record.stage for record in snapshot.records}[ref],
                         Stage.PAINT_SUPERSEDED)
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 0)
        self.assertFalse(scene._persistence.image_item.isVisible())

        projector.dispose()
        scene.release_graphics_after_shutdown()
        scene.close()
        scene.deleteLater()
        self.app.processEvents()

    def test_stale_projection_failure_cannot_cancel_newer_density_worker_or_ref(self):
        from tests.ui_v2.test_app05_viewport_projection import ManualWorker
        from sdr_monitor.ui.v2.spectrum.projection import ProjectionRequest, SpectrumProjector

        ledger = PaneDeliveryLedger(("one",))
        new_ref = ledger.admit("one", persistence_identity(2), view=View.PERSISTENCE)
        self.assertIsNotNone(new_ref)
        for stage in (Stage.PREPARING, Stage.PREPARED, Stage.QUEUED,
                      Stage.QUEUE_DRAINED, Stage.UI_ADMITTED):
            self.assertTrue(ledger.note(new_ref, stage))

        spectrum_worker, persistence_worker = ManualWorker(), ManualWorker()
        projector = SpectrumProjector(spectrum_worker.submit,
                                      persistence_submit=persistence_worker.submit)
        scene = SpectrumScene()
        scene.set_projection_port(projector)
        scene.set_delivery_stage_callback(ledger.note)
        scene.set_frame(live_frame())
        scene.commit_projection()
        spectrum_worker.finish()
        self.app.processEvents()

        old_density = density_frame(1)
        scene.set_persistence_frame(old_density)
        scene.commit_projection()
        self.assertTrue(persistence_worker.jobs)
        old_request = scene._persistence.worker_request
        self.assertIsNotNone(old_request)

        # Explicit visibility invalidates the first request. Re-enable starts
        # one current request under the existing worker/cadence policy.
        scene.set_persistence_visible(False)
        scene.set_persistence_visible(True)
        scene.commit_projection()
        newer_request = scene._persistence.worker_request
        self.assertIsNotNone(newer_request)
        self.assertIsNot(newer_request, old_request)

        new_density = density_frame(2)
        scene.set_persistence_delivery_ref(new_ref, new_density)
        scene.set_persistence_frame(new_density,
            now_ns=scene._persistence._last_upload_ns or 1)
        self.assertTrue(scene._persistence.upload_pending_for(new_density))
        stale_failure = ProjectionRequest(
            owner=scene._projection_owner, generation=-1, viewport=scene._viewport(),
            traces=(), persistence=old_request,
        )
        scene._projection_failed(stale_failure, "late stale density failure")
        self.assertIs(scene._persistence.worker_request, newer_request)
        self.assertTrue(scene._persistence.upload_pending_for(new_density))
        snapshot = ledger.snapshot()
        self.assertEqual({record.ref: record.stage for record in snapshot.records}[new_ref],
                         Stage.UI_ADMITTED)
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 1)

        scene.stop_delivery_custody((new_ref,))
        snapshot = ledger.snapshot()
        self.assertEqual({record.ref: record.stage for record in snapshot.records}[new_ref],
                         Stage.STOP_CLEARED)
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 0)
        projector.dispose()
        scene.release_graphics_after_shutdown()
        scene.close()
        scene.deleteLater()
        self.app.processEvents()

    def test_actual_fake_owner_pump_preparer_queue_board_and_three_views(self):
        from tempfile import TemporaryDirectory
        from dataclasses import replace as dataclass_replace
        from PySide6.QtCore import QSettings
        from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
        from sdr_monitor.domain.device_capabilities import stable_identity_key
        from sdr_monitor.domain.pane_scheduler import (
            CaptureMeasurementMode, PaneLayoutSlot, compile_pane_layout,
        )
        from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
        from sdr_monitor.services.pane_resource_session import PaneResourceSession
        from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
        from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
        from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
        from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
        from sdr_monitor.ui.v2_pane_runtime import _ResourceWorker
        from tests import test_app07_layer_ready_admission as layer_fixtures
        from tests import test_app07_pane_analytical_identity as analytical_fixtures
        from tests import test_app07_pane_layer_custody as layer_identity_fixtures
        from tests import test_app07_pane_resource_session as graph_fixtures
        from tests.test_app07_shared_capture_schedule import group, pane, profile

        group_spec = group("device", "rx")
        request = pane("one", "rx", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL)
        layout = compile_pane_layout((PaneLayoutSlot(1, request),), (group_spec,),
            {"capture": profile(36e6, CaptureMeasurementMode.RTBW)})
        class Owner(analytical_fixtures.ScopedOwner):
            def start_capture(self, job):
                admission = super().start_capture(job)
                self.scope = dataclass_replace(self.scope, receiver_id=None)
                return dataclass_replace(admission, owner_journal_scopes=(("rx", self.scope),))

        owner = Owner()
        session = PaneResourceSession(layout.schedule, (group_spec,), {"device": owner},
            ReceiverLeaseManager(),
            source_identity_keys={"device:source": stable_identity_key("device:source")})
        session.apply()
        activation = session.start_resource("device")
        self.addCleanup(session.stop_all)

        base = graph_fixtures.live_frame(owner.scope.source_id, owner.scope.session_id,
                                         owner.scope.acquisition_epoch)
        spectrum = dataclass_replace(base.spectrum, detector_ready=analytical_fixtures.ready(owner.scope))
        live_density = dataclass_replace(
            layer_fixtures.density_frame(), source_id=spectrum.source_id,
            config_generation=spectrum.config_generation, source_frame_sequence=spectrum.sequence,
            frequency_bins=len(spectrum.values), frequencies_hz=spectrum.frequencies_hz,
            density=np.zeros((2, len(spectrum.values)), dtype=np.float32), receiver_id=None,
            accumulation_id=owner.scope.session_id,
            acquisition_epoch=owner.scope.acquisition_epoch, native_accumulation_sequence=3)
        live_density, journal = layer_identity_fixtures.packet(live_density, scope=owner.scope)
        owner.layer_journal_snapshots = lambda: (journal,)
        owner.publications.append(("rx", AnalyzerFrameBundle(
            spectrum, base.session_id, base.receiver_id, base.acquisition_epoch,
            base.rtbw, persistence=live_density)))

        preparer = PaneDeliveryPreparer(layout, (group_spec,), PresentationAllocationBudget())
        ledger_callback = session.record_pane_delivery_stage
        queue = PaneFairDeliveryQueue(("one",), stage_callback=ledger_callback)
        worker = _ResourceWorker(layout.schedule.resources[0], ("one",), session,
                                 preparer, queue, 0.01, stage_callback=ledger_callback)
        worker._state = dataclass_replace(worker._state, activation=activation)
        worker._poll_and_advance()
        prepared = queue.drain()
        self.assertEqual(len(prepared), 1)
        self.assertEqual({ref.view for ref in prepared[0].active_obligation_refs},
                         {View.SPECTRUM, View.WATERFALL, View.PERSISTENCE})
        foreign = PaneDeliveryLedger(("one",)).admit(
            "one", prepared[0].delivery.analytical_identity, view=View.SPECTRUM)
        self.assertIsNotNone(foreign)
        with self.assertRaises(ValueError):
            dataclass_replace(prepared[0], active_obligation_refs=(foreign,))
        spectrum_ref = next(ref for ref in prepared[0].active_obligation_refs
                            if ref.view is View.SPECTRUM)
        with self.assertRaises(ValueError):
            dataclass_replace(prepared[0], active_obligation_refs=(spectrum_ref, spectrum_ref))
        with self.assertRaises(ValueError):
            dataclass_replace(prepared[0], waterfall=None)

        temp = TemporaryDirectory()
        settings = QSettings(os.path.join(temp.name, "all-view.ini"), QSettings.Format.IniFormat)
        board = IndependentPaneBoardV2(preparer, settings=settings, stage_callback=ledger_callback)
        board.resize(1200, 760)
        board.show()
        self.app.processEvents()
        self.assertTrue(board.apply_prepared(prepared[0]))
        pane_view = board.pane(1)
        for _ in range(5):
            self.app.processEvents()
            pane_view.spectrum_scene._graphics.viewport().repaint()
            pane_view.waterfall_pane._graphics.viewport().repaint()
        self.app.processEvents()

        snapshot = session.pane_delivery_ledger_snapshot()
        self.assertEqual(snapshot.accounting_failures, 0)
        self.assertEqual(snapshot.panes[0].pending, 0)
        self.assertEqual({record.ref.view: record.stage for record in snapshot.records}, {
            View.SPECTRUM: Stage.PAINT_RETURNED,
            View.WATERFALL: Stage.PAINT_RETURNED,
            View.PERSISTENCE: Stage.PAINT_RETURNED,
        })
        session.stop_all()
        board.release_presentation_after_shutdown()
        board.close()
        board.deleteLater()
        preparer.clear()
        temp.cleanup()
        self.app.processEvents()

    def test_real_fake_owner_sweep_partial_then_gap_row_through_pump_and_board(self):
        from dataclasses import replace as dataclass_replace
        from tempfile import TemporaryDirectory
        from PySide6.QtCore import QSettings
        from sdr_monitor.domain.analyzer import bundle_from_sweep
        from sdr_monitor.domain.device_capabilities import stable_identity_key
        from sdr_monitor.domain.pane_scheduler import (
            CaptureMeasurementMode, PaneLayoutSlot, compile_pane_layout,
        )
        from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
        from sdr_monitor.services.pane_resource_session import PaneResourceSession
        from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
        from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
        from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
        from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
        from sdr_monitor.ui.v2_pane_runtime import _ResourceWorker
        from tests import test_app01_sweep_progress as sweep_fixtures
        from tests import test_app07_layer_ready_admission as layer_ready_fixtures
        from tests import test_app07_pane_layer_custody as layer_identity_fixtures
        from tests import test_app07_pane_resource_session as graph_fixtures
        from tests.test_app07_shared_capture_schedule import group, pane, profile

        group_spec = group("device", "rx")
        layout = compile_pane_layout((PaneLayoutSlot(1, pane(
            "one", "rx", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL)),),
            (group_spec,), {"capture": profile(36e6, CaptureMeasurementMode.SWEEP)})
        owner = graph_fixtures.FakeOwner("device")
        session = PaneResourceSession(layout.schedule, (group_spec,), {"device": owner},
            ReceiverLeaseManager(),
            source_identity_keys={"device:source": stable_identity_key("device:source")})
        session.apply()
        activation = session.start_resource("device")
        preparer = PaneDeliveryPreparer(layout, (group_spec,), PresentationAllocationBudget())
        callback = session.record_pane_delivery_stage
        queue = PaneFairDeliveryQueue(("one",), stage_callback=callback)
        worker = _ResourceWorker(layout.schedule.resources[0], ("one",), session,
                                 preparer, queue, 0.01, stage_callback=callback)
        worker._state = dataclass_replace(worker._state, activation=activation)

        progress = sweep_fixtures.frame()
        sweep_epoch = owner.admission_epoch - 1
        progress = dataclass_replace(progress, source_id="device:source", epoch=sweep_epoch,
                                     frequencies_hz=sweep_fixtures.array((100e6, 108e6), np.float64))
        progress, snapshot = layer_identity_fixtures.packet(progress)
        owner.layer_journal_snapshots = lambda: (snapshot,)
        owner.publications.append(("rx", bundle_from_sweep(progress)))

        temp = TemporaryDirectory()
        board = IndependentPaneBoardV2(preparer, settings=QSettings(
            os.path.join(temp.name, "sweep-custody.ini"), QSettings.Format.IniFormat),
            stage_callback=callback)
        board.resize(1100, 700)
        board.show()
        self.app.processEvents()
        def cleanup_sweep_delivery():
            session.stop_all()
            board.release_presentation_after_shutdown()
            board.close()
            board.deleteLater()
            preparer.clear()
            temp.cleanup()
            self.app.processEvents()
        self.addCleanup(cleanup_sweep_delivery)

        worker._poll_and_advance()
        prepared_progress = queue.drain()
        self.assertEqual(len(prepared_progress), 1)
        self.assertEqual({ref.view for ref in prepared_progress[0].active_obligation_refs},
                         {View.SPECTRUM})
        self.assertTrue(board.apply_prepared(prepared_progress[0]))
        pane_view = board.pane(1)
        self.app.processEvents()
        pane_view.spectrum_scene._graphics.viewport().repaint()
        self.app.processEvents()
        progress_snapshot = session.pane_delivery_ledger_snapshot()
        self.assertEqual(progress_snapshot.panes[0].pending, 0)
        self.assertEqual(progress_snapshot.views[0].counters.pending, 0)
        self.assertEqual(pane_view.waterfall_pane.history_rows, 1)

        terminal = layer_ready_fixtures.terminal_frame(gap=True)
        terminal = dataclass_replace(terminal, sequence=progress.sequence + 1,
                                     source_id="device:source", epoch=sweep_epoch,
                                     frequencies_hz=sweep_fixtures.array((100e6, 108e6), np.float64))
        terminal, terminal_snapshot = layer_identity_fixtures.packet(
            terminal, scope=snapshot.scope, creation=2)
        owner.layer_journal_snapshots = lambda: (terminal_snapshot,)
        owner.publications.append(("rx", bundle_from_sweep(terminal)))
        worker._poll_and_advance()
        prepared_terminal = queue.drain()
        self.assertEqual(len(prepared_terminal), 1)
        self.assertEqual({ref.view for ref in prepared_terminal[0].active_obligation_refs},
                         {View.SPECTRUM, View.WATERFALL})
        self.assertTrue(board.apply_prepared(prepared_terminal[0]))
        for _ in range(5):
            self.app.processEvents()
            pane_view.spectrum_scene._graphics.viewport().repaint()
            pane_view.waterfall_pane._graphics.viewport().repaint()
        self.app.processEvents()

        final = session.pane_delivery_ledger_snapshot()
        self.assertEqual(final.accounting_failures, 0)
        self.assertEqual(final.panes[0].pending, 0)
        self.assertEqual(pane_view.waterfall_pane.history_rows, 2)
        self.assertTrue(all(record.stage is Stage.PAINT_RETURNED for record in final.records))

    def test_actual_two_pane_resource_stop_cancels_refs_but_not_independent_peer(self):
        from dataclasses import replace as dataclass_replace
        from sdr_monitor.domain.analyzer import bundle_from_sweep
        from sdr_monitor.domain.device_capabilities import stable_identity_key
        from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, compile_pane_schedule
        from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
        from sdr_monitor.services.pane_resource_session import PaneResourceSession
        from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
        from tests import test_app01_sweep_progress as sweep_fixtures
        from tests import test_app07_layer_ready_admission as layer_ready_fixtures
        from tests import test_app07_pane_layer_custody as layer_identity_fixtures
        from tests import test_app07_pane_resource_session as graph_fixtures
        from tests.test_app07_shared_capture_schedule import group, pane, profile

        groups = (group("device", "rx"), group("peer-device", "peer-rx"))
        requests = (
            pane("pair-low", "rx", 100e6, 108e6, ReceiverBindingMode.SHARED_CAPTURE),
            pane("pair-high", "rx", 120e6, 128e6, ReceiverBindingMode.SHARED_CAPTURE),
            pane("peer", "peer-rx", 140e6, 148e6, ReceiverBindingMode.DEDICATED_PARALLEL),
        )
        schedule = compile_pane_schedule(groups, requests,
            {"capture": profile(36e6, CaptureMeasurementMode.SWEEP)})
        pair_owner = graph_fixtures.FakeOwner("device")
        peer_owner = graph_fixtures.FakeOwner("peer-device")
        owners = {"device": pair_owner, "peer-device": peer_owner}
        session = PaneResourceSession(schedule, groups, owners, ReceiverLeaseManager(),
            source_identity_keys={"device:source": stable_identity_key("device:source"),
                                  "peer-device:source": stable_identity_key("peer-device:source")})
        self.addCleanup(session.stop_all)
        session.apply()
        pair_activation = session.start_resource("device")
        peer_activation = session.start_resource("peer-device")

        pair_progress = dataclass_replace(layer_ready_fixtures.terminal_frame(gap=True), source_id="device:source",
            epoch=pair_owner.admission_epoch - 1,
            frequencies_hz=sweep_fixtures.array((100e6, 128e6), np.float64))
        pair_progress, pair_snapshot = layer_identity_fixtures.packet(pair_progress)
        pair_owner.layer_journal_snapshots = lambda: (pair_snapshot,)
        pair_deliveries = session.accept_frame(pair_activation, "rx", bundle_from_sweep(pair_progress))
        self.assertEqual({item.pane_id for item in pair_deliveries}, {"pair-low", "pair-high"})
        pair_refs = tuple(ref for item in pair_deliveries for ref in item.layer_obligation_refs)

        peer_progress = dataclass_replace(layer_ready_fixtures.terminal_frame(gap=True),
            source_id="peer-device:source",
            epoch=peer_owner.admission_epoch - 1,
            frequencies_hz=sweep_fixtures.array((140e6, 148e6), np.float64))
        peer_progress, peer_snapshot = layer_identity_fixtures.packet(peer_progress)
        peer_owner.layer_journal_snapshots = lambda: (peer_snapshot,)
        peer_delivery = session.accept_frame(peer_activation, "peer-rx", bundle_from_sweep(peer_progress))[0]
        peer_refs = peer_delivery.layer_obligation_refs
        before = session.pane_delivery_ledger_snapshot()
        self.assertEqual(before.accounting_failures, 0)
        self.assertEqual(sum(item.pending for item in before.panes), 6)

        session.stop_resource("device")
        stopped = session.pane_delivery_ledger_snapshot()
        stages = {record.ref: record.stage for record in stopped.records}
        self.assertEqual({stages[ref] for ref in pair_refs}, {Stage.ADMISSION_CANCELLED})
        self.assertEqual({stages[ref] for ref in peer_refs}, {Stage.ADMITTED})
        self.assertEqual(stopped.accounting_failures, 0)
        self.assertEqual(sum(item.pending for item in stopped.panes), 2)
        self.assertFalse(pair_owner.running)
        self.assertTrue(peer_owner.running)

    def test_typed_rx1_rx2_canvas_stop_settles_pair_refs_and_peer_paints(self):
        from concurrent.futures import Future
        from dataclasses import replace as dataclass_replace
        from tempfile import TemporaryDirectory
        from PySide6.QtCore import QSettings, Qt
        from PySide6.QtWidgets import QWidget
        from sdr_monitor.domain.analyzer import PairedCaptureMetadata
        from sdr_monitor.domain.device_capabilities import stable_identity_key
        from sdr_monitor.domain.pane_scheduler import (
            CaptureMeasurementMode, PaneLayoutSlot, compile_pane_layout,
        )
        from sdr_monitor.domain.receiver_topology import (
            AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint,
        )
        from sdr_monitor.services.pane_resource_session import PaneResourceSession
        from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
        from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
        from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
        from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
        from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
        from sdr_monitor.ui.v2_pane_runtime import _ResourceWorker
        from tests import test_app07_pane_analytical_identity as analytical_fixtures
        from tests import test_app07_pane_resource_session as graph_fixtures
        from tests.test_app07_shared_capture_schedule import group, pane, profile

        class TypedOwner(graph_fixtures.FakeOwner):
            def __init__(self, resource_id, route_id, endpoints):
                super().__init__(resource_id)
                self.admission_mode = "rtbw"
                self.admission_generation = 5
                self.admission_source_id = route_id
                self.receiver_ids = {endpoint: receiver for endpoint, _source, receiver in endpoints}
                self.endpoints = endpoints
                self.scopes = {}

            def start_capture(self, job):
                admission = super().start_capture(job)
                producer_map = tuple((endpoint, source) for endpoint, source, _receiver in self.endpoints)
                self.scopes = {
                    endpoint: analytical_fixtures.owner_scope(
                        source_id=source, receiver_id=receiver,
                        acquisition_epoch=admission.acquisition_epoch,
                        configuration_generation=admission.config_generation,
                    )
                    for endpoint, source, receiver in self.endpoints
                }
                return dataclass_replace(admission, endpoint_source_ids=producer_map,
                    owner_journal_scopes=tuple((endpoint, self.scopes[endpoint])
                                               for endpoint in admission.receiver_endpoint_ids))

            def frame_bundle(self, endpoint, center_hz):
                scope = self.scopes[endpoint]
                frame = graph_fixtures.live_frame(scope.source_id, scope.session_id,
                    scope.acquisition_epoch, generation=scope.configuration_generation,
                    center_hz=center_hz, receiver_id=scope.receiver_id)
                ready = dataclass_replace(analytical_fixtures.ready(scope), receiver_id=scope.receiver_id)
                spectrum = dataclass_replace(frame.spectrum,
                    detector_ready=ready)
                return dataclass_replace(frame, spectrum=spectrum,
                    paired_capture=(None if self.physical_stream_resource_id == "peer" else
                        PairedCaptureMetadata(1, 0, 0)), identity=None)

        paired_endpoints = (
            ReceiverEndpoint("caller:rx1", "paired:route", "paired", ReceiverChainSelection.RX1),
            ReceiverEndpoint("caller:rx2", "paired:route", "paired", ReceiverChainSelection.RX2),
        )
        paired_group = AcquisitionGroup("paired:group", "paired", paired_endpoints)
        peer_group = group("peer", "peer-rx")
        groups = (paired_group, peer_group)
        requests = (
            pane("left", "caller:rx1", 2_440e6, 2_450e6),
            pane("right", "caller:rx2", 2_450e6, 2_460e6),
            pane("peer-pane", "peer-rx", 100e6, 108e6,
                 ReceiverBindingMode.DEDICATED_PARALLEL),
        )
        layout = compile_pane_layout(tuple(PaneLayoutSlot(number, request)
            for number, request in enumerate(requests, 1)), groups,
            {"capture": profile(36e6, CaptureMeasurementMode.RTBW)})
        pair_owner = TypedOwner("paired", "paired:route", (
            ("caller:rx1", "caller:rx1", "RX1"),
            ("caller:rx2", "caller:rx2", "RX2"),
        ))
        peer_owner = TypedOwner("peer", "peer:source", (("peer-rx", "peer:source", "RX1"),))
        session = PaneResourceSession(layout.schedule, groups,
            {"paired": pair_owner, "peer": peer_owner}, ReceiverLeaseManager(),
            source_identity_keys={"paired:route": stable_identity_key("paired:route"),
                                  "peer:source": stable_identity_key("peer:source")})
        self.addCleanup(session.stop_all)
        session.apply()
        pair_activation = session.start_resource("paired")
        peer_activation = session.start_resource("peer")
        pair_owner.publications.extend((
            ("caller:rx1", pair_owner.frame_bundle("caller:rx1", 2_445e6)),
            ("caller:rx2", pair_owner.frame_bundle("caller:rx2", 2_455e6)),
        ))
        peer_owner.publications.append(("peer-rx", peer_owner.frame_bundle("peer-rx", 104e6)))

        preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget(),
            admitted_producer_source_id=session.admitted_producer_source_id)
        callback = session.record_pane_delivery_stage
        queue = PaneFairDeliveryQueue(("left", "right", "peer-pane"), stage_callback=callback)
        pair_worker = _ResourceWorker(next(item for item in layout.schedule.resources
            if item.physical_stream_resource_id == "paired"), ("left", "right"), session,
            preparer, queue, 0.01, stage_callback=callback)
        pair_worker._state = dataclass_replace(pair_worker._state, activation=pair_activation)
        peer_worker = _ResourceWorker(next(item for item in layout.schedule.resources
            if item.physical_stream_resource_id == "peer"), ("peer-pane",), session,
            preparer, queue, 0.01, stage_callback=callback)
        peer_worker._state = dataclass_replace(peer_worker._state, activation=peer_activation)
        pair_worker._poll_and_advance()
        peer_worker._poll_and_advance()
        prepared = queue.drain()
        self.assertEqual({item.binding.pane_id for item in prepared}, {"left", "right", "peer-pane"})
        self.assertEqual({item.producer_source_id for item in prepared if item.binding.pane_id != "peer-pane"},
                         {"caller:rx1", "caller:rx2"})

        temp = TemporaryDirectory()
        board = IndependentPaneBoardV2(preparer, settings=QSettings(
            os.path.join(temp.name, "typed-paired-custody.ini"), QSettings.Format.IniFormat),
            stage_callback=callback)
        board.resize(1500, 900)
        board.show()
        self.app.processEvents()
        try:
            for item in prepared:
                self.assertTrue(board.apply_prepared(item))
            by_pane = {item.binding.pane_id: item for item in prepared}
            pair_refs = tuple(ref for pane_id in ("left", "right")
                              for ref in by_pane[pane_id].active_obligation_refs)
            self.assertEqual({ref.view for ref in pair_refs}, {View.SPECTRUM, View.WATERFALL})

            class StopBoundaryHarness(IndependentPaneSessionV2):
                def __init__(self):
                    QWidget.__init__(self)
                    self.handle = SimpleNamespace(session=session, layout=layout)
                    self.board = board
                    self.stop_boundary.connect(self._on_stop_boundary,
                                               Qt.ConnectionType.QueuedConnection)

            boundary = StopBoundaryHarness()
            stop_future = Future()
            boundary._watch_stop_boundary(stop_future, "paired")
            peer_refs = by_pane["peer-pane"].active_obligation_refs
            pre_stop = session.pane_delivery_ledger_snapshot()
            pre_stop_stages = {record.ref: record.stage for record in pre_stop.records}

            # Source Stop has completed, but the UI has not yet received its
            # production queued boundary signal; paint-owned refs stay pending.
            session.stop_resource("paired")
            before_receipt = session.pane_delivery_ledger_snapshot()
            before_stages = {record.ref: record.stage for record in before_receipt.records}
            self.assertTrue(all(before_stages[ref] in {Stage.UI_ADMITTED, Stage.PAINT_SCHEDULED}
                                for ref in pair_refs))
            self.assertEqual(sum(before_stages[ref] in {Stage.QUEUE_DRAINED, Stage.UI_ADMITTED,
                                                        Stage.PAINT_SCHEDULED} for ref in pair_refs), 4)
            self.assertEqual({ref: before_stages[ref] for ref in peer_refs},
                             {ref: pre_stop_stages[ref] for ref in peer_refs})
            self.assertEqual(before_receipt.accounting_failures, 0)
            stop_future.set_result(None)
            self.app.processEvents()
            for pane_id, slot in (("left", 1), ("right", 2)):
                settled_pane = board.pane(slot)
                expected = by_pane[pane_id].active_obligation_refs
                self.assertIsNone(settled_pane.waterfall_pane._waterfall_delivery_ref)
                self.assertFalse(any(ref in expected for ref in (
                    settled_pane.spectrum_scene._latest_delivery_ref,
                    settled_pane.spectrum_scene.displayed_delivery_ref)))
            peer_view = board.pane(3)
            for _ in range(5):
                self.app.processEvents()
                peer_view.spectrum_scene._graphics.viewport().repaint()
                peer_view.waterfall_pane._graphics.viewport().repaint()
            self.app.processEvents()
            snapshot = session.pane_delivery_ledger_snapshot()
            stages = {record.ref: record.stage for record in snapshot.records}
            self.assertTrue(all(stages[ref] in {Stage.STOP_CLEARED, Stage.PAINT_RETURNED}
                                for ref in pair_refs))
            self.assertEqual({stages[ref] for ref in peer_refs}, {Stage.PAINT_RETURNED})
            self.assertEqual(snapshot.accounting_failures, 0)
            self.assertEqual(sum(item.pending for item in snapshot.panes), 0)
            counts = {(item.counters.pane_id, item.view): item.counters for item in snapshot.views}
            for pane_id in ("left", "right", "peer-pane"):
                for view in (View.SPECTRUM, View.WATERFALL):
                    self.assertEqual(counts[pane_id, view].pending, 0)
                    self.assertEqual(counts[pane_id, view].terminal, 1)
            self.assertFalse(pair_owner.running)
            self.assertTrue(peer_owner.running)
            session.stop_all()
        finally:
            session.stop_all()
            board.release_presentation_after_shutdown()
            board.close()
            boundary.deleteLater()
            self.app.processEvents()
            final_snapshot = session.pane_delivery_ledger_snapshot()
            self.assertEqual(final_snapshot.accounting_failures, 0)
            self.assertEqual(sum(item.pending for item in final_snapshot.panes), 0)
            final_stages = {record.ref: record.stage for record in final_snapshot.records}
            terminal_stages = {Stage.STOP_CLEARED, Stage.PAINT_SUPERSEDED, Stage.PAINT_RETURNED}
            self.assertTrue(all(final_stages[ref] in terminal_stages for ref in pair_refs))
            self.assertTrue(all(final_stages[ref] in terminal_stages
                                for ref in by_pane["peer-pane"].active_obligation_refs))
            board.deleteLater()
            preparer.clear()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
