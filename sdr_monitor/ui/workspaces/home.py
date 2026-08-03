"""Home workspace: a short, action-oriented route into a live session."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..components import EmptyState, SectionCard, StatusChip
from ..design_tokens import StatusTone


class HomeWorkspace(QWidget):
    discover_requested = Signal()
    live_requested = Signal()
    sweep_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel("Добро пожаловать")
        title.setProperty("role", "heading")
        layout.addWidget(title)
        layout.addWidget(QLabel("Подключите приёмник и получите спектр за два действия."))
        actions = QHBoxLayout()
        discover = QPushButton("Найти устройства")
        discover.setAccessibleName("Найти SDR устройства")
        discover.clicked.connect(self.discover_requested)
        live = QPushButton("Открыть мониторинг")
        live.setAccessibleName("Открыть Live Monitor")
        live.clicked.connect(self.live_requested)
        sweep = QPushButton("Создать обзор диапазона")
        sweep.setAccessibleName("Создать Wideband Sweep")
        sweep.clicked.connect(self.sweep_requested)
        for button in (discover, live, sweep):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        status = SectionCard("Состояние")
        row = QGridLayout()
        for index, (label, tone) in enumerate((("Native core: ожидает проверки", StatusTone.NEUTRAL), ("CPU: доступен", StatusTone.SUCCESS), ("Устройства: не обнаружены", StatusTone.NEUTRAL))):
            row.addWidget(StatusChip(label, tone), 0, index)
        status.content.addLayout(row)
        layout.addWidget(status)
        profiles = SectionCard("Последние профили")
        profiles.add_widget(EmptyState("Нет сохранённых профилей", "Создайте или выберите профиль после подключения устройства."))
        layout.addWidget(profiles)
        layout.addStretch(1)

    def shutdown(self) -> None:
        """Home owns no live resources."""
