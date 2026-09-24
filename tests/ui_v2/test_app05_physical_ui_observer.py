"""No-device checks for the opt-in visible physical UI observer."""

import sys
import unittest
from types import SimpleNamespace
from time import time_ns
from unittest.mock import patch

from scripts import benchmark_app05_physical_ui as observer


class PhysicalUiObserverTests(unittest.TestCase):
    def test_required_projection_acceptance_includes_same_source_reprojection(self):
        owner = object()
        request = SimpleNamespace(required_work=True, owner=owner)
        scene = SimpleNamespace(_projection_owner=owner, _early_projection_request=None,
                                _projection_current=lambda _: True)
        self.assertTrue(observer.required_projection_acceptance(scene, request,
                         required_only=False))
        scene._early_projection_request = request
        self.assertFalse(observer.required_projection_acceptance(scene, request,
                         required_only=False))
        self.assertTrue(observer.required_projection_acceptance(scene, request,
                         required_only=True))
        request.required_work = False
        self.assertFalse(observer.required_projection_acceptance(scene, request,
                         required_only=True))

    def test_frame_timeline_records_only_first_paint_and_missing_stages(self):
        timeline = observer.FrameTimeline()
        key = (2, 3, 4)
        stages = ("offer_return", "scheduler_emit", "prepare_begin", "prepare_end",
                  "model_snapshot", "model_bundle", "required_ready", "scene_accept")
        for index, stage in enumerate(stages):
            timeline.mark(key, stage, index * 1_000_000,
                          instance=10 if index <= 4 else 20)
        timeline.paint(key, 8_000_000, instance=20)
        timeline.paint(key, 9_000_000, instance=20)
        report = timeline.report()
        self.assertEqual(report["first_paints"], 1)
        self.assertEqual(report["edges_ms"]["model_bundle_to_paint_return"]["ms"]["p50"], 3.0)
        self.assertEqual(report["edges_ms"]["prepare_begin_to_prepare_end"]["ms"]["p50"], 1.0)
        timeline.paint((2, 3, 5), 9_000_000, instance=20)
        report = timeline.report()
        self.assertEqual(report["missing"]["model_bundle_to_paint_return"], 1)
        self.assertEqual(report["first_paints"], 2)

    def test_frame_timeline_reports_eviction_and_out_of_order_without_false_percentiles(self):
        timeline = observer.FrameTimeline(capacity=1)
        timeline.mark((1, 1, 1), "model_bundle", 10_000_000, instance=1)
        timeline.mark((1, 1, 2), "model_bundle", 12_000_000, instance=2)
        timeline.paint((1, 1, 2), 11_000_000, instance=2)
        report = timeline.report()
        self.assertEqual(report["evicted_keys"], 1)
        self.assertEqual(report["out_of_order"]["model_bundle_to_paint_return"], 1)
        self.assertIsNone(report["edges_ms"]["model_bundle_to_paint_return"]["ms"])

    def test_frame_timeline_excludes_reused_frame_keys_and_replaced_snapshot_edges(self):
        timeline = observer.FrameTimeline()
        reused = (1, 1, 1)
        timeline.mark(reused, "offer_return", 1_000_000, instance=10)
        timeline.mark(reused, "offer_return", 2_000_000, instance=11)
        timeline.paint(reused, 4_000_000, instance=20)
        replaced = (1, 1, 2)
        timeline.mark(replaced, "scheduler_emit", 5_000_000, instance=30)
        timeline.mark(replaced, "prepare_begin", 6_000_000, instance=31)
        timeline.paint(replaced, 9_000_000, instance=40)
        report = timeline.report()
        self.assertEqual(report["ambiguous_paints"], 1)
        self.assertEqual(report["duplicate_stages"]["offer_return"], 1)
        self.assertEqual(report["identity_mismatch"]["scheduler_emit_to_prepare_begin"], 1)
        self.assertEqual(report["edges_ms"]["scheduler_emit_to_prepare_begin"]["count"], 0)

    def test_repeat_paint_classifies_tracked_density_change_without_new_frame(self):
        witness = observer.PaintObserver()
        spectrum_widget = object()
        waterfall_widget = object()
        witness.workspace = SimpleNamespace(visualization=SimpleNamespace(
            spectrum_scene=SimpleNamespace(_graphics=spectrum_widget),
            waterfall_pane=SimpleNamespace(_graphics=waterfall_widget)))
        witness.measuring = True
        frame = SimpleNamespace(key=(1, 2, 3), spectrum=SimpleNamespace(
            timestamp_ns=time_ns(), timestamp_quality="host", loss_reasons=()))
        with patch.object(observer.PaintObserver, "key", lambda _, bundle: bundle.key):
            witness.painted(spectrum_widget, 1, 2, frame, frame, 1.0)
            witness.density_uploads += 1
            witness.painted(spectrum_widget, 3, 4, frame, frame, 0.5)
            witness.model_events += 1
            witness.waterfall_rows_admitted += 1
            witness.painted(spectrum_widget, 5, 6, frame, frame, 0.5)
        self.assertEqual(witness.unique_paints, 1)
        self.assertEqual(witness.repeat_paints, 2)
        self.assertEqual(dict(witness.repeat_causes), {
            "density_only": 1, "no_required_or_density_admission": 1,
        })
        self.assertEqual(dict(witness.repeat_precursors), {"model+waterfall": 1})

    def test_scalar_series_never_mislabels_a_truncated_tail_as_a_percentile(self):
        series = observer.ScalarSeries(capacity=2)
        self.assertIsNone(series.report()["ms"])
        series.add(1.0)
        series.add(3.0)
        self.assertEqual(series.report()["ms"]["p50"], 2.0)
        series.add(100.0)
        report = series.report()
        self.assertEqual(report["count"], 3)
        self.assertEqual(report["dropped"], 1)
        self.assertIsNone(report["ms"])

    def test_invalid_uri_and_fft_fail_before_import_or_open(self):
        for argv in (
            ["observer", "--uri", "not-a-device", "--output", "unused.json"],
            ["observer", "--uri", "usb:3.12.5", "--fft", "1000", "--output", "unused.json"],
        ):
            with self.subTest(argv=argv), patch.object(sys, "argv", argv):
                with self.assertRaises(SystemExit):
                    observer.main()

    def test_parser_defaults_do_not_enable_opt_in_split_lane(self):
        args = observer.parser().parse_args(["--uri", "usb:3.12.5", "--output", "unused.json"])
        self.assertFalse(args.split_persistence)
        self.assertFalse(args.hide_persistence)
        self.assertFalse(args.lock_vertical_range)
        self.assertEqual(args.render_mode, "direct")
        self.assertEqual(args.display_fps, 120)
        self.assertEqual(args.buffer_samples, 262144)

    def test_interval_counters_are_differences_not_lifetime_values(self):
        before = SimpleNamespace(fft_frames_computed=100, fft_frames_dropped=2)
        after = SimpleNamespace(fft_frames_computed=120, fft_frames_dropped=2)
        self.assertEqual(observer.counter_deltas(before, after,
                         ("fft_frames_computed", "fft_frames_dropped")),
                         {"fft_frames_computed": 20, "fft_frames_dropped": 0})
        optional = SimpleNamespace(completed=4, cancelled=1, superseded=2)
        projector = SimpleNamespace(completed=7, cancelled=0, superseded=3,
                                    persistence_projector=optional)
        self.assertEqual(observer.projector_counters(projector), {
            "completed": 7, "cancelled": 0, "superseded": 3,
            "optional_completed": 4, "optional_cancelled": 1, "optional_superseded": 2,
        })


if __name__ == "__main__":
    unittest.main()
