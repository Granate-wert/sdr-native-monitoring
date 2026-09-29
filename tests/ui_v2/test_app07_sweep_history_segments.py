"""Qualified Sweep visits share bounded history, never pass identity or RF time."""

from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer import bundle_from_sweep
from sdr_monitor.domain.pane_scheduler import PaneLayoutSlot, compile_pane_layout
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.domain.sweep_lines import SweepLineState
from sdr_monitor.services.pane_resource_session import PaneDelivery
from sdr_monitor.ui.v2.i18n import UiLocale, text
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.waterfall import WaterfallDirection, WaterfallPane
from sdr_monitor.ui.v2.waterfall.bounded_ring import BoundedWaterfallRenderer
from sdr_monitor.ui.v2.waterfall.sweep_rows import SweepRowStamp, SweepRowState
from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
from sdr_monitor.ui.v2.workspaces.analyzer_pane import AnalyzerPaneViewV2
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer

from tests.test_app07_shared_capture_schedule import group, pane, profile
from tests.test_app07_mixed_source_trace import trace_bundle
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal


class SweepSegmentRingTests(unittest.TestCase):
    def test_epoch_scoped_identity_partial_terminal_old_epoch_and_eviction(self):
        renderer = BoundedWaterfallRenderer()

        def offer(epoch, sequence=1, revision=1, state=SweepRowState.PARTIAL):
            return renderer.upsert_sweep(np.array([epoch, revision]), rows=5,
                stamp=SweepRowStamp(sequence, revision, state, epoch))

        self.assertEqual(offer(7), "append")
        self.assertEqual(offer(8), "reject")  # No implicit epoch join.
        self.assertTrue(renderer.append_sweep_separator(stamp=SweepRowStamp(1, 1, SweepRowState.PARTIAL, 8)))
        self.assertEqual(offer(8), "append")
        self.assertEqual(offer(8, revision=2), "replace")
        self.assertEqual(offer(8, revision=0, state=SweepRowState.COMPLETE), "replace")
        self.assertEqual(offer(7, revision=0, state=SweepRowState.COMPLETE), "reject")
        self.assertEqual(renderer.sweep_stamps(), (
            SweepRowStamp(1, 1, SweepRowState.PARTIAL, 7), None,
            SweepRowStamp(1, 0, SweepRowState.COMPLETE, 8),
        ))
        rows = np.concatenate(renderer.tiles())
        np.testing.assert_array_equal(rows[0], [7, 1])
        self.assertTrue(np.isnan(rows[1]).all())
        np.testing.assert_array_equal(rows[2], [8, 0])
        np.testing.assert_array_equal(renderer.timestamps_ns(), [0, 0, 0])
        self.assertFalse(renderer.append_sweep_separator(stamp=SweepRowStamp(1, 1, SweepRowState.PARTIAL, 7)))
        for epoch in range(9, 50):
            self.assertTrue(renderer.append_sweep_separator(stamp=SweepRowStamp(1, 1, SweepRowState.PARTIAL, epoch)))
            self.assertEqual(offer(epoch), "append")
            self.assertEqual(offer(epoch, revision=0, state=SweepRowState.GAP), "replace")
            self.assertEqual(renderer.buffer.count, 5)
            self.assertLessEqual(len(renderer.buffer._sweep_indices), 5)
            self.assertLessEqual(len(renderer.tiles()), 2)
        self.assertEqual(renderer.sweep_stamps()[-1], SweepRowStamp(1, 0, SweepRowState.GAP, 49))
        self.assertEqual(offer(7), "reject")

    def test_resize_keeps_segment_cursor_even_with_only_separator_retained(self):
        renderer = BoundedWaterfallRenderer()
        old = SweepRowStamp(0, 1, SweepRowState.PARTIAL, 7)
        new = SweepRowStamp(0, 1, SweepRowState.PARTIAL, 8)
        renderer.upsert_sweep(np.array([1, 2]), rows=5, stamp=old)
        self.assertTrue(renderer.append_sweep_separator(stamp=new))
        renderer.resize_rows(1)
        self.assertEqual(renderer.sweep_stamps(), (None,))
        self.assertEqual(renderer.upsert_sweep(np.array([3, 4]), rows=1, stamp=old), "reject")
        self.assertEqual(renderer.upsert_sweep(np.array([3, 4]), rows=1, stamp=new), "append")
        renderer.resize_rows(3)
        self.assertEqual(renderer.upsert_sweep(np.array([5, 6]), rows=3,
            stamp=replace(new, revision=0, state=SweepRowState.COMPLETE)), "replace")
        self.assertEqual(renderer.upsert_sweep(np.array([5, 6]), rows=3, stamp=old), "reject")
        renderer.clear()
        self.assertEqual(renderer.upsert_sweep(np.array([1, 2]), rows=3, stamp=old), "append")

    def test_epoch_validation_unknown_default_and_supersession(self):
        old = SweepRowStamp(1, 1, SweepRowState.PARTIAL)
        self.assertTrue(SweepRowStamp(1, 0, SweepRowState.COMPLETE).supersedes(old))
        self.assertFalse(SweepRowStamp(1, 0, SweepRowState.COMPLETE, 8).supersedes(old))
        for epoch in (-1, True, 1.5, "8"):
            with self.subTest(epoch=epoch), self.assertRaises(ValueError):
                SweepRowStamp(1, 1, SweepRowState.PARTIAL, epoch)
        renderer = BoundedWaterfallRenderer()
        renderer.upsert_sweep(np.array([1, 2]), rows=3, stamp=old)
        self.assertFalse(renderer.append_sweep_separator(stamp=replace(old, acquisition_epoch=8)))
        self.assertEqual(waterfall_line_from_sweep(progress()).stamp.acquisition_epoch, 7)


class SweepVisitPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        settings = QSettings(str(Path(self.temp.name) / "panes.ini"), QSettings.Format.IniFormat)
        groups = (group("one-rx", "rx1"),)
        mode = ReceiverBindingMode.TIME_SLICED
        layout = compile_pane_layout((
            PaneLayoutSlot(1, pane("low", "rx1", 100e6, 102e6, mode)),
            PaneLayoutSlot(2, pane("high", "rx1", 200e6, 202e6, mode)),
            PaneLayoutSlot(3), PaneLayoutSlot(4),
        ), groups, {"capture": profile(36e6)})
        self.assertEqual(len(layout.schedule.resources[0].jobs), 2)
        self.preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget())
        self.board = IndependentPaneBoardV2(self.preparer, settings=settings)

    def tearDown(self):
        self.board.release_presentation_after_shutdown()
        self.board.close()
        self.preparer.clear()
        self.temp.cleanup()

    def offer(self, pane_id, epoch, serial, *, sequence=1, revision=1,
              complete=False, gap=False, shift=0., source=None, unit=None):
        binding = self.preparer.bindings[pane_id]
        frame = terminal(sequence, gap=gap) if complete else progress(sequence, revision)
        offset = (100e6 if pane_id == "high" else 0) + shift
        frequencies = frame.frequencies_hz + offset
        frequencies.setflags(write=False)
        frame = replace(frame, source_id=source or binding.source_id, epoch=epoch,
                        unit=unit or binding.unit, frequencies_hz=frequencies)
        bundle = bundle_from_sweep(frame)
        delivery = PaneDelivery(binding.physical_stream_resource_id, binding.capture_id,
                                binding.receiver_endpoint_id, serial, binding.crop, bundle, float(serial))
        return self.board.apply_prepared(self.preparer.prepare(delivery))

    def test_two_sweep_visits_preserve_rows_without_reusing_sequence_identity(self):
        self.assertTrue(self.offer("low", 7, 1, revision=2))
        self.assertTrue(self.offer("high", 7, 2, complete=True))
        low, high = self.board.pane(1), self.board.pane(2)
        self.assertTrue(self.offer("low", 8, 3))
        self.assertEqual((low.waterfall_pane.history_rows, high.waterfall_pane.history_rows), (3, 1))
        self.assertTrue(self.offer("low", 8, 3, revision=2))
        self.assertTrue(self.offer("low", 8, 3, complete=True, gap=True))
        stamps = low.waterfall_pane._renderer.sweep_stamps()
        self.assertEqual(stamps, (SweepRowStamp(1, 2, SweepRowState.PARTIAL, 7), None,
                                 SweepRowStamp(1, 0, SweepRowState.GAP, 8)))
        self.assertEqual(low.waterfall_pane.metrics.rows_admitted, 2)
        self.assertEqual(low.waterfall_pane.metrics.sweep_rows_updated, 2)
        self.assertEqual(low.waterfall_pane.metrics.presentation_gap_rows, 1)
        self.assertEqual(high.waterfall_pane.metrics.presentation_gap_rows, 0)
        self.assertEqual(low.last_bundle.acquisition_epoch, 8)
        self.assertEqual(high.last_bundle.acquisition_epoch, 7)
        self.assertFalse(self.offer("low", 7, 1, complete=True))
        self.assertFalse(self.offer("low", 8, 3, complete=True, gap=True))
        for locale in UiLocale:
            low.waterfall_pane.set_locale(locale)
            for direction in WaterfallDirection:
                low.waterfall_pane.set_direction(direction)
                capacity = low.waterfall_pane.config.history_seconds * low.waterfall_pane.config.rows_per_second
                ticks = [0., 1., 2.] if direction is WaterfallDirection.NEWEST_AT_TOP else [capacity-1., capacity-2., capacity-3.]
                self.assertEqual(low.waterfall_pane._time_axis.tickStrings(ticks, 1., 1.),
                    ["E8 #1 G", text("waterfall.sweep.visit_gap", locale), "E7 #1 P"])
        self.assertEqual(stamps, low.waterfall_pane._renderer.sweep_stamps())
        rows = np.concatenate(low.waterfall_pane._renderer.tiles())
        self.assertTrue(np.isnan(rows[1]).all())
        self.assertTrue(np.isfinite(rows[0, :2]).all())
        self.assertEqual(self.board.empty_slots, (3, 4))

    def test_freeze_carries_one_boundary_and_hidden_pane_still_admits(self):
        self.offer("low", 7, 1, complete=True)
        low = self.board.pane(1).waterfall_pane
        low.set_frozen(True)
        self.offer("low", 8, 3)
        self.offer("low", 9, 5)
        self.assertEqual(low.history_rows, 1)
        self.assertEqual(low.metrics.presentation_gap_rows, 0)
        low.set_frozen(False)
        low.set_render_visible(False)
        uploads = low.metrics.image_uploads
        self.offer("low", 9, 5, complete=True)
        self.assertEqual(low.history_rows, 3)
        self.assertEqual(low.metrics.presentation_gap_rows, 1)
        self.assertEqual(low._renderer.sweep_stamps()[-1].acquisition_epoch, 9)
        self.offer("low", 9, 5, sequence=2)
        self.assertEqual(low.history_rows, 4)
        self.assertEqual(low.metrics.image_uploads, uploads)
        low.set_render_visible(True)
        self.assertEqual(low.history_rows, 4)

    def test_unqualified_epoch_serial_grid_foreign_source_and_unit_fail_closed(self):
        self.offer("low", 7, 1)
        low = self.board.pane(1).waterfall_pane
        for change in ({"source": "other"}, {"unit": "dBm"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.offer("low", 8, 3, **change)
            self.assertEqual(low.history_rows, 1)
        # New epoch but no new activation has no schedule handoff evidence.
        self.offer("low", 8, 1)
        self.assertEqual(low.history_rows, 1)
        # New activation but same epoch likewise must not join history.
        self.offer("low", 8, 3)
        self.assertEqual(low.history_rows, 1)
        self.offer("low", 9, 5, shift=-.5e6)
        self.assertEqual(low.history_rows, 1)
        self.assertEqual(low.metrics.presentation_gap_rows, 0)
        low.clear_history()
        self.offer("low", 10, 7, shift=-.5e6)
        self.assertEqual(low.history_rows, 1)  # Clear is respected, no orphan separator.

    def test_unqualified_single_source_default_and_invalid_boundary(self):
        settings = QSettings(str(Path(self.temp.name) / "single.ini"), QSettings.Format.IniFormat)
        standalone = WaterfallPane(settings=settings)
        try:
            standalone.set_sweep_line(waterfall_line_from_sweep(progress()))
            standalone.set_sweep_line(waterfall_line_from_sweep(replace(progress(), epoch=8)))
            self.assertEqual(standalone.history_rows, 1)
            self.assertEqual(standalone.metrics.presentation_gap_rows, 0)
            for boundary in (1, None, "yes"):
                with self.subTest(boundary=boundary), self.assertRaises(TypeError):
                    standalone.set_sweep_line(waterfall_line_from_sweep(progress()), segment_boundary=boundary)
        finally:
            standalone.release_presentation_after_shutdown()
            standalone.close()

    def test_instrument_display_context_changes_do_not_join_raw_and_corrected_history(self):
        settings = QSettings(str(Path(self.temp.name) / "context.ini"), QSettings.Format.IniFormat)
        view = AnalyzerPaneViewV2(AnalyzerMode.SWEEP, settings=settings)
        try:
            def offer(epoch, context=None):
                bundle = trace_bundle("trace", epoch)
                if context is not None:
                    # A cancelled instrument row carries only the prior
                    # display-plane key, no fake correction/readback values.
                    frame = replace(bundle.spectrum, state=SweepLineState.GAP,
                        values_db=np.full(4, np.nan, np.float32),
                        instrument=replace(bundle.spectrum.instrument, observed_zero_db=None,
                                           gap_value_context=context))
                    bundle = bundle_from_sweep(frame)
                view._accept_measurement(AnalyzerMode.SWEEP, bundle, None,
                                         scheduled_visit_boundary=epoch != 7)
                if view.waterfall_pane.set_sweep_line(waterfall_line_from_sweep(bundle.spectrum),
                                                      segment_boundary=view._pending_waterfall_gap):
                    view._pending_waterfall_gap = False

            offer(7)
            offer(8, "external:" + "a" * 64)
            self.assertEqual(view.waterfall_pane.history_rows, 1)
            offer(9, "external:" + "a" * 64)
            self.assertEqual(view.waterfall_pane.history_rows, 3)
            self.assertEqual(view.waterfall_pane.metrics.presentation_gap_rows, 1)
            offer(10, "external:" + "b" * 64)
            self.assertEqual(view.waterfall_pane.history_rows, 1)
            offer(11)
            self.assertEqual(view.waterfall_pane.history_rows, 1)
        finally:
            view.release_presentation_after_shutdown()
            view.close()


if __name__ == "__main__":
    unittest.main()
