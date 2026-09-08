"""Source tests for the UI2-11O visible-environment evidence helper."""

from __future__ import annotations

import unittest

from tests.ui_v2.ui2_11o_visible_environment_probe import _screen_payload


class _FakeRect:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self._values = (x, y, width, height)

    def x(self) -> int:
        return self._values[0]

    def y(self) -> int:
        return self._values[1]

    def width(self) -> int:
        return self._values[2]

    def height(self) -> int:
        return self._values[3]


class _FakeScreen:
    def geometry(self) -> _FakeRect:
        return _FakeRect(0, 0, 1920, 1080)

    def availableGeometry(self) -> _FakeRect:
        return _FakeRect(0, 0, 1920, 1032)

    def logicalDotsPerInch(self) -> float:
        return 96.0

    def physicalDotsPerInch(self) -> float:
        return 141.75

    def devicePixelRatio(self) -> float:
        return 1.75


class Ui211OVisibleEnvironmentProbeTests(unittest.TestCase):
    def test_screen_payload_separates_qt_coordinates_from_effective_device_scale(self) -> None:
        payload = _screen_payload(_FakeScreen(), 2)

        self.assertEqual(payload.index, 2)
        self.assertEqual((payload.geometry.width, payload.geometry.height), (1920, 1080))
        self.assertEqual((payload.available_geometry.width, payload.available_geometry.height), (1920, 1032))
        self.assertEqual(payload.logical_dpi, 96.0)
        self.assertEqual(payload.qt_coordinate_scale_percent, 100.0)
        self.assertEqual(payload.effective_device_scale_percent, 175.0)
        self.assertEqual(payload.device_pixel_ratio, 1.75)
        self.assertEqual((payload.estimated_native_pixel_width, payload.estimated_native_pixel_height), (3360, 1890))


if __name__ == "__main__":
    unittest.main()
