"""Pure patch resolution: no Qt command or hardware side effect."""

from dataclasses import replace
from types import SimpleNamespace
import unittest

from sdr_monitor.domain import LiveConfiguration
from sdr_monitor.domain.live_configuration_patch import LiveConfigurationPatch, StaleConfigurationPatch
from sdr_monitor.application.live_session import LiveSessionApplicationService


class ConfigurationPatchTests(unittest.TestCase):
    def test_payload_is_captured_once_and_detached(self):
        values = [["gain_db", 29.0]]
        patch = LiveConfigurationPatch("session", 1, "rx", iter(values))
        values[0][1] = 44.0
        self.assertEqual(patch.changes, (("gain_db", 29.0),))
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                LiveConfigurationPatch("session", 1, "rx", (("gain_db", value),))

    def test_application_checks_current_state_before_any_apply(self):
        base = LiveConfiguration(fft_size=8192)
        current = SimpleNamespace(session_id="session-a", generation=7,
                                  device=SimpleNamespace(device_id="rx-a"),
                                  applied=SimpleNamespace(applied=base))
        calls = []
        port = SimpleNamespace(latest_snapshot=lambda: current,
                               apply_configuration=lambda value: calls.append(value))
        application = LiveSessionApplicationService(port)
        patch = LiveConfigurationPatch("session-a", 7, "rx-a", (("gain_db", 29.0),))
        current.generation = 8
        with self.assertRaises(StaleConfigurationPatch):
            application.apply_configuration(patch)
        self.assertEqual(calls, [])
        current.generation = 7
        application.apply_configuration(patch)
        self.assertEqual(calls, [replace(base, gain_db=29.0)])

    def test_resolve_preserves_unedited_fields_and_rejects_stale_identity(self):
        base = LiveConfiguration(fft_size=8192, profile_id="calibration")
        current = SimpleNamespace(session_id="session-a", generation=7,
                                  device=SimpleNamespace(device_id="rx-a"),
                                  applied=SimpleNamespace(applied=base))
        patch = LiveConfigurationPatch("session-a", 7, "rx-a", (("gain_db", 29.0),))
        self.assertEqual(patch.resolve(current), replace(base, gain_db=29.0))
        for field, value in (("generation", 8), ("session_id", "session-b"),
                             ("device", SimpleNamespace(device_id="rx-b")), ("applied", None)):
            with self.subTest(field=field):
                changed = SimpleNamespace(**(vars(current) | {field: value}))
                with self.assertRaises(StaleConfigurationPatch):
                    patch.resolve(changed)

    def test_invalid_changes_fail_closed(self):
        for changes in ((("unknown", 1),), (("gain_db", 1), ("gain_db", 2)),
                        (("gain_db", []),)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                LiveConfigurationPatch("session", 1, "rx", changes)


if __name__ == "__main__":
    unittest.main()
