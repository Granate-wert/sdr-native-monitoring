"""S04 design-system completeness tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from esw_dfl.ui.design_tokens import StatusTone, ThemeId
from esw_dfl.ui.themes import ThemeProvider


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class S04DesignSystemTests(unittest.TestCase):
    def test_status_chip_renders_all_tones(self) -> None:
        from esw_dfl.ui.components import StatusChip

        _app()
        for tone in (StatusTone.NEUTRAL, StatusTone.INFO, StatusTone.SUCCESS, StatusTone.WARNING, StatusTone.ERROR):
            chip = StatusChip("ok", tone)
            self.assertIsNotNone(chip.objectName())

    def test_theme_stylesheets_cover_several_variants(self) -> None:
        dark = ThemeProvider.stylesheet(ThemeId.DARK)
        light = ThemeProvider.stylesheet(ThemeId.LIGHT)
        hc = ThemeProvider.stylesheet(ThemeId.HIGH_CONTRAST)
        self.assertIsInstance(dark, str)
        self.assertIsInstance(light, str)
        self.assertIsInstance(hc, str)
        self.assertIn("color", dark)
        self.assertNotEqual(dark, light)
        self.assertIn("font-weight", hc)

    def test_all_design_tokens_present(self) -> None:
        from esw_dfl.ui.design_tokens import COLOR_BLIND_SCIENTIFIC_PALETTES, DesignTokens

        tokens = DesignTokens()
        self.assertIn("10pt", tokens.typography.body)
        self.assertGreater(tokens.spacing.M, 0)
        self.assertGreater(tokens.radius.M, 0)
        for name in ("cividis", "viridis", "okabe_ito"):
            self.assertIn(name, COLOR_BLIND_SCIENTIFIC_PALETTES)

    def test_component_classes_construct(self) -> None:
        from esw_dfl.ui.components import (
            EmptyState,
            ErrorState,
            MeasurementCard,
            NumericReadout,
            SectionCard,
            StatusChip,
            TaskProgress,
        )

        _app()
        chip = StatusChip("Running")
        chip.set_status("Running", StatusTone.SUCCESS)

        card = MeasurementCard()
        card.set_values("Frequency", "915.000", "MHz", meta="center")

        section = SectionCard("Session")

        empty = EmptyState("No recordings")

        error = ErrorState("Operation failed")

        progress = TaskProgress()
        progress.set_progress(42.0, "Working")

        numeric = NumericReadout("Offset")
        numeric.set_value(1234.567, "Hz", decimals=2)

        self.assertEqual(chip.objectName(), "p16StatusChip")
        self.assertIn("915.000 MHz", card._value.text())
        self.assertEqual(numeric._value.text(), "1234.57 Hz")
        self.assertEqual(error.objectName(), "p16ErrorState")


if __name__ == "__main__":
    unittest.main()
