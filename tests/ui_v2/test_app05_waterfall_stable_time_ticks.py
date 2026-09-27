"""RTBW uses stable source-age anchors, not jittering row-number labels."""

import os
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.waterfall.axis import WaterfallTimeAxis, _source_row_at_time
from sdr_monitor.ui.v2.waterfall.contracts import WaterfallDirection


class StableTimeTicksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.axis = WaterfallTimeAxis()

    def tearDown(self):
        self.axis.deleteLater()
        self.app.processEvents()

    def set_timebase(self, timestamps, direction=WaterfallDirection.NEWEST_AT_TOP,
                     *, known=True, capacity=300):
        self.axis.set_presentation_timebase(
            direction=direction, rows_per_second=30, display_rows=len(timestamps),
            capacity_rows=capacity, timestamps_ns=np.asarray(timestamps, dtype=np.int64),
            timestamps_known=known,
        )

    def labels(self, size=300):
        ticks = [tick for _, values in self.axis.tickValues(0., 300., size) for tick in values]
        return dict(zip(self.axis.tickStrings(ticks, 1., 1.), ticks))

    def test_jittered_input_keeps_age_text_while_positions_follow_source_time(self):
        timestamps = np.arange(240, dtype=np.int64) * 40_000_000
        self.set_timebase(timestamps)
        before = self.labels()
        self.assertEqual(set(before), {"−0 мс", "−2.0 с", "−4.0 с", "−6.0 с", "−8.0 с"})
        self.set_timebase(np.append(timestamps, timestamps[-1] + 47_000_000))
        after = self.labels()
        self.assertEqual(set(after), set(before))
        self.assertAlmostEqual(after["−2.0 с"] - before["−2.0 с"], -0.175)
        # Repaint/Stop does not advance the timebase or introduce wall time.
        self.assertEqual(self.labels(), after)

    def test_direction_mirrors_positions_without_changing_time_labels(self):
        timestamps = np.arange(240, dtype=np.int64) * 40_000_000
        self.set_timebase(timestamps)
        top = self.labels()
        self.set_timebase(timestamps, WaterfallDirection.NEWEST_AT_BOTTOM)
        bottom = self.labels()
        self.assertEqual(set(top), set(bottom))
        for label in top:
            self.assertAlmostEqual(top[label] + bottom[label], 299.)

    def test_full_ring_regular_jitter_does_not_rewrite_the_tick_text(self):
        intervals = (35, 51, 39, 55, 45, 59, 42)
        timestamps = [0]
        for index in range(370):
            timestamps.append(timestamps[-1] + intervals[index % len(intervals)] * 1_000_000)
        observed = []
        for stop in range(300, 370):
            self.set_timebase(timestamps[stop - 300:stop])
            observed.append(tuple(sorted(self.labels())))
        self.assertEqual(len(set(observed)), 1)
        self.assertGreaterEqual(len(observed[0]), 3)

    def test_only_newest_tick_is_offered_outside_populated_history(self):
        self.set_timebase([1_000_000_000], WaterfallDirection.NEWEST_AT_BOTTOM)
        self.assertEqual(self.axis.tickValues(0., 200., 300)[0][1], [])
        self.assertEqual(self.axis.tickValues(0., 300., 300)[0][1], [299.])

    def test_spacing_hysteresis_does_not_flip_flop_at_a_rounding_boundary(self):
        self.set_timebase(np.arange(300) * 40_000_000)
        for size in (301, 298, 300, 303, 299):
            self.assertEqual(set(self.labels(size)), {"−0 мс", "−5.0 с", "−10.0 с"})

    def test_pause_omits_unobserved_time_ticks_not_fabricated_uniform_rows(self):
        timestamps = np.concatenate((np.arange(50) * 40_000_000,
                                     8_000_000_000 + np.arange(50) * 40_000_000))
        self.set_timebase(timestamps)
        labels = self.labels()
        self.assertIn("−0 мс", labels)
        self.assertIn("−8.0 с", labels)
        for gap_label in ("−2.0 с", "−4.0 с", "−6.0 с"):
            self.assertNotIn(gap_label, labels)
        self.assertIsNone(_source_row_at_time(timestamps, 5_000_000_000, 66_666_666))

    def test_unknown_and_clear_remove_old_time_tick_mapping(self):
        timestamps = np.arange(240) * 40_000_000
        self.set_timebase(timestamps)
        self.assertTrue(self.labels())
        self.set_timebase(timestamps, known=False)
        self.assertEqual(self.axis.tickStrings([0., 100.], 1., 1.), ["", ""])
        self.assertFalse(self.axis._age_ticks)
        self.set_timebase([])
        self.assertEqual(self.axis.tickStrings([0.], 1., 1.), [""])

    def test_slower_regular_producer_is_not_mistaken_for_unobserved_time(self):
        timestamps = np.arange(110) * 90_000_000
        self.set_timebase(timestamps)
        self.assertEqual(set(self.labels()), {"−0 мс", "−2.0 с", "−4.0 с", "−6.0 с", "−8.0 с"})
        self.assertFalse(self.axis._gap_rows)

    def test_locale_resize_and_scale_changes_keep_bounded_source_ticks(self):
        timestamps = np.arange(240) * 40_000_000
        self.set_timebase(timestamps)
        for size in (90, 180, 300, 1080, 2160):
            labels = self.labels(size)
            self.assertLessEqual(len(labels), 21)
            self.assertTrue(all(0 <= row <= 239 for row in labels.values()))
        self.axis.set_locale(UiLocale.EN)
        self.assertIn("−0 ms", self.labels())
        self.assertTrue(all(label.endswith(("ms", "s")) for label in self.labels()))

    def test_gutter_never_shrinks_per_frame_but_can_fit_wider_text(self):
        self.assertFalse(self.axis.style["autoReduceTextSpace"])
        self.axis._updateMaxTextSize(100)
        self.axis._updateMaxTextSize(35)
        self.assertEqual(self.axis.textWidth, 100)
        self.axis._updateMaxTextSize(120)
        self.assertEqual(self.axis.textWidth, 120)


if __name__ == "__main__":
    unittest.main()
