"""Software-only tests for the tinySA Ultra 3,100-point policy analysis."""

from __future__ import annotations

import unittest

from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_sweep_policy import (
    TINYSA_26FC821_PARAMETER_ACCESS,
    TINYSA_PRODUCT_SCANRAW_PAYLOAD_BYTES_MAX,
    TINYSA_PRODUCT_SCANRAW_POINTS_MAX,
    TinySaParameterAccess,
    TinySaSweepAccuracy,
    display_point_limit,
    scanraw_geometry,
    sweep_accuracy_semantics,
    validate_firmware_speedup,
)


class TinySaSweepPolicyTests(unittest.TestCase):
    def test_display_limit_is_not_the_host_scanraw_limit(self) -> None:
        self.assertEqual(display_point_limit(TinySaModel.BASIC), 290)
        self.assertEqual(display_point_limit(TinySaModel.ULTRA), 450)
        self.assertEqual(TINYSA_PRODUCT_SCANRAW_POINTS_MAX, 10_001)
        self.assertEqual(TINYSA_PRODUCT_SCANRAW_PAYLOAD_BYTES_MAX, 30 * 1_024)

    def test_3100_point_geometry_matches_firmware_stop_exclusive_grid(self) -> None:
        geometry = scanraw_geometry(87_500_000, 108_000_000, 3_100)

        self.assertEqual(geometry.frequency_step_hz, 6_612)
        self.assertEqual(geometry.last_frequency_hz, 107_990_588)
        self.assertEqual(geometry.payload_bytes, 9_302)
        self.assertLess(geometry.last_frequency_hz, geometry.requested_stop_frequency_hz)

    def test_product_limit_and_degenerate_step_fail_closed(self) -> None:
        for points, stop in ((10_002, 108_000_000), (10_001, 1_000)):
            with self.subTest(points=points, stop=stop), self.assertRaises(ValueError):
                scanraw_geometry(0, stop, points)

    def test_10001_point_product_cap_fits_the_30_kib_frame_bound(self) -> None:
        geometry = scanraw_geometry(87_500_000, 108_000_000, 10_001)

        self.assertEqual(geometry.frequency_step_hz, 2_049)
        self.assertEqual(geometry.last_frequency_hz, 107_990_000)
        self.assertEqual(geometry.payload_bytes, 30_005)
        self.assertLess(geometry.payload_bytes, TINYSA_PRODUCT_SCANRAW_PAYLOAD_BYTES_MAX)

    def test_accuracy_modes_retain_their_distinct_measurement_tradeoffs(self) -> None:
        unchanged = sweep_accuracy_semantics(TinySaSweepAccuracy.UNCHANGED)
        precise = sweep_accuracy_semantics(TinySaSweepAccuracy.PRECISE)
        fast = sweep_accuracy_semantics(TinySaSweepAccuracy.FAST)
        noise = sweep_accuracy_semantics(TinySaSweepAccuracy.NOISE_SOURCE)

        self.assertTrue(unchanged.safe_automatic_default)
        self.assertIsNone(unchanged.shell_command)
        self.assertEqual(precise.relative_minimum_time, 4.0)
        self.assertEqual(fast.expected_noise_penalty_db, 10.0)
        self.assertIn("broadband", noise.intended_signal)

    def test_speedups_are_ui_only_for_exact_firmware(self) -> None:
        self.assertIs(
            TINYSA_26FC821_PARAMETER_ACCESS["nspeedup"],
            TinySaParameterAccess.UI_ONLY_NO_STABLE_SHELL,
        )
        self.assertIs(
            TINYSA_26FC821_PARAMETER_ACCESS["wspeedup"],
            TinySaParameterAccess.UI_ONLY_NO_STABLE_SHELL,
        )
        for value in (0, 2, 4, 20):
            self.assertEqual(validate_firmware_speedup(value), value)
        for value in (1, 21):
            with self.assertRaises(ValueError):
                validate_firmware_speedup(value)

    def test_version_pinned_readback_surface_is_not_overstated(self) -> None:
        for parameter in ("rbw", "sweep_time", "attenuation"):
            with self.subTest(parameter=parameter):
                self.assertIs(
                    TINYSA_26FC821_PARAMETER_ACCESS[parameter],
                    TinySaParameterAccess.SHELL_READBACK,
                )
        for parameter in ("accuracy_mode", "repeat", "spur_removal", "lna"):
            with self.subTest(parameter=parameter):
                self.assertIs(
                    TINYSA_26FC821_PARAMETER_ACCESS[parameter],
                    TinySaParameterAccess.SHELL_WRITE_NO_READBACK,
                )


if __name__ == "__main__":
    unittest.main()
