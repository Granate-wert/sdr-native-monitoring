"""Live Monitor presentation shell. Device I/O remains outside this widget."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget

from ...domain import BackendKind, LiveConfiguration, LiveProfile, LiveSessionState, LiveSnapshot
from ..components import AppliedValueRow, EmptyState, ErrorState, FrequencyInput, MeasurementCard, SectionCard, StatusChip
from ..design_tokens import StatusTone
from ..formatters import parse_frequency_hz
from ..presenters import LivePresenter


class LiveInspector(QTabWidget):
    """Compact Live context inspector; values always carry quality context."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device = QLabel("Приёмник не подключён")
        self._signal = QLabel("Кадры ещё не поступали")
        self._measurement = QLabel("Нет измерений без опубликованного кадра")
        self._quality = QLabel("UNCALIBRATED · dBFS/bin")
        for title, label in (
            ("Устройство", self._device),
            ("Сигнал", self._signal),
            ("Измерения", self._measurement),
            ("Качество", self._quality),
        ):
            page = QWidget()
            layout = QVBoxLayout(page)
            label.setWordWrap(True)
            layout.addWidget(label)
            layout.addStretch(1)
            self.addTab(page, title)

    def update_snapshot(self, snapshot: LiveSnapshot) -> None:
        if snapshot.device is not None:
            self._device.setText(f"{snapshot.device.label}\n{snapshot.device.uri}")
        self._signal.setText(f"Состояние: {snapshot.state.value}\nКадр: {snapshot.sequence}")
        self._measurement.setText(f"Peak / noise: ожидают численные данные\nЕдиницы: {snapshot.unit}")
        fallback = f"\n{snapshot.quality.fallback_reason}" if snapshot.quality.fallback_reason else ""
        self._quality.setText(
            f"{snapshot.quality.calibration.value.upper()} · {snapshot.quality.backend.value.upper()}\n"
            f"Dropped blocks: {snapshot.quality.dropped_blocks}{fallback}"
        )

