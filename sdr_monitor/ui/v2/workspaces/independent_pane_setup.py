"""User-operated independent-source layout editor on the V2 Analyzer tab."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from PySide6.QtCore import QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_product_session import PaneProductSessionHandle
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft, PaneUserPlanError
from sdr_monitor.ui.v2_pane_user_stage import (
    PaneUserStageError, PreparedPaneUserSession, apply_user_pane_session,
    discard_user_pane_session, prepare_user_pane_session,
)

from ..design import ThemeId, stylesheet_for_theme
from ..i18n import text


class _SlotRow:
    def __init__(self, number: int, parent: QWidget) -> None:
        self.number = number
        self.number_label = QLabel(str(number), parent)
        self.source = QComboBox(parent)
        self.source.setProperty("ui2Role", "utility-select")
        self.source.setMinimumWidth(175)
        self.start = QDoubleSpinBox(parent)
        self.stop = QDoubleSpinBox(parent)
        for field in (self.start, self.stop):
            field.setRange(0.1, 10_000.0)
            field.setDecimals(3)
            field.setSingleStep(1.0)
            field.setProperty("ui2Role", "utility-select")
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
        self.points.setProperty("ui2Role", "utility-select")
        self.mode = QComboBox(parent)
        self.mode.setProperty("ui2Role", "utility-select")
        self.mode_points = QStackedWidget(parent)
        self.mode_points.addWidget(self.mode)
        self.mode_points.addWidget(self.points)
        self._last_mode: CaptureMeasurementMode | None = None
        self._rtbw_rate: float | None = None


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
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("independentPaneSetupV2")
        self.setProperty("ui2Root", True)
        self._install = install
        self._uninstall = uninstall
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
        self._rows = tuple(_SlotRow(number, self) for number in range(1, 5))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.description = QLabel(self)
        self.description.setProperty("ui2Role", "secondary")
        self.description.setWordWrap(True)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.description)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        self.headers = tuple(QLabel(self) for _ in range(7))
        for column, header in enumerate(self.headers):
            header.setProperty("ui2Role", "secondary")
            grid.addWidget(header, 0, column)
        for row_index, row in enumerate(self._rows, 1):
            for column, widget in enumerate((row.number_label, row.source, row.start,
                                             row.stop, row.rate, row.fft, row.mode_points)):
                grid.addWidget(widget, row_index, column)
            row.source.currentIndexChanged.connect(
                lambda _index, target=row: self._source_changed(target))
            row.mode.currentIndexChanged.connect(
                lambda _index, target=row: self._mode_changed(target))
        layout.addLayout(grid)
        self.mode_help = QLabel(self)
        self.mode_help.setProperty("ui2Role", "secondary")
        self.mode_help.setWordWrap(True)
        layout.addWidget(self.mode_help)
        actions = QHBoxLayout()
        self.prepare = self._button(self._begin_prepare)
        self.apply = self._button(self._begin_apply)
        self.discard = self._button(self._begin_discard)
        for button in (self.prepare, self.apply, self.discard):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
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

    @property
    def blocks_single_source(self) -> bool:
        return self._future is not None or self._prepared is not None or self._retained_pool is not None

    @property
    def can_close(self) -> bool:
        return not self.blocks_single_source and self._closing_handle is None

    def update_sources(self, selection: AnalyzerSourceSelection | None) -> None:
        if self._released or self.blocks_single_source or selection is self._selection:
            return
        self._selection = selection
        self._choices = () if selection is None else selection.choices
        for row in self._rows:
            previous = row.source.currentData()
            with QSignalBlocker(row.source):
                row.source.clear()
                row.source.addItem(text("analyzer.pane.setup.empty"), None)
                for choice in self._choices:
                    row.source.addItem(choice.label, choice.device_id)
                    row.source.setItemData(row.source.count() - 1, choice.device_id,
                                           Qt.ItemDataRole.ToolTipRole)
                index = row.source.findData(previous)
                row.source.setCurrentIndex(max(index, 0))
            self._source_changed(row, preserve_range=True)
        self._refresh_actions()

    def _source_changed(self, row: _SlotRow, *, preserve_range: bool = False) -> None:
        source_id = row.source.currentData()
        choice = next((item for item in self._choices if item.device_id == source_id), None)
        family = None if choice is None else choice.family
        previous_rate = row.rate.currentData()
        with QSignalBlocker(row.rate):
            row.rate.clear()
            rates: tuple[tuple[str, float], ...]
            if family is DeviceFamily.AD936X:
                rates = (("20 MS/s", 20_000_000.0), ("61.44 MS/s", 61_440_000.0))
            elif family is DeviceFamily.HACKRF:
                rates = (("16 MS/s", 16_000_000.0), ("20 MS/s", 20_000_000.0))
            else:
                rates = (("—", 20_000_000.0),)
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
        row.start.setEnabled(family is not None and not self.blocks_single_source)
        row.stop.setEnabled(family is not None and not self.blocks_single_source)
        if family is not None and not preserve_range:
            lower, upper = ((200, 210) if family is DeviceFamily.TINYSA else
                            (140, 148) if family is DeviceFamily.HACKRF else (100, 108))
            row.start.setValue(lower)
            row.stop.setValue(upper)

    def _read_drafts(self) -> tuple[PaneSlotDraft, ...]:
        drafts = []
        for row in self._rows:
            source_id = row.source.currentData()
            choice = next((item for item in self._choices if item.device_id == source_id), None)
            network = (choice is not None and choice.transport_label.casefold() in {"ip", "ethernet"})
            drafts.append(PaneSlotDraft(row.number) if source_id is None else PaneSlotDraft(
                row.number, source_id, row.start.value() * 1_000_000,
                row.stop.value() * 1_000_000,
                sample_rate_hz=row.rate.currentData(), fft_size=row.fft.currentData(),
                points=row.points.value(), network_discovery=network,
                measurement_mode=self._selected_mode(row)))
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
                row.rate.setCurrentIndex(row.rate.findData(20_000_000.0))
        elif (row._last_mode is CaptureMeasurementMode.SWEEP
              and mode is CaptureMeasurementMode.RTBW and row._rtbw_rate is not None):
            index = row.rate.findData(row._rtbw_rate)
            if index >= 0:
                with QSignalBlocker(row.rate):
                    row.rate.setCurrentIndex(index)
        previous_fft = row.fft.currentData()
        sizes = (1024, 4096) if mode is CaptureMeasurementMode.SWEEP else (1024, 4096, 16384)
        if row.fft.count() != len(sizes) or any(row.fft.itemData(i) != value
                                                 for i, value in enumerate(sizes)):
            with QSignalBlocker(row.fft):
                row.fft.clear()
                for value in sizes:
                    row.fft.addItem(str(value), value)
                index = row.fft.findData(previous_fft)
                row.fft.setCurrentIndex(index if index >= 0 else row.fft.findData(4096))
        blocked = self.blocks_single_source
        row.rate.setEnabled(family in {DeviceFamily.AD936X, DeviceFamily.HACKRF}
                            and mode is not CaptureMeasurementMode.SWEEP and not blocked)
        row.fft.setEnabled(family in {DeviceFamily.AD936X, DeviceFamily.HACKRF} and not blocked)
        row.points.setEnabled(family is DeviceFamily.TINYSA and not blocked)
        row.mode.setEnabled(family is DeviceFamily.HACKRF and not blocked)
        row._last_mode = mode

    def _begin_prepare(self) -> None:
        if self.blocks_single_source or self._released:
            return
        if not self._can_prepare():
            self._set_error("analyzer.pane.setup.base_busy")
            return
        try:
            drafts = self._read_drafts()
        except PaneUserPlanError:
            self._set_error("analyzer.pane.setup.invalid")
            return
        for draft in drafts:
            choice = next((item for item in self._choices if item.device_id == draft.source_id), None)
            if choice is not None and choice.family is DeviceFamily.HACKRF \
                    and draft.measurement_mode is CaptureMeasurementMode.SWEEP:
                assert draft.start_hz is not None and draft.stop_hz is not None
                span = draft.stop_hz - draft.start_hz
                if (draft.start_hz % 1_000_000 or draft.stop_hz % 1_000_000
                        or span < 20_000_000 or span > 320_000_000
                        or span % 20_000_000 or draft.fft_size not in (1024, 2048, 4096)):
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
            self.preview.setText(text("analyzer.pane.setup.preparing"))
        elif operation == "apply":
            self.preview.setText(text("analyzer.pane.setup.applying"))
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
                self.preview.clear()
            elif operation == "apply":
                self._refresh_preview()
            self._set_error("analyzer.pane.setup.stage_failed" if operation == "prepare"
                            else "analyzer.pane.setup.operation_failed")
        except Exception:
            if operation == "prepare":
                self.preview.clear()
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
                self.preview.clear()
            elif operation == "cleanup":
                self._retained_pool = None
            elif operation == "close_layout":
                try:
                    self._uninstall()
                except Exception:
                    self._set_error("analyzer.pane.setup.operation_failed")
                else:
                    self._closing_handle = None
                    self.preview.clear()
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
            self.preview.clear()
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
        lines.append(text("analyzer.pane.setup.preview_scope"))
        self.preview.setText("\n".join(lines))

    def _set_error(self, key: str | None) -> None:
        self.error.setText("" if key is None else text(key))
        self.error.setVisible(key is not None)

    def _refresh_actions(self) -> None:
        blocked = self._future is not None
        self.prepare.setEnabled(not blocked and self._prepared is None and self._retained_pool is None)
        self.apply.setEnabled(not blocked and self._prepared is not None
                              and not self._prepared.handle.applied
                              and not any(item.recording_conflict for item in self._prepared.preview))
        self.discard.setEnabled(not blocked and (self._prepared is not None or self._retained_pool is not None))
        for row in self._rows:
            row.source.setEnabled(not self.blocks_single_source)
            self._source_changed(row, preserve_range=True)

    def set_theme(self, theme: ThemeId) -> None:
        self.setStyleSheet(stylesheet_for_theme(theme))

    def set_locale(self) -> None:
        for row in self._rows:
            row.start.setSuffix(text("hackrf.unit.mhz"))
            row.stop.setSuffix(text("hackrf.unit.mhz"))
            if row.source.count():
                row.source.setItemText(0, text("analyzer.pane.setup.empty"))
        self.description.setText(text("analyzer.pane.setup.description"))
        self.mode_help.setText(text("analyzer.pane.setup.mode_help"))
        for header, key in zip(self.headers, (
                "analyzer.pane.setup.slot", "analyzer.pane.setup.source",
                "analyzer.pane.setup.start", "analyzer.pane.setup.stop",
                "analyzer.pane.setup.rate", "analyzer.pane.setup.fft",
                "analyzer.pane.setup.mode_points"), strict=True):
            header.setText(text(key))
        for button, key in ((self.prepare, "analyzer.pane.setup.prepare"),
                            (self.apply, "analyzer.pane.setup.apply"),
                            (self.discard, "analyzer.pane.setup.discard")):
            button.setText(text(key))
            button.setAccessibleName(text(key))
        selection = self._selection
        self._selection = None  # Rebuild the visible Empty label in the new locale.
        self.update_sources(selection)
        if self._prepared is not None:
            self._refresh_preview()

    def release_after_shutdown(self) -> None:
        if self._released:
            return
        if not self.can_close:
            raise RuntimeError("pane editor retains a control operation or un-applied resource")
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=False)
        self._released = True


def _discard_uninstalled(prepared: PreparedPaneUserSession) -> None:
    handle = prepared.handle
    if handle.applied:
        for future in handle.pump.stop_all().values():
            future.result(timeout=125)
    discard_user_pane_session(prepared)


__all__ = ["IndependentPaneSetupV2"]
