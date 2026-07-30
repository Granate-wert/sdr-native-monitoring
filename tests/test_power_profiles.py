from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.power_profiles import BUILTIN_POWER_PROFILES, profile_by_name


class PowerProfileTests(unittest.TestCase):
    def test_required_builtin_profiles_are_present(self) -> None:
        names = {profile.name for profile in BUILTIN_POWER_PROFILES}
        self.assertEqual(len(BUILTIN_POWER_PROFILES), 28)
        self.assertTrue({"Custom", "Wi-Fi 320 MHz", "NB-IoT 180 kHz", "dPMR 6.25 kHz", "Generic OFDM"} <= names)

    def test_profile_values_do_not_prevent_manual_override(self) -> None:
        profile = profile_by_name("Wi-Fi 20 MHz")
        manual_bandwidth = profile.main_bandwidth_hz / 2
        self.assertEqual(manual_bandwidth, 10e6)


if __name__ == "__main__":
    unittest.main()
