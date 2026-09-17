"""Shared graph-first RTBW/Sweep workspace using the APP-01 public adapters."""

from __future__ import annotations

from time import monotonic
import numpy as np
from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QStyle, QStyleOptionButton, QVBoxLayout, QWidget

from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.sweep_statistics import SweepStatisticsSettings

from ..design import ThemeId, stylesheet_for_theme
from ..design.icons import V2IconId
from ..i18n import current_locale, text
from .analyzer_configuration import AnalyzerConfigurationDrawer
from .analyzer_display_controls import AnalyzerDisplayControls
from .analyzer_frequency_bar import AnalyzerFrequencyBar
from .analyzer_status_label import AnalyzerStatusLabel
from ..shell.contracts import WorkspaceDefinition
from ..spectrum import PersistenceDensityFrame
from ..spectrum.contracts import TraceKind
from ..state.live_view_state import LiveAction
from ..state.analyzer_readouts import analyzer_status, spectrum_numerical_readout
from ..state.analyzer_status_cadence import AnalyzerStatusCadence
from ..state.analyzer_layers import waterfall_line_from_sweep, persistence_density_from_sweep
from ..state.configuration_readouts import configuration_prefix
from ..view_models.analyzer_view_model import AnalyzerMode, AnalyzerViewModel, AnalyzerViewState
from ..waterfall import SpectrumWaterfallView, WaterfallLineFrame


