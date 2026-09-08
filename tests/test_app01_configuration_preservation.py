"""I02: V2 visible edits preserve the full applied backend configuration."""

from dataclasses import fields
from types import SimpleNamespace
import unittest

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.ui.v2.workspaces.live import LiveWorkspaceV2


class ConfigurationPreservationTests(unittest.TestCase):
    def test_gain_edit_retains_every_non_visible_field(self):
        base = LiveConfiguration(
            fft_size=8192, analog_bandwidth_hz=7e6, window="blackman",
            averaging_frames=3, snapshot_rate_hz=120,
            persistence_half_life_s=2.5, profile_id="test-profile",
        )
        form = SimpleNamespace(
            _last_applied_configuration=base,
            _center_mhz=SimpleNamespace(value=lambda: base.center_hz / 1e6),
            _sample_rate_mhz=SimpleNamespace(value=lambda: base.sample_rate_hz / 1e6),
            _gain_db=SimpleNamespace(value=lambda: 31.0),
            _backend=SimpleNamespace(currentData=lambda: BackendKind.AUTO.value),
        )
        request = LiveWorkspaceV2._current_configuration(form)
        self.assertEqual(request.gain_db, 31.0)
        for field in fields(base):
            if field.name != "gain_db":
                with self.subTest(field=field.name):
                    self.assertEqual(getattr(request, field.name), getattr(base, field.name))
        self.assertEqual(base.gain_db, 18.0)


if __name__ == "__main__":
    unittest.main()
