from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.domain import (
    FrequencyRegion, Marker, MeasurementMetadata, MeasurementSession, SpectrumTrace, TimeRegion,
)
from esw_dfl.workspace import apply_workspace_session, read_workspace, write_workspace


class WorkspaceTests(unittest.TestCase):
    def test_workspace_round_trip_preserves_user_state(self) -> None:
        session = MeasurementSession("s", Path("sample.dfl"), "Sample", MeasurementMetadata())
        session.traces["t"] = SpectrumTrace("t", "T", 1.0, 2.0, 1.0, np.array([-10, -20]))
        session.active_trace_id = "t"
        session.markers.append(Marker(name="M1", frequency_hz=1.0, power=-10.0, trace_id="t"))
        session.frequency_regions.append(FrequencyRegion(name="Band", start_frequency_hz=1.0, stop_frequency_hz=2.0))
        session.time_regions.append(TimeRegion(name="Burst", start_time=1.0, stop_time=2.0))
        session.display_state["time_gated_channel_power"] = {"threshold_on_offset_db": 10.0}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "workspace.json"
            write_workspace(path, [session], session.session_id, {"theme": "Тёмная"})
            payload = read_workspace(path)
            restored = MeasurementSession("s", Path("sample.dfl"), "Other", MeasurementMetadata())
            restored.traces["t"] = SpectrumTrace("t", "T", 1.0, 2.0, 1.0, np.array([-10, -20]))
            apply_workspace_session(restored, payload["sessions"][0])
            self.assertEqual(restored.name, "Sample")
            self.assertEqual(restored.markers[0].name, "M1")
            self.assertEqual(restored.frequency_regions[0].name, "Band")
            self.assertEqual(restored.time_regions[0].name, "Burst")
            self.assertEqual(
                restored.display_state["time_gated_channel_power"]["threshold_on_offset_db"], 10.0
            )
            self.assertFalse(path.with_suffix(".json.part").exists())


if __name__ == "__main__":
    unittest.main()
