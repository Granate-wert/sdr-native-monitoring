"""Slow-frame rows are paired with one actual source, not percentile sums."""
import unittest

from scripts.profile_app05_rtbw_stages import StageRecords
from scripts.profile_app05_sweep_tail import STAGES, paired_paint


class SweepTailObserverTests(unittest.TestCase):
    def test_exact_identity_durations_sum_to_actual_total_and_rows_stay_bounded(self):
        records = StageRecords(2)
        for sequence in range(4):
            identity = (sequence, "partial", 1)
            for index, name in enumerate(STAGES):
                records.mark(identity, name, sequence * 100 + index)
            paired_paint(records, identity, sequence * 100 + len(STAGES))
        self.assertEqual(len(records.rows), 2)
        for row in records.rows:
            self.assertEqual(sum(value for name, value in row.items() if name not in ("identity", "total")), row["total"])
        self.assertEqual(records.rows[-1]["identity"], (3, "partial", 1))
        paired_paint(records, (3, "complete", 0), 999)
        self.assertEqual(records.missing, 1)
        paired_paint(records, (3, "partial", 1), 300)
        self.assertEqual(records.reordered, 1)


if __name__ == "__main__":
    unittest.main()
