"""No-device checks for the opt-in visible physical UI observer."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import benchmark_app05_physical_ui as observer


class PhysicalUiObserverTests(unittest.TestCase):
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
