"""Strict UI V2 presentation strings; this module has no backend dependencies."""

from __future__ import annotations

from contextvars import ContextVar
from enum import StrEnum
from string import Formatter
from types import MappingProxyType
from typing import Mapping


class UiLocale(StrEnum):
    """The only product locales planned for the fully migrated V2 surface."""

    RU = "ru"
    EN = "en"


TranslationEntry = Mapping[UiLocale, str]

_ACTIVE_LOCALE: ContextVar[UiLocale] = ContextVar("ui_v2_active_locale", default=UiLocale.RU)


_CATALOG: Mapping[str, TranslationEntry] = MappingProxyType(
    {
        "waterfall.axis.sweep": MappingProxyType({UiLocale.RU: "Проходы", UiLocale.EN: "Passes"}),
        "waterfall.sweep.invalid": MappingProxyType({UiLocale.RU: "Водопад сканирования недоступен: {reason}", UiLocale.EN: "Sweep Waterfall unavailable: {reason}"}),
        "waterfall.sweep.help": MappingProxyType({UiLocale.RU: "Строка — один проход, а не один момент времени. P — неполный; C — завершённый; G — с пропусками. Пропуски номеров означают непоказанные проходы; это не шкала секунд и не оценка потери I/Q.", UiLocale.EN: "One row is one pass, not one instant. P — unfinished; C — Complete; G — Gap. Skipped numbers are undisplayed passes, not a seconds scale or an I/Q loss estimate."}),
        "waterfall.history.blocks": MappingProxyType({UiLocale.RU: "История: ", UiLocale.EN: "History: "}),
        "waterfall.history.block_size": MappingProxyType({UiLocale.RU: " × {rows} строк", UiLocale.EN: " × {rows} rows"}),
        "waterfall.rows_per_block": MappingProxyType({UiLocale.RU: "Блок {value} строк", UiLocale.EN: "Block of {value} rows"}),
        "analyzer.title": MappingProxyType({UiLocale.RU: "Анализатор", UiLocale.EN: "Analyzer"}),
        "analyzer.description": MappingProxyType({UiLocale.RU: "Общий спектр фиксированной полосы и сканирования", UiLocale.EN: "Shared RTBW / Sweep spectrum"}),
        "analyzer.mode": MappingProxyType({UiLocale.RU: "Режим приёма", UiLocale.EN: "Acquisition mode"}),
        "analyzer.mode.rtbw": MappingProxyType({UiLocale.RU: "RTBW", UiLocale.EN: "RTBW"}),
        "analyzer.mode.sweep": MappingProxyType({UiLocale.RU: "Сканирование", UiLocale.EN: "Sweep"}),
        "analyzer.start": MappingProxyType({UiLocale.RU: "Старт", UiLocale.EN: "Start"}),
        "analyzer.stop": MappingProxyType({UiLocale.RU: "Стоп", UiLocale.EN: "Stop"}),
        "analyzer.starting": MappingProxyType({UiLocale.RU: "Запуск…", UiLocale.EN: "Starting…"}),
        "analyzer.stopping": MappingProxyType({UiLocale.RU: "Остановка…", UiLocale.EN: "Stopping…"}),
        "analyzer.settings": MappingProxyType({UiLocale.RU: "Параметры", UiLocale.EN: "Settings"}),
        "analyzer.display": MappingProxyType({UiLocale.RU: "Отображение", UiLocale.EN: "Display"}),
        "analyzer.close": MappingProxyType({UiLocale.RU: "Закрыть", UiLocale.EN: "Close"}),
        "live_state.age.unknown": MappingProxyType({UiLocale.RU: "Возраст кадра неизвестен", UiLocale.EN: "Frame age unknown"}),
        "analyzer.apply": MappingProxyType({UiLocale.RU: "Применить", UiLocale.EN: "Apply"}),
        "analyzer.cancel": MappingProxyType({UiLocale.RU: "Отменить правки", UiLocale.EN: "Discard edits"}),
        "analyzer.start_frequency": MappingProxyType({UiLocale.RU: "Начало, MHz", UiLocale.EN: "Start, MHz"}),
        "analyzer.stop_frequency": MappingProxyType({UiLocale.RU: "Конец, MHz", UiLocale.EN: "Stop, MHz"}),
        "analyzer.center": MappingProxyType({UiLocale.RU: "Центр, MHz", UiLocale.EN: "Center, MHz"}),
        "analyzer.sample_rate": MappingProxyType({UiLocale.RU: "Дискретизация, MS/s", UiLocale.EN: "Sample rate, MS/s"}),
        "analyzer.gain": MappingProxyType({UiLocale.RU: "Усиление, dB", UiLocale.EN: "Gain, dB"}),
        "analyzer.fft": MappingProxyType({UiLocale.RU: "Физический FFT", UiLocale.EN: "Physical FFT"}),
        "analyzer.unavailable": MappingProxyType({UiLocale.RU: "Нет опубликованного кадра", UiLocale.EN: "No published frame"}),
        "analyzer.sweep_time": MappingProxyType({UiLocale.RU: "Сегменты получены в разное время", UiLocale.EN: "Sweep: segments acquired at different times"}),
        "analyzer.speed.title": MappingProxyType({UiLocale.RU: "Профиль следующего сканирования", UiLocale.EN: "Next Sweep profile"}),
        "analyzer.speed.applied": MappingProxyType({UiLocale.RU: "Из применённой конфигурации", UiLocale.EN: "Use applied configuration"}),
        "analyzer.speed.quick": MappingProxyType({UiLocale.RU: "Быстрый — 1 FFT / спектр", UiLocale.EN: "Quick — 1 FFT / spectrum"}),
        "analyzer.speed.balanced": MappingProxyType({UiLocale.RU: "Сбалансированный — 4 FFT / спектр", UiLocale.EN: "Balanced — 4 FFT / spectrum"}),
        "analyzer.speed.averaged": MappingProxyType({UiLocale.RU: "Усреднённый — 16 FFT / спектр", UiLocale.EN: "Averaged — 16 FFT / spectrum"}),
        "analyzer.speed.help": MappingProxyType({
            UiLocale.RU: "Для следующего запуска сканирования; нажимать «Применить» не требуется. Больше FFT — сильнее усреднение, реже новый спектр. Fs, размер FFT, буферы и отбрасывание после перестройки не меняются. LPS не гарантируется; RTBW сохраняет свои настройки.",
            UiLocale.EN: "For the next Sweep Start only; no Apply needed. More FFTs means more averaging and fewer new spectra. Fs, FFT size, buffers and post-retune discard stay unchanged. No guaranteed LPS; RTBW retains its settings.",
        }),
        "analyzer.sweep_statistics": MappingProxyType({
            UiLocale.RU: "Статистика: {passes} проходов, до #{sequence}, отставание {lag}; density {columns} ячеек — доля наблюдений bin; внутри прохода возможна задержка",
            UiLocale.EN: "Statistics: {passes} passes, through #{sequence}, lag {lag}; density {columns} cells — bin-observation probability; may lag within pass",
        }),
        "analyzer.local": MappingProxyType({UiLocale.RU: "Масштаб графика локальный — не перестраивает SDR", UiLocale.EN: "Plot zoom is local — does not retune SDR"}),
        "analyzer.rx.unavailable": MappingProxyType({UiLocale.RU: "Выбор RX пока не опубликован общим контрактом управления", UiLocale.EN: "RX selection is not yet published by the shared control contract"}),
        "analyzer.rx.unknown": MappingProxyType({UiLocale.RU: "RX —", UiLocale.EN: "RX —"}),
        "analyzer.progress": MappingProxyType({UiLocale.RU: "Сегменты {received}/{total} · ревизия {revision}", UiLocale.EN: "Segments {received}/{total} · revision {revision}"}),
        "analyzer.provenance": MappingProxyType({UiLocale.RU: "Источник {source} · RX {rx} · epoch {epoch} · {unit}", UiLocale.EN: "Source {source} · RX {rx} · epoch {epoch} · {unit}"}),
        "component.empty_chart.default.detail": MappingProxyType(
            {
                UiLocale.RU: "Спектральные данные появятся после запуска приёма.",
                UiLocale.EN: "Spectrum data appears after reception starts.",
            }
        ),
        "component.empty_chart.default.primary": MappingProxyType(
            {UiLocale.RU: "Найти устройства", UiLocale.EN: "Find devices"}
        ),
        "component.empty_chart.default.secondary": MappingProxyType(
            {UiLocale.RU: "Указать URI", UiLocale.EN: "Enter URI"}
        ),
        "component.empty_chart.default.title": MappingProxyType(
            {UiLocale.RU: "Подключите или выберите устройство", UiLocale.EN: "Connect or select a device"}
        ),
        "component.heat_legend.description": MappingProxyType(
            {UiLocale.RU: "От {minimum} до {maximum}", UiLocale.EN: "From {minimum} to {maximum}"}
        ),
        "component.heat_legend.name": MappingProxyType(
            {UiLocale.RU: "Шкала интенсивности", UiLocale.EN: "Intensity scale"}
        ),
        "component.splitter.handle.name": MappingProxyType(
            {UiLocale.RU: "Разделитель областей", UiLocale.EN: "Pane splitter"}
        ),
        "component.splitter.spectrum_waterfall.description": MappingProxyType(
            {
                UiLocale.RU: "Изменяет высоту областей спектра и водопада",
                UiLocale.EN: "Changes the height of the spectrum and waterfall panes",
            }
        ),
        "component.splitter.spectrum_waterfall.name": MappingProxyType(
            {UiLocale.RU: "Разделитель спектра и водопада", UiLocale.EN: "Spectrum and waterfall splitter"}
        ),
        "calibration.accessible.name": MappingProxyType(
            {UiLocale.RU: "Калибровочные профили UI V2", UiLocale.EN: "UI V2 calibration profiles"}
        ),
        "calibration.applicability.accessible": MappingProxyType(
            {UiLocale.RU: "Матрица применимости профиля", UiLocale.EN: "Profile applicability matrix"}
        ),
        "calibration.applicability.boundary": MappingProxyType(
            {
                UiLocale.RU: "Температура / прошивка / статус dBm приёма не опубликованы текущим контрактом. Профиль не помечается активным; импорт/завершение/переопределение недоступны.",
                UiLocale.EN: "Temperature / firmware / Live dBm status is not published by the current contract. The profile is not marked active; import/finalize/override are unavailable.",
            }
        ),
        "calibration.applicability.headers": MappingProxyType(
            {UiLocale.RU: "Поле|Профиль|Текущее|Статус", UiLocale.EN: "Field|Profile|Current|Status"}
        ),
        "calibration.card.applicability": MappingProxyType(
            {UiLocale.RU: "Применимость и происхождение данных", UiLocale.EN: "Applicability and provenance"}
        ),
        "calibration.card.correction": MappingProxyType(
            {UiLocale.RU: "Коррекция / неопределённость", UiLocale.EN: "Correction / uncertainty"}
        ),
        "calibration.card.profiles": MappingProxyType(
            {UiLocale.RU: "Профили", UiLocale.EN: "Profiles"}
        ),
        "calibration.error.title": MappingProxyType(
            {UiLocale.RU: "Ошибка калибровки", UiLocale.EN: "Calibration error"}
        ),
        "calibration.header.detail": MappingProxyType(
            {UiLocale.RU: "Только неизменяемый профиль и применимость. Активация не допущена текущим контрактом.", UiLocale.EN: "Immutable profile/applicability only. Activation is not admitted by the current contract."}
        ),
        "calibration.header.title": MappingProxyType(
            {UiLocale.RU: "Калибровочные профили", UiLocale.EN: "Calibration profiles"}
        ),
        "calibration.inspector.boundary": MappingProxyType(
            {UiLocale.RU: "Активный профиль и статус dBm не публикуются; интерфейс их не выводит.", UiLocale.EN: "Active profile and dBm status are not published; the UI does not display them."}
        ),
        "calibration.inspector.context": MappingProxyType(
            {UiLocale.RU: "Контекст", UiLocale.EN: "Context"}
        ),
        "calibration.inspector.detail": MappingProxyType(
            {UiLocale.RU: "Калибровочные профили — только чтение", UiLocale.EN: "Calibration profiles read-only"}
        ),
        "calibration.inspector.not_loaded": MappingProxyType(
            {UiLocale.RU: "Профили не загружены", UiLocale.EN: "Profiles not loaded"}
        ),
        "calibration.inspector.summary": MappingProxyType(
            {
                UiLocale.RU: "Профилей: {profiles}\nВыбран: {selected}\nЗанято: {busy}",
                UiLocale.EN: "Profiles: {profiles}\nSelected: {selected}\nBusy: {busy}",
            }
        ),
        "calibration.no": MappingProxyType({UiLocale.RU: "нет", UiLocale.EN: "no"}),
        "calibration.not_published": MappingProxyType(
            {UiLocale.RU: "не опубликовано", UiLocale.EN: "not published"}
        ),
        "calibration.plot.accessible.description": MappingProxyType(
            {UiLocale.RU: "Публикуются только неизменяемые точки коррекции и неопределённости.", UiLocale.EN: "Only immutable correction and uncertainty points are published."}
        ),
        "calibration.plot.accessible.name": MappingProxyType(
            {UiLocale.RU: "Коррекция и неопределённость профиля", UiLocale.EN: "Profile correction and uncertainty"}
        ),
        "calibration.plot.empty": MappingProxyType(
            {UiLocale.RU: "Выберите опубликованный профиль", UiLocale.EN: "Select a published profile"}
        ),
        "calibration.plot.db": MappingProxyType({UiLocale.RU: "dB", UiLocale.EN: "dB"}),
        "calibration.plot.frequency": MappingProxyType(
            {UiLocale.RU: "частота", UiLocale.EN: "frequency"}
        ),
        "calibration.plot.legend": MappingProxyType(
            {UiLocale.RU: "синий: коррекция; серый: ± неопределённость", UiLocale.EN: "blue: correction; gray: ± uncertainty"}
        ),
        "calibration.profile.detail": MappingProxyType(
            {
                UiLocale.RU: "{profile_id} · v{version} · {points} точек\n{start:.3f}–{stop:.3f} МГц · опорная плоскость: {plane}\nОборудование: {equipment} · Создан: {created}\nОтпечаток: {fingerprint}…",
                UiLocale.EN: "{profile_id} · v{version} · {points} points\n{start:.3f}–{stop:.3f} MHz · reference plane: {plane}\nEquipment: {equipment} · Created: {created}\nFingerprint: {fingerprint}…",
            }
        ),
        "calibration.profile.select": MappingProxyType(
            {UiLocale.RU: "Выберите профиль после явного обновления.", UiLocale.EN: "Select a profile after explicit refresh."}
        ),
        "calibration.profiles.accessible": MappingProxyType(
            {UiLocale.RU: "Опубликованные калибровочные профили", UiLocale.EN: "Published calibration profiles"}
        ),
        "calibration.refresh": MappingProxyType(
            {UiLocale.RU: "Обновить профили", UiLocale.EN: "Refresh profiles"}
        ),
        "calibration.state.busy": MappingProxyType(
            {UiLocale.RU: "Выполняется только операция слоя представления", UiLocale.EN: "Only a presenter operation is running"}
        ),
        "calibration.state.select": MappingProxyType(
            {UiLocale.RU: "Выберите профиль", UiLocale.EN: "Select a profile"}
        ),
        "calibration.state.selected": MappingProxyType(
            {UiLocale.RU: "Профиль выбран; активный статус/dBm не подтверждены", UiLocale.EN: "Profile selected; active/dBm are unverified"}
        ),
        "calibration.status.mismatch": MappingProxyType(
            {UiLocale.RU: "Несовпадение", UiLocale.EN: "Mismatch"}
        ),
        "calibration.workspace.description": MappingProxyType(
            {UiLocale.RU: "Профили и применимость только для чтения, без непубликованных утверждений", UiLocale.EN: "Read-only profiles and applicability without unpublished claims"}
        ),
        "calibration.workspace.label": MappingProxyType(
            {UiLocale.RU: "Калибровка", UiLocale.EN: "Calibration"}
        ),
        "calibration.yes": MappingProxyType({UiLocale.RU: "да", UiLocale.EN: "yes"}),
        "diagnostics.accessible.description": MappingProxyType(
            {
                UiLocale.RU: "Снимок состояния и автономные проверки только после явной команды. RX-тест недоступен.",
                UiLocale.EN: "Snapshot and offline self-tests run only after an explicit command. RX testing is unavailable.",
            }
        ),
        "diagnostics.accessible.name": MappingProxyType(
            {UiLocale.RU: "Диагностика UI V2", UiLocale.EN: "UI V2 diagnostics"}
        ),
        "diagnostics.bundle.create": MappingProxyType(
            {UiLocale.RU: "Создать пакет поддержки", UiLocale.EN: "Create support bundle"}
        ),
        "diagnostics.bundle.create.name": MappingProxyType(
            {UiLocale.RU: "Создать обезличенный пакет поддержки", UiLocale.EN: "Create privacy-redacted support bundle"}
        ),
        "diagnostics.bundle.directory": MappingProxyType(
            {UiLocale.RU: "Каталог пакета поддержки:", UiLocale.EN: "Support bundle directory:"}
        ),
        "diagnostics.bundle.directory.name": MappingProxyType(
            {UiLocale.RU: "Каталог для пакета поддержки", UiLocale.EN: "Support bundle directory"}
        ),
        "diagnostics.bundle.not_started": MappingProxyType(
            {UiLocale.RU: "Экспорт не запускался.", UiLocale.EN: "Export has not started."}
        ),
        "diagnostics.bundle.placeholder": MappingProxyType(
            {
                UiLocale.RU: "Выберите или введите каталог перед явным экспортом",
                UiLocale.EN: "Select or enter a directory before explicit export",
            }
        ),
        "diagnostics.bundle.summary": MappingProxyType(
            {
                UiLocale.RU: "Пакет: {path} · файлов/секций: {files} · обезличено: {redacted}",
                UiLocale.EN: "Bundle: {path} · files/sections: {files} · redacted: {redacted}",
            }
        ),
        "diagnostics.cancel": MappingProxyType(
            {UiLocale.RU: "Отменить", UiLocale.EN: "Cancel"}
        ),
        "diagnostics.cancel.name": MappingProxyType(
            {UiLocale.RU: "Отменить диагностику", UiLocale.EN: "Cancel diagnostics"}
        ),
        "diagnostics.card.errors": MappingProxyType(
            {UiLocale.RU: "Ошибки", UiLocale.EN: "Errors"}
        ),
        "diagnostics.card.metrics": MappingProxyType(
            {UiLocale.RU: "Метрики", UiLocale.EN: "Metrics"}
        ),
        "diagnostics.card.self_tests": MappingProxyType(
            {UiLocale.RU: "Автономные проверки", UiLocale.EN: "Offline self-tests"}
        ),
        "diagnostics.card.summary": MappingProxyType(
            {UiLocale.RU: "Сводка", UiLocale.EN: "Summary"}
        ),
        "diagnostics.error.title": MappingProxyType(
            {UiLocale.RU: "Ошибка диагностики", UiLocale.EN: "Diagnostics error"}
        ),
        "diagnostics.header.detail": MappingProxyType(
            {
                UiLocale.RU: "Снимок состояния и автономные проверки запускаются только явной командой. Управление или тест RX не опубликованы.",
                UiLocale.EN: "Snapshot and offline self-tests run only by explicit command. RX control and testing are not published.",
            }
        ),
        "diagnostics.header.title": MappingProxyType(
            {UiLocale.RU: "Диагностика", UiLocale.EN: "Diagnostics"}
        ),
        "diagnostics.inspector.boundary": MappingProxyType(
            {UiLocale.RU: "RX-тест, запись и необработанные I/Q не входят в UI2-10A.", UiLocale.EN: "RX testing, recording, and raw I/Q are outside UI2-10A."}
        ),
        "diagnostics.inspector.context": MappingProxyType(
            {UiLocale.RU: "Контекст", UiLocale.EN: "Context"}
        ),
        "diagnostics.inspector.detail": MappingProxyType(
            {UiLocale.RU: "Диагностика только по явному запросу", UiLocale.EN: "Diagnostics only by explicit request"}
        ),
        "diagnostics.inspector.not_loaded": MappingProxyType(
            {UiLocale.RU: "Снимок состояния не загружен; навигация не создаёт слой представления.", UiLocale.EN: "Snapshot is not loaded; navigation does not create a presenter."}
        ),
        "diagnostics.inspector.summary": MappingProxyType(
            {
                UiLocale.RU: "Карточек: {cards}\nОшибок: {errors}\nАвтопроверок: {tests}\nЗанято: {busy}",
                UiLocale.EN: "Cards: {cards}\nErrors: {errors}\nOffline tests: {tests}\nBusy: {busy}",
            }
        ),
        "diagnostics.load": MappingProxyType(
            {UiLocale.RU: "Загрузить диагностику", UiLocale.EN: "Load diagnostics"}
        ),
        "diagnostics.load.name": MappingProxyType(
            {UiLocale.RU: "Загрузить снимок диагностики", UiLocale.EN: "Load diagnostics snapshot"}
        ),
        "diagnostics.metrics.not_loaded": MappingProxyType(
            {UiLocale.RU: "Метрики: снимок диагностики ещё не загружен.", UiLocale.EN: "Metrics: diagnostics snapshot is not loaded yet."}
        ),
        "diagnostics.metrics.unpublished": MappingProxyType(
            {UiLocale.RU: "Метрики не опубликованы текущим снимком диагностики.", UiLocale.EN: "Metrics are not published by the current diagnostics snapshot."}
        ),
        "diagnostics.refresh": MappingProxyType(
            {UiLocale.RU: "Обновить диагностику", UiLocale.EN: "Refresh diagnostics"}
        ),
        "diagnostics.self_test": MappingProxyType(
            {UiLocale.RU: "Запустить автономные проверки", UiLocale.EN: "Run offline self-tests"}
        ),
        "diagnostics.self_test.name": MappingProxyType(
            {UiLocale.RU: "Запустить автономные проверки", UiLocale.EN: "Run offline self-tests"}
        ),
        "diagnostics.state.busy": MappingProxyType(
            {UiLocale.RU: "Выполняется операция слоя представления", UiLocale.EN: "Presenter operation is running"}
        ),
        "diagnostics.state.loaded": MappingProxyType(
            {UiLocale.RU: "Снимок загружен; RX-тест недоступен", UiLocale.EN: "Snapshot loaded; RX testing unavailable"}
        ),
        "diagnostics.state.not_loaded": MappingProxyType(
            {UiLocale.RU: "Диагностика не загружена", UiLocale.EN: "Diagnostics not loaded"}
        ),
        "diagnostics.status.cancelled": MappingProxyType(
            {UiLocale.RU: "Отменён", UiLocale.EN: "Cancelled"}
        ),
        "diagnostics.status.fail": MappingProxyType(
            {UiLocale.RU: "Ошибка", UiLocale.EN: "Failed"}
        ),
        "diagnostics.status.pass": MappingProxyType(
            {UiLocale.RU: "Пройден", UiLocale.EN: "Passed"}
        ),
        "diagnostics.status.unavailable": MappingProxyType(
            {UiLocale.RU: "Недоступен", UiLocale.EN: "Unavailable"}
        ),
        "diagnostics.status.warn": MappingProxyType(
            {UiLocale.RU: "Предупреждение", UiLocale.EN: "Warning"}
        ),
        "diagnostics.table.cards.name": MappingProxyType(
            {UiLocale.RU: "Карточки снимка диагностики", UiLocale.EN: "Diagnostics snapshot cards"}
        ),
        "diagnostics.table.errors.name": MappingProxyType(
            {UiLocale.RU: "Опубликованные ошибки диагностики", UiLocale.EN: "Published diagnostics errors"}
        ),
        "diagnostics.table.headers.cards": MappingProxyType(
            {UiLocale.RU: "Компонент|Статус|Версия|Последний тест|Описание", UiLocale.EN: "Component|Status|Version|Last test|Description"}
        ),
        "diagnostics.table.headers.errors": MappingProxyType(
            {UiLocale.RU: "Ошибка|Причина|Рекомендация|Источник", UiLocale.EN: "Error|Reason|Recommendation|Source"}
        ),
        "diagnostics.table.headers.tests": MappingProxyType(
            {UiLocale.RU: "Тест|Статус|Длительность|Описание", UiLocale.EN: "Test|Status|Duration|Description"}
        ),
        "diagnostics.table.tests.name": MappingProxyType(
            {UiLocale.RU: "Результаты автономных проверок", UiLocale.EN: "Offline self-test results"}
        ),
        "diagnostics.workspace.description": MappingProxyType(
            {UiLocale.RU: "Отложенный снимок и автономные проверки через существующий слой представления", UiLocale.EN: "Deferred snapshot and offline self-tests through the existing presenter"}
        ),
        "diagnostics.workspace.label": MappingProxyType(
            {UiLocale.RU: "Диагностика", UiLocale.EN: "Diagnostics"}
        ),
        "diagnostics.yes": MappingProxyType({UiLocale.RU: "да", UiLocale.EN: "yes"}),
        "diagnostics.no": MappingProxyType({UiLocale.RU: "нет", UiLocale.EN: "no"}),
        "frequency.gigahertz": MappingProxyType(
            {UiLocale.RU: "{value:.3f} ГГц", UiLocale.EN: "{value:.3f} GHz"}
        ),
        "frequency.gigahertz.precise": MappingProxyType({UiLocale.RU: "{value} ГГц", UiLocale.EN: "{value} GHz"}),
        "frequency.megahertz.precise": MappingProxyType({UiLocale.RU: "{value} МГц", UiLocale.EN: "{value} MHz"}),
        "frequency.kilohertz.precise": MappingProxyType({UiLocale.RU: "{value} кГц", UiLocale.EN: "{value} kHz"}),
        "frequency.hertz": MappingProxyType(
            {UiLocale.RU: "{value:.0f} Гц", UiLocale.EN: "{value:.0f} Hz"}
        ),
        "frequency.kilohertz": MappingProxyType(
            {UiLocale.RU: "{value:.3f} кГц", UiLocale.EN: "{value:.3f} kHz"}
        ),
        "frequency.megahertz": MappingProxyType(
            {UiLocale.RU: "{value:.3f} МГц", UiLocale.EN: "{value:.3f} MHz"}
        ),
        "gallery.accessible.name": MappingProxyType({UiLocale.RU: "Галерея компонентов UI V2", UiLocale.EN: "UI V2 component gallery"}),
        "gallery.title": MappingProxyType({UiLocale.RU: "UI V2 · система компонентов", UiLocale.EN: "UI V2 · component system"}),
        "gallery.status.title": MappingProxyType({UiLocale.RU: "Статусы", UiLocale.EN: "Statuses"}),
        "gallery.status.detail": MappingProxyType({UiLocale.RU: "Текст, значок и тон сообщают одно состояние", UiLocale.EN: "Text, icon, and tone communicate one state"}),
        "gallery.status.cpu": MappingProxyType({UiLocale.RU: "Активная обработка", UiLocale.EN: "Active backend"}),
        "gallery.status.calibration_label": MappingProxyType({UiLocale.RU: "КАЛ ОК", UiLocale.EN: "CAL OK"}),
        "gallery.status.loss_label": MappingProxyType({UiLocale.RU: "ПОТЕРИ 4", UiLocale.EN: "LOSS 4"}),
        "gallery.status.error_label": MappingProxyType({UiLocale.RU: "ОШИБКА", UiLocale.EN: "ERROR"}),
        "gallery.status.calibration": MappingProxyType({UiLocale.RU: "Калибровка применена", UiLocale.EN: "Calibration applied"}),
        "gallery.status.loss": MappingProxyType({UiLocale.RU: "Есть потери данных", UiLocale.EN: "Data loss is present"}),
        "gallery.status.rx_unavailable": MappingProxyType({UiLocale.RU: "Приём недоступен", UiLocale.EN: "Reception unavailable"}),
        "gallery.actions.title": MappingProxyType({UiLocale.RU: "Команды", UiLocale.EN: "Commands"}),
        "gallery.actions.detail": MappingProxyType({UiLocale.RU: "Фокус и занятое состояние не блокируют всё окно", UiLocale.EN: "Focus and busy state do not block the whole window"}),
        "gallery.actions.start": MappingProxyType({UiLocale.RU: "Запустить", UiLocale.EN: "Start"}),
        "gallery.actions.start.name": MappingProxyType({UiLocale.RU: "Запустить приём", UiLocale.EN: "Start reception"}),
        "gallery.actions.apply": MappingProxyType({UiLocale.RU: "Применить", UiLocale.EN: "Apply"}),
        "gallery.actions.apply.name": MappingProxyType({UiLocale.RU: "Применить настройки", UiLocale.EN: "Apply settings"}),
        "gallery.actions.applying": MappingProxyType({UiLocale.RU: "Применение…", UiLocale.EN: "Applying…"}),
        "gallery.actions.unavailable": MappingProxyType({UiLocale.RU: "Недоступно", UiLocale.EN: "Unavailable"}),
        "gallery.actions.unavailable.name": MappingProxyType({UiLocale.RU: "Недоступная команда", UiLocale.EN: "Unavailable command"}),
        "gallery.field.center": MappingProxyType({UiLocale.RU: "Центр", UiLocale.EN: "Center"}),
        "gallery.field.frequency": MappingProxyType({UiLocale.RU: "Частота, МГц", UiLocale.EN: "Frequency, MHz"}),
        "gallery.measurements.title": MappingProxyType({UiLocale.RU: "Измерения", UiLocale.EN: "Measurements"}),
        "gallery.measurements.detail": MappingProxyType({UiLocale.RU: "Значения и единицы не преобразуются визуальным слоем", UiLocale.EN: "Values and units are not converted by the visual layer"}),
        "gallery.measurements.peak": MappingProxyType({UiLocale.RU: "Пик", UiLocale.EN: "Peak"}),
        "gallery.measurements.frame": MappingProxyType({UiLocale.RU: "Кадр", UiLocale.EN: "Frame"}),
        "gallery.measurements.age_value": MappingProxyType({UiLocale.RU: "12 мс", UiLocale.EN: "12 ms"}),
        "gallery.measurements.unit": MappingProxyType({UiLocale.RU: "Единица кадра", UiLocale.EN: "Frame unit"}),
        "gallery.measurements.age": MappingProxyType({UiLocale.RU: "Возраст последнего кадра", UiLocale.EN: "Latest frame age"}),
        "gallery.measurements.spectrum_pane": MappingProxyType({UiLocale.RU: "Область спектра", UiLocale.EN: "Spectrum pane"}),
        "gallery.measurements.waterfall_pane": MappingProxyType({UiLocale.RU: "Область водопада", UiLocale.EN: "Waterfall pane"}),
        "gallery.interaction.title": MappingProxyType({UiLocale.RU: "Взаимодействие", UiLocale.EN: "Interaction"}),
        "gallery.interaction.detail": MappingProxyType({UiLocale.RU: "Статические примеры наведения, фокуса и нажатия; рабочие кнопки используют стили состояний", UiLocale.EN: "Static hover, focus, and press examples; working buttons use QSS states"}),
        "gallery.interaction.normal": MappingProxyType({UiLocale.RU: "Обычное", UiLocale.EN: "Normal"}),
        "gallery.interaction.hover": MappingProxyType({UiLocale.RU: "Наведение", UiLocale.EN: "Hover"}),
        "gallery.interaction.focus": MappingProxyType({UiLocale.RU: "Фокус", UiLocale.EN: "Focus"}),
        "gallery.interaction.pressed": MappingProxyType({UiLocale.RU: "Нажато", UiLocale.EN: "Pressed"}),
        "gallery.interaction.preview": MappingProxyType({UiLocale.RU: "Пример состояния {label}", UiLocale.EN: "State example: {label}"}),
        "gallery.feedback.title": MappingProxyType({UiLocale.RU: "Состояния", UiLocale.EN: "States"}),
        "gallery.feedback.detail": MappingProxyType({UiLocale.RU: "Пустое состояние и ошибка остаются понятными без цвета", UiLocale.EN: "Empty state and error remain understandable without colour"}),
        "gallery.feedback.error.title": MappingProxyType({UiLocale.RU: "Не удалось запустить приём", UiLocale.EN: "Could not start reception"}),
        "gallery.feedback.error.detail": MappingProxyType({UiLocale.RU: "Проверьте устройство и настройки. Повтор не будет выполнен скрытно.", UiLocale.EN: "Check the device and settings. Retry will not occur implicitly."}),
        "gallery.feedback.error.action": MappingProxyType({UiLocale.RU: "Открыть диагностику", UiLocale.EN: "Open diagnostics"}),
        "gallery.feedback.popover.open": MappingProxyType({UiLocale.RU: "Открыть пример панели", UiLocale.EN: "Open panel example"}),
        "gallery.feedback.popover.open_name": MappingProxyType({UiLocale.RU: "Открыть пример контекстной панели", UiLocale.EN: "Open contextual panel example"}),
        "gallery.feedback.popover.title": MappingProxyType({UiLocale.RU: "Персистенция", UiLocale.EN: "Persistence"}),
        "gallery.feedback.popover.detail": MappingProxyType({UiLocale.RU: "Настройки отображения доступны в следующих пакетах.", UiLocale.EN: "Display settings are available in subsequent packages."}),
        "gallery.navigation.title": MappingProxyType({UiLocale.RU: "Навигация", UiLocale.EN: "Navigation"}),
        "gallery.navigation.detail": MappingProxyType({UiLocale.RU: "Иконки дополняют подпись, а не заменяют её", UiLocale.EN: "Icons supplement labels; they do not replace them"}),
        "gallery.navigation.live": MappingProxyType({UiLocale.RU: "Приём", UiLocale.EN: "Receive"}),
        "gallery.navigation.live.description": MappingProxyType({UiLocale.RU: "Открыть приёмник", UiLocale.EN: "Open receiver"}),
        "gallery.navigation.sweep": MappingProxyType({UiLocale.RU: "Обзор", UiLocale.EN: "Sweep"}),
        "gallery.navigation.sweep.description": MappingProxyType({UiLocale.RU: "Открыть обзор диапазона", UiLocale.EN: "Open band sweep"}),
        "live.accessible.description": MappingProxyType(
            {
                UiLocale.RU: "Рабочая область приёма с приоритетом графика. Устройством и приёмом управляет только существующий слой представления.",
                UiLocale.EN: "Graph-first Live workspace. Device and reception are controlled only by the existing presenter.",
            }
        ),
        "live.accessible.name": MappingProxyType(
            {UiLocale.RU: "Приём UI V2", UiLocale.EN: "UI V2 reception"}
        ),
        "live.apply": MappingProxyType({UiLocale.RU: "Применить", UiLocale.EN: "Apply"}),
        "live.apply.name": MappingProxyType(
            {
                UiLocale.RU: "Применить только опубликованную конфигурацию приёма",
                UiLocale.EN: "Apply only the published LiveConfiguration",
            }
        ),
        "live.backend.name": MappingProxyType(
            {UiLocale.RU: "Политика обработки", UiLocale.EN: "Backend policy"}
        ),
        "live.cancel_changes": MappingProxyType(
            {UiLocale.RU: "Отменить правки", UiLocale.EN: "Cancel changes"}
        ),
        "live.cancel_changes.name": MappingProxyType(
            {
                UiLocale.RU: "Вернуть только локальные поля к последней применённой конфигурации",
                UiLocale.EN: "Restore only local fields to the last applied configuration",
            }
        ),
        "live.center_frequency.name": MappingProxyType(
            {UiLocale.RU: "Центральная частота в МГц", UiLocale.EN: "Centre frequency in MHz"}
        ),
        "live.center_frequency.suffix": MappingProxyType(
            {UiLocale.RU: " МГц", UiLocale.EN: " MHz"}
        ),
        "live.configuration.applied": MappingProxyType(
            {
                UiLocale.RU: "Применено устройством · профиль: {profile}",
                UiLocale.EN: "Applied by device · profile: {profile}",
            }
        ),
        "live.configuration.local_changes": MappingProxyType(
            {
                UiLocale.RU: "Локальные правки не применены к устройству.",
                UiLocale.EN: "Local changes have not been applied to the device.",
            }
        ),
        "live.configuration.no_applied": MappingProxyType(
            {
                UiLocale.RU: "Нет применённой конфигурации: доступны только опубликованные поля.",
                UiLocale.EN: "No applied configuration: only published fields are available.",
            }
        ),
        "live.configuration.requested": MappingProxyType(
            {
                UiLocale.RU: "Применение запрошено; ожидается подтверждённый снимок состояния.",
                UiLocale.EN: "Apply requested; awaiting a confirmed snapshot.",
            }
        ),
        "analyzer.settings.select_source": MappingProxyType(
            {
                UiLocale.RU: "Сначала выберите приёмник в списке источников. Затем можно применить настройки.",
                UiLocale.EN: "Select a receiver from the source list before applying settings.",
            }
        ),
        "live.configuration.identity_missing": MappingProxyType(
            {
                UiLocale.RU: "Применение недоступно: снимок не содержит идентичность сессии, источника или поколения.",
                UiLocale.EN: "Apply unavailable: snapshot lacks session, source or generation identity.",
            }
        ),
        "live.configuration.conflict": MappingProxyType(
            {
                UiLocale.RU: "Конфигурация изменилась. Отмените черновик, чтобы загрузить актуальные настройки.",
                UiLocale.EN: "Configuration changed. Cancel the draft to load current settings.",
            }
        ),
        "live.configuration.status.name": MappingProxyType(
            {UiLocale.RU: "Статус применённой конфигурации", UiLocale.EN: "Applied configuration status"}
        ),
        "live.contract_note": MappingProxyType(
            {
                UiLocale.RU: "Расширенные FFT, аналоговая полоса, персистенция DSP и запись требуют ещё не опубликованного контракта обработки.",
                UiLocale.EN: "Extended FFT, analogue bandwidth, DSP persistence, and recording require an unpublished backend contract.",
            }
        ),
        "live.contract_note.name": MappingProxyType(
            {UiLocale.RU: "Граница текущего контракта приёма", UiLocale.EN: "Current Live contract boundary"}
        ),
        "live.device.unselected": MappingProxyType(
            {UiLocale.RU: "Устройство не выбрано", UiLocale.EN: "Device not selected"}
        ),
        "live.device_selector.name": MappingProxyType(
            {UiLocale.RU: "Выбрать обнаруженное устройство", UiLocale.EN: "Select discovered device"}
        ),
        "live.discover": MappingProxyType({UiLocale.RU: "Найти", UiLocale.EN: "Discover"}),
        "live.discover.name": MappingProxyType(
            {UiLocale.RU: "Найти устройства через существующий слой представления", UiLocale.EN: "Discover devices through the existing presenter"}
        ),
        "live.error.category.unpublished": MappingProxyType(
            {UiLocale.RU: "Категория не опубликована", UiLocale.EN: "Category not published"}
        ),
        "live.error.category.value": MappingProxyType(
            {UiLocale.RU: "Категория: {category}", UiLocale.EN: "Category: {category}"}
        ),
        "live.error.title": MappingProxyType(
            {UiLocale.RU: "Ошибка приёма", UiLocale.EN: "Live error"}
        ),
        "live.gain.name": MappingProxyType(
            {UiLocale.RU: "Усиление в dB", UiLocale.EN: "Gain in dB"}
        ),
        "live.gain.suffix": MappingProxyType({UiLocale.RU: " dB", UiLocale.EN: " dB"}),
        "live.inspector.applied.detail": MappingProxyType(
            {
                UiLocale.RU: "{center:.3f} МГц · {sample_rate:.3f} MS/s\n{gain:.3f} dB · {backend} · профиль: {profile}",
                UiLocale.EN: "{center:.3f} MHz · {sample_rate:.3f} MS/s\n{gain:.3f} dB · {backend} · profile: {profile}",
            }
        ),
        "live.inspector.applied.label": MappingProxyType(
            {UiLocale.RU: "Применено", UiLocale.EN: "Applied"}
        ),
        "live.inspector.applied.name": MappingProxyType(
            {UiLocale.RU: "Применённая конфигурация", UiLocale.EN: "Applied configuration"}
        ),
        "live.inspector.boundary": MappingProxyType(
            {
                UiLocale.RU: "Панель сведений показывает только опубликованные неизменяемые данные; непубликованные параметры не подменяются значениями интерфейса.",
                UiLocale.EN: "The inspector shows only published immutable data; unpublished parameters are not substituted with interface values.",
            }
        ),
        "live.inspector.boundary.name": MappingProxyType(
            {UiLocale.RU: "Граница панели сведений приёма", UiLocale.EN: "Live inspector boundary"}
        ),
        "live.inspector.frames": MappingProxyType(
            {
                UiLocale.RU: "Спектр: {spectrum}\nПерсистенция: {persistence}\nВодопад: {waterfall}",
                UiLocale.EN: "Spectrum: {spectrum}\nPersistence: {persistence}\nWaterfall: {waterfall}",
            }
        ),
        "live.inspector.frames.label": MappingProxyType(
            {UiLocale.RU: "Кадры", UiLocale.EN: "Frames"}
        ),
        "live.inspector.frames.name": MappingProxyType(
            {UiLocale.RU: "Публикация кадров", UiLocale.EN: "Frame publication"}
        ),
        "live.inspector.heading": MappingProxyType(
            {UiLocale.RU: "Приём: опубликованный контракт", UiLocale.EN: "Live: published contract"}
        ),
        "live.inspector.losses": MappingProxyType(
            {
                UiLocale.RU: "Источник: {source}; очередь: {queue}; FFT: {fft}; публикация: {publication}; связь: {bridge}",
                UiLocale.EN: "Source: {source}; queue: {queue}; FFT: {fft}; publication: {publication}; bridge: {bridge}",
            }
        ),
        "live.inspector.losses.label": MappingProxyType(
            {UiLocale.RU: "Потери", UiLocale.EN: "Losses"}
        ),
        "live.inspector.losses.name": MappingProxyType(
            {UiLocale.RU: "Потери", UiLocale.EN: "Losses"}
        ),
        "live.inspector.no_data": MappingProxyType(
            {UiLocale.RU: "Нет данных", UiLocale.EN: "No data"}
        ),
        "live.inspector.no_frames": MappingProxyType(
            {UiLocale.RU: "Нет опубликованных кадров", UiLocale.EN: "No published frames"}
        ),
        "live.inspector.not_applied": MappingProxyType(
            {UiLocale.RU: "Не применено", UiLocale.EN: "Not applied"}
        ),
        "live.inspector.not_published": MappingProxyType(
            {UiLocale.RU: "не опубликован", UiLocale.EN: "not published"}
        ),
        "live.inspector.published": MappingProxyType(
            {UiLocale.RU: "опубликован", UiLocale.EN: "published"}
        ),
        "live.inspector.quality": MappingProxyType(
            {
                UiLocale.RU: "{calibration}\n{backend}\nЕдиница: {unit}",
                UiLocale.EN: "{calibration}\n{backend}\nUnit: {unit}",
            }
        ),
        "live.inspector.quality.label": MappingProxyType(
            {UiLocale.RU: "Качество", UiLocale.EN: "Quality"}
        ),
        "live.inspector.quality.name": MappingProxyType(
            {UiLocale.RU: "Качество и обработка", UiLocale.EN: "Quality and backend"}
        ),
        "live.inspector.state.name": MappingProxyType(
            {UiLocale.RU: "Состояние приёма", UiLocale.EN: "Live state"}
        ),
        "live.inspector.summary": MappingProxyType(
            {
                UiLocale.RU: "{connection}\n{acquisition}\nУстройство: {device}",
                UiLocale.EN: "{connection}\n{acquisition}\nDevice: {device}",
            }
        ),
        "live.inspector.waiting": MappingProxyType(
            {UiLocale.RU: "Ожидание снимка состояния", UiLocale.EN: "Waiting for snapshot"}
        ),
        "live.losses": MappingProxyType(
            {UiLocale.RU: "Потери: {count}", UiLocale.EN: "Losses: {count}"}
        ),
        "live.primary.apply_settings": MappingProxyType(
            {UiLocale.RU: "Применить настройки", UiLocale.EN: "Apply settings"}
        ),
        "live.primary.discover_device": MappingProxyType(
            {UiLocale.RU: "Найти устройство", UiLocale.EN: "Find device"}
        ),
        "live.primary.name": MappingProxyType(
            {UiLocale.RU: "Основное доступное действие слоя представления приёма", UiLocale.EN: "Primary available Live presenter action"}
        ),
        "live.profile.none": MappingProxyType({UiLocale.RU: "нет", UiLocale.EN: "none"}),
        "live.recording.unavailable": MappingProxyType(
            {UiLocale.RU: "Запись: недоступна", UiLocale.EN: "Recording: unavailable"}
        ),
        "live.recording.unavailable.name": MappingProxyType(
            {
                UiLocale.RU: "Запись требует отдельного опубликованного контракта",
                UiLocale.EN: "Recording requires a separate published contract",
            }
        ),
        "live.sample_rate.name": MappingProxyType(
            {UiLocale.RU: "Частота дискретизации в МГц", UiLocale.EN: "Sample rate in MHz"}
        ),
        "live.sample_rate.suffix": MappingProxyType(
            {UiLocale.RU: " MS/s", UiLocale.EN: " MS/s"}
        ),
        "live.uri.error.empty": MappingProxyType(
            {
                UiLocale.RU: "Укажите URI вида usb: или ip:адрес.",
                UiLocale.EN: "Enter a URI in the form usb: or ip:address.",
            }
        ),
        "live.uri.error.ip_target": MappingProxyType(
            {UiLocale.RU: "Для ip: укажите адрес или имя узла.", UiLocale.EN: "For ip:, enter an address or host name."}
        ),
        "live.uri.error.name": MappingProxyType(
            {UiLocale.RU: "Ошибка URI устройства", UiLocale.EN: "Device URI error"}
        ),
        "live.uri.error.transport": MappingProxyType(
            {
                UiLocale.RU: "Поддерживаются только явные URI usb: или ip:адрес.",
                UiLocale.EN: "Only explicit usb: or ip:address URIs are supported.",
            }
        ),
        "live.uri.error.whitespace": MappingProxyType(
            {UiLocale.RU: "URI не должен содержать пробелы.", UiLocale.EN: "URI must not contain whitespace."}
        ),
        "live.uri.label": MappingProxyType({UiLocale.RU: "URI", UiLocale.EN: "URI"}),
        "live.uri.placeholder": MappingProxyType(
            {UiLocale.RU: "usb: или ip:…", UiLocale.EN: "usb: or ip:…"}
        ),
        "live.uri.use": MappingProxyType(
            {UiLocale.RU: "Использовать URI", UiLocale.EN: "Use URI"}
        ),
        "live.uri.use.name": MappingProxyType(
            {UiLocale.RU: "Выбрать устройство по явному URI", UiLocale.EN: "Select a device by explicit URI"}
        ),
        "live.workspace.description": MappingProxyType(
            {
                UiLocale.RU: "Анализ приёма через существующий адаптер представления",
                UiLocale.EN: "Live analysis through the existing public presenter adapter",
            }
        ),
        "live.workspace.label": MappingProxyType(
            {UiLocale.RU: "Приём", UiLocale.EN: "Receive"}
        ),
        "live_state.action.discover": MappingProxyType(
            {UiLocale.RU: "Найти устройство", UiLocale.EN: "Find device"}
        ),
        "live_state.action.none": MappingProxyType(
            {UiLocale.RU: "Недоступно", UiLocale.EN: "Unavailable"}
        ),
        "live_state.action.retry": MappingProxyType(
            {UiLocale.RU: "Повторить", UiLocale.EN: "Retry"}
        ),
        "live_state.action.review_configuration": MappingProxyType(
            {UiLocale.RU: "Проверить настройки", UiLocale.EN: "Review settings"}
        ),
        "live_state.action.start": MappingProxyType(
            {UiLocale.RU: "Начать приём", UiLocale.EN: "Start reception"}
        ),
        "live_state.action.stop": MappingProxyType(
            {UiLocale.RU: "Остановить", UiLocale.EN: "Stop"}
        ),
        "live_state.acquisition.running": MappingProxyType(
            {UiLocale.RU: "Приём", UiLocale.EN: "Receiving"}
        ),
        "live_state.acquisition.starting": MappingProxyType(
            {UiLocale.RU: "Запуск приёма…", UiLocale.EN: "Starting reception…"}
        ),
        "live_state.acquisition.stopped_last_frame": MappingProxyType(
            {UiLocale.RU: "Остановлено / последний кадр", UiLocale.EN: "Stopped / last frame"}
        ),
        "live_state.acquisition.stopping": MappingProxyType(
            {UiLocale.RU: "Остановка…", UiLocale.EN: "Stopping…"}
        ),
        "live_state.acquisition.unavailable": MappingProxyType(
            {UiLocale.RU: "Приём недоступен", UiLocale.EN: "Reception unavailable"}
        ),
        "live_state.acquisition.unstarted": MappingProxyType(
            {UiLocale.RU: "Приём не запущен", UiLocale.EN: "Reception not started"}
        ),
        "live_state.age.empty": MappingProxyType(
            {UiLocale.RU: "Нет кадра", UiLocale.EN: "No frame"}
        ),
        "live_state.age.milliseconds": MappingProxyType(
            {UiLocale.RU: "{value:.0f} мс", UiLocale.EN: "{value:.0f} ms"}
        ),
        "live_state.age.seconds": MappingProxyType(
            {UiLocale.RU: "{value:.1f} с", UiLocale.EN: "{value:.1f} s"}
        ),
        "live_state.backend.unselected": MappingProxyType(
            {UiLocale.RU: "не выбран", UiLocale.EN: "unselected"}
        ),
        "live_state.backend.empty": MappingProxyType(
            {UiLocale.RU: "Обработка не выбрана", UiLocale.EN: "Backend unselected"}
        ),
        "live_state.calibration.calibrated": MappingProxyType(
            {UiLocale.RU: "Калибровано", UiLocale.EN: "Calibrated"}
        ),
        "live_state.calibration.mismatch": MappingProxyType(
            {UiLocale.RU: "КАЛ: НЕСОВПАДЕНИЕ", UiLocale.EN: "CAL MISMATCH"}
        ),
        "live_state.calibration.profile_selected": MappingProxyType(
            {UiLocale.RU: "Профиль выбран", UiLocale.EN: "Profile selected"}
        ),
        "live_state.calibration.uncalibrated": MappingProxyType(
            {UiLocale.RU: "Некалибровано", UiLocale.EN: "Uncalibrated"}
        ),
        "live_state.connection.connecting": MappingProxyType(
            {UiLocale.RU: "Подключение…", UiLocale.EN: "Connecting…"}
        ),
        "live_state.connection.error": MappingProxyType(
            {UiLocale.RU: "Ошибка", UiLocale.EN: "Error"}
        ),
        "live_state.connection.ready": MappingProxyType(
            {UiLocale.RU: "Устройство готово", UiLocale.EN: "Device ready"}
        ),
        "live_state.connection.none": MappingProxyType(
            {UiLocale.RU: "Нет устройства", UiLocale.EN: "No device"}
        ),
        "live_state.device.unselected": MappingProxyType(
            {UiLocale.RU: "Устройство не выбрано", UiLocale.EN: "Device not selected"}
        ),
        "live_state.persistence.active": MappingProxyType(
            {UiLocale.RU: "Персистенция активна", UiLocale.EN: "Persistence active"}
        ),
        "live_state.persistence.disabled": MappingProxyType(
            {UiLocale.RU: "Персистенция выключена", UiLocale.EN: "Persistence disabled"}
        ),
        "live_state.persistence.not_configured": MappingProxyType(
            {UiLocale.RU: "Персистенция не настроена", UiLocale.EN: "Persistence not configured"}
        ),
        "live_state.persistence.waiting": MappingProxyType(
            {UiLocale.RU: "Персистенция ожидает кадр", UiLocale.EN: "Persistence waiting for frame"}
        ),
        "sweep.accessible.description": MappingProxyType(
            {
                UiLocale.RU: "Развёртка с приоритетом плана через существующий слой представления без неявного запуска.",
                UiLocale.EN: "Plan-first Sweep through the existing presenter without implicit start.",
            }
        ),
        "sweep.accessible.name": MappingProxyType(
            {UiLocale.RU: "Развёртка UI V2", UiLocale.EN: "UI V2 Sweep"}
        ),
        "sweep.calibration.value": MappingProxyType(
            {UiLocale.RU: "Калибровка: {value}", UiLocale.EN: "Calibration: {value}"}
        ),
        "sweep.cancel": MappingProxyType({UiLocale.RU: "Отменить", UiLocale.EN: "Cancel"}),
        "sweep.dc_margin.label": MappingProxyType(
            {UiLocale.RU: "Запас по DC", UiLocale.EN: "DC margin"}
        ),
        "sweep.dc_margin.name": MappingProxyType(
            {UiLocale.RU: "Защитный запас по DC для развёртки, МГц", UiLocale.EN: "Sweep guard DC margin, MHz"}
        ),
        "sweep.discard_blocks.label": MappingProxyType(
            {UiLocale.RU: "Отбрасываемые блоки", UiLocale.EN: "Discard blocks"}
        ),
        "sweep.discard_blocks.name": MappingProxyType(
            {UiLocale.RU: "Число отбрасываемых блоков развёртки", UiLocale.EN: "Number of discarded Sweep blocks"}
        ),
        "sweep.discover": MappingProxyType({UiLocale.RU: "Найти", UiLocale.EN: "Discover"}),
        "sweep.dwell.label": MappingProxyType({UiLocale.RU: "Накопление", UiLocale.EN: "Dwell"}),
        "sweep.dwell.name": MappingProxyType(
            {UiLocale.RU: "Время накопления развёртки, секунды", UiLocale.EN: "Sweep dwell time, seconds"}
        ),
        "sweep.error.title": MappingProxyType(
            {UiLocale.RU: "Ошибка развёртки", UiLocale.EN: "Sweep error"}
        ),
        "sweep.export": MappingProxyType({UiLocale.RU: "Экспортировать", UiLocale.EN: "Export"}),
        "sweep.export.error.empty_path": MappingProxyType(
            {UiLocale.RU: "Укажите путь для экспорта результата.", UiLocale.EN: "Enter a path for result export."}
        ),
        "sweep.export.label": MappingProxyType({UiLocale.RU: "Экспорт", UiLocale.EN: "Export"}),
        "sweep.frequency.suffix": MappingProxyType(
            {UiLocale.RU: " МГц", UiLocale.EN: " MHz"}
        ),
        "sweep.geometry.annotation": MappingProxyType(
            {
                UiLocale.RU: "План: {segments} сегм. · полезные окна выделены синим · {start:.3f}–{stop:.3f} МГц",
                UiLocale.EN: "Plan: {segments} segments · usable windows are highlighted blue · {start:.3f}–{stop:.3f} MHz",
            }
        ),
        "sweep.geometry.description": MappingProxyType(
            {
                UiLocale.RU: "Плановые сегменты и полезное окно; это не измеренный сшитый спектр.",
                UiLocale.EN: "Planned segments and usable window; this is not a measured stitched spectrum.",
            }
        ),
        "sweep.geometry.name": MappingProxyType(
            {UiLocale.RU: "Геометрия сегментов развёртки", UiLocale.EN: "Sweep segment geometry"}
        ),
        "sweep.geometry.title": MappingProxyType(
            {UiLocale.RU: "Геометрия плана", UiLocale.EN: "Plan geometry"}
        ),
        "sweep.geometry.title.name": MappingProxyType(
            {UiLocale.RU: "Описание плановых сегментов", UiLocale.EN: "Planned segment description"}
        ),
        "sweep.header.detail": MappingProxyType(
            {
                UiLocale.RU: "Сначала план, затем явный запуск. Навигация не создаёт развёртку.",
                UiLocale.EN: "Plan first, then explicit start. Navigation does not create Sweep.",
            }
        ),
        "sweep.header.title": MappingProxyType(
            {UiLocale.RU: "Обзор частот", UiLocale.EN: "Frequency sweep"}
        ),
        "sweep.inspector.boundary": MappingProxyType(
            {
                UiLocale.RU: "Сшитые спектральные отсчёты и геометрия пропущенных отсчётов не опубликованы в зафиксированном результате развёртки; эта рабочая область V2 их не имитирует.",
                UiLocale.EN: "Stitched spectral bins and missing-bin geometry are not published by the frozen SweepResult; this V2 workspace does not imitate them.",
            }
        ),
        "sweep.inspector.boundary.name": MappingProxyType(
            {UiLocale.RU: "Граница панели сведений развёртки", UiLocale.EN: "Sweep inspector boundary"}
        ),
        "sweep.inspector.context": MappingProxyType({UiLocale.RU: "Контекст", UiLocale.EN: "Context"}),
        "sweep.inspector.detail": MappingProxyType(
            {UiLocale.RU: "Развёртка с приоритетом плана", UiLocale.EN: "Plan-first Sweep"}
        ),
        "sweep.inspector.none": MappingProxyType({UiLocale.RU: "нет", UiLocale.EN: "none"}),
        "sweep.inspector.progress": MappingProxyType(
            {UiLocale.RU: "{state}: {percent:.1f}%", UiLocale.EN: "{state}: {percent:.1f}%"}
        ),
        "sweep.inspector.segments": MappingProxyType(
            {UiLocale.RU: "{segments} сегментов", UiLocale.EN: "{segments} segments"}
        ),
        "sweep.inspector.state.name": MappingProxyType(
            {UiLocale.RU: "Состояние развёртки", UiLocale.EN: "Sweep state"}
        ),
        "sweep.inspector.summary": MappingProxyType(
            {
                UiLocale.RU: "План: {plan}\nХод: {progress}\nРезультат: {result}",
                UiLocale.EN: "Plan: {plan}\nProgress: {progress}\nResult: {result}",
            }
        ),
        "sweep.inspector.waiting": MappingProxyType(
            {UiLocale.RU: "Ожидание плана", UiLocale.EN: "Waiting for plan"}
        ),
        "sweep.mode.balanced": MappingProxyType(
            {UiLocale.RU: "Сбалансированно", UiLocale.EN: "Balanced"}
        ),
        "sweep.mode.fast": MappingProxyType({UiLocale.RU: "Быстро", UiLocale.EN: "Fast"}),
        "sweep.mode.label": MappingProxyType({UiLocale.RU: "Режим", UiLocale.EN: "Mode"}),
        "sweep.mode.name": MappingProxyType({UiLocale.RU: "Режим развёртки", UiLocale.EN: "Sweep mode"}),
        "sweep.mode.precise": MappingProxyType({UiLocale.RU: "Точно", UiLocale.EN: "Precise"}),
        "sweep.no_data": MappingProxyType({UiLocale.RU: "нет данных", UiLocale.EN: "no data"}),
        "sweep.overlap.label": MappingProxyType({UiLocale.RU: "Перекрытие", UiLocale.EN: "Overlap"}),
        "sweep.overlap.name": MappingProxyType(
            {UiLocale.RU: "Перекрытие сегментов развёртки, процент", UiLocale.EN: "Sweep segment overlap, percent"}
        ),
        "sweep.percent.suffix": MappingProxyType({UiLocale.RU: " %", UiLocale.EN: " %"}),
        "sweep.plan.calculate": MappingProxyType(
            {UiLocale.RU: "Рассчитать план", UiLocale.EN: "Calculate plan"}
        ),
        "sweep.plan.none": MappingProxyType({UiLocale.RU: "Нет плана", UiLocale.EN: "No plan"}),
        "sweep.plan.not_calculated": MappingProxyType(
            {UiLocale.RU: "План ещё не рассчитан", UiLocale.EN: "Plan is not calculated yet"}
        ),
        "sweep.plan.not_calculated.summary": MappingProxyType(
            {UiLocale.RU: "План ещё не рассчитан.", UiLocale.EN: "Plan is not calculated yet."}
        ),
        "sweep.plan.ready": MappingProxyType({UiLocale.RU: "План готов", UiLocale.EN: "Plan ready"}),
        "sweep.plan.required": MappingProxyType(
            {UiLocale.RU: "План требуется для текущей конфигурации.", UiLocale.EN: "A plan is required for the current configuration."}
        ),
        "sweep.plan.summary": MappingProxyType(
            {
                UiLocale.RU: "Сегментов: {segments} · Оценка времени: ~{estimated_seconds:.2f} с · Разрешение: {resolution_khz:.3f} кГц",
                UiLocale.EN: "Segments: {segments} · ETA: ~{estimated_seconds:.2f} s · Resolution: {resolution_khz:.3f} kHz",
            }
        ),
        "sweep.plan.summary.name": MappingProxyType(
            {UiLocale.RU: "Сводка плана", UiLocale.EN: "Plan summary"}
        ),
        "sweep.progress.format": MappingProxyType(
            {UiLocale.RU: "{stage}: {completed}/{total} · {percent:.1f}%", UiLocale.EN: "{stage}: {completed}/{total} · {percent:.1f}%"}
        ),
        "sweep.progress.name": MappingProxyType(
            {UiLocale.RU: "Ход выполнения развёртки", UiLocale.EN: "Sweep execution progress"}
        ),
        "sweep.result.none": MappingProxyType(
            {
                UiLocale.RU: "Нет результата. Зафиксированный результат развёртки не публикует сшитые спектральные отсчёты, поэтому график не подменяется макетом.",
                UiLocale.EN: "No result. The frozen SweepResult does not publish stitched spectral bins, so the graph is not replaced with a mockup.",
            }
        ),
        "sweep.result.summary": MappingProxyType(
            {
                UiLocale.RU: "{state} · {duration:.3f} с · отсутствующие сегменты: {missing_segments} · Стык p95: {seam} · покрытие калибровкой: {coverage}{note}",
                UiLocale.EN: "{state} · {duration:.3f} s · missing segments: {missing_segments} · Seam p95: {seam} · CAL coverage: {coverage}{note}",
            }
        ),
        "sweep.result.summary.name": MappingProxyType(
            {UiLocale.RU: "Сводка результата развёртки", UiLocale.EN: "Sweep result summary"}
        ),
        "sweep.result.title": MappingProxyType(
            {UiLocale.RU: "Результат и качество", UiLocale.EN: "Result and quality"}
        ),
        "sweep.result.title.name": MappingProxyType(
            {UiLocale.RU: "Качество развёртки", UiLocale.EN: "Sweep quality"}
        ),
        "sweep.run": MappingProxyType({UiLocale.RU: "Запустить развёртку", UiLocale.EN: "Run Sweep"}),
        "sweep.seam.value": MappingProxyType(
            {UiLocale.RU: "Стык: {value}", UiLocale.EN: "Seam: {value}"}
        ),
        "sweep.seconds.suffix": MappingProxyType({UiLocale.RU: " с", UiLocale.EN: " s"}),
        "sweep.settling.label": MappingProxyType({UiLocale.RU: "Стабилизация", UiLocale.EN: "Settling"}),
        "sweep.settling.name": MappingProxyType(
            {UiLocale.RU: "Время стабилизации развёртки, секунды", UiLocale.EN: "Sweep settling time, seconds"}
        ),
        "sweep.start_frequency.label": MappingProxyType({UiLocale.RU: "Начало", UiLocale.EN: "Start"}),
        "sweep.start_frequency.name": MappingProxyType(
            {UiLocale.RU: "Начальная частота развёртки, МГц", UiLocale.EN: "Sweep start frequency, MHz"}
        ),
        "sweep.stop_frequency.label": MappingProxyType({UiLocale.RU: "Конец", UiLocale.EN: "Stop"}),
        "sweep.stop_frequency.name": MappingProxyType(
            {UiLocale.RU: "Конечная частота развёртки, МГц", UiLocale.EN: "Sweep stop frequency, MHz"}
        ),
        "sweep.workspace.description": MappingProxyType(
            {
                UiLocale.RU: "Планирование и выполнение развёртки через существующий слой представления",
                UiLocale.EN: "Sweep planning and execution through the existing presenter",
            }
        ),
        "sweep.workspace.label": MappingProxyType({UiLocale.RU: "Обзор", UiLocale.EN: "Sweep"}),
        "sweep.state.cancelled": MappingProxyType(
            {UiLocale.RU: "Отменено", UiLocale.EN: "Cancelled"}
        ),
        "sweep.state.completed": MappingProxyType(
            {UiLocale.RU: "Завершено", UiLocale.EN: "Completed"}
        ),
        "sweep.state.error": MappingProxyType({UiLocale.RU: "Ошибка", UiLocale.EN: "Error"}),
        "sweep.state.idle": MappingProxyType({UiLocale.RU: "Ожидание", UiLocale.EN: "Idle"}),
        "sweep.state.planned": MappingProxyType({UiLocale.RU: "Запланировано", UiLocale.EN: "Planned"}),
        "sweep.state.running": MappingProxyType({UiLocale.RU: "Выполняется", UiLocale.EN: "Running"}),
        "tinysa_activation.candidate": MappingProxyType(
            {UiLocale.RU: "{label} — {assurance}", UiLocale.EN: "{label} — {assurance}"}
        ),
        "tinysa_activation.phase.composed": MappingProxyType(
            {UiLocale.RU: "Подготовлено", UiLocale.EN: "Composed"}
        ),
        "tinysa_activation.phase.discovered": MappingProxyType(
            {UiLocale.RU: "Обнаружено", UiLocale.EN: "Discovered"}
        ),
        "tinysa_activation.phase.faulted": MappingProxyType(
            {UiLocale.RU: "Ошибка", UiLocale.EN: "Faulted"}
        ),
        "tinysa_activation.phase.ready": MappingProxyType(
            {UiLocale.RU: "Готово", UiLocale.EN: "Ready"}
        ),
        "tinysa_activation.phase.selected": MappingProxyType(
            {UiLocale.RU: "Выбрано", UiLocale.EN: "Selected"}
        ),
        "tinysa_activation.phase.verified": MappingProxyType(
            {UiLocale.RU: "Идентификатор подтверждён", UiLocale.EN: "Identity verified"}
        ),
        "tinysa_activation.accessible.name": MappingProxyType({UiLocale.RU: "Подключение tinySA UI V2", UiLocale.EN: "UI V2 tinySA connection"}),
        "tinysa_activation.accessible.description": MappingProxyType({UiLocale.RU: "Явные действия: Найти, Выбрать, Проверить и Использовать. Навигация не перечисляет и не открывает последовательные порты.", UiLocale.EN: "Explicit Discover, Select, Verify, and Compose. Navigation does not enumerate or open serial ports."}),
        "tinysa_activation.header.title": MappingProxyType({UiLocale.RU: "Подключение tinySA", UiLocale.EN: "Connect tinySA"}),
        "tinysa_activation.header.detail": MappingProxyType({UiLocale.RU: "Последовательность обязательна: Найти → Выбрать → Проверить идентификатор → Использовать проверенный источник.", UiLocale.EN: "The sequence is mandatory: Discover → Select → Verify identity → Use verified source."}),
        "tinysa_activation.boundary": MappingProxyType({UiLocale.RU: "Открытие страницы не создаёт контроллер представления и не обращается к USB. Поиск показывает точки подключения без открытия порта; проверка отправляет единственную доступную только для чтения команду version выбранному источнику.", UiLocale.EN: "Opening the page creates no presenter and does not access USB. Discover lists endpoints without opening a port; Verify sends one read-only version command only to the selected source."}),
        "tinysa_activation.state.inactive": MappingProxyType({UiLocale.RU: "Источник не активирован", UiLocale.EN: "Source not activated"}),
        "tinysa_activation.source.label": MappingProxyType({UiLocale.RU: "Обнаруженный источник", UiLocale.EN: "Discovered source"}),
        "tinysa_activation.source.name": MappingProxyType({UiLocale.RU: "Обнаруженный источник tinySA", UiLocale.EN: "Discovered tinySA source"}),
        "tinysa_activation.source.description": MappingProxyType({UiLocale.RU: "Кандидаты с маршрутом, скрытым для защиты данных, полученные последним явным поиском", UiLocale.EN: "Route-redacted candidates from the last explicit discovery"}),
        "tinysa_activation.discover": MappingProxyType({UiLocale.RU: "Найти tinySA", UiLocale.EN: "Discover tinySA"}),
        "tinysa_activation.discover.name": MappingProxyType({UiLocale.RU: "Найти источники tinySA", UiLocale.EN: "Discover tinySA sources"}),
        "tinysa_activation.discover.description": MappingProxyType({UiLocale.RU: "Явно перечисляет точки подключения USB без открытия последовательного порта", UiLocale.EN: "Explicitly lists USB endpoints without opening a serial port"}),
        "tinysa_activation.select": MappingProxyType({UiLocale.RU: "Выбрать источник", UiLocale.EN: "Select source"}),
        "tinysa_activation.select.name": MappingProxyType({UiLocale.RU: "Выбрать источник tinySA", UiLocale.EN: "Select tinySA source"}),
        "tinysa_activation.select.description": MappingProxyType({UiLocale.RU: "Выбирает идентификатор источника без раскрытия маршрута, не открывая точку подключения", UiLocale.EN: "Selects an opaque source id without opening an endpoint"}),
        "tinysa_activation.verify": MappingProxyType({UiLocale.RU: "Проверить идентификатор", UiLocale.EN: "Verify identity"}),
        "tinysa_activation.verify.name": MappingProxyType({UiLocale.RU: "Проверить идентификатор tinySA", UiLocale.EN: "Verify tinySA identity"}),
        "tinysa_activation.verify.description": MappingProxyType({UiLocale.RU: "Отправляет единственную доступную только для чтения команду version выбранному источнику", UiLocale.EN: "Sends one read-only version command to the selected source"}),
        "tinysa_activation.compose": MappingProxyType({UiLocale.RU: "Использовать проверенный источник", UiLocale.EN: "Use verified source"}),
        "tinysa_activation.compose.name": MappingProxyType({UiLocale.RU: "Использовать проверенный источник tinySA", UiLocale.EN: "Use verified tinySA source"}),
        "tinysa_activation.compose.description": MappingProxyType({UiLocale.RU: "Создаёт неактивную привязку анализатора без запуска scanraw и изменения параметров", UiLocale.EN: "Creates an inert analyzer binding without scanraw or settings changes"}),
        "tinysa_activation.identity.unverified": MappingProxyType({UiLocale.RU: "Идентификатор ещё не проверен. Непрерывность устройства не подтверждается поиском или командой version.", UiLocale.EN: "Identity is not verified yet. Device continuity is not proven by discovery or a version command."}),
        "tinysa_activation.identity.name": MappingProxyType({UiLocale.RU: "Статус идентификатора tinySA", UiLocale.EN: "tinySA identity status"}),
        "tinysa_activation.identity.verified": MappingProxyType({UiLocale.RU: "{label}; достоверность идентификатора: {assurance}; непрерывность устройства по-прежнему не подтверждена.", UiLocale.EN: "{label}; identity assurance: {assurance}; device continuity remains unverified."}),
        "tinysa_activation.busy": MappingProxyType({UiLocale.RU: "Операция источника выполняется…", UiLocale.EN: "Source operation is running…"}),
        "tinysa_activation.busy.name": MappingProxyType({UiLocale.RU: "Операция источника tinySA выполняется", UiLocale.EN: "tinySA source operation is running"}),
        "tinysa_activation.error.title": MappingProxyType({UiLocale.RU: "Ошибка подключения tinySA", UiLocale.EN: "tinySA connection error"}),
        "tinysa_activation.error.select": MappingProxyType({UiLocale.RU: "Выберите обнаруженный источник tinySA.", UiLocale.EN: "Select a discovered tinySA source."}),
        "tinysa_activation.error.registration_failed": MappingProxyType({UiLocale.RU: "Регистрация анализатора tinySA в UI V2 не удалась.", UiLocale.EN: "tinySA V2 analyzer registration failed."}),
        "tinysa_activation.error.invalid_registration": MappingProxyType({UiLocale.RU: "Регистрация рабочего пространства анализатора UI V2 недействительна.", UiLocale.EN: "V2 analyzer workspace registration is invalid."}),
        "tinysa_activation.workspace.description": MappingProxyType({UiLocale.RU: "Явное подключение и однократный анализ спектра", UiLocale.EN: "Explicit connection and one-shot spectrum analyzer"}),
        "tinysa_activation.inspector.context": MappingProxyType({UiLocale.RU: "Контекст", UiLocale.EN: "Context"}),
        "tinysa_activation.inspector.detail": MappingProxyType({UiLocale.RU: "Активация источника tinySA", UiLocale.EN: "tinySA source activation"}),
        "tinysa_activation.inspector.identity": MappingProxyType({UiLocale.RU: "Источник выбирается по идентификатору без раскрытия маршрута. UI V2 не показывает COM-маршрут или серийный номер USB и не заявляет о непрерывности устройства.", UiLocale.EN: "The source is selected by route-redacted identity. UI V2 does not show a COM route or USB serial and makes no device-continuity claim."}),
        "tinysa_activation.inspector.boundary": MappingProxyType({UiLocale.RU: "Использование создаёт только неактивную привязку. scanraw и рабочие параметры требуют отдельных последующих явных действий.", UiLocale.EN: "Compose creates only an inert binding. scanraw and runtime settings require separate later explicit actions."}),
        "tinysa_analyzer.accuracy.fast": MappingProxyType(
            {UiLocale.RU: "Быстро", UiLocale.EN: "Fast"}
        ),
        "tinysa_analyzer.accuracy.noise_source": MappingProxyType(
            {UiLocale.RU: "Источник шума", UiLocale.EN: "Noise source"}
        ),
        "tinysa_analyzer.accuracy.normal": MappingProxyType(
            {UiLocale.RU: "Обычный", UiLocale.EN: "Normal"}
        ),
        "tinysa_analyzer.accuracy.precise": MappingProxyType(
            {UiLocale.RU: "Точный", UiLocale.EN: "Precise"}
        ),
        "tinysa_analyzer.accuracy.unchanged": MappingProxyType(
            {UiLocale.RU: "Не изменять", UiLocale.EN: "Unchanged"}
        ),
        "tinysa_analyzer.attenuation_mode.auto": MappingProxyType(
            {UiLocale.RU: "Авто", UiLocale.EN: "Auto"}
        ),
        "tinysa_analyzer.attenuation_mode.manual": MappingProxyType(
            {UiLocale.RU: "Вручную", UiLocale.EN: "Manual"}
        ),
        "tinysa_analyzer.attenuation_mode.unchanged": MappingProxyType(
            {UiLocale.RU: "Не изменять", UiLocale.EN: "Unchanged"}
        ),
        "tinysa_analyzer.accessible.name": MappingProxyType({UiLocale.RU: "Анализатор tinySA — UI V2", UiLocale.EN: "tinySA analyzer UI V2"}),
        "tinysa_analyzer.accessible.description": MappingProxyType({UiLocale.RU: "Однократные трассы dBm, выдаваемые прибором, и явно проверенные рабочие параметры. Нет непрерывного потока и необработанных I/Q-данных.", UiLocale.EN: "One-shot device-reported dBm traces and explicitly reviewed runtime settings. No continuous stream or raw I/Q."}),
        "tinysa_analyzer.header.title": MappingProxyType({UiLocale.RU: "Анализатор tinySA", UiLocale.EN: "tinySA analyzer"}),
        "tinysa_analyzer.header.detail": MappingProxyType({UiLocale.RU: "За одно действие выполняется один ограниченный scanraw. Значения выдаются прибором в dBm; это не SDR dBFS и не необработанные I/Q-данные.", UiLocale.EN: "One bounded scanraw per action. Values are device-reported dBm, not SDR dBFS or raw I/Q."}),
        "tinysa_analyzer.state.ready": MappingProxyType({UiLocale.RU: "Готов", UiLocale.EN: "Ready"}),
        "tinysa_analyzer.source.name": MappingProxyType({UiLocale.RU: "Проверенный источник tinySA", UiLocale.EN: "Verified tinySA source"}),
        "tinysa_analyzer.busy": MappingProxyType({UiLocale.RU: "Операция tinySA выполняется…", UiLocale.EN: "tinySA operation is running…"}),
        "tinysa_analyzer.busy.name": MappingProxyType({UiLocale.RU: "Операция tinySA выполняется", UiLocale.EN: "tinySA operation is running"}),
        "tinysa_analyzer.error.title": MappingProxyType({UiLocale.RU: "Ошибка tinySA", UiLocale.EN: "tinySA error"}),
        "tinysa_analyzer.card.acquisition": MappingProxyType({UiLocale.RU: "Однократное получение", UiLocale.EN: "One-shot acquisition"}),
        "tinysa_analyzer.card.trace": MappingProxyType({UiLocale.RU: "Трасса dBm от прибора", UiLocale.EN: "Device-reported dBm trace"}),
        "tinysa_analyzer.card.settings": MappingProxyType({UiLocale.RU: "Рабочие параметры сканирования", UiLocale.EN: "Runtime sweep settings"}),
        "tinysa_analyzer.frequency.start.name": MappingProxyType({UiLocale.RU: "Начальная частота tinySA, МГц", UiLocale.EN: "tinySA start frequency, MHz"}),
        "tinysa_analyzer.frequency.stop.name": MappingProxyType({UiLocale.RU: "Конечная частота tinySA, МГц", UiLocale.EN: "tinySA stop frequency, MHz"}),
        "tinysa_analyzer.frequency.description": MappingProxyType({UiLocale.RU: "Преобразуется в точную сетку с целыми значениями в герцах перед явным scanraw", UiLocale.EN: "Converted to an exact integer-Hz grid before explicit scanraw"}),
        "tinysa_analyzer.form.start": MappingProxyType({UiLocale.RU: "Начало, МГц", UiLocale.EN: "Start, MHz"}),
        "tinysa_analyzer.form.stop": MappingProxyType({UiLocale.RU: "Конец, МГц", UiLocale.EN: "Stop, MHz"}),
        "tinysa_analyzer.form.points": MappingProxyType({UiLocale.RU: "Точки", UiLocale.EN: "Points"}),
        "tinysa_analyzer.points.name": MappingProxyType({UiLocale.RU: "Число точек однократного сканирования tinySA", UiLocale.EN: "tinySA one-shot point count"}),
        "tinysa_analyzer.points.description": MappingProxyType({UiLocale.RU: "Программный предел: 10 001 точка и 30 КиБ для scanraw", UiLocale.EN: "Product cap: 10,001 points and 30 KiB for scanraw"}),
        "tinysa_analyzer.acquire": MappingProxyType({UiLocale.RU: "Получить одну трассу", UiLocale.EN: "Acquire one trace"}),
        "tinysa_analyzer.acquire.name": MappingProxyType({UiLocale.RU: "Получить одну трассу tinySA", UiLocale.EN: "Acquire one tinySA trace"}),
        "tinysa_analyzer.acquire.description": MappingProxyType({UiLocale.RU: "Явно получает смещение нуля и один scanraw с нулевой опцией без изменения рабочих параметров", UiLocale.EN: "Explicitly requests zero offset and one option-zero scanraw without runtime settings changes"}),
        "tinysa_analyzer.trace.empty": MappingProxyType({UiLocale.RU: "Трасса не получена.", UiLocale.EN: "Trace not acquired."}),
        "tinysa_analyzer.trace.summary": MappingProxyType({UiLocale.RU: "{source_points:,} точек анализа · {display_points:,} экстремумов для отображения · {elapsed:.3f} с · {rate:.2f} точек/с по времени хоста (не LPS и не FFT/s).", UiLocale.EN: "{source_points:,} analytical points · {display_points:,} presentation extrema · {elapsed:.3f} s · {rate:.2f} host-total points/s (not LPS or FFT/s)."}),
        "tinysa_analyzer.trace.unavailable": MappingProxyType({UiLocale.RU: "Трасса недоступна в текущем состоянии tinySA.", UiLocale.EN: "Trace is unavailable in the current tinySA state."}),
        "tinysa_analyzer.trace.scene_name": MappingProxyType({UiLocale.RU: "График значений dBm, выдаваемых tinySA", UiLocale.EN: "tinySA device-reported dBm plot"}),
        "tinysa_analyzer.trace.boundary": MappingProxyType({UiLocale.RU: "График получает только ограниченное отображение с сохранением пиков. Полная аналитическая трасса остаётся за границей прикладного слоя и представления.", UiLocale.EN: "The plot receives only bounded peak-preserving presentation. The complete analytical trace remains behind the presenter/application boundary."}),
        "tinysa_analyzer.settings.detail": MappingProxyType({UiLocale.RU: "Проверка не открывает порт. Применение требует отдельного подтверждения; подтверждение команды не означает считывание состояния, откат или постоянное сохранение.", UiLocale.EN: "Review does not open a port. Apply requires separate confirmation; acknowledgement is not readback, rollback, or persistent save."}),
        "tinysa_analyzer.settings.accuracy.name": MappingProxyType({UiLocale.RU: "Политика точности сканирования", UiLocale.EN: "Sweep accuracy policy"}),
        "tinysa_analyzer.settings.rbw_mode.name": MappingProxyType({UiLocale.RU: "Режим RBW tinySA", UiLocale.EN: "tinySA RBW mode"}),
        "tinysa_analyzer.settings.rbw.name": MappingProxyType({UiLocale.RU: "Ручная RBW tinySA, Гц", UiLocale.EN: "Manual tinySA RBW, Hz"}),
        "tinysa_analyzer.settings.lna.name": MappingProxyType({UiLocale.RU: "Политика LNA tinySA", UiLocale.EN: "tinySA LNA policy"}),
        "tinysa_analyzer.settings.attenuation_mode.name": MappingProxyType({UiLocale.RU: "Режим ослабления tinySA", UiLocale.EN: "tinySA attenuation mode"}),
        "tinysa_analyzer.settings.attenuation.name": MappingProxyType({UiLocale.RU: "Ручное ослабление tinySA, дБ", UiLocale.EN: "Manual tinySA attenuation, dB"}),
        "tinysa_analyzer.settings.spur.name": MappingProxyType({UiLocale.RU: "Политика подавления паразитных составляющих tinySA", UiLocale.EN: "tinySA spur-removal policy"}),
        "tinysa_analyzer.settings.sweep_time": MappingProxyType({UiLocale.RU: "Установить явное время сканирования", UiLocale.EN: "Set explicit sweep time"}),
        "tinysa_analyzer.settings.sweep_time.name": MappingProxyType({UiLocale.RU: "Включить явное время сканирования tinySA", UiLocale.EN: "Enable explicit tinySA sweep time"}),
        "tinysa_analyzer.settings.sweep_time_ms.name": MappingProxyType({UiLocale.RU: "Время сканирования tinySA, миллисекунды", UiLocale.EN: "tinySA sweep time, milliseconds"}),
        "tinysa_analyzer.settings.repeat": MappingProxyType({UiLocale.RU: "Установить количество повторов", UiLocale.EN: "Set repeat count"}),
        "tinysa_analyzer.settings.repeat.name": MappingProxyType({UiLocale.RU: "Включить количество повторов tinySA", UiLocale.EN: "Enable tinySA repeat count"}),
        "tinysa_analyzer.settings.repeat_count.name": MappingProxyType({UiLocale.RU: "Количество повторов tinySA", UiLocale.EN: "tinySA repeat count"}),
        "tinysa_analyzer.settings.form.accuracy": MappingProxyType({UiLocale.RU: "Точность", UiLocale.EN: "Accuracy"}),
        "tinysa_analyzer.settings.form.rbw": MappingProxyType({UiLocale.RU: "RBW", UiLocale.EN: "RBW"}),
        "tinysa_analyzer.settings.form.manual_rbw": MappingProxyType({UiLocale.RU: "Ручная RBW", UiLocale.EN: "Manual RBW"}),
        "tinysa_analyzer.settings.form.lna": MappingProxyType({UiLocale.RU: "LNA", UiLocale.EN: "LNA"}),
        "tinysa_analyzer.settings.form.attenuation": MappingProxyType({UiLocale.RU: "Ослабление", UiLocale.EN: "Attenuation"}),
        "tinysa_analyzer.settings.form.manual_attenuation": MappingProxyType({UiLocale.RU: "Ручное ослабление", UiLocale.EN: "Manual attenuation"}),
        "tinysa_analyzer.settings.form.spur": MappingProxyType({UiLocale.RU: "Подавление паразитных составляющих", UiLocale.EN: "Spur removal"}),
        "tinysa_analyzer.settings.unavailable": MappingProxyType({UiLocale.RU: "NSPEEDUP / WSPEEDUP: значения интерфейса прошивки существуют, но для 26fc821 отсутствуют допущенные стабильные команды хоста и считывание состояния; UI V2 их не применяет.", UiLocale.EN: "NSPEEDUP / WSPEEDUP: firmware UI values exist, but 26fc821 has no admitted stable host command/readback; UI V2 does not apply them."}),
        "tinysa_analyzer.settings.unavailable.name": MappingProxyType({UiLocale.RU: "NSPEEDUP и WSPEEDUP недоступны", UiLocale.EN: "NSPEEDUP and WSPEEDUP unavailable"}),
        "tinysa_analyzer.settings.not_reviewed": MappingProxyType({UiLocale.RU: "Настройки не проверены.", UiLocale.EN: "Settings not reviewed."}),
        "tinysa_analyzer.settings.review.name": MappingProxyType({UiLocale.RU: "Проверить рабочие параметры tinySA", UiLocale.EN: "Review tinySA runtime settings"}),
        "tinysa_analyzer.settings.review": MappingProxyType({UiLocale.RU: "Проверить настройки", UiLocale.EN: "Review settings"}),
        "tinysa_analyzer.settings.review.description": MappingProxyType({UiLocale.RU: "Строит проверку без открытия порта и без изменения прибора", UiLocale.EN: "Builds a review without opening a port or changing the device"}),
        "tinysa_analyzer.settings.apply": MappingProxyType({UiLocale.RU: "Применить проверенные настройки…", UiLocale.EN: "Apply reviewed settings…"}),
        "tinysa_analyzer.settings.apply.name": MappingProxyType({UiLocale.RU: "Применить проверенные рабочие параметры tinySA", UiLocale.EN: "Apply reviewed tinySA runtime settings"}),
        "tinysa_analyzer.settings.apply.description": MappingProxyType({UiLocale.RU: "Запрашивает отдельное подтверждение перед ограниченной операцией с настройками", UiLocale.EN: "Requests separate confirmation before finite settings operation"}),
        "tinysa_analyzer.settings.unavailable_review": MappingProxyType({UiLocale.RU: "Проверка настроек недоступна в текущем состоянии tinySA.", UiLocale.EN: "Review settings is unavailable in the current tinySA state."}),
        "tinysa_analyzer.settings.review_summary": MappingProxyType({UiLocale.RU: "Проверка: {commands}. Состояние остаётся непроверенным и не подлежит восстановлению. Предупреждения: {warnings}.", UiLocale.EN: "Review: {commands}. State remains unverified and non-restorable. Warnings: {warnings}."}),
        "tinysa_analyzer.no": MappingProxyType({UiLocale.RU: "нет", UiLocale.EN: "no"}),
        "tinysa_analyzer.yes": MappingProxyType({UiLocale.RU: "да", UiLocale.EN: "yes"}),
        "tinysa_analyzer.dialog.title": MappingProxyType({UiLocale.RU: "Применить рабочие параметры tinySA", UiLocale.EN: "Apply tinySA runtime settings"}),
        "tinysa_analyzer.dialog.detail": MappingProxyType({UiLocale.RU: "Применить проверенные рабочие параметры? Некоторые поля не имеют полного считывания состояния или отката; постоянное сохранение не выполняется.", UiLocale.EN: "Apply reviewed runtime settings? Some fields lack complete readback/rollback; persistent save is not performed."}),
        "tinysa_analyzer.phase.busy": MappingProxyType(
            {UiLocale.RU: "Выполняется", UiLocale.EN: "Busy"}
        ),
        "tinysa_analyzer.phase.faulted": MappingProxyType(
            {UiLocale.RU: "Ошибка", UiLocale.EN: "Faulted"}
        ),
        "tinysa_analyzer.phase.ready": MappingProxyType(
            {UiLocale.RU: "Готов", UiLocale.EN: "Ready"}
        ),
        "tinysa_analyzer.phase.settings_review": MappingProxyType(
            {UiLocale.RU: "Проверка настроек", UiLocale.EN: "Settings review"}
        ),
        "tinysa_analyzer.phase.trace_ready": MappingProxyType(
            {UiLocale.RU: "Трасса готова", UiLocale.EN: "Trace ready"}
        ),
        "tinysa_analyzer.rbw_mode.auto": MappingProxyType(
            {UiLocale.RU: "Авто", UiLocale.EN: "Auto"}
        ),
        "tinysa_analyzer.rbw_mode.manual": MappingProxyType(
            {UiLocale.RU: "Вручную", UiLocale.EN: "Manual"}
        ),
        "tinysa_analyzer.rbw_mode.unchanged": MappingProxyType(
            {UiLocale.RU: "Не изменять", UiLocale.EN: "Unchanged"}
        ),
        "tinysa_analyzer.reason.no_pending_settings": MappingProxyType(
            {UiLocale.RU: "Нет ожидающих настроек", UiLocale.EN: "No pending settings"}
        ),
        "tinysa_analyzer.reason.no_settings_changes": MappingProxyType(
            {UiLocale.RU: "Настройки не изменены", UiLocale.EN: "No settings changes"}
        ),
        "tinysa_analyzer.reason.operation_already_pending": MappingProxyType(
            {UiLocale.RU: "Операция уже ожидает завершения", UiLocale.EN: "Operation already pending"}
        ),
        "tinysa_analyzer.reason.settings_acknowledged_unverified": MappingProxyType(
            {UiLocale.RU: "Команды подтверждены; состояние не проверено", UiLocale.EN: "Commands acknowledged; state unverified"}
        ),
        "tinysa_analyzer.reason.settings_application_failed": MappingProxyType(
            {UiLocale.RU: "Не удалось применить настройки", UiLocale.EN: "Settings application failed"}
        ),
        "tinysa_analyzer.reason.settings_cancelled": MappingProxyType(
            {UiLocale.RU: "Применение настроек отменено", UiLocale.EN: "Settings application cancelled"}
        ),
        "tinysa_analyzer.reason.trace_collection_failed": MappingProxyType(
            {UiLocale.RU: "Не удалось получить трассу", UiLocale.EN: "Trace collection failed"}
        ),
        "tinysa_analyzer.source.summary": MappingProxyType({UiLocale.RU: "{label} · {model} · достоверность идентификатора: {assurance} · непрерывность устройства не подтверждена", UiLocale.EN: "{label} · {model} · identity assurance: {assurance} · device continuity unverified"}),
        "tinysa_analyzer.spur_policy.auto": MappingProxyType(
            {UiLocale.RU: "Авто", UiLocale.EN: "Auto"}
        ),
        "tinysa_analyzer.spur_policy.off": MappingProxyType(
            {UiLocale.RU: "Выключено", UiLocale.EN: "Off"}
        ),
        "tinysa_analyzer.spur_policy.on": MappingProxyType(
            {UiLocale.RU: "Включено", UiLocale.EN: "On"}
        ),
        "tinysa_analyzer.spur_policy.unchanged": MappingProxyType(
            {UiLocale.RU: "Не изменять", UiLocale.EN: "Unchanged"}
        ),
        "tinysa_analyzer.switch_policy.off": MappingProxyType(
            {UiLocale.RU: "Выключено", UiLocale.EN: "Off"}
        ),
        "tinysa_analyzer.switch_policy.on": MappingProxyType(
            {UiLocale.RU: "Включено", UiLocale.EN: "On"}
        ),
        "tinysa_analyzer.switch_policy.unchanged": MappingProxyType(
            {UiLocale.RU: "Не изменять", UiLocale.EN: "Unchanged"}
        ),
        "tinysa_analyzer.unit.db": MappingProxyType({UiLocale.RU: " дБ", UiLocale.EN: " dB"}),
        "tinysa_analyzer.unit.hz": MappingProxyType({UiLocale.RU: " Гц", UiLocale.EN: " Hz"}),
        "tinysa_analyzer.unit.mhz": MappingProxyType({UiLocale.RU: " МГц", UiLocale.EN: " MHz"}),
        "tinysa_analyzer.unit.ms": MappingProxyType({UiLocale.RU: " мс", UiLocale.EN: " ms"}),
        "tinysa_analyzer.workspace.label": MappingProxyType(
            {UiLocale.RU: "Анализатор tinySA", UiLocale.EN: "tinySA analyzer"}
        ),
        "tinysa_identity.assurance.pnp_endpoint_only_continuity_unverified": MappingProxyType(
            {
                UiLocale.RU: "только точка подключения PnP; непрерывность не подтверждена",
                UiLocale.EN: "PnP endpoint only; continuity unverified",
            }
        ),
        "tinysa_identity.assurance.usb_location": MappingProxyType(
            {UiLocale.RU: "расположение USB", UiLocale.EN: "USB location"}
        ),
        "tinysa_identity.assurance.usb_serial": MappingProxyType(
            {UiLocale.RU: "серийный номер USB", UiLocale.EN: "USB serial"}
        ),
        "tinysa_analyzer.workspace.description": MappingProxyType({UiLocale.RU: "Однократные трассы dBm, выдаваемые прибором, через проверенный источник tinySA", UiLocale.EN: "One-shot device-reported dBm traces through verified tinySA source"}),
        "tinysa_analyzer.inspector.context": MappingProxyType({UiLocale.RU: "Контекст", UiLocale.EN: "Context"}),
        "tinysa_analyzer.inspector.boundary": MappingProxyType({UiLocale.RU: "Значения трассы выдаются прибором в dBm и имеют встроенные сведения о происхождении. Эта страница не показывает необработанные I/Q-данные, преобразование dBFS, доказательство непрерывности или заявление о метрологической точности.", UiLocale.EN: "Trace values are device-reported dBm with built-in provenance. This page never displays raw I/Q, dBFS conversion, continuity proof, or a metrological accuracy claim."}),
        "tinysa_analyzer.inspector.summary": MappingProxyType({UiLocale.RU: "Источник: {source}\nМодель: {model}\nТрасса: {trace}\nВыполняется: {busy}", UiLocale.EN: "Source: {source}\nModel: {model}\nTrace: {trace}\nBusy: {busy}"}),
        "tinysa_analyzer.inspector.trace": MappingProxyType({UiLocale.RU: "{points:,} точек / {elapsed:.3f} с", UiLocale.EN: "{points:,} points / {elapsed:.3f} s"}),
        "replay.accessible.description": MappingProxyType(
            {
                UiLocale.RU: "Открывает только выбранную пользователем запись спектров. Приём в реальном времени, запись и I/Q-кадры не передаются в UI V2.",
                UiLocale.EN: "Opens only a user-selected spectrum recording. Live, recording, and I/Q frames are not sent to UI V2.",
            }
        ),
        "replay.accessible.name": MappingProxyType(
            {UiLocale.RU: "Воспроизведение спектров — UI V2", UiLocale.EN: "UI V2 spectrum replay"}
        ),
        "replay.backend.cpu": MappingProxyType({UiLocale.RU: "CPU", UiLocale.EN: "CPU"}),
        "replay.backend.cuda": MappingProxyType(
            {UiLocale.RU: "CUDA (резерв: CPU)", UiLocale.EN: "CUDA (fallback: CPU)"}
        ),
        "replay.backend.name": MappingProxyType(
            {UiLocale.RU: "Вычислительный модуль офлайн-пересчёта I/Q", UiLocale.EN: "Offline I/Q reprocess backend"}
        ),
        "replay.card.reprocess": MappingProxyType(
            {UiLocale.RU: "Офлайн-пересчёт I/Q", UiLocale.EN: "Offline I/Q reprocess"}
        ),
        "replay.card.source": MappingProxyType(
            {UiLocale.RU: "Источник", UiLocale.EN: "Source"}
        ),
        "replay.card.timeline": MappingProxyType(
            {UiLocale.RU: "Кадры спектра", UiLocale.EN: "Spectrum frames"}
        ),
        "replay.choose": MappingProxyType({UiLocale.RU: "Выбрать…", UiLocale.EN: "Choose…"}),
        "replay.choose.name": MappingProxyType(
            {UiLocale.RU: "Выбрать файл архивной записи", UiLocale.EN: "Choose historical recording file"}
        ),
        "replay.dialog.confirm.detail": MappingProxyType(
            {
                UiLocale.RU: "Будет создан новый .reprocessed.jsonl рядом с выбранной записью. I/Q не будет передан в UI. Продолжить?",
                UiLocale.EN: "A new .reprocessed.jsonl will be created next to the selected recording. I/Q will not be sent to the UI. Continue?",
            }
        ),
        "replay.dialog.confirm.title": MappingProxyType(
            {UiLocale.RU: "Подтвердите офлайн-пересчёт", UiLocale.EN: "Confirm offline reprocess"}
        ),
        "replay.dialog.filter": MappingProxyType(
            {UiLocale.RU: "Записи SDR (*.sdrrec);;Все файлы (*)", UiLocale.EN: "SDR recordings (*.sdrrec);;All files (*)"}
        ),
        "replay.dialog.open.title": MappingProxyType(
            {UiLocale.RU: "Выберите архивную запись", UiLocale.EN: "Choose historical recording"}
        ),
        "replay.error.title": MappingProxyType(
            {UiLocale.RU: "Ошибка воспроизведения", UiLocale.EN: "Replay error"}
        ),
        "replay.header.detail": MappingProxyType(
            {
                UiLocale.RU: "Открытие файла и переход к следующему кадру — явные действия. Это воспроизведение архивного .sdrrec, а не приём в реальном времени и не нативная запись RTBW.",
                UiLocale.EN: "Opening a file and advancing to the next frame are explicit actions. This is historical .sdrrec replay, not Live or native RTBW recording.",
            }
        ),
        "replay.header.title": MappingProxyType(
            {UiLocale.RU: "Воспроизведение спектров", UiLocale.EN: "Spectrum replay"}
        ),
        "replay.index.summary": MappingProxyType(
            {
                UiLocale.RU: "Файл: {filename} · кадров спектра: {frames:,} · длительность: {duration:.3f} с · размер: {size:,} байт",
                UiLocale.EN: "File: {filename} · spectrum frames: {frames:,} · duration: {duration:.3f} s · size: {size:,} B",
            }
        ),
        "replay.index.unopened": MappingProxyType(
            {UiLocale.RU: "Путь и содержимое файла не читаются до явного открытия.", UiLocale.EN: "File path and contents are not read before explicit open."}
        ),
        "replay.inspector.boundary": MappingProxyType(
            {
                UiLocale.RU: "Этот маршрут UI V2 читает только выбранные архивные кадры спектра. Он не запускает приём в реальном времени, не записывает, не передаёт I/Q в интерфейс и не заявляет о накоплении или непрерывности.",
                UiLocale.EN: "This UI V2 route reads only selected historical spectrum frames. It does not start Live, record, feed I/Q into Qt, or claim persistence/continuity.",
            }
        ),
        "replay.inspector.context": MappingProxyType(
            {UiLocale.RU: "Контекст", UiLocale.EN: "Context"}
        ),
        "replay.inspector.detail": MappingProxyType(
            {UiLocale.RU: "Только воспроизведение спектра", UiLocale.EN: "Spectrum replay only"}
        ),
        "replay.inspector.summary": MappingProxyType(
            {
                UiLocale.RU: "Файл: {filename}\nКадров спектра: {frames:,}\nВыполняется: {busy}",
                UiLocale.EN: "File: {filename}\nSpectrum frames: {frames:,}\nBusy: {busy}",
            }
        ),
        "replay.inspector.unopened": MappingProxyType(
            {UiLocale.RU: "Запись не открыта; навигация не создаёт контроллер воспроизведения.", UiLocale.EN: "Recording is not open; navigation does not create ReplayPresenter."}
        ),
        "replay.next": MappingProxyType(
            {UiLocale.RU: "Следующий спектр", UiLocale.EN: "Next spectrum"}
        ),
        "replay.next.name": MappingProxyType(
            {UiLocale.RU: "Прочитать следующий спектральный кадр", UiLocale.EN: "Read next spectrum frame"}
        ),
        "replay.no": MappingProxyType({UiLocale.RU: "нет", UiLocale.EN: "no"}),
        "replay.open": MappingProxyType(
            {UiLocale.RU: "Открыть спектры", UiLocale.EN: "Open spectra"}
        ),
        "replay.open.name": MappingProxyType(
            {UiLocale.RU: "Открыть спектральные кадры из выбранной записи", UiLocale.EN: "Open spectrum frames from selected recording"}
        ),
        "replay.path.label": MappingProxyType(
            {UiLocale.RU: "Файл .sdrrec:", UiLocale.EN: ".sdrrec file:"}
        ),
        "replay.path.name": MappingProxyType(
            {UiLocale.RU: "Файл для воспроизведения спектра", UiLocale.EN: "Spectrum replay file"}
        ),
        "replay.path.placeholder": MappingProxyType(
            {UiLocale.RU: "Укажите архивную запись перед явным открытием", UiLocale.EN: "Enter a historical recording before explicit open"}
        ),
        "replay.position.current": MappingProxyType(
            {UiLocale.RU: "Позиция: {ordinal:,}/{total:,} · {percent:.1f}%", UiLocale.EN: "Position: {ordinal:,}/{total:,} · {percent:.1f}%"}
        ),
        "replay.position.empty": MappingProxyType(
            {UiLocale.RU: "Позиция: нет индекса", UiLocale.EN: "Position: no index"}
        ),
        "replay.position.name": MappingProxyType(
            {UiLocale.RU: "Позиция воспроизведения спектра", UiLocale.EN: "Spectrum replay position"}
        ),
        "replay.reprocess.cancel": MappingProxyType(
            {UiLocale.RU: "Отменить", UiLocale.EN: "Cancel"}
        ),
        "replay.reprocess.cancel.name": MappingProxyType(
            {UiLocale.RU: "Отменить офлайн-пересчёт I/Q", UiLocale.EN: "Cancel offline I/Q reprocess"}
        ),
        "replay.reprocess.explanation": MappingProxyType(
            {
                UiLocale.RU: "Отдельная явная команда офлайн-пересчёта архивной I/Q-записи создаёт .reprocessed.jsonl. I/Q-данные не публикуются в интерфейс.",
                UiLocale.EN: "A separate explicit historical I/Q reprocess command creates .reprocessed.jsonl. The I/Q payload is not published to Qt.",
            }
        ),
        "replay.reprocess.name": MappingProxyType(
            {UiLocale.RU: "Явно пересчитать архивную I/Q-запись", UiLocale.EN: "Explicitly reprocess historical I/Q recording"}
        ),
        "replay.reprocess.not_published": MappingProxyType(
            {UiLocale.RU: "не опубликован", UiLocale.EN: "not published"}
        ),
        "replay.reprocess.not_started": MappingProxyType(
            {UiLocale.RU: "Офлайн-пересчёт не запускался.", UiLocale.EN: "Offline reprocess has not started."}
        ),
        "replay.reprocess.run": MappingProxyType(
            {UiLocale.RU: "Пересчитать I/Q офлайн", UiLocale.EN: "Reprocess I/Q offline"}
        ),
        "replay.reprocess.summary": MappingProxyType(
            {
                UiLocale.RU: "Пересчёт: {status} · кадров: {frames:,} · вычислительный модуль: {backend_used} (запрошен {backend_requested}) · результат: {output}{warning}",
                UiLocale.EN: "Reprocess: {status} · frames: {frames:,} · backend: {backend_used} (requested {backend_requested}) · output: {output}{warning}",
            }
        ),
        "replay.scene.warning": MappingProxyType(
            {UiLocale.RU: "Плотность накопления не записывается этим маршрутом воспроизведения", UiLocale.EN: "Persistence density is not recorded by this replay route"}
        ),
        "replay.state.busy": MappingProxyType(
            {UiLocale.RU: "Выполняется операция слоя представления", UiLocale.EN: "Presenter operation is running"}
        ),
        "replay.state.ready": MappingProxyType(
            {UiLocale.RU: "Индекс спектров готов", UiLocale.EN: "Spectrum index ready"}
        ),
        "replay.state.unopened": MappingProxyType(
            {UiLocale.RU: "Запись не открыта", UiLocale.EN: "Recording is not open"}
        ),
        "replay.timeline.boundary": MappingProxyType(
            {
                UiLocale.RU: "Шкала времени не запускает непрерывное воспроизведение. В этом пакете по команде публикуется только один спектральный кадр.",
                UiLocale.EN: "The timeline does not start a continuous playback timer. This package publishes only one explicit spectrum frame per command.",
            }
        ),
        "replay.workspace.description": MappingProxyType(
            {UiLocale.RU: "Отложенное воспроизведение только спектров через существующий контроллер", UiLocale.EN: "Deferred spectrum-only replay through the existing ReplayPresenter"}
        ),
        "replay.workspace.label": MappingProxyType(
            {UiLocale.RU: "Воспроизведение", UiLocale.EN: "Replay"}
        ),
        "replay.yes": MappingProxyType({UiLocale.RU: "да", UiLocale.EN: "yes"}),
        "appearance.description": MappingProxyType(
            {
                UiLocale.RU: "Только настройки отображения UI V2. Устройство, профиль, калибровка, запись и прежний интерфейс не изменяются.",
                UiLocale.EN: "UI V2 display settings only. Device, profile, calibration, recording, and Legacy remain unchanged.",
            }
        ),
        "appearance.locale.description": MappingProxyType(
            {
                UiLocale.RU: "Пересобирает только виджеты UI V2; приём в реальном времени, обзор диапазона и приёмник не запускаются и не перезапускаются.",
                UiLocale.EN: "Rebuilds only UI V2 widgets; Live, Sweep, and the receiver are neither started nor restarted.",
            }
        ),
        "appearance.locale.en": MappingProxyType({UiLocale.RU: "Английский", UiLocale.EN: "English"}),
        "appearance.locale.name": MappingProxyType(
            {UiLocale.RU: "Использовать язык интерфейса: {locale_name}", UiLocale.EN: "Use interface language: {locale_name}"}
        ),
        "appearance.locale.ru": MappingProxyType({UiLocale.RU: "Русский", UiLocale.EN: "Russian"}),
        "appearance.reset_layout.description": MappingProxyType(
            {
                UiLocale.RU: "Сбрасывает только геометрию окна, навигацию и инспектор UI V2. Данные устройства не изменяются.",
                UiLocale.EN: "Resets only UI V2 window geometry, navigation, and inspector. Device data remains unchanged.",
            }
        ),
        "appearance.reset_layout.name": MappingProxyType(
            {UiLocale.RU: "Сбросить компоновку UI V2", UiLocale.EN: "Reset UI V2 layout"}
        ),
        "appearance.reset_layout.text": MappingProxyType(
            {UiLocale.RU: "Сбросить компоновку", UiLocale.EN: "Reset layout"}
        ),
        "appearance.reset_shell.description": MappingProxyType(
            {
                UiLocale.RU: "Сбрасывает только сохранённые настройки оболочки UI V2; прежний интерфейс и измерительные данные не затрагиваются.",
                UiLocale.EN: "Resets only saved UI V2 shell settings; Legacy and measurement data remain untouched.",
            }
        ),
        "appearance.reset_shell.name": MappingProxyType(
            {UiLocale.RU: "Сбросить настройки оболочки UI V2", UiLocale.EN: "Reset UI V2 shell settings"}
        ),
        "appearance.reset_shell.text": MappingProxyType(
            {UiLocale.RU: "Сбросить настройки UI V2", UiLocale.EN: "Reset UI V2 settings"}
        ),
        "appearance.reset_visuals.description": MappingProxyType(
            {
                UiLocale.RU: "Возвращает только тему UI V2 к тёмному значению по умолчанию.",
                UiLocale.EN: "Returns only the UI V2 theme to the default dark value.",
            }
        ),
        "appearance.reset_visuals.name": MappingProxyType(
            {UiLocale.RU: "Сбросить тему UI V2", UiLocale.EN: "Reset UI V2 theme"}
        ),
        "appearance.reset_visuals.text": MappingProxyType(
            {UiLocale.RU: "Сбросить вид", UiLocale.EN: "Reset appearance"}
        ),
        "appearance.theme.description": MappingProxyType(
            {UiLocale.RU: "Изменяет только оформление интерфейса UI V2.", UiLocale.EN: "Changes only the UI V2 presentation."}
        ),
        "appearance.theme.name": MappingProxyType(
            {UiLocale.RU: "Выбрать тему UI V2: {theme}", UiLocale.EN: "Select UI V2 theme: {theme}"}
        ),
        "appearance.title": MappingProxyType(
            {UiLocale.RU: "Вид и компоновка", UiLocale.EN: "Appearance and layout"}
        ),
        "home.accessible.description": MappingProxyType(
            {
                UiLocale.RU: "Безопасный старт SDR Native Monitoring без скрытого доступа к устройству.",
                UiLocale.EN: "Safe SDR Native Monitoring start without hidden device access.",
            }
        ),
        "home.accessible.name": MappingProxyType({UiLocale.RU: "Главная UI V2", UiLocale.EN: "UI V2 Home"}),
        "home.auto_search": MappingProxyType(
            {UiLocale.RU: "Автопоиск выключен", UiLocale.EN: "Auto-discovery off"}
        ),
        "home.auto_search_detail": MappingProxyType(
            {UiLocale.RU: "Откройте Приём, затем нажмите Найти или укажите URI.", UiLocale.EN: "Open Receive, then select Find or enter a URI."}
        ),
        "home.boundary": MappingProxyType(
            {
                UiLocale.RU: "UI V2 не создаёт второй RX-сеанс, не получает необработанные I/Q и не выводит непубликованные параметры как реальные.",
                UiLocale.EN: "UI V2 does not create a second RX session, receive raw I/Q, or present unpublished parameters as real.",
            }
        ),
        "home.calibration.action": MappingProxyType(
            {UiLocale.RU: "Открыть профили", UiLocale.EN: "Open profiles"}
        ),
        "home.calibration.detail.available": MappingProxyType(
            {
                UiLocale.RU: "Просмотр неизменяемых профилей, коррекции/неопределённости и применимости.",
                UiLocale.EN: "View immutable profiles, correction/uncertainty, and applicability.",
            }
        ),
        "home.calibration.detail.unavailable": MappingProxyType(
            {UiLocale.RU: "Профили и измерения будут перенесены отдельным V2-пакетом.", UiLocale.EN: "Profiles and measurements will move in a separate V2 package."}
        ),
        "home.calibration.title": MappingProxyType({UiLocale.RU: "Калибровка", UiLocale.EN: "Calibration"}),
        "home.card.action_name": MappingProxyType(
            {UiLocale.RU: "{action}: {title}", UiLocale.EN: "{action}: {title}"}
        ),
        "home.context.detail": MappingProxyType(
            {
                UiLocale.RU: "Навигация не ищет и не открывает SDR. Только кнопка Найти в Приёме передаёт явную команду существующему слою представления.",
                UiLocale.EN: "Navigation does not discover or open an SDR. Only Find in Receive sends an explicit command to the existing presenter.",
            }
        ),
        "home.context.subtitle": MappingProxyType({UiLocale.RU: "Безопасный старт", UiLocale.EN: "Safe start"}),
        "home.context.title": MappingProxyType({UiLocale.RU: "Контекст", UiLocale.EN: "Context"}),
        "home.diagnostics.action": MappingProxyType(
            {UiLocale.RU: "Открыть диагностику", UiLocale.EN: "Open diagnostics"}
        ),
        "home.diagnostics.detail.available": MappingProxyType(
            {
                UiLocale.RU: "Явный снимок диагностики и автономные проверки через существующий слой представления.",
                UiLocale.EN: "Explicit diagnostics snapshot and offline self-tests through the existing presenter.",
            }
        ),
        "home.diagnostics.detail.unavailable": MappingProxyType(
            {UiLocale.RU: "Диагностика будет доступна после отдельного контрактного решения.", UiLocale.EN: "Diagnostics will be available after a separate contract decision."}
        ),
        "home.diagnostics.title": MappingProxyType({UiLocale.RU: "Диагностика", UiLocale.EN: "Diagnostics"}),
        "home.header.detail": MappingProxyType(
            {
                UiLocale.RU: "Новая интерфейсная поверхность. Устройство не открывается до явного действия пользователя.",
                UiLocale.EN: "New interface surface. A device is not opened until an explicit user action.",
            }
        ),
        "home.header.title": MappingProxyType(
            {UiLocale.RU: "SDR Native Monitoring", UiLocale.EN: "SDR Native Monitoring"}
        ),
        "home.live.action": MappingProxyType({UiLocale.RU: "Открыть Приём", UiLocale.EN: "Open Receive"}),
        "home.live.detail": MappingProxyType(
            {UiLocale.RU: "Подключение и анализ приёма через существующий слой представления.", UiLocale.EN: "Connection and live analysis through the existing presenter."}
        ),
        "home.live.title": MappingProxyType({UiLocale.RU: "Приём", UiLocale.EN: "Receive"}),
        "home.replay.action": MappingProxyType(
            {UiLocale.RU: "Открыть воспроизведение", UiLocale.EN: "Open Replay"}
        ),
        "home.replay.detail.available": MappingProxyType(
            {
                UiLocale.RU: "Явное воспроизведение только спектра архивного файла .sdrrec через существующий слой представления.",
                UiLocale.EN: "Explicit spectrum-only replay of historical .sdrrec through the existing presenter.",
            }
        ),
        "home.replay.detail.unavailable": MappingProxyType(
            {
                UiLocale.RU: "Запись доступна только через отдельную нативную композицию RTBW; воспроизведение будет перенесено отдельным пакетом V2.",
                UiLocale.EN: "Recording is available only through a separate native RTBW composition; Replay will move in a separate V2 package.",
            }
        ),
        "home.replay.title": MappingProxyType({UiLocale.RU: "Воспроизведение", UiLocale.EN: "Replay"}),
        "home.sweep.action": MappingProxyType({UiLocale.RU: "Открыть обзор", UiLocale.EN: "Open Sweep"}),
        "home.sweep.detail.available": MappingProxyType(
            {
                UiLocale.RU: "Построение плана, явный запуск и оценка качества развёртки через существующий слой представления.",
                UiLocale.EN: "Plan building, explicit execution, and Sweep quality through the existing presenter.",
            }
        ),
        "home.sweep.detail.unavailable": MappingProxyType(
            {UiLocale.RU: "Развёртка будет доступна после отдельного контрактного решения.", UiLocale.EN: "Sweep will be available after a separate contract decision."}
        ),
        "home.sweep.title": MappingProxyType({UiLocale.RU: "Обзор частот", UiLocale.EN: "Frequency sweep"}),
        "home.tinysa.action": MappingProxyType({UiLocale.RU: "Открыть tinySA", UiLocale.EN: "Open tinySA"}),
        "home.tinysa.detail.available": MappingProxyType(
            {
                UiLocale.RU: "Явное подключение, проверенный источник и однократные трассы dBm через существующие границы представления.",
                UiLocale.EN: "Explicit connection, verified source, and one-shot dBm traces through existing presenter boundaries.",
            }
        ),
        "home.tinysa.detail.unavailable": MappingProxyType(
            {UiLocale.RU: "tinySA будет доступен после отдельного контрактного решения.", UiLocale.EN: "tinySA will be available after a separate contract decision."}
        ),
        "home.tinysa.title": MappingProxyType({UiLocale.RU: "tinySA", UiLocale.EN: "tinySA"}),
        "home.unavailable": MappingProxyType({UiLocale.RU: "Недоступно", UiLocale.EN: "Unavailable"}),
        "home.workspace.description": MappingProxyType(
            {UiLocale.RU: "Безопасный старт и навигация UI V2", UiLocale.EN: "Safe start and UI V2 navigation"}
        ),
        "home.workspace.label": MappingProxyType({UiLocale.RU: "Главная", UiLocale.EN: "Home"}),
        "navigation.collapse": MappingProxyType({UiLocale.RU: "Свернуть", UiLocale.EN: "Collapse"}),
        "navigation.collapse_name": MappingProxyType(
            {UiLocale.RU: "Свернуть навигацию", UiLocale.EN: "Collapse navigation"}
        ),
        "navigation.current_description": MappingProxyType(
            {
                UiLocale.RU: "Текущая страница. {detail}",
                UiLocale.EN: "Current page. {detail}",
            }
        ),
        "navigation.current_name": MappingProxyType(
            {
                UiLocale.RU: "{label}, текущая страница",
                UiLocale.EN: "{label}, current page",
            }
        ),
        "navigation.expand": MappingProxyType({UiLocale.RU: "Развернуть", UiLocale.EN: "Expand"}),
        "navigation.expand_name": MappingProxyType(
            {UiLocale.RU: "Развернуть навигацию", UiLocale.EN: "Expand navigation"}
        ),
        "placeholder.context": MappingProxyType({UiLocale.RU: "Контекст", UiLocale.EN: "Context"}),
        "placeholder.help": MappingProxyType({UiLocale.RU: "Справка", UiLocale.EN: "Help"}),
        "placeholder.inspector": MappingProxyType(
            {UiLocale.RU: "Инспектор: {label}", UiLocale.EN: "Inspector: {label}"}
        ),
        "placeholder.inspector.detail": MappingProxyType(
            {
                UiLocale.RU: "Оболочка UI V2 не создаёт скрытые команды или параметры устройства.",
                UiLocale.EN: "The V2 shell does not create hidden commands or device parameters.",
            }
        ),
        "placeholder.inspector.primary": MappingProxyType(
            {UiLocale.RU: "Недоступно", UiLocale.EN: "Unavailable"}
        ),
        "placeholder.inspector.title": MappingProxyType(
            {UiLocale.RU: "Параметры появятся позднее", UiLocale.EN: "Parameters will appear later"}
        ),
        "placeholder.workspace.detail": MappingProxyType(
            {
                UiLocale.RU: "Эта страница пока не выполняет поиск, настройку или запуск приёмника.",
                UiLocale.EN: "This page does not yet discover, configure, or start a receiver.",
            }
        ),
        "placeholder.workspace.primary": MappingProxyType(
            {UiLocale.RU: "Недоступно до следующего пакета", UiLocale.EN: "Unavailable until the next package"}
        ),
        "placeholder.workspace.secondary": MappingProxyType(
            {UiLocale.RU: "Открыть описание", UiLocale.EN: "Open description"}
        ),
        "placeholder.workspace.title": MappingProxyType(
            {UiLocale.RU: "Рабочее пространство готовится", UiLocale.EN: "Workspace is being prepared"}
        ),
        "workspace.calibration.description": MappingProxyType(
            {
                UiLocale.RU: "Калибровка появится в UI2-09",
                UiLocale.EN: "Calibration will appear in UI2-09",
            }
        ),
        "workspace.calibration.label": MappingProxyType(
            {UiLocale.RU: "Калибровка", UiLocale.EN: "Calibration"}
        ),
        "workspace.diagnostics.description": MappingProxyType(
            {
                UiLocale.RU: "Диагностика появится в UI2-10",
                UiLocale.EN: "Diagnostics will appear in UI2-10",
            }
        ),
        "workspace.diagnostics.label": MappingProxyType(
            {UiLocale.RU: "Диагностика", UiLocale.EN: "Diagnostics"}
        ),
        "workspace.home.description": MappingProxyType(
            {UiLocale.RU: "Статус и быстрый старт", UiLocale.EN: "Status and quick start"}
        ),
        "workspace.home.label": MappingProxyType(
            {UiLocale.RU: "Главная", UiLocale.EN: "Home"}
        ),
        "workspace.live.description": MappingProxyType(
            {
                UiLocale.RU: "Приём в реальном времени появится в UI2-07",
                UiLocale.EN: "Live analysis will appear in UI2-07",
            }
        ),
        "workspace.live.label": MappingProxyType(
            {UiLocale.RU: "Приём", UiLocale.EN: "Receive"}
        ),
        "workspace.recording.description": MappingProxyType(
            {
                UiLocale.RU: "Запись доступна после рабочего пространства Приём",
                UiLocale.EN: "Recording is available after the Live workspace",
            }
        ),
        "workspace.recording.label": MappingProxyType(
            {UiLocale.RU: "Запись", UiLocale.EN: "Recording"}
        ),
        "workspace.replay.description": MappingProxyType(
            {
                UiLocale.RU: "Воспроизведение появится в UI2-10",
                UiLocale.EN: "Replay will appear in UI2-10",
            }
        ),
        "workspace.replay.label": MappingProxyType(
            {UiLocale.RU: "Воспроизведение", UiLocale.EN: "Replay"}
        ),
        "workspace.sweep.description": MappingProxyType(
            {
                UiLocale.RU: "Обзор диапазона появится в UI2-08",
                UiLocale.EN: "Sweep will appear in UI2-08",
            }
        ),
        "workspace.sweep.label": MappingProxyType(
            {UiLocale.RU: "Обзор", UiLocale.EN: "Sweep"}
        ),
        "shell.appearance.name": MappingProxyType(
            {UiLocale.RU: "Открыть настройки внешнего вида и компоновки UI V2", UiLocale.EN: "Open UI V2 appearance and layout settings"}
        ),
        "shell.appearance.text": MappingProxyType({UiLocale.RU: "Вид", UiLocale.EN: "View"}),
        "shell.appearance.tooltip": MappingProxyType(
            {UiLocale.RU: "Тема и безопасный сброс компоновки UI V2", UiLocale.EN: "UI V2 theme and safe layout reset"}
        ),
        "shell.auto_search": MappingProxyType(
            {UiLocale.RU: "Автопоиск: {state}", UiLocale.EN: "Auto-discovery: {state}"}
        ),
        "shell.auto_search_disabled": MappingProxyType({UiLocale.RU: "выключен", UiLocale.EN: "off"}),
        "shell.auto_search_enabled": MappingProxyType({UiLocale.RU: "включён", UiLocale.EN: "on"}),
        "shell.auto_search_detail": MappingProxyType(
            {UiLocale.RU: "Показывает только сохранённую политику; оболочка не запускает поиск.", UiLocale.EN: "Shows only the saved policy; the shell does not start discovery."}
        ),
        "shell.close_requires_stop": MappingProxyType(
            {UiLocale.RU: "Закрытие требует завершить активную операцию", UiLocale.EN: "Close requires the active operation to finish"}
        ),
        "shell.context_inspector": MappingProxyType(
            {UiLocale.RU: "Контекстный инспектор", UiLocale.EN: "Context inspector"}
        ),
        "shell.drawer_context_inspector": MappingProxyType(
            {UiLocale.RU: "Контекстный инспектор в выдвижной панели", UiLocale.EN: "Context inspector in slide-out drawer"}
        ),
        "analyzer.applying": MappingProxyType({UiLocale.RU: "Применение…", UiLocale.EN: "Applying…"}),
        "analyzer.quick.name": MappingProxyType({UiLocale.RU: "Частота и разрешение", UiLocale.EN: "Frequency and resolution"}),
        "analyzer.rtbw_rates": MappingProxyType({UiLocale.RU: "FFT/с: {fft} · Публ./с: {publications} · I/Q MS/s: {iq}", UiLocale.EN: "FFT/s: {fft} · Pub/s: {publications} · I/Q MS/s: {iq}"}),
        "analyzer.quick.center": MappingProxyType({UiLocale.RU: "Центр, МГц", UiLocale.EN: "Center, MHz"}),
        "analyzer.quick.span": MappingProxyType({UiLocale.RU: "Вид, МГц", UiLocale.EN: "View, MHz"}),
        "analyzer.quick.fft": MappingProxyType({UiLocale.RU: "FFT", UiLocale.EN: "FFT"}),
        "analyzer.quick.gain": MappingProxyType({UiLocale.RU: "Gain, дБ", UiLocale.EN: "Gain, dB"}),
        "analyzer.quick.span_hint": MappingProxyType({UiLocale.RU: "Только видимый диапазон графика. Не меняет частоту, Fs или настройки SDR.", UiLocale.EN: "Visible plot span only. Does not change tuning, Fs or SDR configuration."}),
        "analyzer.quick.rf_hint": MappingProxyType({UiLocale.RU: "Черновик измерения. Изменение вступит в силу только после явного Применить и подтверждения устройства; во время приёма требуется сначала Stop.", UiLocale.EN: "Measurement draft. An edit takes effect only after explicit Apply and device confirmation; stop acquisition first."}),
        "waterfall.time_axis.unknown": MappingProxyType({UiLocale.RU: "Время неизвестно", UiLocale.EN: "Time unknown"}),
        "waterfall.time_axis.host": MappingProxyType({UiLocale.RU: "Время хоста (не RF)", UiLocale.EN: "Host time (not RF)"}),
        "analyzer.applied_prefix": MappingProxyType({UiLocale.RU: "Применено:", UiLocale.EN: "Applied:"}),
        "analyzer.prepared_prefix": MappingProxyType({UiLocale.RU: "Подготовлено — без проверки устройства:", UiLocale.EN: "Prepared — no RF readback:"}),
        "analyzer.start_requires_configuration": MappingProxyType({UiLocale.RU: "Выберите устройство и подтвердите настройки перед запуском.", UiLocale.EN: "Select a device and submit its settings before starting."}),
        "analyzer.rf_readback_prefix": MappingProxyType({UiLocale.RU: "Считано с устройства:", UiLocale.EN: "RF readback:"}),
        "analyzer.applied_capture_range": MappingProxyType({UiLocale.RU: "Полоса по применённым center/Fs: {lower}–{upper} МГц. Это не подтверждение полезной полосы или наличия данных; FFT обозначает физический размер преобразования.", UiLocale.EN: "Band from applied center/Fs: {lower}–{upper} MHz. This does not prove usable bandwidth or data availability; FFT denotes the physical transform size."}),
        "analyzer.rx.observed": MappingProxyType({UiLocale.RU: "Наблюдаемые цифровые цепи: {selections}. Это не применённый выбор и не проверка радиотракта.", UiLocale.EN: "Observed digital chains: {selections}. This is not an applied selection or RF-path verification."}),
        "analyzer.running": MappingProxyType({UiLocale.RU: "Приём", UiLocale.EN: "Acquiring"}),
        "analyzer.idle": MappingProxyType({UiLocale.RU: "Остановлено", UiLocale.EN: "Stopped"}),
        "analyzer.stopped_last": MappingProxyType({UiLocale.RU: "Остановлено — последний кадр", UiLocale.EN: "Stopped — last frame"}),
        "analyzer.failed": MappingProxyType({UiLocale.RU: "Ошибка", UiLocale.EN: "Error"}),
        "analyzer.failed_last": MappingProxyType({UiLocale.RU: "Ошибка — последние данные, не текущие", UiLocale.EN: "Error — last data, not current"}),
        "analyzer.quality_unknown": MappingProxyType({UiLocale.RU: "Качество: неизвестно", UiLocale.EN: "Quality: unknown"}),
        "analyzer.quality_mask": MappingProxyType({UiLocale.RU: "Флаги качества: {mask}", UiLocale.EN: "Quality flags: {mask}"}),
        "analyzer.frame_loss": MappingProxyType({UiLocale.RU: "Потери перед кадром: {samples} отсч., {blocks} блоков, {fft} FFT", UiLocale.EN: "Loss before frame: {samples} samples, {blocks} blocks, {fft} FFT"}),
        "analyzer.partial": MappingProxyType({UiLocale.RU: "Неполное покрытие", UiLocale.EN: "Partial coverage"}),
        "analyzer.completed": MappingProxyType({UiLocale.RU: "Проход завершён", UiLocale.EN: "Pass completed"}),
        "analyzer.gapped": MappingProxyType({UiLocale.RU: "Проход с разрывом", UiLocale.EN: "Gapped pass"}),
        "analyzer.terminal_progress": MappingProxyType({UiLocale.RU: "Участки: {received}/{total}", UiLocale.EN: "Segments: {received}/{total}"}),
        "shell.inspector.drawer_tooltip": MappingProxyType({UiLocale.RU: "Открыть инспектор поверх графика; приём и масштаб спектра не изменятся", UiLocale.EN: "Open the inspector over the plot without changing acquisition or spectrum zoom"}),
        "shell.inspector.hide": MappingProxyType({UiLocale.RU: "Скрыть инспектор", UiLocale.EN: "Hide inspector"}),
        "shell.inspector.hide_name": MappingProxyType(
            {UiLocale.RU: "Скрыть контекстный инспектор", UiLocale.EN: "Hide context inspector"}
        ),
        "shell.inspector.show": MappingProxyType({UiLocale.RU: "Показать инспектор", UiLocale.EN: "Show inspector"}),
        "shell.inspector.show_name": MappingProxyType(
            {UiLocale.RU: "Показать контекстный инспектор", UiLocale.EN: "Show context inspector"}
        ),
        "shell.inspector.tooltip": MappingProxyType(
            {UiLocale.RU: "Показать или скрыть контекстный инспектор", UiLocale.EN: "Show or hide the context inspector"}
        ),
        "shell.inspector.narrow_tooltip": MappingProxyType(
            {UiLocale.RU: "Показать или скрыть контекстный инспектор в выдвижной панели", UiLocale.EN: "Show or hide the context inspector in the slide-out drawer"}
        ),
        "shell.navigation": MappingProxyType({UiLocale.RU: "Навигация UI V2", UiLocale.EN: "UI V2 navigation"}),
        "shell.status.boundary": MappingProxyType(
            {UiLocale.RU: "Навигация не запускает устройство", UiLocale.EN: "Navigation does not start a device"}
        ),
        "shell.status.detail": MappingProxyType(
            {UiLocale.RU: "В UI2-03 все рабочие пространства пока служат заглушками.", UiLocale.EN: "In UI2-03 all workspaces are inert placeholders."}
        ),
        "shell.status.label": MappingProxyType({UiLocale.RU: "UI V2", UiLocale.EN: "UI V2"}),
        "shell.status.layout_reset": MappingProxyType(
            {UiLocale.RU: "Компоновка UI V2 сброшена", UiLocale.EN: "UI V2 layout reset"}
        ),
        "shell.status.settings_reset": MappingProxyType(
            {UiLocale.RU: "Настройки оболочки UI V2 сброшены", UiLocale.EN: "UI V2 shell settings reset"}
        ),
        "shell.status.theme": MappingProxyType(
            {UiLocale.RU: "Тема UI V2: {theme}", UiLocale.EN: "UI V2 theme: {theme}"}
        ),
        "shell.status.visuals_reset": MappingProxyType(
            {UiLocale.RU: "Вид UI V2 сброшен", UiLocale.EN: "UI V2 appearance reset"}
        ),
        "shell.title": MappingProxyType(
            {UiLocale.RU: "SDR Native Monitoring — UI V2", UiLocale.EN: "SDR Native Monitoring — UI V2"}
        ),
        "shell.workspace": MappingProxyType({UiLocale.RU: "Рабочее пространство UI V2", UiLocale.EN: "UI V2 workspace"}),
        "shell.workspace_already_registered": MappingProxyType(
            {UiLocale.RU: "Рабочее пространство UI V2 уже зарегистрировано", UiLocale.EN: "V2 workspace is already registered"}
        ),
        "shell.workspace_registration_rejected": MappingProxyType(
            {UiLocale.RU: "Регистрация рабочего пространства UI V2 отклонена", UiLocale.EN: "V2 workspace registration rejected"}
        ),
        "spectrum.accessible.description": MappingProxyType(
            {
                UiLocale.RU: "Единая область спектра: M1/M2 мышью, 1/2 выбор маркера, P — пик, скобки — соседний пик.",
                UiLocale.EN: "One spectrum area: M1/M2 by mouse, 1/2 selects a marker, P selects a peak, brackets select the adjacent peak.",
            }
        ),
        "spectrum.accessible.name": MappingProxyType(
            {UiLocale.RU: "Спектральная сцена", UiLocale.EN: "Spectrum scene"}
        ),
        "spectrum.cursor.empty": MappingProxyType(
            {UiLocale.RU: "Курсор: —", UiLocale.EN: "Cursor: —"}
        ),
        "spectrum.cursor.name": MappingProxyType(
            {UiLocale.RU: "Курсор спектра", UiLocale.EN: "Spectrum cursor"}
        ),
        "spectrum.cursor.value": MappingProxyType(
            {UiLocale.RU: "Курсор: {frequency}, {value:.2f} {unit}", UiLocale.EN: "Cursor: {frequency}, {value:.2f} {unit}"}
        ),
        "spectrum.empty.detail": MappingProxyType(
            {
                UiLocale.RU: "Сцена ожидает опубликованный неизменяемый кадр. Приёмник не запускается.",
                UiLocale.EN: "The scene awaits a public immutable frame. The receiver does not start.",
            }
        ),
        "spectrum.empty.primary": MappingProxyType(
            {UiLocale.RU: "Ожидание", UiLocale.EN: "Waiting"}
        ),
        "spectrum.empty.secondary": MappingProxyType(
            {UiLocale.RU: "Подробнее", UiLocale.EN: "Details"}
        ),
        "spectrum.empty.title": MappingProxyType(
            {UiLocale.RU: "Нет спектрального кадра", UiLocale.EN: "No spectrum frame"}
        ),
        "spectrum.persistence.clear": MappingProxyType(
            {UiLocale.RU: "Очистить отображение", UiLocale.EN: "Clear display"}
        ),
        "spectrum.persistence.clear.name": MappingProxyType(
            {UiLocale.RU: "Очистить только локальное отображение накопления", UiLocale.EN: "Clear only the persistence display"}
        ),
        "spectrum.persistence.log": MappingProxyType(
            {UiLocale.RU: "Логарифмическая плотность", UiLocale.EN: "Log density"}
        ),
        "spectrum.persistence.log.name": MappingProxyType(
            {UiLocale.RU: "Логарифмическая шкала плотности", UiLocale.EN: "Logarithmic density scale"}
        ),
        "spectrum.persistence.log.help": MappingProxyType(
            {UiLocale.RU: "Цвет = log10(1 + 9999 × p) / 4. Для вероятности p — исходное значение; для числа попаданий p — доля от максимума кадра. Ноль прозрачен, p=1 соответствует максимуму шкалы. Измеренная плотность не изменяется.",
             UiLocale.EN: "Color = log10(1 + 9999 × p) / 4. For probability, p is the original value; for counts, p is a fraction of the frame maximum. Zero is transparent; p=1 is full scale. Measured density is unchanged."}
        ),
        "analyzer.numerical_unknown": MappingProxyType(
            {UiLocale.RU: "Численные метаданные кадра не опубликованы.",
             UiLocale.EN: "Frame numerical metadata is not published."}
        ),
        "analyzer.numerical_metadata": MappingProxyType(
            {UiLocale.RU: "Метаданные кадра: окно {window}; детектор {detector}; накопление {averaging} FFT; точность {precision}\nШаг FFT {bin_width} Гц; ENBW {enbw} Гц; номинальная RBW {rbw} Гц\nКалибровка {calibration}; профиль {profile}; неопределённость {uncertainty} дБ",
             UiLocale.EN: "Frame metadata: window {window}; detector {detector}; accumulation {averaging} FFT; precision {precision}\nFFT bin width {bin_width} Hz; ENBW {enbw} Hz; nominal RBW {rbw} Hz\nCalibration {calibration}; profile {profile}; uncertainty {uncertainty} dB"}
        ),
        "spectrum.persistence.mode.direct": MappingProxyType(
            {UiLocale.RU: "Прямой", UiLocale.EN: "Direct"}
        ),
        "spectrum.persistence.mode.name": MappingProxyType(
            {UiLocale.RU: "Режим отображения накопления", UiLocale.EN: "Persistence display mode"}
        ),
        "spectrum.persistence.mode.visual": MappingProxyType(
            {UiLocale.RU: "Визуальный", UiLocale.EN: "Visual"}
        ),
        "spectrum.persistence.no_data": MappingProxyType(
            {UiLocale.RU: "Нет данных", UiLocale.EN: "No data"}
        ),
        "spectrum.persistence.no_density": MappingProxyType(
            {UiLocale.RU: "Накопление: нет плотности", UiLocale.EN: "Persistence: no density"}
        ),
        "spectrum.persistence.scale.linear": MappingProxyType(
            {UiLocale.RU: "линейная плотность", UiLocale.EN: "linear density"}
        ),
        "spectrum.persistence.scale.log": MappingProxyType(
            {UiLocale.RU: "логарифмическая плотность", UiLocale.EN: "log density"}
        ),
        "spectrum.persistence.status": MappingProxyType(
            {UiLocale.RU: "Накопление: {mode} · {scale}{suffix}", UiLocale.EN: "Persistence: {mode} · {scale}{suffix}"}
        ),
        "spectrum.persistence.status_cleared": MappingProxyType(
            {UiLocale.RU: "Накопление: локальное отображение очищено", UiLocale.EN: "Persistence: display cleared locally"}
        ),
        "spectrum.persistence.suffix.direct": MappingProxyType(
            {UiLocale.RU: " · ПРЯМОЙ", UiLocale.EN: " · DIRECT"}
        ),
        "spectrum.persistence.suffix.visual": MappingProxyType(
            {UiLocale.RU: " · ВИЗУАЛЬНЫЙ", UiLocale.EN: " · VISUAL"}
        ),
        "spectrum.persistence.visible": MappingProxyType(
            {UiLocale.RU: "Накопление", UiLocale.EN: "Persistence"}
        ),
        "spectrum.persistence.visible.name": MappingProxyType(
            {UiLocale.RU: "Показывать плотность накопления", UiLocale.EN: "Show persistence density"}
        ),
        "spectrum.range.auto": MappingProxyType(
            {UiLocale.RU: "Автоуровень", UiLocale.EN: "Auto level"}
        ),
        "spectrum.range.auto.name": MappingProxyType(
            {UiLocale.RU: "Автоматический вертикальный диапазон", UiLocale.EN: "Automatic vertical range"}
        ),
        "spectrum.range.db_per_division": MappingProxyType(
            {UiLocale.RU: "дБ на деление", UiLocale.EN: "dB per division"}
        ),
        "spectrum.range.lock": MappingProxyType(
            {UiLocale.RU: "Зафиксировать", UiLocale.EN: "Lock"}
        ),
        "spectrum.range.lock.name": MappingProxyType(
            {UiLocale.RU: "Зафиксировать вертикальный диапазон", UiLocale.EN: "Lock vertical range"}
        ),
        "spectrum.range.locked": MappingProxyType(
            {UiLocale.RU: "Уровень зафиксирован", UiLocale.EN: "Level locked"}
        ),
        "spectrum.range.manual": MappingProxyType(
            {UiLocale.RU: "Ручной уровень", UiLocale.EN: "Manual level"}
        ),
        "spectrum.range.name": MappingProxyType(
            {UiLocale.RU: "Вертикальный диапазон", UiLocale.EN: "Vertical range"}
        ),
        "spectrum.range.reference": MappingProxyType(
            {UiLocale.RU: "Опорный уровень", UiLocale.EN: "Reference level"}
        ),
        "spectrum.range.summary": MappingProxyType(
            {
                UiLocale.RU: "{mode}: Опорный {reference:.1f}, {division:.1f} дБ/дел",
                UiLocale.EN: "{mode}: Ref {reference:.1f}, {division:.1f} dB/div",
            }
        ),
        "spectrum.shortcuts.auto_tooltip": MappingProxyType(
            {UiLocale.RU: "A — автоуровень один раз", UiLocale.EN: "A — auto level once"}
        ),
        "spectrum.shortcuts.body": MappingProxyType(
            {
                UiLocale.RU: (
                    "Space — Пуск/Стоп, только когда сцена спектра в фокусе и действие доступно.\n"
                    "M — установить выбранный маркер в центр видимого диапазона.\n"
                    "1 / 2 — выбрать M1 / M2; P — максимум; [ / ] — соседний пик.\n"
                    "A — автоуровень один раз; 0 — полный диапазон кадра и автоуровень.\n"
                    "Esc — закрыть эту панель; F1 — открыть или закрыть справку.\n"
                    "Ctrl+R — недоступно: контракт записи ещё не подключён к UI V2."
                ),
                UiLocale.EN: (
                    "Space — Start/Stop only while the spectrum scene is focused and the action is available.\n"
                    "M — place the selected marker at the centre of the visible span.\n"
                    "1 / 2 — select M1 / M2; P — maximum; [ / ] — adjacent peak.\n"
                    "A — auto level once; 0 — full frame span and auto level.\n"
                    "Esc — close this panel; F1 — open or close shortcut help.\n"
                    "Ctrl+R — unavailable: the recording contract is not connected to UI V2."
                ),
            }
        ),
        "spectrum.shortcuts.button": MappingProxyType(
            {UiLocale.RU: "Клавиши", UiLocale.EN: "Keys"}
        ),
        "spectrum.shortcuts.name": MappingProxyType(
            {UiLocale.RU: "Справка по клавишам спектра", UiLocale.EN: "Spectrum keyboard help"}
        ),
        "spectrum.shortcuts.summary": MappingProxyType(
            {
                UiLocale.RU: "Клавиши: Space Пуск/Стоп; M маркер; P пик; A авто; 0 сброс; F1 справка.",
                UiLocale.EN: "Keys: Space Start/Stop; M marker; P peak; A auto; 0 reset; F1 help.",
            }
        ),
        "spectrum.shortcuts.title": MappingProxyType(
            {UiLocale.RU: "Клавиши спектра", UiLocale.EN: "Spectrum shortcuts"}
        ),
        "spectrum.trace.average": MappingProxyType(
            {UiLocale.RU: "Среднее", UiLocale.EN: "Average"}
        ),
        "spectrum.trace.current": MappingProxyType(
            {UiLocale.RU: "Текущий", UiLocale.EN: "Current"}
        ),
        "spectrum.trace.maximum": MappingProxyType(
            {UiLocale.RU: "Максимум", UiLocale.EN: "Maximum"}
        ),
        "spectrum.trace.minimum": MappingProxyType(
            {UiLocale.RU: "Минимум", UiLocale.EN: "Minimum"}
        ),
        "spectrum.unit.exact_description": MappingProxyType(
            {UiLocale.RU: "Точная единица кадра: {unit}", UiLocale.EN: "Exact frame unit: {unit}"}
        ),
        "spectrum.unit.name": MappingProxyType(
            {UiLocale.RU: "Единица спектра", UiLocale.EN: "Spectrum unit"}
        ),
        "spectrum.unit.no_frame": MappingProxyType(
            {UiLocale.RU: "Единица: нет кадра", UiLocale.EN: "Unit: no frame"}
        ),
        "spectrum.unit.readout": MappingProxyType(
            {UiLocale.RU: "Единица: {unit}", UiLocale.EN: "Unit: {unit}"}
        ),
        "spectrum.warning.name": MappingProxyType(
            {UiLocale.RU: "Предупреждение спектра", UiLocale.EN: "Spectrum warning"}
        ),
        "theme.dark": MappingProxyType({UiLocale.RU: "Тёмная", UiLocale.EN: "Dark"}),
        "theme.high_contrast": MappingProxyType({UiLocale.RU: "Высокий контраст", UiLocale.EN: "High contrast"}),
        "theme.light": MappingProxyType({UiLocale.RU: "Светлая", UiLocale.EN: "Light"}),
        "waterfall.accessible.description": MappingProxyType(
            {
                UiLocale.RU: "Ограниченная локальная история спектральных строк. Очистка и пауза не управляют приёмником.",
                UiLocale.EN: "Bounded local spectrum-row history. Clear and pause do not control the receiver.",
            }
        ),
        "waterfall.accessible.name": MappingProxyType(
            {UiLocale.RU: "Водопад", UiLocale.EN: "Waterfall"}
        ),
        "waterfall.age.milliseconds": MappingProxyType(
            {UiLocale.RU: "−{value:.0f} мс", UiLocale.EN: "−{value:.0f} ms"}
        ),
        "waterfall.age.minutes": MappingProxyType(
            {UiLocale.RU: "−{value:.1f} мин", UiLocale.EN: "−{value:.1f} min"}
        ),
        "waterfall.age.seconds": MappingProxyType(
            {UiLocale.RU: "−{value:.1f} с", UiLocale.EN: "−{value:.1f} s"}
        ),
        "waterfall.axis.frequency": MappingProxyType(
            {UiLocale.RU: "Частота", UiLocale.EN: "Frequency"}
        ),
        "waterfall.axis.time": MappingProxyType(
            {UiLocale.RU: "Время", UiLocale.EN: "Time"}
        ),
        "waterfall.clear": MappingProxyType(
            {UiLocale.RU: "Очистить водопад", UiLocale.EN: "Clear waterfall"}
        ),
        "waterfall.clear.name": MappingProxyType(
            {UiLocale.RU: "Очистить только локальную историю водопада", UiLocale.EN: "Clear only local waterfall history"}
        ),
        "waterfall.direction.bottom": MappingProxyType(
            {UiLocale.RU: "Новые снизу", UiLocale.EN: "Newest at bottom"}
        ),
        "waterfall.direction.name": MappingProxyType(
            {UiLocale.RU: "Положение самой новой строки водопада", UiLocale.EN: "Position of the newest waterfall row"}
        ),
        "waterfall.direction.top": MappingProxyType(
            {UiLocale.RU: "Новые сверху", UiLocale.EN: "Newest at top"}
        ),
        "waterfall.follow": MappingProxyType(
            {UiLocale.RU: "Следовать уровню спектра", UiLocale.EN: "Follow spectrum level"}
        ),
        "waterfall.follow.name": MappingProxyType(
            {
                UiLocale.RU: "Следовать отображаемым уровням спектра при совпадающей единице",
                UiLocale.EN: "Follow displayed spectrum levels only when the unit matches",
            }
        ),
        "waterfall.freeze": MappingProxyType(
            {UiLocale.RU: "Пауза кадров", UiLocale.EN: "Pause frames"}
        ),
        "waterfall.freeze.name": MappingProxyType(
            {UiLocale.RU: "Заморозить локальное добавление строк водопада", UiLocale.EN: "Freeze local waterfall-row admission"}
        ),
        "waterfall.history.name": MappingProxyType(
            {UiLocale.RU: "Глубина истории водопада в секундах", UiLocale.EN: "Waterfall history depth in seconds"}
        ),
        "waterfall.history.prefix": MappingProxyType(
            {UiLocale.RU: "История: ", UiLocale.EN: "History: "}
        ),
        "waterfall.history.suffix": MappingProxyType(
            {UiLocale.RU: " с", UiLocale.EN: " s"}
        ),
        "waterfall.level.maximum.name": MappingProxyType(
            {UiLocale.RU: "Верхний уровень водопада", UiLocale.EN: "Waterfall upper level"}
        ),
        "waterfall.level.minimum.name": MappingProxyType(
            {UiLocale.RU: "Нижний уровень водопада", UiLocale.EN: "Waterfall lower level"}
        ),
        "waterfall.new_grid": MappingProxyType(
            {UiLocale.RU: "Водопад: новая сетка · эпоха {epoch}", UiLocale.EN: "Waterfall: new grid · epoch {epoch}"}
        ),
        "waterfall.palette.name": MappingProxyType(
            {UiLocale.RU: "Палитра водопада, только отображение", UiLocale.EN: "Waterfall palette, presentation only"}
        ),
        "waterfall.palette.cividis": MappingProxyType(
            {UiLocale.RU: "Cividis", UiLocale.EN: "Cividis"}
        ),
        "waterfall.palette.grayscale": MappingProxyType(
            {UiLocale.RU: "Оттенки серого", UiLocale.EN: "Grayscale"}
        ),
        "waterfall.palette.turbo": MappingProxyType(
            {UiLocale.RU: "Turbo", UiLocale.EN: "Turbo"}
        ),
        "waterfall.palette.viridis": MappingProxyType(
            {UiLocale.RU: "Viridis", UiLocale.EN: "Viridis"}
        ),
        "waterfall.rows_per_second.item": MappingProxyType(
            {UiLocale.RU: "{value} строк/с", UiLocale.EN: "{value} rows/s"}
        ),
        "waterfall.rows_per_second.name": MappingProxyType(
            {UiLocale.RU: "Строк в секунду водопада", UiLocale.EN: "Waterfall rows per second"}
        ),
        "waterfall.status.cleared": MappingProxyType(
            {UiLocale.RU: "Водопад: локальная история очищена", UiLocale.EN: "Waterfall: local history cleared"}
        ),
        "waterfall.status.frozen": MappingProxyType(
            {UiLocale.RU: "Водопад: пауза · {rows} строк", UiLocale.EN: "Waterfall: paused · {rows} rows"}
        ),
        "waterfall.status.name": MappingProxyType(
            {UiLocale.RU: "Статус водопада", UiLocale.EN: "Waterfall status"}
        ),
        "waterfall.status.ready": MappingProxyType(
            {
                UiLocale.RU: "Водопад: эпоха {epoch} · {rows} строк · {unit}",
                UiLocale.EN: "Waterfall: epoch {epoch} · {rows} rows · {unit}",
            }
        ),
        "waterfall.status.waiting": MappingProxyType(
            {UiLocale.RU: "Водопад: ожидание строк", UiLocale.EN: "Waterfall: waiting for rows"}
        ),
        "waterfall.view.accessible.description": MappingProxyType(
            {
                UiLocale.RU: "Единая композиция спектра сверху и локального водопада снизу",
                UiLocale.EN: "One composition with spectrum above and local waterfall below",
            }
        ),
        "waterfall.view.accessible.name": MappingProxyType(
            {UiLocale.RU: "Спектр и водопад", UiLocale.EN: "Spectrum and waterfall"}
        ),
        "waterfall.visible": MappingProxyType(
            {UiLocale.RU: "Водопад", UiLocale.EN: "Waterfall"}
        ),
        "waterfall.visible.name": MappingProxyType(
            {UiLocale.RU: "Показывать водопад", UiLocale.EN: "Show waterfall"}
        ),
    }
)


