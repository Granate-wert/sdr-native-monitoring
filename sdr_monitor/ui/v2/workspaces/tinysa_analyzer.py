"""V2 tinySA analyzer page over an already verified, externally owned presenter."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sdr_monitor.application.tinysa_analyzer import TinySaAnalyzerPhase
from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_serial_trace_collector import TinySaScanRawRequest
from sdr_monitor.services.tinysa_sweep_policy import TinySaSweepAccuracy
from sdr_monitor.services.tinysa_sweep_settings_controller import (
    TinySaAttenuationMode,
    TinySaRbwMode,
    TinySaSpurPolicy,
    TinySaSweepSettingsPlan,
    TinySaSwitchPolicy,
)

from ..components import ErrorBanner, SectionHeader, StatusChipV2
from ..design import StatusTone, ThemeId, stylesheet_for_theme
from ..design.icons import V2IconId
from ..i18n import enum_text, text
from ..shell.contracts import WorkspaceDefinition
from ..spectrum.scene import SpectrumScene
from ..view_models.tinysa_view_model import TinySaAnalyzerViewModel, TinySaAnalyzerViewState


class TinySaAnalyzerWorkspaceV2(QWidget):
    """Render bounded device-reported dBm output; every device operation is explicit."""

    def __init__(
        self,
        view_model: TinySaAnalyzerViewModel,
        *,
        theme: ThemeId = ThemeId.DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._theme = theme
        self._build_ui()
        self.set_theme(theme)
        self._unsubscribe = view_model.subscribe(self._render_state)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        self._unsubscribe()
        super().closeEvent(event)

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.setStyleSheet(stylesheet_for_theme(theme))
        self._state_chip.set_theme(theme)
        self._scene.set_theme(theme)

    def _build_ui(self) -> None:
        source = self._view_model.state.source
        maximum_mhz = 5_300.0 if source.model is TinySaModel.ULTRA else 960.0
        self.setProperty("ui2Root", True)
        self.setAccessibleName(text("tinysa_analyzer.accessible.name"))
        self.setAccessibleDescription(text("tinysa_analyzer.accessible.description"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(
            SectionHeader(
                text("tinysa_analyzer.header.title"), text("tinysa_analyzer.header.detail"),
                parent=self,
            )
        )
        header = QFrame(self)
        header.setProperty("ui2Role", "card")
        header_layout = QHBoxLayout(header)
        self._state_chip = StatusChipV2(text("tinysa_analyzer.state.ready"), tone=StatusTone.INFO, parent=header)
        header_layout.addWidget(self._state_chip)
        self._source_identity = QLabel(self._source_text(), header)
        self._source_identity.setProperty("ui2Role", "secondary")
        self._source_identity.setWordWrap(True)
        self._source_identity.setAccessibleName(text("tinysa_analyzer.source.name"))
        header_layout.addWidget(self._source_identity, 1)
        layout.addWidget(header)
        layout.addWidget(self._acquisition_card(maximum_mhz))
        layout.addWidget(self._trace_card(), 1)
        layout.addWidget(self._settings_card())
        self._busy = QLabel(text("tinysa_analyzer.busy"), self)
        self._busy.setProperty("ui2Role", "secondary")
        self._busy.setAccessibleName(text("tinysa_analyzer.busy.name"))
        self._busy.setVisible(False)
        layout.addWidget(self._busy)
        self._error = ErrorBanner(text("tinysa_analyzer.error.title"), "", parent=self)
        self._error.setVisible(False)
        layout.addWidget(self._error)

    def _acquisition_card(self, maximum_mhz: float) -> QWidget:
        card = _card(text("tinysa_analyzer.card.acquisition"), self)
        card_layout = _card_layout(card)
        form = QFormLayout()
        self._start_mhz = _frequency_spin(87.5, maximum_mhz, text("tinysa_analyzer.frequency.start.name"), card)
        self._stop_mhz = _frequency_spin(108.0, maximum_mhz, text("tinysa_analyzer.frequency.stop.name"), card)
        self._points = QSpinBox(card)
        self._points.setProperty("ui2Role", "range-control")
        self._points.setRange(2, 10_001)
        self._points.setValue(10_001)
        self._points.setAccessibleName(text("tinysa_analyzer.points.name"))
        self._points.setAccessibleDescription(text("tinysa_analyzer.points.description"))
        form.addRow(text("tinysa_analyzer.form.start"), self._start_mhz)
        form.addRow(text("tinysa_analyzer.form.stop"), self._stop_mhz)
        form.addRow(text("tinysa_analyzer.form.points"), self._points)
        card_layout.addLayout(form)
        row = QHBoxLayout()
        self._acquire = QPushButton(text("tinysa_analyzer.acquire"), card)
        self._acquire.setProperty("ui2Role", "primary-action")
        self._acquire.setAccessibleName(text("tinysa_analyzer.acquire.name"))
        self._acquire.setAccessibleDescription(text("tinysa_analyzer.acquire.description"))
        self._acquire.clicked.connect(self._request_trace)
        row.addWidget(self._acquire)
        self._trace_summary = QLabel(text("tinysa_analyzer.trace.empty"), card)
        self._trace_summary.setProperty("ui2Role", "secondary")
        self._trace_summary.setWordWrap(True)
        self._trace_summary.setAccessibleName(text("tinysa_analyzer.card.trace"))
        row.addWidget(self._trace_summary, 1)
        card_layout.addLayout(row)
        return card

    def _trace_card(self) -> QWidget:
        card = _card(text("tinysa_analyzer.card.trace"), self)
        card_layout = _card_layout(card)
        self._scene = SpectrumScene(theme=self._theme, parent=card)
        self._scene.setAccessibleName(text("tinysa_analyzer.trace.scene_name"))
        self._scene.setMinimumHeight(250)
        card_layout.addWidget(self._scene, 1)
        note = QLabel(
            text("tinysa_analyzer.trace.boundary"),
            card,
        )
        note.setProperty("ui2Role", "secondary")
        note.setWordWrap(True)
        card_layout.addWidget(note)
        return card

    def _settings_card(self) -> QWidget:
        card = _card(text("tinysa_analyzer.card.settings"), self)
        card_layout = _card_layout(card)
        detail = QLabel(
            text("tinysa_analyzer.settings.detail"),
            card,
        )
        detail.setProperty("ui2Role", "secondary")
        detail.setWordWrap(True)
        card_layout.addWidget(detail)
        form = QFormLayout()
        self._accuracy = _enum_combo(
            text("tinysa_analyzer.settings.accuracy.name"), "tinysa_analyzer.accuracy", TinySaSweepAccuracy, card
        )
        self._rbw_mode = _enum_combo(
            text("tinysa_analyzer.settings.rbw_mode.name"), "tinysa_analyzer.rbw_mode", TinySaRbwMode, card
        )
        self._rbw_hz = QSpinBox(card)
        self._rbw_hz.setProperty("ui2Role", "range-control")
        self._rbw_hz.setRange(200, 850_000)
        self._rbw_hz.setSingleStep(100)
        self._rbw_hz.setValue(10_000)
        self._rbw_hz.setSuffix(text("tinysa_analyzer.unit.hz"))
        self._rbw_hz.setAccessibleName(text("tinysa_analyzer.settings.rbw.name"))
        self._lna = _enum_combo(
            text("tinysa_analyzer.settings.lna.name"), "tinysa_analyzer.switch_policy", TinySaSwitchPolicy, card
        )
        self._attenuation_mode = _enum_combo(
            text("tinysa_analyzer.settings.attenuation_mode.name"),
            "tinysa_analyzer.attenuation_mode",
            TinySaAttenuationMode,
            card,
        )
        self._attenuation_db = QSpinBox(card)
        self._attenuation_db.setProperty("ui2Role", "range-control")
        self._attenuation_db.setRange(0, 31)
        self._attenuation_db.setSuffix(text("tinysa_analyzer.unit.db"))
        self._attenuation_db.setAccessibleName(text("tinysa_analyzer.settings.attenuation.name"))
        self._spur = _enum_combo(
            text("tinysa_analyzer.settings.spur.name"), "tinysa_analyzer.spur_policy", TinySaSpurPolicy, card
        )
        self._sweep_time_enabled = QCheckBox(text("tinysa_analyzer.settings.sweep_time"), card)
        self._sweep_time_enabled.setAccessibleName(text("tinysa_analyzer.settings.sweep_time.name"))
        self._sweep_time_ms = QSpinBox(card)
        self._sweep_time_ms.setProperty("ui2Role", "range-control")
        self._sweep_time_ms.setRange(3, 60_000)
        self._sweep_time_ms.setValue(1_000)
        self._sweep_time_ms.setSuffix(text("tinysa_analyzer.unit.ms"))
        self._sweep_time_ms.setAccessibleName(text("tinysa_analyzer.settings.sweep_time_ms.name"))
        self._repeat_enabled = QCheckBox(text("tinysa_analyzer.settings.repeat"), card)
        self._repeat_enabled.setAccessibleName(text("tinysa_analyzer.settings.repeat.name"))
        self._repeat = QSpinBox(card)
        self._repeat.setProperty("ui2Role", "range-control")
        self._repeat.setRange(1, 1_000)
        self._repeat.setValue(1)
        self._repeat.setAccessibleName(text("tinysa_analyzer.settings.repeat_count.name"))
        form.addRow(text("tinysa_analyzer.settings.form.accuracy"), self._accuracy)
        form.addRow(text("tinysa_analyzer.settings.form.rbw"), self._rbw_mode)
        form.addRow(text("tinysa_analyzer.settings.form.manual_rbw"), self._rbw_hz)
        form.addRow(text("tinysa_analyzer.settings.form.lna"), self._lna)
        form.addRow(text("tinysa_analyzer.settings.form.attenuation"), self._attenuation_mode)
        form.addRow(text("tinysa_analyzer.settings.form.manual_attenuation"), self._attenuation_db)
        form.addRow(text("tinysa_analyzer.settings.form.spur"), self._spur)
        form.addRow(self._sweep_time_enabled, self._sweep_time_ms)
        form.addRow(self._repeat_enabled, self._repeat)
        card_layout.addLayout(form)
        unavailable = QLabel(
            text("tinysa_analyzer.settings.unavailable"),
            card,
        )
        unavailable.setProperty("ui2Role", "secondary")
        unavailable.setWordWrap(True)
        unavailable.setAccessibleName(text("tinysa_analyzer.settings.unavailable.name"))
        card_layout.addWidget(unavailable)
        self._settings_detail = QLabel(text("tinysa_analyzer.settings.not_reviewed"), card)
        self._settings_detail.setProperty("ui2Role", "secondary")
        self._settings_detail.setWordWrap(True)
        self._settings_detail.setAccessibleName(text("tinysa_analyzer.settings.review.name"))
        card_layout.addWidget(self._settings_detail)
        actions = QHBoxLayout()
        self._review_settings = QPushButton(text("tinysa_analyzer.settings.review"), card)
        self._review_settings.setProperty("ui2Role", "utility-action")
        self._review_settings.setAccessibleName(text("tinysa_analyzer.settings.review.name"))
        self._review_settings.setAccessibleDescription(text("tinysa_analyzer.settings.review.description"))
        self._review_settings.clicked.connect(self._stage_settings)
        self._apply_settings = QPushButton(text("tinysa_analyzer.settings.apply"), card)
        self._apply_settings.setProperty("ui2Role", "utility-action")
        self._apply_settings.setAccessibleName(text("tinysa_analyzer.settings.apply.name"))
        self._apply_settings.setAccessibleDescription(text("tinysa_analyzer.settings.apply.description"))
        self._apply_settings.clicked.connect(self._confirm_settings)
        actions.addWidget(self._review_settings)
        actions.addWidget(self._apply_settings)
        actions.addStretch(1)
        card_layout.addLayout(actions)
        return card

    def _request_trace(self) -> None:
        source = self._view_model.state.source
        try:
            request = TinySaScanRawRequest(
                model=source.model,
                start_frequency_hz=round(self._start_mhz.value() * 1_000_000),
                stop_frequency_hz=round(self._stop_mhz.value() * 1_000_000),
                points=self._points.value(),
                deadline_seconds=120.0,
            )
        except (TypeError, ValueError) as error:
            self._show_error(str(error))
            return
        width = max(1, min(4_096, self._scene.width()))
        if not self._view_model.collect_trace(request, width):
            self._show_error(text("tinysa_analyzer.trace.unavailable"))

    def _stage_settings(self) -> None:
        try:
            plan = self._settings_plan()
        except (TypeError, ValueError) as error:
            self._show_error(str(error))
            return
        if not self._view_model.stage_settings(plan):
            self._show_error(text("tinysa_analyzer.settings.unavailable_review"))

    def _settings_plan(self) -> TinySaSweepSettingsPlan:
        rbw_mode = TinySaRbwMode(str(self._rbw_mode.currentData()))
        attenuation_mode = TinySaAttenuationMode(str(self._attenuation_mode.currentData()))
        return TinySaSweepSettingsPlan(
            accuracy=TinySaSweepAccuracy(str(self._accuracy.currentData())),
            rbw_mode=rbw_mode,
            rbw_hz=self._rbw_hz.value() if rbw_mode is TinySaRbwMode.MANUAL else None,
            sweep_time_ms=self._sweep_time_ms.value() if self._sweep_time_enabled.isChecked() else None,
            spur_removal=TinySaSpurPolicy(str(self._spur.currentData())),
            lna=TinySaSwitchPolicy(str(self._lna.currentData())),
            attenuation_mode=attenuation_mode,
            attenuation_db=self._attenuation_db.value()
            if attenuation_mode is TinySaAttenuationMode.MANUAL
            else None,
            repeat_count=self._repeat.value() if self._repeat_enabled.isChecked() else None,
        )

    def _confirm_settings(self) -> None:
        answer = QMessageBox.warning(
            self,
            text("tinysa_analyzer.dialog.title"), text("tinysa_analyzer.dialog.detail"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        self._view_model.confirm_settings(user_confirmed=answer is QMessageBox.StandardButton.Yes)

    def _render_state(self, state: TinySaAnalyzerViewState) -> None:
        snapshot = state.snapshot
        tone = {
            TinySaAnalyzerPhase.READY: StatusTone.INFO,
            TinySaAnalyzerPhase.BUSY: StatusTone.WARNING,
            TinySaAnalyzerPhase.TRACE_READY: StatusTone.SUCCESS,
            TinySaAnalyzerPhase.SETTINGS_REVIEW: StatusTone.WARNING,
            TinySaAnalyzerPhase.FAULTED: StatusTone.ERROR,
        }[snapshot.phase]
        self._state_chip.set_text(enum_text("tinysa_analyzer.phase", snapshot.phase))
        self._state_chip.set_tone(tone)
        self._busy.setVisible(state.busy)
        self._acquire.setEnabled(snapshot.can_collect and not state.busy)
        self._review_settings.setEnabled(snapshot.can_review_settings and not state.busy)
        self._apply_settings.setEnabled(snapshot.can_confirm_settings and not state.busy)
        if state.trace_frame is not None:
            self._scene.set_frame(state.trace_frame)
        if state.trace_result is not None:
            result = state.trace_result
            self._trace_summary.setText(
                text("tinysa_analyzer.trace.summary", source_points=result.presentation.source_point_count, display_points=result.presentation.display_point_count, elapsed=result.elapsed_seconds, rate=result.observed_points_per_second)
            )
        if state.settings_review is not None:
            commands = ", ".join(state.settings_review.commands)
            warnings = ", ".join(state.settings_review.warnings) or text("tinysa_analyzer.no")
            self._settings_detail.setText(
                text("tinysa_analyzer.settings.review_summary", commands=commands, warnings=warnings)
            )
        elif snapshot.reason is not None:
            self._settings_detail.setText(enum_text("tinysa_analyzer.reason", snapshot.reason))
        if state.error:
            self._show_error(state.error)
        else:
            self._error.setVisible(False)

    def _source_text(self) -> str:
        source = self._view_model.state.source
        assurance = enum_text("tinysa_identity.assurance", source.identity_assurance)
        return text("tinysa_analyzer.source.summary", label=source.label, model=source.model.value, assurance=assurance)

    def _show_error(self, message: str) -> None:
        self._error.set_content(text("tinysa_analyzer.error.title"), message)
        self._error.set_action("", enabled=False)
        self._error.setVisible(True)


def tinysa_analyzer_workspace_definition(
    view_model: TinySaAnalyzerViewModel,
    *,
    theme: ThemeId = ThemeId.DARK,
) -> WorkspaceDefinition:
    """Return an optional V2 analyzer page after a successful source composition."""

    return WorkspaceDefinition(
        workspace_id="tinysa-analyzer",
        label=text("tinysa_analyzer.workspace.label"),
        description=text("tinysa_analyzer.workspace.description"),
        icon=V2IconId.NAVIGATION,
        workspace_factory=lambda: TinySaAnalyzerWorkspaceV2(view_model, theme=theme),
        inspector_factory=lambda: _inspector(view_model, theme),
        optional=True,
        label_key="tinysa_analyzer.workspace.label",
        description_key="tinysa_analyzer.workspace.description",
    )


def _card(title: str, parent: QWidget) -> QFrame:
    card = QFrame(parent)
    card.setProperty("ui2Role", "card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.setSpacing(8)
    heading = QLabel(title, card)
    heading.setProperty("ui2Role", "section-heading")
    layout.addWidget(heading)
    return card


def _frequency_spin(value: float, maximum: float, accessible_name: str, parent: QWidget) -> QDoubleSpinBox:
    control = QDoubleSpinBox(parent)
    control.setProperty("ui2Role", "range-control")
    control.setRange(0.1, maximum)
    control.setDecimals(6)
    control.setValue(value)
    control.setSuffix(text("tinysa_analyzer.unit.mhz"))
    control.setAccessibleName(accessible_name)
    control.setAccessibleDescription(text("tinysa_analyzer.frequency.description"))
    return control


def _card_layout(card: QFrame) -> QVBoxLayout:
    layout = card.layout()
    if not isinstance(layout, QVBoxLayout):
        raise RuntimeError("tinySA V2 card has an invalid layout")
    return layout


def _enum_combo(
    accessible_name: str,
    translation_prefix: str,
    enum_type: type[StrEnum],
    parent: QWidget,
) -> QComboBox:
    control = QComboBox(parent)
    control.setProperty("ui2Role", "utility-select")
    control.setAccessibleName(accessible_name)
    for item in enum_type.__members__.values():
        control.addItem(enum_text(translation_prefix, item), item.value)
    return control


def _inspector(view_model: TinySaAnalyzerViewModel, theme: ThemeId) -> QWidget:
    inspector = QFrame()
    inspector.setProperty("ui2Root", True)
    inspector.setProperty("ui2Role", "panel")
    inspector.setStyleSheet(stylesheet_for_theme(theme))
    layout = QVBoxLayout(inspector)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.addWidget(SectionHeader(text("tinysa_analyzer.inspector.context"), text("tinysa_analyzer.header.title"), parent=inspector))
    summary = QLabel(inspector)
    summary.setProperty("ui2Role", "secondary")
    summary.setWordWrap(True)
    layout.addWidget(summary)
    boundary = QLabel(
        text("tinysa_analyzer.inspector.boundary"),
        inspector,
    )
    boundary.setProperty("ui2Role", "secondary")
    boundary.setWordWrap(True)
    layout.addWidget(boundary)
    layout.addStretch(1)

    def render(state: TinySaAnalyzerViewState) -> None:
        trace = state.trace_result
        trace_text = text("tinysa_analyzer.no") if trace is None else text("tinysa_analyzer.inspector.trace", points=trace.presentation.source_point_count, elapsed=trace.elapsed_seconds)
        summary.setText(
            text("tinysa_analyzer.inspector.summary", source=state.source.label, model=state.source.model.value, trace=trace_text, busy=text("tinysa_analyzer.yes") if state.busy else text("tinysa_analyzer.no"))
        )

    unsubscribe = view_model.subscribe(render)
    inspector.destroyed.connect(lambda: unsubscribe())
    return inspector


__all__ = ["TinySaAnalyzerWorkspaceV2", "tinysa_analyzer_workspace_definition"]
