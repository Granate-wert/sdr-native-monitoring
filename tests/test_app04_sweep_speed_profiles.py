"""Real native config and DSP semantics for explicit Sweep presets; no hardware."""
import importlib
import unittest

import numpy as np

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.sweep_speed import SweepSpeedProfile
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
from sdr_monitor.services.native_sweep import NativeSweepLease, NativeSweepSource


class SweepSpeedProfileTests(unittest.TestCase):
    def test_profiles_expose_required_measurement_work_not_ui_fps(self):
        live = LiveConfiguration(sample_rate_hz=61.44e6, fft_size=4096, averaging_frames=7,
                                 backend=BackendKind.CPU)
        for profile, count in (("applied", 7), ("quick", 1), ("balanced", 4), ("averaged", 16)):
            request = ContinuousSweepPlanRequest(100e6, 200e6, speed_profile=profile)
            result = NativeContinuousSweepPlanFactory.preflight_profile(live, request)
            self.assertEqual(result.fft_averaging_frames, count)
            self.assertEqual(result.minimum_samples_per_spectrum, 4096 + (count - 1) * 2048)
            self.assertEqual(result.physical_fft_size, 4096)
            self.assertEqual(result.sample_rate_hz, 61.44e6)
            self.assertEqual(request.acquisition_buffer_samples, 262144)
        self.assertEqual(live.averaging_frames, 7)
        with self.assertRaises(ValueError):
            ContinuousSweepPlanRequest(100e6, 200e6, speed_profile="unverified-fast")

    def test_compiled_factory_and_dsp_honor_profile_without_changing_rtbw(self):
        native = importlib.import_module("sdr_monitor._sdr_native")
        live = LiveConfiguration(sample_rate_hz=61.44e6, analog_bandwidth_hz=56e6,
                                 fft_size=4096, averaging_frames=7, backend=BackendKind.CPU)
        lease = NativeSweepLease(native, NativeSweepSource("ip:never-opened", "profile-test", live),
                                 lambda: None, lambda: None)
        factory = NativeContinuousSweepPlanFactory(lease)
        try:
            baseline = factory.build(ContinuousSweepPlanRequest(100e6, 200e6))
            for profile in SweepSpeedProfile:
                for stop_hz in (130e6, 200e6):
                    request = ContinuousSweepPlanRequest(100e6, stop_hz, speed_profile=profile)
                    result = factory.build(request)
                    count = profile.averaging_frames(7)
                    for segment in result.segments:
                        config = segment.fixed_band
                        self.assertEqual(config.dsp.averaging_frames, count)
                        self.assertEqual(config.dsp.fft_size, 4096)
                        self.assertEqual(config.device.sample_rate_hz, 61.44e6)
                        self.assertEqual(config.device.buffer_samples, 262144)
                        self.assertEqual(config.discard_blocks_after_start,
                                         baseline.segments[0].fixed_band.discard_blocks_after_start)
                    # Real CPU DSP groups count analytical transforms per output.
                    dsp = native.CpuDspBackend()
                    dsp.configure(result.segments[0].fixed_band.dsp)
                    sample_count = 4096 + (count * 2 - 1) * 2048
                    samples = np.asarray(.25 * np.exp(2j * np.pi * 73 * np.arange(sample_count) / 4096), np.complex64)
                    dsp.push_samples(samples, live.sample_rate_hz, live.center_hz)
                    frames = dsp.poll_spectrum(0)
                    self.assertEqual(len(frames), 2)
                    self.assertTrue(all(frame.averaging_frames == count for frame in frames))
                    self.assertAlmostEqual(float(np.max(frames[-1].values)), 20 * np.log10(.25), delta=1e-3)
            self.assertIs(lease.source.live_configuration, live)
            self.assertEqual(live.averaging_frames, 7)
            self.assertEqual(factory.build(ContinuousSweepPlanRequest(100e6, 200e6))
                             .segments[0].fixed_band.dsp.averaging_frames, 7)
        finally:
            factory.close()
