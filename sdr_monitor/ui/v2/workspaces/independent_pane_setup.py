"""User-operated independent-source layout editor on the V2 Analyzer tab."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QResizeEvent, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import AdapterRuntimeAvailability, DeviceFamily
from sdr_monitor.domain.pane_scheduler import (
    CaptureMeasurementMode, HackrfRtbwPaneProfile, PaneCaptureProfile, RtlRtbwPaneProfile,
    PaneRevisitEstimate, TinySaTracePaneProfile,
)
from sdr_monitor.domain.rtl_live import RTL_FFT_CHOICES, RTL_RATE_CHOICES_HZ
from sdr_monitor.domain.tinysa_settings import TinySaInputMode
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
from .analyzer_tinysa_settings import TinySaSettingsDrawer


class _SlotRow:
    def __init__(self, number: int, parent: QWidget) -> None:
        self.number = number
        self.number_label = QLabel(str(number), parent)
        self.number_label.setProperty("ui2Role", "secondary")
        self.source = QComboBox(parent)
        self.source.setProperty("ui2Role", "utility-select")
        self.source.setMinimumWidth(175)
        self.start = QDoubleSpinBox(parent)
        self.stop = QDoubleSpinBox(parent)
        for field in (self.start, self.stop):
            field.setRange(0.1, 10_000.0)
            field.setDecimals(3)
            field.setSingleStep(1.0)
            field.setProperty("ui2Role", "range-control")
        self.start.setValue(100.0)
        self.stop.setValue(108.0)
        self.rate = QComboBox(parent)
        self.rate.setProperty("ui2Role", "utility-select")
        self.fft = QComboBox(parent)
        self.fft.setProperty("ui2Role", "utility-select")
        for value in (1024, 4096, 16384):
            self.fft.addItem(str(value), value)
        self.fft.setCurrentIndex(1)
        self.points = QSpinBox(parent)
        self.points.setRange(2, 10001)
        self.points.setValue(101)
        self.points.setProperty("ui2Role", "range-control")
        self.mode = QComboBox(parent)
        self.mode.setProperty("ui2Role", "utility-select")
        self.mode_points = QStackedWidget(parent)
        self.mode_points.addWidget(self.mode)
        self.mode_points.addWidget(self.points)
        self._last_mode: CaptureMeasurementMode | None = None
        self._rtbw_rate: float | None = None
        self.band_number_label = QLabel(str(number), parent)
        self.band_number_label.setProperty("ui2Role", "secondary")
        self.band = QComboBox(parent)
        self.band.setProperty("ui2Role", "utility-select")
        for policy in RtbwBandPolicy:
            self.band.addItem(policy.value, policy)
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
        self.tinysa_settings = TinySaSettingsDrawer(parent)


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
            for column, widget in enumerate((row.number_label, row.source, row.start,
                                             row.stop, row.rate, row.fft, row.mode_points)):
                grid.addWidget(widget, row_index, column)
            row.source.currentIndexChanged.connect(
                lambda _index, target=row: self._source_user_changed(target))
            row.mode.currentIndexChanged.connect(
                lambda _index, target=row: self._mode_changed(target))
        layout.addLayout(grid)
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
        self.tinysa_pane = QComboBox(self.tinysa_controls)
        self.tinysa_pane.setProperty("ui2Role", "utility-select")
        tiny_controls.addWidget(self.tinysa_toggle)
        tiny_controls.addWidget(self.tinysa_pane)
        tiny_controls.addStretch(1)
        layout.addWidget(self.tinysa_controls)
        self.tinysa_stack = QStackedWidget(self)
        self.tinysa_stack.setMinimumHeight(300)
        self.tinysa_stack.setMaximumHeight(380)
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
        self.band_controls = QWidget(self)
        band_grid = QGridLayout(self.band_controls)
        band_grid.setContentsMargins(0, 0, 0, 0)
        band_grid.setHorizontalSpacing(6)
        band_grid.setColumnStretch(2, 1)
        self.band_headers = tuple(QLabel(self.band_controls) for _ in range(2))
        for column, header in enumerate(self.band_headers):
            header.setProperty("ui2Role", "secondary")
            band_grid.addWidget(header, 0, column)
        for row_index, row in enumerate(self._rows, 1):
            band_grid.addWidget(row.band_number_label, row_index, 0)
            band_grid.addWidget(row.band, row_index, 1)
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

    def _show_details(self) -> None:
        target = self.error if not self.error.isHidden() else self.preview
        self.scroll_area.setFocus(Qt.FocusReason.OtherFocusReason)
        self.scroll_area.verticalScrollBar().setValue(target.y())

    def _set_preview_text(self, value: str) -> None:
        self.preview.setText(value)
        self.preview.setAccessibleName(value)
        self._sync_contents_height()

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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            self._sync_contents_height()
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

    def _rtl_choice_tip(self, choice: AnalyzerSourceChoice, reason: str | None) -> str:
        if reason is not None:
            return text(reason)
        return text(self._rtl_assurance_key(choice)) if choice.family is DeviceFamily.RTL_SDR else choice.device_id

    def _source_user_changed(self, row: _SlotRow) -> None:
        self._source_changed(row)
        self._refresh_actions()

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
                index = row.source.findData(previous)
                row.source.setCurrentIndex(max(index, 0))
            self._source_changed(row, preserve_range=True)
        self._refresh_actions()

    def _source_changed(self, row: _SlotRow, *, preserve_range: bool = False) -> None:
        source_id = row.source.currentData()
        choice = next((item for item in self._choices if item.device_id == source_id), None)
        family = None if choice is None else choice.family
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
        previous_rate = row.rate.currentData()
        with QSignalBlocker(row.rate):
            row.rate.clear()
            rates: tuple[tuple[str, float | None], ...]
            if family is DeviceFamily.AD936X:
                rates = (("20 MS/s", 20_000_000.0), ("61.44 MS/s", 61_440_000.0))
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
            index = row.rate.findData(previous_rate)
            row.rate.setCurrentIndex(index if index >= 0 else row.rate.count() - 1)
        previous_mode = row.mode.currentData()
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
        row.mode_points.setCurrentWidget(row.points if family is DeviceFamily.TINYSA else row.mode)
        self._mode_changed(row)
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
        self._refresh_tinysa_row(row)
        self._refresh_tinysa_selector()

    def _read_drafts(self) -> tuple[PaneSlotDraft, ...]:
        drafts = []
        for row in self._rows:
            source_id = row.source.currentData()
            choice = next((item for item in self._choices if item.device_id == source_id), None)
            network = (choice is not None and choice.transport_label.casefold() in {"ip", "ethernet"})
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
                measurement_mode=self._selected_mode(row), priority=row.priority.value(),
                maximum_revisit_s=row.maximum_revisit.value() or None, tinysa=tiny,
                rtbw_band=(RtbwBandPolicy(row.band.currentData())
                           if self._selected_mode(row) is CaptureMeasurementMode.RTBW
                           else RtbwBandPolicy.EDGE_TRIMMED)))
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
        if mode is CaptureMeasurementMode.SWEEP:
            with QSignalBlocker(row.rate):
                sweep_rate = 61_440_000.0 if family is DeviceFamily.AD936X else 20_000_000.0
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
        row.fft.setToolTip(text("analyzer.pane.setup.fft_help"))
        row._last_mode = mode

    def _begin_prepare(self) -> None:
        if self.blocks_single_source or self._released:
            return
        for row in self._rows:
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
        for row in self._rows:
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
            if operation == "prepare" and error.revisit_violations:
                self._set_error("analyzer.pane.setup.deadline_refused",
                                revisit_violations=error.revisit_violations,
                                cleanup_required=error.pool is not None)
            else:
                self._set_error("analyzer.pane.setup.stage_failed" if operation == "prepare"
                                else "analyzer.pane.setup.operation_failed")
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
                    self.hide()
            elif operation == "discard":
                self._prepared = None
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
            self._set_preview_text("")
            return
        lines = [text("analyzer.pane.setup.preview_intro")]
        sources = dict(prepared.plan.resource_sources)
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
            lines.append(text("analyzer.pane.setup.preview_ad_sweep",
                              pane=pane_id.rsplit("-", 1)[-1],
                              rate=f"{geometry.sample_rate_hz / 1_000_000:.2f}",
                              window=f"{geometry.usable_window_hz / 1_000_000:g}",
                              bins=geometry.analysis_bins_per_usable_window,
                              fft=geometry.physical_fft_size,
                              step=f"{geometry.segment_stride_hz / 1_000_000:g}",
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
                    for crop in job.crops:
                        lines.append(text("analyzer.pane.setup.preview_rtl_rtbw",
                                          pane=crop.pane_id.rsplit("-", 1)[-1],
                                          rate=f"{profile.sample_rate_hz / 1e6:g}",
                                          usable=f"{profile.usable_capture_span_hz / 1e6:g}",
                                          start=f"{crop.start_hz / 1e6:g}",
                                          stop=f"{crop.stop_hz / 1e6:g}",
                                          fft=profile.fft_size, hop=profile.hop_size))
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

    def _set_error(self, key: str | None, *,
                   revisit_violations: tuple[PaneRevisitEstimate, ...] = (),
                   cleanup_required: bool = False) -> None:
        self._error_key = key
        self._revisit_violations = revisit_violations
        self._cleanup_required = cleanup_required
        lines = [] if key is None else [text(key)]
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
        self._sync_contents_height()

    def _refresh_actions(self) -> None:
        blocked = self._future is not None
        rtl_reason = next((reason for row in self._rows
                           for choice in self._choices if choice.device_id == row.source.currentData()
                           for reason in (self._rtl_unavailable_key(choice),) if reason is not None), None)
        self.prepare.setEnabled(not blocked and rtl_reason is None
                                and self._prepared is None and self._retained_pool is None)
        self.apply.setEnabled(not blocked and self._prepared is not None
                              and rtl_reason is None
                              and not self._prepared.handle.applied
                              and not any(item.recording_conflict for item in self._prepared.preview))
        self.prepare.setToolTip("" if rtl_reason is None else text(rtl_reason))
        self.prepare.setAccessibleDescription("" if rtl_reason is None else text(rtl_reason))
        self.discard.setEnabled(not blocked and (self._prepared is not None or self._retained_pool is not None))
        self.details.setEnabled(bool(self.preview.text() or self.error.text()))
        for row in self._rows:
            row.source.setEnabled(not self.blocks_single_source)
            self._source_changed(row, preserve_range=True)

    def set_theme(self, theme: ThemeId) -> None:
        self.setStyleSheet(stylesheet_for_theme(theme))
        for row in self._rows:
            for field in (row.source, row.start, row.stop, row.rate, row.fft,
                          row.points, row.mode, row.mode_points, row.priority, row.maximum_revisit,
                          row.band):
                field.ensurePolished()
                field.setMinimumHeight(field.minimumSizeHint().height())
        self._sync_contents_height()

    def set_locale(self) -> None:
        for row in self._rows:
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
            with QSignalBlocker(row.band):
                for index in range(row.band.count()):
                    policy = RtbwBandPolicy(row.band.itemData(index))
                    row.band.setItemText(index, text("analyzer.pane.setup.rtbw_band_" + policy.value))
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
        self.description.setText(text("analyzer.pane.setup.description"))
        self.mode_help.setText(text("analyzer.pane.setup.mode_help"))
        self.scheduler_toggle.setText(text("analyzer.pane.setup.scheduler"))
        self.scheduler_toggle.setAccessibleName(text("analyzer.pane.setup.scheduler"))
        self.band_toggle.setText(text("analyzer.pane.setup.rtbw_band"))
        self.band_toggle.setAccessibleName(text("analyzer.pane.setup.rtbw_band"))
        self.band_help.setText(text("analyzer.pane.setup.rtbw_band_help"))
        for header, key in zip(self.band_headers, (
                "analyzer.pane.setup.slot", "analyzer.pane.setup.rtbw_band"), strict=True):
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
                        cleanup_required=self._cleanup_required)
        reason = next((reason for row in self._rows for choice in self._choices
                       if choice.device_id == row.source.currentData()
                       for reason in (self._rtl_unavailable_key(choice),) if reason is not None), None)
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
