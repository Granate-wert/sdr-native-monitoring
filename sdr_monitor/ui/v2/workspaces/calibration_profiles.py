"""UI2-09A read-only calibration profile browser."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sdr_monitor.domain import CalibrationApplicability, CalibrationProfile

from ..components import ErrorBanner, SectionHeader, StatusChipV2
from ..design import StatusTone, ThemeId, stylesheet_for_theme, tokens_for_theme
from ..design.icons import V2IconId
from ..i18n import text
from ..shell.contracts import WorkspaceDefinition
from ..view_models.calibration_view_model import CalibrationProfileViewModel, CalibrationProfileViewState
from ..view_models.live_calibration_view_model import LiveCalibrationState, LiveCalibrationViewModel
from sdr_monitor.services.live_calibration_signature import CalibrationFrontendContext


class CalibrationCorrectionPlot(QWidget):
    """Presentation of immutable profile points; no calibration mathematics lives here."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profile: CalibrationProfile | None = None
        self.setMinimumHeight(184)
        self.setAccessibleName(text("calibration.plot.accessible.name"))
        self.setAccessibleDescription(text("calibration.plot.accessible.description"))

    def set_profile(self, profile: CalibrationProfile | None) -> None:
        self._profile = profile
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override.
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#171E26"))
        profile = self._profile
        if profile is None:
            painter.setPen(QColor("#9EABB8"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, text("calibration.plot.empty")
            )
            painter.end()
            return
        points = profile.points
        left, right, top, bottom = 36, self.width() - 16, 20, self.height() - 30
        values = tuple(point.correction_db for point in points)
        low = min(values) - max(1.0, max(point.uncertainty_db for point in points))
        high = max(values) + max(1.0, max(point.uncertainty_db for point in points))
        frequency_span = max(points[-1].frequency_hz - points[0].frequency_hz, 1.0)
        value_span = max(high - low, 1e-9)
        painter.setPen(QPen(QColor("#2D3945"), 1))
        painter.drawRect(left, top, right - left, bottom - top)
        coordinates: list[tuple[float, float, float]] = []
        for point in points:
            x = left + (right - left) * (point.frequency_hz - points[0].frequency_hz) / frequency_span
            y = bottom - (bottom - top) * (point.correction_db - low) / value_span
            uncertainty = (bottom - top) * point.uncertainty_db / value_span
            coordinates.append((x, y, uncertainty))
        painter.setPen(QPen(QColor("#62788D"), 1))
        for x, y, uncertainty in coordinates:
            painter.drawLine(int(x), int(y - uncertainty), int(x), int(y + uncertainty))
        painter.setPen(QPen(QColor("#3CA6FF"), 2))
        for (left_x, left_y, _), (right_x, right_y, _) in zip(coordinates, coordinates[1:]):
            painter.drawLine(int(left_x), int(left_y), int(right_x), int(right_y))
        painter.setPen(QColor("#9EABB8"))
        painter.drawText(left, self.height() - 8, text("calibration.plot.frequency"))
        painter.drawText(4, top + 12, text("calibration.plot.db"))
        painter.drawText(right - 170, top + 14, text("calibration.plot.legend"))
        painter.end()


class CalibrationProfilesWorkspaceV2(QWidget):
    """Read-only browser; activation/import/finalization are deliberately unavailable."""

    def __init__(self, view_model: CalibrationProfileViewModel, *,
                 live_calibration: LiveCalibrationViewModel | None = None,
                 theme: ThemeId = ThemeId.DARK, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._theme = theme
        self._selection_sync = False
        self._live_calibration = live_calibration
        self._live_unsubscribe = None
        self._build_ui()
        self.set_theme(theme)
        self._unsubscribe = view_model.subscribe(self._render_state)
        if live_calibration is not None:
            self._live_unsubscribe = live_calibration.subscribe(self._render_live)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override.
        self._unsubscribe()
        if self._live_unsubscribe is not None:
            self._live_unsubscribe()
            self._live_unsubscribe = None
        if self._live_calibration is not None:
            self._live_calibration.close_binding()
        super().closeEvent(event)

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.setStyleSheet(stylesheet_for_theme(theme))
        self._state_chip.set_theme(theme)
        for field in getattr(self, "_frontend_fields", ()):
            field.setStyleSheet(f"color: {tokens_for_theme(theme).colors.primary_text};")

    def _build_ui(self) -> None:
        self.setProperty("ui2Root", True)
        self.setAccessibleName(text("calibration.accessible.name"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(
            SectionHeader(
                text("calibration.header.title"), text("calibration.header.detail"), parent=self
            )
        )
        command = QFrame(self)
        command.setProperty("ui2Role", "card")
        command_row = QHBoxLayout(command)
        self._refresh_button = QPushButton(text("calibration.refresh"), command)
        self._refresh_button.setProperty("ui2Role", "primary-action")
        self._refresh_button.clicked.connect(self._view_model.refresh)
        command_row.addWidget(self._refresh_button)
        self._state_chip = StatusChipV2(
            text("calibration.inspector.not_loaded"), tone=StatusTone.NEUTRAL, parent=command
        )
        command_row.addWidget(self._state_chip)
        command_row.addStretch(1)
        layout.addWidget(command)
        if self._live_calibration is not None:
            self._build_live_commands(layout)
        self._error_banner = ErrorBanner(text("calibration.error.title"), "", parent=self)
        self._error_banner.setVisible(False)
        layout.addWidget(self._error_banner)
        content = QHBoxLayout()
        profile_card = _card(text("calibration.card.profiles"), self)
        profile_layout = profile_card.layout()
        assert isinstance(profile_layout, QVBoxLayout)
        self._profiles = QListWidget(profile_card)
        self._profiles.setProperty("ui2Role", "data-list")
        self._profiles.setAccessibleName(text("calibration.profiles.accessible"))
        self._profiles.currentItemChanged.connect(self._select_item)
        profile_layout.addWidget(self._profiles, 1)
        content.addWidget(profile_card, 1)
        detail_card = _card(text("calibration.card.correction"), self)
        detail_layout = detail_card.layout()
        assert isinstance(detail_layout, QVBoxLayout)
        self._plot = CalibrationCorrectionPlot(detail_card)
        detail_layout.addWidget(self._plot, 1)
        self._profile_detail = _secondary(text("calibration.profile.select"), detail_card)
        self._profile_detail.setWordWrap(True)
        detail_layout.addWidget(self._profile_detail)
        content.addWidget(detail_card, 2)
        applicability_card = _card(text("calibration.card.applicability"), self)
        applicability_layout = applicability_card.layout()
        assert isinstance(applicability_layout, QVBoxLayout)
        self._applicability = QTableWidget(0, 4, applicability_card)
        self._applicability.setProperty("ui2Role", "data-table")
        self._applicability.setHorizontalHeaderLabels(
            tuple(text("calibration.applicability.headers").split("|"))
        )
        self._applicability.verticalHeader().setVisible(False)
        self._applicability.setAccessibleName(text("calibration.applicability.accessible"))
        applicability_layout.addWidget(self._applicability, 1)
        boundary = _secondary(
            text("calibration.applicability.boundary"),
            applicability_card,
        )
        boundary.setWordWrap(True)
        applicability_layout.addWidget(boundary)
        content.addWidget(applicability_card, 2)
        layout.addLayout(content, 1)

    def _build_live_commands(self, layout: QVBoxLayout) -> None:
        assert self._live_calibration is not None
        card = _card(text("live_calibration.title"), self)
        rows = card.layout()
        assert isinstance(rows, QVBoxLayout)
        frontend = QHBoxLayout()
        self._frontend_fields = []
        binding = self._live_calibration.state.binding
        values = (("", "", "") if binding is None else
                  (binding.frontend.rf_port_path, binding.frontend.frontend_chain, binding.frontend.reference_plane))
        for key, value in zip(("port", "chain", "plane"), values):
            field = QLineEdit(card)
            field.setProperty("ui2Role", "command-field")
            field.setText(value)
            field.setMaxLength(128)
            field.setPlaceholderText(text("live_calibration." + key))
            field.setAccessibleName(text("live_calibration." + key))
            field.setToolTip(text("live_calibration.frontend_help"))
            field.textEdited.connect(self._frontend_edited)
            self._frontend_fields.append(field)
            frontend.addWidget(field)
        rows.addLayout(frontend)
        commands = QHBoxLayout()
        self._bind = QPushButton(text("live_calibration.bind"), card)
        self._preview = QPushButton(text("live_calibration.preview"), card)
        self._select = QPushButton(text("live_calibration.select"), card)
        self._clear = QPushButton(text("live_calibration.clear"), card)
        self._bind.clicked.connect(self._bind_live)
        self._preview.clicked.connect(self._preview_live)
        self._select.clicked.connect(self._live_calibration.select)
        self._clear.clicked.connect(self._live_calibration.clear)
        for button in (self._bind, self._preview, self._select, self._clear):
            button.setProperty("ui2Role", "primary-action")
            button.setAccessibleName(button.text())
            button.setToolTip(text("live_calibration.boundary"))
            commands.addWidget(button)
        rows.addLayout(commands)
        self._live_status = QLabel(card)
        self._live_status.setProperty("ui2Role", "secondary")
        self._live_status.setWordWrap(True)
        self._live_status.setTextFormat(Qt.TextFormat.PlainText)
        self._live_status.setAccessibleName(text("live_calibration.title"))
        rows.addWidget(self._live_status)
        boundary = _secondary(text("live_calibration.boundary"), card)
        boundary.setWordWrap(True)
        rows.addWidget(boundary)
        layout.addWidget(card)

    def _frontend_edited(self, *_args) -> None:
        if self._live_calibration is not None:
            self._live_calibration.frontend_edited()

    def _bind_live(self) -> None:
        assert self._live_calibration is not None
        try:
            frontend = CalibrationFrontendContext(*(field.text().strip() for field in self._frontend_fields))
        except ValueError as error:
            self._live_status.setText(text("live_calibration.refused", detail=str(error)))
            return
        self._live_calibration.bind(frontend)

    def _preview_live(self) -> None:
        assert self._live_calibration is not None
        profile = self._view_model.state.selected
        if profile is not None:
            self._live_calibration.preview(profile)

    def _render_live(self, state: LiveCalibrationState) -> None:
        if self._live_calibration is None:
            return
        self._bind.setEnabled(state.available and not state.busy)
        bound = state.binding is not None and not state.busy
        self._preview.setEnabled(bound and self._view_model.state.selected is not None)
        self._select.setEnabled(bound and state.preview is not None and state.preview.profile is not None
                                and state.preview.profile == self._view_model.state.selected
                                and state.preview.applicability is not None and state.preview.applicability.applicable)
        self._clear.setEnabled(bound)
        binding = state.binding
        lines = [text("live_calibration.phase." + state.phase)]
        if binding is not None:
            lines.append(text("live_calibration.bound", rx=binding.endpoint.selection.value.upper(),
                              source=binding.endpoint.source_id, port=binding.frontend.rf_port_path,
                              chain=binding.frontend.frontend_chain, plane=binding.frontend.reference_plane))
        if state.preview is not None:
            preview = state.preview
            profile = preview.profile
            lines.append(text("live_calibration.preview_detail", profile="—" if profile is None else profile.profile_id,
                              version="—" if profile is None else profile.profile_version,
                              result="—" if preview.applicability is None else preview.applicability.reason))
        if state.acknowledged is not None:
            lines.append(text("live_calibration.ack." + state.acknowledged))
        if state.error:
            lines.append(text("live_calibration.refused", detail=state.error))
        self._live_status.setText("\n".join(lines))

    def _select_item(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if self._selection_sync:
            return
        profile = None if current is None else current.data(Qt.ItemDataRole.UserRole)
        self._view_model.select(profile if isinstance(profile, CalibrationProfile) else None)

    def _render_state(self, state: CalibrationProfileViewState) -> None:
        self._refresh_button.setEnabled(not state.busy)
        self._render_profiles(state)
        self._render_profile(state.selected)
        self._render_applicability(state.applicability)
        if self._live_calibration is not None:
            self._render_live(self._live_calibration.state)
        if state.error:
            self._error_banner.set_content(text("calibration.error.title"), state.error)
            self._error_banner.set_action("", enabled=False)
            self._error_banner.setVisible(True)
        else:
            self._error_banner.setVisible(False)
        if state.busy:
            self._state_chip.set_text(text("calibration.state.busy"))
            self._state_chip.set_tone(StatusTone.WARNING)
        elif state.selected is not None:
            self._state_chip.set_text(text("calibration.state.selected"))
            self._state_chip.set_tone(StatusTone.INFO)
        else:
            self._state_chip.set_text(
                text("calibration.inspector.not_loaded")
                if not state.profiles
                else text("calibration.state.select")
            )
            self._state_chip.set_tone(StatusTone.NEUTRAL)

    def _render_profiles(self, state: CalibrationProfileViewState) -> None:
        self._selection_sync = True
        try:
            self._profiles.clear()
            selected_row = -1
            for row, profile in enumerate(state.profiles):
                item = QListWidgetItem(f"{profile.profile_id} · v{profile.profile_version}")
                item.setData(Qt.ItemDataRole.UserRole, profile)
                self._profiles.addItem(item)
                if profile == state.selected:
                    selected_row = row
            if selected_row >= 0:
                self._profiles.setCurrentRow(selected_row)
        finally:
            self._selection_sync = False

    def _render_profile(self, profile: CalibrationProfile | None) -> None:
        self._plot.set_profile(profile)
        if profile is None:
            self._profile_detail.setText(text("calibration.profile.select"))
            return
        self._profile_detail.setText(
            text(
                "calibration.profile.detail",
                profile_id=profile.profile_id,
                version=profile.profile_version,
                points=len(profile.points),
                start=profile.points[0].frequency_hz / 1e6,
                stop=profile.points[-1].frequency_hz / 1e6,
                plane=profile.reference_plane,
                equipment=profile.reference_equipment or text("calibration.not_published"),
                created=profile.created_at,
                fingerprint=profile.fingerprint[:16],
            )
        )

    def _render_applicability(self, applicability: CalibrationApplicability | None) -> None:
        rows = () if applicability is None else applicability.rows
        self._applicability.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = (
                item.label,
                item.expected,
                item.actual,
                "OK" if item.matches else text("calibration.status.mismatch"),
            )
            for column, value in enumerate(values):
                self._applicability.setItem(row, column, QTableWidgetItem(value))


def calibration_profiles_workspace_definition(
    view_model: CalibrationProfileViewModel,
    *,
    live_calibration: LiveCalibrationViewModel | None = None,
    theme: ThemeId = ThemeId.DARK,
) -> WorkspaceDefinition:
    return WorkspaceDefinition(
        workspace_id="calibration",
        label=text("calibration.workspace.label"),
        description=text("calibration.workspace.description"),
        icon=V2IconId.INFO,
        workspace_factory=lambda: CalibrationProfilesWorkspaceV2(view_model, live_calibration=live_calibration, theme=theme),
        inspector_factory=lambda: _inspector(view_model, theme),
        label_key="calibration.workspace.label",
        description_key="calibration.workspace.description",
    )


def _inspector(view_model: CalibrationProfileViewModel, theme: ThemeId) -> QWidget:
    inspector = QFrame()
    inspector.setProperty("ui2Root", True)
    inspector.setProperty("ui2Role", "panel")
    inspector.setStyleSheet(stylesheet_for_theme(theme))
    layout = QVBoxLayout(inspector)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.addWidget(
        SectionHeader(
            text("calibration.inspector.context"),
            text("calibration.inspector.detail"),
            parent=inspector,
        )
    )
    detail = _secondary(text("calibration.inspector.not_loaded"), inspector)
    detail.setWordWrap(True)
    layout.addWidget(detail)
    layout.addWidget(_secondary(text("calibration.inspector.boundary"), inspector))
    layout.addStretch(1)

    def render(state: CalibrationProfileViewState) -> None:
        selected = text("calibration.no") if state.selected is None else f"{state.selected.profile_id} v{state.selected.profile_version}"
        detail.setText(
            text(
                "calibration.inspector.summary",
                profiles=len(state.profiles),
                selected=selected,
                busy=text("calibration.yes") if state.busy else text("calibration.no"),
            )
        )

    unsubscribe = view_model.subscribe(render)
    inspector.destroyed.connect(lambda: unsubscribe())
    return inspector


def _card(title: str, parent: QWidget) -> QFrame:
    card = QFrame(parent)
    card.setProperty("ui2Role", "card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(10, 10, 10, 10)
    layout.addWidget(_secondary(title, card))
    return card


def _secondary(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("ui2Role", "secondary")
    return label
