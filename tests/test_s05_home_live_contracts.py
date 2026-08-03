"""S05 domain/service tests independent from hardware and Qt widgets."""

from __future__ import annotations

import unittest

from sdr_monitor.domain import BackendKind, LiveConfiguration, LiveSessionState
from sdr_monitor.services.live_session import InMemoryLiveSessionService, fake_pluto_device


class S05HomeLiveContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = InMemoryLiveSessionService((fake_pluto_device(),))

    def test_device_selection_and_adjusted_values_remain_truthful(self) -> None:
        selected = self.service.select_device("fake-pluto-usb")
        self.assertEqual(selected.state, LiveSessionState.CONNECTED)
        self.assertEqual(selected.device.device_id, "fake-pluto-usb")
        snapshot = self.service.apply_configuration(LiveConfiguration(sample_rate_hz=20e6, gain_db=99.0, backend=BackendKind.CUDA))
        self.assertEqual(snapshot.device.device_id, "fake-pluto-usb")
        self.assertEqual(snapshot.applied.requested.sample_rate_hz, 20e6)
        self.assertEqual(snapshot.applied.applied.sample_rate_hz, 19.999e6)
        self.assertEqual(snapshot.applied.applied.gain_db, 73.0)
        self.assertEqual(snapshot.quality.backend, BackendKind.CPU)
        self.assertFalse(snapshot.reports_dbm)

    def test_manual_usb_uri_becomes_a_selectable_device(self) -> None:
        snapshot = self.service.select_manual_uri("usb:1.12.5")
        self.assertEqual(snapshot.state, LiveSessionState.CONNECTED)
        self.assertEqual(self.service.discover_devices()[-1].uri, "usb:1.12.5")
    def test_start_stop_and_stale_generation_are_bounded(self) -> None:
        self.service.select_device("fake-pluto-usb")
        applied = self.service.apply_configuration(LiveConfiguration())
        self.assertEqual(self.service.start().state, LiveSessionState.RUNNING)
        self.assertEqual(self.service.publish_fake_snapshot(applied.generation).sequence, 1)
        self.assertEqual(self.service.publish_fake_snapshot(applied.generation - 1).sequence, 1)
        self.assertEqual(self.service.stop().state, LiveSessionState.CONNECTED)

    def test_start_without_applied_configuration_returns_actionable_error(self) -> None:
        snapshot = self.service.start()
        self.assertEqual(snapshot.state, LiveSessionState.ERROR)
        self.assertIn("Apply", snapshot.error)


if __name__ == "__main__":
    unittest.main()
