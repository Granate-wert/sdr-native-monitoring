"""Offline DFL workspace: thin GUI layer over :class:`OfflineDflPresenter`.

The widget is deliberately thin.  ``OfflineDflPresenter`` owns session
loading, frame navigation, markers, measurements, heatmap persistence and
exports; this module renders immutable :mod:`esw_dfl.ui.offline_state`
snapshots and forwards user intent back to the presenter.  The pyqtgraph
renderers stay owned by the presenter and are reparented into this widget
exactly once, mirroring the legacy ``MainWindow`` behaviour.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QCloseEvent, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..renderers import PyQtGraphSpectrumRenderer, PyQtGraphWaterfallRenderer
from .i18n import LocaleId, Translator
from .offline_presenter import OfflineDflPresenter
from .offline_state import OfflineWorkspaceSnapshot

_OPEN_FILES_FILTER = "DFL files (*.DFL *.dfl);;All files (*)"
_WORKSPACE_FILTER = "Workspace JSON (*.json);;All files (*)"
_CSV_FILTER = "CSV files (*.csv);;All files (*)"
_NPZ_FILTER = "NumPy NPZ (*.npz);;All files (*)"
_PNG_FILTER = "PNG images (*.png);;All files (*)"
_JSON_FILTER = "JSON files (*.json);;All files (*)"
_GIF_FILTER = "GIF animation (*.gif);;All files (*)"

_PLAYBACK_SPEEDS = ("0.25×", "0.5×", "1×", "2×", "4×", "8×")

_MARKER_COLUMNS = (
    "offline.marker_name",
    "offline.marker_type",
    "offline.marker_frequency",
    "offline.marker_power",
    "offline.marker_delta_f",
    "offline.marker_delta_l",
    "offline.marker_timestamp",
    "offline.marker_trace",
)

_RESULT_COLUMNS = (
    "offline.result_name",
    "offline.result_key",
    "offline.result_value",
)

ROLE_SESSION = "session"
ROLE_TRACE = "trace"
ROLE_WATERFALL = "waterfall"


class OfflineDflWorkspace(QWidget):
    """Session tree, spectrum/waterfall, playback and context inspector."""

    def __init__(
        self,
        presenter: OfflineDflPresenter | None = None,
        *,
        locale: LocaleId = LocaleId.RU,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tr = Translator(locale)
        self._locale = locale
        self._presenter = presenter if presenter is not None else OfflineDflPresenter()
        self._last_tree_key: tuple[object, ...] | None = None
        self.setObjectName("offlineDflWorkspace")
        self.setAccessibleName("offline_dfl_workspace")
        self._build_ui()
        self._wire_signals()
        self._presenter.snapshot_ready.connect(self._refresh_from_snapshot)
        self._presenter.frame_ready.connect(self._on_frame_ready)
        self._presenter.heatmap_phase.connect(self._on_heatmap_phase)
        self._presenter.time_gated_ready.connect(self._on_time_gated_ready)
        self._presenter.activity_event.connect(self._on_activity_event)
        self._presenter.error.connect(self._on_presenter_error)
        self._presenter.busy.connect(self._on_presenter_busy)
        self._presenter.waterfall_renderer.widget.installEventFilter(self)
        self._refresh_from_snapshot(self._presenter.snapshot())

    # ------------------------------------------------------------------
    # Accessors (test surface)
    # ------------------------------------------------------------------
    @property
    def presenter(self) -> OfflineDflPresenter:
        return self._presenter

    @property
    def tree(self) -> QTreeWidget:
        return self._session_tree

    @property
    def spectrum_view(self) -> PyQtGraphSpectrumRenderer:
        return self._presenter.spectrum_renderer

    @property
    def waterfall_view(self) -> PyQtGraphWaterfallRenderer:
        return self._presenter.waterfall_renderer

    @property
    def frame_spin(self) -> QSpinBox:
        return self._frame_spin

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.addLayout(self._build_left_column(), 0)
        root.addLayout(self._build_center_column(), 1)
        root.addLayout(self._build_right_column(), 0)

    def _build_left_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.addWidget(self._build_file_group())
        column.addWidget(self._build_session_group(), 1)
        return column

    def _build_file_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("offline.files"), self)
        layout = QVBoxLayout(group)
        self.open_files_button = QPushButton(self._tr.text("offline.open_files"), group)
        self.open_files_button.setObjectName("offlineOpenFilesButton")
        self.open_workspace_button = QPushButton(self._tr.text("offline.open_workspace"), group)
        self.open_workspace_button.setObjectName("offlineOpenWorkspaceButton")
        self.save_workspace_button = QPushButton(self._tr.text("offline.save_workspace"), group)
        self.save_workspace_button.setObjectName("offlineSaveWorkspaceButton")
        self.close_session_button = QPushButton(self._tr.text("offline.close_session"), group)
        self.close_session_button.setObjectName("offlineCloseSessionButton")
        layout.addWidget(self.open_files_button)
        layout.addWidget(self.open_workspace_button)
        layout.addWidget(self.save_workspace_button)
        layout.addWidget(self.close_session_button)
        return group

    def _build_session_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("offline.sessions"), self)
        layout = QVBoxLayout(group)
        self._session_tree = QTreeWidget(group)
        self._session_tree.setObjectName("offlineSessionTree")
        self._session_tree.setColumnCount(2)
        self._session_tree.setHeaderLabels((
            self._tr.text("offline.session"),
            "",
        ))
        self._session_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._session_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._session_tree.setRootIsDecorated(True)
        layout.addWidget(self._session_tree, 1)
        self._status_label = QLabel(self._tr.text("offline.status_no_session"), group)
        self._status_label.setObjectName("offlineStatusLabel")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)
        return group

    def _build_center_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.addWidget(self._presenter.spectrum_renderer.widget, 3)
        column.addWidget(self._presenter.waterfall_renderer.widget, 2)
        column.addWidget(self._build_playback_group())
        column.addWidget(self._build_heatmap_group())
        column.addWidget(self._build_time_gated_group())
        return column

    def _build_playback_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("offline.playback"), self)
        layout = QHBoxLayout(group)
        self._play_button = QPushButton(self._tr.text("offline.play"), group)
        self._play_button.setObjectName("offlinePlayButton")
        self._stop_button = QPushButton(self._tr.text("offline.stop"), group)
        self._stop_button.setObjectName("offlineStopButton")
        self._frame_spin = QSpinBox(group)
        self._frame_spin.setObjectName("offlineFrameSpin")
        self._frame_spin.setRange(1, 1)
        self._frame_spin.setKeyboardTracking(False)
        self._frame_count_label = QLabel("/ 1", group)
        self._frame_count_label.setObjectName("offlineFrameCountLabel")
        self._speed_combo = QComboBox(group)
        self._speed_combo.setObjectName("offlineSpeedCombo")
        self._speed_combo.addItems(_PLAYBACK_SPEEDS)
        self._speed_combo.setCurrentText("1×")
        self._loop_check = QCheckBox(self._tr.text("offline.loop"), group)
        self._loop_check.setObjectName("offlineLoopCheck")
        self._no_skip_check = QCheckBox(self._tr.text("offline.no_skip"), group)
        self._no_skip_check.setObjectName("offlineNoSkipCheck")
        layout.addWidget(self._play_button)
        layout.addWidget(self._stop_button)
        layout.addWidget(QLabel(self._tr.text("offline.frame"), group))
        layout.addWidget(self._frame_spin)
        layout.addWidget(self._frame_count_label)
        layout.addSpacing(12)
        layout.addWidget(QLabel(self._tr.text("offline.speed"), group))
        layout.addWidget(self._speed_combo)
        layout.addWidget(self._loop_check)
        layout.addWidget(self._no_skip_check)
        layout.addStretch(1)
        return group

    def _build_heatmap_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("offline.heatmap"), self)
        layout = QHBoxLayout(group)
        self.heatmap_enable_button = QPushButton(self._tr.text("offline.heatmap_enable"), group)
        self.heatmap_enable_button.setObjectName("offlineHeatmapEnableButton")
        self.heatmap_disable_button = QPushButton(self._tr.text("offline.heatmap_disable"), group)
        self.heatmap_disable_button.setObjectName("offlineHeatmapDisableButton")
        self.heatmap_recalculate_button = QPushButton(self._tr.text("offline.heatmap_recalculate"), group)
        self.heatmap_recalculate_button.setObjectName("offlineHeatmapRecalculateButton")
        self.heatmap_cancel_button = QPushButton(self._tr.text("offline.heatmap_cancel"), group)
        self.heatmap_cancel_button.setObjectName("offlineHeatmapCancelButton")
        self.heatmap_clear_button = QPushButton(self._tr.text("offline.heatmap_clear"), group)
        self.heatmap_clear_button.setObjectName("offlineHeatmapClearButton")
        self._heatmap_status_label = QLabel("", group)
        self._heatmap_status_label.setObjectName("offlineHeatmapStatusLabel")
        self._heatmap_status_label.setWordWrap(True)
        layout.addWidget(self.heatmap_enable_button)
        layout.addWidget(self.heatmap_disable_button)
        layout.addWidget(self.heatmap_recalculate_button)
        layout.addWidget(self.heatmap_cancel_button)
        layout.addWidget(self.heatmap_clear_button)
        layout.addWidget(self._heatmap_status_label, 1)
        return group

    def _build_time_gated_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("offline.time_gated"), self)
        layout = QHBoxLayout(group)
        self._time_gated_start = QDoubleSpinBox(group)
        self._time_gated_start.setObjectName("offlineTimeGatedStartMHz")
        self._time_gated_start.setRange(-1.0e6, 1.0e6)
        self._time_gated_start.setDecimals(6)
        self._time_gated_start.setSuffix(" MHz")
        self._time_gated_stop = QDoubleSpinBox(group)
        self._time_gated_stop.setObjectName("offlineTimeGatedStopMHz")
        self._time_gated_stop.setRange(-1.0e6, 1.0e6)
        self._time_gated_stop.setDecimals(6)
        self._time_gated_stop.setSuffix(" MHz")
        self._time_gated_run_button = QPushButton(self._tr.text("offline.time_gated_calculate"), group)
        self._time_gated_run_button.setObjectName("offlineTimeGatedRunButton")
        self._time_gated_cancel_button = QPushButton(self._tr.text("offline.time_gated_cancel"), group)
        self._time_gated_cancel_button.setObjectName("offlineTimeGatedCancelButton")
        self._time_gated_status_label = QLabel(self._tr.text("offline.time_gated_not_calculated"), group)
        self._time_gated_status_label.setObjectName("offlineTimeGatedStatusLabel")
        layout.addWidget(QLabel(self._tr.text("offline.time_gated_start"), group))
        layout.addWidget(self._time_gated_start)
        layout.addWidget(QLabel(self._tr.text("offline.time_gated_stop"), group))
        layout.addWidget(self._time_gated_stop)
        layout.addWidget(self._time_gated_run_button)
        layout.addWidget(self._time_gated_cancel_button)
        layout.addWidget(self._time_gated_status_label, 1)
        return group
    def _build_right_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        self._inspector = QTabWidget(self)
        self._inspector.setObjectName("offlineInspector")
        self._inspector.addTab(self._build_markers_tab(), self._tr.text("offline.markers"))
        self._inspector.addTab(self._build_results_tab(), self._tr.text("offline.results"))
        self._inspector.addTab(self._build_properties_tab(), self._tr.text("offline.properties"))
        self._events_text = QPlainTextEdit(self._inspector)
        self._events_text.setObjectName("offlineEventsText")
        self._events_text.setReadOnly(True)
        self._events_text.setMaximumBlockCount(500)
        self._inspector.addTab(self._events_text, self._tr.text("shell.bottom_tools.events"))
        self._logs_text = QPlainTextEdit(self._inspector)
        self._logs_text.setObjectName("offlineLogsText")
        self._logs_text.setReadOnly(True)
        self._logs_text.setMaximumBlockCount(500)
        self._inspector.addTab(self._logs_text, self._tr.text("shell.bottom_tools.logs"))
        column.addWidget(self._inspector, 1)
        column.addWidget(self._build_export_group())
        return column

    def _build_markers_tab(self) -> QWidget:
        tab = QWidget(self._inspector)
        layout = QVBoxLayout(tab)
        self._markers_table = QTableWidget(0, len(_MARKER_COLUMNS), tab)
        self._markers_table.setObjectName("offlineMarkersTable")
        self._markers_table.setHorizontalHeaderLabels([self._tr.text(key) for key in _MARKER_COLUMNS])
        self._markers_table.verticalHeader().setVisible(False)
        self._markers_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._markers_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._markers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._markers_table, 1)
        row = QHBoxLayout()
        self.add_marker_button = QPushButton(self._tr.text("offline.add_marker"), tab)
        self.add_marker_button.setObjectName("offlineAddMarkerButton")
        self.add_peak_marker_button = QPushButton(self._tr.text("offline.add_peak_marker"), tab)
        self.add_peak_marker_button.setObjectName("offlineAddPeakMarkerButton")
        self.add_delta_marker_button = QPushButton(self._tr.text("offline.add_delta_marker"), tab)
        self.add_delta_marker_button.setObjectName("offlineAddDeltaMarkerButton")
        self.remove_marker_button = QPushButton(self._tr.text("offline.remove_marker"), tab)
        self.remove_marker_button.setObjectName("offlineRemoveMarkerButton")
        self.clear_markers_button = QPushButton(self._tr.text("offline.clear_markers"), tab)
        self.clear_markers_button.setObjectName("offlineClearMarkersButton")
        for button in (
            self.add_marker_button,
            self.add_peak_marker_button,
            self.add_delta_marker_button,
            self.remove_marker_button,
            self.clear_markers_button,
        ):
            row.addWidget(button)
        layout.addLayout(row)
        return tab

    def _build_results_tab(self) -> QWidget:
        tab = QWidget(self._inspector)
        layout = QVBoxLayout(tab)
        self._results_table = QTableWidget(0, len(_RESULT_COLUMNS), tab)
        self._results_table.setObjectName("offlineResultsTable")
        self._results_table.setHorizontalHeaderLabels([self._tr.text(key) for key in _RESULT_COLUMNS])
        self._results_table.verticalHeader().setVisible(False)
        self._results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._results_table, 1)
        row = QHBoxLayout()
        self.remove_result_button = QPushButton(self._tr.text("offline.remove_marker"), tab)
        self.remove_result_button.setObjectName("offlineRemoveResultButton")
        self.clear_results_button = QPushButton(self._tr.text("offline.clear_results"), tab)
        self.clear_results_button.setObjectName("offlineClearResultsButton")
        row.addWidget(self.remove_result_button)
        row.addWidget(self.clear_results_button)
        row.addStretch(1)
        layout.addLayout(row)
        return tab

    def _build_properties_tab(self) -> QWidget:
        tab = QWidget(self._inspector)
        layout = QVBoxLayout(tab)
        self._properties_text = QPlainTextEdit(tab)
        self._properties_text.setObjectName("offlinePropertiesText")
        self._properties_text.setReadOnly(True)
        self._properties_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._properties_text, 1)
        return tab

    def _build_export_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("offline.export"), self)
        layout = QHBoxLayout(group)
        self._export_button = QPushButton(self._tr.text("offline.export"), group)
        self._export_button.setObjectName("offlineExportButton")
        self._export_menu = QMenu(self._export_button)
        self._build_export_menu()
        self._export_button.setMenu(self._export_menu)
        layout.addWidget(self._export_button)
        layout.addStretch(1)
        return group

    def _build_export_menu(self) -> None:
        items: list[tuple[str, Callable[[], None], str]] = [
            ("offline.export_trace", lambda: self._export_dialog("trace"), _CSV_FILTER),
            ("offline.export_traces", lambda: self._export_dialog("traces"), _CSV_FILTER),
            ("offline.export_npz", lambda: self._export_dialog("npz"), _NPZ_FILTER),
            ("offline.export_waterfall", lambda: self._export_dialog("waterfall"), _CSV_FILTER),
            ("offline.export_markers", lambda: self._export_dialog("markers"), _CSV_FILTER),
            ("offline.export_results", lambda: self._export_dialog("results"), _CSV_FILTER),
            ("offline.export_metadata", lambda: self._export_dialog("metadata"), _JSON_FILTER),
            ("offline.export_animation", lambda: self._export_dialog("animation"), _GIF_FILTER),
            ("offline.export_heatmap_png", lambda: self._export_dialog("heatmap_png"), _PNG_FILTER),
            ("offline.export_heatmap_csv", lambda: self._export_dialog("heatmap_csv"), _CSV_FILTER),
            ("offline.export_heatmap_npz", lambda: self._export_dialog("heatmap_npz"), _NPZ_FILTER),
            ("offline.export_heatmap_json", lambda: self._export_dialog("heatmap_json"), _JSON_FILTER),
            (
                "offline.export_time_gated_summary",
                lambda: self._export_dialog("time_gated_summary"),
                _CSV_FILTER,
            ),
            (
                "offline.export_time_gated_frames",
                lambda: self._export_dialog("time_gated_frames"),
                _CSV_FILTER,
            ),
            (
                "offline.export_time_gated_events",
                lambda: self._export_dialog("time_gated_events"),
                _CSV_FILTER,
            ),
            (
                "offline.export_time_gated_json",
                lambda: self._export_dialog("time_gated_json"),
                _JSON_FILTER,
            ),
        ]
        for key, handler, _filter in items:
            action = self._export_menu.addAction(self._tr.text(key))
            action.setObjectName(f"offlineExportAction.{key}")
            action.triggered.connect(handler)

    def _wire_signals(self) -> None:
        self.open_files_button.clicked.connect(self._on_open_files)
        self.open_workspace_button.clicked.connect(self._on_open_workspace)
        self.save_workspace_button.clicked.connect(self._on_save_workspace)
        self.close_session_button.clicked.connect(self._on_close_session)
        self._session_tree.itemClicked.connect(self._on_tree_item_clicked)
        self._frame_spin.valueChanged.connect(self._on_frame_spin_changed)
        self._play_button.clicked.connect(self._on_play_clicked)
        self._stop_button.clicked.connect(self._on_stop_clicked)
        self._speed_combo.currentTextChanged.connect(self._on_speed_changed)
        self._loop_check.toggled.connect(self._on_loop_toggled)
        self._no_skip_check.toggled.connect(self._on_no_skip_toggled)
        self.heatmap_enable_button.clicked.connect(self._on_heatmap_enable)
        self.heatmap_disable_button.clicked.connect(self._on_heatmap_disable)
        self.heatmap_recalculate_button.clicked.connect(self._on_heatmap_recalculate)
        self.heatmap_cancel_button.clicked.connect(self._on_heatmap_cancel)
        self.heatmap_clear_button.clicked.connect(self._on_heatmap_clear)
        self.add_marker_button.clicked.connect(self._presenter.add_marker)
        self.add_peak_marker_button.clicked.connect(self._presenter.add_peak_marker)
        self.add_delta_marker_button.clicked.connect(self._presenter.add_delta_marker)
        self.remove_marker_button.clicked.connect(self._on_remove_marker)
        self.clear_markers_button.clicked.connect(self._presenter.clear_markers)
        self.remove_result_button.clicked.connect(self._on_remove_result)
        self.clear_results_button.clicked.connect(self._presenter.clear_measurement_results)
        self._time_gated_run_button.clicked.connect(self._on_time_gated_run)
        self._time_gated_cancel_button.clicked.connect(self._presenter.cancel_time_gated_power)

    # ------------------------------------------------------------------
    # Event filter: waterfall wheel navigation
    # ------------------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if (
            watched is self._presenter.waterfall_renderer.widget
            and isinstance(event, QWheelEvent)
            and event.type() == QEvent.Type.Wheel
        ):
            self._presenter.handle_waterfall_wheel(
                event.angleDelta().y(), event.pixelDelta(), event.modifiers()
            )
            return True
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # Signal handlers
    # ------------------------------------------------------------------
    def _on_open_files(self) -> None:
        paths, _selected = QFileDialog.getOpenFileNames(self, self._tr.text("offline.open_files"), "", _OPEN_FILES_FILTER)
        if paths:
            self._presenter.open_files(paths)

    def _on_open_workspace(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(self, self._tr.text("offline.open_workspace"), "", _WORKSPACE_FILTER)
        if path:
            self._presenter.open_workspace(path)

    def _on_save_workspace(self) -> None:
        path, _selected = QFileDialog.getSaveFileName(self, self._tr.text("offline.save_workspace"), "", _WORKSPACE_FILTER)
        if path:
            self._presenter.save_workspace(path)

    def _on_close_session(self) -> None:
        self._presenter.close_active_session()

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        del column
        if item is None:
            return
        session_id = item.data(0, Qt.ItemDataRole.UserRole)
        kind = item.data(0, Qt.ItemDataRole.UserRole + 1)
        object_id = item.data(0, Qt.ItemDataRole.UserRole + 2)
        if session_id is None:
            return
        if kind is None:
            kind = ROLE_SESSION
        self._presenter.select_tree_item(session_id, kind, object_id)

    def _on_frame_spin_changed(self, value: int) -> None:
        self._presenter.show_frame(max(0, value - 1))

    def _on_play_clicked(self) -> None:
        self._presenter.toggle_play()

    def _on_stop_clicked(self) -> None:
        self._presenter.stop()

    def _on_speed_changed(self, text: str) -> None:
        if text:
            self._presenter.set_playback_speed(text)

    def _on_loop_toggled(self, enabled: bool) -> None:
        self._presenter.set_playback_loop(enabled)

    def _on_no_skip_toggled(self, enabled: bool) -> None:
        self._presenter.set_playback_no_skip(enabled)

    def _on_time_gated_run(self) -> None:
        start = self._time_gated_start.value()
        stop = self._time_gated_stop.value()
        if stop > start:
            self._presenter.request_time_gated_power(start_mhz=start, stop_mhz=stop)
        else:
            self._presenter.request_time_gated_power()

    def _on_heatmap_enable(self) -> None:
        self._presenter.heatmap_enable()

    def _on_heatmap_disable(self) -> None:
        self._presenter.heatmap_disable()

    def _on_heatmap_recalculate(self) -> None:
        self._presenter.heatmap_recalculate()

    def _on_heatmap_cancel(self) -> None:
        self._presenter.heatmap_cancel()

    def _on_activity_event(self, line: str) -> None:
        self._events_text.appendPlainText(line)
        self._logs_text.appendPlainText(line)

    def _on_time_gated_ready(self, result: object) -> None:
        quality = getattr(getattr(result, "calculation_quality", None), "value", "ready")
        self._time_gated_status_label.setText(f"Channel Power: {quality}")

    def _on_heatmap_clear(self) -> None:
        self._presenter.heatmap_clear()

    def _on_remove_marker(self) -> None:
        row = self._markers_table.currentRow()
        if row >= 0:
            self._presenter.remove_selected_marker(row)

    def _on_remove_result(self) -> None:
        item = self._results_table.item(self._results_table.currentRow(), 0)
        if item is None:
            return
        result_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(result_id, str) and result_id:
            self._presenter.remove_analysis_result(result_id)

    def _on_presenter_error(self, title: str, message: str) -> None:
        del title
        self._status_label.setText(message)
        self._status_label.setToolTip(message)

    def _on_presenter_busy(self, busy: bool, text: str) -> None:
        self.setEnabled(not busy)
        if busy and text:
            self._status_label.setText(text)

    def _on_heatmap_phase(self, event: object) -> None:
        del event
        self._refresh_from_snapshot(self._presenter.snapshot())

    def _on_frame_ready(self, snapshot: object) -> None:
        del snapshot
        self._refresh_frame_label()

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------
    def _export_dialog(self, kind: str) -> None:
        default_name = {
            "trace": "trace.csv",
            "traces": "traces.csv",
            "npz": "session.npz",
            "waterfall": "waterfall.csv",
            "markers": "markers.csv",
            "results": "results.csv",
            "metadata": "metadata.json",
            "animation": "animation.gif",
            "heatmap_png": "heatmap.png",
            "heatmap_csv": "heatmap.csv",
            "heatmap_npz": "heatmap.npz",
            "heatmap_json": "heatmap.json",
            "time_gated_summary": "time_gated_summary.csv",
            "time_gated_frames": "time_gated_frames.csv",
            "time_gated_events": "time_gated_events.csv",
            "time_gated_json": "time_gated.json",
        }.get(kind, "export")
        _filters = {
            "trace": _CSV_FILTER,
            "traces": _CSV_FILTER,
            "npz": _NPZ_FILTER,
            "waterfall": _CSV_FILTER,
            "markers": _CSV_FILTER,
            "results": _CSV_FILTER,
            "metadata": _JSON_FILTER,
            "animation": _GIF_FILTER,
            "heatmap_png": _PNG_FILTER,
            "heatmap_csv": _CSV_FILTER,
            "heatmap_npz": _NPZ_FILTER,
            "heatmap_json": _JSON_FILTER,
            "time_gated_summary": _CSV_FILTER,
            "time_gated_frames": _CSV_FILTER,
            "time_gated_events": _CSV_FILTER,
            "time_gated_json": _JSON_FILTER,
        }
        path, _selected = QFileDialog.getSaveFileName(
            self, self._tr.text("offline.export"), default_name, _filters.get(kind, _CSV_FILTER)
        )
        if not path:
            return
        self._presenter.request_export(kind, path)

    # Snapshot rendering
    # ------------------------------------------------------------------
    def _refresh_from_snapshot(self, snapshot: OfflineWorkspaceSnapshot) -> None:
        self._refresh_tree(snapshot)
        self._refresh_playback(snapshot)
        self._refresh_heatmap(snapshot)
        self._refresh_markers()
        self._refresh_results()
        self._refresh_properties(snapshot)

    def _refresh_tree(self, snapshot: OfflineWorkspaceSnapshot) -> None:
        key = tuple(
            (session.session_id, session.name, session.visible, session.current_frame)
            for session in snapshot.sessions
        )
        if key == self._last_tree_key:
            return
        self._last_tree_key = key
        self._session_tree.blockSignals(True)
        self._session_tree.clear()
        for session in snapshot.sessions:
            session_item = QTreeWidgetItem(self._session_tree)
            session_item.setText(0, session.name)
            session_item.setText(1, self._tr.text("offline.session"))
            session_item.setData(0, Qt.ItemDataRole.UserRole, session.session_id)
            session_item.setData(0, Qt.ItemDataRole.UserRole + 1, ROLE_SESSION)
            session_item.setFlags(session_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            for trace in session.traces:
                trace_item = QTreeWidgetItem(session_item)
                trace_item.setText(0, trace.name)
                trace_item.setText(1, self._tr.text("offline.trace"))
                trace_item.setData(0, Qt.ItemDataRole.UserRole, session.session_id)
                trace_item.setData(0, Qt.ItemDataRole.UserRole + 1, ROLE_TRACE)
                trace_item.setData(0, Qt.ItemDataRole.UserRole + 2, trace.trace_id)
            for waterfall in session.waterfalls:
                waterfall_item = QTreeWidgetItem(session_item)
                waterfall_item.setText(0, waterfall.name)
                waterfall_item.setText(1, self._tr.text("offline.waterfall"))
                waterfall_item.setData(0, Qt.ItemDataRole.UserRole, session.session_id)
                waterfall_item.setData(0, Qt.ItemDataRole.UserRole + 1, ROLE_WATERFALL)
                waterfall_item.setData(0, Qt.ItemDataRole.UserRole + 2, waterfall.waterfall_id)
            session_item.setExpanded(True)
        self._session_tree.blockSignals(False)
        self._select_active_session(snapshot.active_session_id)

    def _select_active_session(self, session_id: str | None) -> None:
        if session_id is None:
            return
        for index in range(self._session_tree.topLevelItemCount()):
            item = self._session_tree.topLevelItem(index)
            if item is not None and item.data(0, Qt.ItemDataRole.UserRole) == session_id:
                self._session_tree.setCurrentItem(item)
                break

    def _refresh_playback(self, snapshot: OfflineWorkspaceSnapshot) -> None:
        playback = snapshot.playback
        self._frame_spin.blockSignals(True)
        self._frame_spin.setRange(1, max(1, playback.frame_count))
        self._frame_spin.setValue(max(1, min(playback.frame + 1, max(1, playback.frame_count))))
        self._frame_spin.blockSignals(False)
        self._frame_count_label.setText(f"/ {max(1, playback.frame_count)}")
        self._play_button.setText(
            self._tr.text("offline.pause") if playback.playing else self._tr.text("offline.play")
        )
        self._speed_combo.blockSignals(True)
        index = self._speed_combo.findText(playback.speed)
        if index >= 0:
            self._speed_combo.setCurrentIndex(index)
        self._speed_combo.blockSignals(False)
        self._loop_check.blockSignals(True)
        self._loop_check.setChecked(playback.loop)
        self._loop_check.blockSignals(False)
        self._no_skip_check.blockSignals(True)
        self._no_skip_check.setChecked(playback.no_skip)
        self._no_skip_check.blockSignals(False)

    def _refresh_heatmap(self, snapshot: OfflineWorkspaceSnapshot) -> None:
        heatmap = snapshot.heatmap
        self.heatmap_enable_button.setEnabled(not heatmap.enabled)
        self.heatmap_disable_button.setEnabled(heatmap.enabled)
        self.heatmap_recalculate_button.setEnabled(heatmap.enabled)
        self.heatmap_cancel_button.setEnabled(heatmap.can_cancel)
        self.heatmap_clear_button.setEnabled(heatmap.applied)
        parts = [heatmap.mode, heatmap.phase] if heatmap.mode else [heatmap.phase]
        status = heatmap.status or " ".join(parts)
        self._heatmap_status_label.setText(status)
        self._heatmap_status_label.setStyleSheet(
            "color: #c0392b;" if heatmap.error else ""
        )

    def _refresh_markers(self) -> None:
        markers = self._presenter.markers_snapshot()
        self._markers_table.setRowCount(len(markers))
        for row, marker in enumerate(markers):
            values = (
                marker.name,
                marker.marker_type,
                marker.frequency_mhz,
                marker.power_dbm,
                marker.delta_f_mhz,
                marker.delta_l_db,
                marker.timestamp,
                marker.trace_id or "",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._markers_table.setItem(row, column, item)

    def _refresh_results(self) -> None:
        results = self._presenter.results_snapshot()
        self._results_table.setRowCount(len(results))
        for row, result in enumerate(results):
            values = (result.name, result.key, result.value)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, result.result_id)
                self._results_table.setItem(row, column, item)

    def _refresh_properties(self, snapshot: OfflineWorkspaceSnapshot) -> None:
        session = snapshot.active_session_id
        text = self._presenter.metadata_text()
        if session:
            properties = self._presenter.trace_properties_text()
            if properties:
                text = f"{text}\n\n{properties}" if text else properties
        if self._properties_text.toPlainText() != text:
            self._properties_text.setPlainText(text)
        self._refresh_status(snapshot)

    def _refresh_status(self, snapshot: OfflineWorkspaceSnapshot) -> None:
        if snapshot.error:
            self._status_label.setText(snapshot.error)
            self._status_label.setToolTip(snapshot.error)
            return
        parts = []
        if snapshot.status.source_path:
            parts.append(snapshot.status.source_path)
        if snapshot.status.trace_summary:
            parts.append(snapshot.status.trace_summary)
        self._status_label.setText(" | ".join(parts) if parts else self._tr.text("offline.status_no_session"))
        self._status_label.setToolTip("")

    def _refresh_frame_label(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._presenter.close()
        super().closeEvent(event)


__all__ = ["OfflineDflWorkspace"]
