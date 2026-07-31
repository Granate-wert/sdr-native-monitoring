"""Small deterministic Russian/English catalog for new presentation modules."""

from __future__ import annotations

from enum import StrEnum
from typing import Mapping


class LocaleId(StrEnum):
    RU = "ru"
    EN = "en"


_CATALOGS: Mapping[LocaleId, Mapping[str, str]] = {
    LocaleId.RU: {
        "theme.system": "Системная",
        "theme.dark": "Тёмная",
        "theme.light": "Светлая",
        "theme.high_contrast": "Высокая контрастность",
        "glossary.spectrum": "Спектр",
        "glossary.waterfall": "Водопад",
        "glossary.persistence": "Накопление",
        "glossary.sweep": "Сканирование",
        "glossary.calibration": "Калибровка",
        "status.ok": "Готово",
        "status.info": "Информация",
        "status.warning": "Предупреждение",
        "status.error": "Ошибка",
        "unit.invalid_frequency": "Некорректное значение частоты",
    },
    LocaleId.EN: {
        "theme.system": "System",
        "theme.dark": "Dark",
        "theme.light": "Light",
        "theme.high_contrast": "High contrast",
        "glossary.spectrum": "Spectrum",
        "glossary.waterfall": "Waterfall",
        "glossary.persistence": "Persistence",
        "glossary.sweep": "Sweep",
        "glossary.calibration": "Calibration",
        "status.ok": "Ready",
        "status.info": "Information",
        "status.warning": "Warning",
        "status.error": "Error",
        "unit.invalid_frequency": "Invalid frequency value",
    },
}


def translation_keys() -> frozenset[str]:
    return frozenset(_CATALOGS[LocaleId.RU])


def validate_catalogs() -> None:
    expected = translation_keys()
    for locale, catalog in _CATALOGS.items():
        missing = expected - set(catalog)
        extra = set(catalog) - expected
        if missing or extra:
            raise ValueError(f"translation catalog {locale} differs: missing={sorted(missing)}, extra={sorted(extra)}")


class Translator:
    def __init__(self, locale: LocaleId = LocaleId.RU) -> None:
        validate_catalogs()
        self._locale = locale

    @property
    def locale(self) -> LocaleId:
        return self._locale

    def text(self, key: str, **values: object) -> str:
        try:
            template = _CATALOGS[self._locale][key]
        except KeyError as error:
            raise KeyError(f"missing translation key: {key}") from error
        return template.format(**values)


DEFAULT_TRANSLATOR = Translator()
