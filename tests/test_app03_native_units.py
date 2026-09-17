"""Native enum wire names must not leak into measurement units."""

from types import SimpleNamespace
import unittest

import numpy as np

from sdr_monitor.services.native_live import NativeLiveSessionService, _native_spectrum_unit
from tests.test_native_live_discovery import _FakeNative


class NativeUnitTests(unittest.TestCase):
    def test_all_declared_units_remain_distinct_and_unknown_is_rejected(self):
        for name, expected in (("DBFS_BIN", "dBFS/bin"), ("DBFS_HZ", "dBFS/Hz"),
                               ("DBM_BIN", "dBm/bin"), ("DBM_HZ", "dBm/Hz"),
                               ("DBM", "dBm")):
            with self.subTest(name=name):
                self.assertEqual(_native_spectrum_unit(SimpleNamespace(name=name)), expected)
                self.assertEqual(_native_spectrum_unit(expected), expected)
        for invalid in (None, 0, "volts", "SpectrumUnit.DBFS_BIN"):
            with self.assertRaises(ValueError):
                _native_spectrum_unit(invalid)

    def test_spectrum_and_density_use_the_same_canonical_unit(self):
        service = NativeLiveSessionService(_FakeNative())
        frequencies = np.array([99.0, 100.0])
        frame = SimpleNamespace(
            frame_sequence=1, timestamp_ns=1, center_frequency_hz=100.0,
            sample_rate_hz=2.0, fft_size=2, hop_size=1,
            frequencies_hz=frequencies, values=np.array([-80.0, -70.0]),
            unit=SimpleNamespace(name="DBFS_BIN"), dropped_samples_before=0,
            dropped_iq_blocks_before=0, dropped_fft_frames_before=0,
            config_generation=0, source=SimpleNamespace(source_id="native-live"),
        )
        self.assertTrue(service._publish_frame(frame))
        self.assertEqual(service.latest_snapshot().spectrum.unit, "dBFS/bin")
        density = SimpleNamespace(
            update_sequence=1, timestamp_ns=1, source_frame_sequence=1,
            power_min_db=-120.0, power_max_db=0.0, power_bins=2, frequency_bins=2,
            processed_frames=1, exponential_decay=False, frequencies_hz=frequencies,
            density=np.ones((2, 2), dtype=np.float32), source_id="native-live",
            config_generation=0, unit=SimpleNamespace(name="DBFS_BIN"),
        )
        service._publish_persistence(density)
        self.assertEqual(service.latest_snapshot().persistence.unit, "dBFS/bin")
