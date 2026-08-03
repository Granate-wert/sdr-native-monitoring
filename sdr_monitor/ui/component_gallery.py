"""Developer gallery for visual regression and manual theme review."""

from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QWidget

from .components import AppliedValueRow, EmptyState, ErrorState, MeasurementCard, NumericReadout, SectionCard, StatusChip, TaskProgress
from .design_tokens import StatusTone


def build_component_gallery(parent: QWidget | None = None) -> QWidget:
    gallery = QWidget(parent)
    layout = QGridLayout(gallery)
    for column, tone in enumerate(StatusTone):
        layout.addWidget(StatusChip(tone.value, tone), 0, column)
    card = MeasurementCard()
    card.set_values("PEAK", "-52.34", "dBFS/bin", meta="2.412 GHz", quality="Калибровка не применена")
    layout.addWidget(card, 1, 0)
    applied = AppliedValueRow("Полоса")
    applied.set_values("20.000 MHz", "19.999 MHz")
    layout.addWidget(applied, 1, 1)
    readout = NumericReadout("Частота")
    readout.set_value(2.4, "GHz", decimals=3)
    layout.addWidget(readout, 1, 2)
    section = SectionCard("Состояние")
    section.add_widget(EmptyState("Нет записей", "Создайте запись в отдельном рабочем пространстве."))
    layout.addWidget(section, 2, 0, 1, 2)
    progress = TaskProgress()
    progress.set_progress(42, "Обработка")
    layout.addWidget(progress, 2, 2)
    layout.addWidget(ErrorState("Не удалось выполнить операцию."), 3, 0, 1, 2)
    return gallery
