from __future__ import annotations

import logging
import math
import sys
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime
from functools import partial
from pathlib import Path
from threading import Event
from typing import Any, Callable

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QByteArray, QSettings, Qt, QThreadPool, QTimer, Signal, QObject
from PySide6.QtGui import QAction, QCloseEvent, QCursor, QFont, QShowEvent, QValidator
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .adapter import DflMeasurementAdapter
from .activity_log import (
    DEFAULT_MAX_RECORDS,
    default_activity_log_path,
    install_activity_file_logging,
    log_event,
)
from .animation import export_waterfall_animation
from .domain import (
    AnalysisResult,
    FrequencyRegion,
    Marker,
    MarkerType,
    MeasurementSession,
    SpectrumTrace,
    TimeRegion,
    WaterfallData,
)
from .domain_export import (
    export_markers_csv,
    export_results_csv,
    export_session_json,
    export_session_npz,
    export_trace_csv,
    export_traces_csv,
    export_waterfall_region_csv,
    export_time_gated_events_csv,
    export_time_gated_frames_csv,
    export_time_gated_json,
    export_time_gated_summary_csv,
)
from .models import MeasurementQuality, MeasurementWarning, SpectrogramInfo, SpectrogramPreview
from .parser import DflParser
from .processing import acpr, channel_power, noise_floor, occupied_bandwidth, peak_search_values, snr
from .power_measurements import (
    AclrService,
    SingleChannelPowerService,
    SpectrumFrame,
    carrier_to_noise,
    harmonic_powers,
    measure_regions,
    MeasurementRegion,
    MultiChannelDefinition,
    RegionRole,
    SemMaskSegment,
    spectrum_emission_mask,
    spurious_search,
    multi_channel_aclr,
    occupied_bandwidth as power_occupied_bandwidth,
    x_db_bandwidth as power_x_db_bandwidth,
)
from .power_profiles import BUILTIN_POWER_PROFILES, profile_by_name
from .renderers import PyQtGraphSpectrumRenderer, PyQtGraphWaterfallRenderer
from .smoothing import (
    SpectrumSmoothMethod,
    SpectrumSmoothSettings,
    WaterfallSmoothMethod,
    WaterfallSmoothSettings,
)
from .repository import MemoryMeasurementRepository
from .spectrogram import (
    SpectrogramFrameReader,
    SpectrogramIndex,
    SpectrogramRow,
    compute_frame_period_statistics,
    load_spectrogram_preview_with_index,
    read_spectrogram_frame,
    iter_spectrogram_rows,
)
from .frame_navigation import (
    FrameLoadCoordinator,
    FrameNavigationController,
    FramePresentationScheduler,
    FrameSnapshot,
    FrameSpanEvent,
    NavigationConfig,
    NavigationReason,
    ScrollState,
)
from .heatmap import (
    HeatmapCache,
    HeatmapConfig,
    HeatmapNormalization,
    HeatmapRangeMode,
    HeatmapResult,
    HeatmapSamplingPolicy,
    frequency_bin_edges,
    frequency_grid_hash,
)
from .heatmap_export import (
    export_heatmap_csv,
    export_heatmap_json,
    export_heatmap_npz,
    export_heatmap_png,
)
from .heatmap_persistence import (
    ColorScaleMode,
    HeatmapDisplayConfig,
    PersistenceConfig,
    heatmap_render_budget,
    PersistenceMode,
    PersistencePhase,
    PersistenceSnapshot,
    PersistenceSourceKey,
    WindowUnit,
)
from .heatmap_persistence_controller import (
    HeatmapPersistenceController,
    HeatmapPhaseEvent,
    PersistenceSourceContext,
)
from .time_gated_power import (
    ActivityDetectionConfig,
    ActivityThresholdMode,
    ChannelPowerMode,
    ChannelPowerRequest,
    FrameInclusion,
    ManualOverride,
    PowerSemantics,
    SmoothingMode,
    TimeGatedChannelPowerResult,
    TimeGatedChannelPowerService,
)
from .workers import TaskWorker
from .workspace import apply_workspace_session, read_workspace, write_workspace
from .sdr.contracts import (
    DeviceConfig, DspConfig, SpectrumUnit, SweepConfig, SweepSpectrumFrame,
    PersistenceConfig as SdrPersistenceConfig,
    PersistenceMode as SdrPersistenceMode,
)
from .sdr.controller import LiveSdrController, LiveSessionConfig
from .sdr.fixed_band import FixedBandEngineService, FixedBandOptions
from .sdr.session_adapter import LiveSessionAdapter, sweep_trace_from_frame
from .sdr.measurements import LiveMeasurementAdapter, LiveMeasurementResult
from .sdr.sweep import SweepExecutor, SweepPlannerOptions, plan_sweep
from .sdr.stitching import SweepStitchOptions, stitch_sweep


LOGGER = logging.getLogger("esw_dfl")
ROLE_KIND = Qt.ItemDataRole.UserRole
ROLE_SESSION = Qt.ItemDataRole.UserRole + 1
ROLE_OBJECT = Qt.ItemDataRole.UserRole + 2

HOLD_TRACE_MODES = frozenset({"Max Hold", "Average", "Min Hold"})


class ClampedSpinBox(QSpinBox):
    """Allow arbitrary integer entry and clamp it when editing is committed."""

    def validate(self, text: str, position: int) -> tuple[QValidator.State, str, int]:
        stripped = text.strip()
        try:
            value = int(stripped)
        except ValueError:
            state = QValidator.State.Intermediate if stripped in {"", "+", "-"} else QValidator.State.Invalid
            return state, text, position
        state = (
            QValidator.State.Acceptable
            if self.minimum() <= value <= self.maximum()
            else QValidator.State.Intermediate
        )
        return state, text, position

    def fixup(self, text: str) -> str:
        try:
            value = int(text.strip())
        except ValueError:
            value = self.value()
        return str(min(self.maximum(), max(self.minimum(), value)))


def _analyze_time_gated_waterfall(
    service: TimeGatedChannelPowerService,
    source_path: Path,
    info: SpectrogramInfo,
    frequencies_hz: np.ndarray,
    request: ChannelPowerRequest,
    manual_override: np.ndarray,
    index: SpectrogramIndex | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancel: Event | None = None,
) -> TimeGatedChannelPowerResult:
    if (
        request.mode == ChannelPowerMode.CURRENT_FRAME
        and request.selected_frame_index is not None
        and index is not None
    ):
        row = read_spectrogram_frame(source_path, index, request.selected_frame_index)
        series = service.channel_power.build_series((row,), frequencies_hz, request, cancel)
        # ``build_series`` numbers an arbitrary row iterator from zero.  A
        # CURRENT_FRAME random-access read must retain the source frame number;
        # otherwise the GUI cannot associate the result with its 1..N cursor.
        series.frame_indices[:] = request.selected_frame_index
        if request.selected_frame_index < index.timestamps.size:
            series.timestamps_s[:] = index.timestamps[request.selected_frame_index]
        selected_override = (
            manual_override[request.selected_frame_index : request.selected_frame_index + 1]
            if manual_override.size > request.selected_frame_index else None
        )
        activity = service.activity_detection.detect(
            series, request.activity_config or ActivityDetectionConfig(), selected_override
        )
        return service.burst_analysis.summarize(request, series, activity)
    rows = None
    if service.cache.get(request) is None:
        rows = iter_spectrogram_rows(
            source_path, info, progress=progress, cancel=cancel
        )
    return service.analyze(request, frequencies_hz, rows, manual_override, cancel)


def _execute_p13_sweep(
    uri: str,
    base_options: FixedBandOptions,
    sweep_config: SweepConfig,
    planner_options: SweepPlannerOptions,
    stitch_options: SweepStitchOptions,
    progress: Callable[[float, str], None] | None = None,
    cancel: Event | None = None,
) -> SweepSpectrumFrame:
    """Run P12 acquisition and P13 stitching off the Qt main thread."""

    if progress is not None:
        progress(0.0, "P13: планирование полного диапазона…")
    plan = plan_sweep(sweep_config, planner_options)

    def report(event: Any) -> None:
        if progress is not None:
            progress(
                float(event.fraction),
                f"P13: {event.stage}; "
                f"segments {event.completed_segments}/{event.total_segments}",
            )

    with FixedBandEngineService(uri, timeout_ms=3000) as service:
        execution = SweepExecutor(service, base_options).execute(
            plan,
            cancel=cancel,
            progress=report,
        )
    return stitch_sweep(execution, stitch_options)

class _LogEmitter(QObject):
    message = Signal(str)


class QtLogHandler(logging.Handler):
    def __init__(self, emitter: _LogEmitter) -> None:
        super().__init__()
        self.emitter = emitter
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.emitter.message.emit(self.format(record))
        except RuntimeError:
            # The window can be destroyed while a parser/export worker is
            # finishing. Logging during shutdown must never crash that worker.
            return


class ViewSettingsDialog(QDialog):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Масштаб и динамический диапазон")
        self.setModal(False)
        layout = QGridLayout(self)
        x_range = window.spectrum_renderer.plot.viewRange()[0]
        y_range = window.spectrum_renderer.plot.viewRange()[1]
        self.x_min = self._spin(x_range[0], -1e15, 1e15, 3)
        self.x_max = self._spin(x_range[1], -1e15, 1e15, 3)
        self.y_min = self._spin(y_range[0], -500.0, 500.0, 2)
        self.y_max = self._spin(y_range[1], -500.0, 500.0, 2)
        layout.addWidget(QLabel("X min"), 0, 0)
        layout.addWidget(self.x_min, 0, 1)
        layout.addWidget(QLabel("X max"), 1, 0)
        layout.addWidget(self.x_max, 1, 1)
        layout.addWidget(QLabel("Амплитуда min"), 2, 0)
        layout.addWidget(self.y_min, 2, 1)
        layout.addWidget(QLabel("Амплитуда max / Ref"), 3, 0)
        layout.addWidget(self.y_max, 3, 1)
        auto_x = QPushButton("Auto X")
        auto_y = QPushButton("Auto амплитуда")
        auto_all = QPushButton("Auto всё")
        auto_x.clicked.connect(self._auto_x)
        auto_y.clicked.connect(self._auto_y)
        auto_all.clicked.connect(self._auto_all)
        layout.addWidget(auto_x, 4, 0)
        layout.addWidget(auto_y, 4, 1)
        layout.addWidget(auto_all, 5, 0, 1, 2)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons, 6, 0, 1, 2)

    def refresh_from_view(self) -> None:
        x_range, y_range = self.window.spectrum_renderer.plot.viewRange()
        for spin, value in (
            (self.x_min, x_range[0]), (self.x_max, x_range[1]),
            (self.y_min, y_range[0]), (self.y_max, y_range[1]),
        ):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    @staticmethod
    def _spin(value: float, minimum: float, maximum: float, decimals: int) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        return spin

    def _auto_x(self) -> None:
        self.window.spectrum_renderer.plot.enableAutoRange(axis="x")
        x_range = self.window.spectrum_renderer.plot.viewRange()[0]
        self.x_min.setValue(x_range[0])
        self.x_max.setValue(x_range[1])
        self.window._audit("user", "view_auto_x", x_min=x_range[0], x_max=x_range[1])

    def _auto_y(self) -> None:
        self.window.spectrum_renderer.plot.enableAutoRange(axis="y")
        y_range = self.window.spectrum_renderer.plot.viewRange()[1]
        self.y_min.setValue(y_range[0])
        self.y_max.setValue(y_range[1])
        self.window._audit("user", "view_auto_y", y_min=y_range[0], y_max=y_range[1])

    def _auto_all(self) -> None:
        self.window._auto_scale()
        self._auto_x()
        self._auto_y()

    def apply(self) -> None:
        if self.x_min.value() < self.x_max.value():
            self.window.spectrum_renderer.plot.setXRange(
                self.x_min.value(), self.x_max.value(), padding=0
            )
        if self.y_min.value() < self.y_max.value():
            self.window.spectrum_renderer.plot.setYRange(
                self.y_min.value(), self.y_max.value(), padding=0
            )
            self.window.level_min.setValue(self.y_min.value())
            self.window.level_max.setValue(self.y_max.value())
        self.window._audit(
            "user",
            "view_range_applied",
            x_min=self.x_min.value(),
            x_max=self.x_max.value(),
            y_min=self.y_min.value(),
            y_max=self.y_max.value(),
        )


class FrameNavigationSettingsDialog(QDialog):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Настройки навигации по кадрам")
        self.setModal(False)
        layout = QFormLayout(self)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Быстрый переход", "Последовательный"])
        layout.addRow("Режим прокрутки", self.mode_combo)

        self.wheel_step_spin = QSpinBox()
        self.wheel_step_spin.setRange(1, 10_000)
        layout.addRow("Шаг колеса, кадров", self.wheel_step_spin)

        self.touchpad_threshold_spin = QDoubleSpinBox()
        self.touchpad_threshold_spin.setRange(1.0, 1000.0)
        self.touchpad_threshold_spin.setDecimals(1)
        self.touchpad_threshold_spin.setSuffix(" px")
        layout.addRow("Порог тачпада", self.touchpad_threshold_spin)

        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["30", "60", "120", "144", "240"])
        layout.addRow("Частота UI, FPS", self.fps_combo)

        self.settle_delay_spin = QSpinBox()
        self.settle_delay_spin.setRange(0, 2000)
        self.settle_delay_spin.setSuffix(" мс")
        layout.addRow("Задержка завершения жеста", self.settle_delay_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.close)
        layout.addRow(buttons)

        self.refresh_from_window()

    def refresh_from_window(self) -> None:
        cfg = self.window._frame_nav.config
        self.mode_combo.setCurrentIndex(1 if cfg.sequential_mode else 0)
        self.wheel_step_spin.setValue(cfg.wheel_step)
        self.touchpad_threshold_spin.setValue(cfg.touchpad_threshold)
        self.fps_combo.setCurrentText(str(cfg.fps))
        self.settle_delay_spin.setValue(cfg.settle_delay_ms)

    def apply(self) -> None:
        cfg = self.window._frame_nav.config
        sequential = self.mode_combo.currentIndex() == 1
        cfg.sequential_mode = sequential
        self.window.no_skip_check.setChecked(sequential)
        cfg.wheel_step = self.wheel_step_spin.value()
        cfg.touchpad_threshold = self.touchpad_threshold_spin.value()
        cfg.fps = int(self.fps_combo.currentText())
        cfg.settle_delay_ms = self.settle_delay_spin.value()
        self.window._frame_scheduler.set_fps(cfg.fps)
        self.window._frame_scheduler.set_settle_delay_ms(cfg.settle_delay_ms)
        self.window.fps_combo.setCurrentText(str(cfg.fps))
        self.window._set_wheel_step(cfg.wheel_step)
        self.window.settings.setValue("frame_navigation/sequential_mode", sequential)
        self.window.settings.setValue("frame_navigation/wheel_step", cfg.wheel_step)
        self.window.settings.setValue(
            "frame_navigation/touchpad_threshold", cfg.touchpad_threshold
        )
        self.window.settings.setValue("frame_navigation/fps", cfg.fps)
        self.window.settings.setValue("frame_navigation/settle_delay_ms", cfg.settle_delay_ms)
        self.window._audit(
            "user",
            "frame_navigation_settings_applied",
            sequential_mode=sequential,
            wheel_step=cfg.wheel_step,
            touchpad_threshold=cfg.touchpad_threshold,
            fps=cfg.fps,
            settle_delay_ms=cfg.settle_delay_ms,
        )


HEATMAP_LIVE_MODES = frozenset(
    {PersistenceMode.ROLLING_EXACT, PersistenceMode.EXPONENTIAL_DECAY}
)