def text(key: str, locale: UiLocale | None = None, /, **parameters: object) -> str:
    """Resolve one catalog key exactly; missing keys or parameters fail closed."""

    resolved_locale = current_locale() if locale is None else locale
    try:
        template = _CATALOG[key][resolved_locale]
    except KeyError as error:
        raise KeyError(f"missing UI V2 translation: {key!r} / {resolved_locale.value}") from error
    try:
        return template.format(**parameters)
    except (KeyError, IndexError, ValueError) as error:
        raise ValueError(f"invalid parameters for UI V2 translation {key!r}") from error


def enum_text(prefix: str, value: object, locale: UiLocale | None = None, /) -> str:
    """Resolve a display-only enum label without leaking its wire value into UI."""

    raw_value = getattr(value, "value", value)
    if not isinstance(prefix, str) or not prefix or not isinstance(raw_value, str) or not raw_value:
        raise ValueError("UI V2 enum label requires a non-empty prefix and value")
    return text(f"{prefix}.{raw_value}", locale)


def current_locale() -> UiLocale:
    """Return this UI thread's active presentation locale."""

    return _ACTIVE_LOCALE.get()


def set_active_locale(locale: UiLocale) -> None:
    """Set the UI-thread locale; callers must rebuild existing widgets explicitly."""

    if not isinstance(locale, UiLocale):
        raise TypeError("UI V2 active locale must be UiLocale")
    _ACTIVE_LOCALE.set(locale)


def resolve_locale(raw: object) -> UiLocale:
    """Return the safe Russian default for absent or malformed V2 preferences."""

    try:
        return UiLocale(str(raw))
    except ValueError:
        return UiLocale.RU


def catalog_keys() -> frozenset[str]:
    """Expose immutable catalog membership for deterministic localization tests."""

    return frozenset(_CATALOG)


def _template_fields(template: str) -> frozenset[str]:
    return frozenset(field_name for _, field_name, _, _ in Formatter().parse(template) if field_name)


def _validate_catalog() -> None:
    for key, entry in _CATALOG.items():
        if set(entry) != set(UiLocale):
            raise ValueError(f"UI V2 translation {key!r} must define every supported locale")
        fields = {_template_fields(template) for template in entry.values()}
        if len(fields) != 1:
            raise ValueError(f"UI V2 translation {key!r} has inconsistent format fields")
        if any(not value.strip() for value in entry.values()):
            raise ValueError(f"UI V2 translation {key!r} must not be blank")


_validate_catalog()
