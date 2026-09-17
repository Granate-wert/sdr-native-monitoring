"""Presets must not quantize supported continuous SDR rates."""

from dataclasses import replace
import unittest

from sdr_monitor.domain.live import DeviceCapabilities
from sdr_monitor.services.live_session import _nearest_sample_rate


class SampleRateRangeTests(unittest.TestCase):
    def test_native_adapter_carries_range_through_discovery_selection_and_apply(self):
        from sdr_monitor.domain.live import LiveConfiguration
        from sdr_monitor.services.native_live import NativeLiveSessionService
        from tests.test_native_live_discovery import _FakeNative

        service = NativeLiveSessionService(_FakeNative())
        device = service.discover_devices()[0]
        self.assertTrue(device.capabilities.sample_rate_ranges_hz)
        service.select_device(device.device_id)
        staged = service.apply_configuration(LiveConfiguration(sample_rate_hz=3e6))
        self.assertEqual(staged.applied.requested.sample_rate_hz, 3e6)
        self.assertEqual(staged.applied.applied.sample_rate_hz, 3e6)

    def test_continuous_range_preserves_three_msps_between_presets(self):
        caps = DeviceCapabilities((2.4e6, 5e6, 20e6), (0, 73),
                                  sample_rate_ranges_hz=((2.083333e6, 61.44e6, 0),))
        self.assertEqual(_nearest_sample_rate(caps, 3e6), 3e6)
        self.assertEqual(_nearest_sample_rate(caps, 70e6), 61.44e6)
        self.assertEqual(_nearest_sample_rate(replace(caps, sample_rate_ranges_hz=()), 3e6), 2.4e6)

    def test_step_and_disjoint_ranges_do_not_admit_holes_or_off_grid_maximum(self):
        caps = DeviceCapabilities((10.,), (0, 73),
                                  sample_rate_ranges_hz=((10., 25., 4.), (50., 60., 0.)))
        self.assertEqual(_nearest_sample_rate(caps, 25), 22)
        self.assertEqual(_nearest_sample_rate(caps, 40), 50)
        self.assertEqual(_nearest_sample_rate(caps, 55), 55)
        for ranges in (((10., 1., 0.),), ((1., 10., -1.),), ((1., float('inf'), 0.),)):
            with self.assertRaises(ValueError):
                _nearest_sample_rate(replace(caps, sample_rate_ranges_hz=ranges), 3)
