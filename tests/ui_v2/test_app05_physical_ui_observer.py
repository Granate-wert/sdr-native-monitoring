"""No-device checks for the opt-in visible physical UI observer."""

import gc
import sys
import unittest
import weakref
from types import SimpleNamespace
from time import time_ns
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QPolygon

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
        timeline.mark(reused, "model_bundle", 3_000_000, instance=20)
        timeline.paint(reused, 4_000_000, instance=20)
        replaced = (1, 1, 2)
        timeline.mark(replaced, "scheduler_emit", 5_000_000, instance=30)
        timeline.mark(replaced, "prepare_begin", 6_000_000, instance=31)
        timeline.paint(replaced, 9_000_000, instance=40)
        report = timeline.report()
        self.assertEqual(report["ambiguous_paints"], 1)
        self.assertEqual(report["duplicate_stages"]["offer_return"], 1)
        self.assertEqual(report["ambiguous_edges"]["offer_return_to_scheduler_emit"], 1)
        self.assertEqual(report["edges_ms"]["model_bundle_to_paint_return"]["count"], 1)
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
        self.assertFalse(args.visual_substages)
        self.assertFalse(args.hide_persistence)
        self.assertFalse(args.lock_vertical_range)
        self.assertFalse(args.hide_show)
        self.assertEqual(args.render_mode, "direct")
        self.assertEqual(args.display_fps, 120)
        self.assertEqual(args.buffer_samples, 262144)
        self.assertFalse(args.teardown_timing)
        self.assertFalse(args.process_resources)

    def test_process_resource_sampler_is_bounded_and_reports_missed_due_times(self):
        state = {"private_bytes": 100, "working_set_bytes": 200,
                 "peak_working_set_bytes": 250, "handle_count": 10,
                 "python_thread_count": 2}
        sampler = observer.ProcessResourceSampler(reader=lambda: dict(state), capacity=5)
        sampler.start(0)
        sampler.tick(500_000_000)
        self.assertEqual(sampler.report()["retained"], 1)
        state["private_bytes"] = 140
        state["handle_count"] = 11
        sampler.tick(1_000_000_000)
        sampler.tick(2_000_000_000)
        state["private_bytes"] = 160
        state["handle_count"] = 12
        sampler.mark("measure_end", 2_100_000_000)
        sampler.mark("stop_ack", 2_200_000_000)
        with self.assertRaisesRegex(OverflowError, "bounded"):
            sampler.mark("after_close", 2_300_000_000)
        self.assertFalse(sampler.report()["complete"])
        with self.assertRaisesRegex(RuntimeError, "already started"):
            sampler.start(3_000_000_000)

        rows = observer.ProcessResourceSampler(reader=lambda: dict(state), capacity=6)
        rows.start(0)
        rows.tick(1_000_000_000)
        state["private_bytes"] = 180
        state["handle_count"] = 14
        rows.tick(3_100_000_000)  # one missed 2-s deadline, never invented a sample
        rows.mark("measure_end", 3_200_000_000)
        rows.mark("stop_ack", 3_300_000_000)
        rows.mark("after_close", 3_400_000_000)
        report = rows.report()
        self.assertTrue(report["complete"])
        self.assertEqual(report["retained"], 6)
        self.assertEqual(report["missed_due_intervals"], 1)
        self.assertEqual(report["measured_private_delta_bytes"], 20)
        self.assertEqual(report["measured_handle_delta"], 2)
        self.assertEqual(report["measured_private_peak_bytes"], 180)
        self.assertEqual({row["phase"] for row in report["rows"]},
                         {"measure_start", "measure", "measure_end", "stop_ack", "after_close"})

    def test_process_resource_sampler_has_capacity_at_maximum_duration(self):
        state = {"private_bytes": 100, "working_set_bytes": 200,
                 "peak_working_set_bytes": 250, "handle_count": 10,
                 "python_thread_count": 2}
        for delayed in (False, True):
            with self.subTest(delayed=delayed):
                sampler = observer.ProcessResourceSampler(reader=lambda: dict(state))
                sampler.start(0)
                for second in range(1, 121):
                    if delayed and 61 <= second <= 69:
                        continue
                    when_ns = second * 1_000_000_000
                    if delayed and second == 70:
                        when_ns += 250_000_000
                    sampler.tick(when_ns)
                sampler.mark("measure_end", 120_100_000_000)
                sampler.mark("stop_ack", 120_200_000_000)
                sampler.mark("after_close", 120_300_000_000)
                report = sampler.report()
                self.assertTrue(report["complete"])
                self.assertEqual(report["retained"], 115 if delayed else 124)
                self.assertEqual(report["missed_due_intervals"], 9 if delayed else 0)
                self.assertLessEqual(report["retained"], report["capacity"])
                self.assertEqual(report["rows"][-1]["phase"], "after_close")

    @unittest.skipUnless(sys.platform == "win32", "Windows process counters")
    def test_windows_process_resources_read_current_process_without_device(self):
        sampled = observer.windows_process_resources()
        self.assertGreater(sampled["private_bytes"], 0)
        self.assertGreater(sampled["working_set_bytes"], 0)
        self.assertGreater(sampled["peak_working_set_bytes"], 0)
        self.assertGreater(sampled["handle_count"], 0)
        self.assertGreaterEqual(sampled["python_thread_count"], 1)

    def test_visual_substage_profile_requires_an_explicit_flag(self):
        args = observer.parser().parse_args([
            "--uri", "usb:3.12.5", "--output", "unused.json", "--visual-substages",
        ])
        self.assertTrue(args.visual_substages)

    def test_visual_substages_reject_nonvisual_and_split_lane_before_device_open(self):
        for options in ([], ["--render-mode", "visual", "--split-persistence"]):
            argv = ["observer", "--uri", "usb:3.12.5", "--output", "unused.json",
                    "--visual-substages", *options]
            with self.subTest(options=options), patch.object(sys, "argv", argv):
                with self.assertRaises(SystemExit):
                    observer.main()

    def test_hide_show_rejects_confounded_or_too_short_run_before_device_open(self):
        for options in ([], ["--render-mode", "visual", "--duration", "5"],
                        ["--render-mode", "visual", "--hide-persistence"],
                        ["--render-mode", "visual", "--split-persistence"]):
            argv = ["observer", "--uri", "usb:3.12.5", "--output", "unused.json",
                    "--hide-show", *options]
            with self.subTest(options=options), patch.object(sys, "argv", argv):
                with self.assertRaises(SystemExit):
                    observer.main()

    def test_density_identity_witness_is_bounded_and_does_not_retain_array(self):
        witness = observer.DensitySequenceWitness(capacity=1)
        first = np.zeros((2, 2), dtype=np.float32)
        state = SimpleNamespace(bundle=SimpleNamespace(persistence=SimpleNamespace(update_sequence=7)),
                                live=SimpleNamespace(persistence_frame=SimpleNamespace(density=first)))
        witness.observe(state)
        self.assertEqual(witness.sequence(first), 7)
        second = np.ones((2, 2), dtype=np.float32)
        state.live.persistence_frame.density = second
        state.bundle.persistence.update_sequence = 8
        witness.observe(state)
        self.assertIsNone(witness.sequence(first))
        self.assertEqual(witness.sequence(second), 8)
        second_ref = weakref.ref(second)
        state.live.persistence_frame.density = None
        del second
        gc.collect()
        self.assertIsNone(second_ref())

    def test_paint_intersection_requires_visible_item_and_matching_dirty_region(self):
        widget = SimpleNamespace(mapFromScene=lambda _: QPolygon([
            QPoint(10, 10), QPoint(30, 10), QPoint(30, 30), QPoint(10, 30)]))
        item = SimpleNamespace(isVisible=lambda: True, sceneBoundingRect=lambda: QRect(10, 10, 20, 20))
        event = SimpleNamespace(rect=lambda: QRect(20, 20, 5, 5))
        self.assertTrue(observer.paint_intersects_item(widget, event, item))
        event.rect = lambda: QRect(40, 40, 5, 5)
        self.assertFalse(observer.paint_intersects_item(widget, event, item))
        item.isVisible = lambda: False
        self.assertFalse(observer.paint_intersects_item(widget, event, item))

    def test_hide_show_gate_rejects_stale_upload_and_missing_paint(self):
        base = dict(running=True, epoch=4)
        trace = dict(before_hide=dict(**base, workspace="analyzer", native_sequence=100,
                                      latest_sequence=10),
                     after_hide=dict(**base, workspace="calibration", analyzer_visible=False,
                                     presentation_active=False, image_visible=False,
                                     image_present=False, uploaded_sequence=None, image_uploads=5),
                     before_show=dict(**base, native_sequence=110, latest_sequence=13, image_uploads=5),
                     after_show=dict(**base, workspace="analyzer", analyzer_visible=True,
                                     presentation_active=True),
                     at_end=dict(**base, presentation_active=True, image_visible=True,
                                 uploaded_sequence=14, displayed_frame_key=(4, 1, 100)),
                     after_stop=dict(running=False, workspace="analyzer", image_visible=True,
                                     uploaded_sequence=14, displayed_frame_key=(4, 1, 101)),
                     show_target_sequence=13, fresh_paint_ms=250,
                     new_spectrum_paint_ms=210, uploads_total=1, uploads_not_retained=0,
                     hidden_uploads=0, shown_stale_uploads=0, shown_unmapped_uploads=0,
                     first_fresh_visible_upload_ns=123,
                     uploads=[dict(phase="shown", sequence=13, visible=True)])
        self.assertTrue(all(observer.hide_show_checks(trace).values()))
        trace["uploads"] = [dict(phase="shown", sequence=12, visible=True)]
        trace["shown_stale_uploads"] = 1
        trace["first_fresh_visible_upload_ns"] = None
        trace["fresh_paint_ms"] = None
        checks = observer.hide_show_checks(trace)
        self.assertFalse(checks["no_stale_show_upload"])
        self.assertFalse(checks["fresh_visible_upload"])
        self.assertFalse(checks["fresh_qt_paint_within_1s"])
        trace["shown_stale_uploads"] = 0
        trace["first_fresh_visible_upload_ns"] = 123
        trace["fresh_paint_ms"] = 250
        trace["uploads_total"] = 1000
        trace["uploads_not_retained"] = 999
        self.assertTrue(all(observer.hide_show_checks(trace).values()))

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
