"""The memory observation harness must expose and bound its own retention."""
import unittest

from tests.ui_v2.run_app05_memory_inventory import MemorySamples


def sample(index):
    return {"index": index, "seconds": index / 10, "page": "analyzer" if index % 2 else "calibration",
            "private_bytes": index * 100, "working_set": index * 50,
            "inventory": {"unique_array_bytes": 10000 - index,
                          "allocation_budget": {"peak_bytes": 20000 + index, "reserved_bytes": index % 3}}}


class MemorySamplerTests(unittest.TestCase):
    def test_bounded_rows_preserve_all_sample_scalar_extrema_and_phase_endpoints(self):
        recorder = MemorySamples(32)
        for index in range(10000):
            recorder.append(sample(index))
        summary = recorder.summary()
        self.assertEqual(len(recorder.rows), 32)
        self.assertEqual(recorder.rows[0]["index"], 9968)
        self.assertEqual(recorder.rows[-1]["index"], 9999)
        self.assertEqual(summary["total_sampled"], 10000)
        self.assertEqual(summary["unique_array_peak"], 10000)
        self.assertEqual(summary["ledger_peak_observed_reserved"], 29999)
        self.assertEqual(summary["post_ack_reserved_peak"], 2)
        self.assertEqual(summary["phases"]["analyzer"]["count"], 5000)
        self.assertEqual(summary["phases"]["analyzer"]["first"]["index"], 1)
        self.assertEqual(summary["phases"]["calibration"]["last"]["index"], 9998)
        self.assertNotIn("inventory", summary["phases"]["analyzer"]["first"])
        self.assertEqual(summary["timing_distribution_scope"], "retained samples only")

    def test_default_preserves_original_all_samples_mode(self):
        recorder = MemorySamples()
        for index in range(100):
            recorder.append(sample(index))
        self.assertEqual(len(recorder.rows), 100)
        self.assertIsNone(recorder.summary()["capacity"])

    def test_invalid_capacity_rejected_before_starting_any_fixture(self):
        for capacity in (0, -1, True, 1.5, "32"):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                MemorySamples(capacity)
