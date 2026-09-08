"""Graph-first UI2-07 Live core over an externally owned public view model."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.domain.live_configuration_patch import LiveConfigurationPatch

from ..components import CommandField, ErrorBanner, PrimaryActionButton, StatusChipV2
from ..design import StatusTone, ThemeId, stylesheet_for_theme
from ..design.icons import V2IconId
from ..i18n import text
from ..shell.contracts import WorkspaceDefinition
from ..spectrum import PersistenceDensityFrame, TraceKind
from ..state.live_view_state import CalibrationPresentation, LiveAction, LiveViewState
from ..view_models.live_view_model import LiveViewModel
from ..waterfall import SpectrumWaterfallView, WaterfallLineFrame


@dataclass(frozen=True, slots=True)
class _SpectrumFrame:
    """No-copy adapter retaining exactly the external spectrum arrays and unit."""

    source: object
    frequencies_hz: np.ndarray
    values: np.ndarray
    unit: str


class LiveWorkspaceV2(QWidget):
    """Inject one V2 view model; never construct or shut down a presenter/service."""

    def __init__(
        self,
        view_model: LiveViewModel,
        *,
        theme: ThemeId = ThemeId.DARK,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._theme = theme
        self._last_applied_configuration: LiveConfiguration | None = None
        self._form_dirty = False
        self._draft_snapshot = None
        self._pending_configuration = None
        self._last_spectrum_source: object | None = None
        self._last_persistence_source: object | None = None
        self._last_waterfall_source: object | None = None
        self._last_measurement_signature: tuple[object, ...] | None = None
        self._build_ui()
        self.set_theme(theme)
        self._unsubscribe_state = view_model.subscribe(self._render_state)
        self._unsubscribe_devices = view_model.subscribe_devices(self._render_devices)
        view_model.refresh_presentation()

    @property
    def visualization(self) -> SpectrumWaterfallView:
        return self._visualization

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.setStyleSheet(stylesheet_for_theme(theme))
        self._visualization.set_theme(theme)
        for chip in (self._connection_chip, self._calibration_chip, self._backend_chip, self._drops_chip):
            chip.set_theme(theme)

    def closeEvent(self, event) -> None:
        self._unsubscribe_state()
        self._unsubscribe_devices()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        self.setProperty("ui2Root", True)
        self.setAccessibleName(text("live.accessible.name"))
        self.setAccessibleDescription(text("live.accessible.description"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._build_command_bar())
        layout.addWidget(self._build_health_bar())
        self._error_banner = ErrorBanner(text("live.error.title"), "", parent=self)
        self._error_banner.setVisible(False)
        layout.addWidget(self._error_banner)
        self._visualization = SpectrumWaterfallView(theme=self._theme, parent=self)
        layout.addWidget(self._visualization, 1)
        self._space_live_shortcut = QShortcut(
            QKeySequence(Qt.Key.Key_Space), self._visualization.spectrum_scene
        )
        self._space_live_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._space_live_shortcut.activated.connect(self._execute_space_live_action)
        layout.addWidget(self._build_configuration_bar())
        self._contract_note = _secondary_label(
            text("live.contract_note"),
            text("live.contract_note.name"),
            self,
        )
        self._contract_note.setWordWrap(True)
        layout.addWidget(self._contract_note)

    def _build_command_bar(self) -> QWidget:
        bar = QFrame(self)
        bar.setProperty("ui2Role", "card")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(8)
        self._device_selector = QComboBox(bar)
        self._device_selector.setProperty("ui2Role", "utility-select")
        self._device_selector.setAccessibleName(text("live.device_selector.name"))
        self._device_selector.addItem(text("live.device.unselected"), None)
        self._device_selector.currentIndexChanged.connect(self._select_device)
        row.addWidget(self._device_selector, 1)
        discover = QPushButton(text("live.discover"), bar)
        discover.setProperty("ui2Role", "utility-action")
        discover.setAccessibleName(text("live.discover.name"))
        discover.clicked.connect(self._view_model.discover_devices)
        row.addWidget(discover)
        self._manual_uri = CommandField(text("live.uri.label"), placeholder=text("live.uri.placeholder"), parent=bar)
        self._manual_uri.setMaximumWidth(260)
        row.addWidget(self._manual_uri)
        manual = QPushButton(text("live.uri.use"), bar)
        manual.setProperty("ui2Role", "utility-action")
        manual.setAccessibleName(text("live.uri.use.name"))
        manual.clicked.connect(self._select_manual_uri)
        row.addWidget(manual)
        self._manual_uri_error = _secondary_label("", text("live.uri.error.name"), bar)
        self._manual_uri_error.setProperty("ui2Tone", "warning")
        self._manual_uri_error.setVisible(False)
        row.addWidget(self._manual_uri_error)
        self._primary_action = PrimaryActionButton(
            text("live.primary.discover_device"),
            accessible_description=text("live.primary.name"),
            parent=bar,
        )
        self._primary_action.clicked.connect(self._execute_primary)
        row.addWidget(self._primary_action)
        return bar

    def _build_health_bar(self) -> QWidget:
        bar = QFrame(self)
        bar.setProperty("ui2Role", "card")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        self._connection_chip = StatusChipV2(text("live_state.connection.none"), tone=StatusTone.NEUTRAL, parent=bar)
        self._calibration_chip = StatusChipV2(
            text("live_state.calibration.uncalibrated"), tone=StatusTone.WARNING, parent=bar
        )
        self._backend_chip = StatusChipV2(text("live_state.backend.empty"), tone=StatusTone.NEUTRAL, parent=bar)
        self._drops_chip = StatusChipV2(text("live.losses", count=0), tone=StatusTone.SUCCESS, parent=bar)
        for chip in (self._connection_chip, self._calibration_chip, self._backend_chip, self._drops_chip):
            row.addWidget(chip)
        row.addStretch(1)
        self._recording = QPushButton(text("live.recording.unavailable"), bar)
        self._recording.setProperty("ui2Role", "utility-action")
        self._recording.setEnabled(False)
        self._recording.setAccessibleName(text("live.recording.unavailable.name"))
        row.addWidget(self._recording)
        return bar

    def _build_configuration_bar(self) -> QWidget:
        bar = QFrame(self)
        bar.setProperty("ui2Role", "card")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(8)
        self._center_mhz = _numeric_spin(
            bar,
            accessible_name=text("live.center_frequency.name"),
            lower=0.001,
            upper=8_000.0,
            value=2_400.0,
            suffix=text("live.center_frequency.suffix"),
        )
        self._sample_rate_mhz = _numeric_spin(
            bar,
            accessible_name=text("live.sample_rate.name"),
            lower=0.001,
            upper=100.0,
            value=20.0,
            suffix=text("live.sample_rate.suffix"),
        )
        self._gain_db = _numeric_spin(
            bar,
            accessible_name=text("live.gain.name"),
            lower=0.0,
            upper=100.0,
            value=18.0,
            suffix=text("live.gain.suffix"),
        )
        self._backend = QComboBox(bar)
        self._backend.setProperty("ui2Role", "utility-select")
        self._backend.setAccessibleName(text("live.backend.name"))
        for backend in BackendKind:
            self._backend.addItem(backend.value.upper(), backend.value)
        self._apply_button = QPushButton(text("live.apply"), bar)
        self._apply_button.setProperty("ui2Role", "utility-action")
        self._apply_button.setAccessibleName(text("live.apply.name"))
        self._apply_button.clicked.connect(self._apply_configuration)
        self._cancel_button = QPushButton(text("live.cancel_changes"), bar)
        self._cancel_button.setProperty("ui2Role", "utility-action")
        self._cancel_button.setAccessibleName(text("live.cancel_changes.name"))
        self._cancel_button.clicked.connect(self._cancel_local_configuration)
        self._cancel_button.setEnabled(False)
        for control in (
            self._center_mhz,
            self._sample_rate_mhz,
            self._gain_db,
            self._backend,
            self._apply_button,
            self._cancel_button,
        ):
            row.addWidget(control)
        row.addStretch(1)
        layout.addLayout(row)
        self._configuration_status = _secondary_label(
            text("live.configuration.no_applied"),
            text("live.configuration.status.name"),
            bar,
        )
        self._configuration_status.setWordWrap(True)
        layout.addWidget(self._configuration_status)
        self._center_mhz.valueChanged.connect(self._on_local_configuration_changed)
        self._sample_rate_mhz.valueChanged.connect(self._on_local_configuration_changed)
        self._gain_db.valueChanged.connect(self._on_local_configuration_changed)
        self._backend.currentIndexChanged.connect(self._on_local_configuration_changed)
        return bar

    def _render_devices(self, devices: tuple[object, ...]) -> None:
        current = self._device_selector.currentData()
        with QSignalBlocker(self._device_selector):
            self._device_selector.clear()
            self._device_selector.addItem(text("live.device.unselected"), None)
            for device in devices:
                identifier = str(getattr(device, "device_id", "")).strip()
                if identifier:
                    self._device_selector.addItem(str(getattr(device, "label", identifier)), identifier)
            index = self._device_selector.findData(current)
            self._device_selector.setCurrentIndex(max(0, index))

    def _render_state(self, state: LiveViewState) -> None:
        self._connection_chip.set_text(state.acquisition_label, detail=state.connection_label)
        self._connection_chip.set_tone(StatusTone.SUCCESS if state.primary_action is LiveAction.STOP else StatusTone.INFO)
        calibration_tone = {
            CalibrationPresentation.CALIBRATED: StatusTone.SUCCESS,
            CalibrationPresentation.MISMATCH: StatusTone.WARNING,
        }.get(state.calibration, StatusTone.WARNING)
        self._calibration_chip.set_text(state.calibration_label)
        self._calibration_chip.set_tone(calibration_tone)
        self._backend_chip.set_text(state.backend_label)
        self._backend_chip.set_tone(StatusTone.INFO)
        drops = state.loss.source_blocks + state.loss.acquisition_blocks + state.loss.fft_frames
        self._drops_chip.set_text(text("live.losses", count=drops))
        self._drops_chip.set_tone(StatusTone.WARNING if drops else StatusTone.SUCCESS)
        self._render_error(state)
        self._sync_configuration_from_snapshot(state)
        self._update_primary_action(state)
        self._render_published_frames(state)

    def _render_error(self, state: LiveViewState) -> None:
        """Expose only a snapshot-provided failure; do not infer a retry operation."""

        if state.error_label is None:
            self._error_banner.setVisible(False)
            return
        category = (
            text("live.error.category.unpublished")
            if state.error_kind is None
            else text("live.error.category.value", category=state.error_kind)
        )
        self._error_banner.set_content(text("live.error.title"), f"{state.error_label}\n{category}")
        self._error_banner.set_action("", enabled=False)
        self._error_banner.setVisible(True)

    def _sync_configuration_from_snapshot(self, state: LiveViewState) -> None:
        snapshot = state.snapshot
        applied = getattr(snapshot, "applied", None)
        configuration = getattr(applied, "applied", None)
        if self._form_dirty and self._draft_snapshot is not None:
            old_session, old_generation, old_source = _configuration_identity(self._draft_snapshot)
            new_session, new_generation, new_source = _configuration_identity(snapshot)
            confirmed = (
                configuration == self._pending_configuration and configuration is not None
                and new_session == old_session and new_source == old_source
                and isinstance(new_generation, int) and isinstance(old_generation, int)
                and new_generation > old_generation and getattr(snapshot, "error", None) is None
            )
            if confirmed:
                self._draft_snapshot = None
                self._form_dirty = False
                self._pending_configuration = None
            else:
                if (_configuration_identity(snapshot) != _configuration_identity(self._draft_snapshot)
                        or configuration != self._last_applied_configuration):
                    self._configuration_status.setText(text("live.configuration.conflict"))
                return
        if configuration is None or configuration == self._last_applied_configuration:
            return
        with QSignalBlocker(self._center_mhz):
            self._center_mhz.setValue(float(getattr(configuration, "center_hz", 2.4e9)) / 1e6)
        with QSignalBlocker(self._sample_rate_mhz):
            self._sample_rate_mhz.setValue(float(getattr(configuration, "sample_rate_hz", 20e6)) / 1e6)
        with QSignalBlocker(self._gain_db):
            self._gain_db.setValue(float(getattr(configuration, "gain_db", 18.0)))
        backend = str(getattr(getattr(configuration, "backend", None), "value", "auto"))
        with QSignalBlocker(self._backend):
            index = self._backend.findData(backend)
            if index >= 0:
                self._backend.setCurrentIndex(index)
        if isinstance(configuration, LiveConfiguration):
            self._last_applied_configuration = configuration
        else:
            self._last_applied_configuration = None
        self._set_form_dirty(False)

    def _on_local_configuration_changed(self, _value: object) -> None:
        """Track only uncommitted presentation fields; never reconfigure a receiver."""

        applied = self._last_applied_configuration
        if applied is None:
            self._set_form_dirty(False)
            return
        self._set_form_dirty(self._current_configuration() != applied)

    def _set_form_dirty(self, dirty: bool) -> None:
        if dirty and not self._form_dirty:
            self._draft_snapshot = self._view_model.state.snapshot
        elif not dirty:
            self._draft_snapshot = None
        self._form_dirty = bool(dirty)
        self._cancel_button.setEnabled(self._form_dirty and self._last_applied_configuration is not None)
        if self._last_applied_configuration is None:
            self._configuration_status.setText(text("live.configuration.no_applied"))
        elif self._form_dirty:
            self._configuration_status.setText(text("live.configuration.local_changes"))
        else:
            profile = self._last_applied_configuration.profile_id or text("live.profile.none")
            self._configuration_status.setText(text("live.configuration.applied", profile=profile))
        self._update_primary_action(self._view_model.state)

    def _update_primary_action(self, state: LiveViewState) -> None:
        action_label = state.primary_action_label
        requires_apply = state.primary_action is LiveAction.START and (
            not state.has_applied_configuration or state.configuration_dirty or self._form_dirty
        )
        if requires_apply:
            action_label = text("live.primary.apply_settings")
        self._primary_action.set_action_text(action_label)
        self._primary_action.set_busy(state.busy, activity=state.acquisition_label)
        if not state.busy:
            self._primary_action.setEnabled(state.primary_action_enabled)

    def _render_published_frames(self, state: LiveViewState) -> None:
        identity = getattr(state.analyzer_bundle, "identity", None)
        signature = None if identity is None else (
            identity.source_id, identity.receiver_id, identity.acquisition_epoch,
            identity.config_generation, identity.unit,
            identity.frequencies_hz.shape, identity.frequencies_hz.tobytes(),
        )
        if (signature is not None and self._last_measurement_signature is not None
                and signature != self._last_measurement_signature):
            self._visualization.waterfall_pane.clear_history()
            self._last_waterfall_source = None
        if signature is not None:
            self._last_measurement_signature = signature
        if state.spectrum is None and self._last_spectrum_source is not None:
            self._visualization.spectrum_scene.clear_trace(TraceKind.CURRENT)
            self._last_spectrum_source = None
        if state.spectrum is not None and state.spectrum is not self._last_spectrum_source:
            spectrum: object | None = state.analyzer_bundle
            if spectrum is None:
                unit = str(getattr(state.snapshot, "unit", "")).strip()
                spectrum = _adapt_spectrum(state.spectrum, unit)
            if spectrum is not None:
                self._visualization.spectrum_scene.set_frame(spectrum)
                self._last_spectrum_source = state.spectrum
        if isinstance(state.persistence_frame, PersistenceDensityFrame) and (
            state.persistence_frame is not self._last_persistence_source
        ):
            self._visualization.spectrum_scene.set_persistence_frame(state.persistence_frame)
            self._last_persistence_source = state.persistence_frame
        elif state.persistence_frame is None and self._last_persistence_source is not None:
            self._visualization.spectrum_scene.clear_persistence_display()
            self._last_persistence_source = None
        if isinstance(state.waterfall_line, WaterfallLineFrame) and (
            state.waterfall_line is not self._last_waterfall_source
        ):
            self._visualization.waterfall_pane.set_line(state.waterfall_line)
            self._last_waterfall_source = state.waterfall_line
        elif state.waterfall_line is None and self._last_waterfall_source is not None:
            self._visualization.waterfall_pane.clear_history()
            self._last_waterfall_source = None

    def _select_device(self, index: int) -> None:
        identifier = self._device_selector.itemData(index)
        if isinstance(identifier, str):
            self._view_model.select_device(identifier)

    def _select_manual_uri(self) -> None:
        value, error = _validated_manual_uri(self._manual_uri.value)
        self._manual_uri_error.setText(error or "")
        self._manual_uri_error.setVisible(error is not None)
        if value is not None:
            self._view_model.select_manual_uri(value)

    def _execute_primary(self) -> None:
        state = self._view_model.state
        if state.primary_action is LiveAction.START and (
            not state.has_applied_configuration or state.configuration_dirty or self._form_dirty
        ):
            self._apply_configuration()
            return
        self._view_model.execute_primary_action()

    def _execute_space_live_action(self) -> None:
        """Delegate Space only for an already-public Start/Stop action."""

        state = self._view_model.state
        if state.primary_action not in {LiveAction.START, LiveAction.STOP}:
            return
        if not state.primary_action_enabled:
            return
        self._execute_primary()

    def _apply_configuration(self) -> None:
        if (self._draft_snapshot is not None and
                _configuration_identity(self._draft_snapshot) !=
                _configuration_identity(self._view_model.state.snapshot)):
            self._configuration_status.setText(text("live.configuration.conflict"))
            return
        configuration = self._current_configuration()
        base = self._last_applied_configuration
        if base is not None:
            snapshot = self._draft_snapshot or self._view_model.state.snapshot
            if (not getattr(snapshot, "session_id", None)
                    or not getattr(getattr(snapshot, "device", None), "device_id", None)
                    or type(getattr(snapshot, "generation", None)) is not int):
                self._configuration_status.setText(text("live.configuration.identity_missing"))
                return
            request = LiveConfigurationPatch(
                expected_session_id=getattr(snapshot, "session_id", ""),
                expected_generation=getattr(snapshot, "generation", -1),
                expected_source_id=getattr(getattr(snapshot, "device", None), "device_id", ""),
                changes=tuple(
                    (field.name, getattr(configuration, field.name))
                    for field in fields(configuration)
                    if getattr(configuration, field.name) != getattr(base, field.name)
                ),
            )
        else:
            request = configuration
        if self._view_model.apply_configuration(request):
            self._pending_configuration = configuration
            self._configuration_status.setText(text("live.configuration.requested"))

    def _cancel_local_configuration(self) -> None:
        """Restore fields from the last applied snapshot without calling a presenter."""

        self._draft_snapshot = None
        self._pending_configuration = None
        self._form_dirty = False
        self._sync_configuration_from_snapshot(self._view_model.state)
        applied = self._last_applied_configuration
        if applied is None:
            return
        with QSignalBlocker(self._center_mhz):
            self._center_mhz.setValue(applied.center_hz / 1e6)
        with QSignalBlocker(self._sample_rate_mhz):
            self._sample_rate_mhz.setValue(applied.sample_rate_hz / 1e6)
        with QSignalBlocker(self._gain_db):
            self._gain_db.setValue(applied.gain_db)
        with QSignalBlocker(self._backend):
            self._backend.setCurrentIndex(self._backend.findData(applied.backend.value))
        self._set_form_dirty(False)

    def _current_configuration(self) -> LiveConfiguration:
        backend = BackendKind(str(self._backend.currentData()))
        base = self._last_applied_configuration or LiveConfiguration()
        return replace(
            base,
            center_hz=self._center_mhz.value() * 1e6,
            sample_rate_hz=self._sample_rate_mhz.value() * 1e6,
            gain_db=self._gain_db.value(),
            backend=backend,
        )


def _configuration_identity(snapshot: object) -> tuple[object, object, object]:
    return (getattr(snapshot, "session_id", None), getattr(snapshot, "generation", None),
            getattr(getattr(snapshot, "device", None), "device_id", None))


def live_workspace_definition(view_model: LiveViewModel, *, theme: ThemeId = ThemeId.DARK) -> WorkspaceDefinition:
    """Return an externally supplied replacement for the inert `live` placeholder."""

    def workspace_factory() -> QWidget:
        return LiveWorkspaceV2(view_model, theme=theme)

    def inspector_factory() -> QWidget:
        return _live_inspector(view_model, theme=theme)

    return WorkspaceDefinition(
        workspace_id="live",
        label=text("live.workspace.label"),
        description=text("live.workspace.description"),
        icon=V2IconId.NAVIGATION,
        workspace_factory=workspace_factory,
        inspector_factory=inspector_factory,
        label_key="live.workspace.label",
        description_key="live.workspace.description",
    )


def _live_inspector(view_model: LiveViewModel, *, theme: ThemeId) -> QWidget:
    inspector = QFrame()
    inspector.setProperty("ui2Root", True)
    inspector.setProperty("ui2Role", "panel")
    inspector.setStyleSheet(stylesheet_for_theme(theme))
    layout = QVBoxLayout(inspector)
    heading = QLabel(text("live.inspector.heading"), inspector)
    heading.setProperty("ui2Role", "section-heading")
    layout.addWidget(heading)
    summary = _secondary_label(text("live.inspector.waiting"), text("live.inspector.state.name"), inspector)
    summary.setWordWrap(True)
    layout.addWidget(summary)
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    applied = _secondary_label(text("live.inspector.not_applied"), text("live.inspector.applied.name"), inspector)
    applied.setObjectName("v2-live-inspector-applied")
    quality = _secondary_label(text("live.inspector.no_data"), text("live.inspector.quality.name"), inspector)
    quality.setObjectName("v2-live-inspector-quality")
    losses = _secondary_label(text("live.inspector.no_data"), text("live.inspector.losses.name"), inspector)
    losses.setObjectName("v2-live-inspector-losses")
    frames = _secondary_label(text("live.inspector.no_frames"), text("live.inspector.frames.name"), inspector)
    frames.setObjectName("v2-live-inspector-frames")
    for label in (applied, quality, losses, frames):
        label.setWordWrap(True)
    form.addRow(text("live.inspector.applied.label"), applied)
    form.addRow(text("live.inspector.quality.label"), quality)
    form.addRow(text("live.inspector.losses.label"), losses)
    form.addRow(text("live.inspector.frames.label"), frames)
    layout.addLayout(form)
    error = _secondary_label("", text("live.error.title"), inspector)
    error.setProperty("ui2Tone", "warning")
    error.setWordWrap(True)
    error.setVisible(False)
    layout.addWidget(error)
    boundary = _secondary_label(
        text("live.inspector.boundary"),
        text("live.inspector.boundary.name"),
        inspector,
    )
    boundary.setWordWrap(True)
    layout.addWidget(boundary)
    layout.addStretch(1)

    def render(state: LiveViewState) -> None:
        summary.setText(
            text(
                "live.inspector.summary",
                connection=state.connection_label,
                acquisition=state.acquisition_label,
                device=state.device_label,
            )
        )
        applied.setText(_applied_configuration_detail(state))
        quality.setText(
            text(
                "live.inspector.quality",
                calibration=state.calibration_label,
                backend=state.backend_label,
                unit=state.unit_label,
            )
        )
        losses.setText(
            text(
                "live.inspector.losses",
                source=state.loss.source_blocks,
                queue=state.loss.acquisition_blocks,
                fft=state.loss.fft_frames,
                publication=state.loss.publication_frames,
                bridge=state.loss.bridge_frames,
            )
        )
        frames.setText(
            text(
                "live.inspector.frames",
                spectrum=text("live.inspector.published") if state.has_spectrum else text("live.inspector.not_published"),
                persistence=state.persistence_label,
                waterfall=(
                    text("live.inspector.published")
                    if state.waterfall_line is not None
                    else text("live.inspector.not_published")
                ),
            )
        )
        error.setText("" if state.error_label is None else state.error_label)
        error.setVisible(state.error_label is not None)

    unsubscribe = view_model.subscribe(render)
    inspector.destroyed.connect(lambda: unsubscribe())
    return inspector


def _applied_configuration_detail(state: LiveViewState) -> str:
    """Format only the five frozen configuration fields from the applied snapshot."""

    applied = getattr(getattr(state.snapshot, "applied", None), "applied", None)
    if not isinstance(applied, LiveConfiguration):
        return text("live.inspector.not_applied")
    profile = applied.profile_id or text("live.profile.none")
    return text(
        "live.inspector.applied.detail",
        center=applied.center_hz / 1e6,
        sample_rate=applied.sample_rate_hz / 1e6,
        gain=applied.gain_db,
        backend=applied.backend.value.upper(),
        profile=profile,
    )


def _adapt_spectrum(source: object, unit_label: str) -> _SpectrumFrame | None:
    """Admit only an immutable self-describing public spectrum; never repair it in Qt."""

    frequencies = getattr(source, "frequencies_hz", None)
    values = getattr(source, "values", None)
    if (
        not isinstance(frequencies, np.ndarray)
        or not isinstance(values, np.ndarray)
        or frequencies.ndim != 1
        or values.ndim != 1
        or frequencies.size < 2
        or frequencies.size != values.size
        or frequencies.flags.writeable
        or values.flags.writeable
        or not np.issubdtype(frequencies.dtype, np.number)
        or not np.issubdtype(values.dtype, np.number)
        or not unit_label
        or not np.all(np.isfinite(frequencies))
        or np.any(np.diff(frequencies) <= 0.0)
    ):
        return None
    return _SpectrumFrame(source=source, frequencies_hz=frequencies, values=values, unit=unit_label)


def _numeric_spin(
    parent: QWidget,
    *,
    accessible_name: str,
    lower: float,
    upper: float,
    value: float,
    suffix: str,
) -> QDoubleSpinBox:
    spin = QDoubleSpinBox(parent)
    spin.setProperty("ui2Role", "range-control")
    spin.setRange(lower, upper)
    spin.setDecimals(3)
    spin.setValue(value)
    spin.setSuffix(suffix)
    spin.setAccessibleName(accessible_name)
    return spin


def _secondary_label(text: str, accessible_name: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setProperty("ui2Role", "secondary")
    label.setAccessibleName(accessible_name)
    return label


def _validated_manual_uri(raw_value: str) -> tuple[str | None, str | None]:
    """Fail closed before a presenter call; this is syntax, not device validation."""

    value = raw_value.strip()
    if not value:
        return None, text("live.uri.error.empty")
    if any(character.isspace() for character in value):
        return None, text("live.uri.error.whitespace")
    transport, separator, target = value.partition(":")
    transport = transport.casefold()
    if not separator or transport not in {"usb", "ip"}:
        return None, text("live.uri.error.transport")
    if transport == "ip" and not target:
        return None, text("live.uri.error.ip_target")
    return f"{transport}:{target}", None
