"""Progressive Sweep into the shared bounded V2 Waterfall; no physical RX."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState, SweepQualitySchema
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.ui.v2.i18n import UiLocale, text
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.waterfall import WaterfallPane, WaterfallDirection
from sdr_monitor.ui.v2.waterfall.bounded_ring import BoundedWaterfallRenderer
from sdr_monitor.ui.v2.waterfall.contracts import SweepWaterfallLine
from sdr_monitor.ui.v2.waterfall.sweep_rows import SweepRowStamp, SweepRowState
from tests import test_app02_analyzer_workspace_product as product_fixture
from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay


def _readonly(values, dtype):
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


def progress(sequence=1, revision=1):
    return SweepProgressFrame(
        source_id="fake-sweep", epoch=7, sequence=sequence, revision=revision, unit="dBFS/bin",
        frequencies_hz=_readonly([100e6, 101e6, 102e6, 103e6], np.float64),
        values_db=_readonly([-70. + i * 5 if i < revision else np.nan for i in range(4)], np.float32),
        quality_flags=_readonly([0 if i < revision else 4096 for i in range(4)], np.uint32),
        source_segment_indices=_readonly([i if i < revision else -1 for i in range(4)], np.int32),
        acquired_segment_generations=tuple((i, 11 + i) for i in range(revision)),
        pending_segment_indices=tuple(range(revision, 4)),
    )


def terminal(sequence=1, *, gap=False):
    return SweepLineFrame(
        source_id="fake-sweep", epoch=7, sequence=sequence, completed_at_ns=987654321,
        state=SweepLineState.GAP if gap else SweepLineState.COMPLETE,
        unit="dBFS/bin", frequencies_hz=np.array([100e6, 101e6, 102e6, 103e6]),
        values_db=np.array([-70., -65., np.nan if gap else -60., np.nan if gap else -55.]),
        quality_flags=np.array([0, 0, 4096 if gap else 0, 4096 if gap else 0], np.uint16),
        source_segment_indices=np.array([0, 1, -1 if gap else 2, -1 if gap else 3]),
        missing_segment_indices=(2, 3) if gap else (), gap_reasons=(),
        segment_config_generations=tuple((i, 11 + i) for i in range(2 if gap else 4)),
        quality_schema=SweepQualitySchema.NATIVE_V5,
    )


class SweepWaterfallAdapterTests(unittest.TestCase):
    def test_physical_edges_unknown_time_exact_unit_and_original_arrays(self):
        for frame in (progress(), terminal(), terminal(gap=True)):
            update = waterfall_line_from_sweep(frame)
            self.assertIs(update.row.values, frame.values_db)
            self.assertEqual(update.row.timestamp_ns, 0)
            self.assertFalse(update.row.timestamp_known)
            self.assertEqual(update.row.unit_label, frame.unit)
            np.testing.assert_array_equal(update.row.frequency_edges_hz,
                                          [99.5e6, 100.5e6, 101.5e6, 102.5e6, 103.5e6])
            self.assertFalse(update.row.frequency_edges_hz.flags.writeable)
        self.assertEqual(waterfall_line_from_sweep(progress()).stamp.state, SweepRowState.PARTIAL)
        self.assertEqual(waterfall_line_from_sweep(terminal(gap=True)).stamp.state, SweepRowState.GAP)

    def test_large_grid_reuses_peak_and_gap_preserving_lod(self):
        count = 8192
        values = np.full(count, -90., np.float32)
        values[2] = -20.
        values[9] = np.nan
        values.setflags(write=False)
        frame = replace(progress(), frequencies_hz=_readonly(100e6 + np.arange(count), np.float64),
                        values_db=values,
                        quality_flags=_readonly(np.where(np.isnan(values), 4096, 0), np.uint32),
                        source_segment_indices=_readonly(np.where(np.isnan(values), -1, 0), np.int32))
        row = waterfall_line_from_sweep(frame).row
        self.assertEqual(row.values.size, 2048)
        self.assertEqual(row.values[0], -20.)
        self.assertTrue(np.isnan(row.values[2]))
        self.assertEqual(row.frequency_edges_hz[-1], 100e6 + count - .5)
        self.assertTrue(np.isnan(frame.values_db[9]))

    def test_rejects_fabricated_time_mutability_and_nonregular_grid(self):
        update = waterfall_line_from_sweep(progress())
        for change in ({"timestamp_known": True}, {"timestamp_ns": 987654321},
                       {"values": update.row.values.copy()}, {"sequence": 50}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                SweepWaterfallLine(replace(update.row, **change), update.stamp)
        with self.assertRaisesRegex(ValueError, "regular physical grid"):
            waterfall_line_from_sweep(replace(terminal(), frequencies_hz=np.array([1., 2., 4., 8.])))


class SweepWaterfallRingTests(unittest.TestCase):
    def test_upsert_wrap_late_terminal_eviction_and_resize(self):
        renderer = BoundedWaterfallRenderer()
        def offer(sequence, revision=1, state=SweepRowState.PARTIAL):
            return renderer.upsert_sweep(np.array([sequence, revision]), rows=3,
                                         stamp=SweepRowStamp(sequence, revision, state))
        self.assertEqual(offer(1), "append")
        self.assertEqual(offer(1, 2), "replace")
        self.assertEqual(offer(1, 1), "reject")
        self.assertEqual(renderer.buffer.count, 1)
        offer(2)
        self.assertEqual(offer(1, 0, SweepRowState.COMPLETE), "replace")
        self.assertEqual(offer(1, 3), "reject")
        offer(3)
        offer(4)
        self.assertEqual(len(renderer.buffer._sweep_indices), 3)
        self.assertEqual(offer(1, 0, SweepRowState.COMPLETE), "reject")
        self.assertEqual([stamp.sequence for stamp in renderer.sweep_stamps()], [2, 3, 4])
        self.assertLessEqual(len(renderer.tiles()), 2)
        renderer.resize_rows(2)
        self.assertEqual([stamp.sequence for stamp in renderer.sweep_stamps()], [3, 4])
        self.assertEqual(renderer.upsert_sweep(np.array([3, 0]), rows=2,
                                             stamp=SweepRowStamp(3, 0, SweepRowState.GAP)), "replace")
        self.assertEqual(renderer.sweep_stamps()[0].state, SweepRowState.GAP)
        renderer.clear()
        self.assertEqual(renderer.sweep_stamps(), ())
        self.assertEqual(renderer.buffer._sweep_indices, {})


class SweepWaterfallPaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.Format.IniFormat)
        self.pane = WaterfallPane(settings=self.settings)

    def tearDown(self):
        self.pane.close()
        self.pane.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def offer(self, frame):
        self.pane.set_sweep_line(waterfall_line_from_sweep(frame))

    def test_partial_updates_same_row_terminal_once_and_gap_preserved(self):
        for revision in (1, 2, 2, 1, 3):
            self.offer(progress(revision=revision))
            self.assertEqual(self.pane.history_rows, 1)
        self.assertEqual(self.pane.metrics.rows_admitted, 1)
        self.assertEqual(self.pane.metrics.sweep_rows_updated, 2)
        self.assertEqual(self.pane._time_axis.tickStrings([0.], 1., 1.), ["#1 P"])
        self.offer(terminal())
        self.offer(terminal())
        self.offer(progress())
        self.assertEqual(self.pane.history_rows, 1)
        self.assertEqual(self.pane._time_axis.tickStrings([0.], 1., 1.), ["#1 C"])
        self.offer(progress(2))
        self.offer(terminal(2, gap=True))
        self.assertEqual(self.pane._time_axis.tickStrings([0., 1.], 1., 1.), ["#2 G", "#1 C"])
        self.assertTrue(np.isnan(self.pane._renderer.tiles()[0][-1, -1]))
        self.assertEqual(self.pane.metrics.rows_admitted, 2)

    def test_late_terminal_updates_old_row_without_moving_newest(self):
        self.offer(progress(1))
        self.offer(progress(2))
        self.offer(terminal(1))
        self.assertEqual(self.pane.history_rows, 2)
        self.assertEqual(self.pane._time_axis.tickStrings([0., 1.], 1., 1.), ["#2 P", "#1 C"])
        self.pane.set_direction(WaterfallDirection.NEWEST_AT_BOTTOM)
        rows = self.pane.config.history_seconds * self.pane.config.rows_per_second
        self.assertEqual(self.pane._time_axis.tickStrings([rows-2., rows-1.], 1., 1.), ["#1 C", "#2 P"])

    def test_in_place_update_invalidates_image_nan_transparency(self):
        self.offer(progress(revision=3))
        item = self.pane.image_items[0]
        item.render()
        self.assertEqual(item.qimage.pixelColor(2, 0).alpha(), 255)
        self.assertEqual(item.qimage.pixelColor(3, 0).alpha(), 0)
        self.offer(terminal(gap=True))
        item.render()
        self.assertEqual(item.qimage.pixelColor(2, 0).alpha(), 0)
        self.assertEqual(self.pane.history_rows, 1)

    def test_freeze_hidden_clear_epoch_and_source_are_presentation_only(self):
        self.pane.set_render_visible(False)
        self.offer(progress())
        self.pane.set_frozen(True)
        self.offer(terminal())
        self.pane.set_frozen(False)
        self.offer(progress(2))
        self.assertEqual(self.pane.history_rows, 2)
        self.assertEqual(self.pane.metrics.image_uploads, 0)
        self.assertEqual(self.pane._renderer.sweep_stamps()[0].state, SweepRowState.PARTIAL)
        self.offer(terminal(1))
        self.assertEqual(self.pane._renderer.sweep_stamps()[0].state, SweepRowState.COMPLETE)
        self.offer(replace(progress(), source_id="other"))
        self.assertEqual(self.pane.history_rows, 1)
        self.offer(replace(progress(), source_id="other", epoch=9))
        self.assertEqual(self.pane.history_rows, 1)
        self.pane.clear_history()
        self.assertEqual(self.pane.history_rows, 0)
        self.assertEqual(self.pane._time_axis.tickStrings([0.], 1., 1.), [""])

    def test_localized_history_capacity_never_claims_sweep_seconds(self):
        self.offer(progress())
        for locale in (UiLocale.RU, UiLocale.EN):
            self.pane.set_locale(locale)
            self.assertEqual(self.pane._history_seconds.suffix(),
                             text("waterfall.history.block_size", locale, rows=30))
            self.assertEqual(self.pane._rows_per_second.itemText(0),
                             text("waterfall.rows_per_block", locale, value=30))
            self.assertEqual(self.pane._graphics.toolTip(), text("waterfall.sweep.help", locale))


class SweepWaterfallProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        product_fixture.AnalyzerWorkspaceProductTests.setUpClass()

    def test_actual_composition_publishes_partial_and_terminal_to_same_waterfall(self):
        harness = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        harness.setUp()
        try:
            harness.select_and_apply()
            page = harness.page
            canvas = page.visualization
            page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
            snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), progress())
            with patch.object(_FakeAnalyzerDisplay, "poll_latest", return_value=snapshot) as poll:
                page.primary.click()
                harness.wait(lambda: canvas.waterfall_pane.history_rows == 1)
                self.assertIs(page.visualization, canvas)
                self.assertTrue(np.isnan(canvas.waterfall_pane._renderer.tiles()[0][-1, -1]))
                poll.return_value = ContinuousSweepDisplaySnapshot(
                    terminal(1), ContinuousSweepDisplayMetrics(completed_lines=1), progress(2, 2))
                harness.wait(lambda: canvas.waterfall_pane.history_rows == 2)
                stamps = canvas.waterfall_pane._renderer.sweep_stamps()
                self.assertEqual([(s.sequence, s.state) for s in stamps],
                                 [(1, SweepRowState.COMPLETE), (2, SweepRowState.PARTIAL)])
                self.assertEqual(canvas.spectrum_scene.latest_frame.spectrum.sequence, 2)
                poll.return_value = ContinuousSweepDisplaySnapshot(
                    terminal(2, gap=True), ContinuousSweepDisplayMetrics(gapped_lines=1))
                page.primary.click()
                harness.wait(lambda: harness.composition.analyzer_presenter.can_close())
                self.assertEqual(canvas.waterfall_pane.history_rows, 2)
                self.assertEqual(canvas.waterfall_pane._renderer.sweep_stamps()[-1].state, SweepRowState.GAP)
                self.assertEqual(harness.events, ["sweep-start", "sweep-stop"])
            page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.RTBW))
            self.assertEqual(canvas.waterfall_pane.history_rows, 0)
            self.assertFalse(canvas.waterfall_pane._sweep_mode)
        finally:
            harness.tearDown()
            harness.doCleanups()