class LiveMonitorWorkspace(QWidget):
    """Main visualization has priority; presenter owns all service operations."""
    discovery_requested = Signal()

    def __init__(self, presenter: LivePresenter, profiles: tuple[LiveProfile, ...] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._presenter = presenter
        self._snapshot: LiveSnapshot | None = None
        self.inspector = LiveInspector()
        self._profiles = {profile.profile_id: profile for profile in profiles}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Live Monitor"))
        self.view_preset = QComboBox()
        self.view_preset.addItems(["Spectrum + Waterfall", "Spectrum", "Persistence"])
        self.view_preset.setAccessibleName("Представление визуализации")
        toolbar.addWidget(self.view_preset)
        toolbar.addStretch(1)
        self._start_stop = QPushButton("Подключить")
        self._start_stop.setAccessibleName("Подключить или запустить мониторинг")
        self._start_stop.clicked.connect(self._request_transition)
        toolbar.addWidget(self._start_stop)
        layout.addLayout(toolbar)

        self._quality = QHBoxLayout()
        self._calibration_chip = StatusChip("UNCALIBRATED", StatusTone.WARNING)
        self._backend_chip = StatusChip("CPU", StatusTone.INFO)
        self._drops_chip = StatusChip("NO DROPS", StatusTone.SUCCESS)
        for chip in (self._calibration_chip, self._backend_chip, self._drops_chip):
            self._quality.addWidget(chip)
        self._quality.addStretch(1)
        layout.addLayout(self._quality)

        self._visual_splitter = QSplitter()
        self._visual_splitter.setOrientation(Qt.Orientation.Vertical)
        self._spectrum_panel = self._visual_panel("Spectrum", "Подключите устройство, чтобы увидеть спектр.")
        self._waterfall_panel = self._visual_panel("Waterfall", "История спектра появится после запуска.")
        self._persistence_panel = self._visual_panel("Persistence", "Накопление доступно после публикации кадров.")
        for panel in (self._spectrum_panel, self._waterfall_panel, self._persistence_panel):
            self._visual_splitter.addWidget(panel)
        self._visual_splitter.setSizes([360, 220, 160])
        self.view_preset.currentIndexChanged.connect(self._apply_view_preset)
        layout.addWidget(self._visual_splitter, 1)

        setup = SectionCard("Базовые настройки")
        form = QFormLayout()
        self.center = FrequencyInput()
        self.center.set_frequency_hz(2.4e9)
        self.sample_rate = QComboBox()
        self.sample_rate.addItems(["2 MHz", "10 MHz", "20 MHz"])
        self.gain = QDoubleSpinBox()
        self.gain.setRange(0.0, 73.0)
        self.gain.setValue(18.0)
        self.gain.setSuffix(" dB")
        self.backend = QComboBox()
        self.backend.addItems(["Auto", "CPU", "CUDA", "HIP"])
        self.profile = QComboBox()
        self.profile.addItem("Без профиля")
        for saved_profile in profiles:
            self.profile.addItem(saved_profile.title, saved_profile.profile_id)
        self.profile.currentIndexChanged.connect(self._load_profile)
        form.addRow("Центр", self.center)
        form.addRow("Полоса", self.sample_rate)
        form.addRow("Усиление", self.gain)
        form.addRow("Backend", self.backend)
        form.addRow("Профиль", self.profile)
        setup.content.addLayout(form)
        self._applied = AppliedValueRow("Полоса")
        self._applied.set_values("20.000 MHz", "ожидает устройство")
        setup.add_widget(self._applied)
        layout.addWidget(setup)

        measurements = QHBoxLayout()
        self._peak_card = MeasurementCard()
        self._peak_card.set_values("Peak", "—", "dBFS/bin", meta="Ожидается кадр", quality="UNCALIBRATED")
        self._noise_card = MeasurementCard()
        self._noise_card.set_values("Noise floor", "—", "dBFS/bin", meta="Ожидается кадр", quality="UNCALIBRATED")
        measurements.addWidget(self._peak_card)
        measurements.addWidget(self._noise_card)
        layout.addLayout(measurements)

        expert = QGroupBox("Экспертные параметры")
        expert.setCheckable(True)
        expert.setChecked(False)
        expert_layout = QVBoxLayout(expert)
        expert_layout.addWidget(EmptyState("RF / DSP / Persistence", "Расширенные параметры появятся при подключении capability-aware adapter."))
        layout.addWidget(expert)
        self._frame_status = QLabel("Кадры: ожидание")
        layout.addWidget(self._frame_status)
        self._error = ErrorState("Нет активной ошибки")
        self._error.setVisible(False)
        layout.addWidget(self._error)
        presenter.snapshot_changed.connect(self._render_snapshot)
        presenter.task_failed.connect(self._render_error)
        presenter.busy_changed.connect(self._set_busy)
        presenter.render_ready.connect(self._render_frame)
        self._space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._space_shortcut.activated.connect(self._request_transition)

    def _request_transition(self) -> None:
        snapshot = self._snapshot
        if snapshot is None or snapshot.state in {LiveSessionState.DISCONNECTED, LiveSessionState.ERROR}:
            self.discovery_requested.emit()
        elif snapshot.state is LiveSessionState.RUNNING:
            self._presenter.stop()
        elif snapshot.applied is None:
            self._presenter.apply_configuration(self._requested_configuration())
        else:
            self._presenter.start()

    def _apply_view_preset(self, index: int) -> None:
        self._spectrum_panel.setVisible(index in (0, 1))
        self._waterfall_panel.setVisible(index == 0)
        self._persistence_panel.setVisible(index == 2)

    def _load_profile(self, index: int) -> None:
        if index <= 0:
            return
        profile_id = self.profile.itemData(index)
        selected = self._profiles.get(profile_id)
        if selected is None:
            return
        configuration = selected.configuration
        self.center.set_frequency_hz(configuration.center_hz)
        rate_label = f"{configuration.sample_rate_hz / 1e6:g} MHz"
        rate_index = self.sample_rate.findText(rate_label)
        if rate_index >= 0:
            self.sample_rate.setCurrentIndex(rate_index)
        self.gain.setValue(configuration.gain_db)
        backend_index = self.backend.findText(configuration.backend.value.upper())
        if backend_index >= 0:
            self.backend.setCurrentIndex(backend_index)
    def _requested_configuration(self) -> LiveConfiguration:
        backend = BackendKind(self.backend.currentText().casefold())
        return LiveConfiguration(
            center_hz=self.center.frequency_hz(),
            sample_rate_hz=parse_frequency_hz(self.sample_rate.currentText()),
            gain_db=self.gain.value(),
            backend=backend,
        )

    def _render_snapshot(self, snapshot: LiveSnapshot) -> None:
        self._snapshot = snapshot
        self.inspector.update_snapshot(snapshot)
        if snapshot.device is not None:
            capabilities = snapshot.device.capabilities
            self.sample_rate.clear()
            self.sample_rate.addItems([f"{rate / 1e6:g} MHz" for rate in capabilities.sample_rates_hz])
            self.backend.clear()
            self.backend.addItems([backend.value.upper() for backend in capabilities.supported_backends])
        if snapshot.applied is not None:
            requested = snapshot.applied.requested
            applied = snapshot.applied.applied
            self._applied.set_values(f"{requested.sample_rate_hz / 1e6:.3f} MHz", f"{applied.sample_rate_hz / 1e6:.3f} MHz")
            self._backend_chip.set_status(applied.backend.value.upper(), StatusTone.INFO)
        quality = snapshot.quality
        calibration_tone = StatusTone.SUCCESS if quality.calibration.value == "calibrated" else StatusTone.WARNING
        self._calibration_chip.set_status(quality.calibration.value.upper(), calibration_tone)
        drop_tone = StatusTone.WARNING if quality.dropped_blocks else StatusTone.SUCCESS
        self._drops_chip.set_status(f"Drops {quality.dropped_blocks}", drop_tone)
        labels = {
            LiveSessionState.DISCONNECTED: "Подключить",
            LiveSessionState.CONNECTED: "Применить",
            LiveSessionState.RUNNING: "Остановить",
            LiveSessionState.ERROR: "Повторить",
        }
        self._start_stop.setText(labels.get(snapshot.state, "Подождите"))
        self._start_stop.setEnabled(snapshot.state not in {LiveSessionState.CONNECTING, LiveSessionState.STARTING, LiveSessionState.STOPPING})
        if snapshot.error:
            self._render_error(snapshot.error)

    def _render_frame(self, snapshot: LiveSnapshot) -> None:
        self._frame_status.setText(f"Кадр {snapshot.sequence} · generation {snapshot.generation}")
        self._peak_card.set_values("Peak", "—", snapshot.unit, meta=f"Кадр {snapshot.sequence}", quality=snapshot.quality.calibration.value.upper())
        self._noise_card.set_values("Noise floor", "—", snapshot.unit, meta="Display path: latest-wins", quality=snapshot.quality.calibration.value.upper())
    def _render_error(self, message: str) -> None:
        self._error.setVisible(True)
        self._error.set_message(message)
        self.inspector.setCurrentIndex(3)

    def _set_busy(self, busy: bool) -> None:
        self._start_stop.setEnabled(not busy)

    @staticmethod
    def _visual_panel(title: str, message: str) -> QWidget:
        panel = QFrame()
        panel.setProperty("card", True)
        content = QVBoxLayout(panel)
        heading = QLabel(title)
        heading.setProperty("role", "heading")
        content.addWidget(heading)
        content.addWidget(EmptyState(title, message))
        return panel

    def shutdown(self) -> None:
        self._presenter.snapshot_changed.disconnect(self._render_snapshot)
        self._presenter.task_failed.disconnect(self._render_error)
        self._presenter.busy_changed.disconnect(self._set_busy)
        self._presenter.render_ready.disconnect(self._render_frame)
