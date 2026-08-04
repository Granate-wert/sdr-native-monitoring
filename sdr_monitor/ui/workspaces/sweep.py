"""Standalone S06 wideband sweep workspace."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...domain import SweepConfiguration, SweepMode, SweepPlan, SweepProgress, SweepResult, SweepState
from ..components import EmptyState, ErrorState, FrequencyInput, NumericReadout, SectionCard, StatusChip, TaskProgress
from ..design_tokens import StatusTone
from ..presenters import SweepPresenter


_MODE_TITLES = (("Быстро", SweepMode.FAST), ("Сбалансированно", SweepMode.BALANCED), ("Точно", SweepMode.PRECISE))


class SweepWorkspace(QWidget):
    """Plan-first presentation; plotting is intentionally outside the GUI worker path."""

    def __init__(self, presenter: SweepPresenter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._presenter = presenter
        self._plan: SweepPlan | None = None
        self._result: SweepResult | None = None
        self._build_ui()
        self._apply_mode(self.mode_combo.currentIndex())
        presenter.plan_ready.connect(self._show_plan)
        presenter.progress_changed.connect(self._show_progress)
        presenter.result_ready.connect(self._show_result)
        presenter.export_ready.connect(self._show_export)
        presenter.task_failed.connect(self._show_error)
        presenter.busy_changed.connect(self._set_busy)
        self.request_plan()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        toolbar = QHBoxLayout()
        title = QLabel("Wideband Sweep")
        title.setProperty("role", "heading")
        toolbar.addWidget(title)
        toolbar.addStretch(1)
        self.plan_button = QPushButton("Показать план")
        self.plan_button.clicked.connect(self.request_plan)
        self.run_button = QPushButton("Запустить обзор")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.run)
        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._presenter.cancel)
        self.export_button = QPushButton("Экспортировать сводку")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._choose_export)
        for button in (self.plan_button, self.run_button, self.cancel_button, self.export_button):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        setup = SectionCard("Диапазон и режим")
        form = QFormLayout()
        self.start = FrequencyInput()
        self.start.set_frequency_hz(400e6)
        self.stop = FrequencyInput()
        self.stop.set_frequency_hz(6e9)
        self.mode_combo = QComboBox()
        for title, mode in _MODE_TITLES:
            self.mode_combo.addItem(title, mode.value)
        self.mode_combo.setAccessibleName("Режим обзора")
        self.mode_combo.currentIndexChanged.connect(self._apply_mode)
        form.addRow("От", self.start)
        form.addRow("До", self.stop)
        form.addRow("Режим", self.mode_combo)
        setup.content.addLayout(form)
        layout.addWidget(setup)

        self._quality = QHBoxLayout()
        self._state_chip = StatusChip("Нет плана", StatusTone.NEUTRAL)
        self._seam_chip = StatusChip("Seam: не измерено", StatusTone.NEUTRAL)
        self._coverage_chip = StatusChip("Калибровка: не измерена", StatusTone.NEUTRAL)
        for chip in (self._state_chip, self._seam_chip, self._coverage_chip):
            self._quality.addWidget(chip)
        self._quality.addStretch(1)
        layout.addLayout(self._quality)

        plan_card = SectionCard("План диапазона")
        self._plan_summary = QLabel("План ещё не рассчитан")
        self._plan_summary.setWordWrap(True)
        self._band_diagram = QLabel("Нет сегментов")
        self._band_diagram.setWordWrap(True)
        self._band_diagram.setAccessibleName("Диаграмма сегментов обзора")
        plan_card.add_widget(self._plan_summary)
        plan_card.add_widget(self._band_diagram)
        layout.addWidget(plan_card)

        self._progress = TaskProgress()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        expert = QGroupBox("Экспертные параметры")
        expert.setCheckable(True)
        expert.setChecked(False)
        expert_form = QFormLayout(expert)
        self.overlap = QDoubleSpinBox()
        self.overlap.setRange(0.0, 49.0)
        self.overlap.setValue(10.0)
        self.overlap.setSuffix(" %")
        self.dc_margin = QDoubleSpinBox()
        self.dc_margin.setRange(0.0, 10.0)
        self.dc_margin.setValue(0.1)
        self.dc_margin.setSuffix(" MHz")
        self.settling = QDoubleSpinBox()
        self.settling.setRange(0.0, 10.0)
        self.settling.setValue(0.02)
        self.settling.setSuffix(" s")
        self.dwell = QDoubleSpinBox()
        self.dwell.setRange(0.0, 60.0)
        self.dwell.setValue(0.10)
        self.dwell.setSuffix(" s")
        expert_form.addRow("Overlap", self.overlap)
        expert_form.addRow("DC margin", self.dc_margin)
        expert_form.addRow("Settling", self.settling)
        expert_form.addRow("Dwell", self.dwell)
        layout.addWidget(expert)

        result_card = SectionCard("Результат")
        self._result_state = EmptyState("Нет результата", "После обзора здесь появится full-span result и quality evidence.")
        result_card.add_widget(self._result_state)
        summary = QHBoxLayout()
        self._missing = NumericReadout("Missing segments")
        self._seam = NumericReadout("Seam p95")
        self._coverage = NumericReadout("Calibration coverage")
        self._duration = NumericReadout("Duration")
        for card in (self._missing, self._seam, self._coverage, self._duration):
            summary.addWidget(card)
        result_card.content.addLayout(summary)
        layout.addWidget(result_card)

        self._error = ErrorState("Нет активной ошибки", retry=self.request_plan)
        self._error.setVisible(False)
        layout.addWidget(self._error)
        layout.addStretch(1)

    def request_plan(self) -> None:
        configuration = self._configuration_or_error()
        if configuration is not None:
            self._presenter.plan(configuration)

    def run(self) -> None:
        configuration = self._configuration_or_error()
        if configuration is None:
            return
        if self._plan is None or self._plan.configuration != configuration:
            self.request_plan()
            return
        self._presenter.execute(configuration)

    def _configuration_or_error(self) -> SweepConfiguration | None:
        try:
            return SweepConfiguration(
                start_hz=self.start.frequency_hz(),
                stop_hz=self.stop.frequency_hz(),
                mode=SweepMode(self.mode_combo.currentData()),
                overlap_fraction=self.overlap.value() / 100.0,
                dc_margin_hz=self.dc_margin.value() * 1e6,
                settling_s=self.settling.value(),
                dwell_s=self.dwell.value(),
            )
        except ValueError as error:
            self._show_error(str(error))
            return None

    def _apply_mode(self, index: int) -> None:
        mode = SweepMode(self.mode_combo.itemData(index))
        self.dwell.setValue({SweepMode.FAST: 0.02, SweepMode.BALANCED: 0.10, SweepMode.PRECISE: 0.25}[mode])
        self._plan = None
        self.run_button.setEnabled(False)

    def _show_plan(self, plan: SweepPlan) -> None:
        current = self._configuration_or_error()
        if current is None or plan.configuration != current:
            return
        self._plan = plan
        self.run_button.setEnabled(True)
        self._state_chip.set_status("План готов", StatusTone.INFO)
        self._plan_summary.setText(
            f"Сегментов: {len(plan.segments)} · Время: ~{plan.estimated_seconds:.1f} s · "
            f"Разрешение: {plan.resolution_hz / 1e3:.1f} kHz"
        )
        preview = " ".join(f"|S{segment.index + 1}|" for segment in plan.segments[:8])
        suffix = " …" if len(plan.segments) > 8 else ""
        self._band_diagram.setText(f"{plan.configuration.start_hz / 1e6:.0f} MHz {preview}{suffix} {plan.configuration.stop_hz / 1e6:.0f} MHz")

    def _show_progress(self, progress: SweepProgress) -> None:
        self._progress.setVisible(True)
        self._progress.set_progress(progress.percent, f"{progress.stage}: сегмент {progress.completed_segments} из {progress.total_segments}")
        self._state_chip.set_status(f"{progress.state.value}: {progress.percent:.0f}%", StatusTone.WARNING)
        self.cancel_button.setEnabled(progress.state is SweepState.RUNNING)

    def _show_result(self, result: SweepResult) -> None:
        self._result = result
        self._progress.setVisible(False)
        self.cancel_button.setEnabled(False)
        self.export_button.setEnabled(True)
        tone = StatusTone.SUCCESS if result.state is SweepState.COMPLETED else StatusTone.WARNING
        self._state_chip.set_status(result.state.value.upper(), tone)
        self._result_state.setVisible(False)
        quality = result.quality
        self._missing.set_value(float(quality.missing_segments), "")
        self._seam.set_value(quality.seam_p95_db, "dB")
        self._coverage.set_value(quality.calibration_coverage_percent, "%")
        self._duration.set_value(result.duration_seconds, "s")
        self._seam_chip.set_status("Seam: не измерено" if quality.seam_p95_db is None else f"Seam p95: {quality.seam_p95_db:.2f} dB", StatusTone.NEUTRAL)
        coverage = "Калибровка: не измерена" if quality.calibration_coverage_percent is None else f"Калибровка: {quality.calibration_coverage_percent:.0f}%"
        self._coverage_chip.set_status(coverage, StatusTone.NEUTRAL)

    def _choose_export(self) -> None:
        if self._result is None:
            return
        selected, _filter = QFileDialog.getSaveFileName(self, "Экспортировать сводку обзора", "sweep-summary.json", "JSON (*.json)")
        if selected:
            self._presenter.export_result(self._result, Path(selected))

    def _show_export(self, output_path: Path) -> None:
        self._state_chip.set_status(f"Экспортировано: {output_path.name}", StatusTone.SUCCESS)

    def _show_error(self, message: str) -> None:
        self._error.set_message(message)
        self._error.setVisible(True)
        self._state_chip.set_status("Ошибка плана", StatusTone.ERROR)

    def _set_busy(self, busy: bool) -> None:
        self.plan_button.setEnabled(not busy)
        self.run_button.setEnabled(not busy and self._plan is not None)

    def shutdown(self) -> None:
        self._presenter.plan_ready.disconnect(self._show_plan)
        self._presenter.progress_changed.disconnect(self._show_progress)
        self._presenter.result_ready.disconnect(self._show_result)
        self._presenter.export_ready.disconnect(self._show_export)
        self._presenter.task_failed.disconnect(self._show_error)
        self._presenter.busy_changed.disconnect(self._set_busy)
