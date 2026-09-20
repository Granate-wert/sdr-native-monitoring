"""The memory observation harness must expose and bound its own retention."""
import unittest

from tests.ui_v2.run_app05_memory_inventory import MemorySamples, should_sample, workspace_for


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
        self.assertEqual(len(summary["checkpoints"]), 100)
        self.assertEqual(summary["checkpoints"][0]["index"], 0)
        self.assertEqual(summary["checkpoints"][-1]["index"], 9900)
        self.assertNotIn("inventory", summary["checkpoints"][0])

    def test_checkpoint_history_has_fixed_bound_beyond_cli_profile_limit(self):
        recorder = MemorySamples(1)
        for index in range(20001):
            recorder.append(sample(index))
        self.assertEqual(len(recorder.checkpoints), 100)
        self.assertEqual(recorder.checkpoints[0]["index"], 10100)
        self.assertEqual(recorder.checkpoints[-1]["index"], 20000)

    def test_default_preserves_original_all_samples_mode(self):
        recorder = MemorySamples()
        for index in range(100):
            recorder.append(sample(index))
        self.assertEqual(len(recorder.rows), 100)
        self.assertIsNone(recorder.summary()["capacity"])

    def test_process_only_does_not_report_unmeasured_arrays_as_zero(self):
        recorder = MemorySamples(2)
        for index in range(3):
            recorder.append({**sample(index), "inventory": None})
        summary = recorder.summary()
        self.assertEqual(summary["total_sampled"], 3)
        self.assertEqual(summary["inventory_sampled"], 0)
        for field in ("unique_array_peak", "ledger_peak_observed_reserved", "post_ack_reserved_peak"):
            self.assertIsNone(summary[field])

    def test_sampling_policy_preserves_first_phase_and_final_endpoints(self):
        for mode in ("full", "process-only"):
            self.assertTrue(all(should_sample(i, 1000, mode) for i in range(1000)))
        self.assertEqual([i for i in range(1000) if should_sample(i, 1000, "checkpoints")],
                         [0, 20, 100, 200, 300, 400, 500, 600, 700, 800, 900, 999])
        with self.assertRaises(ValueError):
            should_sample(0, 10, "skip-all")

    def test_visibility_is_explicit_and_default_keeps_original_alternation(self):
        self.assertEqual([workspace_for(i, "alternate") for i in (0, 1, 19, 20, 21, 40)],
                         ["analyzer", None, None, "calibration", None, "analyzer"])
        for page in ("analyzer", "calibration"):
            self.assertEqual(workspace_for(0, page), page)
            self.assertTrue(all(workspace_for(i, page) is None for i in range(1, 1000)))
        with self.assertRaises(ValueError):
            workspace_for(0, "unknown")
        self.assertEqual([workspace_for(i, "alternate", 1) for i in range(4)],
                         ["analyzer", "calibration", "analyzer", "calibration"])
        self.assertTrue(should_sample(1, 200, "checkpoints", 1))
        for invalid in (0, -1, True, 1.5):
            with self.subTest(cadence=invalid), self.assertRaises(ValueError):
                workspace_for(0, "alternate", invalid)

    def test_invalid_capacity_rejected_before_starting_any_fixture(self):
        for capacity in (0, -1, True, 1.5, "32"):
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                MemorySamples(capacity)
