"""Small, explicit translation boundary for standalone SDR UI strings."""

from __future__ import annotations

from enum import StrEnum


class LocaleId(StrEnum):
    RU = "ru"
    EN = "en"


_MESSAGES = {
    "app.name": ("SDR Native Monitoring", "SDR Native Monitoring"),
    "workspace.home": ("Обзор", "Home"),
    "workspace.live": ("Мониторинг", "Live"),
    "workspace.sweep": ("Диапазон", "Sweep"),
    "workspace.calibration": ("Калибровка", "Calibration"),
    "workspace.recording": ("Запись", "Recording"),
    "workspace.diagnostics": ("Диагностика", "Diagnostics"),
    "home.description": ("Быстрый старт и состояние системы", "Quick start and system status"),
    "live.description": ("Спектр и водопад в реальном времени", "Live spectrum and waterfall"),
    "sweep.description": ("План и выполнение обзора диапазона", "Wideband sweep planning and execution"),
    "calibration.description": ("Профили и применимость калибровки", "Calibration profiles and applicability"),
    "recording.description": ("Запись и воспроизведение измерений", "Measurement recording and replay"),
    "diagnostics.description": ("Проверка окружения и оборудования", "Environment and equipment diagnostics"),
    "status.ready": ("Готово", "Ready"),
    "status.not_connected": ("Приёмник не подключён", "Receiver is not connected"),
    "action.expand_navigation": ("Развернуть навигацию", "Expand navigation"),
    "action.collapse_inspector": ("Скрыть инспектор", "Hide inspector"),
    "inspector.title": ("Контекст", "Context"),
    "inspector.empty": ("Параметры выбранного рабочего пространства появятся здесь.", "Settings for the selected workspace appear here."),
    "unit.invalid_frequency": ("Некорректная частота", "Invalid frequency"),
    "field.requested": ("Запрошено", "Requested"),
    "field.applied": ("Применено", "Applied"),
    "error.title": ("Ошибка", "Error"),
    "action.retry": ("Повторить", "Retry"),
}


class Translator:
    def __init__(self, locale: LocaleId = LocaleId.RU) -> None:
        self.locale = locale

    def text(self, key: str) -> str:
        pair = _MESSAGES.get(key)
        if pair is None:
            return key
        return pair[0] if self.locale is LocaleId.RU else pair[1]


DEFAULT_TRANSLATOR = Translator()
