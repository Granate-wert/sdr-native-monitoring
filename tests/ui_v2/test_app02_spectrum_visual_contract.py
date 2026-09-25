"""F17 colour, LUT legend and in-place locale contracts."""

from __future__ import annotations

import os
import unittest
from dataclasses import fields

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.design.tokens import (
    ThemeId,
    contrast_ratio,
    density_lookup_table,
    tokens_for_theme,
)
from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.spectrum import TraceKind
from sdr_monitor.ui.v2.spectrum.persistence_contracts import inferno_lookup_table
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene


def _frame():
    frequencies = np.linspace(100.0, 200.0, 101)
    values = np.linspace(-100.0, -20.0, 101, dtype=np.float32)
    frequencies.setflags(write=False)
    values.setflags(write=False)
    return type("Frame", (), {"frequencies_hz": frequencies, "values": values, "unit": "dBFS/Hz"})()


class SpectrumVisualContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_light_scientific_colours_are_readable_and_hc_roles_distinct(self) -> None:
        light = tokens_for_theme(ThemeId.LIGHT)
        for item in fields(light.scientific):
            color = getattr(light.scientific, item.name)
            self.assertGreaterEqual(contrast_ratio(color, light.colors.panel), 4.5)
        high_contrast = tokens_for_theme(ThemeId.HIGH_CONTRAST).scientific
        self.assertEqual(
            len({getattr(high_contrast, item.name) for item in fields(high_contrast)}),
            len(fields(high_contrast)),
        )

    def test_density_image_and_legend_share_one_exact_lut(self) -> None:
        self.assertTrue(np.array_equal(inferno_lookup_table(), density_lookup_table()))
        self.assertEqual(tuple(density_lookup_table()[0]), (0, 0, 4, 0))
        self.assertEqual(tuple(density_lookup_table()[-1]), (252, 255, 164, 255))

    def test_existing_curves_receive_theme_pens_without_replacing_measurement(self) -> None:
        scene = SpectrumScene()
        scene.set_frame(_frame())
        original = scene.latest_frame
        mapping = {TraceKind.CURRENT: "current_spectrum", TraceKind.AVERAGE: "average",
                   TraceKind.MAXIMUM: "max_hold", TraceKind.MINIMUM: "min_hold"}
        for theme in (ThemeId.LIGHT, ThemeId.HIGH_CONTRAST, ThemeId.DARK):
            scene.set_theme(theme)
            colors = tokens_for_theme(theme).scientific
            for kind, role in mapping.items():
                self.assertEqual(scene._curves[kind].opts["pen"].color().name(), getattr(colors, role).lower())
            self.assertIs(scene.latest_frame, original)
            if theme is ThemeId.HIGH_CONTRAST:
                self.assertEqual(len({curve.opts["pen"].style() for curve in scene._curves.values()}), 4)
        scene.close()

    def test_locale_retranslation_preserves_canvas_state(self) -> None:
        scene = SpectrumScene()
        frame = _frame()
        scene.set_frame(frame)
        scene.plot_item.setXRange(120.0, 140.0, padding=0.0)
        scene.place_marker("M1", 130.0)
        viewport = tuple(scene.plot_item.getViewBox().viewRange()[0])
        latest = scene.latest_frame
        marker = scene.markers[0]
        scene.set_locale(UiLocale.EN)
        self.assertEqual(scene._frequency_axis.tickStrings([1_000_000.0], 1.0, 1.0), ["1.000000 MHz"])
        self.assertEqual(tuple(scene.plot_item.getViewBox().viewRange()[0]), viewport)
        self.assertIs(scene.latest_frame, latest)
        self.assertEqual(scene.markers[0], marker)


if __name__ == "__main__":
    unittest.main()
