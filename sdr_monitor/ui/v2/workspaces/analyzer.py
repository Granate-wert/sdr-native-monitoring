"""Shared graph-first RTBW/Sweep workspace using the APP-01 public adapters."""

from __future__ import annotations

from collections.abc import Callable
import math
from time import monotonic
from PySide6.QtCore import QEvent, QSignalBlocker, QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceSelection
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.sweep_statistics import SweepStatisticsSettings

from ..design import ThemeId, stylesheet_for_theme
from ..design.icons import V2IconId
from ..i18n import current_locale, text
from ..shell.contracts import WorkspaceDefinition
from ..spectrum.projection import SpectrumProjector
from ..state.analyzer_readouts import (
    analyzer_periods,
    analyzer_quality_detail,
    analyzer_status,
    spectrum_numerical_readout,
    tinysa_settings_readout,
)
from ..state.analyzer_status_cadence import AnalyzerStatusCadence
from ..state.configuration_readouts import configuration_prefix, rf_bandwidth_summary
from ..state.live_view_state import LiveAction
from ..view_models.analyzer_view_model import AnalyzerMode, AnalyzerViewModel, AnalyzerViewState
from ..view_models.calibration_view_model import CalibrationProfileViewModel
from .analyzer_configuration import AnalyzerConfigurationDrawer
from .analyzer_display_controls import AnalyzerDisplayControls
from .analyzer_frequency_bar import AnalyzerFrequencyBar
from .analyzer_hackrf_configuration import HackrfConfigurationBar
from .analyzer_hackrf_sweep import HackrfSweepConfigurationBar
from .analyzer_inspector import AnalyzerInspector
from .analyzer_pane import AnalyzerPaneViewV2
from .analyzer_status_label import AnalyzerPeriodsLabel, AnalyzerStatusLabel
from .analyzer_sweep_preview import AnalyzerSweepPreview
from .analyzer_tinysa_configuration import TinySaConfigurationBar
from .independent_pane_session import IndependentPaneSessionV2
from .independent_pane_setup import IndependentPaneSetupV2
from sdr_monitor.ui.v2_pane_product_session import PaneProductSessionHandle


