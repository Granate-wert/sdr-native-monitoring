"""P16UI-02 design, units, localisation and icon contract tests."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import ClassVar
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.ui.components import FrequencyInput, ReadOnlyValue, StatusBadge
from esw_dfl.ui.design_tokens import COLOR_BLIND_SCIENTIFIC_PALETTES, StatusTone, ThemeId
from esw_dfl.ui.i18n import LocaleId, Translator, translation_keys, validate_catalogs
from esw_dfl.ui.icons import IconId, IconRegistry
from esw_dfl.ui.themes import ThemeProvider
from esw_dfl.ui.units import format_frequency_hz, format_level, parse_frequency_hz, parse_localized_number


class P16UiDesignSystemTests(unittest.TestCase):
    app: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_frequency_parse_and_format_roundtrip_preserves_hz(self) -> None:
        cases = {
            "100k": 100_000.0,
            "2.4G": 2_400_000_000.0,
            "915 MHz": 915_000_000.0,
            "2,4 MHz": 2_400_000.0,
        }
        for text, expected in cases.items():
            self.assertEqual(parse_frequency_hz(text), expected)
        value_hz = 2_400_000_000.0
        rendered = format_frequency_hz(value_hz, decimals=6, locale=LocaleId.EN)
        self.assertEqual(rendered, "2.400000 GHz")
        self.assertAlmostEqual(parse_frequency_hz(rendered, LocaleId.EN), value_hz)
        self.assertAlmostEqual(parse_localized_number("1,25", LocaleId.RU), 1.25)
        with self.assertRaises(ValueError):
            parse_frequency_hz("-1 MHz")

    def test_level_format_never_changes_the_supplied_unit(self) -> None:
        self.assertEqual(format_level(-73.24, "dBFS/bin", locale=LocaleId.EN), "-73.24 dBFS/bin")
        self.assertEqual(format_level(-81.4, "dBm", locale=LocaleId.EN), "-81.40 dBm")
        self.assertEqual(format_level(-132.2, "dBm/Hz", decimals=1, locale=LocaleId.EN), "-132.2 dBm/Hz")

    def test_translation_catalogs_are_complete_and_russian_is_default(self) -> None:
        validate_catalogs()
        self.assertIn("glossary.spectrum", translation_keys())
        self.assertEqual(Translator(LocaleId.RU).text("glossary.waterfall"), "Водопад")
        self.assertEqual(Translator(LocaleId.EN).text("glossary.waterfall"), "Waterfall")
        with self.assertRaisesRegex(KeyError, "missing translation key"):
            Translator().text("missing.key")

    def test_theme_provider_supports_all_required_modes_and_high_contrast(self) -> None:
        for theme in ThemeId:
            applied = ThemeProvider.apply(self.app, theme)
            self.assertEqual(applied, theme)
        self.assertIn("outline: 2px", ThemeProvider.stylesheet(ThemeId.HIGH_CONTRAST))
        options = ThemeProvider.options(Translator(LocaleId.RU))
        self.assertEqual({option.theme_id for option in options}, set(ThemeId))
        self.assertEqual(ThemeProvider.resolve("Тёмная"), ThemeId.DARK)
        self.assertEqual(ThemeProvider.resolve("Высокая контрастность"), ThemeId.HIGH_CONTRAST)

    def test_svg_registry_and_status_badge_provide_icon_and_text(self) -> None:
        for icon_id in IconId:
            self.assertFalse(IconRegistry.icon(icon_id).isNull())
            self.assertIn("<svg", IconRegistry.svg(icon_id))
        badge = StatusBadge("Предупреждение", StatusTone.WARNING)
        self.assertEqual(badge.accessibleName(), "Предупреждение")
        self.assertIn("warning", badge.accessibleDescription())
        self.assertEqual(badge.text, "Предупреждение")
        self.assertGreaterEqual(len(COLOR_BLIND_SCIENTIFIC_PALETTES["cividis"]), 3)

    def test_reusable_controls_keep_values_textual_and_accessible(self) -> None:
        received: list[float] = []
        input_widget = FrequencyInput(locale=LocaleId.EN)
        input_widget.frequency_accepted.connect(received.append)
        input_widget.setText("915 MHz")
        input_widget.editingFinished.emit()
        self.assertEqual(received, [915_000_000.0])
        value = ReadOnlyValue("Power")
        value.set_value(-81.4, "dBm", locale=LocaleId.EN)
        self.assertEqual(value.value_text, "-81.40 dBm")
        self.assertIn("Power", value.accessibleDescription())


if __name__ == "__main__":
    unittest.main()
