"""A native route without a logical device must fail before engine creation."""

from dataclasses import replace
import unittest

from sdr_monitor.domain.live import LiveConfiguration, LiveErrorKind, LiveSessionState
from sdr_monitor.services.native_live import NativeLiveSessionService
from tests.test_native_live_discovery import _FakeNative


class StartIdentityTests(unittest.TestCase):
    def test_missing_device_identity_never_opens_native_engine(self):
        native = _FakeNative()
        service = NativeLiveSessionService(native)
        self.addCleanup(service.stop_and_wait, 1.0)
        device = service.discover_devices()[0]
        service.select_device(device.device_id)
        service.apply_configuration(LiveConfiguration(sample_rate_hz=2e6, gain_db=18))
        service._snapshot = replace(service.latest_snapshot(), device=None)
        result = service.start()
        self.assertIs(result.state, LiveSessionState.ERROR)
        self.assertIs(result.error_kind, LiveErrorKind.DEVICE_NOT_FOUND)
        self.assertEqual(native.engines, [])
        self.assertFalse(service.is_running())