# Bump whenever the dock layout changes: saved windowState blobs from other
# layout versions are ignored, otherwise Qt misplaces docks it does not know
# (overlapping/off-screen panels after an upgrade).
WINDOW_STATE_VERSION = 3


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle("R&S DFL parcer")
        self.resize(1500, 920)
        self.setDockNestingEnabled(True)
        self.setUnifiedTitleAndToolBarOnMac(True)
        self.settings = QSettings("RohdeSchwarzTools", "R&S DFL parcer")
        self.repository = MemoryMeasurementRepository()
        self.adapter = DflMeasurementAdapter()
        self.thread_pool = QThreadPool.globalInstance()
        self.active_session_id: str | None = None
        self._workers: set[TaskWorker] = set()
        self._workspace_payloads: dict[str, dict[str, Any]] = {}
        self._tree_updating = False
        self._shown_once = False
        self._syncing_range = False
        self._current_workspace: Path | None = None
        self._spectrogram_indexes: dict[tuple[str, str], SpectrogramIndex] = {}
        self._frame_readers: dict[tuple[str, str], SpectrogramFrameReader] = {}
        self._live_controllers: dict[str, LiveSdrController] = {}
        self._live_adapters: dict[str, LiveSessionAdapter] = {}
        self._live_refresh_counter = 0
        self._last_sweep_trace: SpectrumTrace | None = None
        self._p13_sweep_worker: TaskWorker | None = None
        self._view_settings_dialog: ViewSettingsDialog | None = None
        self._frame_navigation_settings_dialog: FrameNavigationSettingsDialog | None = None
        self._navigation_connected = False
        self.time_gated_service = TimeGatedChannelPowerService()
        self._channel_power_results: dict[tuple[str, str], TimeGatedChannelPowerResult] = {}
        self._activity_overrides: dict[tuple[str, str], np.ndarray] = {}
        self._channel_power_serial = 0
        self._power_measurement_serial = 0
        self._channel_power_worker: TaskWorker | None = None
        self._channel_power_session_id: str | None = None
        self._pending_channel_power_request: tuple[
            MeasurementSession, WaterfallData, SpectrogramIndex,
            ChannelPowerRequest, np.ndarray, int
        ] | None = None
        self._channel_plot_regions: list[Any] = []
        self._syncing_channel_frequency = False
        self._syncing_time_region = False
        self._manual_noise_ranges: dict[tuple[str, str], tuple[float, float]] = {}
        self._channel_time_origin = 0.0
        self._last_progress_bucket = -1

        # Heatmap persistence orchestration lives in the controller (P2/P3);
        # MainWindow keeps widgets, config assembly, status, renderer apply,
        # export entry points and settings. The fixed-result LRU cache is
        # owned by the controller (see the _heatmap_cache alias below).
        self._heatmap_controller = HeatmapPersistenceController(
            thread_pool=self.thread_pool,
            audit=lambda event, **details: self._audit("heatmap", event, **details),
            parent=self,
        )
        self._heatmap_applied_snapshot: PersistenceSnapshot | None = None
        # Compatibility aliases for export/tests (one transition package):
        # synthesized from _heatmap_applied_snapshot at apply time.
        self._heatmap_applied: HeatmapResult | None = None
        self._heatmap_applied_key: tuple[str, str, str] | None = None
        self._heatmap_applied_range: tuple[int, int] | None = None
        self._heatmap_last_context_identity: tuple[str, str] | None = None
        self._heatmap_last_apply_at = 0.0
        self._heatmap_current_levels: tuple[float, float] | None = None
        self._heatmap_restoring = False
        self._heatmap_navigation_connected = False
        self._heatmap_window_minimum_frames = 1
        self._heatmap_window_minimum_seconds: float | None = None
        self._heatmap_render_budget_signature: tuple[object, ...] | None = None
        self._heatmap_render_intervals: deque[float] = deque(maxlen=200)
        self._heatmap_controller.snapshot_ready.connect(self._apply_persistence_snapshot)
        self._heatmap_controller.phase_changed.connect(self._apply_heatmap_phase)
        self._heatmap_controller.failed.connect(self._heatmap_controller_failed)

        self.spectrum_renderer = PyQtGraphSpectrumRenderer()
        self.waterfall_renderer = PyQtGraphWaterfallRenderer()
        self.playback_timer = QTimer(self)
        self.playback_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.playback_timer.timeout.connect(self._advance_frame)
        self._playback_start_frame: int = 0
        self._playback_start_time: float | None = None

        self._frame_config = NavigationConfig()
        self._frame_nav = FrameNavigationController(self._frame_config, parent=self)
        self._frame_loader = FrameLoadCoordinator(
            self._frame_loader_context, max_cache=256, parent=self
        )
        self._frame_scheduler = FramePresentationScheduler(
            self._frame_nav, self._frame_loader, self._frame_config, parent=self
        )
        self._frame_scheduler.apply_snapshot.connect(self._apply_frame_snapshot)
        self._frame_scheduler.settled.connect(self._on_frame_settled)
        self._frame_loader.error.connect(self._show_frame_load_error)
        self._live_timer = QTimer(self)
        self._live_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._live_timer.setInterval(33)
        self._live_timer.timeout.connect(self._poll_live_updates)
        self._live_timer.start()

        self._create_central_area()
        self._create_docks()
        self._create_actions()
        self._create_menus_and_toolbar()
        self._create_status_bar()
        self._connect_plot_ranges()
        self._connect_navigation()
        self._connect_heatmap_navigation()
        self.fps_combo.currentTextChanged.connect(self._refresh_heatmap_render_budget)
        self.speed_combo.currentTextChanged.connect(self._refresh_heatmap_render_budget)
        self.no_skip_check.toggled.connect(self._refresh_heatmap_render_budget)
        self._refresh_heatmap_render_budget()
        self.wheel_step_spin.valueChanged.connect(self._set_wheel_step)
        self._frame_nav.config.wheel_step = self.wheel_step_spin.value()
        self.fps_combo.currentTextChanged.connect(
            lambda text: self._frame_scheduler.set_fps(int(text))
        )
        self._frame_scheduler.set_fps(int(self.fps_combo.currentText()))
        self._install_logging()
        self._restore_settings()
        LOGGER.info("R&S DFL parcer запущен; Qt %s", QApplication.instance().style().objectName())
        self._audit(
            "program",
            "main_window_ready",
            qt_style=QApplication.instance().style().objectName(),
            activity_log_path=str(default_activity_log_path()),
            activity_log_limit=DEFAULT_MAX_RECORDS,
        )

    # --- UI construction -------------------------------------------------
    def _create_central_area(self) -> None:
        self.central_splitter = QSplitter(Qt.Orientation.Vertical)
        self.central_splitter.setObjectName("spectrumWaterfallSplitter")
        self.central_splitter.addWidget(self.spectrum_renderer.widget)
        self.central_splitter.addWidget(self.waterfall_renderer.widget)
        self.central_splitter.setSizes([430, 390])
        self.setCentralWidget(self.central_splitter)

    def _dock(self, title: str, object_name: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(object_name)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        dock.visibilityChanged.connect(
            lambda visible, name=object_name: self._audit(
                "user", "dock_visibility_changed", dock=name, visible=visible
            )
        )
        dock.topLevelChanged.connect(
            lambda floating, name=object_name: self._audit(
                "user", "dock_floating_changed", dock=name, floating=floating
            )
        )
        return dock

    @staticmethod
    def _exec_context_menu(menu: QMenu) -> QAction | None:
        return menu.exec(QCursor.pos())

    def _create_docks(self) -> None:
        self.trace_tree = QTreeWidget()
        self.trace_tree.setHeaderLabels(["Файлы и трассы", "Тип"])
        self.trace_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.trace_tree.itemSelectionChanged.connect(self._tree_selection_changed)
        self.trace_tree.itemChanged.connect(self._tree_item_changed)
        self.trace_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.trace_tree.customContextMenuRequested.connect(self._session_context_menu)
        self.files_dock = self._dock(
            "Файлы и трассы", "filesTracesDock", self.trace_tree, Qt.DockWidgetArea.LeftDockWidgetArea
        )

        self.marker_table = QTableWidget(0, 10)
        self.marker_table.setHorizontalHeaderLabels(
            ["№", "Имя", "Тип", "Частота", "Уровень", "Δf", "ΔL", "Время", "Трасса", "Вкл."]
        )
        self.marker_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.marker_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.marker_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.marker_table.customContextMenuRequested.connect(self._marker_context_menu)
        marker_widget = QWidget()
        marker_layout = QVBoxLayout(marker_widget)
        marker_buttons = QHBoxLayout()
        for text, slot in (
            ("Добавить", self.add_marker), ("Пик", self.add_peak_marker),
            ("Delta", self.add_delta_marker), ("Удалить", self.remove_selected_marker),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            marker_buttons.addWidget(button)
        marker_layout.addLayout(marker_buttons)
        marker_layout.addWidget(self.marker_table)
        self.markers_dock = self._dock(
            "Маркеры", "markersDock", marker_widget, Qt.DockWidgetArea.RightDockWidgetArea
        )

        measurements = QWidget()
        measurements_layout = QVBoxLayout(measurements)
        form = QFormLayout()
        self.band_start = self._frequency_spin()
        self.band_stop = self._frequency_spin()
        self.acpr_offset = self._frequency_spin(1.0)
        self.acpr_width = self._frequency_spin(1.0)
        form.addRow("Начало, MHz", self.band_start)
        form.addRow("Конец, MHz", self.band_stop)
        form.addRow("ACPR offset, MHz", self.acpr_offset)
        form.addRow("ACPR полоса, MHz", self.acpr_width)
        measurements_layout.addLayout(form)
        for text, slot in (
            ("Мощность в полосе", self.measure_channel_power),
            ("Occupied Bandwidth 99%", self.measure_obw),
            ("Noise Floor", self.measure_noise),
            ("SNR", self.measure_snr),
            ("ACPR / ACLR", self.measure_acpr),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            measurements_layout.addWidget(button)
        self.measurement_results = QTableWidget(0, 3)
        self.measurement_results.setHorizontalHeaderLabels(["Расчёт", "Параметр", "Значение"])
        self.measurement_results.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.measurement_results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.measurement_results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.measurement_results.customContextMenuRequested.connect(
            self._measurement_context_menu
        )
        measurements.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        measurements.customContextMenuRequested.connect(
            lambda position: self._measurement_context_menu(
                self.measurement_results.viewport().mapFromGlobal(
                    measurements.mapToGlobal(position)
                )
            )
        )
        measurements_layout.addWidget(self.measurement_results)
        # Scroll area keeps the form + buttons + table reachable when the
        # tabbed dock is squeezed (otherwise this dock alone forced ~400 px
        # of minimum height and pushed the window past small work areas).
        measurements_scroll = QScrollArea()
        measurements_scroll.setWidgetResizable(True)
        measurements_scroll.setWidget(measurements)
        self.measurements_dock = self._dock(
            "Измерения", "measurementsDock", measurements_scroll, Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.tabifyDockWidget(self.markers_dock, self.measurements_dock)

        self.properties_text = QTextEdit()
        self.properties_text.setReadOnly(True)
        self.properties_dock = self._dock(
            "Свойства трассы", "propertiesDock", self.properties_text, Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.tabifyDockWidget(self.files_dock, self.properties_dock)

        display = QWidget()
        display_form = QFormLayout(display)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Тёмная", "Светлая"])
        self.theme_combo.currentTextChanged.connect(self._apply_theme)
        self.grid_check = QCheckBox("Показывать сетку")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self._toggle_grid)
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["15", "30", "60", "120", "144", "240"])
        self.fps_combo.setCurrentText("60")
        self.fps_combo.currentTextChanged.connect(
            lambda value: self._audit("user", "display_fps_changed", fps=int(value))
        )
        self.fps_combo.currentTextChanged.connect(self._update_playback_interval)
        self.spectrum_smooth_combo = QComboBox()
        for label, method in (
            ("PCHIP", "pchip"),
            ("Makima", "makima"),
        ):
            self.spectrum_smooth_combo.addItem(label, method)
        self.spectrum_smooth_combo.currentIndexChanged.connect(self._apply_smoothing_settings)
        self.spectrum_smooth_enable = QCheckBox("Включить")
        self.spectrum_smooth_enable.setChecked(False)
        self.spectrum_smooth_enable.toggled.connect(self._apply_smoothing_settings)
        self.spectrum_smooth_enable.toggled.connect(self.spectrum_smooth_combo.setEnabled)
        self.spectrum_smooth_combo.setEnabled(False)
        self.spectrum_smooth_auto = QCheckBox("Только при приближении")
        self.spectrum_smooth_auto.setChecked(True)
        self.spectrum_smooth_auto.toggled.connect(self._apply_smoothing_settings)
        self.waterfall_smooth_combo = QComboBox()
        for label, method in (
            ("Nearest (научный)", "nearest"),
            ("Bilinear (красивый)", "bilinear"),
        ):
            self.waterfall_smooth_combo.addItem(label, method)
        self.waterfall_smooth_combo.currentIndexChanged.connect(self._apply_smoothing_settings)
        self.waterfall_smooth_auto = QCheckBox("Авто по масштабу")
        self.waterfall_smooth_auto.setChecked(True)
        self.waterfall_smooth_auto.toggled.connect(self._apply_smoothing_settings)
        display_form.addRow("Тема", self.theme_combo)
        display_form.addRow(self.grid_check)
        display_form.addRow("Частота UI, FPS", self.fps_combo)
        display_form.addRow("Интерполяция спектра", self.spectrum_smooth_combo)
        display_form.addRow(self.spectrum_smooth_enable)
        display_form.addRow(self.spectrum_smooth_auto)
        display_scroll = QScrollArea()
        display_scroll.setWidgetResizable(True)
        display_scroll.setWidget(display)
        self.display_dock = self._dock(
            "Отображение", "displayDock", display_scroll, Qt.DockWidgetArea.RightDockWidgetArea
        )

        waterfall_settings = QWidget()
        waterfall_form = QFormLayout(waterfall_settings)
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(["Turbo", "Viridis", "Plasma", "Inferno", "Magma", "Grayscale", "Jet"])
        self.colormap_combo.currentTextChanged.connect(self._set_colormap)
        self.level_min = QDoubleSpinBox()
        self.level_max = QDoubleSpinBox()
        for spin, value in ((self.level_min, -120.0), (self.level_max, -20.0)):
            spin.setRange(-300.0, 300.0)
            spin.setDecimals(2)
            spin.setValue(value)
            spin.valueChanged.connect(self._set_levels)
        auto_level = QPushButton("Auto Level")
        auto_level.clicked.connect(self._auto_levels)
        waterfall_form.addRow("Палитра", self.colormap_combo)
        waterfall_form.addRow("Min, dBm", self.level_min)
        waterfall_form.addRow("Max, dBm", self.level_max)
        waterfall_form.addRow(auto_level)
        waterfall_form.addRow("Интерполяция водопада", self.waterfall_smooth_combo)
        waterfall_form.addRow(self.waterfall_smooth_auto)
        waterfall_scroll = QScrollArea()
        waterfall_scroll.setWidgetResizable(True)
        waterfall_scroll.setWidget(waterfall_settings)
        self.waterfall_settings_dock = self._dock(
            "Настройки waterfall", "waterfallSettingsDock", waterfall_scroll,
            Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self.tabifyDockWidget(self.display_dock, self.waterfall_settings_dock)

        playback = QWidget()
        playback_layout = QVBoxLayout(playback)
        controls = QHBoxLayout()
        for text, slot in (
            ("|<", self.first_frame), ("<", self.previous_frame), ("▶", self.play),
            ("Ⅱ", self.pause), ("■", self.stop), (">", self.next_frame), (">|", self.last_frame),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            controls.addWidget(button)
        playback_layout.addLayout(controls)
        self.time_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_slider.setRange(0, 0)
        self.time_slider.valueChanged.connect(self._show_frame)
        playback_layout.addWidget(self.time_slider)
        frame_row = QHBoxLayout()
        self.frame_spin = ClampedSpinBox()
        self.frame_spin.setRange(1, 1)
        self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.valueChanged.connect(lambda value: self.time_slider.setValue(value - 1))
        self.frame_total_label = QLabel("из 1")
        self.wheel_step_spin = QSpinBox()
        self.wheel_step_spin.setRange(1, 10000)
        self.wheel_step_spin.setValue(1)
        self.wheel_step_spin.valueChanged.connect(
            lambda value: self._audit("user", "waterfall_wheel_step_changed", step=value)
        )
        frame_row.addWidget(QLabel("Кадр"))
        frame_row.addWidget(self.frame_spin)
        frame_row.addWidget(self.frame_total_label)
        frame_row.addStretch(1)
        frame_row.addWidget(QLabel("Шаг колеса"))
        frame_row.addWidget(self.wheel_step_spin)
        playback_layout.addLayout(frame_row)
        self.current_frame_measurement = QLabel("Текущий Channel Power: —")
        playback_layout.addWidget(self.current_frame_measurement)
        options = QHBoxLayout()
        self.speed_combo = QComboBox()
        self.speed_combo.addItems([
            "0.0001×", "0.001×", "0.01×", "0.1×", "0.25×", "0.5×",
            "1×", "2×", "5×", "10×",
        ])
        self.speed_combo.setCurrentText("1×")
        self.speed_combo.currentTextChanged.connect(self._update_playback_interval)
        self.no_skip_check = QCheckBox("Без пропуска кадров")
        self.no_skip_check.toggled.connect(self._update_playback_interval)
        self.loop_check = QCheckBox("Цикл")
        self.loop_check.toggled.connect(
            lambda enabled: self._audit("user", "playback_loop_changed", enabled=enabled)
        )
        options.addWidget(QLabel("Скорость"))
        options.addWidget(self.speed_combo)
        options.addWidget(self.no_skip_check)
        options.addWidget(self.loop_check)
        playback_layout.addLayout(options)
        animation_buttons = QHBoxLayout()
        gif_button = QPushButton("Экспорт GIF")
        gif_button.clicked.connect(lambda: self.export_animation("gif"))
        mp4_button = QPushButton("Экспорт MP4")
        mp4_button.clicked.connect(lambda: self.export_animation("mp4"))
        animation_buttons.addWidget(gif_button)
        animation_buttons.addWidget(mp4_button)
        playback_layout.addLayout(animation_buttons)
        self.playback_dock = self._dock(
            "Воспроизведение", "playbackDock", playback, Qt.DockWidgetArea.BottomDockWidgetArea
        )

        self.events_table = QTableWidget(0, 11)
        self.events_table.setHorizontalHeaderLabels(
            [
                "Event ID", "Начало", "Конец", "Длительность", "Среднее",
                "Максимум", "Минимум", "Активные кадры", "Duty Cycle",
                "Ручная правка", "Статус",
            ]
        )
        self.events_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.events_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.events_table.customContextMenuRequested.connect(self._event_context_menu)
        self.events_table.cellDoubleClicked.connect(self._event_double_clicked)
        self.events_dock = self._dock(
            "События", "eventsDock", self.events_table, Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.tabifyDockWidget(self.playback_dock, self.events_dock)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(DEFAULT_MAX_RECORDS)
        self.log_dock = self._dock("Журнал", "logDock", self.log_text, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.tabifyDockWidget(self.events_dock, self.log_dock)

        self.metadata_text = QTextEdit()
        self.metadata_text.setReadOnly(True)
        self.metadata_dock = self._dock(
            "Метаданные", "metadataDock", self.metadata_text, Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self.tabifyDockWidget(self.properties_dock, self.metadata_dock)
        self._create_channel_power_docks()
        self._create_heatmap_dock()
        self._create_live_dock()
        self.live_dock.hide()
        self.files_dock.raise_()

    def _create_live_dock(self) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)

        device = QGroupBox("Device / Receive")
        device_form = QFormLayout(device)
        self.live_uri_edit = QLineEdit("usb:")
        self.live_source_id_edit = QLineEdit("pluto-live")
        self.live_center_spin = QDoubleSpinBox()
        self.live_center_spin.setRange(1.0, 6000.0)
        self.live_center_spin.setDecimals(6)
        self.live_center_spin.setValue(2400.0)
        self.live_sample_rate_spin = QDoubleSpinBox()
        self.live_sample_rate_spin.setRange(0.001, 1000.0)
        self.live_sample_rate_spin.setDecimals(6)
        self.live_sample_rate_spin.setValue(3.0)
        self.live_bandwidth_spin = QDoubleSpinBox()
        self.live_bandwidth_spin.setRange(0.001, 1000.0)
        self.live_bandwidth_spin.setDecimals(6)
        self.live_bandwidth_spin.setValue(1.5)
        self.live_gain_spin = QDoubleSpinBox()
        self.live_gain_spin.setRange(-100.0, 100.0)
        self.live_gain_spin.setDecimals(2)
        self.live_gain_spin.setValue(20.0)
        device_form.addRow("URI", self.live_uri_edit)
        device_form.addRow("Source ID", self.live_source_id_edit)
        device_form.addRow("Center, MHz", self.live_center_spin)
        device_form.addRow("Sample rate, MHz", self.live_sample_rate_spin)
        device_form.addRow("Analog BW, MHz", self.live_bandwidth_spin)
        device_form.addRow("Manual gain, dB", self.live_gain_spin)
        layout.addWidget(device)

        dsp = QGroupBox("DSP / Persistence")
        dsp_form = QFormLayout(dsp)
        self.live_fft_spin = QSpinBox()
        self.live_fft_spin.setRange(256, 262144)
        self.live_fft_spin.setSingleStep(256)
        self.live_fft_spin.setValue(4096)
        self.live_persistence_check = QCheckBox("Native persistence requested")
        self.live_persistence_check.setChecked(True)
        self.live_persistence_window = QSpinBox()
        self.live_persistence_window.setRange(2, 100000)
        self.live_persistence_window.setValue(500)
        self.live_persistence_bins = QSpinBox()
        self.live_persistence_bins.setRange(2, 2048)
        self.live_persistence_bins.setValue(256)
        dsp_form.addRow("FFT", self.live_fft_spin)
        dsp_form.addRow("Hop", QLabel("50% (fixed-band baseline)"))
        dsp_form.addRow(self.live_persistence_check)
        dsp_form.addRow("Persistence frames", self.live_persistence_window)
        dsp_form.addRow("Persistence bins", self.live_persistence_bins)
        layout.addWidget(dsp)

        calibration = QGroupBox("Calibration")
        calibration_form = QFormLayout(calibration)
        self.live_calibration_label = QLabel("Uncalibrated (dBFS)")
        self.live_calibration_label.setWordWrap(True)
        calibration_form.addRow("Status", self.live_calibration_label)
        layout.addWidget(calibration)

        sweep_quality = QGroupBox("P13 Full-span sweep")
        sweep_quality_form = QFormLayout(sweep_quality)
        self.live_sweep_start_spin = QDoubleSpinBox()
        self.live_sweep_start_spin.setRange(1.0, 6000.0)
        self.live_sweep_start_spin.setDecimals(6)
        self.live_sweep_start_spin.setValue(2300.0)
        self.live_sweep_stop_spin = QDoubleSpinBox()
        self.live_sweep_stop_spin.setRange(1.0, 6000.0)
        self.live_sweep_stop_spin.setDecimals(6)
        self.live_sweep_stop_spin.setValue(2500.0)
        self.live_sweep_overlap_spin = QDoubleSpinBox()
        self.live_sweep_overlap_spin.setRange(0.001, 1000.0)
        self.live_sweep_overlap_spin.setDecimals(6)
        self.live_sweep_overlap_spin.setValue(0.2)
        sweep_quality_form.addRow("Start, MHz", self.live_sweep_start_spin)
        sweep_quality_form.addRow("Stop, MHz", self.live_sweep_stop_spin)
        sweep_quality_form.addRow("Overlap, MHz", self.live_sweep_overlap_spin)
        self.live_sweep_quality_label = QLabel("No stitched sweep frame")
        self.live_sweep_quality_label.setWordWrap(True)
        self.live_sweep_seams_label = QLabel("Seams: —")
        self.live_sweep_seams_label.setWordWrap(True)
        sweep_quality_form.addRow("Quality", self.live_sweep_quality_label)
        sweep_quality_form.addRow("Seams", self.live_sweep_seams_label)
        self.live_sweep_progress_label = QLabel("Sweep is stopped")
        self.live_sweep_progress_label.setWordWrap(True)
        sweep_quality_form.addRow("Progress", self.live_sweep_progress_label)
        sweep_buttons = QHBoxLayout()
        self.live_sweep_start_button = QPushButton("Start P13 sweep")
        self.live_sweep_start_button.clicked.connect(self.start_p13_sweep)
        self.live_sweep_cancel_button = QPushButton("Cancel P13 sweep")
        self.live_sweep_cancel_button.clicked.connect(self.cancel_p13_sweep)
        self.live_sweep_cancel_button.setEnabled(False)
        sweep_buttons.addWidget(self.live_sweep_start_button)
        sweep_buttons.addWidget(self.live_sweep_cancel_button)
        sweep_quality_form.addRow(sweep_buttons)
        layout.addWidget(sweep_quality)

        diagnostics = QGroupBox("Diagnostics")
        diagnostics_form = QFormLayout(diagnostics)
        self.live_requested_label = QLabel("—")
        self.live_applied_label = QLabel("—")
        self.live_diagnostics_label = QLabel("Not connected")
        self.live_diagnostics_label.setWordWrap(True)
        diagnostics_form.addRow("Requested", self.live_requested_label)
        diagnostics_form.addRow("Applied", self.live_applied_label)
        diagnostics_form.addRow("Metrics", self.live_diagnostics_label)
        layout.addWidget(diagnostics)

        buttons = QHBoxLayout()
        self.live_start_button = QPushButton("Start live")
        self.live_start_button.clicked.connect(self.open_live_sdr)
        self.live_stop_button = QPushButton("Stop live")
        self.live_stop_button.clicked.connect(self.stop_active_live_session)
        buttons.addWidget(self.live_start_button)
        buttons.addWidget(self.live_stop_button)
        layout.addLayout(buttons)
        self.live_status_label = QLabel("Live source is stopped")
        self.live_status_label.setWordWrap(True)
        layout.addWidget(self.live_status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.live_dock = self._dock(
            "Live SDR", "liveSdrDock", scroll, Qt.DockWidgetArea.LeftDockWidgetArea
        )

    def _live_options_from_panel(self) -> LiveSessionConfig:
        source_id = self.live_source_id_edit.text().strip() or "pluto-live"
        uri = self.live_uri_edit.text().strip()
        fft_size = int(self.live_fft_spin.value())
        if fft_size & (fft_size - 1):
            raise ValueError("FFT must be a power of two")
        device = DeviceConfig(
            source_id=source_id,
            context_uri=uri,
            center_frequency_hz=self.live_center_spin.value() * 1e6,
            sample_rate_hz=self.live_sample_rate_spin.value() * 1e6,
            analog_bandwidth_hz=self.live_bandwidth_spin.value() * 1e6,
            manual_gain_db=self.live_gain_spin.value(),
            buffer_samples=262144,
        )
        dsp = DspConfig(fft_size=fft_size, hop_size=fft_size // 2, unit=SpectrumUnit.DBFS_BIN)
        persistence_enabled = self.live_persistence_check.isChecked()
        persistence = SdrPersistenceConfig(
            enabled=persistence_enabled,
            mode=(SdrPersistenceMode.ROLLING_EXACT if persistence_enabled
                  else SdrPersistenceMode.DISABLED),
            window_frames=int(self.live_persistence_window.value()),
            power_min_db=-140.0,
            power_max_db=20.0,
            power_bins=int(self.live_persistence_bins.value()),
            snapshot_rate_hz=30.0,
        )
        options = FixedBandOptions(device=device, dsp=dsp, persistence=persistence)
        self.live_requested_label.setText(
            f"{device.center_frequency_hz / 1e6:.6f} MHz; "
            f"SR {device.sample_rate_hz / 1e6:.6f} MHz; FFT {dsp.fft_size}"
        )
        return LiveSessionConfig(source_id, f"Live SDR — {source_id}", uri, options)

    def open_live_sdr(self) -> None:
        try:
            config = self._live_options_from_panel()
        except (TypeError, ValueError) as exc:
            self._show_error("Live SDR", str(exc))
            return
        if config.source_id in self._live_controllers:
            self.set_active_session(self._live_adapters[config.source_id].session_id)
            self.live_status_label.setText("Эта live-сессия уже запущена")
            return
        adapter = LiveSessionAdapter(
            source_id=config.source_id, display_name=config.display_name,
            uri=config.uri, max_waterfall_rows=512,
        )
        session = adapter.create_session()
        controller = LiveSdrController(config)
        self.repository.add(session)
        self._live_adapters[config.source_id] = adapter
        self._live_controllers[config.source_id] = controller
        self._refresh_tree()
        self.set_active_session(session.session_id)
        controller.start()
        self.live_dock.show()
        self.live_status_label.setText(f"Запуск {config.display_name}…")

    def _p13_sweep_request(self) -> tuple[str, FixedBandOptions, SweepConfig, SweepPlannerOptions]:
        source_id = self.live_source_id_edit.text().strip() or "pluto-live"
        uri = self.live_uri_edit.text().strip()
        if not uri:
            raise ValueError("URI is required for P13 sweep")
        start_hz = self.live_sweep_start_spin.value() * 1.0e6
        stop_hz = self.live_sweep_stop_spin.value() * 1.0e6
        if stop_hz <= start_hz:
            raise ValueError("P13 stop frequency must exceed start frequency")
        sample_rate_hz = self.live_sample_rate_spin.value() * 1.0e6
        bandwidth_hz = self.live_bandwidth_spin.value() * 1.0e6
        overlap_hz = self.live_sweep_overlap_spin.value() * 1.0e6
        fft_size = int(self.live_fft_spin.value())
        config = SweepConfig(
            start_frequency_hz=start_hz,
            stop_frequency_hz=stop_hz,
            sample_rate_hz=sample_rate_hz,
            analog_bandwidth_hz=bandwidth_hz,
            overlap_hz=overlap_hz,
            fft_size=fft_size,
            hop_size=fft_size // 2,
            dwell_frames=1,
            settling_time_seconds=0.0,
            discard_blocks=2,
        )
        device = DeviceConfig(
            source_id=source_id,
            context_uri=uri,
            center_frequency_hz=(start_hz + stop_hz) / 2.0,
            sample_rate_hz=sample_rate_hz,
            analog_bandwidth_hz=bandwidth_hz,
            manual_gain_db=self.live_gain_spin.value(),
            buffer_samples=262144,
        )
        dsp = DspConfig(
            fft_size=fft_size,
            hop_size=fft_size // 2,
            unit=SpectrumUnit.DBFS_BIN,
        )
        return (
            uri,
            FixedBandOptions(device=device, dsp=dsp),
            config,
            SweepPlannerOptions(),
        )

    def start_p13_sweep(self) -> None:
        if self._p13_sweep_worker is not None:
            return
        try:
            uri, base_options, config, planner_options = self._p13_sweep_request()
        except (TypeError, ValueError) as exc:
            self._show_error("P13 sweep", str(exc))
            return
        worker = TaskWorker(
            _execute_p13_sweep,
            uri,
            base_options,
            config,
            planner_options,
            SweepStitchOptions(),
            pass_progress=True,
            pass_cancel=True,
        )
        worker.signals.progress.connect(
            lambda _fraction, message: self.live_sweep_progress_label.setText(message)
        )
        worker.signals.result.connect(self.show_sweep_frame)
        worker.signals.finished.connect(
            lambda worker=worker: self._p13_sweep_finished(worker)
        )
        self._p13_sweep_worker = worker
        self.live_sweep_start_button.setEnabled(False)
        self.live_sweep_cancel_button.setEnabled(True)
        self.live_sweep_progress_label.setText("P13: starting…")
        self._start_worker(worker)

    def cancel_p13_sweep(self) -> None:
        if self._p13_sweep_worker is not None:
            self._p13_sweep_worker.cancel()
            self.live_sweep_progress_label.setText("P13: cancellation requested…")

    def _p13_sweep_finished(self, worker: TaskWorker) -> None:
        if self._p13_sweep_worker is worker:
            self._p13_sweep_worker = None
            self.live_sweep_start_button.setEnabled(True)
            self.live_sweep_cancel_button.setEnabled(False)
            if self.live_sweep_progress_label.text() == "P13: starting…":
                self.live_sweep_progress_label.setText("P13: finished")
    def stop_active_live_session(self) -> None:
        session = self.active_session()
        descriptor = session.source_descriptor if session is not None else None
        controller = self._live_controllers.get(descriptor.source_id) if descriptor else None
        if controller is not None:
            controller.request_stop()
            self.live_status_label.setText("Остановка live-сессии…")

    def _poll_live_updates(self) -> None:
        for source_id, controller in tuple(self._live_controllers.items()):
            update = controller.poll_latest()
            if update is None:
                continue
            adapter = self._live_adapters.get(source_id)
            if adapter is None:
                continue
            session = self.repository.get(adapter.session_id)
            if session is None:
                continue
            state = adapter.apply(session, update)
            if state.ignored_as_stale:
                continue
            sweep_frame = getattr(update, "sweep_frame", None)
            if state.trace is not None:
                self.spectrum_renderer.set_trace(state.trace)
            if sweep_frame is not None:
                self.show_sweep_frame(sweep_frame)
            if state.waterfall is not None and self.active_session_id == session.session_id:
                self.waterfall_renderer.set_data(state.waterfall)
            if (
                state.persistence_snapshot is not None
                and self.active_session_id == session.session_id
            ):
                self._apply_live_persistence_snapshot(state.persistence_snapshot)
            self._update_live_diagnostics(source_id, state)
            self._live_refresh_counter += 1
            if self._live_refresh_counter % 8 == 0:
                self._refresh_tree()

    def show_sweep_frame(self, frame: Any) -> None:
        """Display one P13 stitched frame and expose its quality evidence."""

        trace = sweep_trace_from_frame(frame)
        self._last_sweep_trace = trace
        self.spectrum_renderer.set_trace(trace)
        metadata = trace.metadata
        sweep_id = int(metadata["sweep_id"])
        missing_bins = int(metadata["missing_bins"])
        overlap_bins = int(metadata["overlap_bins"])
        calibration_status = metadata["calibration_status"]
        self.live_sweep_quality_label.setText(
            f"Sweep {sweep_id}; {trace.point_count:,} bins; "
            f"missing {missing_bins:,}; overlap {overlap_bins:,}; "
            f"{calibration_status}"
        )
        seams = metadata["seams"]
        if seams:
            worst = max(seams, key=lambda item: item["after_p95_db"])
            after_p95 = float(worst["after_p95_db"])
            correction = float(worst["correction_db"])
            self.live_sweep_seams_label.setText(
                f"{len(seams)}; worst after P95 {after_p95:.3f} dB; "
                f"correction {correction:+.3f} dB"
            )
        else:
            self.live_sweep_seams_label.setText("No measurable overlap seam")
        self.live_status_label.setText("P13 stitched full-span frame displayed")

    def _apply_live_persistence_snapshot(self, snapshot: Any) -> None:
        frequencies = np.asarray(snapshot.frequencies_hz, dtype=np.float64)
        if frequencies.size < 2 or snapshot.frequency_bins != frequencies.size:
            self.spectrum_renderer.clear_heatmap()
            return
        density = np.asarray(snapshot.density, dtype=np.float32)
        expected = int(snapshot.power_bins) * int(snapshot.frequency_bins)
        if density.size != expected:
            self.spectrum_renderer.clear_heatmap()
            return
        image = np.log1p(np.maximum(density, 0.0)).reshape(
            int(snapshot.power_bins), int(snapshot.frequency_bins)
        )
        finite = image[np.isfinite(image)]
        vmax = float(np.max(finite)) if finite.size else 1.0
        self.spectrum_renderer.set_heatmap(
            image,
            float(frequencies[0]),
            float(frequencies[-1]),
            float(snapshot.power_min_db),
            float(snapshot.power_max_db),
            levels=(0.0, max(1.0, vmax)),
        )
        self.live_status_label.setText(
            f"Native persistence: {snapshot.processed_frames:,} frames"
        )

    def _update_live_diagnostics(self, source_id: str, state: Any) -> None:
        self.live_status_label.setText(f"{source_id}: {state.state.value}")
        if state.error:
            self.live_diagnostics_label.setText(state.error)
            return
        applied = state.applied_config
        if applied is not None:
            self.live_applied_label.setText(
                f"{getattr(applied, 'center_frequency_hz', 0.0) / 1e6:.6f} MHz; "
                f"SR {getattr(applied, 'sample_rate_hz', 0.0) / 1e6:.6f} MHz; "
                f"generation {getattr(applied, 'config_generation', 0)}"
            )
        metrics = getattr(state.metrics, "engine", None)
        if metrics is not None:
            self.live_diagnostics_label.setText(
                f"FFT {metrics.fft_frames_computed:,}; "
                f"snapshots {metrics.spectrum_snapshots_emitted:,}; "
                f"persistence {metrics.persistence_updates:,}; "
                f"drops I/Q {metrics.iq_blocks_dropped:,}, FFT {metrics.fft_frames_dropped:,}"
            )

    @staticmethod
    def _frequency_spin(value: float = 0.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 1_000_000.0)
        spin.setDecimals(6)
        spin.setValue(value)
        spin.setSingleStep(0.1)
        return spin

    def _create_channel_power_docks(self) -> None:
        content = QWidget()
        layout = QVBoxLayout(content)

        measurement_group = QGroupBox("Power Measurements")
        measurement_layout = QFormLayout(measurement_group)
        self.power_measurement_mode = QComboBox()
        for name in (
            "Time-Gated Channel Power", "Single Channel Power", "ACLR / ACPR",
            "Multi-Channel ACLR", "Occupied Bandwidth", "X dB Bandwidth",
            "Carrier-to-Noise", "Harmonic Power", "Spectrum Emission Mask",
            "Spurious Search", "Custom Multi-Region Power",
        ):
            self.power_measurement_mode.addItem(name, name)
        self.power_source = QComboBox()
        for name in (
            "Current Displayed Trace", "Current Waterfall Frame",
            "Selected Waterfall Interval", "Entire Waterfall", "Selected Events",
        ):
            self.power_source.addItem(name, name)
        self.power_profile = QComboBox()
        for profile in BUILTIN_POWER_PROFILES:
            self.power_profile.addItem(profile.name, profile.name)
        self.power_profile.currentIndexChanged.connect(self._power_profile_changed)
        measurement_layout.addRow("Режим", self.power_measurement_mode)
        measurement_layout.addRow("Источник", self.power_source)
        measurement_layout.addRow("Профиль", self.power_profile)
        layout.addWidget(measurement_group)

        frequency_group = QGroupBox("Frequency Selection")
        frequency_layout = QFormLayout(frequency_group)
        self.cp_start = self._frequency_spin()
        self.cp_stop = self._frequency_spin()
        self.cp_center = self._frequency_spin()
        self.cp_bandwidth = self._frequency_spin()
        self.cp_semantics = QComboBox()
        self.cp_semantics.addItem("Auto", None)
        for label, semantics in (
            ("Power per bin", PowerSemantics.POWER_PER_BIN),
            ("PSD per Hz", PowerSemantics.PSD_PER_HZ),
            ("RBW-filtered power", PowerSemantics.RBW_FILTERED_POWER),
            ("Unknown / approximate", PowerSemantics.UNKNOWN),
        ):
            self.cp_semantics.addItem(label, semantics)
        frequency_layout.addRow("Start, MHz", self.cp_start)
        frequency_layout.addRow("Stop, MHz", self.cp_stop)
        frequency_layout.addRow("Center, MHz", self.cp_center)
        frequency_layout.addRow("Bandwidth, MHz", self.cp_bandwidth)
        frequency_layout.addRow("Power semantics", self.cp_semantics)
        frequency_buttons = QHBoxLayout()
        for text, slot in (
            ("Видимый диапазон", self._cp_use_visible_range),
            ("Выделенная область", self._cp_use_frequency_region),
            ("Сброс", self._cp_reset_frequency),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            frequency_buttons.addWidget(button)
        frequency_layout.addRow(frequency_buttons)
        self.cp_start.valueChanged.connect(self._cp_edges_changed)
        self.cp_stop.valueChanged.connect(self._cp_edges_changed)
        self.cp_center.valueChanged.connect(self._cp_center_band_changed)
        self.cp_bandwidth.valueChanged.connect(self._cp_center_band_changed)
        layout.addWidget(frequency_group)

        time_group = QGroupBox("Time Selection / Frame Inclusion")
        time_layout = QFormLayout(time_group)
        self.cp_time_mode = QComboBox()
        for label, mode in (
            ("Текущий кадр", ChannelPowerMode.CURRENT_FRAME),
            ("Выбранный интервал", ChannelPowerMode.SELECTED_INTERVAL_ALL_FRAMES),
            ("Вся запись", ChannelPowerMode.ENTIRE_RECORDING_ALL_FRAMES),
            ("Выбранные события", ChannelPowerMode.SELECTED_EVENTS),
        ):
            self.cp_time_mode.addItem(label, mode)
        self.cp_frame_inclusion = QComboBox()
        for label, inclusion in (
            ("Все кадры", FrameInclusion.ALL),
            ("Только активные", FrameInclusion.ACTIVE_ONLY),
            ("Только неактивные", FrameInclusion.INACTIVE_ONLY),
            ("Ручная маска", FrameInclusion.MANUAL_MASK),
        ):
            self.cp_frame_inclusion.addItem(label, inclusion)
        self.cp_start_frame = QSpinBox()
        self.cp_stop_frame = QSpinBox()
        for spin in (self.cp_start_frame, self.cp_stop_frame):
            spin.setRange(1, 1)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self._cp_time_selection_changed)
        self.cp_time_info = QLabel("—")
        time_layout.addRow("Режим", self.cp_time_mode)
        time_layout.addRow("Включать", self.cp_frame_inclusion)
        time_layout.addRow("Начальный кадр", self.cp_start_frame)
        time_layout.addRow("Конечный кадр", self.cp_stop_frame)
        time_layout.addRow("Интервал", self.cp_time_info)
        layout.addWidget(time_group)

        activity_group = QGroupBox("Activity Detection")
        activity_layout = QFormLayout(activity_group)
        self.cp_activity_enabled = QCheckBox("Автоматическое обнаружение")
        self.cp_activity_enabled.setChecked(True)
        self.cp_threshold_mode = QComboBox()
        for label, mode in (
            ("Noise-relative", ActivityThresholdMode.AUTO_NOISE_RELATIVE),
            ("Absolute", ActivityThresholdMode.ABSOLUTE),
            ("Robust statistics", ActivityThresholdMode.AUTO_ROBUST_STATISTICS),
            ("Manual noise interval", ActivityThresholdMode.MANUAL_NOISE_REGION),
            ("Percentile", ActivityThresholdMode.PERCENTILE),
        ):
            self.cp_threshold_mode.addItem(label, mode)
        self.cp_absolute_threshold = self._power_spin(-65.0)
        self.cp_on_offset = self._power_spin(10.0, 0.0, 100.0)
        self.cp_off_offset = self._power_spin(6.0, 0.0, 100.0)
        self.cp_idle_percentile = self._power_spin(20.0, 1.0, 80.0)
        self.cp_robust_sigma = self._power_spin(6.0, 0.1, 30.0)
        self.cp_smoothing = QComboBox()
        for label, mode in (
            ("Median", SmoothingMode.MEDIAN),
            ("Moving average (linear)", SmoothingMode.MOVING_AVERAGE),
            ("Exponential", SmoothingMode.EXPONENTIAL),
            ("None", SmoothingMode.NONE),
        ):
            self.cp_smoothing.addItem(label, mode)
        self.cp_smoothing_window = QSpinBox()
        self.cp_smoothing_window.setRange(1, 10001)
        self.cp_smoothing_window.setValue(3)
        self.cp_min_active = QSpinBox()
        self.cp_min_inactive = QSpinBox()
        self.cp_max_gap = QSpinBox()
        self.cp_merge_gap = QSpinBox()
        for spin, value in (
            (self.cp_min_active, 2), (self.cp_min_inactive, 2),
            (self.cp_max_gap, 1), (self.cp_merge_gap, 1),
        ):
            spin.setRange(0, 1_000_000)
            spin.setValue(value)
        self.cp_hysteresis = QCheckBox("Использовать гистерезис")
        self.cp_hysteresis.setChecked(True)
        for label, widget in (
            ("", self.cp_activity_enabled), ("Threshold mode", self.cp_threshold_mode),
            ("Absolute, dBm", self.cp_absolute_threshold), ("ON offset, dB", self.cp_on_offset),
            ("OFF offset, dB", self.cp_off_offset), ("Idle percentile, %", self.cp_idle_percentile),
            ("Robust sigma", self.cp_robust_sigma), ("Smoothing", self.cp_smoothing),
            ("Smoothing window", self.cp_smoothing_window), ("Min active, frames", self.cp_min_active),
            ("Min inactive, frames", self.cp_min_inactive), ("Max gap, frames", self.cp_max_gap),
            ("Merge gap, frames", self.cp_merge_gap), ("", self.cp_hysteresis),
        ):
            activity_layout.addRow(label, widget)
        layout.addWidget(activity_group)

        regions_group = QGroupBox("Frequency Regions")
        regions_layout = QVBoxLayout(regions_group)
        self.power_regions_table = QTableWidget(0, 9)
        self.power_regions_table.setHorizontalHeaderLabels(
            ["Name", "Role", "Center", "Start", "Stop", "Bandwidth", "Color", "Enabled", "Reference"]
        )
        self.power_regions_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.power_regions_table.setMinimumHeight(90)
        regions_layout.addWidget(self.power_regions_table)
        region_buttons = QHBoxLayout()
        for text, slot in (("Добавить", self._power_add_region), ("Удалить", self._power_remove_region)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            region_buttons.addWidget(button)
        regions_layout.addLayout(region_buttons)
        layout.addWidget(regions_group)

        options_group = QGroupBox("Параметры выбранного режима")
        options_layout = QFormLayout(options_group)
        self.power_obw_percent = self._power_spin(99.0, 90.0, 99.99)
        self.power_xdb = self._power_spin(3.0, 0.01, 100.0)
        self.power_adjacent_pairs = QSpinBox()
        self.power_adjacent_pairs.setRange(1, 32)
        self.power_adjacent_pairs.setValue(1)
        self.power_harmonics = QSpinBox()
        self.power_harmonics.setRange(1, 100)
        self.power_harmonics.setValue(5)
        self.power_sem_limit = self._power_spin(-30.0, -300.0, 300.0)
        self.power_spur_level = self._power_spin(-80.0, -300.0, 300.0)
        self.power_spur_prominence = self._power_spin(6.0, 0.0, 300.0)
        self.power_spur_distance = self._frequency_spin()
        self.power_spur_count = QSpinBox()
        self.power_spur_count.setRange(1, 10000)
        self.power_spur_count.setValue(100)
        options_layout.addRow("OBW, %", self.power_obw_percent)
        options_layout.addRow("X dB", self.power_xdb)
        options_layout.addRow("Adjacent pairs", self.power_adjacent_pairs)
        options_layout.addRow("Harmonics", self.power_harmonics)
        options_layout.addRow("SEM limit, dBm", self.power_sem_limit)
        options_layout.addRow("Spurious min, dBm", self.power_spur_level)
        options_layout.addRow("Spurious prominence, dB", self.power_spur_prominence)
        options_layout.addRow("Spurious distance, MHz", self.power_spur_distance)
        options_layout.addRow("Spurious max count", self.power_spur_count)
        layout.addWidget(options_group)

        action_grid = QGridLayout()
        for index, (text, slot) in enumerate((
            ("Detect / Recalculate", self._run_selected_power_measurement),
            ("Noise interval", self._cp_select_noise_interval),
            ("Mark Active", lambda: self._set_manual_override(ManualOverride.FORCE_ACTIVE)),
            ("Mark Inactive", lambda: self._set_manual_override(ManualOverride.FORCE_INACTIVE)),
            ("Mark Auto", lambda: self._set_manual_override(ManualOverride.AUTO)),
            ("Clear overrides", self._clear_manual_overrides),
            ("Reset defaults", self._cp_reset_defaults),
        )):
            button = QPushButton(text)
            button.clicked.connect(slot)
            if index == 0:
                self.cp_recalculate_button = button
            action_grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(action_grid)

        self.cp_recalc_status = QLabel("Настройки ещё не рассчитаны")
        self.cp_recalc_status.setWordWrap(True)
        layout.addWidget(self.cp_recalc_status)
        self.power_quality_label = QLabel("Quality: —")
        self.power_warnings_label = QLabel("Warnings: —")
        self.power_warnings_label.setWordWrap(True)
        layout.addWidget(self.power_quality_label)
        layout.addWidget(self.power_warnings_label)

        layout.addStretch(1)

        self.cp_result_table = QTableWidget(0, 2)
        self.cp_result_table.setHorizontalHeaderLabels(["Результат", "Значение"])
        self.cp_result_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.cp_result_table.setMinimumHeight(240)
        self.cp_result_table.setAlternatingRowColors(True)
        self.cp_result_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cp_result_table.customContextMenuRequested.connect(
            self._channel_power_context_menu
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        content.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        content.customContextMenuRequested.connect(
            lambda position: self._channel_power_context_menu(
                self.cp_result_table.viewport().mapFromGlobal(content.mapToGlobal(position))
            )
        )
        channel_power_splitter = QSplitter(Qt.Orientation.Vertical)
        channel_power_splitter.setObjectName("channelPowerSettingsResultsSplitter")
        channel_power_splitter.setChildrenCollapsible(False)
        channel_power_splitter.addWidget(scroll)
        channel_power_splitter.addWidget(self.cp_result_table)
        channel_power_splitter.setStretchFactor(0, 3)
        channel_power_splitter.setStretchFactor(1, 2)
        channel_power_splitter.setSizes([560, 300])
        self.channel_power_dock = self._dock(
            "Channel Power", "channelPowerDock", channel_power_splitter,
            Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self.tabifyDockWidget(self.measurements_dock, self.channel_power_dock)

        self.channel_power_plot = pg.PlotWidget(background="#10151c")
        self.channel_power_plot.setLabel("bottom", "Время", units="s")
        self.channel_power_plot.setLabel("left", "Channel Power", units="dBm")
        self.channel_power_plot.showGrid(x=True, y=True, alpha=0.2)
        self.channel_power_plot.getPlotItem().setDownsampling(auto=True, mode="peak")
        self.channel_power_plot.getPlotItem().setClipToView(True)
        self.cp_raw_curve = self.channel_power_plot.plot(pen=pg.mkPen("#35c6ff80", width=1))
        self.cp_smooth_curve = self.channel_power_plot.plot(pen=pg.mkPen("#ffb347", width=1.5))
        self.cp_manual_active_curve = self.channel_power_plot.plot(
            pen=None, symbol="t", symbolSize=6, symbolBrush="#3ddc97"
        )
        self.cp_manual_inactive_curve = self.channel_power_plot.plot(
            pen=None, symbol="t1", symbolSize=6, symbolBrush="#ff5f56"
        )
        self.cp_current_line = pg.InfiniteLine(angle=90, pen=pg.mkPen("#ffffffa0"))
        self.cp_threshold_on_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ff5f56"))
        self.cp_threshold_off_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ffbd2e"))
        self.cp_idle_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#3ddc97"))
        self.cp_time_region = pg.LinearRegionItem(
            (0.0, 0.0), movable=True, brush=pg.mkBrush("#ff8c4225"),
            pen=pg.mkPen("#ff8c42")
        )
        for item in (
            self.cp_current_line, self.cp_threshold_on_line, self.cp_threshold_off_line,
            self.cp_idle_line, self.cp_time_region,
        ):
            self.channel_power_plot.addItem(item)
        self.cp_time_region.hide()
        self.cp_time_region.sigRegionChangeFinished.connect(self._cp_plot_region_changed)
        self.channel_power_plot.scene().sigMouseClicked.connect(self._cp_time_plot_clicked)
        self.channel_power_plot.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.channel_power_plot.customContextMenuRequested.connect(
            self._channel_power_context_menu
        )
        self._connect_channel_power_control_logging()
        self.channel_power_time_dock = self._dock(
            "Channel Power во времени", "channelPowerTimeDock", self.channel_power_plot,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.tabifyDockWidget(self.events_dock, self.channel_power_time_dock)

    def _create_heatmap_dock(self) -> None:
        content = QWidget()
        form = QFormLayout(content)

        self.heatmap_enabled = QCheckBox("Включить Heatmap")
        self.heatmap_enabled.setChecked(False)
        self.heatmap_enabled.toggled.connect(self._heatmap_toggled)
        form.addRow(self.heatmap_enabled)

        self.heatmap_range_mode = QComboBox()
        for label, mode in (
            ("Rolling Exact", PersistenceMode.ROLLING_EXACT),
            ("Exponential Decay", PersistenceMode.EXPONENTIAL_DECAY),
            ("Selected Range", PersistenceMode.SELECTED_RANGE),
            ("Full Recording", PersistenceMode.FULL_RECORDING),
        ):
            self.heatmap_range_mode.addItem(label, mode)
        self.heatmap_range_mode.currentIndexChanged.connect(self._heatmap_structural_changed)
        form.addRow("Режим", self.heatmap_range_mode)

        self.heatmap_window_unit = QComboBox()
        self.heatmap_window_unit.addItem("Frames", WindowUnit.FRAMES)
        self.heatmap_window_unit.addItem("Time (s)", WindowUnit.SECONDS)
        self.heatmap_window_unit.currentIndexChanged.connect(self._heatmap_structural_changed)
        form.addRow("Единицы окна", self.heatmap_window_unit)

        self.heatmap_window_frames_spin = QSpinBox()
        self.heatmap_window_frames_spin.setRange(1, 1_000_000)
        self.heatmap_window_frames_spin.setValue(500)
        self.heatmap_window_frames_spin.setKeyboardTracking(False)
        self.heatmap_window_frames_spin.valueChanged.connect(self._heatmap_structural_changed)
        form.addRow("Окно, кадров", self.heatmap_window_frames_spin)
        self.heatmap_window_budget_label = QLabel("Нижний предел: ожидается источник")
        form.addRow("Бюджет UI", self.heatmap_window_budget_label)

        self.heatmap_window_seconds_spin = QDoubleSpinBox()
        self.heatmap_window_seconds_spin.setRange(0.001, 1_000_000.0)
        self.heatmap_window_seconds_spin.setDecimals(3)
        self.heatmap_window_seconds_spin.setValue(10.0)
        self.heatmap_window_seconds_spin.valueChanged.connect(self._heatmap_structural_changed)
        form.addRow("Окно, секунд", self.heatmap_window_seconds_spin)

        self.heatmap_follow_playhead = QCheckBox("Следовать за playhead")
        self.heatmap_follow_playhead.setChecked(True)
        self.heatmap_follow_playhead.toggled.connect(self._heatmap_structural_changed)
        form.addRow(self.heatmap_follow_playhead)

        self.heatmap_start_spin = QSpinBox()
        self.heatmap_end_spin = QSpinBox()
        for spin in (self.heatmap_start_spin, self.heatmap_end_spin):
            spin.setRange(1, 1)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self._heatmap_structural_changed)
        self.heatmap_start_label = "Начальный кадр"
        self.heatmap_end_label = "Конечный кадр"
        form.addRow(self.heatmap_start_label, self.heatmap_start_spin)
        form.addRow(self.heatmap_end_label, self.heatmap_end_spin)

        self.heatmap_compute_mode = QComboBox()
        self.heatmap_compute_mode.addItem("Точный", HeatmapSamplingPolicy.FULL_RANGE)
        self.heatmap_compute_mode.addItem("Ускоренный preview", HeatmapSamplingPolicy.SAMPLED_RANGE)
        self.heatmap_compute_mode.currentIndexChanged.connect(self._heatmap_structural_changed)
        form.addRow("Режим вычисления", self.heatmap_compute_mode)

        self.heatmap_normalization = QComboBox()
        for label, normalization in (
            ("Count", HeatmapNormalization.COUNT),
            ("Probability", HeatmapNormalization.PROBABILITY),
            ("Log Density", HeatmapNormalization.LOG_DENSITY),
        ):
            self.heatmap_normalization.addItem(label, normalization)
        self.heatmap_normalization.setCurrentIndex(2)
        self.heatmap_normalization.currentIndexChanged.connect(self._heatmap_normalization_changed)
        form.addRow("Нормализация", self.heatmap_normalization)

        self.heatmap_power_min = QDoubleSpinBox()
        self.heatmap_power_max = QDoubleSpinBox()
        for spin, value in ((self.heatmap_power_min, -120.0), (self.heatmap_power_max, 0.0)):
            spin.setRange(-300.0, 300.0)
            spin.setDecimals(1)
            spin.setValue(value)
            spin.valueChanged.connect(self._heatmap_structural_changed)
        form.addRow("Power min, dBm", self.heatmap_power_min)
        form.addRow("Power max, dBm", self.heatmap_power_max)

        self.heatmap_power_bins = QComboBox()
        self.heatmap_power_bins.addItems(["64", "128", "256", "512"])
        self.heatmap_power_bins.setCurrentText("256")
        self.heatmap_power_bins.currentIndexChanged.connect(self._heatmap_structural_changed)
        form.addRow("Power bins", self.heatmap_power_bins)

        self.heatmap_opacity = QDoubleSpinBox()
        self.heatmap_opacity.setRange(0.0, 1.0)
        self.heatmap_opacity.setDecimals(2)
        self.heatmap_opacity.setSingleStep(0.05)
        self.heatmap_opacity.setValue(0.65)
        self.heatmap_opacity.valueChanged.connect(self._heatmap_opacity_changed)
        form.addRow("Непрозрачность", self.heatmap_opacity)

        self.heatmap_palette = QComboBox()
        self.heatmap_palette.addItems(["Turbo", "Viridis", "Plasma", "Inferno", "Magma", "Grayscale", "Jet"])
        self.heatmap_palette.setCurrentText("Viridis")
        self.heatmap_palette.currentTextChanged.connect(self._heatmap_palette_changed)
        form.addRow("Цветовая палитра", self.heatmap_palette)

        self.heatmap_color_scale_mode = QComboBox()
        for label, mode in (
            ("Auto (по текущему)", ColorScaleMode.AUTO_CURRENT),
            ("Fixed", ColorScaleMode.FIXED),
            ("Percentile", ColorScaleMode.PERCENTILE),
            ("Smoothed Auto", ColorScaleMode.SMOOTHED_AUTO),
        ):
            self.heatmap_color_scale_mode.addItem(label, mode)
        self.heatmap_color_scale_mode.currentIndexChanged.connect(self._heatmap_color_levels_changed)
        form.addRow("Цветовая шкала", self.heatmap_color_scale_mode)

        self.heatmap_color_min = QDoubleSpinBox()
        self.heatmap_color_max = QDoubleSpinBox()
        for spin, value in ((self.heatmap_color_min, 0.0), (self.heatmap_color_max, 1.0)):
            spin.setRange(-1_000_000.0, 1_000_000.0)
            spin.setDecimals(3)
            spin.setValue(value)
            spin.valueChanged.connect(self._heatmap_color_levels_changed)
        form.addRow("Color min", self.heatmap_color_min)
        form.addRow("Color max", self.heatmap_color_max)

        # Exponential Decay is a data-time half-life model (approximate
        # persistence), not the legacy 0..1 coefficient. Default 1.0 s is a
        # product constant documented in CHANGELOG — NOT derived from 0.95.
        half_life_row = QWidget()
        half_life_layout = QHBoxLayout(half_life_row)
        half_life_layout.setContentsMargins(0, 0, 0, 0)
        self.heatmap_half_life_spin = QDoubleSpinBox()
        self.heatmap_half_life_spin.setRange(0.001, 1_000_000.0)
        self.heatmap_half_life_spin.setDecimals(3)
        self.heatmap_half_life_spin.setValue(1.0)
        self.heatmap_half_life_spin.valueChanged.connect(self._heatmap_structural_changed)
        self.heatmap_half_life_unit = QComboBox()
        self.heatmap_half_life_unit.addItems(["s", "ms"])
        self.heatmap_half_life_unit.currentIndexChanged.connect(self._heatmap_structural_changed)
        half_life_layout.addWidget(self.heatmap_half_life_spin)
        half_life_layout.addWidget(self.heatmap_half_life_unit)
        self.heatmap_half_life_row = half_life_row
        form.addRow("Half-life (приближённая модель)", half_life_row)

        buttons = QHBoxLayout()
        self.heatmap_recalculate_button = QPushButton("Rebuild now")
        self.heatmap_recalculate_button.clicked.connect(self._heatmap_recalculate)
        self.heatmap_cancel_button = QPushButton("Отменить")
        self.heatmap_cancel_button.clicked.connect(self._heatmap_cancel)
        self.heatmap_clear_button = QPushButton("Очистить")
        self.heatmap_clear_button.clicked.connect(self._heatmap_clear)
        for button in (self.heatmap_recalculate_button, self.heatmap_cancel_button, self.heatmap_clear_button):
            buttons.addWidget(button)
        form.addRow(buttons)

        self.heatmap_status = QLabel("Heatmap выключен")
        self.heatmap_status.setWordWrap(True)
        form.addRow(self.heatmap_status)

        # Scroll area keeps every control reachable when the tabified dock is
        # squeezed on short screens (the form is taller than small work areas).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.heatmap_dock = self._dock(
            "Spectrum → Heatmap", "heatmapDock", scroll, Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.tabifyDockWidget(self.display_dock, self.heatmap_dock)
        self._update_heatmap_controls_for_mode()

    def _connect_channel_power_control_logging(self) -> None:
        controls: tuple[tuple[str, Any], ...] = (
            ("power_semantics", self.cp_semantics),
            ("time_mode", self.cp_time_mode),
            ("frame_inclusion", self.cp_frame_inclusion),
            ("activity_enabled", self.cp_activity_enabled),
            ("threshold_mode", self.cp_threshold_mode),
            ("absolute_threshold_dbm", self.cp_absolute_threshold),
            ("threshold_on_offset_db", self.cp_on_offset),
            ("threshold_off_offset_db", self.cp_off_offset),
            ("idle_percentile", self.cp_idle_percentile),
            ("robust_sigma", self.cp_robust_sigma),
            ("smoothing", self.cp_smoothing),
            ("smoothing_window_frames", self.cp_smoothing_window),
            ("min_active_frames", self.cp_min_active),
            ("min_inactive_frames", self.cp_min_inactive),
            ("max_gap_frames", self.cp_max_gap),
            ("merge_gap_frames", self.cp_merge_gap),
            ("hysteresis", self.cp_hysteresis),
        )
        for name, control in controls:
            if isinstance(control, QComboBox):
                control.currentIndexChanged.connect(
                    lambda _index, name=name, control=control: self._channel_power_control_changed(
                        name, str(control.currentData()), control.currentText()
                    )
                )
            elif isinstance(control, QCheckBox):
                control.toggled.connect(
                    lambda value, name=name: self._channel_power_control_changed(name, value)
                )
            else:
                control.valueChanged.connect(
                    lambda value, name=name: self._channel_power_control_changed(name, value)
                )

    def _power_profile_changed(self) -> None:
        profile = profile_by_name(str(self.power_profile.currentData()))
        if profile.main_bandwidth_hz <= 0:
            return
        self.cp_bandwidth.setValue(profile.main_bandwidth_hz / 1e6)
        if profile.adjacent_offsets_hz:
            self.acpr_offset.setValue(profile.adjacent_offsets_hz[0] / 1e6)
        if profile.adjacent_bandwidths_hz:
            self.acpr_width.setValue(profile.adjacent_bandwidths_hz[0] / 1e6)
        self.power_obw_percent.setValue(profile.obw_percent)
        self.cp_on_offset.setValue(profile.default_activity_threshold_db)
        self._audit("user", "power_measurement_profile_selected", profile=profile.name)

    def _power_add_region(self) -> None:
        row = self.power_regions_table.rowCount()
        self.power_regions_table.insertRow(row)
        center = self.cp_center.value()
        bandwidth = max(self.cp_bandwidth.value(), 0.001)
        values = (
            f"Region {row + 1}", RegionRole.MEASURE.value, f"{center:.6f}",
            f"{center - bandwidth / 2:.6f}", f"{center + bandwidth / 2:.6f}",
            f"{bandwidth:.6f}", "#3ddc97", "1", "0",
        )
        for column, value in enumerate(values):
            self.power_regions_table.setItem(row, column, QTableWidgetItem(value))
        self._mark_channel_power_dirty("добавлена область измерения")

    def _power_remove_region(self) -> None:
        rows = sorted({index.row() for index in self.power_regions_table.selectedIndexes()}, reverse=True)
        if not rows and self.power_regions_table.currentRow() >= 0:
            rows = [self.power_regions_table.currentRow()]
        for row in rows:
            self.power_regions_table.removeRow(row)
        if rows:
            self._mark_channel_power_dirty("удалена область измерения")

    def _power_regions(self) -> tuple[MeasurementRegion, ...]:
        regions: list[MeasurementRegion] = []
        for row in range(self.power_regions_table.rowCount()):
            def text(column: int) -> str:
                return self.power_regions_table.item(row, column).text().strip()
            try:
                role_text = text(1)
                role = RegionRole(role_text) if role_text in {item.value for item in RegionRole} else RegionRole.MEASURE
                regions.append(MeasurementRegion(
                    text(0) or f"Region {row + 1}", float(text(3)) * 1e6,
                    float(text(4)) * 1e6, role,
                    text(7).casefold() not in {"0", "false", "нет"},
                    text(6) or "#3ddc97", text(8) or None,
                ))
            except (AttributeError, ValueError):
                continue
        if not regions:
            regions.append(MeasurementRegion(
                "Main", self.cp_start.value() * 1e6, self.cp_stop.value() * 1e6,
                RegionRole.MAIN,
            ))
        return tuple(regions)

    def _current_power_frame(self) -> SpectrumFrame | None:
        session = self.active_session()
        source = str(self.power_source.currentData())
        trace = session.traces.get(source.removeprefix("trace:")) if session and source.startswith("trace:") else (
            self._active_frequency_trace(session) if session else None
        )
        if trace is None:
            return None
        result = DflMeasurementAdapter.spectrum_frame(trace)
        displayed = self.spectrum_renderer.trace_data(trace.trace_id)
        if displayed is not None and not source.startswith("trace:"):
            result.frequencies_hz = np.asarray(displayed[0], dtype=np.float64)
            result.values_db = np.asarray(displayed[1], dtype=np.float64)
            result.frame_index = session.current_frame if session and session.active_waterfall_id else None
        semantics = self.cp_semantics.currentData()
        if semantics is not None:
            result.power_semantics = PowerSemantics(semantics)
        return result

    def _refresh_power_sources(self, session: MeasurementSession) -> None:
        previous = self.power_source.currentData()
        base = (
            "Current Displayed Trace", "Current Waterfall Frame",
            "Selected Waterfall Interval", "Entire Waterfall", "Selected Events",
        )
        self.power_source.blockSignals(True)
        self.power_source.clear()
        for name in base:
            self.power_source.addItem(name, name)
        for trace in session.traces.values():
            if trace.is_frequency_trace:
                self.power_source.addItem(f"Named Trace: {trace.name}", f"trace:{trace.trace_id}")
        index = self.power_source.findData(previous)
        self.power_source.setCurrentIndex(max(0, index))
        self.power_source.blockSignals(False)

    def _run_selected_power_measurement(self) -> None:
        mode = str(self.power_measurement_mode.currentData())
        if mode == "Time-Gated Channel Power":
            self.run_time_gated_channel_power()
            return
        session = self.active_session()
        frame = self._current_power_frame()
        if session is None or frame is None:
            self._show_error("Power Measurements", "Выберите частотную трассу или кадр waterfall")
            return
        regions = self._power_regions()
        existing = {region.name: region for region in session.frequency_regions}
        visual_regions: list[FrequencyRegion] = []
        for region in regions:
            visual = existing.get(region.name) or FrequencyRegion(name=region.name)
            visual.start_frequency_hz = region.start_hz
            visual.stop_frequency_hz = region.stop_hz
            visual.region_type = region.role.value
            visual.enabled = region.enabled
            visual.color = region.color
            visual_regions.append(visual)
        session.frequency_regions = visual_regions
        self.spectrum_renderer.set_regions(session.frequency_regions)
        self._connect_frequency_regions(session)
        main = regions[0]
        center = (main.start_hz + main.stop_hz) / 2
        bandwidth = abs(main.stop_hz - main.start_hz)
        adjacent_offset_hz = self.acpr_offset.value() * 1e6
        adjacent_bandwidth_hz = self.acpr_width.value() * 1e6
        adjacent_pairs = self.power_adjacent_pairs.value()
        obw_fraction = self.power_obw_percent.value() / 100
        drop_db = self.power_xdb.value()
        harmonic_count = self.power_harmonics.value()
        sem_limit_dbm = self.power_sem_limit.value()
        spur_level_dbm = self.power_spur_level.value()
        spur_prominence_db = self.power_spur_prominence.value()
        spur_distance_hz = self.power_spur_distance.value() * 1e6
        spur_count = self.power_spur_count.value()

        def calculate() -> Any:
            if mode == "Single Channel Power":
                return SingleChannelPowerService().measure(frame, main.start_hz, main.stop_hz)
            if mode == "ACLR / ACPR":
                return AclrService().measure(
                    [frame], center, bandwidth, adjacent_offset_hz,
                    adjacent_bandwidth_hz=adjacent_bandwidth_hz,
                    adjacent_pairs=adjacent_pairs,
                )
            if mode == "Multi-Channel ACLR":
                definitions = tuple(
                    MultiChannelDefinition(region.name, (region.start_hz + region.stop_hz) / 2, abs(region.stop_hz - region.start_hz), region.role == RegionRole.MAIN)
                    for region in regions
                )
                return multi_channel_aclr(frame, definitions)
            if mode == "Occupied Bandwidth":
                return power_occupied_bandwidth(frame, obw_fraction, (main.start_hz, main.stop_hz))
            if mode == "X dB Bandwidth":
                return power_x_db_bandwidth(frame, drop_db, (main.start_hz, main.stop_hz))
            if mode == "Carrier-to-Noise":
                noise = next((region for region in regions if region.role == RegionRole.NOISE), None)
                if noise is None:
                    raise ValueError("Добавьте область с ролью noise")
                return carrier_to_noise(frame, (main.start_hz, main.stop_hz), (noise.start_hz, noise.stop_hz))
            if mode == "Harmonic Power":
                return harmonic_powers(frame, center, harmonic_count, bandwidth)
            if mode == "Spectrum Emission Mask":
                return spectrum_emission_mask(frame, (SemMaskSegment(main.start_hz, main.stop_hz, sem_limit_dbm, sem_limit_dbm),))
            if mode == "Spurious Search":
                exclusions = tuple((region.start_hz, region.stop_hz) for region in regions if region.role in (RegionRole.MAIN, RegionRole.EXCLUDE))
                main_result = SingleChannelPowerService().measure(
                    frame, main.start_hz, main.stop_hz
                ).integrated
                return spurious_search(
                    frame, frame.frequencies_hz[0], frame.frequencies_hz[-1],
                    minimum_level_dbm=spur_level_dbm,
                    minimum_prominence_db=spur_prominence_db,
                    minimum_distance_hz=spur_distance_hz,
                    exclusions=exclusions,
                    limit=spur_count,
                    measurement_bandwidth_hz=max(frame.rbw_hz or 0.0, bandwidth / 100),
                    main_power_dbm=main_result.power_dbm,
                    main_center_hz=center,
                )
            return measure_regions(frame, regions)

        self._power_measurement_serial += 1
        serial = self._power_measurement_serial
        worker = TaskWorker(calculate)
        worker.signals.result.connect(
            lambda value: self._power_measurement_ready(serial, session.session_id, mode, value)
        )
        self.cp_recalc_status.setText(f"Расчёт {mode} выполняется…")
        self._set_busy(True, f"Расчёт: {mode}")
        self._start_worker(worker)

    def _power_measurement_ready(self, serial: int, session_id: str, mode: str, value: Any) -> None:
        if serial != self._power_measurement_serial or session_id != self.active_session_id:
            return
        quality = getattr(value, "quality", None)
        warnings = getattr(value, "warnings", ())
        if quality is None and hasattr(value, "integrated"):
            quality = getattr(value.integrated, "quality", None)
            warnings = getattr(value.integrated, "warnings", warnings)
        if quality is None and hasattr(value, "main"):
            quality = getattr(value.main, "quality", None)
            warnings = getattr(value.main, "warnings", warnings)
        self.power_quality_label.setText(f"Quality: {getattr(quality, 'value', quality) or '—'}")
        self.power_warnings_label.setText(
            "Warnings: " + (" | ".join(getattr(item, "message", str(item)) for item in warnings) or "—")
        )
        payload = asdict(value) if hasattr(value, "__dataclass_fields__") else {"result": value}
        rows = self._flatten_power_result(payload)
        self.cp_result_table.setRowCount(len(rows))
        for row, (name, result) in enumerate(rows):
            self.cp_result_table.setItem(row, 0, QTableWidgetItem(name))
            self.cp_result_table.setItem(row, 1, QTableWidgetItem(self._format_value(result)))
        self.cp_recalc_status.setText(f"{mode}: расчёт завершён")
        self.cp_recalc_status.setStyleSheet("color: #3ddc97;")
        trace = self._active_frequency_trace(self.repository.get(session_id))
        if trace is not None:
            self._analysis_ready(session_id, trace.trace_id, "power", mode, value)

    @classmethod
    def _flatten_power_result(cls, value: Any, prefix: str = "") -> list[tuple[str, Any]]:
        rows: list[tuple[str, Any]] = []
        if isinstance(value, dict):
            for key, item in value.items():
                rows.extend(cls._flatten_power_result(item, f"{prefix}.{key}" if prefix else str(key)))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                rows.extend(cls._flatten_power_result(item, f"{prefix}[{index}]"))
        else:
            rows.append((prefix or "result", value))
        return rows

    def _channel_power_control_changed(
        self, name: str, value: Any, text: str | None = None
    ) -> None:
        details = {"control": name, "value": value}
        if text is not None:
            details["text"] = text
        self._audit("configuration", "channel_power_control_changed", **details)
        self._mark_channel_power_dirty(f"изменён параметр {name}")

    def _mark_channel_power_dirty(self, reason: str) -> None:
        self._invalidate_channel_power()
        if hasattr(self, "cp_recalc_status"):
            self.cp_recalc_status.setText(
                f"Настройки изменены ({reason}). Нажмите «Рассчитать / пересчитать»."
            )
            self.cp_recalc_status.setStyleSheet("color: #ffbd2e;")
        if hasattr(self, "cp_recalculate_button"):
            self.cp_recalculate_button.setText("Рассчитать / пересчитать")

    @staticmethod
    def _power_spin(value: float, minimum: float = -300.0, maximum: float = 300.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(3)
        spin.setValue(value)
        return spin

    def _set_cp_frequency_values(
        self, start_mhz: float, stop_mhz: float, *, activate_region: bool = True
    ) -> None:
        start_mhz, stop_mhz = sorted((float(start_mhz), float(stop_mhz)))
        self._syncing_channel_frequency = True
        try:
            self.cp_start.setValue(start_mhz)
            self.cp_stop.setValue(stop_mhz)
            self.cp_center.setValue((start_mhz + stop_mhz) / 2.0)
            self.cp_bandwidth.setValue(stop_mhz - start_mhz)
            self.band_start.setValue(start_mhz)
            self.band_stop.setValue(stop_mhz)
        finally:
            self._syncing_channel_frequency = False
        if activate_region:
            self._update_cp_frequency_region()

    def _cp_edges_changed(self) -> None:
        if self._syncing_channel_frequency:
            return
        start, stop = self.cp_start.value(), self.cp_stop.value()
        if stop <= start:
            return
        self._set_cp_frequency_values(start, stop)
        self._mark_channel_power_dirty("изменена частотная полоса")
        self._audit("user", "channel_power_frequency_edges_changed", start_mhz=start, stop_mhz=stop)

    def _cp_center_band_changed(self) -> None:
        if self._syncing_channel_frequency:
            return
        half = self.cp_bandwidth.value() / 2.0
        if half <= 0:
            return
        self._set_cp_frequency_values(self.cp_center.value() - half, self.cp_center.value() + half)
        self._mark_channel_power_dirty("изменены центр или ширина полосы")
        self._audit(
            "user",
            "channel_power_center_band_changed",
            center_mhz=self.cp_center.value(),
            bandwidth_mhz=self.cp_bandwidth.value(),
        )

    def _update_cp_frequency_region(self) -> None:
        session = self.active_session()
        if session is None or self.cp_stop.value() <= self.cp_start.value():
            return
        start_hz, stop_hz = self.cp_start.value() * 1e6, self.cp_stop.value() * 1e6
        if session.frequency_regions:
            region = session.frequency_regions[0]
            region.start_frequency_hz, region.stop_frequency_hz = start_hz, stop_hz
        else:
            session.frequency_regions.append(
                FrequencyRegion(start_frequency_hz=start_hz, stop_frequency_hz=stop_hz)
            )
        self.spectrum_renderer.set_regions(session.frequency_regions)
        self._connect_frequency_regions(session)
        self.waterfall_renderer.set_frequency_region(start_hz, stop_hz)

    def _cp_use_visible_range(self) -> None:
        low, high = self.spectrum_renderer.plot.viewRange()[0]
        self._set_cp_frequency_values(low / 1e6, high / 1e6)
        self._audit("user", "channel_power_use_visible_range", start_hz=low, stop_hz=high)

    def _cp_use_frequency_region(self) -> None:
        session = self.active_session()
        if session and session.frequency_regions:
            region = session.frequency_regions[0]
            self._set_cp_frequency_values(
                region.start_frequency_hz / 1e6, region.stop_frequency_hz / 1e6
            )
            self._audit(
                "user",
                "channel_power_use_frequency_region",
                start_hz=region.start_frequency_hz,
                stop_hz=region.stop_frequency_hz,
            )

    def _cp_reset_frequency(self) -> None:
        session = self.active_session()
        trace = self._active_frequency_trace(session) if session else None
        waterfall = self._active_waterfall(session) if session else None
        if trace is not None:
            self._set_cp_frequency_values(
                trace.start_frequency_hz / 1e6, trace.stop_frequency_hz / 1e6
            )
        elif waterfall is not None:
            self._set_cp_frequency_values(
                waterfall.start_frequency_hz / 1e6, waterfall.stop_frequency_hz / 1e6
            )
        self._audit("user", "channel_power_frequency_reset")

    def _invalidate_channel_power(self) -> None:
        self._channel_power_serial += 1
        self._pending_channel_power_request = None
        if self._channel_power_worker is not None:
            self._channel_power_worker.cancel()

    def _channel_power_context_menu(self, _position: Any) -> None:
        menu = QMenu(self)
        cancel = menu.addAction("Отменить активный расчёт")
        cancel.setEnabled(self._channel_power_worker is not None)
        clear = menu.addAction("Выключить и очистить Channel Power")
        menu.addSeparator()
        clear_all = menu.addAction("Очистить все инструменты анализа")
        chosen = self._exec_context_menu(menu)
        if chosen == cancel and self._channel_power_worker is not None:
            self._channel_power_worker.cancel()
            self._audit("user", "channel_power_cancel_requested")
        elif chosen == clear:
            self.clear_channel_power_tools()
        elif chosen == clear_all:
            self.clear_all_analysis_tools()

    def clear_channel_power_tools(self) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        self._invalidate_channel_power()
        if session is not None and waterfall is not None:
            key = (session.session_id, waterfall.waterfall_id)
            self._channel_power_results.pop(key, None)
            self._activity_overrides.pop(key, None)
            self._manual_noise_ranges.pop(key, None)
        if session is not None:
            session.frequency_regions.clear()
            session.time_regions.clear()
            session.analysis_results = [
                result for result in session.analysis_results
                if result.kind != "Channel Power"
            ]
            self._refresh_measurement_table(session)
        self.spectrum_renderer.set_regions([])
        self.waterfall_renderer.clear_frequency_region()
        self.waterfall_renderer.clear_time_region()
        self.waterfall_renderer.clear_noise_region()
        self.waterfall_renderer.set_event_regions([])
        self.cp_result_table.setRowCount(0)
        self.events_table.setRowCount(0)
        empty = np.empty(0, dtype=np.float64)
        self.cp_raw_curve.setData(x=empty, y=empty)
        self.cp_smooth_curve.setData(x=empty, y=empty)
        self.cp_manual_active_curve.setData(x=empty, y=empty)
        self.cp_manual_inactive_curve.setData(x=empty, y=empty)
        for line in (
            self.cp_threshold_on_line,
            self.cp_threshold_off_line,
            self.cp_idle_line,
        ):
            line.hide()
        self.cp_time_region.hide()
        for region in self._channel_plot_regions:
            self.channel_power_plot.removeItem(region)
        self._channel_plot_regions.clear()
        self.current_frame_measurement.setText("Текущий Channel Power: —")
        self.cp_recalc_status.setText("Channel Power выключен; результатов нет")
        self.cp_recalc_status.setStyleSheet("")
        self._audit("user", "channel_power_tools_cleared")

    def clear_all_analysis_tools(self) -> None:
        session = self.active_session()
        self.clear_channel_power_tools()
        if session is not None:
            result_count = len(session.analysis_results)
            session.analysis_results.clear()
            self._refresh_measurement_table(session)
        else:
            result_count = 0
        self.clear_markers()
        self._audit("user", "all_analysis_tools_cleared", result_count=result_count)

    def cancel_active_operations(self) -> None:
        workers = list(self._workers)
        for worker in workers:
            worker.cancel()
        self.playback_timer.stop()
        self._audit("user", "all_active_operations_cancel_requested", count=len(workers))

    def _cp_time_selection_changed(self) -> None:
        if self._syncing_time_region:
            return
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        index = self._active_spectrogram_index(session)
        if session is None or waterfall is None or index is None or index.frame_count == 0:
            return
        start = min(self.cp_start_frame.value(), self.cp_stop_frame.value()) - 1
        stop = max(self.cp_start_frame.value(), self.cp_stop_frame.value()) - 1
        start = int(np.clip(start, 0, index.frame_count - 1))
        stop = int(np.clip(stop, 0, index.frame_count - 1))
        start_time = float(index.timestamps[start])
        stop_time = float(index.timestamps[stop])
        duration = max(0.0, stop_time - start_time) if np.isfinite(start_time + stop_time) else 0.0
        self.cp_time_info.setText(f"{stop - start + 1:,} кадров · {duration:.6g} s")
        if session.time_regions:
            region = session.time_regions[0]
            region.start_time, region.stop_time = start_time, stop_time
        else:
            session.time_regions.append(TimeRegion(start_time=start_time, stop_time=stop_time))
        start_row = self._frame_to_preview_row(waterfall, index, start)
        stop_row = self._frame_to_preview_row(waterfall, index, stop)
        self.waterfall_renderer.set_time_region(start_row, stop_row, True)
        self._channel_time_origin = self._finite_time_origin(index.timestamps)
        self._syncing_time_region = True
        try:
            self.cp_time_region.setRegion(
                (start_time - self._channel_time_origin, stop_time - self._channel_time_origin)
            )
            self.cp_time_region.show()
        finally:
            self._syncing_time_region = False
        self._audit(
            "user",
            "channel_power_time_selection_changed",
            start_frame=start,
            stop_frame=stop,
            start_time_s=start_time,
            stop_time_s=stop_time,
        )
        self._mark_channel_power_dirty("изменён временной интервал")

    @staticmethod
    def _finite_time_origin(timestamps: np.ndarray) -> float:
        finite = timestamps[np.isfinite(timestamps)]
        return float(finite[0]) if finite.size else 0.0

    def _cp_plot_region_changed(self) -> None:
        if self._syncing_time_region:
            return
        session = self.active_session()
        index = self._active_spectrogram_index(session)
        if index is None or not index.frame_count:
            return
        low, high = sorted(self.cp_time_region.getRegion())
        absolute = np.asarray((low, high)) + self._channel_time_origin
        start = int(np.clip(np.searchsorted(index.timestamps, absolute[0]), 0, index.frame_count - 1))
        stop = int(np.clip(np.searchsorted(index.timestamps, absolute[1]), 0, index.frame_count - 1))
        self._syncing_time_region = True
        try:
            self.cp_start_frame.setValue(start + 1)
            self.cp_stop_frame.setValue(stop + 1)
        finally:
            self._syncing_time_region = False
        self._cp_time_selection_changed()

    def _cp_time_plot_clicked(self, event: Any) -> None:
        if not self.channel_power_plot.sceneBoundingRect().contains(event.scenePos()):
            return
        session = self.active_session()
        index = self._active_spectrogram_index(session)
        if index is None or not index.frame_count:
            return
        point = self.channel_power_plot.getPlotItem().vb.mapSceneToView(event.scenePos())
        target = point.x() + self._channel_time_origin
        frame = int(np.clip(np.searchsorted(index.timestamps, target), 0, index.frame_count - 1))
        self._audit("user", "channel_power_plot_clicked", frame=frame, timestamp_s=target)
        self.time_slider.setValue(frame)

    def _channel_power_request(self) -> tuple[
        MeasurementSession, WaterfallData, SpectrogramIndex, ChannelPowerRequest, np.ndarray
    ] | None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        index = self._active_spectrogram_index(session)
        if session is None or waterfall is None or index is None or not index.frame_count:
            self._show_error("Channel Power", "Сначала дождитесь загрузки индекса waterfall")
            return None
        if self.cp_stop.value() <= self.cp_start.value():
            self._show_error("Channel Power", "Конечная частота должна быть выше начальной")
            return None
        trace = self._active_frequency_trace(session)
        rbw_hz = trace.rbw_hz if trace is not None else None
        semantics = self.cp_semantics.currentData()
        if semantics is None:
            semantics = PowerSemantics.RBW_FILTERED_POWER if rbw_hz else PowerSemantics.UNKNOWN
        else:
            semantics = PowerSemantics(semantics)
        mode = ChannelPowerMode(self.cp_time_mode.currentData())
        start_frame = min(self.cp_start_frame.value(), self.cp_stop_frame.value()) - 1
        stop_frame = max(self.cp_start_frame.value(), self.cp_stop_frame.value()) - 1
        time_start = time_stop = None
        selected_frame = None
        if mode == ChannelPowerMode.CURRENT_FRAME:
            selected_frame = session.current_frame
        elif mode in (ChannelPowerMode.SELECTED_INTERVAL_ALL_FRAMES, ChannelPowerMode.SELECTED_EVENTS):
            time_start = float(index.timestamps[start_frame])
            time_stop = float(index.timestamps[stop_frame])
        noise = self._manual_noise_ranges.get((session.session_id, waterfall.waterfall_id))
        config = ActivityDetectionConfig(
            enabled=self.cp_activity_enabled.isChecked(),
            threshold_mode=ActivityThresholdMode(self.cp_threshold_mode.currentData()),
            absolute_threshold_dbm=self.cp_absolute_threshold.value(),
            threshold_on_offset_db=self.cp_on_offset.value(),
            threshold_off_offset_db=self.cp_off_offset.value(),
            robust_sigma_multiplier=self.cp_robust_sigma.value(),
            idle_percentile=self.cp_idle_percentile.value(),
            smoothing_mode=SmoothingMode(self.cp_smoothing.currentData()),
            smoothing_window_frames=self.cp_smoothing_window.value(),
            min_active_frames=self.cp_min_active.value(),
            min_inactive_frames=self.cp_min_inactive.value(),
            max_gap_frames=self.cp_max_gap.value(),
            merge_gap_frames=self.cp_merge_gap.value(),
            manual_noise_start_s=noise[0] if noise else None,
            manual_noise_stop_s=noise[1] if noise else None,
            use_hysteresis=self.cp_hysteresis.isChecked(),
        )
        request = ChannelPowerRequest(
            session_id=session.session_id,
            trace_id=waterfall.waterfall_id,
            frequency_start_hz=self.cp_start.value() * 1e6,
            frequency_stop_hz=self.cp_stop.value() * 1e6,
            time_start_s=time_start,
            time_stop_s=time_stop,
            mode=mode,
            frame_inclusion=FrameInclusion(self.cp_frame_inclusion.currentData()),
            activity_config=config,
            selected_frame_index=selected_frame,
            power_semantics=semantics,
            rbw_hz=rbw_hz,
            source_revision=self._source_revision(session.source_path),
        )
        key = (session.session_id, waterfall.waterfall_id)
        overrides = self._activity_overrides.get(key)
        if overrides is None or overrides.size != index.frame_count:
            overrides = np.full(index.frame_count, ManualOverride.AUTO, dtype=np.uint8)
            self._activity_overrides[key] = overrides
        return session, waterfall, index, request, overrides.copy()

    @staticmethod
    def _source_revision(path: Path) -> str:
        try:
            stat = path.stat()
        except OSError:
            return "unavailable"
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    def run_time_gated_channel_power(self) -> None:
        snapshot = self._channel_power_request()
        if snapshot is None:
            return
        session, waterfall, index, request, overrides = snapshot
        self._audit(
            "user",
            "time_gated_channel_power_started",
            waterfall_id=waterfall.waterfall_id,
            frequency_start_hz=request.frequency_start_hz,
            frequency_stop_hz=request.frequency_stop_hz,
            mode=request.mode.value,
            frame_inclusion=request.frame_inclusion.value,
            power_semantics=request.power_semantics.value,
            selected_frame_index=request.selected_frame_index,
            time_start_s=request.time_start_s,
            time_stop_s=request.time_stop_s,
            activity_enabled=request.activity_config.enabled,
            threshold_mode=request.activity_config.threshold_mode.value,
            absolute_threshold_dbm=request.activity_config.absolute_threshold_dbm,
            threshold_on_offset_db=request.activity_config.threshold_on_offset_db,
            threshold_off_offset_db=request.activity_config.threshold_off_offset_db,
            robust_sigma_multiplier=request.activity_config.robust_sigma_multiplier,
            idle_percentile=request.activity_config.idle_percentile,
            smoothing_mode=request.activity_config.smoothing_mode.value,
            smoothing_window_frames=request.activity_config.smoothing_window_frames,
            min_active_frames=request.activity_config.min_active_frames,
            min_inactive_frames=request.activity_config.min_inactive_frames,
            max_gap_frames=request.activity_config.max_gap_frames,
            merge_gap_frames=request.activity_config.merge_gap_frames,
            use_hysteresis=request.activity_config.use_hysteresis,
        )
        self._channel_power_serial += 1
        serial = self._channel_power_serial
        pending = (session, waterfall, index, request, overrides, serial)
        if self._channel_power_worker is not None:
            self._channel_power_worker.cancel()
            self._pending_channel_power_request = pending
            self.cp_recalc_status.setText(
                "Предыдущий расчёт отменяется; последний запрос поставлен в очередь…"
            )
            self.cp_recalc_status.setStyleSheet("color: #ffbd2e;")
            self._audit(
                "program",
                "time_gated_channel_power_queued",
                waterfall_id=waterfall.waterfall_id,
                request_serial=serial,
            )
            return
        self._start_channel_power_request(*pending)

    def _start_channel_power_request(
        self,
        session: MeasurementSession,
        waterfall: WaterfallData,
        index: SpectrogramIndex,
        request: ChannelPowerRequest,
        overrides: np.ndarray,
        serial: int,
    ) -> None:
        if serial != self._channel_power_serial or session.session_id != self.active_session_id:
            return
        worker = TaskWorker(
            _analyze_time_gated_waterfall,
            self.time_gated_service,
            session.source_path,
            self._spectrogram_info(waterfall),
            waterfall.frequencies_hz,
            request,
            overrides,
            index,
            pass_progress=True,
            pass_cancel=True,
        )
        self._channel_power_worker = worker
        self._channel_power_session_id = session.session_id
        worker.signals.result.connect(
            lambda result: self._time_gated_ready(
                session.session_id, waterfall.waterfall_id, serial, result
            )
        )
        worker.signals.finished.connect(
            lambda worker=worker: self._channel_power_worker_finished(worker)
        )
        self.cp_recalc_status.setText("Расчёт выполняется…")
        self.cp_recalc_status.setStyleSheet("color: #35c6ff;")
        self._set_busy(True, "Channel Power по времени…")
        self._start_worker(worker)

    def _channel_power_worker_finished(self, worker: TaskWorker) -> None:
        if self._channel_power_worker is not worker:
            return
        self._channel_power_worker = None
        self._channel_power_session_id = None
        pending = self._pending_channel_power_request
        self._pending_channel_power_request = None
        if pending is not None:
            QTimer.singleShot(0, lambda pending=pending: self._start_channel_power_request(*pending))

    def _time_gated_ready(
        self,
        session_id: str,
        waterfall_id: str,
        serial: int,
        result: TimeGatedChannelPowerResult,
    ) -> None:
        if serial != self._channel_power_serial:
            return
        key = (session_id, waterfall_id)
        self._channel_power_results[key] = result
        if session_id != self.active_session_id:
            return
        session = self.repository.get(session_id)
        self.power_quality_label.setText(
            f"Quality: {result.calculation_quality.value}"
        )
        self.power_warnings_label.setText(
            "Warnings: " + (" | ".join(result.warnings) or "—")
        )
        self._populate_time_gated_results(result)
        self._render_channel_power_time(result)
        self._populate_events(result)
        self._sync_current_frame_measurement()
        self._persist_channel_power_state(session, waterfall_id)
        self.cp_recalc_status.setText(
            "Рассчитано. Следующий пересчёт выполняется только кнопкой или явной ручной правкой."
        )
        self.cp_recalc_status.setStyleSheet("color: #3ddc97;")
        self.cp_recalculate_button.setText("Пересчитать")
        self._audit(
            "program",
            "time_gated_channel_power_completed",
            waterfall_id=waterfall_id,
            valid_frames=result.frame_count_valid,
            events=len(result.events),
            duty_cycle_percent=result.duty_cycle_percent,
            quality=result.calculation_quality.value,
        )
        LOGGER.info(
            "Channel Power(t): %d кадров, %d событий, duty cycle %.3f%%",
            result.frame_count_valid, len(result.events), result.duty_cycle_percent,
        )

    def _populate_time_gated_results(self, result: TimeGatedChannelPowerResult) -> None:
        idle = result.activity.idle_estimate
        values = (
            ("Frequency", f"{result.request.frequency_start_hz / 1e6:.6f}–{result.request.frequency_stop_hz / 1e6:.6f} MHz"),
            ("Time / mode", result.request.mode.value),
            ("Frames", result.request.frame_inclusion.value),
            ("Active Mean Channel Power", self._dbm(result.active_mean_power_dbm)),
            ("Long-Term Mean Channel Power", self._dbm(result.long_term_mean_power_dbm)),
            ("Maximum Frame Channel Power", self._dbm(result.maximum_frame_power_dbm)),
            ("Minimum Active Channel Power", self._dbm(result.minimum_active_power_dbm)),
            ("Idle Channel Power", self._dbm(result.idle_mean_power_dbm)),
            ("Noise-Corrected Active Power", self._dbm(result.noise_corrected_active_power_dbm)),
            ("Duty Cycle", f"{result.duty_cycle_percent:.6g} %"),
            ("Active Duration", f"{result.active_duration_s:.9g} s"),
            ("Selected Duration", f"{result.selected_duration_s:.9g} s"),
            ("Event Count", str(len(result.events))),
            ("Valid Frame Count", f"{result.frame_count_valid:,}"),
            ("Threshold ON", self._dbm(result.activity.threshold_on_dbm)),
            ("Threshold OFF", self._dbm(result.activity.threshold_off_dbm)),
            ("Idle Level", self._dbm(idle.median_idle_dbm if idle else None)),
            ("Calculation Quality", result.calculation_quality.value),
            ("Warnings", " | ".join(result.warnings) or "—"),
        )
        self.cp_result_table.setRowCount(len(values))
        for row, (name, value) in enumerate(values):
            self.cp_result_table.setItem(row, 0, QTableWidgetItem(name))
            self.cp_result_table.setItem(row, 1, QTableWidgetItem(value))

    @staticmethod
    def _dbm(value: float | None) -> str:
        return "—" if value is None or not np.isfinite(value) else f"{value:.6f} dBm"

    def _render_channel_power_time(self, result: TimeGatedChannelPowerResult) -> None:
        series = result.series
        self._channel_time_origin = self._finite_time_origin(series.timestamps_s)
        x = series.timestamps_s - self._channel_time_origin
        self.cp_raw_curve.setData(x=x, y=series.power_dbm, connect="finite")
        self.cp_smooth_curve.setData(x=x, y=result.activity.smoothed_power_dbm, connect="finite")
        manual = result.activity.manual_override_mask
        active_manual = manual == ManualOverride.FORCE_ACTIVE
        inactive_manual = manual == ManualOverride.FORCE_INACTIVE
        self.cp_manual_active_curve.setData(
            x=x[active_manual], y=result.activity.smoothed_power_dbm[active_manual]
        )
        self.cp_manual_inactive_curve.setData(
            x=x[inactive_manual], y=result.activity.smoothed_power_dbm[inactive_manual]
        )
        for line, value in (
            (self.cp_threshold_on_line, result.activity.threshold_on_dbm),
            (self.cp_threshold_off_line, result.activity.threshold_off_dbm),
            (self.cp_idle_line, result.activity.idle_estimate.median_idle_dbm if result.activity.idle_estimate else None),
        ):
            line.setVisible(bool(value is not None and np.isfinite(value)))
            if value is not None and np.isfinite(value):
                line.setValue(value)
        for region in self._channel_plot_regions:
            self.channel_power_plot.removeItem(region)
        self._channel_plot_regions.clear()
        for event in result.events:
            region = pg.LinearRegionItem(
                (event.start_time_s - self._channel_time_origin, event.stop_time_s - self._channel_time_origin),
                movable=False, brush=pg.mkBrush("#3ddc9720"), pen=pg.mkPen("#3ddc9760")
            )
            region.setZValue(-10)
            self.channel_power_plot.addItem(region)
            self._channel_plot_regions.append(region)
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        index = self._active_spectrogram_index(session)
        if waterfall is not None and index is not None:
            self.waterfall_renderer.set_event_regions([
                (
                    self._frame_to_preview_row(waterfall, index, event.start_frame_index),
                    self._frame_to_preview_row(waterfall, index, event.stop_frame_index),
                    event.manually_edited,
                )
                for event in result.events
            ])
        self._sync_channel_current_line()
        self.channel_power_plot.enableAutoRange()

    def _populate_events(self, result: TimeGatedChannelPowerResult) -> None:
        self.events_table.setRowCount(len(result.events))
        for row, event in enumerate(result.events):
            duty = event.duration_s / max(result.selected_duration_s, np.finfo(float).eps) * 100.0
            values = (
                event.event_id[:8], f"{event.start_time_s:.9f}", f"{event.stop_time_s:.9f}",
                f"{event.duration_s:.9g}", f"{event.mean_power_dbm:.6f}",
                f"{event.max_power_dbm:.6f}", f"{event.min_power_dbm:.6f}",
                str(event.active_frame_count), f"{duty:.4f}%",
                "Да" if event.manually_edited else "Нет", "Active",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(ROLE_OBJECT, event)
                self.events_table.setItem(row, column, item)

    def _sync_channel_current_line(self) -> None:
        session = self.active_session()
        index = self._active_spectrogram_index(session)
        if session is None or index is None or not index.frame_count:
            return
        frame = int(np.clip(session.current_frame, 0, index.frame_count - 1))
        timestamp = float(index.timestamps[frame])
        if np.isfinite(timestamp):
            self.cp_current_line.setValue(timestamp - self._channel_time_origin)

    def _event_at_row(self, row: int) -> Any | None:
        item = self.events_table.item(row, 0)
        return item.data(ROLE_OBJECT) if item is not None else None

    def _event_double_clicked(self, row: int, _column: int) -> None:
        event = self._event_at_row(row)
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        index = self._active_spectrogram_index(session)
        if event is None or waterfall is None or index is None:
            return
        self.time_slider.setValue(event.start_frame_index)
        self.cp_start_frame.setValue(event.start_frame_index + 1)
        self.cp_stop_frame.setValue(event.stop_frame_index + 1)
        low = event.start_time_s - self._channel_time_origin
        high = event.stop_time_s - self._channel_time_origin
        padding = max((high - low) * 0.05, 1e-9)
        self.channel_power_plot.setXRange(low - padding, high + padding, padding=0)
        self.waterfall_renderer.plot.setYRange(
            self._frame_to_preview_row(waterfall, index, event.start_frame_index),
            self._frame_to_preview_row(waterfall, index, event.stop_frame_index),
            padding=0.03,
        )

    def _event_context_menu(self, position: Any) -> None:
        selected_rows = sorted({index.row() for index in self.events_table.selectedIndexes()})
        if not selected_rows:
            row = self.events_table.rowAt(position.y())
            if row >= 0:
                selected_rows = [row]
        events = [event for row in selected_rows if (event := self._event_at_row(row)) is not None]
        menu = QMenu(self.events_table)
        mark_active = menu.addAction("Mark Active")
        mark_inactive = menu.addAction("Mark Inactive / Delete Event")
        split_event = menu.addAction("Split Event")
        merge_events = menu.addAction("Merge Selected Events")
        for action in (mark_active, mark_inactive, split_event, merge_events):
            action.setEnabled(bool(events))
        menu.addSeparator()
        zoom_event = menu.addAction("Zoom to Event")
        export_event = menu.addAction("Export Event(s)")
        zoom_event.setEnabled(bool(events))
        export_event.setEnabled(bool(events))
        menu.addSeparator()
        clear_events = menu.addAction("Выключить и очистить Channel Power")
        chosen = self._exec_context_menu(menu)
        if chosen is None:
            return
        if chosen == clear_events:
            self.clear_channel_power_tools()
            return
        if not events:
            return
        start = min(event.start_frame_index for event in events)
        stop = max(event.stop_frame_index for event in events)
        if chosen == mark_active or chosen == merge_events:
            self._set_manual_override(ManualOverride.FORCE_ACTIVE, (start, stop))
        elif chosen == mark_inactive:
            self._set_manual_override(ManualOverride.FORCE_INACTIVE, (start, stop))
        elif chosen == split_event:
            middle = (start + stop) // 2
            self._set_manual_override(ManualOverride.FORCE_INACTIVE, (middle, middle))
        elif chosen == zoom_event:
            self._event_double_clicked(selected_rows[0], 0)
        elif chosen == export_event:
            self.export_time_gated_events()

    def _set_manual_override(
        self,
        value: ManualOverride,
        frame_range: tuple[int, int] | None = None,
    ) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        index = self._active_spectrogram_index(session)
        if session is None or waterfall is None or index is None:
            return
        key = (session.session_id, waterfall.waterfall_id)
        override = self._activity_overrides.get(key)
        if override is None or override.size != index.frame_count:
            override = np.full(index.frame_count, ManualOverride.AUTO, dtype=np.uint8)
            self._activity_overrides[key] = override
        start, stop = frame_range or (
            min(self.cp_start_frame.value(), self.cp_stop_frame.value()) - 1,
            max(self.cp_start_frame.value(), self.cp_stop_frame.value()) - 1,
        )
        start = int(np.clip(start, 0, index.frame_count - 1))
        stop = int(np.clip(stop, 0, index.frame_count - 1))
        override[start : stop + 1] = value
        self._audit(
            "user",
            "activity_manual_override_set",
            value=value.value,
            start_frame=start,
            stop_frame=stop,
        )
        self.run_time_gated_channel_power()

    def _clear_manual_overrides(self) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if session is None or waterfall is None:
            return
        override = self._activity_overrides.get((session.session_id, waterfall.waterfall_id))
        if override is not None:
            override.fill(ManualOverride.AUTO)
        self._audit("user", "activity_manual_overrides_cleared")
        self.run_time_gated_channel_power()

    def _cp_select_noise_interval(self) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        index = self._active_spectrogram_index(session)
        if session is None or waterfall is None or index is None:
            return
        start = min(self.cp_start_frame.value(), self.cp_stop_frame.value()) - 1
        stop = max(self.cp_start_frame.value(), self.cp_stop_frame.value()) - 1
        self._manual_noise_ranges[(session.session_id, waterfall.waterfall_id)] = (
            float(index.timestamps[start]), float(index.timestamps[stop])
        )
        self._audit(
            "user",
            "channel_power_noise_interval_selected",
            start_frame=start,
            stop_frame=stop,
        )
        self.waterfall_renderer.set_noise_region(
            self._frame_to_preview_row(waterfall, index, start),
            self._frame_to_preview_row(waterfall, index, stop),
            True,
        )
        self.cp_threshold_mode.setCurrentIndex(
            self.cp_threshold_mode.findData(ActivityThresholdMode.MANUAL_NOISE_REGION)
        )
        self.run_time_gated_channel_power()

    def _cp_reset_defaults(self) -> None:
        self.cp_activity_enabled.setChecked(True)
        self.cp_threshold_mode.setCurrentIndex(0)
        self.cp_absolute_threshold.setValue(-65.0)
        self.cp_on_offset.setValue(10.0)
        self.cp_off_offset.setValue(6.0)
        self.cp_idle_percentile.setValue(20.0)
        self.cp_robust_sigma.setValue(6.0)
        self.cp_smoothing.setCurrentIndex(0)
        self.cp_smoothing_window.setValue(3)
        self.cp_min_active.setValue(2)
        self.cp_min_inactive.setValue(2)
        self.cp_max_gap.setValue(1)
        self.cp_merge_gap.setValue(1)
        self.cp_hysteresis.setChecked(True)

    def _persist_channel_power_state(self, session: MeasurementSession, waterfall_id: str) -> None:
        key = (session.session_id, waterfall_id)
        override = self._activity_overrides.get(key)
        runs: list[list[int]] = []
        if override is not None:
            boundaries = np.r_[0, np.flatnonzero(np.diff(override)) + 1, override.size]
            for start, stop in zip(boundaries[:-1], boundaries[1:]):
                value = int(override[int(start)])
                if value != ManualOverride.AUTO:
                    runs.append([int(start), int(stop - 1), value])
        noise = self._manual_noise_ranges.get(key)
        session.display_state["time_gated_channel_power"] = {
            "waterfall_id": waterfall_id,
            "frequency_start_mhz": self.cp_start.value(),
            "frequency_stop_mhz": self.cp_stop.value(),
            "time_mode": ChannelPowerMode(self.cp_time_mode.currentData()).value,
            "frame_inclusion": FrameInclusion(self.cp_frame_inclusion.currentData()).value,
            "start_frame": self.cp_start_frame.value(),
            "stop_frame": self.cp_stop_frame.value(),
            "activity_enabled": self.cp_activity_enabled.isChecked(),
            "threshold_mode": ActivityThresholdMode(self.cp_threshold_mode.currentData()).value,
            "absolute_threshold_dbm": self.cp_absolute_threshold.value(),
            "threshold_on_offset_db": self.cp_on_offset.value(),
            "threshold_off_offset_db": self.cp_off_offset.value(),
            "idle_percentile": self.cp_idle_percentile.value(),
            "robust_sigma_multiplier": self.cp_robust_sigma.value(),
            "smoothing_mode": SmoothingMode(self.cp_smoothing.currentData()).value,
            "smoothing_window_frames": self.cp_smoothing_window.value(),
            "min_active_frames": self.cp_min_active.value(),
            "min_inactive_frames": self.cp_min_inactive.value(),
            "max_gap_frames": self.cp_max_gap.value(),
            "merge_gap_frames": self.cp_merge_gap.value(),
            "use_hysteresis": self.cp_hysteresis.isChecked(),
            "manual_noise_range": list(noise) if noise else None,
            "manual_override_runs": runs,
            "measurement_mode": self.power_measurement_mode.currentData(),
            "measurement_source": self.power_source.currentData(),
            "profile": self.power_profile.currentData(),
            "obw_percent": self.power_obw_percent.value(),
            "x_db": self.power_xdb.value(),
            "adjacent_pairs": self.power_adjacent_pairs.value(),
            "harmonic_count": self.power_harmonics.value(),
            "sem_limit_dbm": self.power_sem_limit.value(),
            "spurious_min_dbm": self.power_spur_level.value(),
            "spurious_prominence_db": self.power_spur_prominence.value(),
            "spurious_distance_mhz": self.power_spur_distance.value(),
            "spurious_max_count": self.power_spur_count.value(),
            "regions": [
                [
                    self.power_regions_table.item(row, column).text()
                    if self.power_regions_table.item(row, column) is not None else ""
                    for column in range(self.power_regions_table.columnCount())
                ]
                for row in range(self.power_regions_table.rowCount())
            ],
        }

    def _restore_channel_power_state(
        self,
        session: MeasurementSession,
        waterfall_id: str,
        index: SpectrogramIndex,
    ) -> None:
        state = session.display_state.get("time_gated_channel_power")
        if not isinstance(state, dict) or state.get("waterfall_id") not in (None, waterfall_id):
            if self.cp_stop.value() <= self.cp_start.value():
                self._cp_reset_frequency()
            self.cp_start_frame.setValue(1)
            self.cp_stop_frame.setValue(max(1, index.frame_count))
            self._cp_time_selection_changed()
            return
        self._set_cp_frequency_values(
            float(state.get("frequency_start_mhz", self.cp_start.value())),
            float(state.get("frequency_stop_mhz", self.cp_stop.value())),
        )
        combo_values = (
            (self.cp_time_mode, ChannelPowerMode, state.get("time_mode")),
            (self.cp_frame_inclusion, FrameInclusion, state.get("frame_inclusion")),
            (self.cp_threshold_mode, ActivityThresholdMode, state.get("threshold_mode")),
            (self.cp_smoothing, SmoothingMode, state.get("smoothing_mode")),
        )
        for combo, enum_type, raw_value in combo_values:
            try:
                combo_index = combo.findData(enum_type(raw_value))
            except (TypeError, ValueError):
                combo_index = -1
            if combo_index >= 0:
                combo.setCurrentIndex(combo_index)
        self.cp_activity_enabled.setChecked(bool(state.get("activity_enabled", True)))
        for spin, key in (
            (self.cp_absolute_threshold, "absolute_threshold_dbm"),
            (self.cp_on_offset, "threshold_on_offset_db"),
            (self.cp_off_offset, "threshold_off_offset_db"),
            (self.cp_idle_percentile, "idle_percentile"),
            (self.cp_robust_sigma, "robust_sigma_multiplier"),
            (self.cp_smoothing_window, "smoothing_window_frames"),
            (self.cp_min_active, "min_active_frames"),
            (self.cp_min_inactive, "min_inactive_frames"),
            (self.cp_max_gap, "max_gap_frames"),
            (self.cp_merge_gap, "merge_gap_frames"),
        ):
            if key in state:
                spin.setValue(state[key])
        self.cp_hysteresis.setChecked(bool(state.get("use_hysteresis", True)))
        for combo, key in (
            (self.power_measurement_mode, "measurement_mode"),
            (self.power_source, "measurement_source"),
            (self.power_profile, "profile"),
        ):
            combo_index = combo.findData(state.get(key))
            if combo_index >= 0:
                combo.setCurrentIndex(combo_index)
        for spin, key in (
            (self.power_obw_percent, "obw_percent"),
            (self.power_xdb, "x_db"),
            (self.power_adjacent_pairs, "adjacent_pairs"),
            (self.power_harmonics, "harmonic_count"),
            (self.power_sem_limit, "sem_limit_dbm"),
            (self.power_spur_level, "spurious_min_dbm"),
            (self.power_spur_prominence, "spurious_prominence_db"),
            (self.power_spur_distance, "spurious_distance_mhz"),
            (self.power_spur_count, "spurious_max_count"),
        ):
            if key in state:
                spin.setValue(state[key])
        regions = state.get("regions")
        if isinstance(regions, list):
            self.power_regions_table.setRowCount(0)
            for values in regions:
                if not isinstance(values, list):
                    continue
                row = self.power_regions_table.rowCount()
                self.power_regions_table.insertRow(row)
                for column, value in enumerate(values[: self.power_regions_table.columnCount()]):
                    self.power_regions_table.setItem(row, column, QTableWidgetItem(str(value)))
        self.cp_start_frame.setValue(
            int(np.clip(state.get("start_frame", 1), 1, max(1, index.frame_count)))
        )
        self.cp_stop_frame.setValue(
            int(np.clip(state.get("stop_frame", index.frame_count), 1, max(1, index.frame_count)))
        )
        key = (session.session_id, waterfall_id)
        override = np.full(index.frame_count, ManualOverride.AUTO, dtype=np.uint8)
        for run in state.get("manual_override_runs", []):
            try:
                start, stop, value = map(int, run)
                override[max(0, start) : min(index.frame_count, stop + 1)] = ManualOverride(value)
            except (TypeError, ValueError):
                continue
        self._activity_overrides[key] = override
        noise = state.get("manual_noise_range")
        if isinstance(noise, list) and len(noise) == 2:
            self._manual_noise_ranges[key] = (float(noise[0]), float(noise[1]))
            waterfall = session.waterfalls.get(waterfall_id)
            if waterfall is not None:
                start = int(np.clip(np.searchsorted(index.timestamps, float(noise[0])), 0, index.frame_count - 1))
                stop = int(np.clip(np.searchsorted(index.timestamps, float(noise[1])), 0, index.frame_count - 1))
                self.waterfall_renderer.set_noise_region(
                    self._frame_to_preview_row(waterfall, index, start),
                    self._frame_to_preview_row(waterfall, index, stop),
                    True,
                )
        self._cp_time_selection_changed()
        if session.display_state.get("source_changed_since_workspace"):
            LOGGER.warning(
                "Исходный DFL изменён после сохранения workspace; результаты Channel Power не восстановлены"
            )

    def _create_actions(self) -> None:
        self.open_action = QAction("Открыть DFL…", self, shortcut="Ctrl+O", triggered=self.open_files)
        self.open_live_action = QAction("Открыть Live SDR…", self, shortcut="Ctrl+L", triggered=self.open_live_sdr)
        self.close_action = QAction("Закрыть сессию", self, triggered=self.close_active_session)
        self.open_workspace_action = QAction("Открыть workspace…", self, shortcut="Ctrl+Shift+O", triggered=self.open_workspace)
        self.save_workspace_action = QAction("Сохранить workspace", self, shortcut="Ctrl+S", triggered=self.save_workspace)
        self.save_workspace_as_action = QAction("Сохранить workspace как…", self, triggered=lambda: self.save_workspace(True))
        self.exit_action = QAction("Выход", self, shortcut="Alt+F4", triggered=self.close)
        self.add_marker_action = QAction("Добавить маркер", self, shortcut="M", triggered=self.add_marker)
        self.peak_action = QAction("Peak Search", self, shortcut="P", triggered=self.add_peak_marker)
        self.delta_action = QAction("Delta Marker", self, triggered=self.add_delta_marker)
        self.region_action = QAction("Выделить полосу", self, triggered=self._update_region)
        self.clear_tools_action = QAction(
            "Очистить инструменты анализа", self, triggered=self.clear_all_analysis_tools
        )
        self.cancel_operations_action = QAction(
            "Отменить активные операции", self, triggered=self.cancel_active_operations
        )
        self.auto_scale_action = QAction("Auto Scale", self, shortcut="A", triggered=self._auto_scale)
        self.reset_zoom_action = QAction(
            "Reset Zoom", self, shortcut="R", triggered=self._reset_zoom
        )
        self.view_settings_action = QAction(
            "Zoom / диапазон…", self, shortcut="Z", triggered=self._show_view_settings
        )
        self.frame_navigation_settings_action = QAction(
            "Настройки навигации…", self, triggered=self._show_frame_navigation_settings
        )
        self.play_action = QAction("Воспроизведение", self, shortcut="Space", triggered=self._toggle_play)
        for action in (
            self.open_action,
            self.close_action,
            self.open_workspace_action,
            self.save_workspace_action,
            self.save_workspace_as_action,
            self.exit_action,
            self.add_marker_action,
            self.peak_action,
            self.delta_action,
            self.region_action,
            self.clear_tools_action,
            self.cancel_operations_action,
            self.auto_scale_action,
            self.reset_zoom_action,
            self.view_settings_action,
            self.frame_navigation_settings_action,
            self.play_action,
        ):
            action.triggered.connect(
                lambda checked=False, action=action: self._audit(
                    "user",
                    "action_triggered",
                    action=action.text(),
                    checked=bool(checked),
                )
            )

    def _create_menus_and_toolbar(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")
        file_menu.addActions([self.open_action, self.open_live_action, self.close_action])
        file_menu.addSeparator()
        file_menu.addActions([self.open_workspace_action, self.save_workspace_action, self.save_workspace_as_action])
        file_menu.addSeparator()
        export_menu = file_menu.addMenu("Экспорт")
        self._populate_export_menu(export_menu)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        view_menu = self.menuBar().addMenu("Вид")
        view_menu.addAction(self.auto_scale_action)
        view_menu.addAction(self.reset_zoom_action)
        view_menu.addAction(self.view_settings_action)
        view_menu.addAction(self.frame_navigation_settings_action)
        docks_menu = view_menu.addMenu("Панели")
        for dock in self.findChildren(QDockWidget):
            docks_menu.addAction(dock.toggleViewAction())
        trace_menu = self.menuBar().addMenu("Трасса")
        trace_menu.addAction(self.auto_scale_action)
        marker_menu = self.menuBar().addMenu("Маркеры")
        marker_menu.addActions([self.add_marker_action, self.peak_action, self.delta_action])
        measurement_menu = self.menuBar().addMenu("Измерения")
        measurement_menu.addAction(self.region_action)
        for text, slot in (
            ("Channel Power", self.measure_channel_power), ("OBW99", self.measure_obw),
            ("Noise Floor", self.measure_noise), ("SNR", self.measure_snr), ("ACPR/ACLR", self.measure_acpr),
        ):
            measurement_menu.addAction(text, slot)
        measurement_menu.addSeparator()
        measurement_menu.addAction(self.clear_tools_action)
        measurement_menu.addAction(self.cancel_operations_action)
        playback_menu = self.menuBar().addMenu("Воспроизведение")
        playback_menu.addAction(self.play_action)
        playback_menu.addAction("Пауза", self.pause)
        playback_menu.addAction("Стоп", self.stop)
        export_top = self.menuBar().addMenu("Экспорт")
        self._populate_export_menu(export_top)

        toolbar = QToolBar("Основная панель", self)
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(True)
        toolbar.addActions([self.open_action, self.open_live_action, self.save_workspace_action])
        toolbar.addSeparator()
        toolbar.addActions([
            self.auto_scale_action,
            self.reset_zoom_action,
            self.view_settings_action,
            self.add_marker_action,
            self.peak_action,
            self.region_action,
            self.clear_tools_action,
        ])
        toolbar.addSeparator()
        toolbar.addAction(self.play_action)
        self.addToolBar(toolbar)

    def _populate_export_menu(self, menu: Any) -> None:
        menu.addAction("Активная трасса CSV…", self.export_active_trace)
        menu.addAction("Все включённые трассы CSV…", self.export_all_traces)
        menu.addAction("Сессия NPZ…", self.export_npz)
        menu.addAction("Предпросмотр waterfall CSV…", self.export_waterfall)
        menu.addAction("Waterfall GIF…", lambda: self.export_animation("gif"))
        menu.addAction("Waterfall MP4…", lambda: self.export_animation("mp4"))
        menu.addAction("Маркеры CSV…", self.export_markers)
        menu.addAction("Результаты CSV…", self.export_results)
        menu.addSeparator()
        menu.addAction("Channel Power Summary CSV…", self.export_time_gated_summary)
        menu.addAction("Channel Power Frames CSV…", self.export_time_gated_frames)
        menu.addAction("Channel Power Events CSV…", self.export_time_gated_events)
        menu.addAction("Channel Power JSON…", self.export_time_gated_json)
        menu.addSeparator()
        menu.addAction("Heatmap PNG…", self.export_heatmap_png)
        menu.addAction("Heatmap CSV…", self.export_heatmap_csv)
        menu.addAction("Heatmap NPZ…", self.export_heatmap_npz)
        menu.addAction("Heatmap JSON…", self.export_heatmap_json)
        menu.addSeparator()
        menu.addAction("Метаданные JSON…", self.export_metadata)
        menu.addAction("Снимок окна PNG…", self.export_screenshot)

    def _create_status_bar(self) -> None:
        status = QStatusBar(self)
        self.setStatusBar(status)
        self.status_file = QLabel("Файл не открыт")
        self.status_info = QLabel("")
        self.status_cursor = QLabel("")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        status.addWidget(self.status_file, 2)
        status.addPermanentWidget(self.status_info)
        status.addPermanentWidget(self.status_cursor)
        status.addPermanentWidget(self.progress)

    # --- loading and sessions -------------------------------------------
    def open_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Открыть DFL", "", "DFL (*.dfl);;Все файлы (*)")
        self._audit("user", "open_files_dialog_completed", selected_count=len(paths))
        for path in paths:
            self.load_file(Path(path))

    def load_file(self, path: Path, workspace_state: dict[str, Any] | None = None) -> None:
        path = path.resolve()
        existing = self.repository.find_by_path(path)
        if existing is not None:
            self._audit("user", "existing_file_activated", path=str(path))
            self.set_active_session(existing.session_id)
            return
        if not path.is_file():
            self._audit("program", "file_open_failed", path=str(path), reason="not_found")
            self._show_error("Файл не найден", f"Исходный DFL недоступен:\n{path}")
            return
        self._audit("user", "dfl_open_requested", path=str(path))
        LOGGER.info("Открытие DFL: %s", path)
        self._set_busy(True, "Чтение DFL…")

        def parse_and_adapt() -> MeasurementSession:
            document = DflParser().parse(path)
            return self.adapter.adapt(document)

        worker = TaskWorker(parse_and_adapt)
        worker.signals.result.connect(lambda value: self._session_loaded(value, workspace_state))
        self._start_worker(worker)

    def _session_loaded(self, session: MeasurementSession, workspace_state: dict[str, Any] | None) -> None:
        if workspace_state:
            apply_workspace_session(session, workspace_state)
        self.repository.add(session)
        self.active_session_id = session.session_id
        self._refresh_tree()
        self.set_active_session(session.session_id)
        elapsed = ""
        LOGGER.info(
            "DFL прочитан: %s; трасс %d; waterfall %d%s",
            session.source_path.name, len(session.traces), len(session.waterfalls), elapsed,
        )
        self._audit(
            "program",
            "dfl_loaded",
            session_id=session.session_id,
            source_path=str(session.source_path),
            trace_count=len(session.traces),
            waterfall_count=len(session.waterfalls),
        )
        if session.active_waterfall_id:
            self._load_waterfall_preview(session, session.active_waterfall_id)

    def _load_waterfall_preview(self, session: MeasurementSession, waterfall_id: str) -> None:
        waterfall = session.waterfalls[waterfall_id]
        info = self._spectrogram_info(waterfall)
        self._audit(
            "program",
            "waterfall_preview_started",
            waterfall_id=waterfall_id,
            frames=info.line_count,
            points=info.point_count,
        )
        self._set_busy(True, "Потоковое чтение waterfall…")
        worker = TaskWorker(
            load_spectrogram_preview_with_index,
            session.source_path,
            info,
            max_rows=1600,
            pass_progress=True,
            pass_cancel=True,
        )
        worker.signals.result.connect(lambda preview: self._preview_loaded(session.session_id, preview))
        self._start_worker(worker)

    @staticmethod
    def _spectrogram_info(waterfall: WaterfallData) -> SpectrogramInfo:
        metadata = waterfall.metadata
        return SpectrogramInfo(
            key=waterfall.waterfall_id,
            title=waterfall.name,
            mode=str(metadata.get("mode", "Unknown")),
            measurement=str(metadata.get("measurement", "Unknown")),
            measurement_type=str(metadata.get("measurement_type", "Unknown")),
            source_stream=waterfall.source_stream,
            line_count=waterfall.line_count,
            point_count=waterfall.point_count,
            start_hz=waterfall.start_frequency_hz,
            stop_hz=waterfall.stop_frequency_hz,
            oldest_timestamp=metadata.get("oldest_timestamp"),
            newest_timestamp=metadata.get("newest_timestamp"),
            y_unit=waterfall.unit,
            history_depth=int(metadata.get("history_depth", waterfall.line_count)),
            metadata=dict(metadata),
        )

    def _preview_loaded(
        self, session_id: str, payload: tuple[SpectrogramPreview, SpectrogramIndex]
    ) -> None:
        preview, index = payload
        session = self.repository.get(session_id)
        waterfall = self.adapter.attach_preview(session, preview)
        self._spectrogram_indexes[(session_id, waterfall.waterfall_id)] = index
        mode = str(waterfall.metadata.get("mode", ""))
        timing = session.acquisition_timing.get(mode)
        if timing is not None:
            timing.recorded_period_statistics = compute_frame_period_statistics(index)
            timing.deadline_source = (
                "instrument_settings_and_timestamps"
                if timing.instrument_sweep_time_s is not None and timing.t_recorded_s is not None
                else "timestamps" if timing.t_recorded_s is not None
                else timing.deadline_source
            )
            if timing.t_deadline_s is None and timing.quality == MeasurementQuality.EXACT:
                timing.quality = MeasurementQuality.UNKNOWN
                timing.warnings.append(
                    MeasurementWarning(
                        "timing_deadline_unknown",
                        "Не удалось определить deadline из приборных настроек или timestamps",
                    )
                )
        reader_key = (session_id, waterfall.waterfall_id)
        previous_reader = self._frame_readers.pop(reader_key, None)
        if previous_reader is not None:
            previous_reader.close()
        self._frame_readers[reader_key] = SpectrogramFrameReader(session.source_path, index)
        LOGGER.info("Waterfall preview: %d × %d, %.1f MiB", *waterfall.values.shape, waterfall.values.nbytes / 2**20)
        self._audit(
            "program",
            "waterfall_preview_completed",
            waterfall_id=waterfall.waterfall_id,
            preview_rows=int(waterfall.values.shape[0]),
            points=int(waterfall.values.shape[1]),
            full_frames=index.frame_count,
            memory_bytes=int(waterfall.values.nbytes),
        )
        if session_id == self.active_session_id:
            self.waterfall_renderer.set_data(waterfall)
            self.level_min.blockSignals(True)
            self.level_max.blockSignals(True)
            self.level_min.setValue(waterfall.min_level or -120.0)
            self.level_max.setValue(waterfall.max_level or -20.0)
            self.level_min.blockSignals(False)
            self.level_max.blockSignals(False)
            self.colormap_combo.setCurrentText(waterfall.colormap)
            frame_count = index.frame_count or waterfall.values.shape[0]
            self.time_slider.setRange(0, max(0, frame_count - 1))
            self.frame_spin.setRange(1, max(1, frame_count))
            self.cp_start_frame.setRange(1, max(1, frame_count))
            self.cp_stop_frame.setRange(1, max(1, frame_count))
            self.cp_stop_frame.setValue(max(1, frame_count))
            for spin in (self.heatmap_start_spin, self.heatmap_end_spin):
                spin.blockSignals(True)
                spin.setRange(1, max(1, frame_count))
                spin.blockSignals(False)
            self.heatmap_end_spin.blockSignals(True)
            self.heatmap_end_spin.setValue(max(1, frame_count))
            self.heatmap_end_spin.blockSignals(False)
            self.frame_total_label.setText(f"из {frame_count:,}")
            self.time_slider.setValue(min(session.current_frame, frame_count - 1))
            self._frame_scheduler.set_active_context(session.session_id, waterfall.waterfall_id)
            self._frame_nav.set_frame_count(frame_count)
            self._frame_nav.seek(self.time_slider.value(), NavigationReason.API)
            self._restore_channel_power_state(session, waterfall.waterfall_id, index)
            self._show_frame(self.time_slider.value())
            self._heatmap_index_ready()

    def _first_visible_session_id(self) -> str | None:
        for session in self.repository.all():
            if session.visible:
                return session.session_id
        return None

    def set_active_session(self, session_id: str) -> None:
        session = self.repository.get(session_id)
        if session is not None and not session.visible:
            replacement = self._first_visible_session_id()
            if replacement is None:
                self._clear_ui()
                self.active_session_id = None
                return
            session_id = replacement
        self.active_session_id = session_id
        session = self.repository.get(session_id)
        if session is None:
            self._clear_ui()
            return
        self._audit(
            "user",
            "session_activated",
            session_id=session.session_id,
            source_path=str(session.source_path),
        )
        self.status_file.setText(str(session.source_path))
        # Invalidate the heatmap context before the frame seek below can fire
        # the rolling trigger, so no request is started for a stale context.
        self._heatmap_context_changed()
        source_type = (
            session.source_descriptor.source_type.value
            if session.source_descriptor is not None else ""
        )
        if source_type != "live_iq":
            self.spectrum_renderer.clear_heatmap()
        elif getattr(self, "_live_heatmap_source_id", None) != session.source_descriptor.source_id:
            self.spectrum_renderer.clear_heatmap()
            self._live_heatmap_source_id = session.source_descriptor.source_id
        self._render_sessions()
        self._update_metadata(session)
        self._update_marker_table(session)
        self._refresh_measurement_table(session)
        self._refresh_power_sources(session)
        self._set_band_from_trace(self._active_trace(session))
        waterfall = self._active_waterfall(session)
        if waterfall is not None and waterfall.values is not None:
            self.waterfall_renderer.set_data(waterfall)
            region = next((item for item in session.frequency_regions if item.enabled), None)
            if region is not None:
                self.waterfall_renderer.set_frequency_region(
                    region.start_frequency_hz, region.stop_frequency_hz
                )
            else:
                self.waterfall_renderer.clear_frequency_region()
            index = self._active_spectrogram_index(session)
            frame_count = index.frame_count if index is not None else waterfall.values.shape[0]
            self.time_slider.setRange(0, max(0, frame_count - 1))
            self.frame_spin.setRange(1, max(1, frame_count))
            self.cp_start_frame.setRange(1, max(1, frame_count))
            self.cp_stop_frame.setRange(1, max(1, frame_count))
            for spin in (self.heatmap_start_spin, self.heatmap_end_spin):
                spin.blockSignals(True)
                spin.setRange(1, max(1, frame_count))
                spin.blockSignals(False)
            self.frame_total_label.setText(f"из {frame_count:,}")
            self.time_slider.setValue(min(session.current_frame, frame_count - 1))
            self._frame_scheduler.set_active_context(session.session_id, waterfall.waterfall_id)
            self._frame_nav.set_frame_count(frame_count)
            self._frame_nav.seek(self.time_slider.value(), NavigationReason.API)
            self._frame_scheduler.schedule(immediate=True)
            if index is not None:
                self._restore_channel_power_state(session, waterfall.waterfall_id, index)
        else:
            self.waterfall_renderer.clear()
            self.time_slider.setRange(0, 0)
            self.frame_spin.setRange(1, 1)
            self.cp_start_frame.setRange(1, 1)
            self.cp_stop_frame.setRange(1, 1)
            for spin in (self.heatmap_start_spin, self.heatmap_end_spin):
                spin.blockSignals(True)
                spin.setRange(1, 1)
                spin.blockSignals(False)
            self.frame_total_label.setText("из 1")
            self._frame_nav.set_frame_count(0)
            self._frame_nav.seek(0, NavigationReason.API)
        self._update_status()

    def close_active_session(self) -> None:
        if self.active_session_id is None:
            return
        closing = self.active_session()
        self._audit(
            "user",
            "session_closed",
            closed_session_id=self.active_session_id,
            closed_source_path=str(closing.source_path) if closing is not None else None,
        )
        closing_id = self.active_session_id
        closing_session = self.repository.get(closing_id)
        if closing_session is not None and closing_session.source_descriptor is not None:
            source_id = closing_session.source_descriptor.source_id
            controller = self._live_controllers.pop(source_id, None)
            self._live_adapters.pop(source_id, None)
            if controller is not None:
                controller.close(wait=False)
        self.repository.remove(closing_id)
        for key in [key for key in self._frame_readers if key[0] == closing_id]:
            self._frame_readers.pop(key).close()
            self._spectrogram_indexes.pop(key, None)
        self._heatmap_on_session_removed(self.active_session_id)
        sessions = self.repository.all()
        self.active_session_id = sessions[-1].session_id if sessions else None
        self._refresh_tree()
        if self.active_session_id:
            self.set_active_session(self.active_session_id)
        else:
            self._clear_ui()

    def _refresh_tree(self) -> None:
        self._tree_updating = True
        self.trace_tree.clear()
        for session in self.repository.all():
            label = session.name if session.visible else f"{session.name} [скрыт]"
            source_type = session.source_descriptor.source_type.value if session.source_descriptor else "dfl_file"
            root = QTreeWidgetItem([label, "Live" if source_type == "live_iq" else "DFL"])
            root.setData(0, ROLE_KIND, "session")
            root.setData(0, ROLE_SESSION, session.session_id)
            root.setToolTip(0, str(session.source_path))
            if not session.visible:
                root.setForeground(0, self.palette().placeholderText())
            self.trace_tree.addTopLevelItem(root)
            trace_group = QTreeWidgetItem(["Трассы", str(len(session.traces))])
            root.addChild(trace_group)
            for trace in session.traces.values():
                item = QTreeWidgetItem([trace.name, trace.trace_mode])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if trace.enabled else Qt.CheckState.Unchecked)
                item.setData(0, ROLE_KIND, "trace")
                item.setData(0, ROLE_SESSION, session.session_id)
                item.setData(0, ROLE_OBJECT, trace.trace_id)
                trace_group.addChild(item)
            waterfall_group = QTreeWidgetItem(["Waterfall", str(len(session.waterfalls))])
            root.addChild(waterfall_group)
            for waterfall in session.waterfalls.values():
                item = QTreeWidgetItem([waterfall.name, f"{waterfall.line_count} × {waterfall.point_count}"])
                item.setData(0, ROLE_KIND, "waterfall")
                item.setData(0, ROLE_SESSION, session.session_id)
                item.setData(0, ROLE_OBJECT, waterfall.waterfall_id)
                waterfall_group.addChild(item)
            root.setExpanded(True)
            trace_group.setExpanded(True)
            waterfall_group.setExpanded(True)
        self._tree_updating = False

    def _tree_selection_changed(self) -> None:
        items = self.trace_tree.selectedItems()
        if not items:
            return
        item = items[0]
        session_id = item.data(0, ROLE_SESSION)
        if not session_id:
            return
        session = self.repository.get(session_id)
        if session is None or not session.visible:
            return
        kind = item.data(0, ROLE_KIND)
        object_id = item.data(0, ROLE_OBJECT)
        if kind == "trace" and object_id in session.traces:
            session.active_trace_id = object_id
            self._audit("user", "trace_selected", selected_session_id=session_id, trace_id=object_id)
            self.set_active_session(session_id)
            self._update_trace_properties(session.traces[object_id])
            self._set_band_from_trace(session.traces[object_id])
        elif kind == "waterfall" and object_id in session.waterfalls:
            session.active_waterfall_id = object_id
            self._audit(
                "user", "waterfall_selected", selected_session_id=session_id, waterfall_id=object_id
            )
            self.set_active_session(session_id)
            waterfall = session.waterfalls[object_id]
            if waterfall.values is None:
                self._load_waterfall_preview(session, object_id)
            else:
                self.waterfall_renderer.set_data(waterfall)
        else:
            self.set_active_session(session_id)

    def _tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._tree_updating or item.data(0, ROLE_KIND) != "trace":
            return
        session = self.repository.get(item.data(0, ROLE_SESSION))
        trace = session.traces[item.data(0, ROLE_OBJECT)]
        trace.enabled = item.checkState(0) == Qt.CheckState.Checked
        self._audit(
            "user",
            "trace_visibility_changed",
            trace_id=trace.trace_id,
            enabled=trace.enabled,
        )
        self._render_sessions()

    def _session_context_menu(self, position: Any) -> None:
        item = self.trace_tree.itemAt(position)
        if item is None:
            return
        kind = item.data(0, ROLE_KIND)
        session_id = item.data(0, ROLE_SESSION)
        if not session_id or kind != "session":
            return
        session = self.repository.get(session_id)
        if session is None:
            return
        menu = QMenu(self)
        toggle_action = QAction(
            "Показать файл" if not session.visible else "Скрыть файл", self
        )
        remove_action = QAction("Удалить файл из программы", self)
        menu.addAction(toggle_action)
        menu.addAction(remove_action)
        chosen = menu.exec(self.trace_tree.viewport().mapToGlobal(position))
        if chosen is toggle_action:
            self._toggle_session_visibility(session_id)
        elif chosen is remove_action:
            self._remove_session(session_id)

    def _toggle_session_visibility(self, session_id: str) -> None:
        session = self.repository.get(session_id)
        if session is None:
            return
        new_state = not session.visible
        session.visible = new_state
        for trace in session.traces.values():
            trace.enabled = new_state
        self._audit(
            "user",
            "session_visibility_changed",
            session_id=session_id,
            visible=new_state,
        )
        if not new_state and self.active_session_id == session_id:
            replacement = self._first_visible_session_id()
            if replacement is not None:
                self.set_active_session(replacement)
            else:
                self.active_session_id = None
                self._clear_ui()
        self._refresh_tree()
        self._render_sessions()

    def _remove_session(self, session_id: str) -> None:
        session = self.repository.get(session_id)
        if session is None:
            return
        reply = QMessageBox.question(
            self,
            "Удалить файл",
            f"Удалить '{session.name}' из программы?\nИсходный DFL не изменится.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        was_active = self.active_session_id == session_id

        # Remove every (session_id, waterfall_id) index entry and close every
        # matching SpectrogramFrameReader so no tuple-key state dangles.
        for key in [k for k in self._spectrogram_indexes if k[0] == session_id]:
            del self._spectrogram_indexes[key]
        for key in [k for k in self._frame_readers if k[0] == session_id]:
            self._frame_readers.pop(key).close()

        # Cancel frame-loader work scoped to this session. Only the active and
        # pending request slots exist, so inspect them directly.
        loader = self._frame_loader
        active_request = getattr(loader, "_active", None)
        pending_request = getattr(loader, "_pending", None)
        if active_request is not None and active_request.session_id == session_id:
            loader.cancel_all()
        elif pending_request is not None and pending_request.session_id == session_id:
            loader._pending = None
            loader._diagnostics["pending_loads"] = 0
            loader.diagnostics.emit(loader._diagnostics.copy())

        # Cancel channel-power work scoped to this session and discard any
        # queued request so a stale result cannot mutate removed UI state.
        if (
            self._channel_power_worker is not None
            and self._channel_power_session_id == session_id
        ):
            self._channel_power_worker.cancel()
            self._channel_power_session_id = None
        if (
            self._pending_channel_power_request is not None
            and self._pending_channel_power_request[0].session_id == session_id
        ):
            self._pending_channel_power_request = None

        # Cancel heatmap work scoped to this session, drop its pending request
        # and cached densities, and invalidate late callbacks.
        self._heatmap_on_session_removed(session_id)
        if session.source_descriptor is not None:
            source_id = session.source_descriptor.source_id
            controller = self._live_controllers.pop(source_id, None)
            self._live_adapters.pop(source_id, None)
            if controller is not None:
                controller.close(wait=False)

        self.repository.remove(session_id)
        self._audit(
            "user",
            "session_removed",
            session_id=session_id,
            source_path=str(session.source_path),
        )
        if was_active:
            remaining = [s.session_id for s in self.repository.all()]
            self.active_session_id = remaining[0] if remaining else None
            if self.active_session_id:
                self._switch_to_session(self.active_session_id)
            else:
                self._clear_ui()
        self._refresh_tree()

    def _clear_ui(self) -> None:
        self.spectrum_renderer.clear()
        self.waterfall_renderer.clear()
        self._heatmap_controller.set_context(None)
        self._heatmap_controller.clear()
        self._heatmap_reset_overlay()
        self._heatmap_last_context_identity = None
        self._set_heatmap_status("Heatmap выключен" if not self.heatmap_enabled.isChecked() else "Нет данных")
        self.status_file.setText("Файл не открыт")
        self.status_cursor.setText("")
        self.current_frame_measurement.setText("Текущий Channel Power: —")

    def _switch_to_session(self, session_id: str) -> None:
        self.set_active_session(session_id)
        session = self.repository.get(session_id)
        if session is None:
            return
        waterfall = self._active_waterfall(session)
        if waterfall is not None:
            if waterfall.values is None:
                self._load_waterfall_preview(session, waterfall.waterfall_id)
            else:
                self.waterfall_renderer.set_data(waterfall)
        self._show_frame(session.current_frame)

    def _render_sessions(self) -> None:
        self.spectrum_renderer.clear()
        active_session = self.active_session()
        active_trace = self._active_trace(active_session)
        axis_unit = active_trace.axis_unit if active_trace is not None else "Hz"
        y_unit = active_trace.unit if active_trace is not None else "dBm"
        self.spectrum_renderer.set_axis_units(axis_unit, y_unit)
        for session in self.repository.all():
            if not session.visible:
                continue
            for trace in session.traces.values():
                if trace.enabled and trace.axis_unit == axis_unit:
                    self.spectrum_renderer.set_trace(trace)
        frequency_traces = [
            trace for candidate in self.repository.all()
            for trace in candidate.traces.values()
            if candidate.visible and trace.enabled and trace.axis_unit == axis_unit and trace.point_count
        ]
        if frequency_traces and axis_unit == "Hz":
            self.spectrum_renderer.set_x_limits(
                min(trace.start_frequency_hz for trace in frequency_traces),
                max(trace.stop_frequency_hz for trace in frequency_traces),
            )
        session = self.active_session()
        if session and session.visible:
            for marker in session.markers:
                self.spectrum_renderer.set_marker(marker)
                self._connect_marker_line(marker)
            self.spectrum_renderer.set_regions(session.frequency_regions)
            self._connect_frequency_regions(session)
        elif session is None or not session.visible:
            self.waterfall_renderer.clear()

    # --- markers ---------------------------------------------------------
    def add_marker(self) -> None:
        session = self.active_session()
        trace = self._active_trace(session) if session else None
        if session is None or trace is None or not trace.is_frequency_trace:
            return
        if len(session.markers) >= 10:
            self._show_error("Маркеры", "Поддерживается не более 10 маркеров")
            return
        x_range = self.spectrum_renderer.plot.viewRange()[0]
        frequency = float(np.mean(x_range)) if trace.is_frequency_trace else float(trace.x_values[trace.point_count // 2])
        marker = Marker(name=f"M{len(session.markers) + 1}", frequency_hz=frequency, trace_id=trace.trace_id)
        self._update_marker_power(marker, trace)
        session.markers.append(marker)
        self.spectrum_renderer.set_marker(marker)
        self._connect_marker_line(marker)
        self._update_marker_table(session)
        self._audit(
            "user",
            "marker_added",
            marker_id=marker.marker_id,
            name=marker.name,
            trace_id=marker.trace_id,
            frequency_hz=marker.frequency_hz,
            power_dbm=marker.power,
        )

    def add_peak_marker(self) -> None:
        session = self.active_session()
        trace = self._active_trace(session) if session else None
        if session is None or trace is None:
            return
        raw = self.spectrum_renderer.raw_trace_data(trace.trace_id)
        if raw is None:
            return
        frequencies, values = raw
        self.add_marker()
        marker = session.markers[-1]
        marker.marker_type = MarkerType.PEAK
        marker.locked = True
        self._distribute_peak_markers(session, frequencies, values)
        live = self._active_live_measurement_adapter(session)
        if live is not None:
            live_result = live.peak(limit=1)
            self.power_quality_label.setText(
                f"Quality: {live_result.quality.value}; frame {live_result.frame_sequence}; "
                f"config {live_result.config_generation}; calibration {live_result.calibration_status.value}"
            )
            self.power_warnings_label.setText(
                "Warnings: " + (" | ".join(item.message for item in live_result.warnings) or "—")
            )
        self.spectrum_renderer.set_marker(marker)
        self._update_marker_table(session)
        self._audit(
            "user",
            "marker_peak_selected",
            marker_id=marker.marker_id,
            trace_id=trace.trace_id,
            source="raw_trace",
            frequency_hz=marker.frequency_hz,
            power_dbm=marker.power,
        )

    def add_delta_marker(self) -> None:
        session = self.active_session()
        if session is None:
            return
        if not session.markers:
            self.add_peak_marker()
        if not session.markers:
            return
        reference = session.markers[0]
        self.add_marker()
        marker = session.markers[-1]
        marker.marker_type = MarkerType.DELTA
        marker.reference_marker_id = reference.marker_id
        self.spectrum_renderer.set_marker(marker)
        self._update_marker_table(session)
        self._audit(
            "user",
            "delta_marker_added",
            marker_id=marker.marker_id,
            reference_marker_id=reference.marker_id,
        )

    def remove_selected_marker(self) -> None:
        session = self.active_session()
        row = self.marker_table.currentRow()
        if session is None or row < 0 or row >= len(session.markers):
            return
        marker = session.markers.pop(row)
        self.spectrum_renderer.remove_marker(marker.marker_id)
        self._update_marker_table(session)
        self._audit("user", "marker_removed", marker_id=marker.marker_id, name=marker.name)

    def _selected_markers(self) -> list[Marker]:
        session = self.active_session()
        if session is None:
            return []
        rows = sorted({index.row() for index in self.marker_table.selectedIndexes()})
        if not rows and self.marker_table.currentRow() >= 0:
            rows = [self.marker_table.currentRow()]
        marker_ids = {
            self.marker_table.item(row, 0).data(ROLE_OBJECT)
            for row in rows
            if self.marker_table.item(row, 0) is not None
        }
        return [marker for marker in session.markers if marker.marker_id in marker_ids]

    def _marker_context_menu(self, _position: Any) -> None:
        selected = self._selected_markers()
        session = self.active_session()
        if session is None:
            return
        menu = QMenu(self.marker_table)
        toggle = menu.addAction(
            "Включить выбранные" if selected and not all(m.enabled for m in selected)
            else "Выключить выбранные"
        )
        toggle.setEnabled(bool(selected))
        remove = menu.addAction("Удалить выбранные")
        remove.setEnabled(bool(selected))
        lock = menu.addAction(
            "Разблокировать перемещение" if selected and all(m.locked for m in selected)
            else "Заблокировать перемещение"
        )
        lock.setEnabled(bool(selected))
        type_menu = QMenu("Тип маркера", menu)
        type_labels = {
            MarkerType.MANUAL: "Ручной (Manual)",
            MarkerType.PEAK: "Пик (Peak)",
            MarkerType.DELTA: "Дельта (Delta)",
            MarkerType.MINIMUM: "Минимум",
            MarkerType.BAND_CENTER: "Центр полосы",
            MarkerType.NOISE: "Шум",
            MarkerType.HARMONIC: "Гармоника",
            MarkerType.REFERENCE: "Опорный",
        }
        type_actions: dict[QAction, MarkerType] = {}
        for marker_type in MarkerType:
            action = type_menu.addAction(type_labels.get(marker_type, marker_type.value))
            action.setEnabled(bool(selected))
            type_actions[action] = marker_type
        menu.addMenu(type_menu)
        menu.addSeparator()
        clear = menu.addAction("Удалить все маркеры")
        clear.setEnabled(bool(session.markers))
        chosen = self._exec_context_menu(menu)
        if chosen == toggle:
            enabled = not all(marker.enabled for marker in selected)
            for marker in selected:
                marker.enabled = enabled
                self.spectrum_renderer.set_marker(marker)
            self._update_marker_table(session)
            self._audit("user", "markers_visibility_changed", count=len(selected), enabled=enabled)
        elif chosen == remove:
            selected_ids = {marker.marker_id for marker in selected}
            session.markers = [m for m in session.markers if m.marker_id not in selected_ids]
            for marker_id in selected_ids:
                self.spectrum_renderer.remove_marker(marker_id)
            self._update_marker_table(session)
            self._audit("user", "markers_removed", count=len(selected_ids))
        elif chosen == lock:
            locked = not all(marker.locked for marker in selected)
            for marker in selected:
                marker.locked = locked
                self.spectrum_renderer.set_marker(marker)
            self._update_marker_table(session)
            self._audit("user", "markers_lock_changed", count=len(selected), locked=locked)
        elif chosen in type_actions:
            marker_type = type_actions[chosen]
            for marker in selected:
                self._set_marker_type(marker, marker_type)
        elif chosen == clear:
            self.clear_markers()

    def clear_markers(self) -> None:
        session = self.active_session()
        if session is None:
            return
        marker_ids = [marker.marker_id for marker in session.markers]
        session.markers.clear()
        for marker_id in marker_ids:
            self.spectrum_renderer.remove_marker(marker_id)
        self._update_marker_table(session)
        self._audit("user", "markers_cleared", count=len(marker_ids))

    def _connect_marker_line(self, marker: Marker) -> None:
        pair = self.spectrum_renderer.markers.get(marker.marker_id)
        if not pair or pair[0].property("connected"):
            return
        pair[0].setProperty("connected", True)
        pair[0].sigPositionChanged.connect(lambda line, marker_id=marker.marker_id: self._marker_moved(marker_id, line.value()))

    def _marker_moved(self, marker_id: str, frequency: float) -> None:
        session = self.active_session()
        if session is None:
            return
        marker = next((item for item in session.markers if item.marker_id == marker_id), None)
        if marker is None:
            return
        marker.frequency_hz = float(frequency)
        trace = session.traces.get(marker.trace_id or "") or self._active_trace(session)
        if trace:
            self._update_marker_power(marker, trace)
        self.spectrum_renderer.set_marker(marker)
        self._update_marker_table(session)
        self._audit(
            "user",
            "marker_moved",
            marker_id=marker.marker_id,
            trace_id=marker.trace_id,
            frequency_hz=marker.frequency_hz,
            power_dbm=marker.power,
        )

    @staticmethod
    def _update_marker_power(marker: Marker, trace: SpectrumTrace) -> None:
        x = trace.x_values
        if x.size:
            index = int(np.argmin(np.abs(x - marker.frequency_hz)))
            marker.frequency_hz = float(x[index])
            marker.power = float(trace.power_values[index])

    def _distribute_peak_markers(
        self,
        session: MeasurementSession,
        frequencies: np.ndarray,
        values: np.ndarray,
    ) -> None:
        """Assign distinct peaks to all PEAK markers in descending order."""
        peak_markers = [m for m in session.markers if m.marker_type == MarkerType.PEAK and m.enabled]
        if not peak_markers:
            return
        peaks = peak_search_values(frequencies, values, limit=len(peak_markers))
        for marker, peak in zip(peak_markers, peaks):
            marker.frequency_hz, marker.power, _ = peak

    def _set_marker_type(self, marker: Marker, marker_type: MarkerType) -> None:
        session = self.active_session()
        if session is None:
            return
        marker.marker_type = marker_type
        marker.locked = marker_type == MarkerType.PEAK
        if marker_type == MarkerType.PEAK:
            trace = session.traces.get(marker.trace_id or "") or self._active_trace(session)
            if trace is not None:
                raw = self.spectrum_renderer.raw_trace_data(trace.trace_id)
                if raw is not None:
                    self._distribute_peak_markers(session, *raw)
        elif marker_type == MarkerType.DELTA and session.markers:
            marker.reference_marker_id = session.markers[0].marker_id
        self.spectrum_renderer.set_marker(marker)
        self._update_marker_table(session)
        self._audit(
            "user",
            "marker_type_changed",
            marker_id=marker.marker_id,
            marker_type=marker_type.value,
            locked=marker.locked,
        )

    def _update_marker_table(self, session: MeasurementSession) -> None:
        self.marker_table.setRowCount(len(session.markers))
        lookup = {item.marker_id: item for item in session.markers}
        for row, marker in enumerate(session.markers):
            reference = lookup.get(marker.reference_marker_id or "")
            delta_f = marker.frequency_hz - reference.frequency_hz if reference else np.nan
            delta_l = marker.power - reference.power if reference else np.nan
            values = [
                str(row + 1), marker.name, marker.marker_type.value,
                f"{marker.frequency_hz / 1e6:.6f} MHz", f"{marker.power:.3f} dBm",
                f"{delta_f / 1e6:.6f} MHz" if np.isfinite(delta_f) else "",
                f"{delta_l:.3f} dB" if np.isfinite(delta_l) else "",
                datetime.fromtimestamp(marker.timestamp).isoformat() if marker.timestamp else "",
                marker.trace_id or "", "Да" if marker.enabled else "Нет",
            ]
            for column, value in enumerate(values):
                if column == 8:
                    continue
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(ROLE_OBJECT, marker.marker_id)
                if column == 2:
                    # Type is shown for selection/copy only; change it via the context menu.
                    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                self.marker_table.setItem(row, column, item)
            trace_combo = QComboBox()
            for trace in session.traces.values():
                if trace.is_frequency_trace:
                    trace_combo.addItem(trace.name, trace.trace_id)
            combo_index = trace_combo.findData(marker.trace_id)
            if combo_index >= 0:
                trace_combo.setCurrentIndex(combo_index)
            trace_combo.currentIndexChanged.connect(
                lambda _index, marker_id=marker.marker_id, combo=trace_combo: self._marker_trace_changed(
                    marker_id, combo.currentData()
                )
            )
            self.marker_table.setCellWidget(row, 8, trace_combo)

    def _marker_trace_changed(self, marker_id: str, trace_id: str | None) -> None:
        session = self.active_session()
        if session is None or trace_id is None:
            return
        marker = next((item for item in session.markers if item.marker_id == marker_id), None)
        trace = session.traces.get(trace_id)
        if marker is None or trace is None:
            return
        marker.trace_id = trace_id
        self._update_marker_power(marker, trace)
        self.spectrum_renderer.set_marker(marker)
        self._update_marker_table(session)
        self._audit(
            "user",
            "marker_trace_changed",
            marker_id=marker.marker_id,
            trace_id=trace_id,
            frequency_hz=marker.frequency_hz,
            power_dbm=marker.power,
        )

    # --- analysis --------------------------------------------------------
    def _update_region(self) -> FrequencyRegion | None:
        session = self.active_session()
        if session is None:
            return None
        start = self.band_start.value() * 1e6
        stop = self.band_stop.value() * 1e6
        if stop <= start:
            self._show_error("Полоса", "Конечная частота должна быть выше начальной")
            return None
        if session.frequency_regions:
            region = session.frequency_regions[0]
            region.start_frequency_hz, region.stop_frequency_hz = start, stop
        else:
            region = FrequencyRegion(start_frequency_hz=start, stop_frequency_hz=stop)
            session.frequency_regions.append(region)
        self.spectrum_renderer.set_regions(session.frequency_regions)
        self._connect_frequency_regions(session)
        self._audit(
            "user",
            "frequency_region_updated",
            region_id=region.region_id,
            start_hz=region.start_frequency_hz,
            stop_hz=region.stop_frequency_hz,
        )
        return region

    def _connect_frequency_regions(self, session: MeasurementSession) -> None:
        for region_id, item in self.spectrum_renderer.regions.items():
            if item.property("controller_connected"):
                continue
            item.setProperty("controller_connected", True)
            item.sigRegionChangeFinished.connect(
                lambda region_item, sid=session.session_id, rid=region_id: self._frequency_region_moved(
                    sid, rid, region_item.getRegion()
                )
            )

    def _frequency_region_moved(
        self, session_id: str, region_id: str, values: tuple[float, float]
    ) -> None:
        session = self.repository.get(session_id)
        region = next((item for item in session.frequency_regions if item.region_id == region_id), None)
        if region is None:
            return
        region.start_frequency_hz, region.stop_frequency_hz = sorted(map(float, values))
        self.band_start.setValue(region.start_frequency_hz / 1e6)
        self.band_stop.setValue(region.stop_frequency_hz / 1e6)
        self._set_cp_frequency_values(
            region.start_frequency_hz / 1e6, region.stop_frequency_hz / 1e6
        )
        self._mark_channel_power_dirty("перемещена частотная область")
        self._audit(
            "user",
            "frequency_region_moved",
            region_id=region_id,
            start_hz=region.start_frequency_hz,
            stop_hz=region.stop_frequency_hz,
        )

    def measure_channel_power(self) -> None:
        self._run_trace_analysis("Channel Power", lambda trace, region: channel_power(trace, region.start_frequency_hz, region.stop_frequency_hz))

    def measure_obw(self) -> None:
        self._run_trace_analysis("Occupied Bandwidth 99%", lambda trace, region: occupied_bandwidth(trace, 0.99), needs_region=False)

    def measure_noise(self) -> None:
        self._run_trace_analysis("Noise Floor", lambda trace, region: noise_floor(trace, region.start_frequency_hz, region.stop_frequency_hz))

    def measure_snr(self) -> None:
        self._run_trace_analysis("SNR", lambda trace, region: snr(trace, (region.start_frequency_hz, region.stop_frequency_hz)))

    def measure_acpr(self) -> None:
        def calculate(trace: SpectrumTrace, region: FrequencyRegion) -> Any:
            return acpr(
                trace, region.center_frequency_hz, region.bandwidth_hz,
                self.acpr_offset.value() * 1e6, self.acpr_width.value() * 1e6,
            )
        self._run_trace_analysis("ACPR / ACLR", calculate)

    def _active_live_measurement_adapter(
        self, session: MeasurementSession | None
    ) -> LiveMeasurementAdapter | None:
        if session is None or session.source_descriptor is None:
            return None
        adapter = self._live_adapters.get(session.source_descriptor.source_id)
        frame = adapter.latest_frame if adapter is not None else None
        return LiveMeasurementAdapter(frame) if frame is not None else None

    def _run_live_trace_analysis(
        self,
        session: MeasurementSession,
        name: str,
        adapter: LiveMeasurementAdapter,
        needs_region: bool,
    ) -> None:
        frame = adapter.frame
        if needs_region:
            region = self._update_region()
        else:
            region = session.frequency_regions[0] if session.frequency_regions else FrequencyRegion(
                start_frequency_hz=float(frame.frequencies_hz[0]),
                stop_frequency_hz=float(frame.frequencies_hz[-1]),
            )
        if region is None:
            return

        def calculate() -> LiveMeasurementResult[Any]:
            if name == "Channel Power":
                return adapter.channel_power(region.start_frequency_hz, region.stop_frequency_hz)
            if name == "Occupied Bandwidth 99%":
                return adapter.occupied_bandwidth(0.99)
            if name == "Noise Floor":
                return adapter.noise_floor(region.start_frequency_hz, region.stop_frequency_hz)
            if name == "SNR":
                return adapter.snr((region.start_frequency_hz, region.stop_frequency_hz))
            if name == "ACPR / ACLR":
                return adapter.acpr(
                    region.center_frequency_hz,
                    region.bandwidth_hz,
                    self.acpr_offset.value() * 1e6,
                    adjacent_bandwidth_hz=self.acpr_width.value() * 1e6,
                )
            raise ValueError(f"Unsupported live measurement: {name}")

        self._power_measurement_serial += 1
        serial = self._power_measurement_serial
        worker = TaskWorker(calculate)
        worker.signals.result.connect(
            lambda value: self._live_measurement_ready(
                serial, session.session_id, name,
                int(frame.frame_sequence), int(frame.config_generation), value,
            )
        )
        self.cp_recalc_status.setText(f"Расчёт {name} выполняется…")
        self._set_busy(True, f"Расчёт: {name}")
        self._start_worker(worker)

    def _live_measurement_ready(
        self,
        serial: int,
        session_id: str,
        name: str,
        frame_sequence: int,
        config_generation: int,
        value: LiveMeasurementResult[Any],
    ) -> None:
        if serial != self._power_measurement_serial or session_id != self.active_session_id:
            return
        if value.frame_sequence != frame_sequence or value.config_generation != config_generation:
            self._show_error("Измерение", "Результат относится к неожиданной версии live-кадра")
            return
        self._power_measurement_ready(serial, session_id, name, value)
        quality = getattr(value.quality, "value", value.quality)
        self.power_quality_label.setText(
            f"Quality: {quality}; frame {value.frame_sequence}; config {value.config_generation}; "
            f"calibration {value.calibration_status.value}"
        )
        self._audit(
            "program", "live_measurement_completed", analysis=name,
            source_id=value.source_id, frame_sequence=value.frame_sequence,
            config_generation=value.config_generation, quality=quality,
            warning_codes=[item.code for item in value.warnings],
        )

    def _run_trace_analysis(
        self,
        name: str,
        function: Callable[[SpectrumTrace, FrequencyRegion], Any],
        needs_region: bool = True,
    ) -> None:
        session = self.active_session()
        live_adapter = self._active_live_measurement_adapter(session)
        if live_adapter is not None:
            self._run_live_trace_analysis(session, name, live_adapter, needs_region)
            return
        trace = self._active_trace(session) if session else None
        if session is None or trace is None or not trace.is_frequency_trace:
            self._show_error("Измерение", "Выберите частотную трассу")
            return
        region = self._update_region() if needs_region else (
            session.frequency_regions[0] if session.frequency_regions else FrequencyRegion(
                start_frequency_hz=trace.start_frequency_hz, stop_frequency_hz=trace.stop_frequency_hz
            )
        )
        if region is None:
            return
        self._audit(
            "user",
            "trace_analysis_started",
            analysis=name,
            trace_id=trace.trace_id,
            region_id=region.region_id,
            start_hz=region.start_frequency_hz,
            stop_hz=region.stop_frequency_hz,
        )
        self._set_busy(True, f"Расчёт: {name}")
        worker = TaskWorker(function, trace, region)
        worker.signals.result.connect(lambda value: self._analysis_ready(session.session_id, trace.trace_id, region.region_id, name, value))
        self._start_worker(worker)

    def _analysis_ready(self, session_id: str, trace_id: str, region_id: str, name: str, value: Any) -> None:
        session = self.repository.get(session_id)
        rows: list[tuple[str, Any]] = []
        if isinstance(value, list):
            for index, item in enumerate(value):
                rows.extend((f"{index + 1}.{key}", val) for key, val in asdict(item).items())
        elif hasattr(value, "__dataclass_fields__"):
            rows = list(asdict(value).items())
        else:
            rows = [("result", value)]
        approximate = bool(getattr(value, "approximate", False))
        result = AnalysisResult(name, name, {key: self._json_value(val) for key, val in rows}, trace_id, region_id, approximate)
        session.analysis_results.append(result)
        self._refresh_measurement_table(session)
        LOGGER.info("Расчёт %s завершён", name)
        self._audit(
            "program",
            "trace_analysis_completed",
            analysis=name,
            trace_id=trace_id,
            region_id=region_id,
            approximate=approximate,
        )

    def _refresh_measurement_table(self, session: MeasurementSession | None = None) -> None:
        session = session or self.active_session()
        self.measurement_results.setRowCount(0)
        if session is None:
            return
        for result in session.analysis_results:
            for key, value in result.values.items():
                row = self.measurement_results.rowCount()
                self.measurement_results.insertRow(row)
                state = "" if result.enabled else " [выкл.]"
                name_item = QTableWidgetItem(result.name + state)
                name_item.setData(ROLE_OBJECT, result.result_id)
                self.measurement_results.setItem(row, 0, name_item)
                self.measurement_results.setItem(row, 1, QTableWidgetItem(str(key)))
                self.measurement_results.setItem(
                    row, 2, QTableWidgetItem(self._format_value(value))
                )

    def _selected_analysis_results(self) -> list[AnalysisResult]:
        session = self.active_session()
        if session is None:
            return []
        rows = sorted({index.row() for index in self.measurement_results.selectedIndexes()})
        if not rows and self.measurement_results.currentRow() >= 0:
            rows = [self.measurement_results.currentRow()]
        result_ids = {
            self.measurement_results.item(row, 0).data(ROLE_OBJECT)
            for row in rows
            if self.measurement_results.item(row, 0) is not None
        }
        return [result for result in session.analysis_results if result.result_id in result_ids]

    def _measurement_context_menu(self, _position: Any) -> None:
        session = self.active_session()
        if session is None:
            return
        selected = self._selected_analysis_results()
        menu = QMenu(self.measurement_results)
        toggle_results = menu.addAction(
            "Включить выбранные результаты"
            if selected and not all(result.enabled for result in selected)
            else "Выключить выбранные результаты"
        )
        toggle_results.setEnabled(bool(selected))
        delete_results = menu.addAction("Удалить выбранные результаты")
        delete_results.setEnabled(bool(selected))
        clear_results = menu.addAction("Удалить все результаты")
        clear_results.setEnabled(bool(session.analysis_results))
        menu.addSeparator()
        region = session.frequency_regions[0] if session.frequency_regions else None
        toggle_region = menu.addAction(
            "Показать полосу" if region is not None and not region.enabled else "Скрыть полосу"
        )
        toggle_region.setEnabled(region is not None)
        delete_region = menu.addAction("Удалить полосу")
        delete_region.setEnabled(region is not None)
        menu.addSeparator()
        clear_all = menu.addAction("Очистить все инструменты анализа")
        chosen = self._exec_context_menu(menu)
        if chosen == toggle_results:
            enabled = not all(result.enabled for result in selected)
            for result in selected:
                result.enabled = enabled
            self._refresh_measurement_table(session)
            self._audit("user", "measurement_results_visibility_changed", count=len(selected), enabled=enabled)
        elif chosen == delete_results:
            selected_ids = {result.result_id for result in selected}
            session.analysis_results = [
                result for result in session.analysis_results
                if result.result_id not in selected_ids
            ]
            self._refresh_measurement_table(session)
            self._audit("user", "measurement_results_removed", count=len(selected_ids))
        elif chosen == clear_results:
            self.clear_measurement_results()
        elif chosen == toggle_region:
            self.toggle_frequency_region()
        elif chosen == delete_region:
            self.delete_frequency_region()
        elif chosen == clear_all:
            self.clear_all_analysis_tools()

    def clear_measurement_results(self) -> None:
        session = self.active_session()
        if session is None:
            return
        count = len(session.analysis_results)
        session.analysis_results.clear()
        self._refresh_measurement_table(session)
        self._audit("user", "measurement_results_cleared", count=count)

    def toggle_frequency_region(self) -> None:
        session = self.active_session()
        if session is None or not session.frequency_regions:
            return
        region = session.frequency_regions[0]
        region.enabled = not region.enabled
        self.spectrum_renderer.set_regions(session.frequency_regions)
        if region.enabled:
            self._connect_frequency_regions(session)
            self.waterfall_renderer.set_frequency_region(
                region.start_frequency_hz, region.stop_frequency_hz
            )
        else:
            self.waterfall_renderer.clear_frequency_region()
        self._audit("user", "frequency_region_visibility_changed", region_id=region.region_id, enabled=region.enabled)

    def delete_frequency_region(self) -> None:
        session = self.active_session()
        if session is None:
            return
        region_ids = {region.region_id for region in session.frequency_regions}
        session.frequency_regions.clear()
        self.spectrum_renderer.set_regions([])
        self.waterfall_renderer.clear_frequency_region()
        self._mark_channel_power_dirty("частотная область удалена")
        self._audit("user", "frequency_regions_deleted", count=len(region_ids))

    @staticmethod
    def _json_value(value: Any) -> float | str | bool:
        if isinstance(value, (bool, str)):
            return value
        if isinstance(value, (int, float, np.number)):
            return float(value)
        return str(value)

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, float):
            return f"{value:.9g}"
        return str(value)

    # --- playback --------------------------------------------------------
    def play(self) -> None:
        if self.time_slider.maximum() <= 0:
            return
        self._update_playback_interval()
        self._playback_start_frame = self.time_slider.value()
        self._playback_start_time = time.perf_counter()
        self._frame_scheduler.set_playback_active(True)
        self.playback_timer.start()
        self._audit(
            "user",
            "playback_started",
            frame=self.time_slider.value(),
            speed=self.speed_combo.currentText(),
            fps=int(self.fps_combo.currentText()),
            loop=self.loop_check.isChecked(),
        )

    def pause(self) -> None:
        self.playback_timer.stop()
        self._frame_scheduler.cancel_and_invalidate()
        # §5.6: a pending latest-target heatmap job keeps running and catches up.
        self._heatmap_controller.pause()
        self._audit("user", "playback_paused", frame=self.time_slider.value())

    def stop(self) -> None:
        self.playback_timer.stop()
        self._frame_scheduler.cancel_and_invalidate()
        self._audit("user", "playback_stopped", frame=self.time_slider.value())
        self.first_frame()
        # §5.7: explicit initial window at frame 1, not just the slider side effect.
        self._heatmap_controller.stop(target=0)

    def _toggle_play(self) -> None:
        self.pause() if self.playback_timer.isActive() else self.play()

    def _update_playback_interval(self) -> None:
        fps = max(1, int(self.fps_combo.currentText()))
        speed = float(self.speed_combo.currentText().replace("×", ""))
        interval_ms = 1000.0 / fps
        session = self.active_session()
        base_period_s: float | None = None
        if session is not None:
            waterfall = self._active_waterfall(session)
            mode = str(waterfall.metadata.get("mode", "")) if waterfall is not None else ""
            timing = session.acquisition_timing.get(mode)
            if timing is not None:
                base_period_s = timing.t_deadline_s
            if base_period_s is None:
                index = self._active_spectrogram_index(session)
                if index is not None and index.timestamps.size > 1:
                    deltas = np.diff(index.timestamps)
                    finite_positive = deltas[np.isfinite(deltas) & (deltas > 0)]
                    if finite_positive.size:
                        base_period_s = float(np.median(finite_positive))
        if base_period_s is not None and base_period_s > 0 and speed > 0:
            frame_interval_ms = base_period_s * 1000.0 / speed
            if self.no_skip_check.isChecked():
                interval_ms = frame_interval_ms
            else:
                interval_ms = max(interval_ms, frame_interval_ms)
        self.playback_timer.setInterval(
            max(1, min(round(interval_ms), 2_147_483_647))
        )
        self._frame_scheduler.set_sequential_mode(self.no_skip_check.isChecked())
        self._audit(
            "user",
            "playback_speed_changed",
            speed=self.speed_combo.currentText(),
            fps=fps,
            timer_interval_ms=self.playback_timer.interval(),
            no_skip=self.no_skip_check.isChecked(),
        )

    def _advance_frame(self) -> None:
        session = self.active_session()
        current = self._frame_nav.requested_frame
        speed = float(self.speed_combo.currentText().replace("×", ""))
        frame_count = self._frame_nav.frame_count
        if frame_count == 0:
            return
        max_frame = frame_count - 1
        target: int
        if current == max_frame and self.loop_check.isChecked():
            target = 0
            self._playback_start_frame = 0
            self._playback_start_time = time.perf_counter()
            self._frame_scheduler.reset_playback_progress()
        elif self.no_skip_check.isChecked():
            target = current + 1
        else:
            sweep_time = self._playback_sweep_time(session)
            if sweep_time is not None and self._playback_start_time is not None:
                elapsed = time.perf_counter() - self._playback_start_time
                target = self._playback_start_frame + int(math.floor(elapsed * speed / sweep_time))
            else:
                index = self._active_spectrogram_index(session)
                target = current + 1
                if (
                    index is not None
                    and index.timestamps.size
                    and current < index.frame_count - 1
                ):
                    timestamp = index.timestamps[current]
                    if np.isfinite(timestamp):
                        target_time = timestamp + speed * self.playback_timer.interval() / 1000.0
                        target = max(current + 1, int(np.searchsorted(index.timestamps, target_time)))
        if target > max_frame:
            if self.loop_check.isChecked():
                target = 0
                self._playback_start_frame = 0
                self._playback_start_time = time.perf_counter()
            else:
                self.pause()
                return
        self._frame_nav.seek(target, NavigationReason.PLAYBACK)
        self._frame_scheduler.schedule(immediate=True)

    def _playback_sweep_time(self, session: MeasurementSession | None) -> float | None:
        if session is None:
            return None
        waterfall = self._active_waterfall(session)
        if waterfall is None:
            return None
        mode = str(waterfall.metadata.get("mode", ""))
        timing = session.acquisition_timing.get(mode)
        if timing is not None and timing.t_deadline_s is not None and timing.t_deadline_s > 0:
            return float(timing.t_deadline_s)
        index = self._active_spectrogram_index(session)
        if index is not None and index.timestamps.size > 1:
            deltas = np.diff(index.timestamps)
            finite_positive = deltas[np.isfinite(deltas) & (deltas > 0)]
            if finite_positive.size:
                return float(np.median(finite_positive))
        return None

    def _show_frame(self, frame: int) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if session is None or waterfall is None or waterfall.values is None or not waterfall.values.shape[0]:
            return
        index = self._active_spectrogram_index(session)
        frame_count = index.frame_count if index is not None else waterfall.values.shape[0]
        frame = int(np.clip(frame, 0, max(0, frame_count - 1)))
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(frame + 1)
        self.frame_spin.blockSignals(False)
        self._frame_nav.seek(frame, NavigationReason.FRAME_INPUT)
        self._frame_scheduler.schedule(immediate=True)

    def _frame_loader_context(
        self,
        session_id: str,
        waterfall_id: str,
    ) -> tuple[str | Path, SpectrogramIndex | None, SpectrogramFrameReader | None]:
        session = self.repository.get(session_id) or self.active_session()
        if session is None:
            return (Path(""), None, None)
        waterfall = session.waterfalls.get(waterfall_id) or self._active_waterfall(session)
        if waterfall is None:
            return (session.source_path, None, None)
        key = (session.session_id, waterfall.waterfall_id)
        index = self._spectrogram_indexes.get(key)
        reader = self._frame_readers.get(key)
        return (session.source_path, index, reader)

    def _waterfall_wheel(self, angle_delta: int, pixel_delta: Any, modifiers: Any) -> None:
        self._audit(
            "user",
            "waterfall_wheel_step_queued",
            angle_delta=angle_delta,
            requested_frame=self._frame_nav.requested_frame,
        )
        self._frame_nav.handle_wheel(angle_delta, pixel_delta, modifiers)
        self._frame_scheduler.schedule(immediate=True)

    def _set_wheel_step(self, value: int) -> None:
        self._frame_nav.config.wheel_step = value

    def _show_frame_load_error(self, message: str) -> None:
        LOGGER.error("Frame load error: %s", message)

    def _apply_frame_snapshot(self, snapshot: FrameSnapshot) -> None:
        session = self.repository.get(snapshot.session_id) or self.active_session()
        if session is None:
            return
        waterfall = session.waterfalls.get(snapshot.waterfall_id) or self._active_waterfall(session)
        if waterfall is None:
            return
        frame = snapshot.frame_index
        row = snapshot.row
        index = self._active_spectrogram_index(session)
        frame_count = (
            index.frame_count
            if index is not None
            else waterfall.values.shape[0] if waterfall.values is not None else 1
        )
        self._audit(
            "navigation",
            "frame_selected",
            frame=frame,
            displayed_number=frame + 1,
            frame_count=frame_count,
            playback=self.playback_timer.isActive(),
            displayed_frame=frame,
            reason=snapshot.reason.name,
        )
        session.current_frame = frame
        self.time_slider.blockSignals(True)
        self.time_slider.setValue(frame)
        self.time_slider.blockSignals(False)
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(frame + 1)
        self.frame_spin.blockSignals(False)
        trace = self._frame_trace(session, waterfall)
        if trace is not None:
            values = row.values[: waterfall.point_count]
            frequencies = waterfall.frequencies_hz[: values.size]
            self.spectrum_renderer.update_trace(trace.trace_id, frequencies, values)
            self._distribute_peak_markers(session, frequencies, values)
            for marker in session.markers:
                marker.timestamp = row.timestamp if np.isfinite(row.timestamp) else None
                bound_trace = session.traces.get(marker.trace_id or "")
                if marker.marker_type == MarkerType.PEAK:
                    pass  # already distributed above
                elif bound_trace is trace:
                    sample_index = int(np.argmin(np.abs(frequencies - marker.frequency_hz)))
                    marker.power = float(values[sample_index])
                elif bound_trace is not None:
                    self._update_marker_power(marker, bound_trace)
                self.spectrum_renderer.set_marker(marker)
            self._update_marker_table(session)
        ts_array = np.asarray(row.timestamp)
        timestamp = (
            row.timestamp
            if ts_array.size and bool(np.isfinite(ts_array).all())
            else self._frame_timestamp(waterfall, index, frame)
        )
        waterfall_y = self._frame_to_preview_row(waterfall, index, frame)
        center = (waterfall.start_frequency_hz + waterfall.stop_frequency_hz) / 2.0
        self.waterfall_renderer.set_current_frame_row(
            row.values[: waterfall.point_count], waterfall_y
        )
        self.waterfall_renderer.set_cursor(center, waterfall_y, f"Кадр {frame + 1:,}")
        time_text = (
            datetime.fromtimestamp(timestamp).isoformat(sep=" ", timespec="milliseconds")
            if np.isfinite(timestamp)
            else str(frame)
        )
        self.status_cursor.setText(f"Кадр {frame + 1:,}/{frame_count:,} · {time_text}")
        self._sync_channel_current_line()
        self._sync_current_frame_measurement()
        # Heatmap analytics is driven by the logical FrameSpanEvent path
        # (_on_heatmap_frame_span), never by this displayed-frame hook.

    def _on_frame_settled(self) -> None:
        LOGGER.debug(
            "frame_navigation_settled at frame %s (generation %s)",
            self._frame_nav.requested_frame,
            self._frame_nav.generation,
        )
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if (
            session is None
            or waterfall is None
            or self._frame_nav.scroll_state != ScrollState.IDLE
        ):
            return
        frame = self._frame_nav.requested_frame
        frame_count = self._frame_nav.frame_count
        for offset in (1, -1):
            pf = frame + offset
            if 0 <= pf < frame_count:
                self._frame_loader.request(
                    session.session_id,
                    waterfall.waterfall_id,
                    pf,
                    self._frame_nav.generation,
                    NavigationReason.API,
                )

    def _display_exact_frame(
        self,
        session: MeasurementSession,
        waterfall: WaterfallData,
        frame: int,
        row: SpectrogramRow,
    ) -> None:
        snapshot = FrameSnapshot(
            session_id=session.session_id,
            waterfall_id=waterfall.waterfall_id,
            frame_index=frame,
            generation=self._frame_nav.generation,
            reason=NavigationReason.API,
            row=row,
        )
        self._apply_frame_snapshot(snapshot)

    @staticmethod
    def _frame_timestamp(
        waterfall: WaterfallData,
        index: SpectrogramIndex | None,
        frame: int,
    ) -> float:
        if index is not None and frame < index.timestamps.size:
            return float(index.timestamps[frame])
        if waterfall.timestamps is not None and waterfall.timestamps.size:
            return float(waterfall.timestamps[min(frame, waterfall.timestamps.size - 1)])
        return float("nan")

    def _sync_current_frame_measurement(self) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if session is None or waterfall is None:
            self.current_frame_measurement.setText("Текущий Channel Power: —")
            return
        result = self._channel_power_results.get((session.session_id, waterfall.waterfall_id))
        frame = session.current_frame
        if result is None:
            self.current_frame_measurement.setText("Текущий Channel Power: —")
            return
        matches = np.flatnonzero(result.series.frame_indices == frame)
        if not matches.size:
            self.current_frame_measurement.setText("Текущий Channel Power: —")
            return
        series_index = int(matches[0])
        power = float(result.series.power_dbm[series_index])
        state = (
            "ACTIVE" if result.activity.effective_activity_mask[series_index] else "IDLE"
        )
        text = f"{power:.6f} dBm" if np.isfinite(power) else "нет данных"
        self.current_frame_measurement.setText(
            f"Текущий Channel Power: {text} · {state}"
        )

    @staticmethod
    def _frame_to_preview_row(
        waterfall: WaterfallData,
        index: SpectrogramIndex | None,
        frame: int,
    ) -> float:
        """Return the public, one-based source-frame coordinate for the Y axis."""
        total = index.frame_count if index is not None else waterfall.line_count
        return float(int(np.clip(frame, 0, max(0, total - 1))) + 1)

    @staticmethod
    def _frame_to_preview_index(
        waterfall: WaterfallData,
        index: SpectrogramIndex | None,
        frame: int,
    ) -> float:
        """Map a source frame to the sampled preview matrix index."""
        row_count = waterfall.values.shape[0] if waterfall.values is not None else 1
        if row_count <= 1:
            return 0.0
        if (
            index is not None
            and waterfall.timestamps is not None
            and waterfall.timestamps.size
            and frame < index.timestamps.size
            and np.isfinite(index.timestamps[frame])
        ):
            return float(
                np.interp(
                    index.timestamps[frame],
                    waterfall.timestamps,
                    np.arange(waterfall.timestamps.size, dtype=np.float64),
                )
            )
        total = index.frame_count if index is not None else row_count
        return frame * (row_count - 1) / max(1, total - 1)

    def first_frame(self) -> None:
        self._audit("user", "first_frame_requested")
        self.time_slider.setValue(self.time_slider.minimum())

    def last_frame(self) -> None:
        self._audit("user", "last_frame_requested")
        self.time_slider.setValue(self.time_slider.maximum())

    def previous_frame(self) -> None:
        self._audit("user", "previous_frame_requested", current_frame=self.time_slider.value())
        self.time_slider.setValue(max(self.time_slider.minimum(), self.time_slider.value() - 1))

    def next_frame(self) -> None:
        self._audit("user", "next_frame_requested", current_frame=self.time_slider.value())
        self.time_slider.setValue(min(self.time_slider.maximum(), self.time_slider.value() + 1))

    # --- Heatmap Spectrum ---------------------------------------------------
    def _heatmap_mode(self) -> PersistenceMode:
        return PersistenceMode(self.heatmap_range_mode.currentData())

    def _update_heatmap_controls_for_mode(self) -> None:
        """Enable/visibility matrix per mode (HMP-PERSIST-008 table)."""
        mode = self._heatmap_mode()
        live = mode in HEATMAP_LIVE_MODES
        decay = mode is PersistenceMode.EXPONENTIAL_DECAY
        selected = mode is PersistenceMode.SELECTED_RANGE
        self.heatmap_window_unit.setEnabled(live)
        seconds_unit = self.heatmap_window_unit.currentData() == WindowUnit.SECONDS
        self.heatmap_window_frames_spin.setEnabled(live and not seconds_unit)
        self.heatmap_window_seconds_spin.setEnabled(live and seconds_unit)
        self.heatmap_follow_playhead.setEnabled(live)
        if not live:
            self.heatmap_follow_playhead.setChecked(False)
        self.heatmap_half_life_row.setVisible(decay)
        self.heatmap_start_spin.setEnabled(selected)
        self.heatmap_end_spin.setEnabled(selected)
        self.heatmap_compute_mode.setEnabled(selected or mode is PersistenceMode.FULL_RECORDING)
        self.heatmap_recalculate_button.setText(
            "Rebuild now" if live else "Apply / Rebuild"
        )
        fixed_levels = self.heatmap_color_scale_mode.currentData() == ColorScaleMode.FIXED
        self.heatmap_color_min.setEnabled(fixed_levels)
        self.heatmap_color_max.setEnabled(fixed_levels)

    def _heatmap_color_levels_changed(self) -> None:
        """Visual-only color policy change: restyle, never recompute or reread."""
        if self._heatmap_restoring:
            return
        self._persist_heatmap_settings()
        self._update_heatmap_controls_for_mode()
        snapshot = self._heatmap_applied_snapshot
        if snapshot is None:
            return
        self._apply_snapshot_image(snapshot)
        self._audit(
            "user",
            "heatmap_color_levels_changed",
            color_scale_mode=str(self.heatmap_color_scale_mode.currentData()),
            color_min=self.heatmap_color_min.value(),
            color_max=self.heatmap_color_max.value(),
        )

    def _heatmap_normalization(self) -> HeatmapNormalization:
        return HeatmapNormalization(self.heatmap_normalization.currentData())

    def _heatmap_active_identity(self) -> tuple[str, str] | None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session is not None else None
        if session is None or waterfall is None:
            return None
        return (session.session_id, waterfall.waterfall_id)

    def _heatmap_context(self) -> tuple[MeasurementSession, WaterfallData, SpectrogramIndex] | None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session is not None else None
        if session is None or waterfall is None:
            return None
        index = self._spectrogram_indexes.get((session.session_id, waterfall.waterfall_id))
        if index is None or index.frame_count <= 0:
            return None
        return session, waterfall, index

    def _heatmap_session_exists(self, session_id: str) -> bool:
        try:
            return self.repository.get(session_id) is not None
        except KeyError:
            return False

    def _set_heatmap_status(self, text: str, *, error: bool = False) -> None:
        if not hasattr(self, "heatmap_status"):
            return
        self.heatmap_status.setText(text)
        self.heatmap_status.setStyleSheet("color: #ff5f56;" if error else "")

    def _heatmap_reset_overlay(self) -> None:
        self.spectrum_renderer.clear_heatmap()
        self._heatmap_applied_snapshot = None
        self._heatmap_applied = None
        self._heatmap_applied_key = None
        self._heatmap_applied_range = None

    @property
    def _heatmap_cache(self) -> HeatmapCache:
        """Compatibility alias: the fixed-result LRU cache is controller-owned (P2)."""
        return self._heatmap_controller.cache

    def _heatmap_build_config(self) -> HeatmapConfig:
        mode = self._heatmap_mode()
        frame_start = frame_end = None
        range_mode = HeatmapRangeMode.FULL
        if mode is PersistenceMode.SELECTED_RANGE:
            range_mode = HeatmapRangeMode.SELECTED
            frame_start = self.heatmap_start_spin.value() - 1
            frame_end = self.heatmap_end_spin.value() - 1
        return HeatmapConfig(
            range_mode=range_mode,
            window_frames=self.heatmap_window_frames_spin.value(),
            frame_start=frame_start,
            frame_end=frame_end,
            power_min_dbm=self.heatmap_power_min.value(),
            power_max_dbm=self.heatmap_power_max.value(),
            power_bins=int(self.heatmap_power_bins.currentText()),
            normalization=self._heatmap_normalization(),
            # Legacy decay coefficient is unused: decay mode runs through the
            # half-life engine since wave 3 (P4), never through this config.
            decay=1.0,
            sampling_policy=HeatmapSamplingPolicy(self.heatmap_compute_mode.currentData()),
        )

    # --- controller wiring (P2/P3) ------------------------------------------
    def _connect_heatmap_navigation(self) -> None:
        """Connect the logical FrameSpanEvent source exactly once (idempotent)."""
        if self._heatmap_navigation_connected:
            return
        self._heatmap_navigation_connected = True
        self._frame_nav.span_event.connect(self._on_heatmap_frame_span)
        # FrameRangeAnalysisBridge (frame_navigation.py) is intentionally NOT
        # used as a second router; its removal is a separate cleanup package.

    def _on_heatmap_frame_span(self, event: FrameSpanEvent) -> None:
        if not hasattr(self, "heatmap_enabled") or not self.heatmap_enabled.isChecked():
            return
        identity = self._heatmap_active_identity()
        controller_identity = self._heatmap_controller.context_identity
        if identity is None or controller_identity is None or identity != controller_identity:
            return
        self._heatmap_controller.on_frame_span(event)

    def _heatmap_controller_context(self) -> PersistenceSourceContext | None:
        context = self._heatmap_context()
        if context is None:
            return None
        session, waterfall, index = context
        source_id = waterfall.source_stream or waterfall.waterfall_id
        return PersistenceSourceContext(
            session_id=session.session_id,
            waterfall_id=waterfall.waterfall_id,
            source_id=source_id,
            source_path=session.source_path,
            frequencies_hz=waterfall.frequencies_hz,
            index=index,
            info=self._spectrogram_info(waterfall),
            source_key=PersistenceSourceKey(
                session_id=session.session_id,
                waterfall_id=waterfall.waterfall_id,
                source_id=source_id,
                frequency_grid_hash=frequency_grid_hash(waterfall.frequencies_hz),
            ),
            frame_period_s=self._heatmap_frame_period_s(session, waterfall, index),
        )

    @staticmethod
    def _heatmap_frame_period_s(
        session: MeasurementSession, waterfall: WaterfallData, index: SpectrogramIndex
    ) -> float | None:
        """Documented decay fallback data step: acquisition deadline first,
        then the median positive timestamp delta; None when neither is valid."""
        mode = str(waterfall.metadata.get("mode", ""))
        timing = session.acquisition_timing.get(mode)
        if timing is not None and timing.t_deadline_s is not None and timing.t_deadline_s > 0:
            return float(timing.t_deadline_s)
        if index.timestamps.size > 1:
            deltas = np.diff(index.timestamps)
            positive = deltas[np.isfinite(deltas) & (deltas > 0)]
            if positive.size:
                return float(np.median(positive))
        return None

    def _heatmap_render_budget_for_active_source(self):
        """Return the strict per-file overlap budget for the current Heatmap source."""
        fps = max(1, int(self.fps_combo.currentText()))
        playback_speed = (
            1.0
            if self.no_skip_check.isChecked()
            else float(self.speed_combo.currentText().replace("×", ""))
        )
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session is not None else None
        index = self._active_spectrogram_index(session) if session is not None else None
        if session is None or waterfall is None:
            return heatmap_render_budget(fps, playback_speed=playback_speed)
        mode = str(waterfall.metadata.get("mode", ""))
        timing = session.acquisition_timing.get(mode)
        instrument = timing.instrument_sweep_time_s if timing is not None else None
        recorded = timing.t_recorded_s if timing is not None else None
        if recorded is None and index is not None and index.timestamps.size > 1:
            deltas = np.diff(index.timestamps)
            positive = deltas[np.isfinite(deltas) & (deltas > 0)]
            if positive.size:
                recorded = float(np.median(positive))
        return heatmap_render_budget(
            fps,
            instrument_sweep_time_s=instrument,
            recorded_period_s=recorded,
            playback_speed=playback_speed,
        )

    def _refresh_heatmap_render_budget(self, _change: object | None = None) -> None:
        """Bind Rolling Exact windows to source rate, timestamp speed and UI refresh."""
        budget = self._heatmap_render_budget_for_active_source()
        self._heatmap_controller.set_render_fps(budget.render_fps)
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session is not None else None
        index = self._active_spectrogram_index(session) if session is not None else None
        frame_count = (
            index.frame_count if index is not None else waterfall.line_count if waterfall is not None else 0
        )
        recommended_frames = budget.recommended_window_frames if budget.available else 1
        minimum_frames = min(recommended_frames, max(1, frame_count)) if frame_count else 1
        frame_spin = self.heatmap_window_frames_spin
        was_blocked = frame_spin.blockSignals(True)
        try:
            frame_spin.setMinimum(minimum_frames)
            frame_spin.setMaximum(max(1_000_000, minimum_frames))
            if frame_spin.value() < minimum_frames:
                frame_spin.setValue(minimum_frames)
        finally:
            frame_spin.blockSignals(was_blocked)
        self._heatmap_window_minimum_frames = minimum_frames

        minimum_seconds = (
            max(0.001, minimum_frames * budget.effective_frame_period_s)
            if budget.available and budget.effective_frame_period_s is not None
            else None
        )
        seconds_spin = self.heatmap_window_seconds_spin
        was_blocked = seconds_spin.blockSignals(True)
        try:
            seconds_spin.setMinimum(minimum_seconds if minimum_seconds is not None else 0.001)
            if minimum_seconds is not None and seconds_spin.value() < minimum_seconds:
                seconds_spin.setValue(minimum_seconds)
        finally:
            seconds_spin.blockSignals(was_blocked)
        self._heatmap_window_minimum_seconds = minimum_seconds

        if budget.available and minimum_seconds is not None:
            period_us = budget.effective_frame_period_s * 1_000_000.0
            minimum_ms = minimum_seconds * 1_000.0
            self.heatmap_window_budget_label.setText(
                f"≥ {minimum_frames:,} кадров / {minimum_ms:.3f} мс "
                f"({budget.required_frames_per_refresh:,}/UI × {budget.safety_factor:g}; "
                f"Tэфф {period_us:.3f} мкс; {budget.render_fps} FPS; "
                f"{budget.playback_speed:g}×)"
            )
        else:
            self.heatmap_window_budget_label.setText(
                "≥ 1 кадр (SweepTime и фактический период недоступны)"
            )
        signature = (
            budget.render_fps,
            budget.playback_speed,
            budget.instrument_sweep_time_s,
            budget.recorded_period_s,
            budget.effective_frame_period_s,
            budget.required_frames_per_refresh,
            minimum_frames,
            minimum_seconds,
        )
        changed = signature != self._heatmap_render_budget_signature
        self._heatmap_render_budget_signature = signature
        if changed:
            self._audit(
                "heatmap",
                "HEATMAP_RENDER_WINDOW_BUDGET",
                render_fps=budget.render_fps,
                playback_speed=budget.playback_speed,
                instrument_sweep_time_s=budget.instrument_sweep_time_s,
                recorded_period_s=budget.recorded_period_s,
                effective_frame_period_s=budget.effective_frame_period_s,
                required_frames_per_refresh=budget.required_frames_per_refresh,
                recommended_window_frames=budget.recommended_window_frames,
                recommended_window_seconds=budget.recommended_window_seconds,
                applied_minimum_window_frames=minimum_frames,
                applied_minimum_window_seconds=minimum_seconds,
                safety_factor=budget.safety_factor,
            )
        if (
            changed
            and hasattr(self, "heatmap_enabled")
            and self.heatmap_enabled.isChecked()
            and self._heatmap_mode() is PersistenceMode.ROLLING_EXACT
            and self._heatmap_controller.context_identity is not None
        ):
            self._heatmap_controller.structural_config_changed(
                self._heatmap_build_persistence_config()
            )

    def _heatmap_build_persistence_config(self) -> PersistenceConfig:
        mode = self._heatmap_mode()
        seconds_unit = self.heatmap_window_unit.currentData() == WindowUnit.SECONDS
        half_life_s = self.heatmap_half_life_spin.value() * (
            0.001 if self.heatmap_half_life_unit.currentText() == "ms" else 1.0
        )
        return PersistenceConfig(
            mode=mode,
            window_unit=WindowUnit(self.heatmap_window_unit.currentData()),
            window_frames=self.heatmap_window_frames_spin.value(),
            window_seconds=self.heatmap_window_seconds_spin.value() if seconds_unit else None,
            half_life_seconds=(
                half_life_s if mode is PersistenceMode.EXPONENTIAL_DECAY else None
            ),
            decay_cutoff_epsilon=1e-3,
            follow_playhead=self.heatmap_follow_playhead.isChecked(),
            power_min_dbm=self.heatmap_power_min.value(),
            power_max_dbm=self.heatmap_power_max.value(),
            power_bins=int(self.heatmap_power_bins.currentText()),
            sampling_policy=HeatmapSamplingPolicy(self.heatmap_compute_mode.currentData()),
            minimum_window_frames=self._heatmap_window_minimum_frames,
            minimum_window_seconds=(
                self._heatmap_window_minimum_seconds if seconds_unit else None
            ),
        )

    def _heatmap_build_display_config(self) -> HeatmapDisplayConfig:
        return HeatmapDisplayConfig(
            normalization=self._heatmap_normalization(),
            palette=self.heatmap_palette.currentText(),
            opacity=self.heatmap_opacity.value(),
            color_scale_mode=ColorScaleMode(self.heatmap_color_scale_mode.currentData()),
            color_min=self.heatmap_color_min.value(),
            color_max=self.heatmap_color_max.value(),
        )

    def _heatmap_frame_timestamp(self, frame: int) -> float | None:
        session = self.active_session()
        index = self._active_spectrogram_index(session) if session is not None else None
        if index is not None and 0 <= frame < index.timestamps.size:
            value = float(index.timestamps[frame])
            return value if np.isfinite(value) else None
        return None

    def _heatmap_activate_current_mode(self, reason: str) -> None:
        mode = self._heatmap_mode()
        session = self.active_session()
        current_frame = session.current_frame if session is not None else 0
        if mode in HEATMAP_LIVE_MODES:
            self._heatmap_controller.enable(
                self._heatmap_build_persistence_config(),
                current_frame,
                self._heatmap_frame_timestamp(current_frame),
            )
        elif reason == "enabled":
            # Explicit user action (toggle on) computes once for any mode.
            self._heatmap_controller.request_fixed(self._heatmap_build_config(), current_frame)
        elif not self._heatmap_controller.try_show_cached(self._heatmap_build_config(), current_frame):
            self._set_heatmap_status("Heatmap не рассчитан — нажмите «Пересчитать»")

    def _current_heatmap_frame(self) -> int:
        session = self.active_session()
        return session.current_frame if session is not None else 0

    # --- snapshot / phase / failure application --------------------------------
    def _apply_persistence_snapshot(self, snapshot: PersistenceSnapshot) -> None:
        identity = (
            snapshot.source_key.session_id,
            snapshot.source_key.waterfall_id,
            snapshot.source_key.source_id,
        )
        if identity[:2] != self._heatmap_active_identity():
            return
        display = self._heatmap_build_display_config()
        if not self._apply_snapshot_image(snapshot, display=display):
            return  # unsupported grid: layer hidden, explicit error already reported
        self._heatmap_applied_snapshot = snapshot
        self._heatmap_applied = self._heatmap_result_from_snapshot(snapshot, display.normalization)
        self._heatmap_applied_key = identity
        self._heatmap_applied_range = (snapshot.frame_start, snapshot.frame_end)
        if self._heatmap_controller.phase is PersistencePhase.CURRENT:
            status = self._heatmap_phase_status_text(
                HeatmapPhaseEvent(
                    phase=PersistencePhase.CURRENT,
                    target_frame=snapshot.target_frame,
                    applied_frame=snapshot.target_frame,
                    frame_start=snapshot.frame_start,
                    frame_end=snapshot.frame_end,
                )
            )
            if status:
                self._set_heatmap_status(status)
        now = time.perf_counter()
        if self._heatmap_last_apply_at > 0.0:
            self._heatmap_render_intervals.append(now - self._heatmap_last_apply_at)
        self._heatmap_last_apply_at = now
        self._audit(
            "heatmap",
            "HEATMAP_APPLIED",
            generation=snapshot.generation,
            exact=snapshot.exact,
            approximate=snapshot.approximate,
            processed_frames=snapshot.processed_frames,
            frame_start=snapshot.frame_start,
            frame_end=snapshot.frame_end,
        )

    def _apply_snapshot_image(
        self,
        snapshot: PersistenceSnapshot,
        display: HeatmapDisplayConfig | None = None,
    ) -> bool:
        """Restyle the current snapshot with display policy; density untouched.

        Returns False (layer hidden, explicit error status + audit) when the
        frequency grid cannot be mapped to physical edges per §3.8 — never
        silently degrades to an inexact transform.
        """
        if display is None:
            display = self._heatmap_build_display_config()
        image = self._normalize_snapshot(snapshot, display.normalization)
        levels = self._heatmap_compute_levels(image, display)
        config = snapshot.config
        try:
            left, right = self._heatmap_frequency_edges(snapshot)
        except ValueError as exc:
            self.spectrum_renderer.clear_heatmap()
            self._set_heatmap_status(
                f"Неподдерживаемая частотная сетка Heatmap: {exc}", error=True
            )
            self._audit(
                "heatmap",
                "HEATMAP_FAILED",
                level=logging.ERROR,
                reason="unsupported_frequency_grid",
                message=str(exc),
            )
            return False
        self.spectrum_renderer.set_heatmap(
            image,
            left,
            right,
            config.power_min_dbm,
            config.power_max_dbm,
            levels=levels,
        )
        self._heatmap_current_levels = levels
        self._heatmap_controller.report_render_submitted(snapshot)
        return True

    def _heatmap_frequency_edges(self, snapshot: PersistenceSnapshot) -> tuple[float, float]:
        """Physical edges via §3.8; a single-bin grid uses the source step span."""
        span: float | None = None
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session is not None else None
        if waterfall is not None and waterfall.frequency_step_hz > 0:
            span = float(waterfall.frequency_step_hz)
        return frequency_bin_edges(snapshot.frequencies_hz, single_bin_span_hz=span)

    def _heatmap_compute_levels(
        self, image: np.ndarray, display: HeatmapDisplayConfig
    ) -> tuple[float, float]:
        """Display-levels policy (HMP-PERSIST-007); never touches density.

        Probability without an explicit user choice defaults to 0…1. FIXED
        uses the user's min/max. PERCENTILE uses the 2nd/99.5th percentile of
        non-zero cells. SMOOTHED_AUTO applies a documented EMA (alpha 0.3) to
        the per-snapshot maximum so levels do not flicker between updates.
        """
        finite = image[np.isfinite(image)]
        vmax = float(finite.max()) if finite.size else 0.0
        if display.normalization is HeatmapNormalization.PROBABILITY and (
            display.color_scale_mode is ColorScaleMode.AUTO_CURRENT
        ):
            return (0.0, 1.0)
        if display.color_scale_mode is ColorScaleMode.FIXED:
            if display.color_min is not None and display.color_max is not None and display.color_min < display.color_max:
                return (float(display.color_min), float(display.color_max))
        elif display.color_scale_mode is ColorScaleMode.PERCENTILE:
            nonzero = finite[finite > 0]
            if nonzero.size:
                high = float(np.percentile(nonzero, 99.5))
                return (0.0, high if high > 0.0 else 1.0)
        elif display.color_scale_mode is ColorScaleMode.SMOOTHED_AUTO:
            previous = self._heatmap_current_levels
            if previous is not None and vmax > 0.0:
                smoothed = 0.7 * previous[1] + 0.3 * vmax
                return (0.0, max(smoothed, 1e-12))
        return (0.0, vmax if vmax > 0.0 else 1.0)

    @staticmethod
    def _normalize_snapshot(snapshot: PersistenceSnapshot, mode: HeatmapNormalization) -> np.ndarray:
        density = np.array(snapshot.density, dtype=np.float64)
        if mode is HeatmapNormalization.COUNT:
            return density
        if mode is HeatmapNormalization.PROBABILITY:
            weights = np.asarray(snapshot.normalization_weights_by_frequency, dtype=np.float64)
            if weights.shape != (density.shape[1],):
                return np.zeros_like(density)
            probability = np.zeros_like(density)
            np.divide(density, weights, out=probability, where=weights > 0.0)
            return probability
        return np.log10(1.0 + density)

    def _heatmap_result_from_snapshot(
        self, snapshot: PersistenceSnapshot, normalization: HeatmapNormalization
    ) -> HeatmapResult:
        """Compatibility adapter: export/tests consume HeatmapResult for one package."""
        config = snapshot.config
        if config.mode is PersistenceMode.FULL_RECORDING:
            range_mode = HeatmapRangeMode.FULL
        elif config.mode is PersistenceMode.SELECTED_RANGE:
            range_mode = HeatmapRangeMode.SELECTED
        elif config.mode is PersistenceMode.EXPONENTIAL_DECAY:
            range_mode = HeatmapRangeMode.EXPONENTIAL_DECAY
        else:
            range_mode = HeatmapRangeMode.LAST_N
        heatmap_config = HeatmapConfig(
            range_mode=range_mode,
            window_frames=config.window_frames,
            frame_start=snapshot.frame_start if range_mode is HeatmapRangeMode.SELECTED else None,
            frame_end=snapshot.frame_end if range_mode is HeatmapRangeMode.SELECTED else None,
            power_min_dbm=config.power_min_dbm,
            power_max_dbm=config.power_max_dbm,
            power_bins=config.power_bins,
            normalization=normalization,
            sampling_policy=config.sampling_policy,
        )
        return HeatmapResult(
            density=snapshot.density.copy(),
            frequencies_hz=snapshot.frequencies_hz.copy(),
            power_axis_dbm=snapshot.power_axis_dbm.copy(),
            processed_frames=snapshot.processed_frames,
            total_frames_in_range=snapshot.frame_end - snapshot.frame_start + 1,
            exact=snapshot.exact,
            sampling_policy=config.sampling_policy,
            config=heatmap_config,
            generation=snapshot.generation,
            frequency_grid_hash=snapshot.source_key.frequency_grid_hash,
            approximate=snapshot.approximate,
            computed_at=snapshot.computed_at or None,
            normalization_weights_by_frequency=snapshot.normalization_weights_by_frequency.copy(),
        )

    def _apply_heatmap_phase(self, event: HeatmapPhaseEvent) -> None:
        if event.hide_layer:
            self.spectrum_renderer.set_heatmap_stale(True, hide=True)
        elif event.phase is PersistencePhase.CURRENT:
            self.spectrum_renderer.set_heatmap_stale(False, hide=False)
        status = self._heatmap_phase_status_text(event)
        if status:
            self._set_heatmap_status(status, error=event.phase is PersistencePhase.ERROR)

    def _heatmap_phase_status_text(self, event: HeatmapPhaseEvent) -> str:
        def f1(value: int | None) -> str:
            return f"{value + 1:,}" if value is not None else "—"

        if event.phase is PersistencePhase.REBUILDING:
            progress = (
                f" · {event.processed_frames:,}/{event.total_frames:,}" if event.total_frames else ""
            )
            return (
                f"Rolling Exact · Rebuilding · target frame {f1(event.target_frame)} · "
                f"frames {f1(event.frame_start)}…{f1(event.frame_end)}{progress}"
            )
        if event.phase is PersistencePhase.UPDATING:
            return (
                f"Rolling Exact · Updating · applied {f1(event.applied_frame)} · "
                f"target {f1(event.target_frame)} · lag {event.lag_frames:,} frames"
            )
        if event.phase is PersistencePhase.CURRENT:
            snapshot = self._heatmap_applied_snapshot
            if snapshot is not None:
                mode = snapshot.config.mode
                if mode is PersistenceMode.FULL_RECORDING:
                    preview = "" if snapshot.exact else " · Preview"
                    return (
                        f"Full Recording · Fixed{preview} · frames 1…{snapshot.frame_end + 1:,} · "
                        f"playback does not change this layer"
                    )
                if mode is PersistenceMode.SELECTED_RANGE:
                    preview = "" if snapshot.exact else " · Preview"
                    return (
                        f"Selected Range · Fixed{preview} · frames "
                        f"{snapshot.frame_start + 1:,}…{snapshot.frame_end + 1:,}"
                    )
                if mode is PersistenceMode.EXPONENTIAL_DECAY:
                    half_life = snapshot.half_life_seconds
                    half_text = f"{half_life:g}" if half_life is not None else "—"
                    return (
                        f"Exponential Decay · Current · half-life {half_text} s · "
                        f"target {f1(event.target_frame)}"
                    )
                count = snapshot.processed_frames
                return (
                    f"Rolling Exact · Current · {count:,} frames · "
                    f"frames {f1(event.frame_start)}…{f1(event.frame_end)}"
                )
        if event.phase is PersistencePhase.CANCELLED:
            return "Отменено"
        if event.phase is PersistencePhase.EMPTY:
            return "Heatmap не рассчитан — нажмите «Пересчитать»"
        if event.phase is PersistencePhase.DISABLED:
            return "Heatmap выключен"
        if event.phase is PersistencePhase.ERROR:
            return f"Ошибка расчёта Heatmap: {event.message}"
        return ""

    def _heatmap_controller_failed(self, message: str, details: str) -> None:
        LOGGER.error("Heatmap controller failure: %s\n%s", message, details)
        self._audit("heatmap", "HEATMAP_FAILED", level=logging.ERROR, message=message)

    def _heatmap_context_changed(self) -> None:
        """Session/waterfall switch or index availability: thin controller handoff (§5.9)."""
        identity = self._heatmap_active_identity()
        if identity == self._heatmap_last_context_identity:
            return
        self._heatmap_last_context_identity = identity
        self._heatmap_reset_overlay()
        self._refresh_heatmap_render_budget()
        if not hasattr(self, "heatmap_enabled"):
            return
        context = self._heatmap_controller_context()
        self._heatmap_controller.set_context(context)
        if not self.heatmap_enabled.isChecked():
            return
        if context is None:
            self._set_heatmap_status("Нет данных: откройте DFL и дождитесь индекса waterfall")
            return
        self._heatmap_activate_current_mode("context_changed")

    def _heatmap_index_ready(self) -> None:
        self._refresh_heatmap_render_budget()
        if not hasattr(self, "heatmap_enabled") or not self.heatmap_enabled.isChecked():
            return
        context = self._heatmap_controller_context()
        if context is None:
            return
        self._heatmap_controller.set_context(context)
        self._heatmap_activate_current_mode("index_ready")

    def _heatmap_on_session_removed(self, session_id: str) -> None:
        self._heatmap_controller.invalidate_session(session_id)
        if self._heatmap_applied_key is not None and self._heatmap_applied_key[0] == session_id:
            self._heatmap_reset_overlay()
        if self._heatmap_last_context_identity is not None and self._heatmap_last_context_identity[0] == session_id:
            self._heatmap_last_context_identity = None

    def _heatmap_toggled(self, enabled: bool) -> None:
        if self._heatmap_restoring:
            return  # restore echo: no persist, no audit, no compute
        self._persist_heatmap_settings()
        self._audit("user", "heatmap_toggled", enabled=enabled)
        if not enabled:
            self._heatmap_controller.disable()
            self._heatmap_reset_overlay()
            self._set_heatmap_status("Heatmap выключен")
            return
        context = self._heatmap_controller_context()
        self._heatmap_controller.set_context(context)
        if context is None:
            self._set_heatmap_status("Нет данных: откройте DFL и дождитесь индекса waterfall", error=True)
            return
        self._heatmap_activate_current_mode("enabled")

    def _heatmap_structural_changed(self) -> None:
        if not hasattr(self, "heatmap_enabled") or self._heatmap_restoring:
            return
        # Persist on change (not in closeEvent): immediate durability, and
        # windows that never touch heatmap controls never write heatmap keys.
        self._persist_heatmap_settings()
        self._update_heatmap_controls_for_mode()
        if not self.heatmap_enabled.isChecked():
            return
        if self._heatmap_mode() in HEATMAP_LIVE_MODES:
            self._heatmap_controller.structural_config_changed(
                self._heatmap_build_persistence_config(),
            )
            return
        # SELECTED/FULL recompute only via «Пересчитать» (§5.8: clear + wait).
        self._heatmap_controller.clear()
        self._heatmap_reset_overlay()
        self._set_heatmap_status("Параметры изменены — нажмите «Пересчитать»")

    def _heatmap_recalculate(self) -> None:
        if self._heatmap_mode() in HEATMAP_LIVE_MODES:
            self._heatmap_controller.recalculate()
            return
        self._heatmap_controller.request_fixed(self._heatmap_build_config(), self._current_heatmap_frame())

    def _heatmap_cancel(self) -> None:
        if self._heatmap_controller.active_ticket is None and self._heatmap_controller.pending_ticket is None:
            self._set_heatmap_status("Нет активного расчёта")
            return
        self._set_heatmap_status("Отмена расчёта…")
        self._heatmap_controller.cancel()

    def _heatmap_clear(self) -> None:
        self._heatmap_controller.clear()
        self._heatmap_reset_overlay()
        self._set_heatmap_status("Heatmap очищен")
        self._audit("user", "heatmap_cleared")

    def _heatmap_normalization_changed(self) -> None:
        if self._heatmap_restoring:
            return  # restore echo; normalization applies at the next apply anyway
        self._persist_heatmap_settings()
        snapshot = self._heatmap_applied_snapshot
        if snapshot is None:
            return
        if self._heatmap_applied_key is not None and self._heatmap_applied_key[:2] != self._heatmap_active_identity():
            return
        self._apply_snapshot_image(snapshot)
        self._audit(
            "user", "heatmap_normalization_changed", normalization=self._heatmap_normalization().value
        )

    def _heatmap_opacity_changed(self, value: float) -> None:
        self._persist_heatmap_settings()
        self.spectrum_renderer.set_heatmap_opacity(float(value))
        self._audit("user", "heatmap_opacity_changed", opacity=float(value))

    def _heatmap_palette_changed(self, name: str) -> None:
        self._persist_heatmap_settings()
        self.spectrum_renderer.set_heatmap_palette(name)
        self._audit("user", "heatmap_palette_changed", palette=name)

    def heatmap_diagnostics(self) -> dict[str, float | int]:
        """Diagnostic counters (ТЗ §17 + lag metrics, read-only snapshot).

        Counters and lag metrics are controller-owned; MainWindow adds render
        FPS and the memory estimate (fixed cache + applied density + the
        renderer's float32 image; the transient float64 normalized copy is
        short-lived and intentionally not counted).
        """
        diag = self._heatmap_controller.diagnostics()
        intervals = sorted(self._heatmap_render_intervals)
        if intervals:
            mean = sum(intervals) / len(intervals)
            diag["heatmap_render_fps"] = 1.0 / mean if mean > 0.0 else 0.0
        else:
            diag["heatmap_render_fps"] = 0.0
        memory = self._heatmap_controller.cache.total_size_bytes
        if self._heatmap_applied_snapshot is not None:
            memory += int(self._heatmap_applied_snapshot.density.nbytes)
        renderer = self.spectrum_renderer
        image = getattr(renderer.heatmap_image, "image", None)
        if renderer.heatmap_visible and image is not None:
            memory += int(image.nbytes)
        diag["heatmap_memory_bytes"] = int(memory)
        return diag

    @staticmethod
    def _settings_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _settings_float(value: Any, default: float) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return default
        return result if math.isfinite(result) else default

    @staticmethod
    def _settings_bool(value: Any, default: bool) -> bool:
        # QSettings may return a string ("true"/"false") for a persisted bool;
        # parse it explicitly instead of relying on bool(str).
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().casefold()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
        return default

    def _restore_heatmap_settings(self) -> None:
        settings = self.settings
        # Suppress persist-on-change and rolling triggers for the whole restore:
        # applying one control must not persist the not-yet-restored defaults
        # of the remaining controls over their persisted values.
        self._heatmap_restoring = True
        try:
            persistence_mode = settings.value("heatmap/persistence_mode")
            if persistence_mode is None:
                # Migration: legacy heatmap/range_mode maps into persistence_mode.
                # last_n/centered -> rolling_exact, full -> full_recording,
                # selected -> selected_range. The legacy heatmap/decay
                # coefficient is detected but never converted to seconds.
                legacy = str(settings.value("heatmap/range_mode", "last_n"))
                migration = {
                    "last_n": "rolling_exact",
                    "centered": "rolling_exact",
                    "full": "full_recording",
                    "selected": "selected_range",
                    "exponential_decay": "exponential_decay",
                }
                persistence_mode = migration.get(legacy, "rolling_exact")
                if settings.contains("heatmap/range_mode"):
                    self._audit(
                        "heatmap",
                        "HEATMAP_SETTINGS_MIGRATED",
                        old_mode=legacy,
                        new_mode=persistence_mode,
                    )
            self._combo_set_data(self.heatmap_range_mode, str(persistence_mode))
            self._combo_set_data(self.heatmap_window_unit, str(settings.value("heatmap/window_unit", "frames")))
            self.heatmap_window_frames_spin.setValue(self._settings_int(settings.value("heatmap/window_frames"), 500))
            self.heatmap_window_seconds_spin.setValue(
                self._settings_float(settings.value("heatmap/window_seconds"), 10.0)
            )
            self.heatmap_follow_playhead.setChecked(
                self._settings_bool(settings.value("heatmap/follow_playhead"), True)
            )
            self._combo_set_data(
                self.heatmap_compute_mode, str(settings.value("heatmap/sampling_policy", "full_range"))
            )
            self._combo_set_data(
                self.heatmap_normalization, str(settings.value("heatmap/normalization", "log_density"))
            )
            self.heatmap_power_min.setValue(self._settings_float(settings.value("heatmap/power_min_dbm"), -120.0))
            self.heatmap_power_max.setValue(self._settings_float(settings.value("heatmap/power_max_dbm"), 0.0))
            power_bins = str(settings.value("heatmap/power_bins", "256"))
            if self.heatmap_power_bins.findText(power_bins) >= 0:
                self.heatmap_power_bins.setCurrentText(power_bins)
            self.heatmap_opacity.setValue(self._settings_float(settings.value("heatmap/opacity"), 0.65))
            palette = str(settings.value("heatmap/palette", "Viridis"))
            if self.heatmap_palette.findText(palette) >= 0:
                self.heatmap_palette.setCurrentText(palette)
            self._combo_set_data(
                self.heatmap_color_scale_mode, str(settings.value("heatmap/color_scale_mode", "auto_current"))
            )
            self.heatmap_color_min.setValue(self._settings_float(settings.value("heatmap/color_min"), 0.0))
            self.heatmap_color_max.setValue(self._settings_float(settings.value("heatmap/color_max"), 1.0))
            self.heatmap_half_life_spin.setValue(
                self._settings_float(settings.value("heatmap/half_life_seconds"), 1.0)
            )
            half_life_unit = str(settings.value("heatmap/half_life_unit", "s"))
            if self.heatmap_half_life_unit.findText(half_life_unit) >= 0:
                self.heatmap_half_life_unit.setCurrentText(half_life_unit)
            self.heatmap_enabled.setChecked(self._settings_bool(settings.value("heatmap/enabled"), False))
            self._update_heatmap_controls_for_mode()
        finally:
            self._heatmap_restoring = False

    def _persist_heatmap_settings(self) -> None:
        if self._heatmap_restoring:
            return
        self.settings.setValue("heatmap/enabled", self.heatmap_enabled.isChecked())
        self.settings.setValue("heatmap/persistence_mode", self._heatmap_mode().value)
        self.settings.setValue("heatmap/window_unit", WindowUnit(self.heatmap_window_unit.currentData()).value)
        self.settings.setValue("heatmap/window_frames", self.heatmap_window_frames_spin.value())
        self.settings.setValue("heatmap/window_seconds", self.heatmap_window_seconds_spin.value())
        self.settings.setValue("heatmap/follow_playhead", self.heatmap_follow_playhead.isChecked())
        self.settings.setValue(
            "heatmap/sampling_policy",
            HeatmapSamplingPolicy(self.heatmap_compute_mode.currentData()).value,
        )
        self.settings.setValue("heatmap/normalization", self._heatmap_normalization().value)
        self.settings.setValue("heatmap/power_min_dbm", self.heatmap_power_min.value())
        self.settings.setValue("heatmap/power_max_dbm", self.heatmap_power_max.value())
        self.settings.setValue("heatmap/power_bins", self.heatmap_power_bins.currentText())
        self.settings.setValue("heatmap/opacity", self.heatmap_opacity.value())
        self.settings.setValue("heatmap/palette", self.heatmap_palette.currentText())
        self.settings.setValue(
            "heatmap/color_scale_mode",
            ColorScaleMode(self.heatmap_color_scale_mode.currentData()).value,
        )
        self.settings.setValue("heatmap/color_min", self.heatmap_color_min.value())
        self.settings.setValue("heatmap/color_max", self.heatmap_color_max.value())
        self.settings.setValue("heatmap/half_life_seconds", self.heatmap_half_life_spin.value())
        self.settings.setValue("heatmap/half_life_unit", self.heatmap_half_life_unit.currentText())

    @staticmethod
    def _combo_set_data(combo: QComboBox, value: str) -> None:
        for row in range(combo.count()):
            data = combo.itemData(row)
            if getattr(data, "value", data) == value:
                combo.setCurrentIndex(row)
                return

    # --- export and workspace -------------------------------------------
    def export_active_trace(self) -> None:
        session = self.active_session()
        trace = self._active_trace(session) if session else None
        if trace is None:
            return
        path = self._save_path("Экспорт трассы", "CSV (*.csv)", f"{session.name}_{trace.trace_id.split(':')[-1]}.csv")
        if path:
            self._start_export(export_trace_csv, trace, path)

    def export_all_traces(self) -> None:
        traces = [trace for session in self.repository.all() for trace in session.traces.values() if trace.enabled]
        if not traces:
            return
        path = self._save_path("Экспорт трасс", "CSV (*.csv)", "traces.csv")
        if path:
            worker = TaskWorker(export_traces_csv, traces, path, pass_progress=True, pass_cancel=True)
            self._start_worker(worker)

    def export_npz(self) -> None:
        session = self.active_session()
        if session:
            path = self._save_path("Экспорт NPZ", "NumPy (*.npz)", f"{session.name}.npz")
            if path:
                self._start_export(export_session_npz, session, path)

    def export_waterfall(self) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if waterfall:
            path = self._save_path("Экспорт waterfall", "CSV (*.csv)", f"{session.name}_waterfall.csv")
            if path:
                self._start_export(export_waterfall_region_csv, waterfall, path)

    def export_animation(self, suffix: str) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if session is None or waterfall is None or waterfall.values is None:
            return
        path = self._save_path(
            f"Экспорт {suffix.upper()}",
            f"{suffix.upper()} (*.{suffix})",
            f"{session.name}_waterfall.{suffix}",
        )
        if not path:
            return
        preview = SpectrogramPreview(
            info=self._spectrogram_info(waterfall),
            line_indices=(
                waterfall.line_indices
                if waterfall.line_indices is not None
                else np.arange(waterfall.values.shape[0], dtype=np.int64)
            ),
            timestamps=(
                waterfall.timestamps
                if waterfall.timestamps is not None
                else np.arange(waterfall.values.shape[0], dtype=np.float64)
            ),
            values=waterfall.values,
        )
        worker = TaskWorker(
            export_waterfall_animation,
            preview,
            path,
            int(round(self._frame_to_preview_index(
                waterfall, self._active_spectrogram_index(session), self.time_slider.value()
            ))),
            waterfall.values.shape[0] - 1,
            fps=float(self.fps_combo.currentText()),
            max_frames=300,
            cmap=(
                "gray"
                if self.colormap_combo.currentText() == "Grayscale"
                else self.colormap_combo.currentText().casefold()
            ),
            vmin=self.level_min.value(),
            vmax=self.level_max.value(),
            pass_progress=True,
            pass_cancel=True,
        )
        self._audit(
            "user",
            "waterfall_animation_export_requested",
            format=suffix.lower(),
            path=str(path),
            fps=float(self.fps_combo.currentText()),
            level_min=self.level_min.value(),
            level_max=self.level_max.value(),
        )
        self._set_busy(True, f"Экспорт {suffix.upper()}…")
        self._start_worker(worker)

    def export_markers(self) -> None:
        session = self.active_session()
        if session:
            path = self._save_path("Экспорт маркеров", "CSV (*.csv)", f"{session.name}_markers.csv")
            if path:
                self._start_export(export_markers_csv, session.markers, path)

    def export_results(self) -> None:
        session = self.active_session()
        if session:
            path = self._save_path("Экспорт измерений", "CSV (*.csv)", f"{session.name}_measurements.csv")
            if path:
                self._start_export(export_results_csv, session.analysis_results, path)

    def _current_time_gated_result(self) -> tuple[
        MeasurementSession, WaterfallData, TimeGatedChannelPowerResult
    ] | None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if session is None or waterfall is None:
            return None
        result = self._channel_power_results.get((session.session_id, waterfall.waterfall_id))
        if result is None:
            self._show_error("Экспорт Channel Power", "Сначала выполните расчёт")
            return None
        return session, waterfall, result

    def export_time_gated_summary(self) -> None:
        current = self._current_time_gated_result()
        if current is None:
            return
        session, _waterfall, result = current
        trace = self._active_frequency_trace(session)
        path = self._save_path(
            "Channel Power Summary", "CSV (*.csv)", f"{session.name}_channel_power_summary.csv"
        )
        if path:
            self._start_export(
                export_time_gated_summary_csv,
                result,
                path,
                session.source_path,
                trace.name if trace else result.request.trace_id,
            )

    def export_time_gated_frames(self) -> None:
        current = self._current_time_gated_result()
        if current is None:
            return
        session, _waterfall, result = current
        path = self._save_path(
            "Channel Power Frames", "CSV (*.csv)", f"{session.name}_channel_power_frames.csv"
        )
        if path:
            self._start_long_export(export_time_gated_frames_csv, result, path)

    def export_time_gated_events(self) -> None:
        current = self._current_time_gated_result()
        if current is None:
            return
        session, _waterfall, result = current
        path = self._save_path(
            "Channel Power Events", "CSV (*.csv)", f"{session.name}_channel_power_events.csv"
        )
        if path:
            self._start_export(export_time_gated_events_csv, result, path)

    def export_time_gated_json(self) -> None:
        current = self._current_time_gated_result()
        if current is None:
            return
        session, _waterfall, result = current
        trace = self._active_frequency_trace(session)
        path = self._save_path(
            "Channel Power JSON", "JSON (*.json)", f"{session.name}_channel_power.json"
        )
        if path:
            self._start_long_export(
                export_time_gated_json,
                result,
                path,
                session.source_path,
                trace.name if trace else result.request.trace_id,
            )

    def _current_heatmap_result(self) -> tuple[MeasurementSession, WaterfallData, HeatmapResult] | None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session is not None else None
        result = self._heatmap_applied
        if session is None or waterfall is None or result is None or self._heatmap_applied_key is None:
            self._show_error("Экспорт Heatmap", "Сначала выполните расчёт Heatmap")
            return None
        # Export is blocked while a rebuild makes the displayed layer stale (§HMP-PERSIST-003).
        if self._heatmap_controller.phase in (PersistencePhase.REBUILDING, PersistencePhase.STALE):
            self._show_error("Экспорт Heatmap", "Результат устарел — дождитесь завершения пересчёта")
            return None
        if self._heatmap_applied_key[:2] != (session.session_id, waterfall.waterfall_id):
            self._show_error(
                "Экспорт Heatmap", "Рассчитанный Heatmap относится к другой сессии или потоку"
            )
            return None
        return session, waterfall, result

    def export_heatmap_png(self) -> None:
        current = self._current_heatmap_result()
        if current is None:
            return
        session, _waterfall, result = current
        path = self._save_path("Экспорт Heatmap PNG", "PNG (*.png)", f"{session.name}_heatmap.png")
        if not path:
            return
        self._audit("user", "export_requested", exporter="export_heatmap_png", target=path)
        try:
            snapshot = self._heatmap_applied_snapshot
            normalized = (
                self._normalize_snapshot(snapshot, self._heatmap_normalization())
                if snapshot is not None
                else result.normalized(self._heatmap_normalization())
            )
            written = export_heatmap_png(
                result,
                normalized,
                self.spectrum_renderer.heatmap_lut,
                self.heatmap_opacity.value(),
                path,
                levels=self._heatmap_current_levels,
            )
        except Exception as exc:
            self._show_error("Экспорт Heatmap", str(exc))
            return
        self._export_completed("export_heatmap_png", written)

    def export_heatmap_csv(self) -> None:
        current = self._current_heatmap_result()
        if current is None:
            return
        session, _waterfall, result = current
        path = self._save_path("Экспорт Heatmap CSV", "CSV (*.csv)", f"{session.name}_heatmap.csv")
        if path:
            self._start_long_export(export_heatmap_csv, result, path)

    def export_heatmap_npz(self) -> None:
        current = self._current_heatmap_result()
        if current is None:
            return
        session, _waterfall, result = current
        path = self._save_path("Экспорт Heatmap NPZ", "NumPy (*.npz)", f"{session.name}_heatmap.npz")
        if path:
            self._start_export(export_heatmap_npz, result, path)

    def export_heatmap_json(self) -> None:
        current = self._current_heatmap_result()
        if current is None:
            return
        session, waterfall, result = current
        path = self._save_path(
            "Экспорт Heatmap JSON", "JSON (*.json)", f"{session.name}_heatmap.json"
        )
        if not path:
            return
        assert self._heatmap_applied_key is not None  # guaranteed by _current_heatmap_result
        self._start_export(
            partial(
                export_heatmap_json,
                result,
                path,
                source_path=session.source_path,
                session_id=session.session_id,
                waterfall_id=waterfall.waterfall_id,
                source_id=self._heatmap_applied_key[2],
                frame_range=self._heatmap_applied_range,
                display_config=self._heatmap_build_display_config(),
                persistence_snapshot=self._heatmap_applied_snapshot,
            )
        )

    def export_metadata(self) -> None:
        session = self.active_session()
        if session:
            path = self._save_path("Экспорт метаданных", "JSON (*.json)", f"{session.name}_metadata.json")
            if path:
                self._start_export(export_session_json, session, path)

    def export_screenshot(self) -> None:
        path = self._save_path("Снимок рабочего пространства", "PNG (*.png)", "workspace.png")
        if not path:
            return
        target = Path(path)
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            if not self.grab().save(str(temporary), "PNG"):
                raise OSError("Qt не смог сохранить изображение")
            temporary.replace(target)
            LOGGER.info("Снимок сохранён: %s", target)
            self._audit("program", "screenshot_exported", path=str(target))
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            self._show_error("Экспорт", str(exc))

    def _start_export(self, function: Callable[..., Any], *args: Any) -> None:
        self._audit(
            "user",
            "export_requested",
            exporter=getattr(function, "__name__", type(function).__name__),
            target=self._export_target(args),
        )
        self._set_busy(True, "Экспорт…")
        worker = TaskWorker(function, *args)
        worker.signals.result.connect(
            lambda path, name=getattr(function, "__name__", type(function).__name__):
            self._export_completed(name, path)
        )
        self._start_worker(worker)

    def _start_long_export(self, function: Callable[..., Any], *args: Any) -> None:
        self._audit(
            "user",
            "export_requested",
            exporter=getattr(function, "__name__", type(function).__name__),
            target=self._export_target(args),
            long_operation=True,
        )
        self._set_busy(True, "Экспорт…")
        worker = TaskWorker(function, *args, pass_progress=True, pass_cancel=True)
        worker.signals.result.connect(
            lambda path, name=getattr(function, "__name__", type(function).__name__):
            self._export_completed(name, path)
        )
        self._start_worker(worker)

    def _export_completed(self, exporter: str, path: Any) -> None:
        LOGGER.info("Экспортировано: %s", path)
        self._audit("program", "export_completed", exporter=exporter, path=str(path))

    @staticmethod
    def _export_target(args: tuple[Any, ...]) -> str | None:
        export_suffixes = {".csv", ".json", ".npz", ".png", ".gif", ".mp4"}
        for value in args:
            if isinstance(value, (str, Path)) and Path(value).suffix.casefold() in export_suffixes:
                return str(value)
        return None

    def _save_path(self, title: str, file_filter: str, suggestion: str) -> str:
        path, _ = QFileDialog.getSaveFileName(self, title, suggestion, file_filter)
        self._audit(
            "user",
            "save_dialog_completed",
            title=title,
            selected=bool(path),
            path=path or None,
        )
        return path

    def save_workspace(self, choose_path: bool = False) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if session is not None and waterfall is not None:
            self._persist_channel_power_state(session, waterfall.waterfall_id)
        if choose_path or self._current_workspace is None:
            path, _ = QFileDialog.getSaveFileName(self, "Сохранить workspace", "workspace.rsdfl.json", "Workspace (*.json)")
            self._audit(
                "user",
                "workspace_save_dialog_completed",
                selected=bool(path),
                path=path or None,
            )
            if not path:
                return
            self._current_workspace = Path(path)
        write_workspace(
            self._current_workspace, self.repository.all(), self.active_session_id,
            {"theme": self.theme_combo.currentText(), "splitter": bytes(self.central_splitter.saveState().toBase64()).decode("ascii")},
        )
        LOGGER.info("Workspace сохранён: %s", self._current_workspace)
        self._audit("program", "workspace_saved", path=str(self._current_workspace))

    def open_workspace(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Открыть workspace", "", "Workspace (*.json)")
        self._audit(
            "user",
            "workspace_open_dialog_completed",
            selected=bool(path),
            path=path or None,
        )
        if not path:
            return
        try:
            payload = read_workspace(path)
        except Exception as exc:
            self._show_error("Workspace", str(exc))
            return
        self._current_workspace = Path(path)
        self._audit("user", "workspace_opened", path=str(self._current_workspace))
        self._workspace_payloads = {
            str(Path(item["source_path"]).resolve()).casefold(): item for item in payload["sessions"]
        }
        ui = payload.get("ui_state", {})
        if ui.get("theme"):
            self.theme_combo.setCurrentText(ui["theme"])
        if ui.get("splitter"):
            self.central_splitter.restoreState(QByteArray.fromBase64(ui["splitter"].encode("ascii")))
        for item in payload["sessions"]:
            self.load_file(Path(item["source_path"]), item)

    # --- helpers ---------------------------------------------------------
    def _audit(
        self,
        category: str,
        event: str,
        *,
        level: int = logging.INFO,
        **details: Any,
    ) -> None:
        session = self.active_session()
        if session is not None:
            details.setdefault("session_id", session.session_id)
            details.setdefault("source_path", str(session.source_path))
        log_event(LOGGER, category, event, level=level, **details)

    def _start_worker(self, worker: TaskWorker) -> None:
        self._workers.add(worker)
        self._audit(
            "program",
            "worker_started",
            function=getattr(worker.function, "__name__", type(worker.function).__name__),
            active_workers=len(self._workers),
        )
        worker.signals.progress.connect(self._worker_progress)
        worker.signals.error.connect(self._worker_error)
        worker.signals.cancelled.connect(lambda worker=worker: self._worker_cancelled(worker))
        worker.signals.finished.connect(lambda worker=worker: self._worker_finished(worker))
        self.thread_pool.start(worker)

    def _worker_progress(self, fraction: float, text: str) -> None:
        self.progress.show()
        self.progress.setValue(int(np.clip(fraction, 0.0, 1.0) * 1000))
        self.statusBar().showMessage(text)
        bucket = int(np.clip(fraction, 0.0, 1.0) * 4)
        if bucket != self._last_progress_bucket:
            self._last_progress_bucket = bucket
            self._audit(
                "program",
                "worker_progress",
                fraction=float(np.clip(fraction, 0.0, 1.0)),
                status=text,
            )

    def _worker_error(self, message: str, details: str) -> None:
        LOGGER.error("%s\n%s", message, details)
        self._audit("program", "worker_failed", level=logging.ERROR, message=message, traceback=details)
        self._show_error("Ошибка операции", message)

    def _worker_cancelled(self, worker: TaskWorker) -> None:
        LOGGER.info("Операция отменена")
        self._audit(
            "program",
            "worker_cancelled",
            function=getattr(worker.function, "__name__", type(worker.function).__name__),
        )

    def _worker_finished(self, worker: TaskWorker) -> None:
        self._workers.discard(worker)
        self._audit(
            "program",
            "worker_finished",
            function=getattr(worker.function, "__name__", type(worker.function).__name__),
            active_workers=len(self._workers),
        )
        if not self._workers:
            self._set_busy(False)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.progress.setVisible(busy)
        if busy:
            self._last_progress_bucket = -1
            self.progress.setRange(0, 0)
            self.statusBar().showMessage(text)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(0)
            self.statusBar().clearMessage()

    def active_session(self) -> MeasurementSession | None:
        if self.active_session_id is None:
            return None
        try:
            return self.repository.get(self.active_session_id)
        except KeyError:
            return None

    @staticmethod
    def _active_trace(session: MeasurementSession | None) -> SpectrumTrace | None:
        if session is None:
            return None
        return session.traces.get(session.active_trace_id or "") or next(iter(session.traces.values()), None)

    @staticmethod
    def _active_frequency_trace(session: MeasurementSession) -> SpectrumTrace | None:
        active = MainWindow._active_trace(session)
        if active is not None and active.is_frequency_trace:
            return active
        return next((trace for trace in session.traces.values() if trace.is_frequency_trace), None)

    def _frame_trace(
        self, session: MeasurementSession, waterfall: WaterfallData
    ) -> SpectrumTrace | None:
        """Return the live frequency trace that should receive exact-frame data.

        Max Hold / Average / Min Hold traces are accumulated results and must not
        be overwritten with a single frame.  Prefer a trace whose source stream
        matches the waterfall, otherwise fall back to the active non-hold trace.
        """
        if waterfall.source_stream:
            for trace in session.traces.values():
                if (
                    trace.is_frequency_trace
                    and trace.source_stream == waterfall.source_stream
                    and trace.trace_mode not in HOLD_TRACE_MODES
                ):
                    return trace
        active = self._active_frequency_trace(session)
        if active is not None and active.trace_mode not in HOLD_TRACE_MODES:
            return active
        return next(
            (
                trace
                for trace in session.traces.values()
                if trace.is_frequency_trace and trace.trace_mode not in HOLD_TRACE_MODES
            ),
            active,
        )

    @staticmethod
    def _active_waterfall(session: MeasurementSession | None) -> WaterfallData | None:
        if session is None:
            return None
        return session.waterfalls.get(session.active_waterfall_id or "") or next(iter(session.waterfalls.values()), None)

    def _active_spectrogram_index(
        self, session: MeasurementSession | None
    ) -> SpectrogramIndex | None:
        waterfall = self._active_waterfall(session)
        if session is None or waterfall is None:
            return None
        return self._spectrogram_indexes.get((session.session_id, waterfall.waterfall_id))

    def _set_band_from_trace(self, trace: SpectrumTrace | None) -> None:
        if trace is None or not trace.is_frequency_trace:
            return
        span = trace.stop_frequency_hz - trace.start_frequency_hz
        self.band_start.setValue((trace.start_frequency_hz + span * 0.4) / 1e6)
        self.band_stop.setValue((trace.start_frequency_hz + span * 0.6) / 1e6)
        self.acpr_offset.setValue(max(span * 0.25 / 1e6, 0.001))
        self.acpr_width.setValue(max(span * 0.15 / 1e6, 0.001))
        if hasattr(self, "cp_start"):
            self._set_cp_frequency_values(
                self.band_start.value(), self.band_stop.value(), activate_region=False
            )

    def _update_trace_properties(self, trace: SpectrumTrace) -> None:
        text = [
            f"Название: {trace.name}", f"Точек: {trace.point_count:,}",
            f"Ось: {trace.start_frequency_hz:g} … {trace.stop_frequency_hz:g} {trace.axis_unit}",
            f"Шаг: {trace.frequency_step_hz:g} Hz", f"Единица: {trace.unit}",
            f"Детектор: {trace.detector or 'нет данных'}", f"Режим: {trace.trace_mode}",
            f"RBW: {trace.rbw_hz if trace.rbw_hz is not None else 'нет данных'}",
            f"VBW: {trace.vbw_hz if trace.vbw_hz is not None else 'нет данных'}",
            f"Источник: {trace.source_stream}",
        ]
        self.properties_text.setPlainText("\n".join(text))

    def _update_metadata(self, session: MeasurementSession) -> None:
        metadata = session.metadata
        lines = [
            f"Файл: {session.source_path}", f"Прибор: {metadata.device_type}",
            f"Firmware: {metadata.firmware_version}", f"System: {metadata.system}",
            f"Каналы: {', '.join(metadata.channel_names)}", f"Режимы: {', '.join(metadata.modes)}",
            f"Потоков: {len(metadata.streams)}", f"Предупреждений: {len(metadata.warnings)}",
        ]
        if metadata.warnings:
            lines.extend(["", *metadata.warnings])
        self.metadata_text.setPlainText("\n".join(lines))

    def _update_status(self) -> None:
        session = self.active_session()
        trace = self._active_trace(session) if session else None
        if trace:
            span = abs(trace.stop_frequency_hz - trace.start_frequency_hz)
            rbw = f"{trace.rbw_hz:g} Hz" if trace.rbw_hz else "—"
            self.status_info.setText(f"Span {span / 1e6:.6g} MHz · RBW {rbw} · {trace.point_count:,} точек")
        else:
            self.status_info.clear()

    def _connect_plot_ranges(self) -> None:
        self.spectrum_renderer.plot.vb.sigXRangeChanged.connect(self._spectrum_range_changed)
        self.waterfall_renderer.plot.vb.sigXRangeChanged.connect(self._waterfall_range_changed)

    def _connect_navigation(self) -> None:
        if self._navigation_connected:
            return
        self._navigation_connected = True
        self.waterfall_renderer.view_box.frameWheel.connect(self._waterfall_wheel)
        self.waterfall_renderer.widget.scene().sigMouseClicked.connect(self._waterfall_clicked)
        self.spectrum_renderer.widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.spectrum_renderer.widget.customContextMenuRequested.connect(
            self._spectrum_context_menu
        )
        self.waterfall_renderer.widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.waterfall_renderer.widget.customContextMenuRequested.connect(
            self._waterfall_context_menu
        )

    def _spectrum_context_menu(self, _position: Any) -> None:
        session = self.active_session()
        if session is None:
            return
        menu = QMenu(self.spectrum_renderer.widget)
        reset_zoom = menu.addAction("Reset Zoom")
        menu.addSeparator()
        region = session.frequency_regions[0] if session.frequency_regions else None
        toggle_region = menu.addAction(
            "Показать полосу" if region is not None and not region.enabled else "Скрыть полосу"
        )
        toggle_region.setEnabled(region is not None)
        delete_region = menu.addAction("Удалить полосу")
        delete_region.setEnabled(region is not None)
        menu.addSeparator()
        clear_markers = menu.addAction("Удалить все маркеры")
        clear_markers.setEnabled(bool(session.markers))
        clear_all = menu.addAction("Очистить все инструменты анализа")
        chosen = self._exec_context_menu(menu)
        if chosen == reset_zoom:
            self._reset_zoom()
        elif chosen == toggle_region:
            self.toggle_frequency_region()
        elif chosen == delete_region:
            self.delete_frequency_region()
        elif chosen == clear_markers:
            self.clear_markers()
        elif chosen == clear_all:
            self.clear_all_analysis_tools()

    def _waterfall_context_menu(self, _position: Any) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if session is None:
            return
        menu = QMenu(self.waterfall_renderer.widget)
        region = session.frequency_regions[0] if session.frequency_regions else None
        toggle_region = menu.addAction(
            "Показать частотную полосу"
            if region is not None and not region.enabled else "Скрыть частотную полосу"
        )
        toggle_region.setEnabled(region is not None)
        delete_region = menu.addAction("Удалить частотную полосу")
        delete_region.setEnabled(region is not None)
        clear_time = menu.addAction("Удалить Time ROI")
        clear_time.setEnabled(bool(session.time_regions))
        key = (session.session_id, waterfall.waterfall_id) if waterfall is not None else None
        clear_noise = menu.addAction("Удалить Noise interval")
        clear_noise.setEnabled(bool(key is not None and key in self._manual_noise_ranges))
        menu.addSeparator()
        clear_channel = menu.addAction("Выключить и очистить Channel Power")
        clear_all = menu.addAction("Очистить все инструменты анализа")
        chosen = self._exec_context_menu(menu)
        if chosen == toggle_region:
            self.toggle_frequency_region()
        elif chosen == delete_region:
            self.delete_frequency_region()
        elif chosen == clear_time:
            session.time_regions.clear()
            self.waterfall_renderer.clear_time_region()
            self.cp_time_region.hide()
            self._mark_channel_power_dirty("временная область удалена")
            self._audit("user", "time_region_deleted")
        elif chosen == clear_noise and key is not None:
            self._manual_noise_ranges.pop(key, None)
            self.waterfall_renderer.clear_noise_region()
            self._mark_channel_power_dirty("шумовой интервал удалён")
            self._audit("user", "noise_region_deleted")
        elif chosen == clear_channel:
            self.clear_channel_power_tools()
        elif chosen == clear_all:
            self.clear_all_analysis_tools()

    def _waterfall_clicked(self, event: Any) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self.waterfall_renderer.plot.vb.mapSceneToView(event.scenePos())
        waterfall = self._active_waterfall(self.active_session())
        session = self.active_session()
        index = self._active_spectrogram_index(session)
        if waterfall is None or waterfall.values is None:
            return
        count = index.frame_count if index is not None else waterfall.line_count
        frame = int(np.clip(round(point.y()) - 1, 0, max(0, count - 1)))
        self._audit(
            "user", "waterfall_clicked", waterfall_y=point.y(), target_frame=frame,
            displayed_number=frame + 1,
        )
        self.time_slider.setValue(int(np.clip(frame, 0, self.time_slider.maximum())))

    def _show_view_settings(self) -> None:
        if self._view_settings_dialog is None:
            self._view_settings_dialog = ViewSettingsDialog(self)
        self._view_settings_dialog.refresh_from_view()
        self._view_settings_dialog.show()
        self._view_settings_dialog.raise_()
        self._view_settings_dialog.activateWindow()
        self._audit("user", "view_settings_opened")

    def _show_frame_navigation_settings(self) -> None:
        if self._frame_navigation_settings_dialog is None:
            self._frame_navigation_settings_dialog = FrameNavigationSettingsDialog(self)
        self._frame_navigation_settings_dialog.refresh_from_window()
        self._frame_navigation_settings_dialog.show()
        self._frame_navigation_settings_dialog.raise_()
        self._frame_navigation_settings_dialog.activateWindow()
        self._audit("user", "frame_navigation_settings_opened")

    def _spectrum_range_changed(self, viewbox: Any, ranges: tuple[float, float]) -> None:
        trace = self._active_trace(self.active_session())
        if self._syncing_range or trace is None or not trace.is_frequency_trace:
            return
        self._syncing_range = True
        self._audit(
            "user",
            "spectrum_frequency_range_changed",
            start_hz=float(ranges[0]),
            stop_hz=float(ranges[1]),
        )
        try:
            self.waterfall_renderer.plot.setXRange(ranges[0], ranges[1], padding=0)
        finally:
            self._syncing_range = False

    def _waterfall_range_changed(self, viewbox: Any, ranges: tuple[float, float]) -> None:
        trace = self._active_trace(self.active_session())
        if self._syncing_range or trace is None or not trace.is_frequency_trace:
            return
        self._syncing_range = True
        self._audit(
            "user",
            "waterfall_frequency_range_changed",
            start_hz=float(ranges[0]),
            stop_hz=float(ranges[1]),
        )
        try:
            self.spectrum_renderer.plot.setXRange(ranges[0], ranges[1], padding=0)
        finally:
            self._syncing_range = False

    def _auto_scale(self) -> None:
        self._audit("user", "auto_scale_requested")
        self._reset_zoom()
        self.waterfall_renderer.plot.enableAutoRange()

    def _reset_zoom(self) -> None:
        self._audit("user", "reset_zoom_requested")
        session = self.active_session()
        active = self._active_trace(session) if session else None
        if active is None:
            self.spectrum_renderer.plot.enableAutoRange()
            return
        traces = [
            trace for candidate in self.repository.all()
            for trace in candidate.traces.values()
            if trace.enabled and trace.axis_unit == active.axis_unit and trace.point_count
        ]
        if not traces:
            self.spectrum_renderer.plot.enableAutoRange()
            return
        x_values = np.concatenate([trace.x_values for trace in traces])
        displayed_y: list[np.ndarray] = []
        for trace in traces:
            item = self.spectrum_renderer.items.get(trace.trace_id)
            # PlotDataItem.getData() is view-dependent: after an X zoom it may
            # return only the clipped/downsampled visible subset.  Reset must
            # use the complete currently displayed trace, including an exact
            # spectrogram frame that replaced the original static trace.
            y = item.yData if item is not None else None
            displayed_y.append(
                np.asarray(y, dtype=np.float64) if y is not None else trace.power_values
            )
        y_values = np.concatenate(displayed_y)
        finite_x = x_values[np.isfinite(x_values)]
        finite_y = y_values[np.isfinite(y_values)]
        if finite_x.size:
            self.spectrum_renderer.plot.setXRange(
                float(np.min(finite_x)), float(np.max(finite_x)), padding=0
            )
        if finite_y.size:
            low, high = float(np.min(finite_y)), float(np.max(finite_y))
            if low == high:
                low, high = low - 1.0, high + 1.0
            self.spectrum_renderer.plot.setYRange(low, high, padding=0.05)

    def _toggle_grid(self, enabled: bool) -> None:
        self.spectrum_renderer.plot.showGrid(x=enabled, y=enabled, alpha=0.22)
        self.waterfall_renderer.plot.showGrid(x=enabled, y=enabled, alpha=0.16)
        self._audit("user", "grid_visibility_changed", enabled=enabled)

    def _apply_smoothing_settings(self) -> None:
        if self.spectrum_smooth_enable.isChecked():
            spectrum_method = SpectrumSmoothMethod(
                self.spectrum_smooth_combo.currentData() or SpectrumSmoothMethod.PCHIP
            )
        else:
            spectrum_method = SpectrumSmoothMethod.NONE
        spectrum_settings = SpectrumSmoothSettings(
            method=spectrum_method,
            auto_zoom=self.spectrum_smooth_auto.isChecked(),
        )
        waterfall_method = WaterfallSmoothMethod(
            self.waterfall_smooth_combo.currentData() or WaterfallSmoothMethod.NEAREST
        )
        waterfall_settings = WaterfallSmoothSettings(
            method=waterfall_method,
            auto_zoom=self.waterfall_smooth_auto.isChecked(),
        )
        self.spectrum_renderer.set_smoothing(spectrum_settings)
        self.waterfall_renderer.set_smoothing(waterfall_settings)
        self._audit(
            "user",
            "smoothing_settings_changed",
            spectrum_method=spectrum_method.value,
            waterfall_method=waterfall_method.value,
            auto_zoom_spectrum=spectrum_settings.auto_zoom,
            auto_zoom_waterfall=waterfall_settings.auto_zoom,
        )

    def _set_colormap(self, name: str) -> None:
        self.waterfall_renderer.set_colormap(name)
        self._audit("user", "waterfall_colormap_changed", colormap=name)

    def _set_levels(self) -> None:
        if self.level_min.value() < self.level_max.value():
            self.waterfall_renderer.set_levels(self.level_min.value(), self.level_max.value())
            self._audit(
                "user",
                "waterfall_levels_changed",
                level_min=self.level_min.value(),
                level_max=self.level_max.value(),
            )

    def _auto_levels(self) -> None:
        waterfall = self._active_waterfall(self.active_session())
        if waterfall is None or waterfall.values is None:
            return
        finite = waterfall.values[np.isfinite(waterfall.values)]
        if not finite.size:
            return
        low, high = np.percentile(finite, (2.0, 99.5))
        self.level_min.setValue(float(low))
        self.level_max.setValue(float(high))
        self._audit("user", "waterfall_auto_levels", level_min=float(low), level_max=float(high))

    def _apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        stylesheet = "" if theme == "Светлая" else DARK_STYLE
        # Re-polishing every widget in the application is expensive; apply
        # the stylesheet only when it actually changes (every MainWindow
        # restore used to pay this cost unconditionally).
        if app.styleSheet() != stylesheet:
            app.setStyleSheet(stylesheet)
        self._audit("user", "theme_changed", theme=theme)

    def _install_logging(self) -> None:
        self.log_emitter = _LogEmitter(self)
        self.log_emitter.message.connect(self.log_text.appendPlainText)
        self.log_handler = QtLogHandler(self.log_emitter)
        LOGGER.setLevel(logging.INFO)
        LOGGER.addHandler(self.log_handler)

    def _restore_settings(self) -> None:
        self.theme_combo.setCurrentText(self.settings.value("theme", "Тёмная"))
        geometry = self.settings.value("geometry")
        state = self.settings.value("windowState")
        splitter = self.settings.value("splitter")
        if geometry:
            self.restoreGeometry(geometry)
        state_version = self._settings_int(self.settings.value("windowStateVersion"), 1)
        if state and state_version == WINDOW_STATE_VERSION:
            self.restoreState(state)
        elif state:
            self._audit(
                "program",
                "window_state_ignored",
                saved_version=state_version,
                current_version=WINDOW_STATE_VERSION,
            )
        if splitter:
            self.central_splitter.restoreState(splitter)
        self._clamp_window_to_screen()
        self._apply_theme(self.theme_combo.currentText())
        self._restore_frame_navigation_settings()
        self._restore_heatmap_settings()
        self._audit(
            "program",
            "ui_settings_restored",
            geometry_restored=bool(geometry),
            window_state_restored=bool(state),
            splitter_restored=bool(splitter),
            theme=self.theme_combo.currentText(),
        )

    def _restore_frame_navigation_settings(self) -> None:
        cfg = self._frame_nav.config
        sequential = bool(self.settings.value("frame_navigation/sequential_mode", False))
        cfg.sequential_mode = sequential
        self.no_skip_check.setChecked(sequential)
        cfg.wheel_step = int(self.settings.value("frame_navigation/wheel_step", cfg.wheel_step))
        cfg.touchpad_threshold = float(
            self.settings.value("frame_navigation/touchpad_threshold", cfg.touchpad_threshold)
        )
        cfg.fps = int(self.settings.value("frame_navigation/fps", cfg.fps))
        cfg.settle_delay_ms = int(
            self.settings.value("frame_navigation/settle_delay_ms", cfg.settle_delay_ms)
        )
        self._frame_scheduler.set_fps(cfg.fps)
        self._frame_scheduler.set_settle_delay_ms(cfg.settle_delay_ms)
        self.fps_combo.setCurrentText(str(cfg.fps))
        self.wheel_step_spin.setValue(cfg.wheel_step)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # The dock layout only computes its true minimum size after the first
        # show; re-run the clamp once so a too-tall restored layout is pulled
        # back into the work area before the user sees it.
        if not self._shown_once:
            self._shown_once = True
            QTimer.singleShot(0, self._clamp_window_to_screen)

    def _clamp_window_to_screen(self) -> None:
        """Keep the window fully inside the available area of its screen.

        Applies to the initial fixed size and to restored geometry alike:
        a window saved on another monitor/DPI must never come back partly
        off-screen or larger than the current work area.
        """
        if self.isMaximized() or self.isFullScreen():
            return
        # Iterate: the frame/client margins are re-measured after every pass,
        # because they can shift once the geometry changes (platform-specific).
        clamped = False
        for _ in range(3):
            frame = self.frameGeometry()
            inner = self.geometry()
            # setGeometry() addresses the client area, so convert between the
            # outer frame rect (what must fit the screen) and the client rect.
            extra_w = frame.width() - inner.width()
            extra_h = frame.height() - inner.height()
            off_x = inner.x() - frame.x()
            off_y = inner.y() - frame.y()
            screen = QApplication.screenAt(frame.topLeft()) or QApplication.primaryScreen()
            if screen is None:
                return
            available = screen.availableGeometry()
            width = min(frame.width(), available.width())
            height = min(frame.height(), available.height())
            max_x = available.x() + available.width() - width
            max_y = available.y() + available.height() - height
            x = min(max(frame.x(), available.x()), max_x)
            y = min(max(frame.y(), available.y()), max_y)
            if (x, y, width, height) == (frame.x(), frame.y(), frame.width(), frame.height()):
                break
            self.setGeometry(
                x + off_x, y + off_y, max(1, width - extra_w), max(1, height - extra_h)
            )
            clamped = True
        if clamped:
            self._audit(
                "program",
                "window_geometry_clamped",
                screen=screen.name(),
                available=f"{available.width()}x{available.height()}",
            )

    def _show_error(self, title: str, message: str) -> None:
        self._audit("program", "error_dialog_shown", level=logging.ERROR, title=title, message=message)
        QMessageBox.critical(self, title, message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._audit(
            "user",
            "application_close",
            open_sessions=len(self.repository.all()),
            active_workers=len(self._workers),
        )
        self.playback_timer.stop()
        self._live_timer.stop()
        for controller in self._live_controllers.values():
            controller.close(wait=False)
        self._live_controllers.clear()
        self._live_adapters.clear()
        self._frame_loader.close()
        # §5.10: controller shutdown first (phase, pending, cancel, late-callback
        # guard); the common worker cancel below stays as a safety net.
        self._heatmap_controller.shutdown()
        # pyqtgraph creates the channel-power ViewBox menu as an orphan
        # top-level widget tree; without an explicit delete it leaks one
        # menu tree per MainWindow lifetime.
        channel_menu = self.channel_power_plot.plotItem.vb.menu
        if channel_menu is not None:
            channel_menu.deleteLater()
        for worker in list(self._workers):
            worker.cancel()
        self._audit("program", "heatmap_shutdown", **self.heatmap_diagnostics())
        for reader in self._frame_readers.values():
            reader.close()
        self._frame_readers.clear()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("windowStateVersion", WINDOW_STATE_VERSION)
        self.settings.setValue("splitter", self.central_splitter.saveState())
        self.settings.setValue("theme", self.theme_combo.currentText())
        cfg = self._frame_nav.config
        self.settings.setValue("frame_navigation/sequential_mode", cfg.sequential_mode)
        self.settings.setValue("frame_navigation/wheel_step", cfg.wheel_step)
        self.settings.setValue("frame_navigation/touchpad_threshold", cfg.touchpad_threshold)
        self.settings.setValue("frame_navigation/fps", cfg.fps)
        self.settings.setValue("frame_navigation/settle_delay_ms", cfg.settle_delay_ms)
        LOGGER.removeHandler(self.log_handler)
        super().closeEvent(event)


DARK_STYLE = """
QWidget { background: #171c24; color: #e6edf3; font-size: 10pt; }
QMainWindow::separator { background: #30363d; width: 5px; height: 5px; }
QMenuBar, QMenu, QToolBar, QStatusBar { background: #111820; }
QDockWidget::title { background: #202832; padding: 6px; font-weight: 600; }
QTreeWidget, QTableWidget, QTextEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox, QSpinBox {
    background: #0d1117; border: 1px solid #30363d; selection-background-color: #1f6feb;
}
QHeaderView::section { background: #202832; color: #e6edf3; padding: 5px; border: 0; }
QPushButton { background: #263444; border: 1px solid #425466; border-radius: 3px; padding: 5px 9px; }
QPushButton:hover { background: #31506f; }
QPushButton:pressed { background: #1f6feb; }
QProgressBar { border: 1px solid #425466; text-align: center; }
QProgressBar::chunk { background: #1f6feb; }
"""


def run_gui() -> None:
    file_handler = install_activity_file_logging(LOGGER)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("R&S DFL parcer")
    app.setOrganizationName("RohdeSchwarzTools")
    app.setStyle("Fusion")
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    log_event(
        LOGGER,
        "program",
        "activity_log_ready",
        path=str(file_handler.path),
        max_records=file_handler.max_records,
    )
    window = MainWindow()
    window.show()
    try:
        app.exec()
    finally:
        log_event(LOGGER, "program", "application_stopped")
        file_handler.flush()
