"""S04 design-system acceptance tests for standalone SDR controls."""

from __future__ import annotations

import os
import pathlib
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from sdr_monitor.ui.component_gallery import build_component_gallery
from sdr_monitor.ui.components import AppliedValueRow, FrequencyInput, MeasurementCard, NumericReadout, StatusChip
from sdr_monitor.ui.design_tokens import COLOR_BLIND_SCIENTIFIC_PALETTES, DARK_COLORS, DesignTokens, StatusTone, ThemeId, contrast_ratio
from sdr_monitor.ui.formatters import parse_frequency_hz
from sdr_monitor.ui.themes import ThemeProvider

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class S04DesignSystemTests(unittest.TestCase):
    def test_all_themes_and_semantic_tokens_exist(self) -> None:
        tokens = DesignTokens()
        self.assertEqual(tokens.spacing.M, 12)
        self.assertIn("Cascadia", tokens.typography.numeric_family)
        for theme in ThemeId:
            self.assertIsInstance(ThemeProvider.stylesheet(theme), str)
        self.assertGreaterEqual(contrast_ratio(DARK_COLORS.text, DARK_COLORS.surface), 4.5)
        self.assertTrue({"cividis", "viridis", "okabe_ito"}.issubset(COLOR_BLIND_SCIENTIFIC_PALETTES))

    def test_components_expose_textual_status_and_applied_value_truth(self) -> None:
        _app()
        chip = StatusChip("Подключено", StatusTone.SUCCESS)
        self.assertIn("success", chip.accessibleDescription())
        applied = AppliedValueRow("Полоса")
        applied.set_values("20 MHz", "19.999 MHz")
        self.assertIn("Запрошено", applied.accessibleDescription())
        card = MeasurementCard()
        card.set_values("PEAK", "-52.34", "dBFS/bin", quality="Нет калибровки")
        self.assertIn("Нет калибровки", card.accessibleDescription())
        readout = NumericReadout("Gain")
        readout.set_value(18, "dB", decimals=0)
        self.assertIn("18 dB", readout.accessibleDescription())

    def test_frequency_parsing_is_locale_aware(self) -> None:
        _app()
        control = FrequencyInput()
        control.setText("2,4 GHz")
        self.assertEqual(control.frequency_hz(), 2.4e9)
        self.assertEqual(parse_frequency_hz("915MHz"), 915e6)

    def test_component_gallery_renders_for_dark_light_and_high_contrast(self) -> None:
        app = _app()
        for theme in (ThemeId.DARK, ThemeId.LIGHT, ThemeId.HIGH_CONTRAST):
            ThemeProvider.apply(app, theme)
            gallery = build_component_gallery()
            gallery.resize(900, 500)
            gallery.show()
            app.processEvents()
            image = gallery.grab()
            self.assertFalse(image.isNull())
            self.assertGreater(image.width(), 0)
            gallery.close()
            gallery.deleteLater()

    def test_new_standalone_widgets_do_not_apply_local_stylesheets(self) -> None:
        targets = [path for path in (ROOT / "sdr_monitor" / "ui").glob("*.py") if path.name != "themes.py"]
        violations = [path.name for path in targets if "setStyleSheet(" in path.read_text(encoding="utf-8")]
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
