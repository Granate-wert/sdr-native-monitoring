"""Pure no-device regression for transfer-sized analytical output admission."""
from pathlib import Path
import unittest

from sdr_monitor.services.hackrf_live_admission import HackrfLiveRequest


def request(**changes):
    return HackrfLiveRequest(center_frequency_hz=100e6, sample_rate_hz=8e6,
                            baseband_filter_hz=6_000_000, lna_gain_db=16,
                            vga_gain_db=20, **changes)


class HackrfBurstBudgetTests(unittest.TestCase):
    def test_default_capacity_covers_a_complete_transfer_at_each_fft(self):
        for fft, expected in ((256, 1024), (1024, 256), (4096, 64),
                              (16384, 16), (262144, 1)):
            with self.subTest(fft=fft):
                profile = request(fft_size=fft, hop_size=fft // 2)
                self.assertEqual(profile.dsp_output_capacity, expected)
                self.assertEqual(profile.presentation_capacity, 4)
                # Count frames for every possible carried tail at the input
                # boundary; no hardware or FFT implementation is needed.
                for carried in (fft - profile.hop_size, fft - 1):
                    produced = max(0, (carried + 131072 - fft) // profile.hop_size + 1)
                    self.assertGreaterEqual(profile.dsp_output_capacity, produced)

    def test_nondivisor_hop_uses_ceiling_and_explicit_capacity_is_not_overridden(self):
        self.assertEqual(request(fft_size=1024, hop_size=1000).dsp_output_capacity, 132)
        self.assertEqual(request(dsp_output_capacity=8).dsp_output_capacity, 8)
        self.assertEqual(request(fft_size=1024, hop_size=512,
                                 dsp_output_capacity=256).dsp_output_capacity, 256)

    def test_excessive_overlap_or_queue_payload_fails_before_io(self):
        for fields in ({"hop_size": 1}, {"dsp_output_capacity": 4097},
                       {"dsp_output_capacity": True}, {"dsp_output_capacity": 0},
                       {"fft_size": 262144, "hop_size": 64},
                       {"fft_size": 262144, "hop_size": 131072, "presentation_capacity": 64}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                request(**fields)

    def test_policy_is_pinned_to_current_native_transfer_and_batch_geometry(self):
        root = Path(__file__).resolve().parents[1] / "native/sdr_core"
        ingress = (root / "include/sdr_hackrf/hackrf_rx_ingress.hpp").read_text()
        dsp = (root / "include/sdr_hackrf/hackrf_fixed_band_dsp.hpp").read_text()
        factory = (root / "src/hackrf/hackrf_live_factory.cpp").read_text()
        self.assertIn("slot_bytes{262'144U}", ingress)
        self.assertIn("hackrf_fixed_band_max_dsp_output_capacity = 4096U", dsp)
        self.assertIn("dsp.dsp.batch_size = 1U", factory)


if __name__ == "__main__":
    unittest.main()
