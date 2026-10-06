"""User-operated independent-source layout editor on the V2 Analyzer tab."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QSignalBlocker, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QResizeEvent, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.ad936x_pane_profiles import ad936x_pane_rate_choices
from sdr_monitor.domain.device_capabilities import (
    AdapterRuntimeAvailability, CapabilityEvidenceOrigin, CapabilityField, DeviceFamily,
)
from sdr_monitor.domain.pane_user_refusal import PaneUserRefusal
from sdr_monitor.domain.pluto_route_intent import PlutoOperationalRouteIntent
from sdr_monitor.domain.pane_scheduler import (
    Ad936xPairedSweepPaneProfile, Ad936xSweepPaneProfile, CaptureMeasurementMode, HackrfRtbwPaneProfile,
    PaneCaptureProfile, RtlRtbwPaneProfile,
    PaneRevisitEstimate, TinySaTracePaneProfile,
)
from sdr_monitor.domain.rtl_live import RTL_FFT_CHOICES, RTL_RATE_CHOICES_HZ
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection, ReceiverEndpoint
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.domain.tinysa_settings import TinySaInputMode, TinySaRbwMode, TinySaSweepAccuracy
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_product_session import PaneProductSessionHandle
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft, RtbwBandPolicy, TinySaPaneIntent
from sdr_monitor.ui.v2_pane_user_stage import (
    PaneUserStageError, PreparedPaneUserSession, apply_user_pane_session,
    discard_user_pane_session, prepare_user_pane_session,
)

from ..design import ThemeId, stylesheet_for_theme
from ..i18n import text
from ..view_models.calibration_view_model import CalibrationProfileViewModel
from .analyzer_tinysa_settings import DraftScrollComboBox, TinySaSettingsDrawer, requested_tinysa_parts


class _SlotRow:
    def __init__(self, number: int, parent: QWidget) -> None:
        self.number = number
        self.number_label = QLabel(str(number), parent)
        self.number_label.setProperty("ui2Role", "secondary")
        self.source = DraftScrollComboBox(parent)
        self.source.setProperty("ui2Role", "utility-select")
        self.source.setMinimumWidth(175)
        self.chain = DraftScrollComboBox(parent)
        self.chain.setObjectName(f"independentPaneChain{number}V2")
        self.chain.setProperty("ui2Role", "utility-select")
        self.chain.setMinimumWidth(76)
        self.chain.setMaximumWidth(90)
        self.chain.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.chain.setMinimumContentsLength(3)
        for chain in (ReceiverChainSelection.RX1, ReceiverChainSelection.RX2):
            self.chain.addItem(chain.name, chain.value)
        self.route = DraftScrollComboBox(parent)
        self.route.setObjectName(f"independentPaneRoute{number}V2")
        self.route.setProperty("ui2Role", "utility-select")
        self.route.setMinimumWidth(150)
        self.route.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.route.setMinimumContentsLength(12)
        self.route_scope = QLabel(parent)
        self.route_scope.setProperty("ui2Role", "secondary")
        self.route_scope.setWordWrap(True)
        self.route_scope.hide()
        self._route_source_id: str | None = None
        self._route_intent: PlutoOperationalRouteIntent | None = None
        self._route_unavailable = False
        self._retained_family: DeviceFamily | None = None
        self._retained_network = False
        self.source_line = QWidget(parent)
        source_line_layout = QHBoxLayout(self.source_line)
        source_line_layout.setContentsMargins(0, 0, 0, 0)
        source_line_layout.setSpacing(4)
        source_line_layout.addWidget(self.source, 1)
        source_line_layout.addWidget(self.chain)
        self.source_chain = QWidget(parent)
        source_chain_layout = QVBoxLayout(self.source_chain)
        source_chain_layout.setContentsMargins(0, 0, 0, 0)
        source_chain_layout.setSpacing(4)
        source_chain_layout.addWidget(self.source_line)
        source_chain_layout.addWidget(self.route)
        source_chain_layout.addWidget(self.route_scope)
        self.start = QDoubleSpinBox(parent)
        self.stop = QDoubleSpinBox(parent)
        for field in (self.start, self.stop):
            field.setRange(0.1, 10_000.0)
            field.setDecimals(3)
            field.setSingleStep(1.0)
            field.setProperty("ui2Role", "range-control")
        self.start.setValue(100.0)
        self.stop.setValue(108.0)
        self.rate = DraftScrollComboBox(parent)
        self.rate.setProperty("ui2Role", "utility-select")
        self.fft = DraftScrollComboBox(parent)
        self.fft.setProperty("ui2Role", "utility-select")
        for value in (1024, 4096, 16384):
            self.fft.addItem(str(value), value)
        self.fft.setCurrentIndex(1)
        self.points = QSpinBox(parent)
        self.points.setRange(2, 10001)
        self.points.setValue(101)
        self.points.setProperty("ui2Role", "range-control")
        self.mode = DraftScrollComboBox(parent)
        self.mode.setProperty("ui2Role", "utility-select")
        self.mode_points = QStackedWidget(parent)
        # The source cell may grow for the typed route and shared-pane cue;
        # keep the numeric mode control compact instead of stretching it to
        # the full source-cell stack height.
        self.mode_points.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.mode_points.addWidget(self.mode)
        self.mode_points.addWidget(self.points)
        self._last_mode: CaptureMeasurementMode | None = None
        self._rtbw_rate: float | None = None
        self._sweep_rate: float | None = None
        self.manual_gain = DraftScrollComboBox(parent)
        self.manual_gain.setObjectName(f"independentPaneRtlGain{number}V2")
        self.manual_gain.setProperty("ui2Role", "utility-select")
        self.manual_gain.setMinimumContentsLength(12)
        self.manual_gain.addItem(text("analyzer.pane.setup.rtl_gain_auto"), None)
        self._gain_context: tuple[object, ...] | None = None
        self.gain_label = QLabel(parent)
        self.gain_label.setProperty("ui2Role", "secondary")
        self.band_number_label = QLabel(str(number), parent)
        self.band_number_label.setProperty("ui2Role", "secondary")
        self.band = DraftScrollComboBox(parent)
        self.band.setProperty("ui2Role", "utility-select")
        for policy in RtbwBandPolicy:
            self.band.addItem(policy.value, policy)
        self.band.setCurrentIndex(self.band.findData(RtbwBandPolicy.FULL_RECEIVE))
        self.sweep_window = QDoubleSpinBox(parent)
        self.sweep_window.setObjectName(f"independentPaneAdSweepWindow{number}V2")
        self.sweep_window.setProperty("ui2Role", "range-control")
        self.sweep_window.setRange(0.0, 36.0)
        self.sweep_window.setDecimals(3)
        self.sweep_window.setSingleStep(1.0)
        self.sweep_window.setSpecialValueText("Auto")
        self.sweep_window.setValue(0.0)
        self.schedule_number_label = QLabel(str(number), parent)
        self.schedule_number_label.setProperty("ui2Role", "secondary")
        self.priority = QSpinBox(parent)
        self.priority.setRange(1, 100)
        self.priority.setValue(1)
        self.priority.setProperty("ui2Role", "range-control")
        self.maximum_revisit = QDoubleSpinBox(parent)
        self.maximum_revisit.setRange(0, 3600)
        self.maximum_revisit.setDecimals(3)
        self.maximum_revisit.setSingleStep(0.1)
        self.maximum_revisit.setProperty("ui2Role", "range-control")
        self.tinysa_settings = TinySaSettingsDrawer(parent, embedded_in_editor=True)


class IndependentPaneSetupV2(QWidget):
    """Four editable slots; Stage, Preview and Apply are distinct commands.

    No SDK or serial method runs on Qt.  A single bounded control worker owns
    Stage/Apply/Discard/terminal graph close. A pending preview blocks app
    close and the old one-source command path until explicitly discarded.
    """

    state_changed = Signal()

    def __init__(self, *, install: Callable[[PaneProductSessionHandle], None],
                 uninstall: Callable[[], None],
                 can_prepare: Callable[[], bool] = lambda: True,
                 rtl_candidate_stage_available: Callable[[str, int], bool] = lambda _source_id, _revision: False,
                 calibration_profiles: CalibrationProfileViewModel | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("independentPaneSetupV2")
        self.setProperty("ui2Root", True)
        self._install = install
        self._uninstall = uninstall
        self._rtl_candidate_stage_available = rtl_candidate_stage_available
        self._can_prepare = can_prepare
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="v2-pane-control")
        self._future: Future[Any] | None = None
        self._operation: str | None = None
        self._prepared: PreparedPaneUserSession | None = None
        self._retained_pool: PaneProductGraphPool | None = None
        self._closing_handle: PaneProductSessionHandle | None = None
        self._released = False
        self._choices: tuple[AnalyzerSourceChoice, ...] = ()
        self._selection: AnalyzerSourceSelection | None = None
        self._rtl_admission_state: tuple[tuple[str, str | None], ...] = ()
        self._rows = tuple(_SlotRow(number, self) for number in range(1, 5))
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)
        self.setMaximumHeight(480)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        # A staged plan must not squeeze RF fields or the plots to fit its
        # entire prose. Scroll only this editor; explicit commands stay pinned.
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("independentPaneSetupScrollV2")
        self.scroll_area.setProperty("ui2Role", "panel-scroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setMinimumSize(0, 80)
        self.scroll_contents = QWidget(self.scroll_area)
        self.scroll_contents.setProperty("ui2Role", "panel-scroll-content")
        layout = QVBoxLayout(self.scroll_contents)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self._contents_height = 0
        self.description = QLabel(self)
        self.description.setProperty("ui2Role", "secondary")
        self.description.setWordWrap(True)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.description)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setColumnStretch(1, 1)
        self.headers = tuple(QLabel(self) for _ in range(7))
        for column, header in enumerate(self.headers):
            header.setProperty("ui2Role", "secondary")
            grid.addWidget(header, 0, column)
        for row_index, row in enumerate(self._rows, 1):
            for column, widget in enumerate((row.number_label, row.source_chain, row.start,
                                             row.stop, row.rate, row.fft, row.mode_points)):
                grid.addWidget(widget, row_index, column)
            row.source.currentIndexChanged.connect(
                lambda _index, target=row: self._source_user_changed(target))
            row.route.currentIndexChanged.connect(
                lambda _index, target=row: self._route_user_changed(target))
            row.chain.currentIndexChanged.connect(lambda _index: self._refresh_actions())
            row.mode.currentIndexChanged.connect(
                lambda _index, target=row: self._mode_changed(target))
            row.band.currentIndexChanged.connect(
                lambda _index, target=row: self._band_changed(target))
            row.manual_gain.currentIndexChanged.connect(lambda _index: self._refresh_actions())
        layout.addLayout(grid)
        gain_grid = QGridLayout()
        gain_grid.setContentsMargins(0, 0, 0, 0)
        gain_grid.setHorizontalSpacing(6)
        self.gain_heading = QLabel(text("analyzer.pane.setup.rtl_gain_heading"), self)
        self.gain_heading.setProperty("ui2Role", "secondary")
        gain_grid.addWidget(self.gain_heading, 0, 0)
        for row_index, row in enumerate(self._rows, 1):
            row.gain_label.setText(text("analyzer.pane.setup.rtl_gain_label", pane=row.number))
            gain_grid.addWidget(row.gain_label, row_index, 0)
            gain_grid.addWidget(row.manual_gain, row_index, 1)
        gain_grid.setColumnStretch(2, 1)
        layout.addLayout(gain_grid)
        self.mode_help = QLabel(self)
        self.mode_help.setProperty("ui2Role", "secondary")
        self.mode_help.setWordWrap(True)
        layout.addWidget(self.mode_help)
        # One reused settings drawer per pane, one visible at a time. This
        # never opens serial or mutates the base Analyzer's source selection.
        self.tinysa_controls = QWidget(self)
        tiny_controls = QHBoxLayout(self.tinysa_controls)
        tiny_controls.setContentsMargins(0, 0, 0, 0)
        self.tinysa_toggle = QPushButton(self.tinysa_controls)
        self.tinysa_toggle.setProperty("ui2Role", "utility-action")
        self.tinysa_toggle.setCheckable(True)
        self.tinysa_pane = DraftScrollComboBox(self.tinysa_controls)
        self.tinysa_pane.setProperty("ui2Role", "utility-select")
        tiny_controls.addWidget(self.tinysa_toggle)
        tiny_controls.addWidget(self.tinysa_pane)
        tiny_controls.addStretch(1)
        layout.addWidget(self.tinysa_controls)
        self.tinysa_stack = QStackedWidget(self)
        for row in self._rows:
            drawer = row.tinysa_settings
            drawer.bind_profiles(calibration_profiles)
            self.tinysa_stack.addWidget(drawer)
            drawer.close_requested.connect(lambda: self.tinysa_toggle.setChecked(False))
            drawer.draft_changed.connect(lambda target=row: self._refresh_tinysa_row(target))
        self.tinysa_toggle.toggled.connect(self._show_tinysa_settings)
        self.tinysa_pane.currentIndexChanged.connect(self._select_tinysa_pane)
        self.tinysa_stack.hide()
        self.tinysa_controls.hide()
        layout.addWidget(self.tinysa_stack)
        actions = QHBoxLayout()
        self.prepare = self._button(self._begin_prepare)
        self.apply = self._button(self._begin_apply)
        self.discard = self._button(self._begin_discard)
        for button in (self.prepare, self.apply, self.discard):
            actions.addWidget(button)
        self.scheduler_toggle = QPushButton(self)
        self.scheduler_toggle.setProperty("ui2Role", "utility-action")
        self.scheduler_toggle.setCheckable(True)
        actions.addWidget(self.scheduler_toggle)
        self.band_toggle = QPushButton(self)
        self.band_toggle.setProperty("ui2Role", "utility-action")
        self.band_toggle.setCheckable(True)
        actions.addWidget(self.band_toggle)
        self.details = self._button(self._show_details)
        actions.addWidget(self.details)
        actions.addStretch(1)
        self._actions_layout = actions
        root.addLayout(actions)
        self.impact_summary = QLabel(self)
        self.impact_summary.setObjectName("independentPaneStageImpactV2")
        self.impact_summary.setWordWrap(True)
        self.impact_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.impact_summary.setProperty("ui2Role", "secondary")
        self.impact_summary.hide()
        self.impact_summary.installEventFilter(self)
        root.addWidget(self.impact_summary)
        self.band_controls = QWidget(self)
        band_grid = QGridLayout(self.band_controls)
        band_grid.setContentsMargins(0, 0, 0, 0)
        band_grid.setHorizontalSpacing(6)
        band_grid.setColumnStretch(2, 1)
        self.band_headers = tuple(QLabel(self.band_controls) for _ in range(3))
        for column, header in enumerate(self.band_headers):
            header.setProperty("ui2Role", "secondary")
            band_grid.addWidget(header, 0, column)
        for row_index, row in enumerate(self._rows, 1):
            band_grid.addWidget(row.band_number_label, row_index, 0)
            band_grid.addWidget(row.band, row_index, 1)
            band_grid.addWidget(row.sweep_window, row_index, 2)
        self.band_help = QLabel(self.band_controls)
        self.band_help.setWordWrap(True)
        self.band_help.setTextFormat(Qt.TextFormat.PlainText)
        self.band_help.setProperty("ui2Role", "secondary")
        band_grid.addWidget(self.band_help, 5, 0, 1, 3)
        self.band_toggle.toggled.connect(self._show_band)
        self.band_controls.hide()
        layout.addWidget(self.band_controls)
        self.scheduler_controls = QWidget(self)
        schedule_grid = QGridLayout(self.scheduler_controls)
        schedule_grid.setContentsMargins(0, 0, 0, 0)
        schedule_grid.setHorizontalSpacing(6)
        schedule_grid.setColumnStretch(3, 1)
        self.scheduler_headers = tuple(QLabel(self.scheduler_controls) for _ in range(3))
        for column, header in enumerate(self.scheduler_headers):
            header.setProperty("ui2Role", "secondary")
            schedule_grid.addWidget(header, 0, column)
        for row_index, row in enumerate(self._rows, 1):
            for column, widget in enumerate((row.schedule_number_label, row.priority,
                                             row.maximum_revisit)):
                schedule_grid.addWidget(widget, row_index, column)
        self.scheduler_help = QLabel(self.scheduler_controls)
        self.scheduler_help.setWordWrap(True)
        self.scheduler_help.setTextFormat(Qt.TextFormat.PlainText)
        self.scheduler_help.setProperty("ui2Role", "secondary")
        schedule_grid.addWidget(self.scheduler_help, 5, 0, 1, 4)
        self.scheduler_toggle.toggled.connect(self._show_scheduler)
        self.scheduler_controls.hide()
        layout.addWidget(self.scheduler_controls)
        self.preview = QLabel(self)
        self.preview.setWordWrap(True)
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setProperty("ui2Role", "secondary")
        layout.addWidget(self.preview)
        self.error = QLabel(self)
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setProperty("ui2Tone", "error")
        self.error.hide()
        layout.addWidget(self.error)
        # Keep editor rows compact at larger heights; do not spread surplus
        # space through the mode stack or between the scheduling fields.
        layout.addStretch(1)
        self.scroll_area.setWidget(self.scroll_contents)
        root.addWidget(self.scroll_area, 1)
        self.scroll_area.viewport().installEventFilter(self)
        self._error_key: str | None = None
        self._revisit_violations: tuple[PaneRevisitEstimate, ...] = ()
        self._cleanup_required = False
        self._failed_reason: PaneUserRefusal | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._poll)
        self.set_theme(ThemeId.DARK)
        self.set_locale()
        self._refresh_actions()
        self.hide()

    def _button(self, callback: Callable[[], None]) -> QPushButton:
        button = QPushButton(self)
        button.setProperty("ui2Role", "utility-action")
        button.clicked.connect(callback)
        return button

    def _show_scheduler(self, visible: bool) -> None:
        self.scheduler_controls.setVisible(visible)
        self._sync_contents_height()

    def _show_band(self, visible: bool) -> None:
        self.band_controls.setVisible(visible)
        self._sync_contents_height()

    def _show_tinysa_settings(self, visible: bool) -> None:
        self.tinysa_stack.setVisible(visible and self.tinysa_pane.count() > 0)
        self._sync_contents_height()

    def _select_tinysa_pane(self, _index: int = -1) -> None:
        number = self.tinysa_pane.currentData()
        if number is not None:
            drawer = self._rows[number - 1].tinysa_settings
            self.tinysa_stack.setCurrentWidget(drawer)
            drawer.show()
        self._show_tinysa_settings(self.tinysa_toggle.isChecked())
        self._refresh_tinysa_cue()

    def _refresh_tinysa_row(self, row: _SlotRow) -> None:
        choice = next((item for item in self._choices
                       if item.device_id == row.source.currentData()), None)
        if self._prepared is not None:
            schedule = self._prepared.plan.layout.schedule
            assert schedule is not None
            choice = next((job.profile.request_template.source
                           for resource in schedule.resources for job in resource.jobs
                           if isinstance(job.profile, TinySaTracePaneProfile)
                           and any(crop.pane_id == f"pane-{row.number}" for crop in job.crops)), choice)
        available = bool(choice is not None and choice.family is DeviceFamily.TINYSA
                         and choice.runtime is not None
                         and choice.runtime.availability is AdapterRuntimeAvailability.AVAILABLE
                         and self._selection is not None and not self._selection.release_pending)
        row.tinysa_settings.apply_source_state(
            choice, revision=None if self._selection is None else self._selection.revision,
            available=available, controls_locked=self.blocks_single_source,
            allow_unobserved_draft=True)
        self._refresh_tinysa_cue()

    def _refresh_tinysa_selector(self) -> None:
        previous = self.tinysa_pane.currentData()
        numbers = [row.number for row in self._rows
                   if any(choice.device_id == row.source.currentData()
                          and choice.family is DeviceFamily.TINYSA for choice in self._choices)]
        with QSignalBlocker(self.tinysa_pane):
            self.tinysa_pane.clear()
            for number in numbers:
                self.tinysa_pane.addItem(text("analyzer.pane.setup.tinysa_pane", pane=number), number)
            self.tinysa_pane.setCurrentIndex(max(0, self.tinysa_pane.findData(previous)))
        self.tinysa_controls.setVisible(bool(numbers))
        self._select_tinysa_pane()
        self._refresh_tinysa_cue()

    def _refresh_tinysa_cue(self) -> None:
        for index in range(self.tinysa_pane.count()):
            number = self.tinysa_pane.itemData(index)
            count = self._rows[number - 1].tinysa_settings.requested_count()
            label = text("analyzer.pane.setup.tinysa_pane", pane=number)
            if count:
                label += " · " + text("analyzer.pane.setup.tinysa_request_count", count=count)
            self.tinysa_pane.setItemText(index, label)
        number = self.tinysa_pane.currentData()
        count = 0 if number is None else self._rows[number - 1].tinysa_settings.requested_count()
        label = (text("tinysa.settings.requested_count", count=count) if count else
                 text("analyzer.pane.setup.tinysa_settings"))
        self.tinysa_toggle.setText(label)
        self.tinysa_toggle.setAccessibleName(label)

    def _show_details(self) -> None:
        target = self.error if not self.error.isHidden() else self.preview
        self.scroll_area.setFocus(Qt.FocusReason.OtherFocusReason)
        self.scroll_area.verticalScrollBar().setValue(target.y())

    def _set_preview_text(self, value: str) -> None:
        self.preview.setText(value)
        self.preview.setAccessibleName(value)
        self._sync_contents_height()

    def _set_impact_text(self, value: str) -> None:
        self.impact_summary.setText(value)
        self.impact_summary.setAccessibleName(value)
        self.impact_summary.setVisible(bool(value))
        self._refresh_apply_visibility()

    def _impact_readable(self) -> bool:
        if self._prepared is None or not self.isVisible():
            return True
        summary = self.impact_summary
        if not summary.isVisible() or summary.width() <= 0:
            return False
        required = summary.heightForWidth(summary.width())
        top = summary.mapTo(self, QPoint(0, 0)).y()
        return (summary.height() >= required and top >= 0
                and top + summary.height() <= self.height())

    def _refresh_apply_visibility(self) -> None:
        if not hasattr(self, "apply"):
            return
        summary = self.impact_summary
        required = summary.heightForWidth(max(1, summary.width())) if summary.isVisible() else 0
        # QLabel's size hint can remain one line after a width change. Reserve
        # the compact summary's real wrapped height, but never let arbitrary
        # long text grow this pinned area without bound.
        minimum = min(required, 3 * summary.fontMetrics().lineSpacing())
        if summary.minimumHeight() != minimum:
            summary.setMinimumHeight(minimum)
            summary.updateGeometry()
        refusal = next((reason for row in self._rows
                        for reason in (self._chain_refusal_key(row),) if reason is not None), None)
        if refusal is None:
            refusal = next((reason for row in self._rows
                            for choice in self._choices if choice.device_id == row.source.currentData()
                            for reason in (self._rtl_unavailable_key(choice),) if reason is not None), None)
        if refusal is None:
            refusal = next((reason for row in self._rows
                            for reason in (self._route_refusal_key(row),) if reason is not None), None)
        prepared = self._prepared
        self.apply.setEnabled(self._future is None and prepared is not None
                              and refusal is None and not prepared.handle.applied
                              and not any(item.recording_conflict for item in prepared.preview)
                              and self._impact_readable())

    @staticmethod
    def _pane_numbers(numbers: list[int]) -> str:
        values = sorted(set(numbers))
        if len(values) >= 3 and values == list(range(values[0], values[-1] + 1)):
            return f"{values[0]}–{values[-1]}"
        return ", ".join(map(str, values))

    def _refresh_impact_summary(self) -> None:
        prepared = self._prepared
        if prepared is None:
            self._set_impact_text("")
            return
        pane_numbers = {slot.request.pane_id: slot.number for slot in prepared.plan.layout.slots
                        if slot.request is not None}
        affected = [pane_numbers[pane_id] for item in prepared.preview
                    for pane_id in item.affected_pane_ids]
        empty = [str(slot.number) for slot in prepared.plan.layout.slots if slot.request is None]
        paired_resources = {
            group.physical_stream_resource_id for group in prepared.plan.groups
            if {endpoint.selection for endpoint in group.endpoints if isinstance(endpoint, ReceiverEndpoint)}
            == {ReceiverChainSelection.RX1, ReceiverChainSelection.RX2}
        }
        paired_count = len(paired_resources)
        sliced_count = sum(item.capture_job_count > 1 for item in prepared.preview)
        shared_count = sum(len(item.affected_pane_ids) > 1
                           and item.physical_stream_resource_id not in paired_resources
                           and item.capture_job_count == 1 for item in prepared.preview)
        relations = []
        if paired_count:
            relations.append(text("analyzer.pane.setup.impact_paired", count=paired_count))
        if sliced_count:
            relations.append(text("analyzer.pane.setup.impact_sliced", count=sliced_count))
        if shared_count:
            relations.append(text("analyzer.pane.setup.impact_shared", count=shared_count))
        if not relations:
            relations.append(text("analyzer.pane.setup.impact_independent"))
        if any(item.recording_conflict for item in prepared.preview):
            relations.append(text("analyzer.pane.setup.recording_conflict"))
        summary = text("analyzer.pane.setup.impact", resources=len(prepared.preview),
                       panes=self._pane_numbers(affected) or text("analyzer.pane.setup.impact_none"),
                       empty=", ".join(empty) or text("analyzer.pane.setup.impact_none"),
                       relations=" · ".join(relations))
        schedule = prepared.plan.layout.schedule
        assert schedule is not None
        tiny_groups: list[tuple[TinySaSweepRequest, list[int]]] = []
        for resource in schedule.resources:
            for job in resource.jobs:
                if not isinstance(job.profile, TinySaTracePaneProfile):
                    continue
                request = job.profile.request_template
                for crop in job.crops:
                    for grouped_request, group_panes in tiny_groups:
                        if (grouped_request.input_mode == request.input_mode
                                and grouped_request.settings == request.settings
                                and grouped_request.readback_settings == request.readback_settings
                                and (None if grouped_request.external_correction is None else
                                     grouped_request.external_correction.fingerprint)
                                == (None if request.external_correction is None else
                                    request.external_correction.fingerprint)):
                            group_panes.append(pane_numbers[crop.pane_id])
                            break
                    else:
                        tiny_groups.append((request, [pane_numbers[crop.pane_id]]))
        if tiny_groups:
            groups = []
            for request, numbers in tiny_groups:
                salient = []
                if request.input_mode is not TinySaInputMode.PRESERVE:
                    salient.append(request.input_mode.name)
                if request.settings.accuracy is not TinySaSweepAccuracy.UNCHANGED:
                    accuracy = request.settings.accuracy.value
                    salient.append(text("tinysa.settings.summary_accuracy." + accuracy))
                if request.settings.rbw_mode is TinySaRbwMode.AUTO:
                    salient.append(text("tinysa.settings.summary_rbw_auto"))
                elif request.settings.rbw_mode is TinySaRbwMode.MANUAL:
                    assert request.settings.rbw_hz is not None
                    salient.append(text("tinysa.settings.summary_rbw_manual",
                                        value=f"{request.settings.rbw_hz / 1000:g}"))
                parts = requested_tinysa_parts(request.input_mode, request.settings,
                                               readback=request.readback_settings,
                                               correction=request.external_correction)
                extra = len(parts) - len(salient)
                groups.append(text("analyzer.pane.setup.impact_tinysa_group",
                                   panes=self._pane_numbers(numbers),
                                   salient=" / ".join(salient) if salient else
                                   text("tinysa.settings.preserve"),
                                   extra=(" " + text("analyzer.pane.setup.impact_more", count=extra)
                                          if extra else "")))
            summary += "\n" + text("analyzer.pane.setup.impact_tinysa_compact",
                                    groups="; ".join(groups))
        self._set_impact_text(summary)

    def _sync_contents_height(self) -> None:
        layout = self.scroll_contents.layout()
        assert isinstance(layout, QVBoxLayout)
        layout.invalidate()
        width = max(self.scroll_area.viewport().width(), layout.totalMinimumSize().width(), 1)
        height = layout.totalHeightForWidth(width)
        self._contents_height = max(layout.totalMinimumSize().height(), height)
        if self.scroll_contents.minimumHeight() != self._contents_height:
            self.scroll_contents.setMinimumHeight(self._contents_height)
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if not hasattr(self, "_contents_height"):
            return hint
        # Prefer showing the compact form in full, but bound long accepted or
        # refused plans. The parent may give less space; overflow still scrolls.
        root = self.layout()
        assert isinstance(root, QVBoxLayout)
        margins = root.contentsMargins()
        commands_height = self._actions_layout.sizeHint().height()
        return QSize(hint.width(), min(480, self._contents_height + commands_height
                                      + margins.top() + margins.bottom() + root.spacing()))

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_contents_height()
        self._refresh_apply_visibility()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            self._sync_contents_height()
        if watched is self.impact_summary and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._refresh_apply_visibility()
        return super().eventFilter(watched, event)

    @property
    def blocks_single_source(self) -> bool:
        return self._future is not None or self._prepared is not None or self._retained_pool is not None

    @property
    def can_close(self) -> bool:
        return not self.blocks_single_source and self._closing_handle is None

    def _rtl_unavailable_key(self, choice: AnalyzerSourceChoice) -> str | None:
        if choice.family is not DeviceFamily.RTL_SDR:
            return None
        if (self._selection is None or self._selection.release_pending
                or self._selection.refusal is not None
                or choice not in self._selection.choices):
            return "analyzer.pane.setup.rtl_selection_unavailable"
        if choice.runtime is None or choice.runtime.availability is not AdapterRuntimeAvailability.AVAILABLE:
            return "analyzer.pane.setup.rtl_runtime_unavailable"
        # RTL's generic-serial route is session scoped. A canonical snapshot
        # or calibration key would be a false stable-identity claim here.
        if (choice.binding.adapter_id != "rtl.librtlsdr.rx.v1"
                or choice.binding.snapshot is not None
                or choice.binding.calibration_identity is not None):
            return "analyzer.pane.setup.rtl_capability_unverified"
        try:
            ready = self._rtl_candidate_stage_available(choice.device_id, self._selection.revision)
        except Exception:
            ready = False
        if ready is not True:
            return "analyzer.pane.setup.rtl_owner_unavailable"
        return None

    @staticmethod
    def _rtl_assurance_key(choice: AnalyzerSourceChoice) -> str:
        return ("analyzer.pane.setup.rtl_session_scope"
                if choice.binding.rtl_session_route is not None else
                "analyzer.pane.setup.rtl_inventory_only")

    def _refresh_rtl_gain(self, row: _SlotRow, choice: AnalyzerSourceChoice | None) -> None:
        """Expose only the exact table of the currently selected RTL session."""
        route = (None if choice is None or choice.family is not DeviceFamily.RTL_SDR
                 else choice.binding.rtl_session_route)
        selection = self._selection
        context: tuple[object, ...] = (
            None if choice is None else choice.device_id,
            None if selection is None else selection.revision,
            None if route is None else route.observation_revision,
            None if route is None else route.runtime_set_sha256,
            None if route is None else route.tuner_type,
            None if route is None else route.manual_gain_contract_version,
            () if route is None else route.tuner_gains_tenth_db,
        )
        gains = (() if route is None or not route.manual_gain_available
                 else route.tuner_gains_tenth_db)
        if context != row._gain_context:
            # A new source, selection revision, or selected-tuner observation
            # invalidates any prior manual choice. Fresh Stage starts at Auto.
            with QSignalBlocker(row.manual_gain):
                row.manual_gain.clear()
                row.manual_gain.addItem(text("analyzer.pane.setup.rtl_gain_auto"), None)
                for value in gains:
                    row.manual_gain.addItem(text("analyzer.pane.setup.rtl_gain_value",
                                                 value=value / 10), value)
            row._gain_context = context
        rtl = choice is not None and choice.family is DeviceFamily.RTL_SDR
        ready = (choice is not None and rtl
                 and self._rtl_unavailable_key(choice) is None)
        row.manual_gain.setEnabled(bool(ready and not self.blocks_single_source))
        if rtl and not gains:
            reason = text("analyzer.pane.setup.rtl_gain_unavailable")
        elif rtl:
            reason = text("analyzer.pane.setup.rtl_gain_scope")
        else:
            reason = ""
        row.manual_gain.setToolTip(reason)
        row.manual_gain.setAccessibleName(text("analyzer.pane.setup.rtl_gain_name", pane=row.number))
        row.manual_gain.setAccessibleDescription(reason)
        row.gain_label.setVisible(rtl)
        row.manual_gain.setVisible(rtl)

    def _rtl_choice_tip(self, choice: AnalyzerSourceChoice, reason: str | None) -> str:
        if reason is not None:
            return text(reason)
        return text(self._rtl_assurance_key(choice)) if choice.family is DeviceFamily.RTL_SDR else choice.device_id

    def _source_user_changed(self, row: _SlotRow) -> None:
        source_id = row.source.currentData()
        if source_id != row._route_source_id:
            # A deliberate source edit invalidates the old source-scoped pin;
            # passive discovery refreshes never call this signal path.
            destination = next((candidate for candidate in self._rows
                                if candidate is not row and candidate.source.currentData() == source_id), None)
            row._route_source_id = source_id
            if destination is None:
                row._route_intent = None
                row._route_unavailable = False
                row._retained_family = None
                row._retained_network = False
            else:
                # Joining an existing source group adopts its one route intent;
                # it never manufactures a conflicting peer draft or defaults.
                row._route_intent = destination._route_intent
                row._route_unavailable = destination._route_unavailable
                row._retained_family = destination._retained_family
                row._retained_network = destination._retained_network
        if row.source.currentData() is None:
            # Explicitly choosing Empty clears this draft's chain. A passive
            # source-list refresh uses _source_changed directly and retains it.
            with QSignalBlocker(row.chain):
                row.chain.setCurrentIndex(row.chain.findData(ReceiverChainSelection.RX1.value))
        self._source_changed(row, explicit_source_change=True)
        self._refresh_actions()

    @staticmethod
    def _route_label(route: PlutoOperationalRouteIntent) -> str:
        prefix = "USB" if route.uri.startswith("usb:") else "IP"
        return f"{prefix} · {route.uri}"

    def _route_tip(self, route: PlutoOperationalRouteIntent | None = None) -> str:
        if route is None:
            return text("analyzer.pane.setup.route_help")
        return text("analyzer.pane.setup.route_pinned_help", route=self._route_label(route))

    def _route_scope_tip(self, row: _SlotRow, base: str) -> str:
        source_id = row.source.currentData()
        peers = tuple(candidate.number for candidate in self._rows
                      if candidate.source.currentData() == source_id)
        if len(peers) <= 1:
            return base
        return base + " " + text("analyzer.pane.setup.route_shared", panes=", ".join(map(str, peers)))

    def _route_owner(self, row: _SlotRow) -> _SlotRow:
        source_id = row.source.currentData()
        for candidate in self._rows:
            if candidate.source.currentData() == source_id:
                return candidate
        return row

    def _sync_route_peers(self, row: _SlotRow) -> None:
        source_id = row.source.currentData()
        if source_id is None:
            return
        for peer in self._rows:
            if peer is row or peer.source.currentData() != source_id:
                continue
            peer._route_source_id = source_id
            peer._route_intent = row._route_intent
            peer._route_unavailable = row._route_unavailable
            self._refresh_route(peer)

    def _route_user_changed(self, row: _SlotRow) -> None:
        if self.blocks_single_source:
            return
        value = row.route.currentData()
        row._route_intent = value if isinstance(value, PlutoOperationalRouteIntent) else None
        choice = next((item for item in self._choices
                       if item.device_id == row.source.currentData()), None)
        row._route_unavailable = (row._route_intent is not None
                                  and (choice is None or row._route_intent not in choice.operational_routes))
        row._route_source_id = row.source.currentData()
        owner = self._route_owner(row)
        if owner is not row:
            row._route_intent = owner._route_intent
            row._route_unavailable = owner._route_unavailable
            self._refresh_route(row)
        else:
            self._sync_route_peers(row)
        self._refresh_actions()

    def _refresh_route(self, row: _SlotRow) -> None:
        source_id = row.source.currentData()
        choice = next((item for item in self._choices if item.device_id == source_id), None)
        requested = row._route_intent
        is_ad = choice is not None and choice.family is DeviceFamily.AD936X
        routes = () if choice is None else choice.operational_routes
        available = requested is None or requested in routes
        missing_source = source_id is not None and choice is None
        show = is_ad or requested is not None or missing_source
        with QSignalBlocker(row.route):
            row.route.clear()
            if not show:
                row.route.hide()
                row.route_scope.hide()
                row.route.setToolTip("")
                row.route.setAccessibleDescription("")
                row._route_unavailable = False
                return
            row.route.addItem(text("analyzer.pane.setup.route_automatic"), None)
            for route in routes:
                row.route.addItem(self._route_label(route), route)
            if requested is not None and not available:
                row.route.addItem(text("analyzer.pane.setup.route_unavailable",
                                       route=self._route_label(requested)), requested)
                model = row.route.model()
                if isinstance(model, QStandardItemModel):
                    item = model.item(row.route.count() - 1)
                    if item is not None:
                        item.setEnabled(False)
            index = row.route.findData(requested) if requested is not None else 0
            row.route.setCurrentIndex(index if index >= 0 else 0)
            base_tip = self._route_tip(requested)
            if is_ad and not routes and requested is None:
                base_tip = text("analyzer.pane.setup.route_no_metadata_help")
            tip = self._route_scope_tip(row, base_tip)
            row.route.setToolTip(tip)
            row.route.setAccessibleDescription(tip)
            row.route.setVisible(True)
            peers = tuple(candidate.number for candidate in self._rows
                          if candidate.source.currentData() == source_id)
            if len(peers) > 1:
                row.route_scope.setText(text("analyzer.pane.setup.route_shared",
                                             panes=", ".join(map(str, peers))))
                row.route_scope.setAccessibleName(row.route_scope.text())
                row.route_scope.show()
            else:
                row.route_scope.hide()
        row._route_unavailable = requested is not None and not available
        owner = self._route_owner(row)
        row.route.setEnabled(not self.blocks_single_source and owner is row)

    @staticmethod
    def _selected_chain(row: _SlotRow) -> ReceiverChainSelection:
        # Qt may return a plain string for StrEnum item data. The pane draft
        # still receives the typed selection, never a suffix from a source ID.
        return ReceiverChainSelection(row.chain.currentData())

    def _rx2_unavailable_key(self, choice: AnalyzerSourceChoice | None) -> str:
        if choice is None or choice.family is not DeviceFamily.AD936X:
            return "analyzer.pane.setup.rx2_non_ad"
        selection = self._selection
        if (selection is None or selection.release_pending or selection.refusal is not None
                or choice not in selection.choices):
            return "analyzer.pane.setup.rx2_selection_unavailable"
        snapshot = choice.binding.snapshot
        if snapshot is None or snapshot.rx_channel_count is None or not any(
                item.field is CapabilityField.RX_CHANNEL_COUNT
                and item.origin is CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY
                for item in snapshot.evidence):
            return "analyzer.pane.setup.rx2_unknown"
        if snapshot.rx_channel_count < 2:
            return "analyzer.pane.setup.rx2_single"
        return ""

    def _chain_refusal_key(self, row: _SlotRow) -> str | None:
        if self._selected_chain(row) is ReceiverChainSelection.RX1:
            return None
        choice = next((item for item in self._choices
                       if item.device_id == row.source.currentData()), None)
        return self._rx2_unavailable_key(choice) or None

    def _route_refusal_key(self, row: _SlotRow) -> str | None:
        source_id = row.source.currentData()
        if source_id is None:
            return None
        choice = next((item for item in self._choices if item.device_id == source_id), None)
        if choice is None:
            return "analyzer.pane.setup.route_source_unavailable"
        if row._route_intent is not None and row._route_intent not in choice.operational_routes:
            return "analyzer.pane.setup.route_unavailable_refusal"
        return None

    def _refresh_chain(self, row: _SlotRow, choice: AnalyzerSourceChoice | None) -> None:
        reason = self._rx2_unavailable_key(choice)
        model = row.chain.model()
        rx2_index = row.chain.findData(ReceiverChainSelection.RX2.value)
        row.chain.setItemText(rx2_index, text("analyzer.pane.setup.chain_rx2_unavailable"
                                              if reason else "analyzer.pane.setup.chain_rx2"))
        row.chain.view().setMinimumWidth(
            row.chain.fontMetrics().horizontalAdvance(row.chain.itemText(rx2_index)) + 36)
        if isinstance(model, QStandardItemModel):
            item = model.item(rx2_index)
            if item is not None:
                item.setEnabled(not reason)
        explanation = text(reason or "analyzer.pane.setup.rx2_candidate")
        row.chain.setToolTip(explanation)
        row.chain.setAccessibleDescription(explanation)
        row.chain.setEnabled((choice is not None and choice.family is DeviceFamily.AD936X
                              or self._selected_chain(row) is ReceiverChainSelection.RX2)
                             and not self.blocks_single_source)

    def _refresh_ad_rate_choices(self, row: _SlotRow, choice: AnalyzerSourceChoice,
                                 *, preserve_selection: bool, preferred: float | None = None) -> None:
        mode = self._selected_mode(row)
        sweep = mode is CaptureMeasurementMode.SWEEP
        full_receive = not sweep and RtbwBandPolicy(row.band.currentData()) is RtbwBandPolicy.FULL_RECEIVE
        choices = ad936x_pane_rate_choices(choice.binding.snapshot, sweep=sweep,
                                           full_receive=full_receive)
        requested = row.rate.currentData() if preferred is None else preferred
        if preserve_selection and requested is not None and requested not in choices:
            label = text("analyzer.pane.setup.ad_rate_unsupported", rate=f"{requested / 1e6:g}")
            options = ((label, requested, False),) + tuple(
                (f"{value / 1_000_000:g} MS/s", value, True) for value in choices)
        else:
            options = tuple((f"{value / 1_000_000:g} MS/s", value, True) for value in choices)
            if not options:
                options = ((text("analyzer.pane.setup.ad_rate_unavailable"), None, False),)
        with QSignalBlocker(row.rate):
            row.rate.clear()
            for label, value, enabled in options:
                row.rate.addItem(label, value)
                if not enabled:
                    model = row.rate.model()
                    if isinstance(model, QStandardItemModel):
                        item = model.item(row.rate.count() - 1)
                        if item is not None:
                            item.setEnabled(False)
            index = row.rate.findData(requested) if requested is not None else -1
            if index >= 0:
                row.rate.setCurrentIndex(index)
            elif preserve_selection:
                row.rate.setCurrentIndex(0)
            else:
                row.rate.setCurrentIndex(row.rate.count() - 1)

    def update_sources(self, selection: AnalyzerSourceSelection | None) -> None:
        if self._released or self.blocks_single_source:
            return
        previous_selection = self._selection
        self._selection = selection
        admission_state = (() if selection is None else
                           tuple((choice.device_id, self._rtl_unavailable_key(choice))
                                 for choice in selection.choices if choice.family is DeviceFamily.RTL_SDR))
        if selection is previous_selection and admission_state == self._rtl_admission_state:
            return
        self._rtl_admission_state = admission_state
        self._choices = () if selection is None else selection.choices
        for row in self._rows:
            previous = row.source.currentData()
            if row._route_source_id is None:
                row._route_source_id = previous
            with QSignalBlocker(row.source):
                row.source.clear()
                row.source.addItem(text("analyzer.pane.setup.empty"), None)
                for choice in self._choices:
                    reason = self._rtl_unavailable_key(choice)
                    label = (choice.label if reason is None else
                             text("analyzer.pane.setup.rtl_unavailable_choice", source=choice.label))
                    row.source.addItem(label, choice.device_id)
                    index = row.source.count() - 1
                    row.source.setItemData(index, self._rtl_choice_tip(choice, reason),
                                           Qt.ItemDataRole.ToolTipRole)
                    model = row.source.model()
                    if reason is not None and isinstance(model, QStandardItemModel):
                        item = model.item(index)
                        if item is not None:
                            item.setEnabled(False)
                if previous is not None and row.source.findData(previous) < 0:
                    row.source.addItem(text("analyzer.pane.setup.source_unavailable", source=previous), previous)
                    missing_index = row.source.count() - 1
                    row.source.setItemData(missing_index,
                                           text("analyzer.pane.setup.source_unavailable_help"),
                                           Qt.ItemDataRole.ToolTipRole)
                index = row.source.findData(previous)
                row.source.setCurrentIndex(max(index, 0))
            self._source_changed(row, preserve_range=True)
        self._refresh_actions()

    def _source_changed(self, row: _SlotRow, *, preserve_range: bool = False,
                        explicit_source_change: bool = False) -> None:
        source_id = row.source.currentData()
        choice = next((item for item in self._choices if item.device_id == source_id), None)
        family = None if choice is None else choice.family
        missing_preserve = choice is None and source_id is not None and preserve_range
        if choice is not None:
            row._retained_family = family
            row._retained_network = choice.transport_label.casefold() in {"ip", "ethernet"}
        elif not missing_preserve:
            row._retained_family = None
            row._retained_network = False
        reason = None if choice is None else self._rtl_unavailable_key(choice)
        rtl_ready = choice is not None and reason is None
        # This is only the new row draft. Never carry AD/HackRF's explicit
        # full-receive request into RTL, whose compiler admits edge trim only.
        if family is DeviceFamily.RTL_SDR:
            edge_index = row.band.findData(RtbwBandPolicy.EDGE_TRIMMED.value)
            if edge_index >= 0 and row.band.currentIndex() != edge_index:
                with QSignalBlocker(row.band):
                    row.band.setCurrentIndex(edge_index)
        tip = (self._rtl_choice_tip(choice, reason)
               if choice is not None and choice.family is DeviceFamily.RTL_SDR else "")
        row.source.setToolTip(tip)
        row.source.setAccessibleDescription(tip)
        self._refresh_chain(row, choice)
        self._refresh_rtl_gain(row, choice)
        previous_rate = row.rate.currentData()
        if not missing_preserve:
            with QSignalBlocker(row.rate):
                row.rate.clear()
                rates: tuple[tuple[str, float | None], ...]
                if family is DeviceFamily.AD936X:
                    rates = ()
                elif family is DeviceFamily.HACKRF:
                    rates = (("16 MS/s", 16_000_000.0), ("20 MS/s", 20_000_000.0))
                elif family is DeviceFamily.RTL_SDR:
                    rates = tuple((f"{value / 1_000_000:g} MS/s", float(value))
                                  for value in sorted(RTL_RATE_CHOICES_HZ))
                elif family is DeviceFamily.TINYSA:
                    rates = (("—", 20_000_000.0),)
                else:
                    rates = (("—", None),)
                for label, value in rates:
                    row.rate.addItem(label, value)
                if family is not DeviceFamily.AD936X:
                    index = row.rate.findData(previous_rate)
                    row.rate.setCurrentIndex(index if index >= 0 else row.rate.count() - 1)
        previous_mode = row.mode.currentData()
        if not missing_preserve:
            with QSignalBlocker(row.mode):
                row.mode.clear()
                modes: tuple[CaptureMeasurementMode | None, ...]
                if family is DeviceFamily.HACKRF:
                    modes = (CaptureMeasurementMode.RTBW, CaptureMeasurementMode.SWEEP)
                elif family is DeviceFamily.AD936X:
                    modes = (CaptureMeasurementMode.RTBW, CaptureMeasurementMode.SWEEP)
                elif family is DeviceFamily.RTL_SDR:
                    modes = (CaptureMeasurementMode.RTBW,)
                elif family is DeviceFamily.TINYSA:
                    modes = (CaptureMeasurementMode.INSTRUMENT_TRACE,)
                else:
                    modes = (None,)
                for mode in modes:
                    label = (text("analyzer.pane.setup.mode_empty") if mode is None else
                             text("analyzer.pane.setup.mode_" + mode.value))
                    row.mode.addItem(label, mode)
                index = row.mode.findData(previous_mode)
                row.mode.setCurrentIndex(max(index, 0))
        if family is DeviceFamily.AD936X and choice is not None:
            row._last_mode = None
            if explicit_source_change or not preserve_range:
                row._rtbw_rate = None
                row._sweep_rate = None
            elif self._selected_mode(row) is CaptureMeasurementMode.RTBW:
                row._rtbw_rate = previous_rate
            elif self._selected_mode(row) is CaptureMeasurementMode.SWEEP:
                row._sweep_rate = previous_rate
            self._refresh_ad_rate_choices(row, choice, preserve_selection=preserve_range,
                                          preferred=previous_rate if preserve_range else None)
        row.mode_points.setCurrentWidget(row.points if family is DeviceFamily.TINYSA else row.mode)
        editable = family is not None and (family is not DeviceFamily.RTL_SDR or rtl_ready)
        row.start.setEnabled(editable and not self.blocks_single_source)
        row.stop.setEnabled(editable and not self.blocks_single_source)
        row.priority.setEnabled(editable and not self.blocks_single_source)
        row.maximum_revisit.setEnabled(editable and not self.blocks_single_source)
        if family is not None and not preserve_range:
            lower, upper = ((200, 210) if family is DeviceFamily.TINYSA else
                            (140, 148) if family is DeviceFamily.HACKRF else
                            (100, 101) if family is DeviceFamily.RTL_SDR else (100, 108))
            row.start.setValue(lower)
            row.stop.setValue(upper)
            if family in {DeviceFamily.AD936X, DeviceFamily.HACKRF}:
                with QSignalBlocker(row.band):
                    row.band.setCurrentIndex(row.band.findData(RtbwBandPolicy.FULL_RECEIVE))
            row.sweep_window.setValue(0.0)
        if missing_preserve:
            for field in (row.rate, row.fft, row.points, row.mode, row.band, row.sweep_window):
                field.setEnabled(False)
        else:
            self._mode_changed(row)
        self._refresh_route(row)
        self._refresh_tinysa_row(row)
        self._refresh_tinysa_selector()

    def _read_drafts(self) -> tuple[PaneSlotDraft, ...]:
        drafts = []
        for row in self._rows:
            source_id = row.source.currentData()
            choice = next((item for item in self._choices if item.device_id == source_id), None)
            network = (choice is not None and choice.transport_label.casefold() in {"ip", "ethernet"})
            if choice is None and row._retained_family is not None:
                network = row._retained_network
            retained_ad = (choice is None and row._retained_family is DeviceFamily.AD936X
                           and row._route_source_id == source_id)
            tiny = None
            if choice is not None and choice.family is DeviceFamily.TINYSA:
                drawer = row.tinysa_settings
                correction = drawer.correction_profile()
                tiny = TinySaPaneIntent(
                    settings=drawer.plan(), input_mode=TinySaInputMode(drawer.input.currentData()),
                    readback=drawer.readback.isChecked(), external_correction=correction,
                    frontend_chain=drawer.frontend_chain.text().strip() or "unknown",
                    allow_correction_extrapolation=correction is not None and drawer.extrapolate.isChecked())
            drafts.append(PaneSlotDraft(row.number) if source_id is None else PaneSlotDraft(
                row.number, source_id, row.start.value() * 1_000_000,
                row.stop.value() * 1_000_000,
                sample_rate_hz=row.rate.currentData(), fft_size=row.fft.currentData(),
                points=row.points.value(), network_discovery=network,
                operational_route=row._route_intent,
                measurement_mode=self._selected_mode(row), priority=row.priority.value(),
                maximum_revisit_s=row.maximum_revisit.value() or None, tinysa=tiny,
                sweep_window_hz=(row.sweep_window.value() * 1_000_000
                                 if ((choice is not None and choice.family is DeviceFamily.AD936X) or retained_ad)
                                 and self._selected_mode(row) is CaptureMeasurementMode.SWEEP
                                 and row.sweep_window.value() > 0 else None),
                rtbw_band=(RtbwBandPolicy(row.band.currentData())
                           if self._selected_mode(row) is CaptureMeasurementMode.RTBW
                           else RtbwBandPolicy.EDGE_TRIMMED),
                receiver_selection=self._selected_chain(row),
                manual_tuner_gain_tenth_db=row.manual_gain.currentData()
                if choice is not None and choice.family is DeviceFamily.RTL_SDR else None))
        return tuple(drafts)

    @staticmethod
    def _selected_mode(row: _SlotRow) -> CaptureMeasurementMode | None:
        # Qt stores StrEnum item data as plain str on Windows and offscreen.
        value = row.mode.currentData()
        return None if value is None else CaptureMeasurementMode(value)

    def _mode_changed(self, row: _SlotRow) -> None:
        source_id = row.source.currentData()
        choice = next((item for item in self._choices if item.device_id == source_id), None)
        family = None if choice is None else choice.family
        mode = self._selected_mode(row)
        if (row._last_mode is CaptureMeasurementMode.RTBW
                and mode is CaptureMeasurementMode.SWEEP):
            row._rtbw_rate = row.rate.currentData()
        if (row._last_mode is CaptureMeasurementMode.SWEEP
                and mode is CaptureMeasurementMode.RTBW):
            row._sweep_rate = row.rate.currentData()
        if family is DeviceFamily.AD936X and choice is not None:
            requested = row._sweep_rate if mode is CaptureMeasurementMode.SWEEP else row._rtbw_rate
            self._refresh_ad_rate_choices(row, choice, preserve_selection=requested is not None,
                                          preferred=requested)
            if requested is None:
                # A deliberate mode selection starts at the highest profile
                # admitted for that mode; capability refresh never does this.
                choices = ad936x_pane_rate_choices(
                    choice.binding.snapshot, sweep=mode is CaptureMeasurementMode.SWEEP,
                    full_receive=mode is not CaptureMeasurementMode.SWEEP and
                    RtbwBandPolicy(row.band.currentData()) is RtbwBandPolicy.FULL_RECEIVE)
                if choices:
                    with QSignalBlocker(row.rate):
                        row.rate.setCurrentIndex(row.rate.findData(choices[-1]))
        elif mode is CaptureMeasurementMode.SWEEP:
            with QSignalBlocker(row.rate):
                sweep_rate = 20_000_000.0
                row.rate.setCurrentIndex(row.rate.findData(sweep_rate))
        elif (row._last_mode is CaptureMeasurementMode.SWEEP
              and mode is CaptureMeasurementMode.RTBW and row._rtbw_rate is not None):
            index = row.rate.findData(row._rtbw_rate)
            if index >= 0:
                with QSignalBlocker(row.rate):
                    row.rate.setCurrentIndex(index)
        previous_fft = row.fft.currentData()
        sizes = (tuple(sorted(RTL_FFT_CHOICES)) if family is DeviceFamily.RTL_SDR else
                 (1024, 2048, 4096) if family is DeviceFamily.HACKRF
                 and mode is CaptureMeasurementMode.SWEEP else (1024, 4096, 16384))
        if row.fft.count() != len(sizes) or any(row.fft.itemData(i) != value
                                                 for i, value in enumerate(sizes)):
            with QSignalBlocker(row.fft):
                row.fft.clear()
                for value in sizes:
                    row.fft.addItem(str(value), value)
                index = row.fft.findData(previous_fft)
                row.fft.setCurrentIndex(index if index >= 0 else row.fft.findData(4096))
        blocked = self.blocks_single_source
        ready_sdr = family in {DeviceFamily.AD936X, DeviceFamily.HACKRF} or (
            family is DeviceFamily.RTL_SDR and choice is not None
            and self._rtl_unavailable_key(choice) is None)
        row.rate.setEnabled(ready_sdr
                            and mode is not CaptureMeasurementMode.SWEEP and not blocked)
        row.fft.setEnabled(ready_sdr and not blocked)
        row.points.setEnabled(family is DeviceFamily.TINYSA and not blocked)
        row.mode.setEnabled(family in {DeviceFamily.AD936X, DeviceFamily.HACKRF} and not blocked)
        row.band.setEnabled(family in {DeviceFamily.AD936X, DeviceFamily.HACKRF}
                            and mode is CaptureMeasurementMode.RTBW and not blocked)
        row.sweep_window.setEnabled(family is DeviceFamily.AD936X
                                    and mode is CaptureMeasurementMode.SWEEP and not blocked)
        row.sweep_window.setToolTip(text("analyzer.pane.setup.ad_sweep_window_help"))
        row.sweep_window.setAccessibleName(text("analyzer.pane.setup.ad_sweep_window_name",
                                                pane=row.number))
        row.fft.setToolTip(text("analyzer.pane.setup.fft_help"))
        row._last_mode = mode

    def _band_changed(self, row: _SlotRow) -> None:
        choice = next((item for item in self._choices if item.device_id == row.source.currentData()), None)
        if choice is not None and choice.family is DeviceFamily.AD936X \
                and self._selected_mode(row) is CaptureMeasurementMode.RTBW:
            requested = row.rate.currentData()
            self._refresh_ad_rate_choices(row, choice, preserve_selection=requested is not None,
                                          preferred=requested)

    def _begin_prepare(self) -> None:
        if self.blocks_single_source or self._released:
            return
        for row in self._rows:
            choice = next((item for item in self._choices if item.device_id == row.source.currentData()), None)
            if (choice is not None and choice.family is DeviceFamily.AD936X
                    and self._selected_mode(row) in {CaptureMeasurementMode.RTBW, CaptureMeasurementMode.SWEEP}
                    and row.rate.currentData() is None):
                self._set_error("analyzer.pane.setup.ad_sweep_window_refusal")
                return
            chain_reason = self._chain_refusal_key(row)
            if chain_reason is not None:
                self._set_error(chain_reason)
                return
            route_reason = self._route_refusal_key(row)
            if route_reason is not None:
                self._set_error(route_reason)
                return
            choice = next((item for item in self._choices if item.device_id == row.source.currentData()), None)
            reason = None if choice is None else self._rtl_unavailable_key(choice)
            if reason is not None:
                self._set_error(reason)
                return
        if not self._can_prepare():
            self._set_error("analyzer.pane.setup.base_busy")
            return
        try:
            drafts = self._read_drafts()
        except (ValueError, TypeError):
            self._set_error("analyzer.pane.setup.invalid")
            return
        for draft in drafts:
            choice = next((item for item in self._choices if item.device_id == draft.source_id), None)
            if choice is not None and choice.family is DeviceFamily.HACKRF \
                    and draft.measurement_mode is CaptureMeasurementMode.SWEEP:
                assert draft.start_hz is not None and draft.stop_hz is not None
                span = draft.stop_hz - draft.start_hz
                if (draft.start_hz % 1_000_000 or draft.stop_hz % 1_000_000
                        or span < 20_000_000 or draft.start_hz < 1_000_000
                        or draft.stop_hz > 6_000_000_000 or draft.fft_size not in (1024, 2048, 4096)):
                    self._set_error("hackrf.sweep.invalid")
                    return
        if not any(item.source_id is not None for item in drafts):
            self._set_error("analyzer.pane.setup.no_source")
            return
        self._set_error(None)
        self._submit("prepare", prepare_user_pane_session, drafts)

    def _begin_apply(self) -> None:
        if (self._released or self._future is not None or self._prepared is None
                or any(item.recording_conflict for item in self._prepared.preview)):
            return
        if not self._impact_readable():
            self._set_error("analyzer.pane.setup.impact_unreadable")
            self._refresh_apply_visibility()
            return
        for row in self._rows:
            chain_reason = self._chain_refusal_key(row)
            if chain_reason is not None:
                self._set_error(chain_reason)
                return
            route_reason = self._route_refusal_key(row)
            if route_reason is not None:
                self._set_error(route_reason)
                return
            choice = next((item for item in self._choices if item.device_id == row.source.currentData()), None)
            reason = None if choice is None else self._rtl_unavailable_key(choice)
            if reason is not None:
                self._set_error(reason)
                return
        if not self._can_prepare():
            self._set_error("analyzer.pane.setup.base_busy")
            return
        self._set_error(None)
        self._submit("apply", apply_user_pane_session, self._prepared)

    def _begin_discard(self) -> None:
        if self._released or self._future is not None:
            return
        if self._prepared is not None:
            self._submit("discard", _discard_uninstalled, self._prepared)
        elif self._retained_pool is not None:
            self._submit("cleanup", self._retained_pool.close)

    def close_applied_layout(self, handle: PaneProductSessionHandle) -> None:
        if self._released or self._future is not None or self._closing_handle is not None or not handle.can_close():
            return
        self._closing_handle = handle
        self._submit("close_layout", handle.shutdown_after_stop)

    def _submit(self, operation: str, task: Callable[..., Any], *args: Any) -> None:
        self._operation = operation
        self._future = self._executor.submit(task, *args)
        if operation == "prepare":
            self._set_impact_text(text("analyzer.pane.setup.preparing"))
            self._set_preview_text(text("analyzer.pane.setup.preparing"))
        elif operation == "apply":
            self._set_preview_text(text("analyzer.pane.setup.applying"))
        self._timer.start()
        self._refresh_actions()
        self.state_changed.emit()

    def _poll(self) -> None:
        future = self._future
        if future is None or not future.done():
            return
        self._timer.stop()
        operation = self._operation
        self._future = None
        self._operation = None
        try:
            value = future.result()
        except PaneUserStageError as error:
            if error.pool is not None:
                self._retained_pool = error.pool
            if operation == "prepare":
                self._set_preview_text("")
            elif operation == "apply":
                self._refresh_preview()
            self._set_error(self._refusal_key(error.reason),
                            failed_reason=error.failed_reason,
                            revisit_violations=error.revisit_violations,
                            cleanup_required=error.pool is not None)
        except Exception:
            if operation == "prepare":
                self._set_preview_text("")
            elif operation == "apply":
                self._refresh_preview()
            self._set_error("analyzer.pane.setup.operation_failed")
        else:
            if operation == "prepare":
                self._prepared = value
                self._refresh_preview()
            elif operation == "apply":
                assert self._prepared is not None
                prepared = self._prepared
                self._prepared = None  # Transfer ownership synchronously to the composition.
                try:
                    self._install(prepared.handle)
                except Exception:
                    self._prepared = prepared
                    self._set_error("analyzer.pane.setup.attach_failed")
                else:
                    self._set_impact_text("")
                    self.hide()
            elif operation == "discard":
                self._prepared = None
                self._set_impact_text("")
                self._set_preview_text("")
            elif operation == "cleanup":
                self._retained_pool = None
                self._set_error(self._error_key, revisit_violations=self._revisit_violations)
            elif operation == "close_layout":
                try:
                    self._uninstall()
                except Exception:
                    self._set_error("analyzer.pane.setup.operation_failed")
                else:
                    self._closing_handle = None
                    self._set_impact_text("")
                    self._set_preview_text("")
                    self.hide()
        if operation == "close_layout" and self._closing_handle is not None:
            QMessageBox.warning(self, text("analyzer.pane.setup.close_failed.title"),
                                text("analyzer.pane.setup.close_failed.detail"))
            self._closing_handle = None
        self._refresh_actions()
        self.state_changed.emit()

    def _refresh_preview(self) -> None:
        prepared = self._prepared
        if prepared is None:
            self._set_impact_text("")
            self._set_preview_text("")
            return
        self._refresh_impact_summary()
        lines = [text("analyzer.pane.setup.preview_intro")]
        sources = dict(prepared.plan.resource_sources)
        staged_routes = {}
        staged_route_sources = set()
        context = prepared.handle.rf_context
        if context is not None:
            staged_routes = {draft.source_id: draft.operational_route
                             for draft in context.drafts if draft.source_id is not None}
            staged_route_sources = {source for source, choice, _revision in context.selections
                                    if choice.family is DeviceFamily.AD936X}
        for item in prepared.preview:
            mode = (text("analyzer.pane.setup.time_sliced") if item.capture_job_count > 1 else
                    text("analyzer.pane.setup.shared") if len(item.affected_pane_ids) > 1 else
                    text("analyzer.pane.setup.parallel"))
            numbers = ", ".join(pane.rsplit("-", 1)[-1] for pane in item.affected_pane_ids)
            conflict = (" · " + text("analyzer.pane.setup.recording_conflict")
                        if item.recording_conflict else "")
            source_id = sources[item.physical_stream_resource_id]
            label = prepared.handle.source_labels.get(source_id, source_id)
            lines.append(text("analyzer.pane.setup.preview_resource", panes=numbers, source=label,
                              mode=mode, jobs=item.capture_job_count) + conflict)
            if source_id in staged_route_sources:
                route = staged_routes.get(source_id)
                lines.append(text("analyzer.pane.setup.preview_route",
                                  route=(text("analyzer.pane.setup.route_automatic")
                                         if route is None else self._route_label(route))))
        schedule = prepared.plan.layout.schedule
        assert schedule is not None
        # A logical pair is identified by typed endpoints in the staged plan,
        # never a source label, endpoint suffix or silicon marketing name.
        for group in prepared.plan.groups:
            endpoints = {endpoint.endpoint_id: endpoint.selection for endpoint in group.endpoints
                         if isinstance(endpoint, ReceiverEndpoint)}
            if set(endpoints.values()) != {ReceiverChainSelection.RX1, ReceiverChainSelection.RX2}:
                continue
            source_id = sources[group.physical_stream_resource_id]
            assignments = []
            pane_resources = {item.pane_id: item.physical_stream_resource_id
                              for item in schedule.pane_revisits}
            for slot in prepared.plan.layout.slots:
                pane_request = slot.request
                if (pane_request is not None and pane_request.receiver_endpoint_id in endpoints
                        and pane_resources.get(pane_request.pane_id) == group.physical_stream_resource_id):
                    assignments.append(text("analyzer.pane.setup.preview_paired_assignment",
                                            pane=slot.number,
                                            chain=endpoints[pane_request.receiver_endpoint_id].name))
            lines.append(text("analyzer.pane.setup.preview_paired_group",
                              source=prepared.handle.source_labels.get(source_id, source_id),
                              assignments=", ".join(assignments)))
            configuration = next((configuration for resource_id, configuration
                                  in prepared.plan.initial_ad_configurations
                                  if resource_id == group.physical_stream_resource_id), None)
            paired_sweep = next((job.profile for resource in schedule.resources
                                 if resource.physical_stream_resource_id == group.physical_stream_resource_id
                                 for job in resource.jobs
                                 if isinstance(job.profile, Ad936xPairedSweepPaneProfile)), None)
            if isinstance(paired_sweep, Ad936xPairedSweepPaneProfile):
                paired_request = paired_sweep.paired_request.sweep
                configuration = paired_sweep.configuration
                lines.append(text("analyzer.pane.setup.preview_paired_sweep_request",
                                  start=f"{paired_request.start_hz / 1e6:g}",
                                  stop=f"{paired_request.stop_hz / 1e6:g}",
                                  rate=f"{configuration.sample_rate_hz / 1e6:g}",
                                  filter=(text("analyzer.pane.setup.value_unknown")
                                          if configuration.analog_bandwidth_hz is None else
                                          f"{configuration.analog_bandwidth_hz / 1e6:g}"
                                          f"{text('analyzer.rf.unit.mhz')}"),
                                  gain=f"{configuration.gain_db:g}", fft=configuration.fft_size,
                                  bins=paired_request.analysis_bins_per_usable_window,
                                  overlap=f"{paired_request.overlap_hz / 1e6:g}",
                                  detector=configuration.detector, window=configuration.window,
                                  averaging=configuration.averaging_frames))
                for resource_id, pane_id, start, stop in prepared.plan.paired_sweep_requested_crops:
                    if resource_id == group.physical_stream_resource_id:
                        lines.append(text("analyzer.pane.setup.preview_paired_sweep_crop",
                                          pane=pane_id.rsplit("-", 1)[-1],
                                          start=f"{start / 1e6:g}", stop=f"{stop / 1e6:g}"))
                lines.append(text("analyzer.pane.setup.preview_paired_sweep_scope"))
            elif configuration is not None:
                hop = round(configuration.fft_size * (1.0 - configuration.overlap_ratio))
                lines.append(text("analyzer.pane.setup.preview_paired_request",
                                  center=f"{configuration.center_hz / 1e6:g}",
                                  rate=f"{configuration.sample_rate_hz / 1e6:g}",
                                  filter=(text("analyzer.pane.setup.value_unknown")
                                          if configuration.analog_bandwidth_hz is None else
                                          f"{configuration.analog_bandwidth_hz / 1e6:g}"
                                          f"{text('analyzer.rf.unit.mhz')}"),
                                  gain=f"{configuration.gain_db:g}", fft=configuration.fft_size,
                                  hop=hop, detector=configuration.detector,
                                  window=configuration.window,
                                  averaging=configuration.averaging_frames))
                lines.append(text("analyzer.pane.setup.preview_paired_scope"))
        intents = {item.pane_id: item for item in prepared.plan.scheduler_intents}
        for resource in prepared.preview:
            for estimate in resource.revisit_estimates:
                intent = intents.get(estimate.pane_id)
                if intent is None:
                    continue
                requested = intent.requested.minimum_revisit_s
                effective = intent.effective.minimum_revisit_s
                lines.append(text("analyzer.pane.setup.preview_scheduler",
                                  pane=estimate.pane_id.rsplit("-", 1)[-1],
                                  priority=intent.requested.weight,
                                  effective_priority=intent.effective.weight,
                                  target=self._preview_target(requested),
                                  effective_target=self._preview_target(effective),
                                  modeled=f"{estimate.maximum_revisit_s:.3f}",
                                  visits=estimate.visits_per_cycle))
        for pane_id, geometry in prepared.plan.ad_sweep_geometry:
            ad_profile = next((job.profile for resource in schedule.resources for job in resource.jobs
                               if isinstance(job.profile, Ad936xSweepPaneProfile)
                               and any(crop.pane_id == pane_id for crop in job.crops)), None)
            if ad_profile is None:
                raise RuntimeError("AD936x Sweep preview has no typed profile")
            filter_hz = ad_profile.configuration.analog_bandwidth_hz
            lines.append(text("analyzer.pane.setup.preview_ad_sweep",
                              pane=pane_id.rsplit("-", 1)[-1],
                              rate=f"{geometry.sample_rate_hz / 1_000_000:.2f}",
                              filter=(text("analyzer.pane.setup.value_unknown") if filter_hz is None
                                      else f"{filter_hz / 1_000_000:g}"),
                              window=f"{geometry.usable_window_hz / 1_000_000:g}",
                              bins=geometry.analysis_bins_per_usable_window,
                              fft=geometry.physical_fft_size,
                              step=f"{geometry.segment_stride_hz / 1_000_000:g}",
                              segments=geometry.segment_count,
                              spacing=f"{geometry.output_spacing_hz:.2f}",
                              memory=f"{geometry.reduced.total_bytes / (1024 * 1024):.2f}"))
        for resource_id, geometry in prepared.plan.paired_sweep_geometry:
            affected = next((item.affected_pane_ids for item in prepared.preview
                             if item.physical_stream_resource_id == resource_id), ())
            lines.append(text("analyzer.pane.setup.preview_paired_sweep_geometry",
                              panes=", ".join(pane_id.rsplit("-", 1)[-1] for pane_id in affected),
                              window=f"{geometry.usable_window_hz / 1e6:g}",
                              step=f"{geometry.segment_stride_hz / 1e6:g}",
                              segments=geometry.segment_count,
                              spacing=f"{geometry.output_spacing_hz:.2f}",
                              memory=f"{geometry.reduced.total_bytes / (1024 * 1024):.2f}"))
        for pane_id, geometry in prepared.plan.hackrf_sweep_geometry:
            # The spatial 5 MHz subband partition is NOT the firmware's
            # 20 MHz tuning step. AD's user N/W has no authority here.
            lines.append(text("analyzer.pane.setup.preview_hackrf_sweep",
                              pane=pane_id.rsplit("-", 1)[-1],
                              rate=f"{geometry.sample_rate_hz / 1_000_000:.2f}",
                              window=f"{geometry.usable_window_hz / 1_000_000:g}",
                              fft=geometry.physical_fft_size, step=20,
                              segments=geometry.segment_count,
                              spacing=f"{geometry.output_spacing_hz:.2f}",
                              memory=f"{geometry.reduced.total_bytes / (1024 * 1024):.2f}"))
        for pane_id, start, stop in prepared.plan.hackrf_hardware_ranges:
            lines.append(text("analyzer.pane.setup.preview_hackrf_capture",
                              pane=pane_id.rsplit("-", 1)[-1], start=start // 1_000_000,
                              stop=stop // 1_000_000))
        schedule = prepared.plan.layout.schedule
        assert schedule is not None
        has_standard_rtbw = False
        for resource_schedule in schedule.resources:
            for job in resource_schedule.jobs:
                profile = job.profile
                if isinstance(profile, RtlRtbwPaneProfile):
                    requested_gain = profile.request_template.manual_tuner_gain_tenth_db
                    gain_summary = (text("analyzer.pane.setup.rtl_gain_auto_preview")
                                    if requested_gain is None else
                                    text("analyzer.pane.setup.rtl_gain_manual_preview",
                                         value=requested_gain / 10))
                    for crop in job.crops:
                        lines.append(text("analyzer.pane.setup.preview_rtl_rtbw",
                                          pane=crop.pane_id.rsplit("-", 1)[-1],
                                          rate=f"{profile.sample_rate_hz / 1e6:g}",
                                          usable=f"{profile.usable_capture_span_hz / 1e6:g}",
                                          start=f"{crop.start_hz / 1e6:g}",
                                          stop=f"{crop.stop_hz / 1e6:g}",
                                          fft=profile.fft_size, hop=profile.hop_size,
                                          gain=gain_summary))
                    continue
                if (not isinstance(profile, (PaneCaptureProfile, HackrfRtbwPaneProfile))
                        or profile.measurement_mode is not CaptureMeasurementMode.RTBW):
                    continue
                has_standard_rtbw = True
                bandwidth = (profile.request_template.baseband_filter_hz
                             if isinstance(profile, HackrfRtbwPaneProfile)
                             else profile.analog_bandwidth_hz)
                for crop in job.crops:
                    lines.append(text("analyzer.pane.setup.preview_rtbw_band",
                                      pane=crop.pane_id.rsplit("-", 1)[-1],
                                      rate=f"{profile.sample_rate_hz / 1e6:g}",
                                      filter=f"{bandwidth / 1e6:g}",
                                      usable=f"{profile.usable_capture_span_hz / 1e6:g}",
                                      start=f"{crop.start_hz / 1e6:g}", stop=f"{crop.stop_hz / 1e6:g}",
                                      fft=profile.fft_size, hop=profile.hop_size))
        if has_standard_rtbw:
            lines.append(text("analyzer.pane.setup.rtbw_band_scope"))
        if any(isinstance(job.profile, RtlRtbwPaneProfile)
               for resource in schedule.resources for job in resource.jobs):
            context = getattr(prepared.handle, "rf_context", None)
            for _source_id, choice, _revision in getattr(context, "selections", ()):
                if choice.family is DeviceFamily.RTL_SDR:
                    lines.append(text(self._rtl_assurance_key(choice)))
            lines.append(text("analyzer.pane.setup.rtl_rtbw_scope"))
        tiny_requests = {crop.pane_id: job.profile.request_template
                         for resource in schedule.resources for job in resource.jobs
                         if isinstance(job.profile, TinySaTracePaneProfile) for crop in job.crops}
        for pane_id, request in sorted(tiny_requests.items()):
            commands = (() if request.input_mode is TinySaInputMode.PRESERVE else
                        (f"mode {request.input_mode.value} input",)) + request.settings.commands
            lines.append(text("analyzer.pane.setup.preview_tinysa", pane=pane_id.rsplit("-", 1)[-1],
                              start=f"{request.start_hz / 1e6:g}", stop=f"{request.stop_hz / 1e6:g}",
                              points=request.points,
                              input=text("tinysa.settings." + request.input_mode.value),
                              commands=" · ".join(commands) or text("tinysa.settings.preserve"),
                              readback=text("tinysa.settings.on" if request.readback_settings else
                                            "tinysa.settings.off"),
                              correction=text("tinysa.correction.none") if request.external_correction is None else
                              text("tinysa.correction.item", name=request.external_correction.profile_id,
                                   version=request.external_correction.profile_version,
                                   digest=request.external_correction.fingerprint[:8])))
            lines.append(text("analyzer.pane.setup.preview_tinysa_scope"))
        lines.append(text("analyzer.pane.setup.preview_scope"))
        self._set_preview_text("\n".join(lines))

    @staticmethod
    def _preview_target(value: float | None) -> str:
        return (text("analyzer.pane.setup.target_unset") if value is None else
                text("analyzer.pane.setup.target_value", value=f"{value:g}"))

    @staticmethod
    def _refusal_key(reason: PaneUserRefusal) -> str:
        if not isinstance(reason, PaneUserRefusal):
            raise TypeError("pane UI needs a typed refusal")
        return f"analyzer.pane.setup.refusal.{reason.value}"

    def _set_error(self, key: str | None, *,
                   revisit_violations: tuple[PaneRevisitEstimate, ...] = (),
                   cleanup_required: bool = False,
                   failed_reason: PaneUserRefusal | None = None) -> None:
        self._error_key = key
        self._revisit_violations = revisit_violations
        self._cleanup_required = cleanup_required
        self._failed_reason = failed_reason
        lines = [] if key is None else [text(key)]
        if failed_reason is not None:
            lines.append(text("analyzer.pane.setup.refusal.previous",
                              detail=text(self._refusal_key(failed_reason))))
        for item in revisit_violations:
            target = item.requested_maximum_revisit_s
            lines.append(text("analyzer.pane.setup.deadline_detail",
                              pane=item.pane_id.rsplit("-", 1)[-1],
                              modeled=f"{item.maximum_revisit_s:.3f}",
                              target=text("analyzer.pane.setup.target_unset")
                              if target is None else f"{target:g}"))
        if cleanup_required:
            lines.append(text("analyzer.pane.setup.deadline_cleanup"))
        value = "\n".join(lines)
        self.error.setText(value)
        self.error.setAccessibleName(value)
        self.error.setToolTip(value)
        self.error.setVisible(key is not None)
        if self._prepared is None and self._future is None:
            self._set_impact_text("" if key is None else text(key))
        self._sync_contents_height()

    def _refresh_actions(self) -> None:
        blocked = self._future is not None
        rtl_reason = next((reason for row in self._rows
                           for choice in self._choices if choice.device_id == row.source.currentData()
                           for reason in (self._rtl_unavailable_key(choice),) if reason is not None), None)
        chain_reason = next((reason for row in self._rows
                             for reason in (self._chain_refusal_key(row),) if reason is not None), None)
        route_reason = next((reason for row in self._rows
                             for reason in (self._route_refusal_key(row),) if reason is not None), None)
        refusal = chain_reason or rtl_reason or route_reason
        self.prepare.setEnabled(not blocked and refusal is None
                                and self._prepared is None and self._retained_pool is None)
        self._refresh_apply_visibility()
        self.prepare.setToolTip("" if refusal is None else text(refusal))
        self.prepare.setAccessibleDescription("" if refusal is None else text(refusal))
        self.discard.setEnabled(not blocked and (self._prepared is not None or self._retained_pool is not None))
        self.details.setEnabled(bool(self.preview.text() or self.error.text()))
        for row in self._rows:
            row.source.setEnabled(not self.blocks_single_source)
            self._source_changed(row, preserve_range=True)

    def set_theme(self, theme: ThemeId) -> None:
        self.setStyleSheet(stylesheet_for_theme(theme))
        for row in self._rows:
            for field in (row.source, row.chain, row.start, row.stop, row.rate, row.fft,
                          row.route, row.points, row.mode, row.mode_points, row.priority, row.maximum_revisit,
                          row.band, row.sweep_window):
                field.ensurePolished()
                field.setMinimumHeight(field.minimumSizeHint().height())
        self._sync_contents_height()

    def set_locale(self) -> None:
        self.gain_heading.setText(text("analyzer.pane.setup.rtl_gain_heading"))
        for row in self._rows:
            row.chain.setAccessibleName(text("analyzer.pane.setup.chain_name", pane=row.number))
            row.route.setAccessibleName(text("analyzer.pane.setup.route_name", pane=row.number))
            row.route.setToolTip(self._route_tip(row._route_intent))
            row.route.setAccessibleDescription(self._route_tip(row._route_intent))
            row.gain_label.setText(text("analyzer.pane.setup.rtl_gain_label", pane=row.number))
            row.manual_gain.setAccessibleName(text("analyzer.pane.setup.rtl_gain_name", pane=row.number))
            with QSignalBlocker(row.chain):
                for index in range(row.chain.count()):
                    chain = ReceiverChainSelection(row.chain.itemData(index))
                    row.chain.setItemText(index, text("analyzer.pane.setup.chain_" + chain.value))
            row.start.setSuffix(text("hackrf.unit.mhz"))
            row.stop.setSuffix(text("hackrf.unit.mhz"))
            row.priority.setAccessibleName(text("analyzer.pane.setup.priority_name", pane=row.number))
            row.maximum_revisit.setAccessibleName(text("analyzer.pane.setup.target_name", pane=row.number))
            row.priority.setToolTip(text("analyzer.pane.setup.scheduler_help"))
            row.maximum_revisit.setToolTip(text("analyzer.pane.setup.scheduler_help"))
            row.maximum_revisit.setSuffix(text("analyzer.pane.setup.seconds_suffix"))
            row.maximum_revisit.setSpecialValueText(text("analyzer.pane.setup.target_unset"))
            row.band.setAccessibleName(text("analyzer.pane.setup.rtbw_band_name", pane=row.number))
            row.band.setToolTip(text("analyzer.pane.setup.rtbw_band_help"))
            row.sweep_window.setSuffix(text("hackrf.unit.mhz"))
            row.sweep_window.setSpecialValueText(text("analyzer.pane.setup.ad_sweep_window_auto"))
            row.sweep_window.setAccessibleName(text("analyzer.pane.setup.ad_sweep_window_name",
                                                     pane=row.number))
            with QSignalBlocker(row.band):
                for index in range(row.band.count()):
                    policy = RtbwBandPolicy(row.band.itemData(index))
                    row.band.setItemText(index, text("analyzer.pane.setup.rtbw_band_" + policy.value))
            with QSignalBlocker(row.manual_gain):
                for index in range(row.manual_gain.count()):
                    value = row.manual_gain.itemData(index)
                    row.manual_gain.setItemText(index, text(
                        "analyzer.pane.setup.rtl_gain_auto" if value is None
                        else "analyzer.pane.setup.rtl_gain_value", **({} if value is None else {"value": value / 10})))
            if row.source.count():
                row.source.setItemText(0, text("analyzer.pane.setup.empty"))
            for index in range(1, row.source.count()):
                choice = next((item for item in self._choices
                               if item.device_id == row.source.itemData(index)), None)
                if choice is None:
                    continue
                reason = self._rtl_unavailable_key(choice)
                row.source.setItemText(index, choice.label if reason is None else
                                       text("analyzer.pane.setup.rtl_unavailable_choice",
                                            source=choice.label))
                row.source.setItemData(index, self._rtl_choice_tip(choice, reason),
                                       Qt.ItemDataRole.ToolTipRole)
            choice = next((item for item in self._choices
                           if item.device_id == row.source.currentData()), None)
            reason = None if choice is None else self._rtl_unavailable_key(choice)
            tip = (self._rtl_choice_tip(choice, reason)
                   if choice is not None and choice.family is DeviceFamily.RTL_SDR else "")
            row.source.setToolTip(tip)
            row.source.setAccessibleDescription(tip)
            self._refresh_rtl_gain(row, choice)
            self._refresh_chain(row, choice)
            # A staged draft cannot rebuild source/geometry selectors. Translate
            # the existing mode items in place, without changing their data or
            # issuing a source/mode command through currentIndexChanged.
            with QSignalBlocker(row.mode):
                for index in range(row.mode.count()):
                    value = row.mode.itemData(index)
                    key = ("analyzer.pane.setup.mode_empty" if value is None else
                           "analyzer.pane.setup.mode_" + CaptureMeasurementMode(value).value)
                    row.mode.setItemText(index, text(key))
            row.fft.setToolTip(text("analyzer.pane.setup.fft_help"))
            self._refresh_route(row)
        self.description.setText(text("analyzer.pane.setup.description"))
        self.mode_help.setText(text("analyzer.pane.setup.mode_help"))
        self.scheduler_toggle.setText(text("analyzer.pane.setup.scheduler"))
        self.scheduler_toggle.setAccessibleName(text("analyzer.pane.setup.scheduler"))
        self.band_toggle.setText(text("analyzer.pane.setup.rtbw_band"))
        self.band_toggle.setAccessibleName(text("analyzer.pane.setup.rtbw_band"))
        self.band_help.setText(text("analyzer.pane.setup.rtbw_band_help"))
        for header, key in zip(self.band_headers, (
                "analyzer.pane.setup.slot", "analyzer.pane.setup.rtbw_band",
                "analyzer.pane.setup.ad_sweep_window"), strict=True):
            header.setText(text(key))
        self.tinysa_toggle.setText(text("analyzer.pane.setup.tinysa_settings"))
        self.tinysa_toggle.setAccessibleName(text("analyzer.pane.setup.tinysa_settings"))
        self.tinysa_pane.setAccessibleName(text("analyzer.pane.setup.tinysa_settings"))
        for row in self._rows:
            row.tinysa_settings.set_locale()
            self._refresh_tinysa_row(row)
        self._refresh_tinysa_selector()
        self.scheduler_help.setText(text("analyzer.pane.setup.scheduler_help"))
        self.scroll_area.setAccessibleName(text("analyzer.pane.setup.open"))
        for header, key in zip(self.scheduler_headers, (
                "analyzer.pane.setup.slot", "analyzer.pane.setup.priority",
                "analyzer.pane.setup.target"), strict=True):
            header.setText(text(key))
        for header, key in zip(self.headers, (
                "analyzer.pane.setup.slot", "analyzer.pane.setup.source",
                "analyzer.pane.setup.start", "analyzer.pane.setup.stop",
                "analyzer.pane.setup.rate", "analyzer.pane.setup.fft",
                "analyzer.pane.setup.mode_points"), strict=True):
            header.setText(text(key))
        for button, key in ((self.prepare, "analyzer.pane.setup.prepare"),
                            (self.apply, "analyzer.pane.setup.apply"),
                            (self.discard, "analyzer.pane.setup.discard"),
                            (self.details, "analyzer.pane.setup.details")):
            button.setText(text(key))
            button.setAccessibleName(text(key))
        # Empty/mode/drawer labels were translated in place above. Retain the
        # exact selection while staged; a locale change cannot invalidate the
        # tinySA draft's source key or reset it when Discard unlocks controls.
        if self._prepared is not None:
            self._refresh_preview()
        self._set_error(self._error_key, revisit_violations=self._revisit_violations,
                        cleanup_required=self._cleanup_required, failed_reason=self._failed_reason)
        rtl_reason = next((reason for row in self._rows for choice in self._choices
                       if choice.device_id == row.source.currentData()
                       for reason in (self._rtl_unavailable_key(choice),) if reason is not None), None)
        chain_reason = next((reason for row in self._rows
                             for reason in (self._chain_refusal_key(row),) if reason is not None), None)
        reason = chain_reason or rtl_reason
        self.prepare.setToolTip("" if reason is None else text(reason))
        self.prepare.setAccessibleDescription("" if reason is None else text(reason))

    def release_after_shutdown(self) -> None:
        if self._released:
            return
        if not self.can_close:
            raise RuntimeError("pane editor retains a control operation or un-applied resource")
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=False)
        for row in self._rows:
            row.tinysa_settings.release_profiles()
        self._released = True


def _discard_uninstalled(prepared: PreparedPaneUserSession) -> None:
    handle = prepared.handle
    if handle.applied:
        for future in handle.pump.stop_all().values():
            future.result(timeout=125)
    discard_user_pane_session(prepared)


__all__ = ["IndependentPaneSetupV2"]
