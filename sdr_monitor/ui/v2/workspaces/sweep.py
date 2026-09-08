"""Plan-first Sweep V2 workspace over the frozen public presenter contract."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sdr_monitor.domain import SweepConfiguration, SweepMode, SweepPlan, SweepResult, SweepState

from ..components import CommandField, ErrorBanner, SectionHeader, StatusChipV2
from ..design import StatusTone, ThemeId, stylesheet_for_theme
from ..design.icons import V2IconId
from ..i18n import enum_text, text
from ..shell.contracts import WorkspaceDefinition
from ..view_models.sweep_view_model import SweepViewModel, SweepViewState

_MODE_LABELS: tuple[tuple[str, SweepMode], ...] = (
    (text("sweep.mode.fast"), SweepMode.FAST),
    (text("sweep.mode.balanced"), SweepMode.BALANCED),
    (text("sweep.mode.precise"), SweepMode.PRECISE),
)


class SweepSegmentGeometry(QWidget):
    """A bounded plan geometry visual; it never pretends to be a stitched spectrum."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plan: SweepPlan | None = None
        self.setMinimumHeight(116)
        self.setAccessibleName(text("sweep.geometry.name"))
        self.setAccessibleDescription(text("sweep.geometry.description"))

    @property
    def plan(self) -> SweepPlan | None:
        return self._plan

    def set_plan(self, plan: SweepPlan | None) -> None:
        if plan is self._plan:
            return
        self._plan = plan
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bounds = self.rect().adjusted(12, 24, -12, -24)
        painter.fillRect(self.rect(), QColor("#171E26"))
        if self._plan is None:
            painter.setPen(QColor("#9EABB8"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text("sweep.plan.not_calculated"))
            painter.end()
            return
        plan = self._plan
        start, stop = plan.configuration.start_hz, plan.configuration.stop_hz
        span = max(stop - start, 1.0)
        painter.setPen(QPen(QColor("#2D3945"), 1))
        painter.drawRect(bounds)
        for segment in plan.segments:
            segment_left = bounds.left() + bounds.width() * (segment.start_hz - start) / span
            segment_right = bounds.left() + bounds.width() * (segment.stop_hz - start) / span
            usable_left = bounds.left() + bounds.width() * (segment.usable_start_hz - start) / span
            usable_right = bounds.left() + bounds.width() * (segment.usable_stop_hz - start) / span
            segment_rect = _rect_from_float(segment_left, segment_right, bounds.top() + 18, bounds.bottom() - 18)
            usable_rect = _rect_from_float(usable_left, usable_right, bounds.top() + 26, bounds.bottom() - 26)
            painter.fillRect(segment_rect, QColor("#25313D") if segment.index % 2 == 0 else QColor("#202B35"))
            painter.fillRect(usable_rect, QColor("#3CA6FF"))
            painter.setPen(QPen(QColor("#526574"), 1))
            painter.drawRect(segment_rect)
        painter.setPen(QColor("#9EABB8"))
        painter.drawText(
            self.rect().adjusted(12, 2, -12, -2),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            text(
                "sweep.geometry.annotation",
                segments=len(plan.segments),
                start=start / 1e6,
                stop=stop / 1e6,
            ),
        )
        painter.end()


class SweepWorkspaceV2(QWidget):
    """New V2 presentation for existing plan/execute/cancel/export presenter commands."""

    def __init__(
        self,
        view_model: SweepViewModel,
        *,
        theme: ThemeId = ThemeId.DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._theme = theme
        self._build_ui()
        self.set_theme(theme)
        self._unsubscribe_state = view_model.subscribe(self._render_state)

    def closeEvent(self, event) -> None:
        self._unsubscribe_state()
        super().closeEvent(event)

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.setStyleSheet(stylesheet_for_theme(theme))
        for chip in (self._state_chip, self._seam_chip, self._calibration_chip):
            chip.set_theme(theme)

    def _build_ui(self) -> None:
        self.setProperty("ui2Root", True)
        self.setAccessibleName(text("sweep.accessible.name"))
        self.setAccessibleDescription(text("sweep.accessible.description"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(
            SectionHeader(text("sweep.header.title"), text("sweep.header.detail"), parent=self)
        )
        layout.addWidget(self._build_command_bar())
        layout.addWidget(self._build_configuration_card())
        layout.addWidget(self._build_quality_bar())
        self._error_banner = ErrorBanner(text("sweep.error.title"), "", parent=self)
        self._error_banner.setVisible(False)
        layout.addWidget(self._error_banner)
        geometry_card = QFrame(self)
        geometry_card.setProperty("ui2Role", "card")
        geometry_layout = QVBoxLayout(geometry_card)
        geometry_layout.setContentsMargins(10, 10, 10, 10)
        geometry_layout.addWidget(
            _secondary_label(text("sweep.geometry.title"), text("sweep.geometry.title.name"), geometry_card)
        )
        self._geometry = SweepSegmentGeometry(geometry_card)
        geometry_layout.addWidget(self._geometry)
        self._plan_summary = _secondary_label(
            text("sweep.plan.not_calculated.summary"), text("sweep.plan.summary.name"), geometry_card
        )
        self._plan_summary.setWordWrap(True)
        geometry_layout.addWidget(self._plan_summary)
        layout.addWidget(geometry_card, 1)
        result_card = QFrame(self)
        result_card.setProperty("ui2Role", "card")
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(10, 10, 10, 10)
        result_layout.addWidget(
            _secondary_label(text("sweep.result.title"), text("sweep.result.title.name"), result_card)
        )
        self._result_summary = _secondary_label(
            text("sweep.result.none"),
            text("sweep.result.summary.name"),
            result_card,
        )
        self._result_summary.setWordWrap(True)
        result_layout.addWidget(self._result_summary)
        layout.addWidget(result_card)

    def _build_command_bar(self) -> QWidget:
        bar = QFrame(self)
        bar.setProperty("ui2Role", "card")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(8)
        self._plan_button = QPushButton(text("sweep.plan.calculate"), bar)
        self._plan_button.setProperty("ui2Role", "utility-action")
        self._plan_button.clicked.connect(self._request_plan)
        self._run_button = QPushButton(text("sweep.run"), bar)
        self._run_button.setProperty("ui2Role", "primary-action")
        self._run_button.clicked.connect(self._run)
        self._cancel_button = QPushButton(text("sweep.cancel"), bar)
        self._cancel_button.setProperty("ui2Role", "utility-action")
        self._cancel_button.clicked.connect(self._view_model.cancel)
        self._export_path = CommandField(text("sweep.export.label"), value="sweep-summary.json", parent=bar)
        self._export_path.setMaximumWidth(320)
        self._export_button = QPushButton(text("sweep.export"), bar)
        self._export_button.setProperty("ui2Role", "utility-action")
        self._export_button.clicked.connect(self._export)
        for control in (
            self._plan_button,
            self._run_button,
            self._cancel_button,
            self._export_path,
            self._export_button,
        ):
            row.addWidget(control)
        row.addStretch(1)
        return bar

    def _build_configuration_card(self) -> QWidget:
        card = QFrame(self)
        card.setProperty("ui2Role", "card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        main_form = QFormLayout()
        self._start_mhz = _spin(
            0.001, 20_000.0, 400.0, text("sweep.frequency.suffix"), text("sweep.start_frequency.name"), card
        )
        self._stop_mhz = _spin(
            0.002, 20_000.0, 6_000.0, text("sweep.frequency.suffix"), text("sweep.stop_frequency.name"), card
        )
        self._mode = QComboBox(card)
        self._mode.setProperty("ui2Role", "utility-select")
        self._mode.setAccessibleName(text("sweep.mode.name"))
        for label, mode in _MODE_LABELS:
            self._mode.addItem(label, mode.value)
        self._mode.setCurrentIndex(self._mode.findData(SweepMode.BALANCED.value))
        main_form.addRow(text("sweep.start_frequency.label"), self._start_mhz)
        main_form.addRow(text("sweep.stop_frequency.label"), self._stop_mhz)
        main_form.addRow(text("sweep.mode.label"), self._mode)
        layout.addLayout(main_form, 1)
        expert_form = QFormLayout()
        self._overlap_percent = _spin(
            0.0, 49.0, 10.0, text("sweep.percent.suffix"), text("sweep.overlap.name"), card
        )
        self._dc_margin_mhz = _spin(
            0.0, 10.0, 0.1, text("sweep.frequency.suffix"), text("sweep.dc_margin.name"), card
        )
        self._settling_seconds = _spin(
            0.0, 10.0, 0.02, text("sweep.seconds.suffix"), text("sweep.settling.name"), card
        )
        self._dwell_seconds = _spin(
            0.0, 60.0, 0.10, text("sweep.seconds.suffix"), text("sweep.dwell.name"), card
        )
        self._discard_blocks = QSpinBox(card)
        self._discard_blocks.setProperty("ui2Role", "range-control")
        self._discard_blocks.setAccessibleName(text("sweep.discard_blocks.name"))
        self._discard_blocks.setRange(0, 32)
        self._discard_blocks.setValue(1)
        expert_form.addRow(text("sweep.overlap.label"), self._overlap_percent)
        expert_form.addRow(text("sweep.dc_margin.label"), self._dc_margin_mhz)
        expert_form.addRow(text("sweep.settling.label"), self._settling_seconds)
        expert_form.addRow(text("sweep.dwell.label"), self._dwell_seconds)
        expert_form.addRow(text("sweep.discard_blocks.label"), self._discard_blocks)
        layout.addLayout(expert_form, 1)
        for control in (
            self._start_mhz,
            self._stop_mhz,
            self._mode,
            self._overlap_percent,
            self._dc_margin_mhz,
            self._settling_seconds,
            self._dwell_seconds,
            self._discard_blocks,
        ):
            _connect_changed(control, self._on_configuration_changed)
        return card

    def _build_quality_bar(self) -> QWidget:
        bar = QFrame(self)
        bar.setProperty("ui2Role", "card")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        row = QHBoxLayout()
        self._state_chip = StatusChipV2(text("sweep.plan.none"), tone=StatusTone.NEUTRAL, parent=bar)
        self._seam_chip = StatusChipV2(text("sweep.seam.value", value=text("sweep.no_data")), tone=StatusTone.NEUTRAL, parent=bar)
        self._calibration_chip = StatusChipV2(
            text("sweep.calibration.value", value=text("sweep.no_data")), tone=StatusTone.NEUTRAL, parent=bar
        )
        for chip in (self._state_chip, self._seam_chip, self._calibration_chip):
            row.addWidget(chip)
        row.addStretch(1)
        layout.addLayout(row)
        self._progress = QProgressBar(bar)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._progress.setAccessibleName(text("sweep.progress.name"))
        layout.addWidget(self._progress)
        return bar

    def _configuration(self) -> SweepConfiguration | None:
        try:
            return SweepConfiguration(
                start_hz=self._start_mhz.value() * 1e6,
                stop_hz=self._stop_mhz.value() * 1e6,
                mode=SweepMode(str(self._mode.currentData())),
                overlap_fraction=self._overlap_percent.value() / 100.0,
                dc_margin_hz=self._dc_margin_mhz.value() * 1e6,
                settling_s=self._settling_seconds.value(),
                dwell_s=self._dwell_seconds.value(),
                discard_blocks=self._discard_blocks.value(),
            )
        except ValueError as error:
            self._show_local_error(str(error))
            return None

    def _request_plan(self) -> None:
        configuration = self._configuration()
        if configuration is not None:
            self._view_model.plan(configuration)

    def _run(self) -> None:
        configuration = self._configuration()
        if configuration is None:
            return
        if not self._view_model.execute(configuration):
            self._request_plan()

    def _export(self) -> None:
        raw_path = self._export_path.value.strip()
        if not raw_path:
            self._show_local_error(text("sweep.export.error.empty_path"))
            return
        self._view_model.export_result(Path(raw_path))

    def _on_configuration_changed(self, _value: object) -> None:
        self._render_state(self._view_model.state)

    def _render_state(self, state: SweepViewState) -> None:
        configuration = self._configuration_without_error()
        plan_matches = configuration is not None and state.plan is not None and state.plan.configuration == configuration
        result_matches = (
            configuration is not None
            and state.result is not None
            and state.result.plan.configuration == configuration
        )
        self._plan_button.setEnabled(not state.busy)
        self._run_button.setEnabled(not state.busy and plan_matches)
        self._cancel_button.setEnabled(state.can_cancel)
        self._export_button.setEnabled(not state.busy and result_matches)
        self._geometry.set_plan(state.plan if plan_matches else None)
        self._render_plan(state.plan if plan_matches else None)
        self._render_progress(state)
        self._render_result(state.result if result_matches else None)
        if state.error is None:
            self._error_banner.setVisible(False)
        else:
            self._error_banner.set_content(text("sweep.error.title"), state.error)
            self._error_banner.set_action("", enabled=False)
            self._error_banner.setVisible(True)

    def _configuration_without_error(self) -> SweepConfiguration | None:
        try:
            return SweepConfiguration(
                start_hz=self._start_mhz.value() * 1e6,
                stop_hz=self._stop_mhz.value() * 1e6,
                mode=SweepMode(str(self._mode.currentData())),
                overlap_fraction=self._overlap_percent.value() / 100.0,
                dc_margin_hz=self._dc_margin_mhz.value() * 1e6,
                settling_s=self._settling_seconds.value(),
                dwell_s=self._dwell_seconds.value(),
                discard_blocks=self._discard_blocks.value(),
            )
        except ValueError:
            return None

    def _render_plan(self, plan: SweepPlan | None) -> None:
        if plan is None:
            self._plan_summary.setText(text("sweep.plan.required"))
            return
        self._plan_summary.setText(
            text(
                "sweep.plan.summary",
                segments=len(plan.segments),
                estimated_seconds=plan.estimated_seconds,
                resolution_khz=plan.resolution_hz / 1e3,
            )
        )

    def _render_progress(self, state: SweepViewState) -> None:
        progress = state.progress
        if progress is None:
            self._progress.setVisible(False)
            self._state_chip.set_text(text("sweep.plan.ready") if state.plan is not None else text("sweep.plan.none"))
            self._state_chip.set_tone(StatusTone.INFO if state.plan is not None else StatusTone.NEUTRAL)
            return
        self._progress.setVisible(True)
        self._progress.setValue(round(progress.percent))
        self._progress.setFormat(
            text(
                "sweep.progress.format",
                stage=progress.stage or progress.state.value,
                completed=progress.completed_segments,
                total=progress.total_segments,
                percent=progress.percent,
            )
        )
        tone = StatusTone.WARNING if progress.state is SweepState.RUNNING else StatusTone.INFO
        self._state_chip.set_text(enum_text("sweep.state", progress.state))
        self._state_chip.set_tone(tone)

    def _render_result(self, result: SweepResult | None) -> None:
        if result is None:
            self._result_summary.setText(
                text("sweep.result.none")
            )
            self._seam_chip.set_text(text("sweep.seam.value", value=text("sweep.no_data")))
            self._calibration_chip.set_text(text("sweep.calibration.value", value=text("sweep.no_data")))
            return
        quality = result.quality
        seam = text("sweep.no_data") if quality.seam_p95_db is None else f"{quality.seam_p95_db:.3f} dB"
        coverage = text("sweep.no_data") if quality.calibration_coverage_percent is None else f"{quality.calibration_coverage_percent:.1f}%"
        note = "" if quality.note is None else f" · {quality.note}"
        self._result_summary.setText(
            text(
                "sweep.result.summary",
                state=enum_text("sweep.state", result.state),
                duration=result.duration_seconds,
                missing_segments=quality.missing_segments,
                seam=seam,
                coverage=coverage,
                note=note,
            )
        )
        self._seam_chip.set_text(text("sweep.seam.value", value=seam))
        self._seam_chip.set_tone(StatusTone.WARNING if quality.seam_p95_db is None else StatusTone.INFO)
        self._calibration_chip.set_text(text("sweep.calibration.value", value=coverage))
        self._calibration_chip.set_tone(
            StatusTone.WARNING if quality.calibration_coverage_percent is None else StatusTone.INFO
        )

    def _show_local_error(self, message: str) -> None:
        self._error_banner.set_content(text("sweep.error.title"), message)
        self._error_banner.set_action("", enabled=False)
        self._error_banner.setVisible(True)


def sweep_workspace_definition(view_model: SweepViewModel, *, theme: ThemeId = ThemeId.DARK) -> WorkspaceDefinition:
    """Define the plan-first Sweep V2 workspace without service ownership."""

    return WorkspaceDefinition(
        workspace_id="sweep",
        label=text("sweep.workspace.label"),
        description=text("sweep.workspace.description"),
        icon=V2IconId.CHEVRON,
        workspace_factory=lambda: SweepWorkspaceV2(view_model, theme=theme),
        inspector_factory=lambda: _sweep_inspector(view_model, theme),
        label_key="sweep.workspace.label",
        description_key="sweep.workspace.description",
    )


def _sweep_inspector(view_model: SweepViewModel, theme: ThemeId) -> QWidget:
    inspector = QFrame()
    inspector.setProperty("ui2Root", True)
    inspector.setProperty("ui2Role", "panel")
    inspector.setStyleSheet(stylesheet_for_theme(theme))
    layout = QVBoxLayout(inspector)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.addWidget(SectionHeader(text("sweep.inspector.context"), text("sweep.inspector.detail"), parent=inspector))
    detail = _secondary_label(text("sweep.inspector.waiting"), text("sweep.inspector.state.name"), inspector)
    detail.setWordWrap(True)
    layout.addWidget(detail)
    boundary = _secondary_label(
        text("sweep.inspector.boundary"),
        text("sweep.inspector.boundary.name"),
        inspector,
    )
    boundary.setWordWrap(True)
    layout.addWidget(boundary)
    layout.addStretch(1)

    def render(state: SweepViewState) -> None:
        plan_text = text("sweep.inspector.none") if state.plan is None else text(
            "sweep.inspector.segments", segments=len(state.plan.segments)
        )
        progress_text = text("sweep.inspector.none") if state.progress is None else text(
            "sweep.inspector.progress", state=enum_text("sweep.state", state.progress.state), percent=state.progress.percent
        )
        result_text = (
            text("sweep.inspector.none")
            if state.result is None
            else enum_text("sweep.state", state.result.state)
        )
        detail.setText(text("sweep.inspector.summary", plan=plan_text, progress=progress_text, result=result_text))

    unsubscribe = view_model.subscribe(render)
    inspector.destroyed.connect(lambda: unsubscribe())
    return inspector


def _spin(
    lower: float,
    upper: float,
    value: float,
    suffix: str,
    accessible_name: str,
    parent: QWidget,
) -> QDoubleSpinBox:
    control = QDoubleSpinBox(parent)
    control.setProperty("ui2Role", "range-control")
    control.setAccessibleName(accessible_name)
    control.setRange(lower, upper)
    control.setDecimals(3)
    control.setValue(value)
    control.setSuffix(suffix)
    return control


def _connect_changed(control: object, callback: Callable[[object], None]) -> None:
    signal = getattr(control, "valueChanged", getattr(control, "currentIndexChanged", None))
    if signal is not None:
        signal.connect(callback)


def _secondary_label(text: str, accessible_name: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("ui2Role", "secondary")
    label.setAccessibleName(accessible_name)
    return label


def _rect_from_float(left: float, right: float, top: float, bottom: float) -> QRectF:
    return QRectF(left, top, max(1.0, right - left), max(1.0, bottom - top))
