from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.domain_export import (
    export_time_gated_events_csv,
    export_time_gated_frames_csv,
    export_time_gated_json,
    export_time_gated_summary_csv,
)
from esw_dfl.spectrogram import SpectrogramRow
from esw_dfl.time_gated_power import (
    ActivityDetectionConfig,
    ActivityThresholdMode,
    ChannelPowerRequest,
    PowerSemantics,
    SmoothingMode,
    TimeGatedChannelPowerService,
)


class TimeGatedExportTests(unittest.TestCase):
    @staticmethod
    def result():
        config = ActivityDetectionConfig(
            threshold_mode=ActivityThresholdMode.ABSOLUTE,
            absolute_threshold_dbm=-50.0,
            smoothing_mode=SmoothingMode.NONE,
            min_active_frames=1,
            min_inactive_frames=1,
            max_gap_frames=0,
            merge_gap_frames=0,
        )
        request = ChannelPowerRequest(
            "session", "waterfall", -0.5, 0.5,
            activity_config=config,
            power_semantics=PowerSemantics.POWER_PER_BIN,
        )
        rows = [
            SpectrogramRow(index, float(index), np.array([level], dtype=np.float32))
            for index, level in enumerate([-80.0, -20.0, -20.0, -80.0])
        ]
        return TimeGatedChannelPowerService().analyze(request, np.array([0.0]), rows)

    def test_all_time_gated_exports_are_atomic_and_parseable(self) -> None:
        result = self.result()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            summary = export_time_gated_summary_csv(result, root / "summary.csv", "sample.dfl")
            frames = export_time_gated_frames_csv(result, root / "frames.csv")
            events = export_time_gated_events_csv(result, root / "events.csv")
            payload = export_time_gated_json(result, root / "result.json", "sample.dfl")
            with summary.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(next(csv.DictReader(handle))["File"], "sample.dfl")
            with frames.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 4)
            with events.open(encoding="utf-8-sig", newline="") as handle:
                self.assertEqual(len(list(csv.DictReader(handle))), 1)
            self.assertEqual(len(json.loads(payload.read_text(encoding="utf-8"))["frames"]), 4)
            self.assertFalse(any(root.glob("*.part")))


if __name__ == "__main__":
    unittest.main()
