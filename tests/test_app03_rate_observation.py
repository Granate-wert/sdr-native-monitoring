"""Observed native rates are distinct from unmeasured defaults."""
from types import SimpleNamespace
import unittest
from sdr_monitor.domain.live import LivePerformance
from sdr_monitor.services.native_live import NativeLiveSessionService
from tests.test_native_live_discovery import _FakeNative


class RateObservationTests(unittest.TestCase):
    def test_host_block_period_uses_actual_counts_and_rejects_reset_or_missing(self):
        service = NativeLiveSessionService(_FakeNative())
        counters = SimpleNamespace(analytical_fft_rate=100., spectrum_snapshots_emitted=1,
                                   iq_samples_received=1000, iq_blocks_received=10)
        engine = SimpleNamespace(metrics=lambda: counters)
        service._sample_performance(engine, 10)
        self.assertIsNone(service.latest_snapshot().performance.iq_block_rate_hz)
        counters.iq_blocks_received = 30
        service._sample_performance(engine, 11)
        metrics = service.latest_snapshot().performance
        self.assertEqual(metrics.iq_blocks_received, 30)
        self.assertEqual(metrics.iq_block_rate_hz, 20)
        self.assertEqual(metrics.configured_iq_buffer_samples, 262144)
        counters.iq_blocks_received = 5
        service._sample_performance(engine, 12)
        self.assertIsNone(service.latest_snapshot().performance.iq_block_rate_hz)
        del counters.iq_blocks_received
        service._sample_performance(engine, 13)
        self.assertIsNone(service.latest_snapshot().performance.iq_block_rate_hz)

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
