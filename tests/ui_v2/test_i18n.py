"""Strict unit tests for the UI V2 localization foundation."""

from __future__ import annotations

import re
import unittest

from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.i18n import _CATALOG, UiLocale, catalog_keys, enum_text, resolve_locale, text
from sdr_monitor.ui.v2.shell.appearance_popover import theme_label


class UiV2I18nTests(unittest.TestCase):
    _FORBIDDEN_RU_PROSE = re.compile(
        r"\b(?:active|apply|assurance|backend|bounded|bundle|busy|claims?|compose|"
        r"continuity|contract|correction|direct|discover|dwell|endpoint|eta|finalize|"
        r"frames?|historical|identity|immutable|import|inert|live|native|offline|one-shot|"
        r"override|persistence|placeholders?|presenter|privacy|provenance|raw|readback|"
        r"redacted|ref|replay|review|rollback|runtime|save|seam|select|serial|settings|"
        r"settling|snapshot|support|sweep|trace|uncertainty|verify|visual|waterfall|"
        r"workspace)\b",
        re.IGNORECASE,
    )

    def test_catalog_has_complete_ru_en_entries_and_matching_format_fields(self) -> None:
        self.assertGreater(len(catalog_keys()), 30)
        self.assertEqual(text("appearance.title", UiLocale.RU), "Вид и компоновка")
        self.assertEqual(text("appearance.title", UiLocale.EN), "Appearance and layout")
        self.assertEqual(
            text("shell.status.theme", UiLocale.EN, theme="High contrast"),
            "UI V2 theme: High contrast",
        )

    def test_catalog_fails_closed_for_unknown_key_or_missing_parameter(self) -> None:
        with self.assertRaises(KeyError):
            text("not.a.catalog.key", UiLocale.RU)
        with self.assertRaises(ValueError):
            text("shell.status.theme", UiLocale.RU)

    def test_invalid_persisted_locale_defaults_to_russian_without_global_translator(self) -> None:
        self.assertEqual(resolve_locale(None), UiLocale.RU)
        self.assertEqual(resolve_locale("unexpected"), UiLocale.RU)
        self.assertEqual(resolve_locale("en"), UiLocale.EN)
        self.assertEqual(theme_label(ThemeId.HIGH_CONTRAST, UiLocale.EN), "High contrast")

    def test_enum_labels_are_resolved_from_the_catalog_without_using_wire_values(self) -> None:
        self.assertEqual(enum_text("sweep.state", "completed"), "Завершено")
        self.assertEqual(enum_text("sweep.state", "completed", UiLocale.EN), "Completed")
        with self.assertRaises(ValueError):
            enum_text("", "completed")

    def test_russian_catalog_has_no_known_untranslated_english_prose(self) -> None:
        for key, localized in _CATALOG.items():
            russian = localized[UiLocale.RU].replace("SDR Native Monitoring", "")
            russian = re.sub(r"\{[^}]+\}", "", russian)
            with self.subTest(key=key):
                self.assertIsNone(
                    self._FORBIDDEN_RU_PROSE.search(russian),
                    f"untranslated English prose in {key!r}: {russian!r}",
                )


if __name__ == "__main__":
    unittest.main()