class AnalyzerWorkspaceV2(QWidget):
    """One canvas, no device/session ownership and no implicit RF command.

    The external model survives widget navigation. RTBW and Sweep consume the
    same domain bundle and SpectrumScene. Progress is displayed immediately;
    a partial Sweep is never appended as a fabricated complete Waterfall row.
    """

    def __init__(self, model: AnalyzerViewModel, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self._theme = ThemeId.DARK
        self._last_bundle = None
        self._last_mode = model.state.mode
        self._last_waterfall: WaterfallLineFrame | None = None
        self._last_persistence: PersistenceDensityFrame | None = None
        self._last_identity = None
        self._last_sweep_snapshot = None
        self._last_statistics_key: tuple[str, int, int] | None = None
        self._sweep_waterfall_error = False
        self._status_cadence = AnalyzerStatusCadence()
        self._text_bindings: list[tuple[QLabel | QPushButton, str]] = []
        self.setProperty("ui2Root", True)
        self.setObjectName("analyzerWorkspaceV2")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(4)
        commands = QHBoxLayout()
        self.source = QComboBox(self)
        self.source.setProperty("ui2Role", "utility-select")
        self.source.setAccessibleName(text("live.device_selector.name"))
        self.source.currentIndexChanged.connect(self._select_source)
        commands.addWidget(self.source, 1)
        self.rx = QPushButton(self)
        self.rx.setText(text("analyzer.rx.unknown"))
        self.rx.setProperty("ui2Role", "utility-action")
        self.rx.setEnabled(False)
        self.rx.setToolTip(text("analyzer.rx.unavailable"))
        commands.addWidget(self.rx)
        self.discover = self._button("live.discover", model.discover_devices)
        commands.addWidget(self.discover)
        self.mode = QComboBox(self)
        self.mode.setProperty("ui2Role", "utility-select")
        self.mode.setAccessibleName(text("analyzer.mode"))
        self.mode.addItem(text("analyzer.mode.rtbw"), AnalyzerMode.RTBW)
        self.mode.addItem(text("analyzer.mode.sweep"), AnalyzerMode.SWEEP)
        self.mode.currentIndexChanged.connect(self._select_mode)
        commands.addWidget(self.mode)
        self.settings = self._button("analyzer.settings", self._toggle_settings)
        commands.addWidget(self.settings)
        self.display = self._button("analyzer.display", self._toggle_display)
        commands.addWidget(self.display)
        self.primary = self._button("analyzer.start", self._execute)
        commands.addWidget(self.primary)
        layout.addLayout(commands)
        self.drawer = AnalyzerConfigurationDrawer(model, parent=self)
        self.frequency_bar = AnalyzerFrequencyBar(self.drawer, parent=self)
        self.start_frequency = self.frequency_bar.start
        self.stop_frequency = self.frequency_bar.stop
        layout.addWidget(self.frequency_bar)
        self.applied = QLabel(self)
        self.applied.setProperty("ui2Role", "secondary")
        self.applied.setWordWrap(True)
        layout.addWidget(self.applied)
        self.error = QLabel(self)
        self.error.setProperty("ui2Role", "secondary")
        self.error.setProperty("ui2Tone", "error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.visualization = SpectrumWaterfallView(parent=self)
        self.frequency_bar.viewport_span_requested.connect(self._change_viewport_span)
        self.visualization.spectrum_scene.view_box.sigXRangeChanged.connect(self._viewport_changed)
        self._acquisition_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self.visualization.spectrum_scene)
        self._acquisition_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._acquisition_shortcut.setAutoRepeat(False)
        self._acquisition_shortcut.activated.connect(self._execute_keyboard_primary)
        layout.addWidget(self.visualization, 1)
        self.display_controls = AnalyzerDisplayControls(
            self.visualization.spectrum_scene.take_display_controls(),
            self.visualization.waterfall_pane.take_display_controls(), parent=self,
        )
        self.display_controls.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.display_controls.close_requested.connect(self._hide_display)
        self.status = AnalyzerStatusLabel(self)
        self.status.setProperty("ui2Role", "secondary")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.drawer.close_requested.connect(self._hide_settings)
        self.drawer.draft_changed.connect(lambda: self._render(model.state))
        self.drawer.hide()
        for child in (self.drawer, *self.drawer.findChildren(QWidget),
                      self.display_controls, *self.display_controls.findChildren(QWidget)):
            child.installEventFilter(self)
        self._unsubscribe = model.subscribe(self._render)
        self._unsubscribe_devices = model.live.subscribe_devices(self._devices)
        self.set_locale()

    def _button(self, key, callback):
        button = QPushButton(text(key), self)
        button.setProperty("ui2Role", "utility-action")
        button.clicked.connect(callback)
        self._text_bindings.append((button, key))
        return button

    def _change_viewport_span(self, span_hz: float) -> None:
        scene = self.visualization.spectrum_scene
        if scene.latest_frame is None:
            return
        lower, upper = scene.view_box.viewRange()[0]
        center = (lower + upper) / 2
        scene.view_box.setXRange(center - span_hz / 2, center + span_hz / 2, padding=0)

    def _viewport_changed(self, _view, bounds) -> None:
        self.frequency_bar.set_viewport_span(float(bounds[1] - bounds[0]))

    def set_locale(self) -> None:
        """Translate controls in place; preserve canvas, source and local range."""
        self.setAccessibleName(text("analyzer.title"))
        for widget, key in self._text_bindings:
            widget.setText(text(key))
            widget.setAccessibleName(text(key))
        self.mode.setAccessibleName(text("analyzer.mode"))
        self.rx.setToolTip(text("analyzer.rx.unavailable"))
        self.rx.setAccessibleName(text("analyzer.rx.unavailable"))
        self.mode.setItemText(0, text("analyzer.mode.rtbw"))
        self.mode.setItemText(1, text("analyzer.mode.sweep"))
        self.start_frequency.setAccessibleName(text("analyzer.start_frequency"))
        self.stop_frequency.setAccessibleName(text("analyzer.stop_frequency"))
        self.drawer.set_locale()
        self.frequency_bar.set_locale()
        self.visualization.spectrum_scene.set_locale(current_locale())
        self.visualization.waterfall_pane.set_locale(current_locale())
        # Frequency ticks already carry their units. Avoid a redundant caption
        # (and pyqtgraph's automatic SI multiplier) in the shared Analyzer plot.
        for plot in (self.visualization.spectrum_scene.plot_item,
                     self.visualization.waterfall_pane.plot_item):
            plot.getAxis("bottom").showLabel(False)
        self.display_controls.set_locale(current_locale())
        self._reserve_primary_width()
        self._render(self.model.state)

    def _reserve_primary_width(self) -> None:
        option = QStyleOptionButton()
        option.initFrom(self.primary)
        metrics = self.primary.fontMetrics()
        widths = []
        for key in ("analyzer.applying", "analyzer.starting", "analyzer.stopping",
                    "analyzer.stop", "live.discover", "analyzer.settings", "analyzer.start"):
            option.text = text(key)
            size = QSize(metrics.horizontalAdvance(option.text), metrics.height())
            widths.append(self.primary.style().sizeFromContents(
                QStyle.ContentsType.CT_PushButton, option, size, self.primary).width())
        self.primary.setFixedWidth(max(widths))

    def set_theme(self, theme: ThemeId) -> None:
        self._theme = theme
        self.setStyleSheet(stylesheet_for_theme(theme))
        self._reserve_primary_width()
        self.visualization.set_theme(theme)
        self.drawer.set_theme(theme)

    def closeEvent(self, event) -> None:
        self._unsubscribe()
        self._unsubscribe_devices()
        self.drawer.dispose()
        super().closeEvent(event)

    def _toggle_settings(self) -> None:
        self.display_controls.hide()
        if self.drawer.isVisible():
            self._hide_settings()
        else:
            self._position_settings()
            self.drawer.show()
            self.drawer.raise_()
            self.drawer.setFocus()

    def _hide_settings(self) -> None:
        self.drawer.hide()
        self.settings.setFocus()

    def _toggle_display(self) -> None:
        self.drawer.hide()
        if self.display_controls.isVisible():
            self._hide_display()
        else:
            self._position_settings()
            self.display_controls.show()
            self.display_controls.raise_()
            self.display_controls.setFocus()

    def _hide_display(self) -> None:
        self.display_controls.hide()
        self.display.setFocus()

    def eventFilter(self, watched, event) -> bool:
        if ((self.drawer.isVisible() or self.display_controls.isVisible())
                and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
                and event.key() == Qt.Key.Key_Escape):
            event.accept()
            if event.type() == QEvent.Type.KeyPress:
                self._hide_settings() if self.drawer.isVisible() else self._hide_display()
            return True
        return super().eventFilter(watched, event)

    def _position_settings(self) -> None:
        width = min(460, max(320, self.width() - 16))
        # Below both command rows: the explicit Stop action stays exposed.
        self.drawer.setGeometry(max(8, self.width() - width - 8), 84, width,
                                min(self.drawer.sizeHint().height(), max(120, self.height() - 92)))
        display_width = min(1180, max(320, self.width() - 16))
        self.display_controls.setGeometry(max(8, self.width() - display_width - 8), 84,
                                         display_width,
                                         min(self.display_controls.sizeHint().height(),
                                             max(120, self.height() - 92)))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_settings()

    def _devices(self, devices) -> None:
        with QSignalBlocker(self.source):
            self.source.clear()
            self.source.addItem(text("live.device.unselected"), None)
            for device in devices:
                identifier = getattr(device, "device_id", None)
                if isinstance(identifier, str):
                    self.source.addItem(str(getattr(device, "label", identifier)), identifier)
        self._sync_source(self.model.state)

    def _sync_source(self, state: AnalyzerViewState) -> None:
        """Show published selection, not an optimistic in-flight combo choice."""
        device = getattr(state.live.snapshot, "device", None)
        identifier = getattr(device, "device_id", None)
        with QSignalBlocker(self.source):
            index = self.source.findData(identifier)
            if isinstance(identifier, str) and index < 0:
                self.source.addItem(str(getattr(device, "label", identifier)), identifier)
                index = self.source.count() - 1
            self.source.setCurrentIndex(max(0, index))

    def _select_source(self, index: int) -> None:
        value = self.source.itemData(index)
        if isinstance(value, str):
            self.model.select_device(value)
        self._sync_source(self.model.state)

    def _select_mode(self, index: int) -> None:
        if not self.model.select_mode(self.mode.itemData(index)):
            with QSignalBlocker(self.mode):
                self.mode.setCurrentIndex(self.mode.findData(self.model.state.mode))

    def _execute(self) -> None:
        state = self.model.state
        if state.running or state.stop_required:
            self.model.stop()
        elif state.live.primary_action is LiveAction.DISCOVER:
            self.model.discover_devices()
        elif not state.live.has_applied_configuration or self.drawer.dirty or self.drawer.pending:
            if not self.drawer.isVisible():
                self._toggle_settings()
        else:
            try:
                request = (ContinuousSweepPlanRequest(
                    self.start_frequency.value() * 1e6, self.stop_frequency.value() * 1e6,
                    statistics=SweepStatisticsSettings(),
                ) if state.mode is AnalyzerMode.SWEEP else None)
            except ValueError as error:
                self.error.setText(str(error))
                self.error.show()
                return
            self.model.start(request)

    def _execute_keyboard_primary(self) -> None:
        """Graph-local Space may only dispatch an already-enabled Start/Stop."""
        state = self.model.state
        if not self.primary.isEnabled() or self.drawer.isVisible() or self.display_controls.isVisible():
            return
        if state.running or state.stop_required:
            self._execute()
        elif (state.live.primary_action is LiveAction.START and state.live.has_applied_configuration
              and not self.drawer.dirty and not self.drawer.pending):
            self._execute()

    def _render(self, state: AnalyzerViewState) -> None:
        self._sync_source(state)
        with QSignalBlocker(self.mode):
            self.mode.setCurrentIndex(self.mode.findData(state.mode))
        for control in (self.source, self.discover, self.mode):
            control.setEnabled(not state.controls_locked)
        self.frequency_bar.apply_view_state(state, has_frame=state.bundle is not None)
        key = ("analyzer.applying" if state.configuration_pending
               else "analyzer.starting" if state.starting else "analyzer.stopping" if state.stopping
               else "analyzer.stop" if state.running or state.stop_required
               else "analyzer.start")
        _set_text_if_changed(self.primary, text(key))
        if self.primary.accessibleName() != text(key):
            self.primary.setAccessibleName(text(key))
        ready = (state.live.has_applied_configuration and not self.drawer.dirty
                 and not self.drawer.pending and state.live.primary_action is LiveAction.START
                 and state.live.primary_action_enabled)
        self.primary.setEnabled(not (state.configuration_pending or state.starting or state.stopping or state.live.busy)
                                and (state.running or state.stop_required or ready))
        hint = "" if ready or state.running or state.stop_required else text("analyzer.start_requires_configuration")
        if self.primary.toolTip() != hint:
            self.primary.setToolTip(hint)
        _set_text_if_changed(self.error, state.error or "")
        self.error.setVisible(bool(state.error))
        configuration = getattr(getattr(state.live.snapshot, "applied", None), "applied", None)
        receiver = getattr(state.bundle, "receiver_id", None)
        _set_text_if_changed(self.rx, receiver if receiver else text("analyzer.rx.unknown"))
        capabilities = getattr(getattr(state.live.snapshot, "device", None), "capabilities", None)
        topology = getattr(capabilities, "receiver_topology", None)
        observed = tuple(getattr(topology, "available_selections", ()))
        receiver_detail = text("analyzer.rx.unavailable")
        if observed:
            receiver_detail += "\n" + text("analyzer.rx.observed", selections=", ".join(item.value for item in observed))
        if self.rx.toolTip() != receiver_detail:
            self.rx.setToolTip(receiver_detail)
        if self.rx.accessibleName() != receiver_detail:
            self.rx.setAccessibleName(receiver_detail)
        _set_text_if_changed(self.applied,
            text("live.configuration.no_applied") if configuration is None else
            configuration_prefix(getattr(state.live.snapshot, "applied", None)) + " " +
            f"{configuration.center_hz / 1e6:g} MHz · Fs {configuration.sample_rate_hz / 1e6:g} MS/s · "
            f"FFT {configuration.fft_size} · {configuration.gain_db:g} dB"
        )
        if configuration is not None:
            detail = text(
                "analyzer.applied_capture_range",
                lower=f"{(configuration.center_hz - configuration.sample_rate_hz / 2) / 1e6:g}",
                upper=f"{(configuration.center_hz + configuration.sample_rate_hz / 2) / 1e6:g}",
            ) + "\n" + spectrum_numerical_readout(getattr(state.bundle, "spectrum", None))
        else:
            detail = ""
        if self.applied.toolTip() != detail:
            self.applied.setToolTip(detail)
        if self.applied.accessibleDescription() != detail:
            self.applied.setAccessibleDescription(detail)
        identity = getattr(state.bundle, "identity", None)
        previous = self._last_identity
        fields = ("source_id", "session_id", "receiver_id", "acquisition_epoch", "config_generation",
                  "clock_domain", "accumulation_id", "unit")
        changed_identity = (identity is not None and previous is not None and (
            any(getattr(identity, name) != getattr(previous, name) for name in fields)
            or not np.array_equal(identity.frequencies_hz, previous.frequencies_hz)
        ))
        if (state.mode is not self._last_mode or changed_identity
                or state.bundle is None and self._last_bundle is not None):
            self.visualization.spectrum_scene.clear_measurement()
            self.visualization.waterfall_pane.clear_history(reset_kind=True)
            if self._sweep_waterfall_error:
                self.visualization.spectrum_scene.set_warning(None)
                self._sweep_waterfall_error = False
            self._last_bundle = self._last_waterfall = self._last_persistence = None
            self._last_sweep_snapshot = None
            self._last_statistics_key = None
            self._last_mode = state.mode
        self._last_identity = identity
        bundle = state.bundle
        if bundle is not None and bundle is not self._last_bundle:
            self.visualization.spectrum_scene.set_frame(bundle)
            self._last_bundle = bundle
        if state.mode is AnalyzerMode.SWEEP:
            statistics = bundle.sweep_statistics if bundle is not None else None
            key = ((statistics.source_id, statistics.epoch, statistics.update_sequence)
                   if statistics is not None else None)
            if statistics is not None and key != self._last_statistics_key:
                scene = self.visualization.spectrum_scene
                scene.set_trace(TraceKind.AVERAGE, statistics)
                scene.set_persistence_frame(persistence_density_from_sweep(statistics))
                self._last_statistics_key = key
            elif statistics is None and self._last_statistics_key is not None:
                self.visualization.spectrum_scene.clear_trace(TraceKind.AVERAGE)
                self.visualization.spectrum_scene.clear_persistence_display()
                self._last_statistics_key = None
        if state.mode is AnalyzerMode.RTBW:
            density = state.live.persistence_frame
            if isinstance(density, PersistenceDensityFrame) and density is not self._last_persistence:
                self.visualization.spectrum_scene.set_persistence_frame(density)
                self._last_persistence = density
            elif (density is None and self._last_persistence is not None
                  and "persistence_pending" not in getattr(bundle, "coherence_issues", ())):
                self.visualization.spectrum_scene.clear_persistence_display()
                self._last_persistence = None
            row = state.live.waterfall_line
            if isinstance(row, WaterfallLineFrame) and row is not self._last_waterfall:
                self.visualization.waterfall_pane.set_line(row)
                self._last_waterfall = row
        elif state.sweep_snapshot is not None and state.sweep_snapshot is not self._last_sweep_snapshot:
            snapshot = state.sweep_snapshot
            # Preserve terminal N and progressive N+1 from the same backend
            # poll, even though only N+1 is current on the upper spectrum.
            try:
                rows = tuple(waterfall_line_from_sweep(frame)
                             for frame in (snapshot.line, snapshot.progress) if frame is not None)
            except (ValueError, TypeError) as error:
                self.visualization.waterfall_pane.clear_history()
                self.visualization.spectrum_scene.set_warning(text("waterfall.sweep.invalid", reason=str(error)))
                self._sweep_waterfall_error = True
            else:
                for update in rows:
                    self.visualization.waterfall_pane.set_sweep_line(update)
                if self._sweep_waterfall_error:
                    self.visualization.spectrum_scene.set_warning(None)
                    self._sweep_waterfall_error = False
            self._last_sweep_snapshot = snapshot
        if self._status_cadence.admit(state, monotonic()):
            status = analyzer_status(state)
            _set_text_if_changed(self.status, status)
            if self.status.toolTip() != status:
                self.status.setToolTip(status)


def _set_text_if_changed(widget: QLabel | QPushButton, value: str) -> None:
    """Do not invalidate control text on every analytical publication.

    This is not a throttle: changed errors, lifecycle states and measurement
    readouts are still delivered immediately. Spectrum cadence is untouched.
    """
    if widget.text() != value:
        widget.setText(value)


def analyzer_workspace_definition(model: AnalyzerViewModel) -> WorkspaceDefinition:
    return WorkspaceDefinition(
        workspace_id="analyzer", label=text("analyzer.title"), description=text("analyzer.description"),
        icon=V2IconId.NAVIGATION, workspace_factory=lambda: AnalyzerWorkspaceV2(model),
        inspector_factory=_analyzer_inspector,
        label_key="analyzer.title", description_key="analyzer.description",
    )


def _analyzer_inspector() -> QWidget:
    """A theme-aware local-only hint, never a second configuration owner."""
    label = QLabel(text("analyzer.local"))
    label.setProperty("ui2Role", "secondary")
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    return label