class AnalyzerWorkspaceV2(QWidget):
    """One canvas, no device/session ownership and no implicit RF command.

    The external model survives widget navigation. RTBW and Sweep consume the
    same domain bundle and SpectrumScene. Progress is displayed immediately;
    a partial Sweep is never appended as a fabricated complete Waterfall row.
    """

    def __init__(self, model: AnalyzerViewModel, *, projector: SpectrumProjector | None = None,
                 calibration_profiles: CalibrationProfileViewModel | None = None,
                 shared_projector_factory: Callable[[], SpectrumProjector] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self._shared_projector_factory = shared_projector_factory
        self._calibration_profiles = calibration_profiles
        self._terminal_released = False
        self._independent_session: IndependentPaneSessionV2 | None = None
        self._independent_setup: IndependentPaneSetupV2 | None = None
        self._independent_setup_button: QPushButton | None = None
        self._theme = ThemeId.DARK
        self._last_source_selection: AnalyzerSourceSelection | None = None
        self._last_family_path_available = False
        self._range_anchors: dict[AnalyzerDisplayControls, tuple[object, ...]] = {}
        self._status_cadence = AnalyzerStatusCadence()
        self._text_bindings: list[tuple[QLabel | QPushButton, str]] = []
        self.setProperty("ui2Root", True)
        self.setObjectName("analyzerWorkspaceV2")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(4)
        commands = QHBoxLayout()
        self._commands = commands
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
        self.discover = self._button("analyzer.discover.usb", lambda: model.discover_devices(local_only=True))
        commands.addWidget(self.discover)
        self.discover_network = self._button("analyzer.discover.usb_ip", model.discover_devices)
        commands.addWidget(self.discover_network)
        self.mode = QComboBox(self)
        self.mode.setProperty("ui2Role", "utility-select")
        self.mode.setAccessibleName(text("analyzer.mode"))
        self.mode.addItem(text("analyzer.mode.rtbw"), AnalyzerMode.RTBW)
        self.mode.addItem(text("analyzer.mode.sweep"), AnalyzerMode.SWEEP)
        self.mode.currentIndexChanged.connect(self._select_mode)
        commands.addWidget(self.mode)
        self.shared_views_label = QLabel(self)
        self.shared_views_label.setProperty("ui2Role", "secondary")
        commands.addWidget(self.shared_views_label)
        self.shared_views = QComboBox(self)
        self.shared_views.setProperty("ui2Role", "utility-select")
        for count in range(1, 5):
            self.shared_views.addItem(str(count), count)
        self.shared_views.setEnabled(shared_projector_factory is not None)
        self.shared_views.currentIndexChanged.connect(self._set_shared_view_count)
        commands.addWidget(self.shared_views)
        self.selected_view_label = QLabel(self)
        self.selected_view_label.setProperty("ui2Role", "secondary")
        commands.addWidget(self.selected_view_label)
        self.selected_view = QComboBox(self)
        self.selected_view.setProperty("ui2Role", "utility-select")
        self.selected_view.addItem(str(1), 1)
        self.selected_view.currentIndexChanged.connect(self._select_shared_view)
        commands.addWidget(self.selected_view)
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
        self.hackrf_bar = HackrfConfigurationBar(model, self)
        layout.addWidget(self.hackrf_bar)
        self.hackrf_bar.draft_changed.connect(self._update_primary_availability)
        self.hackrf_sweep_bar = HackrfSweepConfigurationBar(model, self)
        layout.addWidget(self.hackrf_sweep_bar)
        self.hackrf_sweep_bar.draft_changed.connect(self._update_primary_availability)
        self.tinysa_bar = TinySaConfigurationBar(model, self)
        layout.addWidget(self.tinysa_bar)
        self.tinysa_bar.draft_changed.connect(self._update_primary_availability)
        self.tinysa_bar.settings_drawer.close_requested.connect(self._hide_settings)
        self.source_summary = QLabel(self)
        self.source_summary.setObjectName("v2-analyzer-source-summary")
        self.source_summary.setProperty("ui2Role", "secondary")
        self.source_summary.setWordWrap(True)
        self.source_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.source_summary.hide()
        layout.addWidget(self.source_summary)
        self.applied = QLabel(self)
        self.applied.setProperty("ui2Role", "secondary")
        self.applied.setWordWrap(True)
        self.applied.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.applied)
        self.sweep_preview = AnalyzerSweepPreview(model.live.preview_sweep, self)
        self.sweep_preview.status_changed.connect(self._update_primary_availability)
        layout.addWidget(self.sweep_preview)
        for field in (self.start_frequency, self.stop_frequency):
            field.valueChanged.connect(self._refresh_preview)
        self.drawer.sweep_profile.choice.currentIndexChanged.connect(self._refresh_preview)
        self.drawer.sweep_profile.usable_window.valueChanged.connect(self._refresh_preview)
        self.drawer.sweep_profile.overlap.valueChanged.connect(self._refresh_preview)
        self.error = QLabel(self)
        self.error.setProperty("ui2Role", "secondary")
        self.error.setProperty("ui2Tone", "error")
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self._pane_host = QWidget(self)
        self._pane_grid = QGridLayout(self._pane_host)
        self._pane_grid.setContentsMargins(0, 0, 0, 0)
        self._pane_grid.setSpacing(4)
        self.visualization = AnalyzerPaneViewV2(
            model.state.mode, projector=projector,
            on_frame_applied=self._on_visual_frame_applied, pane_number=1,
            parent=self._pane_host,
        )
        self.visualization.set_selected(True)
        self._panes = [self.visualization]
        self._pane_grid.addWidget(self.visualization, 0, 0, 1, 2)
        self.frequency_bar.viewport_span_requested.connect(self._change_viewport_span)
        self.visualization.spectrum_scene.view_box.sigXRangeChanged.connect(self._viewport_changed)
        self._acquisition_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self.visualization.spectrum_scene)
        self._acquisition_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        self._acquisition_shortcut.setAutoRepeat(False)
        self._acquisition_shortcut.activated.connect(self._execute_keyboard_primary)
        layout.addWidget(self._pane_host, 1)
        self.display_controls = AnalyzerDisplayControls(
            self.visualization.spectrum_scene.take_display_controls(),
            self.visualization.waterfall_pane.take_display_controls(), parent=self,
        )
        self.display_controls.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.display_controls.close_requested.connect(self._hide_display)
        self.display_controls.viewport_range_requested.connect(self._apply_selected_view_range)
        self._display_overlays = [self.display_controls]
        self.status = AnalyzerStatusLabel(self)
        self.status.setProperty("ui2Role", "secondary")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.periods = AnalyzerPeriodsLabel(self)
        self.periods.setProperty("ui2Role", "secondary")
        layout.addWidget(self.periods)
        self.drawer.close_requested.connect(self._hide_settings)
        self.drawer.draft_changed.connect(lambda: self._render(model.state))
        self.drawer.hide()
        for child in (self.tinysa_bar.settings_drawer, *self.tinysa_bar.settings_drawer.findChildren(QWidget),
                      self.drawer, *self.drawer.findChildren(QWidget),
                      self.display_controls, *self.display_controls.findChildren(QWidget)):
            child.installEventFilter(self)
        self._unsubscribe = model.subscribe(self._render)
        self._unsubscribe_devices = model.live.subscribe_devices(self._devices)
        self.set_locale()
        self.tinysa_bar.settings_drawer.bind_profiles(calibration_profiles)

    def _button(self, key, callback):
        button = QPushButton(text(key), self)
        button.setProperty("ui2Role", "utility-action")
        button.clicked.connect(callback)
        self._text_bindings.append((button, key))
        return button

    def enable_independent_pane_setup(
        self, *, install: Callable[[PaneProductSessionHandle], None],
        uninstall: Callable[[], None],
    ) -> None:
        """Install the user editor on this existing Analyzer, without RX I/O."""
        if self._terminal_released or self._independent_setup is not None:
            raise RuntimeError("independent pane setup is unavailable")
        button = self._button("analyzer.pane.setup.open", self._toggle_independent_setup)
        self._commands.insertWidget(self._commands.indexOf(self.settings), button)
        setup = IndependentPaneSetupV2(
            install=install, uninstall=uninstall,
            calibration_profiles=self._calibration_profiles,
            can_prepare=lambda: (not self._terminal_released and not self.model.state.controls_locked
                                 and self._independent_session is None),
            parent=self)
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.insertWidget(layout.indexOf(self._pane_host), setup)
        setup.update_sources(self.model.state.source_selection)
        setup.set_theme(self._theme)
        setup.state_changed.connect(lambda: self._render(self.model.state))
        self._independent_setup = setup
        self._independent_setup_button = button
        self._render(self.model.state)

    def _toggle_independent_setup(self) -> None:
        setup = self._independent_setup
        if setup is None or self._independent_session is not None:
            return
        if setup.isVisible() and not setup.blocks_single_source:
            setup.hide()
        else:
            self._hide_display(restore_focus=False)
            self.drawer.hide()
            self.tinysa_bar.settings_drawer.hide()
            setup.show()

    @property
    def independent_setup_can_close(self) -> bool:
        return self._independent_setup is None or self._independent_setup.can_close

    def install_independent_pane_session(self, handle: PaneProductSessionHandle) -> None:
        """Show independent source panes on THIS Analyzer tab after Apply.

        The external product controller owns Stage/preview/Apply and terminal
        graph close. Installing this view never opens or starts a receiver.
        """
        state = self.model.state
        if (self._terminal_released or self._independent_session is not None
                or not isinstance(handle, PaneProductSessionHandle)
                or not handle.applied or state.running or state.stop_required
                or state.starting or state.stopping or state.live.busy):
            raise RuntimeError("independent panes require an idle Analyzer and applied plan")
        widget = IndependentPaneSessionV2(
            handle, close_layout=(None if self._independent_setup is None
                                  else self._independent_setup.close_applied_layout),
            parent=self)
        self._hide_display(restore_focus=False)
        self.drawer.hide()
        self.tinysa_bar.settings_drawer.hide()
        self._independent_session = widget
        if self._independent_setup is not None:
            self._independent_setup.hide()
        if self._independent_setup_button is not None:
            self._independent_setup_button.hide()
        self._single_source_controls = (
            self.source, self.rx, self.discover, self.discover_network, self.mode,
            self.shared_views_label, self.shared_views, self.selected_view_label,
            self.selected_view, self.settings, self.display, self.primary,
            self.frequency_bar, self.hackrf_bar, self.hackrf_sweep_bar,
            self.tinysa_bar, self.source_summary, self.applied, self.sweep_preview,
            self.error, self._pane_host, self.status, self.periods,
        )
        for control in self._single_source_controls:
            control.hide()
        widget.setParent(self)
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.insertWidget(layout.indexOf(self._pane_host) + 1, widget, 1)
        widget.set_theme(self._theme)
        widget.set_locale()
        widget.show()

    @property
    def independent_pane_session(self) -> IndependentPaneSessionV2 | None:
        return self._independent_session

    def uninstall_independent_pane_session(self) -> None:
        """Return to the old single-source controls only after full Stop/close."""
        widget = self._independent_session
        if widget is None:
            return
        if not widget.handle.shutdown_complete:
            raise RuntimeError("independent pane owners must close before returning to one source")
        widget.release_presentation_after_shutdown()
        layout = self.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.removeWidget(widget)
        widget.hide()
        widget.setParent(None)
        widget.deleteLater()
        self._independent_session = None
        for control in self._single_source_controls:
            control.show()
        if self._independent_setup_button is not None:
            self._independent_setup_button.show()
        if self._independent_setup is not None:
            self._independent_setup.hide()
        self._render(self.model.state)

    def _set_shared_view_count(self, _index: int) -> None:
        """Show 1–4 views of the SAME source without extra RX commands."""
        count = self.shared_views.currentData()
        factory = self._shared_projector_factory
        if self._terminal_released or type(count) is not int or not 1 <= count <= 4:
            return
        if count > 1 and factory is None:
            return
        while len(self._panes) < count:
            index = len(self._panes) + 1
            assert factory is not None
            projector = factory()
            pane = AnalyzerPaneViewV2(
                self.model.state.mode, projector=projector,
                settings_prefix=f"ui_v2/analyzer/shared_pane{index}/v1",
                pane_number=index,
                parent=self._pane_host,
            )
            pane.set_theme(self._theme)
            pane.spectrum_scene.set_locale(current_locale())
            pane.waterfall_pane.set_locale(current_locale())
            for plot in (pane.spectrum_scene.plot_item, pane.waterfall_pane.plot_item):
                plot.getAxis("bottom").showLabel(False)
            pane.spectrum_scene.view_box.sigXRangeChanged.connect(self._viewport_changed)
            overlay = AnalyzerDisplayControls(
                pane.spectrum_scene.take_display_controls(),
                pane.waterfall_pane.take_display_controls(), parent=self,
            )
            overlay.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            overlay.close_requested.connect(self._hide_display)
            overlay.viewport_range_requested.connect(self._apply_selected_view_range)
            overlay.set_locale(current_locale())
            for child in (overlay, *overlay.findChildren(QWidget)):
                child.installEventFilter(self)
            self._display_overlays.append(overlay)
            self._panes.append(pane)
        selected = min(self.selected_view.currentData(), count)
        with QSignalBlocker(self.selected_view):
            self.selected_view.clear()
            for index in range(1, count + 1):
                self.selected_view.addItem(str(index), index)
            self.selected_view.setCurrentIndex(selected - 1)
        self._hide_display(restore_focus=False)
        for pane in self._panes:
            self._pane_grid.removeWidget(pane)
            if pane in self._panes[count:]:
                pane.clear_shared_view()
                pane.hide()
            else:
                pane.set_compact_grid_geometry(count >= 3)
        positions = (((0, 0, 1, 2),) if count == 1 else
                     ((0, 0, 1, 1), (0, 1, 1, 1)) if count == 2 else
                     ((0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 2)) if count == 3 else
                     ((0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 1, 1)))
        for pane, (row, column, row_span, column_span) in zip(self._panes[:count], positions, strict=True):
            self._pane_grid.addWidget(pane, row, column, row_span, column_span)
            pane.set_selected(pane.pane_number == selected)
            pane.show()
            if pane is not self.visualization:
                pane.apply_analyzer_state(self.model.state)
        self._pane_grid.setRowStretch(0, 1)
        self._pane_grid.setRowStretch(1, 1 if count >= 3 else 0)
        self._selected_frame_applied(selected)

    def _selected_pane(self) -> AnalyzerPaneViewV2:
        index = self.selected_view.currentData()
        return self._panes[index - 1] if type(index) is int and 1 <= index <= len(self._panes) else self.visualization

    def _selected_overlay(self) -> AnalyzerDisplayControls:
        index = self.selected_view.currentData()
        return self._display_overlays[index - 1] if type(index) is int and 1 <= index <= len(self._display_overlays) else self.display_controls

    def _select_shared_view(self, _index: int) -> None:
        self._hide_display(restore_focus=False)
        for pane in self._panes:
            pane.set_selected(pane.pane_number == self.selected_view.currentData())
        self._selected_frame_applied(self.selected_view.currentData())

    def _selected_frame_applied(self, index: int) -> None:
        if index != self.selected_view.currentData():
            return
        lower, upper = self._selected_pane().spectrum_scene.view_box.viewRange()[0]
        self.frequency_bar.set_viewport_span(float(upper - lower))

    def _change_viewport_span(self, span_hz: float) -> None:
        scene = self._selected_pane().spectrum_scene
        if scene.latest_frame is None:
            return
        lower, upper = scene.view_box.viewRange()[0]
        center = (lower + upper) / 2
        scene.view_box.setXRange(center - span_hz / 2, center + span_hz / 2, padding=0)

    def _prepare_view_range_controls(self, overlay: AnalyzerDisplayControls) -> None:
        pane = self._selected_pane()
        scene = pane.spectrum_scene
        grid = scene.measurement_grid
        available = scene.latest_frame is not None and grid is not None and len(grid) >= 2
        overlay.set_range_available(available)
        if available:
            lower, upper = scene.view_box.viewRange()[0]
            overlay.set_viewport_range(float(lower), float(upper))
            anchor = self._view_range_identity(pane)
            if anchor is not None:
                self._range_anchors[overlay] = anchor
        else:
            self._range_anchors.pop(overlay, None)
            overlay.set_range_error("analyzer.view_range.unavailable")

    @staticmethod
    def _view_range_identity(pane: AnalyzerPaneViewV2) -> tuple[object, ...] | None:
        grid = pane.spectrum_scene.measurement_grid
        identity = pane._last_identity
        if grid is None or identity is None:
            return None
        fields = ("source_id", "session_id", "receiver_id", "acquisition_epoch",
                  "config_generation", "clock_domain", "accumulation_id", "unit")
        return (id(grid), pane._last_mode, *(getattr(identity, name) for name in fields))

    def _apply_selected_view_range(self, start_hz: float, stop_hz: float) -> None:
        overlay = self._selected_overlay()
        if self.sender() is not overlay:
            return  # A hidden/deselected pane must not change another pane.
        pane = self._selected_pane()
        scene = pane.spectrum_scene
        grid = scene.measurement_grid
        if scene.latest_frame is None or grid is None or len(grid) < 2:
            overlay.set_range_error("analyzer.view_range.unavailable")
            return
        if self._range_anchors.get(overlay) != self._view_range_identity(pane):
            overlay.set_range_error("analyzer.view_range.stale")
            return
        if not math.isfinite(start_hz) or not math.isfinite(stop_hz) or stop_hz <= start_hz:
            overlay.set_range_error("analyzer.view_range.invalid")
            return
        capture_start, capture_stop = float(grid[0]), float(grid[-1])
        # Six decimals in MHz round to one hertz; tolerate only this UI
        # quantization at the edge, never silently admit an out-of-band plan.
        if start_hz < capture_start - 0.5 or stop_hz > capture_stop + 0.5:
            overlay.set_range_error("analyzer.view_range.outside")
            return
        scene.view_box.setXRange(max(start_hz, capture_start), min(stop_hz, capture_stop), padding=0)
        overlay.set_range_error(None)

    def _viewport_changed(self, view: object, bounds) -> None:
        if view is self._selected_pane().spectrum_scene.view_box:
            self.frequency_bar.set_viewport_span(float(bounds[1] - bounds[0]))

    def _on_visual_frame_applied(self) -> None:
        # A new frame may retain exactly the old ViewBox range, in which case
        # Qt emits no sigXRangeChanged. Update the no-frame placeholder too.
        self._selected_frame_applied(1)

    @property
    def _last_bundle(self):
        """Compatibility observation of the primary pane's admitted frame."""
        return self.visualization.last_bundle

    @property
    def _last_waterfall(self):
        return self.visualization._last_waterfall

    @property
    def _last_persistence(self):
        return self.visualization._last_persistence

    @property
    def _last_identity(self):
        return self.visualization._last_identity

    @property
    def _last_sweep_snapshot(self):
        return self.visualization._last_sweep_snapshot

    @property
    def _last_statistics_key(self):
        return self.visualization.last_statistics_key

    @property
    def _sweep_waterfall_error(self):
        return self.visualization.sweep_waterfall_error

    def set_locale(self) -> None:
        """Translate controls in place; preserve canvas, source and local range."""
        self._last_source_selection = None  # Reformat this scalar readout in the new locale.
        self.setAccessibleName(text("analyzer.title"))
        self.source.setAccessibleName(text("live.device_selector.name"))
        with QSignalBlocker(self.source):
            for index in range(self.source.count()):
                if self.source.itemData(index) is None:
                    self.source.setItemText(index, text("live.device.unselected"))
        for widget, key in self._text_bindings:
            widget.setText(text(key))
            widget.setAccessibleName(text(key))
        self.discover_network.setToolTip(text("analyzer.discover.usb_ip.warning"))
        self.mode.setAccessibleName(text("analyzer.mode"))
        self.shared_views_label.setText(text("analyzer.shared_views.short"))
        self.shared_views.setAccessibleName(text("analyzer.shared_views.name"))
        self.shared_views.setToolTip(text("analyzer.shared_views.scope"))
        self.selected_view_label.setText(text("analyzer.selected_view.short"))
        self.selected_view.setAccessibleName(text("analyzer.selected_view.name"))
        self.selected_view.setToolTip(text("analyzer.selected_view.scope"))
        self.rx.setToolTip(text("analyzer.rx.unavailable"))
        self.rx.setAccessibleName(text("analyzer.rx.unavailable"))
        self.mode.setItemText(0, text("analyzer.mode.rtbw"))
        self.mode.setItemText(1, text("analyzer.mode.sweep"))
        self.start_frequency.setAccessibleName(text("analyzer.start_frequency"))
        self.stop_frequency.setAccessibleName(text("analyzer.stop_frequency"))
        self.drawer.set_locale()
        self.sweep_preview.set_locale()
        self.frequency_bar.set_locale()
        self.hackrf_bar.set_locale()
        self.hackrf_sweep_bar.set_locale()
        self.tinysa_bar.set_locale()
        for pane in self._panes:
            pane.spectrum_scene.set_locale(current_locale())
            pane.waterfall_pane.set_locale(current_locale())
            pane.set_badge_locale()
            # Frequency ticks carry units; avoid pyqtgraph's SI multiplier.
            for plot in (pane.spectrum_scene.plot_item, pane.waterfall_pane.plot_item):
                plot.getAxis("bottom").showLabel(False)
        for overlay in self._display_overlays:
            overlay.set_locale(current_locale())
        if self._independent_session is not None:
            self._independent_session.set_locale()
        if self._independent_setup is not None:
            self._independent_setup.set_locale()
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
        for pane in self._panes:
            pane.set_theme(theme)
        if self._independent_session is not None:
            self._independent_session.set_theme(theme)
        if self._independent_setup is not None:
            self._independent_setup.set_theme(theme)
        self.drawer.set_theme(theme)

    def closeEvent(self, event) -> None:
        if not self.independent_setup_can_close:
            event.ignore()
            return
        if self._independent_session is not None and not self._independent_session.handle.can_close():
            event.ignore()
            return
        self.sweep_preview.cancel()
        self._unsubscribe()
        self._unsubscribe_devices()
        self.drawer.dispose()
        self.tinysa_bar.settings_drawer.release_profiles()
        super().closeEvent(event)

    def release_presentation_after_shutdown(self) -> None:
        """Explicit parent-shell terminal hook; never used for Stop/hide."""
        if self._terminal_released:
            return
        if self._independent_session is not None:
            self._independent_session.release_presentation_after_shutdown()
        if self._independent_setup is not None:
            self._independent_setup.release_after_shutdown()
        self._unsubscribe()
        self._unsubscribe_devices()
        self.sweep_preview.cancel()
        self.drawer.dispose()
        self.tinysa_bar.settings_drawer.release_profiles()
        # Terminal cleanup may be retried after a partial failure. Do not let
        # Qt paint a PlotItem whose axes have already been retired.
        for pane in self._panes:
            pane.release_presentation_after_shutdown()
        self._terminal_released = True

    def _toggle_settings(self) -> None:
        if self.model.state.tinysa_controls_available:
            self._hide_display()
            self.drawer.hide()
            drawer = self.tinysa_bar.settings_drawer
            if drawer.isVisible():
                self._hide_settings()
            else:
                self._position_settings()
                drawer.show()
                drawer.raise_()
                drawer.setFocus()
            return
        if self.model.state.hackrf_controls_available:
            (self.hackrf_sweep_bar.start_mhz if self.model.state.mode is AnalyzerMode.SWEEP
             else self.hackrf_bar.center).setFocus()
            return
        self._hide_display()
        if self.drawer.isVisible():
            self._hide_settings()
        else:
            self._position_settings()
            self.drawer.show()
            self.drawer.raise_()
            self.drawer.setFocus()

    def _hide_settings(self) -> None:
        self.drawer.hide()
        self.tinysa_bar.settings_drawer.hide()
        self.settings.setFocus()

    def _toggle_display(self) -> None:
        self.drawer.hide()
        self.tinysa_bar.settings_drawer.hide()
        overlay = self._selected_overlay()
        if overlay.isVisible():
            self._hide_display()
        else:
            self._position_settings()
            self._prepare_view_range_controls(overlay)
            overlay.show()
            overlay.raise_()
            overlay.setFocus()

    def _hide_display(self, *, restore_focus: bool = True) -> None:
        for overlay in self._display_overlays:
            overlay.hide()
        self._range_anchors.clear()
        if restore_focus:
            self.display.setFocus()

    def eventFilter(self, watched, event) -> bool:
        if (watched is self.drawer.scroll_area.widget()
                and event.type() == QEvent.Type.LayoutRequest and self.drawer.isVisible()):
            self._position_settings()
        if ((self.drawer.isVisible() or self.tinysa_bar.settings_drawer.isVisible()
             or any(overlay.isVisible() for overlay in self._display_overlays))
                and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress)
                and event.key() == Qt.Key.Key_Escape):
            event.accept()
            if event.type() == QEvent.Type.KeyPress:
                self._hide_settings() if self.drawer.isVisible() or self.tinysa_bar.settings_drawer.isVisible() else self._hide_display()
            return True
        return super().eventFilter(watched, event)

    def _position_settings(self) -> None:
        width = min(460, max(320, self.width() - 16))
        # Below both command rows: the explicit Stop action stays exposed.
        self.drawer.setGeometry(max(8, self.width() - width - 8), 84, width,
                                min(self.drawer.preferred_height(width), max(120, self.height() - 92)))
        self.tinysa_bar.settings_drawer.setGeometry(max(8, self.width() - width - 8), 84, width,
                                                   max(120, self.height() - 92))
        display_width = min(1180, max(320, self.width() - 16))
        for overlay in self._display_overlays:
            overlay.setGeometry(max(8, self.width() - display_width - 8), 84,
                                display_width,
                                min(overlay.sizeHint().height(), max(120, self.height() - 92)))

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
        device = (state.source_selection.selected if state.source_selection is not None
                  else getattr(state.live.snapshot, "device", None))
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
        if self._independent_setup is not None and self._independent_setup.blocks_single_source:
            return
        state = self.model.state
        if state.running or state.stop_required:
            self.model.stop()
        elif state.tinysa_controls_available:
            try:
                self.model.start(self.tinysa_bar.request())
            except (ValueError, TypeError):
                self.error.setText(text("tinysa.common.invalid"))
                self.error.show()
        elif state.live.primary_action is LiveAction.DISCOVER:
            self.model.discover_devices(local_only=True)
        elif state.hackrf_sweep_controls_available and state.mode is AnalyzerMode.SWEEP:
            try:
                self.model.start(self.hackrf_sweep_bar.request())
            except (ValueError, TypeError):
                self.error.setText(text("hackrf.sweep.invalid"))
                self.error.show()
        elif state.hackrf_controls_available:
            if state.rtbw_profile_ready and not self.hackrf_bar.dirty:
                self.model.start()
            else:
                self.hackrf_bar.center.setFocus()
        elif not state.live.has_applied_configuration or self.drawer.dirty or self.drawer.pending:
            if not self.drawer.isVisible():
                self._toggle_settings()
        else:
            try:
                request = self._sweep_request() if state.mode is AnalyzerMode.SWEEP else None
            except ValueError as error:
                self.error.setText(str(error))
                self.error.show()
                return
            if request is not None:
                self._refresh_preview()
                if not self.sweep_preview.resolve():
                    return
            self.model.start(request)

    def _sweep_request(self) -> ContinuousSweepPlanRequest:
        """One immutable draft for preview and Start; no divergent UI planner."""
        return ContinuousSweepPlanRequest(
            self.start_frequency.value() * 1e6, self.stop_frequency.value() * 1e6,
            usable_window_hz=self.drawer.sweep_profile.usable_window.value() * 1e6,
            overlap_hz=self.drawer.sweep_profile.overlap.value() * 1e6,
            statistics=SweepStatisticsSettings(),
            speed_profile=self.drawer.sweep_profile.profile,
        )

    def _refresh_preview(self, _value: object = None) -> None:
        state = self.model.state
        native_sweep = state.mode is AnalyzerMode.SWEEP and state.ad936x_controls_available
        self.sweep_preview.setVisible(native_sweep)
        if not native_sweep:
            self.sweep_preview.cancel()
        elif not state.controls_locked:
            try:
                self.sweep_preview.set_inputs(self.drawer.preview_configuration(), self._sweep_request())
            except (ValueError, TypeError) as error:
                self.sweep_preview.invalidate(str(error))

    def _execute_keyboard_primary(self) -> None:
        """Graph-local Space may only dispatch an already-enabled Start/Stop."""
        state = self.model.state
        if (not self.primary.isEnabled() or self.drawer.isVisible()
                or any(overlay.isVisible() for overlay in self._display_overlays)
                or self.tinysa_bar.settings_drawer.isVisible()):
            return
        if (state.running or state.stop_required
                or state.tinysa_controls_available and self.tinysa_bar.valid
                or state.hackrf_sweep_controls_available and state.mode is AnalyzerMode.SWEEP
                   and self.hackrf_sweep_bar.valid
                or (state.live.primary_action is LiveAction.START and state.rtbw_profile_ready
                    and not self.hackrf_bar.dirty
                    and not self.drawer.dirty and not self.drawer.pending)):
            self._execute()

    def _update_primary_availability(self) -> None:
        """Known-invalid Sweep plans disable Start; pending drafts can still resolve on click."""
        state = self.model.state
        ready = (state.rtbw_profile_ready and
                 (not self.hackrf_bar.dirty if state.hackrf_controls_available else not self.drawer.dirty and not self.drawer.pending)
                 and state.live.primary_action is LiveAction.START
                 and state.live.primary_action_enabled)
        if state.tinysa_controls_available:
            ready = self.tinysa_bar.valid
        elif state.hackrf_sweep_controls_available and state.mode is AnalyzerMode.SWEEP:
            ready = (self.hackrf_sweep_bar.valid and state.live.primary_action is LiveAction.START
                     and state.live.primary_action_enabled)
        invalid_sweep = ready and state.mode is AnalyzerMode.SWEEP and state.ad936x_controls_available and self.sweep_preview.has_error
        enabled = (not (state.configuration_pending or state.starting or state.stopping or state.live.busy)
                   and not (self._independent_setup is not None and self._independent_setup.blocks_single_source)
                   and (state.running or state.stop_required or (ready and not invalid_sweep)))
        self.primary.setEnabled(enabled)
        hint = (self.tinysa_bar.validation_message if state.tinysa_controls_available and not ready else
                text("hackrf.sweep.invalid") if state.hackrf_sweep_controls_available
                    and state.mode is AnalyzerMode.SWEEP and not self.hackrf_sweep_bar.valid else
                text("analyzer.source.family_path_pending")
                if state.source_selection is not None and state.source_selection.selected is not None
                and not state.ad936x_controls_available and not state.hackrf_controls_available and not state.tinysa_controls_available
                else self.sweep_preview.summary.text() if invalid_sweep
                else "" if ready or state.running or state.stop_required
                else text("analyzer.start_requires_configuration"))
        if self.primary.toolTip() != hint:
            self.primary.setToolTip(hint)

    def _render(self, state: AnalyzerViewState) -> None:
        if self._terminal_released:
            return
        if self._independent_session is not None:
            return  # The old single-source views are hidden, not re-projected.
        self._refresh_preview()
        self._sync_source(state)
        selection = state.source_selection
        if self._independent_setup is not None:
            self._independent_setup.update_sources(selection)
        family_available = state.hackrf_controls_available or state.tinysa_controls_available
        if selection is not self._last_source_selection or family_available != self._last_family_path_available:
            self._last_source_selection = selection
            self._last_family_path_available = family_available
            selected = selection.selected if selection is not None else None
            if selected is None:
                self.source_summary.hide()
            else:
                from ..state.source_selection_readout import source_selection_readout
                self.source_summary.setText(source_selection_readout(selected, family_path_available=family_available))
                self.source_summary.show()
        with QSignalBlocker(self.mode):
            self.mode.setCurrentIndex(self.mode.findData(state.mode))
        pane_stage_blocks = (self._independent_setup is not None
                             and self._independent_setup.blocks_single_source)
        for control in (self.source, self.discover, self.discover_network, self.mode):
            control.setEnabled(not state.controls_locked and not pane_stage_blocks)
        if self._independent_setup_button is not None:
            self._independent_setup_button.setEnabled(not state.controls_locked or pane_stage_blocks)
        self.frequency_bar.apply_view_state(state, has_frame=state.bundle is not None)
        self.frequency_bar.setVisible(state.ad936x_controls_available)
        self.hackrf_bar.apply_view_state(state)
        self.hackrf_sweep_bar.apply_view_state(state)
        self.tinysa_bar.apply_view_state(state)
        for control in (self.frequency_bar, self.hackrf_bar, self.hackrf_sweep_bar,
                        self.tinysa_bar, self.settings, self.drawer):
            control.setEnabled(not pane_stage_blocks)
        key = ("analyzer.applying" if state.configuration_pending
               else "analyzer.starting" if state.starting else "analyzer.stopping" if state.stopping
               else "analyzer.stop" if state.running or state.stop_required
               else "analyzer.start")
        _set_text_if_changed(self.primary, text(key))
        if self.primary.accessibleName() != text(key):
            self.primary.setAccessibleName(text(key))
        self._update_primary_availability()
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
        hackrf = getattr(state.live.snapshot, "hackrf_request", None) if state.hackrf_controls_available else None
        hackrf_sweep = state.hackrf_sweep_request if state.mode is AnalyzerMode.SWEEP else None
        _set_text_if_changed(self.applied,
            tinysa_settings_readout(getattr(state.bundle, "spectrum", None)) if state.tinysa_controls_available else
            text("hackrf.sweep.profile", start=hackrf_sweep.start_hz // 1_000_000,
                 stop=hackrf_sweep.stop_hz // 1_000_000, fft=hackrf_sweep.fft_size,
                 lna=hackrf_sweep.lna_gain, vga=hackrf_sweep.vga_gain,
                 preview=hackrf_sweep.preview_rate_hz) if hackrf_sweep is not None else
            text("hackrf.profile", center=f"{hackrf.center_frequency_hz / 1e6:g}", rate=f"{hackrf.sample_rate_hz / 1e6:g}",
                 bandwidth=f"{hackrf.baseband_filter_hz / 1e6:g}", fft=hackrf.fft_size, lna=hackrf.lna_gain_db,
                 vga=hackrf.vga_gain_db, generation=hackrf.configuration_generation) + "\n" +
            text("hackrf.dsp.profile", window=text("hackrf.window." + hackrf.window),
                 detector=text("hackrf.detector." + hackrf.detector), hop=hackrf.hop_size,
                 group=hackrf.averaging_frames) if hackrf is not None else
            text("live.configuration.no_applied") if configuration is None else
            configuration_prefix(getattr(state.live.snapshot, "applied", None)) + " " +
            f"{configuration.center_hz / 1e6:g} MHz · Fs {configuration.sample_rate_hz / 1e6:g} MS/s · "
            f"{rf_bandwidth_summary(configuration.analog_bandwidth_hz)} · "
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
        self.visualization.apply_analyzer_state(state)
        for pane in self._panes[1:self.shared_views.currentData()]:
            pane.apply_analyzer_state(state)
        for overlay in self._display_overlays:
            if overlay.isVisible() and self._range_anchors.get(overlay) != self._view_range_identity(self._selected_pane()):
                overlay.set_range_error("analyzer.view_range.stale")
        self._selected_frame_applied(self.selected_view.currentData())
        scene = self.visualization.spectrum_scene
        if self._status_cadence.admit(state, monotonic()):
            _set_text_if_changed(self.periods, analyzer_periods(state, scene.paint_cadence.period_ms()))
            description = text("analyzer.periods.scope")
            if self.periods.toolTip() != description:
                self.periods.setToolTip(description)
                self.periods.setAccessibleDescription(description)
            status = analyzer_status(state)
            _set_text_if_changed(self.status, status)
            frame = getattr(state.bundle, "spectrum", None)
            quality = ("\n" + analyzer_quality_detail(frame.native_quality_flags)
                       if isinstance(frame, LiveSpectrumFrame) else "")
            detail = status + quality
            if self.status.toolTip() != detail:
                self.status.setToolTip(detail)
                self.status.setAccessibleDescription(detail)


def _set_text_if_changed(widget: QLabel | QPushButton, value: str) -> None:
    """Do not invalidate control text on every analytical publication.

    This is not a throttle: changed errors, lifecycle states and measurement
    readouts are still delivered immediately. Spectrum cadence is untouched.
    """
    if widget.text() != value:
        widget.setText(value)


def analyzer_workspace_definition(model: AnalyzerViewModel,
                                  projector: SpectrumProjector | None = None,
                                  calibration_profiles: CalibrationProfileViewModel | None = None,
                                  *, shared_projector_factory: Callable[[], SpectrumProjector] | None = None,
                                  on_created: Callable[[AnalyzerWorkspaceV2], None] | None = None) -> WorkspaceDefinition:
    def make_workspace() -> AnalyzerWorkspaceV2:
        widget = AnalyzerWorkspaceV2(model, projector=projector,
            calibration_profiles=calibration_profiles,
            shared_projector_factory=shared_projector_factory)
        if on_created is not None:
            on_created(widget)
        return widget

    return WorkspaceDefinition(
        workspace_id="analyzer", label=text("analyzer.title"), description=text("analyzer.description"),
        icon=V2IconId.NAVIGATION, workspace_factory=make_workspace,
        inspector_factory=lambda: AnalyzerInspector(model),
        label_key="analyzer.title", description_key="analyzer.description",
        terminal_cleanup=_release_analyzer_workspace,
    )


def _release_analyzer_workspace(widget: QWidget) -> None:
    if not isinstance(widget, AnalyzerWorkspaceV2):
        raise TypeError("Analyzer terminal cleanup requires its actual workspace")
    widget.release_presentation_after_shutdown()
