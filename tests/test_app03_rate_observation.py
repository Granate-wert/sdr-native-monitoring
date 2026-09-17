"""Observed native rates are distinct from unmeasured defaults."""
from types import SimpleNamespace
import unittest
from sdr_monitor.domain.live import LivePerformance
from sdr_monitor.services.native_live import NativeLiveSessionService
from tests.test_native_live_discovery import _FakeNative


class RateObservationTests(unittest.TestCase):
    def test_observation_interval_distinguishes_default_from_rates(self):
        self.assertIsNone(LivePerformance().rate_observation_interval_s)
        service = NativeLiveSessionService(_FakeNative())
        service._last_metrics_sample_s = 10
        counters = SimpleNamespace(analytical_fft_rate=451., spectrum_snapshots_emitted=40,
                                   iq_samples_received=13_000_000)
        service._sample_performance(SimpleNamespace(metrics=lambda: counters), 11)
        metrics = service.latest_snapshot().performance
        self.assertEqual(metrics.rate_observation_interval_s, 1)
        self.assertEqual((metrics.analytical_fft_rate_hz, metrics.spectrum_snapshot_rate_hz,
                          metrics.iq_sample_rate_hz), (451., 40., 13_000_000.))
