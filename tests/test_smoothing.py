from __future__ import annotations

import unittest

import numpy as np

from esw_dfl.smoothing import (
    SpectrumSmoothMethod,
    interpolate_spectrum,
)


class InterpolationTests(unittest.TestCase):
    def test_none_returns_input_unchanged(self) -> None:
        x = np.array([0.0, 1.0, 2.0])
        y = np.array([0.0, 10.0, 5.0])
        xq = np.array([-1.0, 0.5, 1.5, 3.0])
        result = interpolate_spectrum(x, y, xq, SpectrumSmoothMethod.NONE)
        np.testing.assert_array_equal(result, y)

    def test_pchip_preserves_monotonicity(self) -> None:
        x = np.array([0.0, 1.0, 2.0, 3.0])
        y = np.array([0.0, 5.0, 10.0, 12.0])
        xq = np.linspace(0.0, 3.0, 61)
        result = interpolate_spectrum(x, y, xq, SpectrumSmoothMethod.PCHIP)
        self.assertTrue(np.all(np.diff(result) >= -1e-12))

    def test_makima_matches_linear_for_two_points(self) -> None:
        x = np.array([0.0, 1.0])
        y = np.array([2.0, 5.0])
        xq = np.linspace(0.0, 1.0, 11)
        result = interpolate_spectrum(x, y, xq, SpectrumSmoothMethod.MAKIMA)
        np.testing.assert_allclose(result, np.interp(xq, x, y))


if __name__ == "__main__":
    unittest.main()
