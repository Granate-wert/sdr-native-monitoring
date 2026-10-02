"""An admitted paired input epoch clears both Qt histories before either new RX."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer import PairedCaptureMetadata
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneLayoutSlot, compile_pane_layout
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint,
)
from sdr_monitor.services.pane_resource_session import PaneDelivery
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame
from sdr_monitor.ui.v2.spectrum.persistence_contracts import DensityValueMode, PersistenceDensityFrame
from sdr_monitor.ui.v2.spectrum.projection import SpectrumProjection, SpectrumProjector
from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer, PreparedPaneDelivery

from tests.test_app07_pane_resource_session import live_frame
from tests.test_app07_shared_capture_schedule import group, pane, profile


class PairedVisibleEpochTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        endpoints = (
            ReceiverEndpoint("caller:rx1", "paired:route", "paired", ReceiverChainSelection.RX1),
            ReceiverEndpoint("caller:rx2", "paired:route", "paired", ReceiverChainSelection.RX2),
        )
        groups = (AcquisitionGroup("paired:group", "paired", endpoints), group("peer", "peer-rx"))
        slots = (
            PaneLayoutSlot(1, pane("left", "caller:rx1", 2_440e6, 2_450e6)),
            PaneLayoutSlot(2, pane("right", "caller:rx2", 2_450e6, 2_460e6)),
            PaneLayoutSlot(3, pane("peer-pane", "peer-rx", 100e6, 108e6,
                                   ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(4),
        )
        layout = compile_pane_layout(slots, groups, {"capture": profile(36e6, CaptureMeasurementMode.RTBW)})
        self.preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget(),
            admitted_producer_source_id=lambda resource, endpoint: (
                endpoint if resource == "paired" else "peer:source"))
        settings = QSettings(str(Path(self.temp.name) / "paired.ini"), QSettings.Format.IniFormat)
        self.board = IndependentPaneBoardV2(self.preparer, settings=settings)

    def tearDown(self) -> None:
        self.board.release_presentation_after_shutdown()
        self.board.close()
        self.preparer.clear()
        self.temp.cleanup()

    def prepared(self, pane_id: str, *, sequence: int, synchronization_epoch: int = 1,
                 activation: int = 1, run: int = 1, acquisition_epoch: int = 7,
                 session_id: str = "session-1") -> PreparedPaneDelivery:
        binding = self.preparer.bindings[pane_id]
        receiver = {"left": "RX1", "right": "RX2"}.get(pane_id)
        center = 104e6 if pane_id == "peer-pane" else 2_450e6
        source = binding.source_id if receiver is None else binding.receiver_endpoint_id
        bundle = live_frame(source, session_id, acquisition_epoch, center_hz=center,
                            receiver_id=receiver)
        frame = replace(bundle.spectrum, sequence=sequence, timestamp_ns=sequence,
                        acquisition_epoch=acquisition_epoch)
        metadata = (None if receiver is None else PairedCaptureMetadata(
            synchronization_epoch, (sequence - 1) * 4096, max(0, synchronization_epoch - 1)))
        bundle = replace(bundle, spectrum=frame, paired_capture=metadata, identity=None)
        delivery = PaneDelivery(binding.physical_stream_resource_id, binding.capture_id,
                                binding.receiver_endpoint_id, activation, binding.crop, bundle,
                                float(sequence), run)
        return self.preparer.prepare(delivery)

    def rows(self) -> tuple[int, int, int]:
        return tuple(self.board.pane(number).waterfall_pane.history_rows for number in (1, 2, 3))

    def test_caption_uses_typed_chain_and_mode_not_misleading_endpoint_suffix(self) -> None:
        left = self.preparer.bindings["left"]
        right = self.preparer.bindings["right"]
        board_label = IndependentPaneBoardV2._binding_label
        self.assertIn("paired:route / RX1 ·", board_label(replace(
            left, receiver_endpoint_id="looks-like:trace")))
        self.assertIn("Pluto / RX2 ·", board_label(replace(
            right, receiver_endpoint_id="looks-like:rx1"), "Pluto"))
        trace = replace(left, receiver_endpoint_id="looks-like:rx1",
                        receiver_selection=None,
                        measurement_mode=CaptureMeasurementMode.INSTRUMENT_TRACE)
        self.assertIn("Pluto / trace ·", board_label(trace, "Pluto"))

    def test_typed_pair_requires_metadata_from_first_packet_and_peer_cannot_gain_pair_status(self) -> None:
        first = self.prepared("left", sequence=1)
        missing_delivery = replace(first.delivery,
            bundle=replace(first.bundle, paired_capture=None))
        with self.assertRaisesRegex(ValueError, "pair metadata"):
            self.preparer.prepare(missing_delivery)
        missing = replace(first, delivery=missing_delivery,
                          spectrum=PreparedSpectrumFrame(missing_delivery.bundle))
        self.assertFalse(self.board.apply_prepared(missing))
        self.assertEqual(self.rows(), (0, 0, 0))
        self.assertTrue(self.board.apply_prepared(self.prepared("peer-pane", sequence=1)))
        peer_binding = self.preparer.bindings["peer-pane"]
        peer_bundle = live_frame(peer_binding.source_id, "session-1", 7,
                                 center_hz=104e6, receiver_id="RX1")
        peer_bundle = replace(peer_bundle, paired_capture=PairedCaptureMetadata(1, 0, 0))
        peer_delivery = PaneDelivery(peer_binding.physical_stream_resource_id,
            peer_binding.capture_id, peer_binding.receiver_endpoint_id, 1,
            peer_binding.crop, peer_bundle, 1.0, 1)
        with self.assertRaisesRegex(ValueError, "pair metadata"):
            self.preparer.prepare(peer_delivery)
        peer_prepared = self.prepared("peer-pane", sequence=2)
        peer_delivery = replace(peer_delivery, bundle=replace(peer_bundle,
            spectrum=replace(peer_bundle.spectrum, sequence=2, timestamp_ns=2), identity=None))
        peer_injected = replace(peer_prepared, delivery=peer_delivery,
                                spectrum=PreparedSpectrumFrame(peer_delivery.bundle))
        self.assertFalse(self.board.apply_prepared(peer_injected))
        self.assertEqual(self.rows(), (0, 0, 1))

    def test_shared_gap_clears_both_visible_histories_before_first_new_rx_and_rejects_old_queue(self) -> None:
        for pane_id in ("left", "right", "peer-pane"):
            self.assertTrue(self.board.apply_prepared(self.prepared(pane_id, sequence=1)))
        for pane_id in ("left", "right"):
            self.assertTrue(self.board.apply_prepared(self.prepared(pane_id, sequence=2)))
        self.assertEqual(self.rows(), (2, 2, 1))
        old_right = self.prepared("right", sequence=3)
        new_left = self.prepared("left", sequence=4, synchronization_epoch=2)
        self.assertTrue(self.board.apply_prepared(new_left))
        self.assertEqual(self.rows(), (1, 0, 1))
        right = self.board.pane(2)
        self.assertIsNone(right.last_bundle)
        self.assertIsNone(right.spectrum_scene.displayed_frame)
        self.assertIsNone(self.board.accepted_activation_serial(2))
        self.assertFalse(self.board.apply_prepared(old_right))
        self.assertEqual(self.rows(), (1, 0, 1))
        new_right = self.prepared("right", sequence=4, synchronization_epoch=2)
        self.assertTrue(self.board.apply_prepared(new_right))
        self.assertEqual(self.rows(), (1, 1, 1))
        self.assertFalse(self.board.apply_prepared(new_right))
        self.assertTrue(self.board.apply_prepared(self.prepared(
            "left", sequence=5, synchronization_epoch=2)))
        self.assertEqual(self.rows(), (2, 1, 1))

    def test_invalid_or_out_of_order_context_never_clears_current_visuals(self) -> None:
        for pane_id in ("left", "right", "peer-pane"):
            self.assertTrue(self.board.apply_prepared(self.prepared(pane_id, sequence=2)))
        self.assertFalse(self.board.apply_prepared(self.prepared("left", sequence=1)))
        self.assertFalse(self.board.apply_prepared(self.prepared(
            "left", sequence=3, synchronization_epoch=2, session_id="wrong-session")))
        self.assertFalse(self.board.apply_prepared(self.prepared(
            "left", sequence=3, synchronization_epoch=2, acquisition_epoch=8)))
        self.assertFalse(self.board.apply_prepared(self.prepared(
            "left", sequence=3, synchronization_epoch=0)))
        self.assertFalse(self.board.apply_prepared(self.prepared(
            "left", sequence=3, synchronization_epoch=2, run=2)))
        paired_prepared = self.prepared("left", sequence=3)
        downgraded_delivery = replace(paired_prepared.delivery,
            bundle=replace(paired_prepared.bundle, paired_capture=None))
        with self.assertRaisesRegex(ValueError, "pair metadata"):
            self.preparer.prepare(downgraded_delivery)
        downgraded = replace(paired_prepared, delivery=downgraded_delivery,
                             spectrum=PreparedSpectrumFrame(downgraded_delivery.bundle))
        self.assertFalse(self.board.apply_prepared(downgraded))
        self.assertEqual(self.rows(), (1, 1, 1))
        self.assertIsNotNone(self.board.pane(2).last_bundle)

    def test_value_equal_plan_receipt_requires_new_activation_and_rearm_can_reset_sync_counter(self) -> None:
        for pane_id in ("left", "right", "peer-pane"):
            self.assertTrue(self.board.apply_prepared(self.prepared(pane_id, sequence=2)))
        old_left = self.prepared("left", sequence=3)
        old_right = self.prepared("right", sequence=3)
        self.board.refresh_resource_plan("paired")
        self.assertEqual(self.rows(), (0, 0, 1))
        self.assertFalse(self.board.apply_prepared(old_left))
        self.assertFalse(self.board.apply_prepared(old_right))
        self.assertEqual(self.rows(), (0, 0, 1))
        fresh = self.prepared("left", sequence=1, synchronization_epoch=0,
                              activation=2, run=2, acquisition_epoch=1,
                              session_id="session-2")
        self.assertTrue(self.board.apply_prepared(fresh))
        self.assertEqual(self.rows(), (1, 0, 1))
        self.assertFalse(self.board.apply_prepared(old_right))
        self.assertTrue(self.board.apply_prepared(self.prepared(
            "right", sequence=1, synchronization_epoch=0, activation=2,
            run=2, acquisition_epoch=1, session_id="session-2")))
        self.assertEqual(self.rows(), (1, 1, 1))

    def test_late_projector_and_persistence_results_cannot_restore_cleared_peer(self) -> None:
        pending: list[Future] = []

        def submit(_work):
            future = Future()
            pending.append(future)
            return future

        right = self.board.pane(2)
        scene = right.spectrum_scene
        projector = SpectrumProjector(submit)
        scene.set_projection_port(projector)
        try:
            for pane_id in ("left", "right"):
                self.assertTrue(self.board.apply_prepared(self.prepared(pane_id, sequence=1)))
            self.app.processEvents()
            old_spectrum_request = projector._active
            self.assertIsNotNone(old_spectrum_request)

            density = np.full((2, 2), 0.5, dtype=np.float32)
            frequencies = np.array([2_440e6, 2_450e6, 2_460e6], dtype=np.float64)
            levels = np.array([-100.0, -50.0, 0.0], dtype=np.float64)
            for array in (density, frequencies, levels):
                array.setflags(write=False)
            scene.set_persistence_frame(PersistenceDensityFrame(
                density, frequencies, levels, DensityValueMode.PROBABILITY, "dBFS/bin"), now_ns=1)
            old_density_request = scene._persistence.worker_request
            self.assertIsNotNone(old_density_request)

            self.assertTrue(self.board.apply_prepared(self.prepared(
                "left", sequence=2, synchronization_epoch=2)))
            self.assertEqual(right.waterfall_pane.history_rows, 0)
            self.assertIsNone(right.last_bundle)
            self.assertIsNone(scene._persistence.worker_request)
            self.assertIsNone(scene._persistence.latest_view)
            self.assertFalse(scene._projection_current(old_spectrum_request))
            stale_before = scene.projection_stale
            projector.ready.emit(SpectrumProjection(old_spectrum_request, (), None, None))
            projector.spectrum_ready.emit(SpectrumProjection(old_spectrum_request, (), None, None))
            self.assertEqual(scene.projection_stale, stale_before + 1)
            self.assertFalse(scene._persistence.accept_worker_image(old_density_request, None, "late"))
            self.assertIsNone(scene.displayed_frame)
            self.assertEqual(right.waterfall_pane.history_rows, 0)
        finally:
            projector.dispose()


if __name__ == "__main__":
    unittest.main()
