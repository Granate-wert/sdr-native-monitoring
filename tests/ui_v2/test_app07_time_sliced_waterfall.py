"""Only confirmed scheduled RX revisits may keep one pane's RTBW history."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.pane_scheduler import PaneLayoutSlot, compile_pane_layout
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.pane_resource_session import PaneDelivery
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer

from tests.test_app07_mixed_source_trace import iq_bundle, iq_profile
from tests.test_app07_shared_capture_schedule import group, pane


class TimeSlicedWaterfallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_two_shared_jobs_on_one_rx_keep_both_subscribers_history_on_scheduled_return(self) -> None:
        groups = (group("one-rx", "rx1"),)
        slots = tuple(PaneLayoutSlot(number, replace(
            pane(pane_id, "rx1", start, stop, ReceiverBindingMode.SHARED_CAPTURE),
            profile_id=profile_id)) for number, pane_id, start, stop, profile_id in (
                (1, "low-a", 100e6, 104e6, "low"), (2, "low-b", 105e6, 108e6, "low"),
                (3, "high-a", 200e6, 204e6, "high"), (4, "high-b", 205e6, 208e6, "high")))
        layout = compile_pane_layout(slots, groups, {
            "low": iq_profile(20e6), "high": replace(iq_profile(20e6), manual_gain_db=30.0)})
        assert layout.schedule is not None
        self.assertEqual(len(layout.schedule.resources), 1)
        self.assertEqual(len(layout.schedule.resources[0].jobs), 2)
        self.assertTrue(all(job.mode is ReceiverBindingMode.SHARED_CAPTURE
                            for job in layout.schedule.resources[0].jobs))
        preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget())
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "paired.ini"), QSettings.Format.IniFormat)
            board = IndependentPaneBoardV2(preparer, settings=settings)
            try:
                def offer(pane_id: str, center: float, epoch: int, serial: int, run=0) -> None:
                    binding = preparer.bindings[pane_id]
                    bundle = iq_bundle(binding.source_id, center, 20e6, epoch)
                    bundle = replace(bundle, spectrum=replace(bundle.spectrum,
                        config_generation=epoch, accumulation_id=f"session-{epoch}"),
                        session_id=f"session-{epoch}", identity=None)
                    delivery = PaneDelivery(binding.physical_stream_resource_id, binding.capture_id,
                        binding.receiver_endpoint_id, serial, binding.crop, bundle, float(serial), run)
                    self.assertTrue(board.apply_prepared(preparer.prepare(delivery)))

                for pane_id in ("low-a", "low-b"):
                    offer(pane_id, 104e6, 7, 1)
                for pane_id in ("high-a", "high-b"):
                    offer(pane_id, 204e6, 7, 2)
                self.assertEqual(tuple(board.pane(n).waterfall_pane.history_rows
                                       for n in range(1, 5)), (1, 1, 1, 1))
                for pane_id in ("low-a", "low-b"):
                    offer(pane_id, 104e6, 8, 3)
                self.assertEqual(tuple(board.pane(n).waterfall_pane.history_rows
                                       for n in range(1, 5)), (3, 3, 1, 1))
                for number in (1, 2):
                    waterfall = board.pane(number).waterfall_pane
                    self.assertEqual(waterfall.metrics.presentation_gap_rows, 1)
                    rows = np.concatenate(waterfall._renderer.tiles(), axis=0)
                    self.assertTrue(np.isnan(rows[1]).all())
                for number in (3, 4):
                    self.assertEqual(board.pane(number).waterfall_pane.metrics.presentation_gap_rows, 0)
                # A true new explicit Start remains a history reset, not a
                # continuation disguised as another scheduler visit.
                for pane_id in ("low-a", "low-b"):
                    offer(pane_id, 104e6, 9, 5, run=1)
                self.assertEqual(tuple(board.pane(n).waterfall_pane.history_rows
                                       for n in range(1, 5)), (1, 1, 1, 1))
            finally:
                board.release_presentation_after_shutdown()
                board.close()
                preparer.clear()

    def test_two_disjoint_visits_keep_separate_pane_history_with_gap(self) -> None:
        groups = (group("one-rx", "rx1"),)
        slots = (
            PaneLayoutSlot(1, pane("low", "rx1", 100e6, 108e6, ReceiverBindingMode.TIME_SLICED)),
            PaneLayoutSlot(2, pane("high", "rx1", 200e6, 208e6, ReceiverBindingMode.TIME_SLICED)),
            PaneLayoutSlot(3), PaneLayoutSlot(4),
        )
        layout = compile_pane_layout(slots, groups, {"capture": iq_profile(20e6)})
        assert layout.schedule is not None
        self.assertEqual(layout.empty_slots, (3, 4))
        self.assertEqual(len(layout.schedule.resources[0].jobs), 2)
        preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget())
        with TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / "panes.ini"), QSettings.Format.IniFormat)
            board = IndependentPaneBoardV2(preparer, settings=settings)
            try:
                def offer(pane_id: str, center_hz: float, epoch: int, serial: int,
                          *, generation: int = 5, sequence: int = 1, run_serial: int = 0) -> bool:
                    binding = preparer.bindings[pane_id]
                    bundle = iq_bundle(binding.source_id, center_hz, 20e6, epoch)
                    frame = replace(bundle.spectrum, config_generation=generation,
                                    sequence=sequence, accumulation_id=f"session-{epoch}")
                    bundle = replace(bundle, spectrum=frame,
                                     session_id=f"session-{epoch}", identity=None)
                    delivery = PaneDelivery(
                        binding.physical_stream_resource_id, binding.capture_id,
                        binding.receiver_endpoint_id, serial, binding.crop, bundle, float(serial),
                        run_serial,
                    )
                    return board.apply_prepared(preparer.prepare(delivery))

                self.assertTrue(offer("low", 104e6, 7, 1))
                self.assertTrue(offer("high", 204e6, 7, 2))
                low, high = board.pane(1), board.pane(2)
                assert low is not None and high is not None
                self.assertEqual((low.waterfall_pane.history_rows, high.waterfall_pane.history_rows), (1, 1))

                # A new confirmed activation/epoch for low follows a visit to
                # high. The FFT grid is identical but the producer generation
                # changes. Only low gets one display-only absence marker.
                self.assertTrue(offer("low", 104e6, 8, 3, generation=6))
                self.assertEqual((low.waterfall_pane.history_rows, high.waterfall_pane.history_rows), (3, 1))
                self.assertEqual(low.waterfall_pane.metrics.presentation_gap_rows, 1)
                self.assertEqual(high.waterfall_pane.metrics.presentation_gap_rows, 0)
                rows = np.concatenate(low.waterfall_pane._renderer.tiles(), axis=0)
                self.assertTrue(np.isnan(rows[1]).all())
                self.assertTrue(np.isfinite(rows[0]).all() and np.isfinite(rows[2]).all())
                self.assertEqual(low.last_bundle.acquisition_epoch, 8)
                self.assertEqual(high.last_bundle.acquisition_epoch, 7)

                # More FFT publications in the same activation are not new
                # visits and cannot introduce another gap marker.
                self.assertTrue(offer("low", 104e6, 8, 3, generation=6, sequence=2))
                self.assertEqual(low.waterfall_pane.history_rows, 4)
                self.assertEqual(low.waterfall_pane.metrics.presentation_gap_rows, 1)

                # A new host serial without a new producer epoch is not a
                # qualified handoff. Fail closed instead of joining histories.
                self.assertTrue(offer("low", 104e6, 8, 4, generation=6))
                self.assertEqual(low.waterfall_pane.history_rows, 1)
                self.assertEqual(low.waterfall_pane.metrics.presentation_gap_rows, 1)

                # Even with a new epoch, shifted physical bins cannot inherit
                # old waterfall rows from the same selected source.
                self.assertTrue(offer("low", 105e6, 9, 5, generation=7))
                self.assertEqual(low.waterfall_pane.history_rows, 1)
                self.assertEqual(high.waterfall_pane.history_rows, 1)

                # Local Freeze suppresses admission but must not lose the
                # pending boundary when a later row is finally displayed.
                low.waterfall_pane.set_frozen(True)
                self.assertTrue(offer("low", 105e6, 10, 6, generation=8))
                self.assertEqual(low.waterfall_pane.history_rows, 1)
                low.waterfall_pane.set_frozen(False)
                self.assertTrue(offer("low", 105e6, 10, 6, generation=8, sequence=2))
                self.assertEqual(low.waterfall_pane.history_rows, 3)
                self.assertEqual(low.waterfall_pane.metrics.presentation_gap_rows, 2)
                # Explicit Stop/next Start is a new run, not another planned
                # revisit in the old run. Its first FFT cannot inherit history.
                self.assertTrue(offer("low", 105e6, 11, 7, generation=9, run_serial=1))
                self.assertEqual((low.waterfall_pane.history_rows, high.waterfall_pane.history_rows), (1, 1))
                self.assertEqual(low.waterfall_pane.metrics.presentation_gap_rows, 2)
                self.assertTrue(offer("low", 105e6, 12, 9, generation=10, run_serial=1))
                self.assertEqual(low.waterfall_pane.history_rows, 3)
                self.assertEqual(low.waterfall_pane.metrics.presentation_gap_rows, 3)
            finally:
                board.release_presentation_after_shutdown()
                board.close()
                preparer.clear()


if __name__ == "__main__":
    unittest.main()
