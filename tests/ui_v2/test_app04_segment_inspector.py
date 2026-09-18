"""Actual V2 inspector and scalar provenance, no receiver access."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.sweep_acquisition import SweepSegmentAcquisition
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.state.sweep_inspection import inspect_sweep
from sdr_monitor.ui.v2.state.prepared_sweep import prepare_sweep_snapshot
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.workspaces.analyzer_inspector import AnalyzerInspector
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal
from tests import test_app02_analyzer_workspace_product as fixture


def snapshot(sequence=2, revision=1):
    current = progress(sequence, revision)
    records = tuple(SweepSegmentAcquisition(index, generation, 19 + index, 700 + index,
                                            9876543210 - index, 61.44e6, 4096, 4 + index)
                    for index, generation in current.acquired_segment_generations)
    previous = terminal(sequence - 1)
    previous = replace(previous, segment_acquisition=tuple(
        SweepSegmentAcquisition(index, generation, 9 + index, 100 + index, 1234567890 + index,
                                61.44e6, 4096, 0)
        for index, generation in previous.segment_config_generations))
    return ContinuousSweepDisplaySnapshot(previous, ContinuousSweepDisplayMetrics(),
                                         replace(current, segment_acquisition=records))


class SegmentProjectionTests(unittest.TestCase):
    def test_current_pending_history_are_distinct_and_exact_provenance_retained(self):
        source = snapshot()
        result = inspect_sweep(source)
        self.assertEqual((result.epoch, result.sequence, result.previous_sequence), (7, 2, 1))
        self.assertEqual([row.state for row in result.segments], ["received", "pending", "pending", "pending"])
        self.assertIs(result.segments[0].acquisition, source.progress.segment_acquisition[0])
        self.assertEqual(result.segments[0].acquisition.timestamp_ns, 9876543210)
        self.assertIsNone(result.segments[1].generation)
        self.assertIsNone(result.segments[1].acquisition)
        self.assertEqual(result.segments[1].previous_generation, 12)
        self.assertFalse(result.complete)

    def test_terminal_wins_same_or_older_preview_missing_never_received(self):
        line = terminal(2, gap=True)
        # Historical producers may include expected generations for missing segments.
        line = replace(line, segment_config_generations=tuple((i, 11 + i) for i in range(4)))
        for sequence in (1, 2):
            result = inspect_sweep(ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(), progress(sequence)))
            self.assertTrue(result.terminal)
            self.assertFalse(result.complete)
            self.assertEqual([row.state for row in result.segments], ["received", "received", "missing", "missing"])
            self.assertIsNone(result.segments[2].generation)
            self.assertIsNone(result.previous_sequence)

    def test_unknown_clock_no_record_and_out_of_order_records_do_not_invent_cursor(self):
        source = snapshot(revision=3)
        source = replace(source, progress=replace(source.progress,
                          segment_acquisition=tuple(reversed(source.progress.segment_acquisition))))
        report = inspect_sweep(source)
        self.assertEqual([row.index for row in report.segments], [0, 1, 2, 3])
        self.assertEqual([row.acquisition.frame_sequence for row in report.segments[:3]], [19, 20, 21])
        self.assertFalse(hasattr(report, "cursor"))
        self.assertFalse(hasattr(report, "age_ms"))
        unknown = inspect_sweep(ContinuousSweepDisplaySnapshot(terminal(), ContinuousSweepDisplayMetrics()))
        self.assertTrue(unknown.complete)
        self.assertTrue(all(row.acquisition is None for row in unknown.segments))
        self.assertIsNone(inspect_sweep(None))

    def test_inspection_never_reduces_bins_and_refuses_over_capacity(self):
        source = snapshot()
        with patch("numpy.array_equal", side_effect=AssertionError("bin comparison")), \
             patch("numpy.isfinite", side_effect=AssertionError("bin reduction")):
            self.assertEqual(len(inspect_sweep(source).segments), 4)
        oversized = replace(source.progress, pending_segment_indices=tuple(range(1, 65)))
        with self.assertRaisesRegex(ValueError, "64-segment"):
            inspect_sweep(ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), oversized))


class SegmentInspectorTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_active_locale, current_locale())
        self.harness = fixture.AnalyzerWorkspaceProductTests("runTest")
        self.harness.setUpClass()
        self.harness.setUp()
        self.model = self.harness.composition.analyzer_view_model
        self.model.select_mode(AnalyzerMode.SWEEP)

    def tearDown(self):
        try:
            self.harness.tearDown()
        finally:
            self.harness.doCleanups()

    def publish(self, value):
        # Public adapter signal boundary with deterministic immutable data only.
        self.model._on_running(True)
        self.model._on_sweep_snapshot(prepare_sweep_snapshot(value, value.analyzer_bundle))

    def open(self):
        self.harness.shell._inspector_toggle.click()
        self.harness.app.processEvents()
        return self.harness.shell._narrow_inspector_drawer.findChild(AnalyzerInspector)

    def test_actual_factory_selects_segment_without_commands_and_never_substitutes_history(self):
        self.publish(snapshot())
        inspector = self.open()
        self.assertEqual(inspector.segment.count(), 4)
        self.assertIn("9876543210", inspector.detail.text())
        self.assertIn("0x00000004", inspector.detail.text())
        inspector.segment.setFocus()
        QTest.keyClick(inspector.segment, Qt.Key.Key_Down)
        self.assertEqual(inspector.segment.currentData(), 1)
        self.assertIn(text("analyzer.inspection.no_record"), inspector.detail.text())
        self.assertIn(text("analyzer.inspection.previous", sequence=1, generation=12), inspector.detail.text())
        self.assertIn(text("analyzer.inspection.scope"), inspector.scope.text())
        self.assertEqual(self.harness.events, [])

    def test_visible_updates_coalesce_flush_final_and_hidden_projection_stops(self):
        self.publish(snapshot())
        inspector = self.open()
        path = "sdr_monitor.ui.v2.workspaces.analyzer_inspector.inspect_sweep"
        with patch(path, wraps=inspect_sweep) as project:
            for sequence in range(3, 30):
                value = snapshot(sequence)
                self.model._on_sweep_snapshot(prepare_sweep_snapshot(value, value.analyzer_bundle))
            self.assertEqual(project.call_count, 0)
            self.harness.wait(lambda: inspector._report.sequence == 29)
            self.assertEqual(project.call_count, 1)
            inspector.hide()
            value = snapshot(30)
            self.model._on_sweep_snapshot(prepare_sweep_snapshot(value, value.analyzer_bundle))
            QTest.qWait(280)
            self.assertEqual(project.call_count, 1)
            inspector.show()
            self.assertEqual(inspector._report.sequence, 30)
            self.assertEqual(project.call_count, 2)

    def test_mode_reset_and_drawer_deletion_release_subscription(self):
        self.publish(snapshot())
        initial = len(self.model._listeners)
        inspector = self.open()
        self.assertEqual(len(self.model._listeners), initial + 1)
        self.model._on_running(False)
        self.assertTrue(self.model.select_mode(AnalyzerMode.RTBW))
        self.assertIsNone(inspector._report)
        self.assertEqual(inspector.segment.count(), 0)
        self.harness.shell._hide_narrow_inspector_drawer()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(len(self.model._listeners), initial)

    def test_locale_overlay_and_small_drawer_scroll_preserve_canvas(self):
        scene = self.harness.page.visualization.spectrum_scene
        overlay = scene._empty_overlay
        for locale in UiLocale:
            self.harness.shell.select_appearance_locale(locale)
            self.harness.app.processEvents()
            self.assertIs(scene._empty_overlay, overlay)
            self.assertEqual(overlay.accessibleName(), text("spectrum.empty.title"))
            self.assertEqual(overlay._title_label.text(), text("spectrum.empty.title"))
            self.assertEqual(overlay._primary.text(), text("spectrum.empty.primary"))
            self.assertEqual(self.harness.page.source.itemText(0), text("live.device.unselected"))
        self.publish(snapshot())
        for width, height in ((960, 540), (1920, 1080), (2560, 1440)):
            self.harness.shell.resize(width, height)
            self.harness.app.processEvents()
            canvas_geometry = self.harness.page.visualization.geometry()
            inspector = self.open()
            self.assertEqual(self.harness.page.visualization.geometry(), canvas_geometry)
            self.assertEqual(inspector.horizontalScrollBar().maximum(), 0)
            if height == 540:
                self.assertGreater(inspector.verticalScrollBar().maximum(), 0)
            self.harness.shell._hide_narrow_inspector_drawer()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(self.harness.events, [])
