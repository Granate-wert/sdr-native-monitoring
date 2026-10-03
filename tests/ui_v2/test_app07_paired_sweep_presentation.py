"""Typed paired Sweep delivery and visible attempt boundaries, with no native RX."""

from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer import bundles_from_paired_sweep
from sdr_monitor.domain.live import AppliedLiveConfiguration
from sdr_monitor.domain.paired_sweep import PairedSweepRunIdentity, PairedSweepStepPair
from sdr_monitor.domain.paired_sweep_publication import PairedSweepPublication
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneLayoutSlot, compile_pane_layout
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode, ReceiverChainSelection
from sdr_monitor.domain.sweep_acquisition import SweepSegmentAcquisition
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineGapReason, SweepLineState
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.services.pane_resource_session import PaneDelivery
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer, PreparedPaneDelivery
from sdr_monitor.ui.v2_pane_user_plan import PanePairedSelectionReceipt, PaneSlotDraft, compile_user_pane_plan

from tests.test_app07_paired_sweep_contract import pair_fixture
from tests.test_app07_pane_resource_session import live_frame
from tests.test_app07_shared_capture_schedule import group, pane, profile
from tests.ui_v2.test_app07_paired_setup import _choice


class PairedSweepPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        choice, snapshot = _choice(2)
        drafts = (PaneSlotDraft(1, choice.device_id, 100e6, 104e6,
                                sample_rate_hz=61_440_000., measurement_mode=CaptureMeasurementMode.SWEEP),
                  PaneSlotDraft(2, choice.device_id, 137e6, 140e6,
                                sample_rate_hz=61_440_000., measurement_mode=CaptureMeasurementMode.SWEEP,
                                receiver_selection=ReceiverChainSelection.RX2))
        receipt = PanePairedSelectionReceipt(choice, 17, snapshot)
        plan = compile_user_pane_plan(drafts, {choice.device_id: choice}, {choice.device_id: 17},
                                      paired_selections={choice.device_id: receipt})
        paired_profile = plan.layout.schedule.resources[0].jobs[0].profile
        layout = compile_pane_layout(plan.layout.slots + (PaneLayoutSlot(3,
            pane("peer-pane", "peer-rx", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL)),),
            plan.groups + (group("peer", "peer-rx"),),
            {"pane-1": paired_profile, "pane-2": paired_profile,
             "capture": profile(36e6, CaptureMeasurementMode.RTBW)})
        self.preparer = PaneDeliveryPreparer(layout, plan.groups + (group("peer", "peer-rx"),),
            PresentationAllocationBudget(),
            admitted_producer_source_id=lambda resource, endpoint: (
                endpoint if resource != "peer" else "peer:source"))
        self.intent = paired_profile.paired_request
        self.geometry = plan.paired_sweep_geometry[0][1]
        self.run = self._run(8)
        settings = QSettings(str(Path(self.temp.name) / "paired-sweep.ini"), QSettings.Format.IniFormat)
        self.board = IndependentPaneBoardV2(self.preparer, settings=settings)

    def tearDown(self) -> None:
        self.board.release_presentation_after_shutdown()
        self.board.close()
        self.preparer.clear()
        self.temp.cleanup()

    def _run(self, epoch: int) -> PairedSweepRunIdentity:
        profile = self.intent.pair.configuration
        applied = replace(self.intent.selected_snapshot,
                          applied=AppliedLiveConfiguration(profile, profile))
        return PairedSweepRunIdentity(self.intent, epoch, applied)

    def _publication(self, *, steps: int, epoch: int = 8, line_sequence: int = 7,
                     gap_at_second: bool = False, terminal: bool = False) -> PairedSweepPublication:
        run = self._run(epoch)
        observations = []
        for index in range(steps):
            pair = pair_fixture(self.intent, segment=index, epoch=5)
            gaps = 1 if index == 1 and gap_at_second else 0
            identity = replace(pair.primary.identity, acquisition_epoch=epoch,
                               line_sequence=line_sequence)
            observations.append(PairedSweepStepPair(
                replace(pair.primary, identity=identity, shared_input_gaps_before=gaps),
                replace(pair.secondary, identity=identity, shared_input_gaps_before=gaps)))
        frequencies = (self.intent.sweep.start_hz + np.arange(self.geometry.reduced.output_bins)
                       * self.geometry.output_spacing_hz)
        first_step = frequencies <= 136e6
        owners = np.where(first_step, 0, 1 if steps == 2 else -1).astype(np.int32)
        values = np.where(first_step, -50., -55. if steps == 2 else np.nan).astype(np.float32)
        quality = np.where(first_step | (steps == 2), 0, 4096).astype(np.uint32)
        for array in (frequencies, owners, values, quality):
            array.setflags(write=False)
        frames = []
        for source, receiver in ((self.intent.pair.primary_source_id, "primary"),
                                 (self.intent.pair.secondary_source_id, "secondary")):
            acquisitions = tuple(SweepSegmentAcquisition(
                index, observation.identity.config_generation, observation.frame_sequence,
                observation.first_sample_index, observation.timestamp_ns, observation.sample_rate_hz,
                observation.fft_size, observation.quality_flags)
                for index, pair in enumerate(observations)
                for observation in (getattr(pair, receiver),))
            if terminal:
                frame = SweepLineFrame(line_sequence, 5, 0, source, SweepLineState.COMPLETE,
                    frequencies, values, quality, owners, (), ((0, 19), (1, 19)), (), "dBFS/bin",
                    segment_acquisition=acquisitions)
            else:
                frame = SweepProgressFrame(source, line_sequence, 5, steps, "dBFS/bin", frequencies,
                    values, quality, owners, tuple((index, 19) for index in range(steps)),
                    tuple(range(steps, 2)), segment_acquisition=acquisitions)
            frames.append(frame)
        return PairedSweepPublication(run, tuple(observations), frames[0], frames[1])

    def _prepared(self, publication: PairedSweepPublication, pane_id: str, *, activation: int = 1,
                  run_serial: int = 1) -> PreparedPaneDelivery:
        binding = self.preparer.bindings[pane_id]
        bundles = dict(bundles_from_paired_sweep(publication))
        producer = (publication.run.request.pair.primary_source_id if pane_id == "pane-1"
                    else publication.run.request.pair.secondary_source_id)
        delivery = PaneDelivery(binding.physical_stream_resource_id, binding.capture_id,
                                binding.receiver_endpoint_id, activation, binding.crop,
                                bundles[producer], float(activation), run_serial)
        return self.preparer.prepare(delivery)

    def rows(self) -> tuple[int, int]:
        return tuple(self.board.pane(number).waterfall_pane.history_rows for number in (1, 2))

    def _peer(self) -> PreparedPaneDelivery:
        binding = self.preparer.bindings["peer-pane"]
        delivery = PaneDelivery(binding.physical_stream_resource_id, binding.capture_id,
                                binding.receiver_endpoint_id, 1, binding.crop,
                                live_frame("peer:source", "peer-session", 3, center_hz=104e6), 1., 1)
        return self.preparer.prepare(delivery)

    def test_normal_tune_step_and_terminal_keep_existing_histories(self) -> None:
        first = self._publication(steps=1)
        self.assertTrue(self.board.apply_prepared(self._prepared(first, "pane-1")))
        self.assertTrue(self.board.apply_prepared(self._prepared(first, "pane-2")))
        self.assertEqual(self.rows(), (1, 1))
        second = self._publication(steps=2, terminal=True)
        left, right = self.board.pane(1), self.board.pane(2)
        with (patch.object(left, "clear_paired_synchronization_history",
                           wraps=left.clear_paired_synchronization_history) as left_clear,
              patch.object(right, "clear_paired_synchronization_history",
                           wraps=right.clear_paired_synchronization_history) as right_clear):
            self.assertTrue(self.board.apply_prepared(self._prepared(second, "pane-1")))
            self.assertTrue(self.board.apply_prepared(self._prepared(second, "pane-2")))
            self.assertEqual(left_clear.call_count, 0)
            self.assertEqual(right_clear.call_count, 0)
        self.assertEqual(self.rows(), (1, 1))
        self.assertIs(left.last_bundle.spectrum, second.primary)
        self.assertIs(right.last_bundle.spectrum, second.secondary)

    def test_new_attempt_and_observed_shared_gap_clear_both_not_peer(self) -> None:
        self.assertTrue(self.board.apply_prepared(self._peer()))
        first = self._publication(steps=1)
        self.assertTrue(self.board.apply_prepared(self._prepared(first, "pane-1")))
        self.assertTrue(self.board.apply_prepared(self._prepared(first, "pane-2")))
        old_right = self._prepared(first, "pane-2")
        new = self._publication(steps=1, epoch=9)
        self.assertTrue(self.board.apply_prepared(self._prepared(new, "pane-1", activation=2, run_serial=2)))
        self.assertEqual(self.rows(), (1, 0))
        self.assertEqual(self.board.pane(3).waterfall_pane.history_rows, 1)
        self.assertFalse(self.board.apply_prepared(old_right))
        self.assertTrue(self.board.apply_prepared(self._prepared(new, "pane-2", activation=2, run_serial=2)))
        queued_before_gap = self._prepared(new, "pane-2", activation=2, run_serial=2)
        gap = self._publication(steps=2, epoch=9, gap_at_second=True, terminal=True)
        self.assertTrue(self.board.apply_prepared(self._prepared(gap, "pane-1", activation=2, run_serial=2)))
        self.assertEqual(self.rows(), (1, 0))
        self.assertFalse(self.board.apply_prepared(queued_before_gap))
        self.assertTrue(self.board.apply_prepared(self._prepared(gap, "pane-2", activation=2, run_serial=2)))
        self.assertEqual(self.rows(), (1, 1))
        self.assertEqual(self.board.pane(3).waterfall_pane.history_rows, 1)
        with self.assertRaisesRegex(ValueError, "line or observed gap prefix regressed"):
            self._prepared(new, "pane-1", activation=2, run_serial=2)

        # DualRxDsp is reconfigured at a normal next Sweep line, so its
        # within-line gap prefix starts at zero again. This is not a new gap.
        next_line = self._publication(steps=2, epoch=9, line_sequence=8, terminal=True)
        left, right = self.board.pane(1), self.board.pane(2)
        with (patch.object(left, "clear_paired_synchronization_history",
                           wraps=left.clear_paired_synchronization_history) as left_clear,
              patch.object(right, "clear_paired_synchronization_history",
                           wraps=right.clear_paired_synchronization_history) as right_clear):
            self.assertTrue(self.board.apply_prepared(self._prepared(
                next_line, "pane-1", activation=2, run_serial=2)))
            self.assertTrue(self.board.apply_prepared(self._prepared(
                next_line, "pane-2", activation=2, run_serial=2)))
            self.assertEqual((left_clear.call_count, right_clear.call_count), (0, 0))
        with self.assertRaisesRegex(ValueError, "line or observed gap prefix regressed"):
            self._prepared(gap, "pane-1", activation=2, run_serial=2)
        self.assertEqual(self.board.pane(3).waterfall_pane.history_rows, 1)

    def test_missing_pair_metadata_and_foreign_producer_refuse(self) -> None:
        first = self._prepared(self._publication(steps=1), "pane-1")
        with self.assertRaises(ValueError):
            replace(first, producer_source_id="foreign-producer")
        unpaired = replace(first.bundle, paired_sweep=None, session_id=None, receiver_id=None,
                           acquisition_epoch=first.bundle.spectrum.epoch, identity=None)
        with self.assertRaisesRegex(ValueError, "pair metadata"):
            self.preparer.prepare(replace(first.delivery, bundle=unpaired))
        with self.assertRaises(ValueError):
            replace(first, binding=replace(first.binding, receiver_selection=ReceiverChainSelection.RX2))

    def test_zero_prefix_terminal_is_not_a_fresh_pane_delivery(self) -> None:
        frequencies = (self.intent.sweep.start_hz + np.arange(self.geometry.reduced.output_bins)
                       * self.geometry.output_spacing_hz)
        values = np.full(frequencies.size, np.nan, dtype=np.float32)
        quality = np.full(frequencies.size, 4096, dtype=np.uint32)
        owners = np.full(frequencies.size, -1, dtype=np.int32)
        frames = tuple(SweepLineFrame(7, 5, 0, source, SweepLineState.GAP,
            frequencies, values, quality, owners, (0, 1), (),
            (SweepLineGapReason.MISSING_SEGMENT,), "dBFS/bin")
            for source in (self.intent.pair.primary_source_id, self.intent.pair.secondary_source_id))
        publication = PairedSweepPublication(self._run(8), (), frames[0], frames[1])
        bundle = dict(bundles_from_paired_sweep(publication))[self.intent.pair.primary_source_id]
        binding = self.preparer.bindings["pane-1"]
        delivery = PaneDelivery(binding.physical_stream_resource_id, binding.capture_id,
                                binding.receiver_endpoint_id, 1, binding.crop, bundle, 1., 1)
        with self.assertRaisesRegex(ValueError, "acquired prefix"):
            self.preparer.prepare(delivery)
        self.assertEqual(self.rows(), (0, 0))


if __name__ == "__main__":
    unittest.main()
