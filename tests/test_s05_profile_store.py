"""S05 profile storage tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sdr_monitor.domain import BackendKind, LiveConfiguration, LiveProfile
from sdr_monitor.services.profile_store import LiveProfileStore


class S05ProfileStoreTests(unittest.TestCase):
    def test_profiles_round_trip_atomically_without_part_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "profiles.json"
            store = LiveProfileStore(path)
            profile = LiveProfile("lab-24", "2.4 GHz monitor", LiveConfiguration(backend=BackendKind.CPU))
            saved = store.upsert(profile)
            self.assertEqual(saved, (profile,))
            self.assertEqual(store.load(), (profile,))
            self.assertFalse(path.with_suffix(".json.part").exists())


if __name__ == "__main__":
    unittest.main()
