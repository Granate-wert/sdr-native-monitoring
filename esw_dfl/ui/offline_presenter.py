"""Qt-signal-driven presenter for the Offline DFL workspace.

``OfflineDflPresenter`` owns the DFL workflow state that used to live inside
the legacy ``MainWindow``: session loading, waterfall preview/index/reader
management, frame navigation, markers, analysis, playback, heatmap
persistence and exports.  The workspace widget is a thin GUI layer that
renders immutable :mod:`esw_dfl.ui.offline_state` snapshots and forwards
user intent back to this presenter.

The presenter is deliberately not a widget: tests can drive it without a
window and observe its signals.  It still owns the pyqtgraph renderers
(spectrum/waterfall) because the legacy renderers are reparented into the
shell host, never recreated.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal

from ..activity_log import log_event
from ..adapter import DflMeasurementAdapter
from ..animation import export_waterfall_animation
from ..domain import (
    AnalysisResult,
    FrequencyRegion,
    Marker,
    MarkerType,
    MeasurementSession,
    SpectrumTrace,
    WaterfallData,
)
from ..domain_export import (
    export_markers_csv,
    export_results_csv,
    export_session_json,
    export_session_npz,
    export_time_gated_events_csv,
    export_time_gated_frames_csv,
    export_time_gated_json,
    export_time_gated_summary_csv,
    export_trace_csv,
    export_traces_csv,
    export_waterfall_region_csv,
)
from ..frame_navigation import (
    FrameLoadCoordinator,
    FrameNavigationController,
    FramePresentationScheduler,
    FrameSnapshot,
    FrameSpanEvent,
    NavigationConfig,
    NavigationReason,
    ScrollState,
)
from ..heatmap import (
    HeatmapConfig,
    HeatmapNormalization,
    HeatmapRangeMode,
    HeatmapResult,
    HeatmapSamplingPolicy,
    frequency_bin_edges,
    frequency_grid_hash,
)
from ..heatmap_export import (
    export_heatmap_csv,
    export_heatmap_json,
    export_heatmap_npz,
    export_heatmap_png,
)
from ..heatmap_persistence import (
    ColorScaleMode,
    HeatmapDisplayConfig,
    PersistenceConfig,
    PersistenceMode,
    PersistencePhase,
    PersistenceSnapshot,
    PersistenceSourceKey,
    WindowUnit,
)
from ..heatmap_persistence_controller import (
    HeatmapPersistenceController,
    HeatmapPhaseEvent,
    PersistenceSourceContext,
)
from ..models import MeasurementQuality, MeasurementWarning, SpectrogramInfo, SpectrogramPreview
from ..parser import DflParser
from ..processing import (
    acpr,
    channel_power,
    noise_floor,
    occupied_bandwidth,
    peak_search_values,
    snr,
)
from ..renderers import PyQtGraphSpectrumRenderer, PyQtGraphWaterfallRenderer
from ..repository import MemoryMeasurementRepository
from ..spectrogram import (
    SpectrogramFrameReader,
    SpectrogramIndex,
    compute_frame_period_statistics,
    iter_spectrogram_rows,
    load_spectrogram_preview_with_index,
    read_spectrogram_frame,
)
from ..time_gated_power import (
    ActivityDetectionConfig,
    ChannelPowerMode,
    ChannelPowerRequest,
    FrameInclusion,
    ManualOverride,
    PowerSemantics,
    TimeGatedChannelPowerResult,
    TimeGatedChannelPowerService,
)
from ..workers import TaskWorker
from ..workspace import apply_workspace_session, read_workspace, write_workspace
from .offline_state import (
    OfflineHeatmapSnapshot,
    OfflineMarkerSnapshot,
    OfflinePlaybackSnapshot,
    OfflineResultSnapshot,
    OfflineSessionSnapshot,
    OfflineStatusSnapshot,
    OfflineTraceSnapshot,
    OfflineWaterfallSnapshot,
    OfflineWorkspaceSnapshot,
)

LOGGER = logging.getLogger("esw_dfl")

HOLD_TRACE_MODES = frozenset({"Max Hold", "Average", "Min Hold"})

def _analyze_time_gated_waterfall(
    service: TimeGatedChannelPowerService,
    source_path: Path,
    info: SpectrogramInfo,
    frequencies_hz: np.ndarray,
    request: ChannelPowerRequest,
    manual_override: np.ndarray,
    index: SpectrogramIndex | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancel: threading.Event | None = None,
) -> TimeGatedChannelPowerResult:
    """Run one bounded time-gated analysis without touching Qt state."""

    if (
        request.mode is ChannelPowerMode.CURRENT_FRAME
        and request.selected_frame_index is not None
        and index is not None
    ):
        row = read_spectrogram_frame(source_path, index, request.selected_frame_index)
        series = service.channel_power.build_series((row,), frequencies_hz, request, cancel)
        series.frame_indices[:] = request.selected_frame_index
        if request.selected_frame_index < index.timestamps.size:
            series.timestamps_s[:] = index.timestamps[request.selected_frame_index]
        selected_override = (
            manual_override[request.selected_frame_index:request.selected_frame_index + 1]
            if manual_override.size > request.selected_frame_index else None
        )
        activity = service.activity_detection.detect(
            series, request.activity_config or ActivityDetectionConfig(), selected_override
        )
        return service.burst_analysis.summarize(request, series, activity)

    rows = None
    if service.cache.get(request) is None:
        rows = iter_spectrogram_rows(source_path, info, progress=progress, cancel=cancel)
    return service.analyze(request, frequencies_hz, rows, manual_override, cancel)

HEATMAP_LIVE_MODES = frozenset(
    {PersistenceMode.ROLLING_EXACT, PersistenceMode.EXPONENTIAL_DECAY}
)

_MARKER_TYPE_LABELS = {
    MarkerType.MANUAL: "Manual",
    MarkerType.PEAK: "Peak",
    MarkerType.DELTA: "Delta",
    MarkerType.MINIMUM: "Minimum",
    MarkerType.BAND_CENTER: "Band center",
    MarkerType.NOISE: "Noise",
    MarkerType.HARMONIC: "Harmonic",
    MarkerType.REFERENCE: "Reference",
}


class OfflineDflPresenter(QObject):
    """Owns the DFL analysis workflow; the workspace renders snapshots."""

    snapshot_ready = Signal(object)  # OfflineWorkspaceSnapshot
    frame_ready = Signal(object)  # FrameSnapshot
    heatmap_phase = Signal(object)  # HeatmapPhaseEvent
    error = Signal(str, str)  # title, message
    busy = Signal(bool, str)
    session_activated = Signal(str)  # session_id
    session_closed = Signal(str)  # session_id
    activity_event = Signal(str)  # bounded presentation event
    time_gated_ready = Signal(object)  # TimeGatedChannelPowerResult

    def __init__(
        self,
        *,
        repository: MemoryMeasurementRepository | None = None,
        adapter: DflMeasurementAdapter | None = None,
        thread_pool: Any | None = None,
        heatmap_controller: HeatmapPersistenceController | None = None,
        time_gated_service: TimeGatedChannelPowerService | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        from PySide6.QtCore import QThreadPool

        self.repository = repository or MemoryMeasurementRepository()
        self.adapter = adapter or DflMeasurementAdapter()
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self.active_session_id: str | None = None
        self._workers: set[TaskWorker] = set()
        self._workspace_payloads: dict[str, dict[str, Any]] = {}
        self._current_workspace: Path | None = None
        self._spectrogram_indexes: dict[tuple[str, str], SpectrogramIndex] = {}
        self._frame_readers: dict[tuple[str, str], SpectrogramFrameReader] = {}
        self._generation = 0
        self._activity_lines: deque[str] = deque(maxlen=500)

        self.spectrum_renderer = PyQtGraphSpectrumRenderer()
        self.waterfall_renderer = PyQtGraphWaterfallRenderer()

        self.playback_timer = QTimer(self)
        self.playback_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.playback_timer.timeout.connect(self._advance_frame)
        self._playback_start_frame = 0
        self._playback_start_time: float | None = None
        self._playback_speed = "1×"
        self._playback_fps = 60
        self._playback_loop = False
        self._playback_no_skip = False

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
        self._navigation_connected = False

        self.time_gated_service = time_gated_service or TimeGatedChannelPowerService()
        self._channel_power_results: dict[tuple[str, str], TimeGatedChannelPowerResult] = {}
        self._channel_power_worker: TaskWorker | None = None
        self._channel_power_session_id: str | None = None
        self._channel_power_waterfall_id: str | None = None
        self._channel_power_generation = 0
        self._pending_channel_power_request: tuple[
            MeasurementSession, WaterfallData, SpectrogramIndex,
            Any, np.ndarray, int
        ] | None = None

        self._heatmap_controller = heatmap_controller or HeatmapPersistenceController(
            thread_pool=self.thread_pool,
            audit=lambda event, **details: self._audit("heatmap", event, **details),
            parent=self,
        )
        self._heatmap_applied_snapshot: PersistenceSnapshot | None = None
        self._heatmap_applied: HeatmapResult | None = None
        self._heatmap_applied_key: tuple[str, str, str] | None = None
        self._heatmap_applied_range: tuple[int, int] | None = None
        self._heatmap_last_context_identity: tuple[str, str] | None = None
        self._heatmap_current_levels: tuple[float, float] | None = None
        self._heatmap_navigation_connected = False
        self._heatmap_window_minimum_frames = 1
        self._heatmap_window_minimum_seconds: float | None = None
        self._heatmap_render_budget_signature: tuple[object, ...] | None = None
        self._heatmap_render_intervals: deque[float] = deque(maxlen=200)
        self._heatmap_last_apply_at = 0.0
        self._heatmap_enabled = False
        self._heatmap_status = ""
        self._heatmap_status_error = False
        self._heatmap_controller.snapshot_ready.connect(self._apply_persistence_snapshot)
        self._heatmap_controller.phase_changed.connect(self._apply_heatmap_phase)
        self._heatmap_controller.failed.connect(self._heatmap_controller_failed)

        self._connect_navigation()
        self._connect_heatmap_navigation()

    # ------------------------------------------------------------------
    # Session loading
    # ------------------------------------------------------------------
    def open_files(self, paths: list[Path] | list[str]) -> None:
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
            self.error.emit("Файл не найден", f"Исходный DFL недоступен:\n{path}")
            return
        self._audit("user", "dfl_open_requested", path=str(path))
        LOGGER.info("Открытие DFL: %s", path)
        self.busy.emit(True, "Чтение DFL…")

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
        self.set_active_session(session.session_id)
        LOGGER.info(
            "DFL прочитан: %s; трасс %d; waterfall %d",
            session.source_path.name, len(session.traces), len(session.waterfalls),
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
        self._bump()

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
        self.busy.emit(True, "Потоковое чтение waterfall…")
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
        values = waterfall.values
        if values is None:
            LOGGER.warning("Waterfall preview has no values for %s", session_id)
            return
        LOGGER.info(
            "Waterfall preview: %d × %d, %.1f MiB",
            *values.shape, values.nbytes / 2**20,
        )
        self._audit(
            "program",
            "waterfall_preview_completed",
            waterfall_id=waterfall.waterfall_id,
            preview_rows=int(values.shape[0]),
            points=int(values.shape[1]),
            full_frames=index.frame_count,
            memory_bytes=int(values.nbytes),
        )
        if session_id == self.active_session_id:
            self.waterfall_renderer.set_data(waterfall)
            frame_count = index.frame_count or values.shape[0]
            self._frame_scheduler.set_active_context(session.session_id, waterfall.waterfall_id)
            self._frame_nav.set_frame_count(frame_count)
            self._frame_nav.seek(min(session.current_frame, max(0, frame_count - 1)), NavigationReason.API)
            self.show_frame(self._frame_nav.requested_frame)
            self._heatmap_index_ready()
        self._bump()

    # ------------------------------------------------------------------
    # Session switching
    # ------------------------------------------------------------------
    def set_active_session(self, session_id: str) -> None:
        if self.active_session_id != session_id and self._channel_power_worker is not None:
            self.cancel_time_gated_power()
        session = self.repository.get(session_id)
        if session is not None and not session.visible:
            replacement = self._first_visible_session_id()
            if replacement is None:
                self._clear_ui()
                self.active_session_id = None
                self._bump()
                return
            session_id = replacement
        self.active_session_id = session_id
        session = self.repository.get(session_id)
        if session is None:
            self._clear_ui()
            self._bump()
            return
        self._audit(
            "user",
            "session_activated",
            session_id=session.session_id,
            source_path=str(session.source_path),
        )
        self._heatmap_context_changed()
        source_type = (
            session.source_descriptor.source_type.value
            if session.source_descriptor is not None else ""
        )
        if source_type != "live_iq":
            self.spectrum_renderer.clear_heatmap()
        self._render_sessions()
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
            self._frame_scheduler.set_active_context(session.session_id, waterfall.waterfall_id)
            self._frame_nav.set_frame_count(frame_count)
            self._frame_nav.seek(min(session.current_frame, max(0, frame_count - 1)), NavigationReason.API)
            self._frame_scheduler.schedule(immediate=True)
        else:
            self.waterfall_renderer.clear()
            self._frame_nav.set_frame_count(0)
            self._frame_nav.seek(0, NavigationReason.API)
        self.session_activated.emit(session_id)
        self._bump()

    def close_active_session(self) -> None:
        if self.active_session_id is None:
            return
        if self._channel_power_session_id == self.active_session_id:
            self.cancel_time_gated_power()
        if self.active_session_id is None:
            return
        closing_id = self.active_session_id
        closing = self.repository.get(closing_id)
        self._audit(
            "user",
            "session_closed",
            closed_session_id=closing_id,
            closed_source_path=str(closing.source_path) if closing is not None else None,
        )
        self.repository.remove(closing_id)
        for key in [key for key in self._frame_readers if key[0] == closing_id]:
            self._frame_readers.pop(key).close()
            self._spectrogram_indexes.pop(key, None)
        self._heatmap_on_session_removed(closing_id)
        sessions = self.repository.all()
        self.active_session_id = sessions[-1].session_id if sessions else None
        if self.active_session_id:
            self.set_active_session(self.active_session_id)
        else:
            self._clear_ui()
        self.session_closed.emit(closing_id)
        self._bump()

    def remove_session(self, session_id: str) -> None:
        session = self.repository.get(session_id)
        if session is None:
            return
        was_active = self.active_session_id == session_id
        for key in [k for k in self._spectrogram_indexes if k[0] == session_id]:
            del self._spectrogram_indexes[key]
        for key in [k for k in self._frame_readers if k[0] == session_id]:
            self._frame_readers.pop(key).close()
        loader = self._frame_loader
        active_request = getattr(loader, "_active", None)
        pending_request = getattr(loader, "_pending", None)
        if active_request is not None and active_request.session_id == session_id:
            loader.cancel_all()
        elif pending_request is not None and pending_request.session_id == session_id:
            loader._pending = None
            loader._diagnostics["pending_loads"] = 0
            loader.diagnostics.emit(loader._diagnostics.copy())
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
        self._heatmap_on_session_removed(session_id)
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
                self.set_active_session(self.active_session_id)
            else:
                self._clear_ui()
        self._bump()

    def toggle_session_visibility(self, session_id: str) -> None:
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
        self._render_sessions()
        self._bump()

    def select_tree_item(self, session_id: str, kind: str, object_id: str | None) -> None:
        session = self.repository.get(session_id)
        if session is None or not session.visible:
            return
        if kind == "trace" and object_id in session.traces:
            session.active_trace_id = object_id
            self._audit("user", "trace_selected", selected_session_id=session_id, trace_id=object_id)
            self.set_active_session(session_id)
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
        self._bump()

    def set_trace_enabled(self, session_id: str, trace_id: str, enabled: bool) -> None:
        session = self.repository.get(session_id)
        if session is None:
            return
        trace = session.traces.get(trace_id)
        if trace is None:
            return
        trace.enabled = enabled
        self._audit(
            "user",
            "trace_visibility_changed",
            trace_id=trace.trace_id,
            enabled=enabled,
        )
        self._render_sessions()
        self._bump()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _first_visible_session_id(self) -> str | None:
        for session in self.repository.all():
            if session.visible:
                return session.session_id
        return None

    def _clear_ui(self) -> None:
        self.spectrum_renderer.clear()
        self.waterfall_renderer.clear()
        self._heatmap_controller.set_context(None)
        self._heatmap_controller.clear()
        self._heatmap_reset_overlay()
        self._heatmap_last_context_identity = None

    def _render_sessions(self) -> None:
        self.spectrum_renderer.clear()
        active_session = self.active_session()
        active_trace = self._active_trace(active_session)
        axis_unit = active_trace.axis_unit if active_trace is not None else "Hz"
        y_unit = active_trace.unit if active_trace is not None else "dBm"
        self.spectrum_renderer.set_axis_units(axis_unit, y_unit)
        for candidate in self.repository.all():
            if not candidate.visible:
                continue
            for trace in candidate.traces.values():
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
    # ------------------------------------------------------------------
    # Frame navigation
    # ------------------------------------------------------------------
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
        return (session.source_path, self._spectrogram_indexes.get(key), self._frame_readers.get(key))

    def show_frame(self, frame: int) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if session is None or waterfall is None or waterfall.values is None or not waterfall.values.shape[0]:
            return
        index = self._active_spectrogram_index(session)
        frame_count = index.frame_count if index is not None else waterfall.values.shape[0]
        frame = int(np.clip(frame, 0, max(0, frame_count - 1)))
        self._frame_nav.seek(frame, NavigationReason.FRAME_INPUT)
        self._frame_scheduler.schedule(immediate=True)

    def first_frame(self) -> None:
        self._audit("user", "first_frame_requested")
        self.show_frame(0)

    def last_frame(self) -> None:
        self._audit("user", "last_frame_requested")
        self.show_frame(max(0, self._frame_nav.frame_count - 1))

    def previous_frame(self) -> None:
        self._audit("user", "previous_frame_requested", current_frame=self._frame_nav.requested_frame)
        self.show_frame(max(0, self._frame_nav.requested_frame - 1))

    def next_frame(self) -> None:
        self._audit("user", "next_frame_requested", current_frame=self._frame_nav.requested_frame)
        self.show_frame(min(max(0, self._frame_nav.frame_count - 1), self._frame_nav.requested_frame + 1))

    def handle_waterfall_wheel(self, angle_delta: int, pixel_delta: Any, modifiers: Any) -> None:
        self._audit(
            "user",
            "waterfall_wheel_step_queued",
            angle_delta=angle_delta,
            requested_frame=self._frame_nav.requested_frame,
        )
        self._frame_nav.handle_wheel(angle_delta, pixel_delta, modifiers)
        self._frame_scheduler.schedule(immediate=True)

    def set_wheel_step(self, value: int) -> None:
        self._frame_nav.config.wheel_step = value

    def set_sequential_mode(self, enabled: bool) -> None:
        self._frame_nav.config.sequential_mode = enabled
        self._frame_scheduler.set_sequential_mode(enabled)

    def set_settle_delay_ms(self, value: int) -> None:
        self._frame_nav.config.settle_delay_ms = value
        self._frame_scheduler.set_settle_delay_ms(value)

    def set_frame_fps(self, fps: int) -> None:
        self._playback_fps = max(1, fps)
        self._frame_scheduler.set_fps(self._playback_fps)

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
        waterfall_y = self._frame_to_preview_row(waterfall, index, frame)
        center = (waterfall.start_frequency_hz + waterfall.stop_frequency_hz) / 2.0
        self.waterfall_renderer.set_current_frame_row(
            row.values[: waterfall.point_count], waterfall_y
        )
        self.waterfall_renderer.set_cursor(center, waterfall_y, f"Кадр {frame + 1:,}")
        self.frame_ready.emit(snapshot)
        self._bump()

    def _on_frame_settled(self) -> None:
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

    @staticmethod
    def _frame_to_preview_row(
        waterfall: WaterfallData,
        index: SpectrogramIndex | None,
        frame: int,
    ) -> float:
        """Map a source frame to the sampled preview matrix row.

        Mirrors the legacy ``_frame_to_preview_index``: prefer timestamp
        interpolation over the compact preview, fall back to proportional
        scaling of the logical frame count.
        """
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

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def play(self) -> None:
        if self._frame_nav.frame_count <= 0:
            return
        self._update_playback_interval()
        self._playback_start_frame = self._frame_nav.requested_frame
        self._playback_start_time = time.perf_counter()
        self._frame_scheduler.set_playback_active(True)
        self.playback_timer.start()
        self._audit(
            "user",
            "playback_started",
            frame=self._frame_nav.requested_frame,
            speed=self._playback_speed,
            fps=self._playback_fps,
            loop=self._playback_loop,
        )
        self._bump()

    def pause(self) -> None:
        self.playback_timer.stop()
        self._frame_scheduler.cancel_and_invalidate()
        self._heatmap_controller.pause()
        self._audit("user", "playback_paused", frame=self._frame_nav.requested_frame)
        self._bump()

    def stop(self) -> None:
        self.playback_timer.stop()
        self._frame_scheduler.cancel_and_invalidate()
        self._audit("user", "playback_stopped", frame=self._frame_nav.requested_frame)
        self.first_frame()
        self._heatmap_controller.stop(target=0)
        self._bump()

    def toggle_play(self) -> None:
        self.pause() if self.playback_timer.isActive() else self.play()

    def set_playback_speed(self, text: str) -> None:
        self._playback_speed = text

    def set_playback_loop(self, enabled: bool) -> None:
        self._playback_loop = enabled

    def set_playback_no_skip(self, enabled: bool) -> None:
        self._playback_no_skip = enabled
        self._frame_scheduler.set_sequential_mode(enabled)

    def _update_playback_interval(self) -> None:
        fps = max(1, self._playback_fps)
        speed = float(self._playback_speed.replace("×", ""))
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
            if self._playback_no_skip:
                interval_ms = frame_interval_ms
            else:
                interval_ms = max(interval_ms, frame_interval_ms)
        self.playback_timer.setInterval(
            max(1, min(round(interval_ms), 2_147_483_647))
        )
        self._frame_scheduler.set_sequential_mode(self._playback_no_skip)
        self._audit(
            "user",
            "playback_speed_changed",
            speed=self._playback_speed,
            fps=fps,
            timer_interval_ms=self.playback_timer.interval(),
            no_skip=self._playback_no_skip,
        )

    def _advance_frame(self) -> None:
        session = self.active_session()
        current = self._frame_nav.requested_frame
        speed = float(self._playback_speed.replace("×", ""))
        frame_count = self._frame_nav.frame_count
        if frame_count == 0:
            return
        max_frame = frame_count - 1
        target: int
        if current == max_frame and self._playback_loop:
            target = 0
            self._playback_start_frame = 0
            self._playback_start_time = time.perf_counter()
            self._frame_scheduler.reset_playback_progress()
        elif self._playback_no_skip:
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
            if self._playback_loop:
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

    # ------------------------------------------------------------------
    # Markers
    # ------------------------------------------------------------------
    def add_marker(self) -> Marker | None:
        session = self.active_session()
        trace = self._active_trace(session) if session else None
        if session is None or trace is None or not trace.is_frequency_trace:
            return None
        if len(session.markers) >= 10:
            self.error.emit("Маркеры", "Поддерживается не более 10 маркеров")
            return None
        x_range = self.spectrum_renderer.plot.viewRange()[0]
        frequency = float(np.mean(x_range)) if trace.is_frequency_trace else float(trace.x_values[trace.point_count // 2])
        marker = Marker(name=f"M{len(session.markers) + 1}", frequency_hz=frequency, trace_id=trace.trace_id)
        self._update_marker_power(marker, trace)
        session.markers.append(marker)
        self.spectrum_renderer.set_marker(marker)
        self._connect_marker_line(marker)
        self._audit(
            "user", "marker_added", marker_id=marker.marker_id, name=marker.name,
            trace_id=marker.trace_id, frequency_hz=marker.frequency_hz, power_dbm=marker.power,
        )
        self._bump()
        return marker

    def add_peak_marker(self) -> None:
        session = self.active_session()
        trace = self._active_trace(session) if session else None
        if session is None or trace is None:
            return
        raw = self.spectrum_renderer.raw_trace_data(trace.trace_id)
        if raw is None:
            return
        frequencies, values = raw
        marker = self.add_marker()
        if marker is None:
            return
        marker.marker_type = MarkerType.PEAK
        marker.locked = True
        self._distribute_peak_markers(session, frequencies, values)
        self.spectrum_renderer.set_marker(marker)
        self._audit(
            "user", "marker_peak_selected", marker_id=marker.marker_id,
            trace_id=trace.trace_id, source="raw_trace", frequency_hz=marker.frequency_hz,
            power_dbm=marker.power,
        )
        self._bump()

    def add_delta_marker(self) -> None:
        session = self.active_session()
        if session is None:
            return
        if not session.markers:
            self.add_peak_marker()
        if not session.markers:
            return
        reference = session.markers[0]
        marker = self.add_marker()
        if marker is None:
            return
        marker.marker_type = MarkerType.DELTA
        marker.reference_marker_id = reference.marker_id
        self.spectrum_renderer.set_marker(marker)
        self._audit(
            "user", "delta_marker_added", marker_id=marker.marker_id,
            reference_marker_id=reference.marker_id,
        )
        self._bump()
    def remove_selected_marker(self, row: int) -> None:
        session = self.active_session()
        if session is None or row < 0 or row >= len(session.markers):
            return
        marker = session.markers.pop(row)
        self.spectrum_renderer.remove_marker(marker.marker_id)
        self._audit("user", "marker_removed", marker_id=marker.marker_id, name=marker.name)
        self._bump()

    def clear_markers(self) -> None:
        session = self.active_session()
        if session is None:
            return
        marker_ids = [marker.marker_id for marker in session.markers]
        session.markers.clear()
        for marker_id in marker_ids:
            self.spectrum_renderer.remove_marker(marker_id)
        self._audit("user", "markers_cleared", count=len(marker_ids))
        self._bump()

    def set_marker_type(self, marker_id: str, marker_type: MarkerType) -> None:
        session = self.active_session()
        if session is None:
            return
        marker = next((item for item in session.markers if item.marker_id == marker_id), None)
        if marker is None:
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
        self._audit(
            "user",
            "marker_type_changed",
            marker_id=marker.marker_id,
            marker_type=marker_type.value,
            locked=marker.locked,
        )
        self._bump()

    def set_marker_enabled(self, marker_id: str, enabled: bool) -> None:
        session = self.active_session()
        if session is None:
            return
        marker = next((item for item in session.markers if item.marker_id == marker_id), None)
        if marker is None:
            return
        marker.enabled = enabled
        self.spectrum_renderer.set_marker(marker)
        self._audit("user", "marker_visibility_changed", marker_id=marker_id, enabled=enabled)
        self._bump()

    def set_marker_trace(self, marker_id: str, trace_id: str | None) -> None:
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
        self._audit(
            "user",
            "marker_trace_changed",
            marker_id=marker.marker_id,
            trace_id=trace_id,
            frequency_hz=marker.frequency_hz,
            power_dbm=marker.power,
        )
        self._bump()

    def _connect_marker_line(self, marker: Marker) -> None:
        pair = self.spectrum_renderer.markers.get(marker.marker_id)
        if not pair or pair[0].property("connected"):
            return
        pair[0].setProperty("connected", True)
        pair[0].sigPositionChanged.connect(
            lambda line, marker_id=marker.marker_id: self._marker_moved(marker_id, line.value())
        )

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
        self._audit(
            "user",
            "marker_moved",
            marker_id=marker.marker_id,
            trace_id=marker.trace_id,
            frequency_hz=marker.frequency_hz,
            power_dbm=marker.power,
        )
        self._bump()

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
        peak_markers = [m for m in session.markers if m.marker_type == MarkerType.PEAK and m.enabled]
        if not peak_markers:
            return
        peaks = peak_search_values(frequencies, values, limit=len(peak_markers))
        for marker, peak in zip(peak_markers, peaks):
            marker.frequency_hz, marker.power, _ = peak

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    def update_frequency_region(self, start_mhz: float, stop_mhz: float) -> FrequencyRegion | None:
        session = self.active_session()
        if session is None:
            return None
        start = start_mhz * 1e6
        stop = stop_mhz * 1e6
        if stop <= start:
            self.error.emit("Полоса", "Конечная частота должна быть выше начальной")
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
        self._bump()
        return region

    def set_band_from_trace(self) -> None:
        session = self.active_session()
        trace = self._active_frequency_trace(session) if session else None
        if trace is None:
            return
        span = trace.stop_frequency_hz - trace.start_frequency_hz
        self._last_band = (
            (trace.start_frequency_hz + span * 0.4) / 1e6,
            (trace.start_frequency_hz + span * 0.6) / 1e6,
        )
        self._bump()

    def band_defaults(self) -> tuple[float, float]:
        session = self.active_session()
        trace = self._active_frequency_trace(session) if session else None
        if trace is None or not trace.is_frequency_trace:
            return (0.0, 0.0)
        span = trace.stop_frequency_hz - trace.start_frequency_hz
        return (
            (trace.start_frequency_hz + span * 0.4) / 1e6,
            (trace.start_frequency_hz + span * 0.6) / 1e6,
        )

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
        self._audit(
            "user",
            "frequency_region_moved",
            region_id=region_id,
            start_hz=region.start_frequency_hz,
            stop_hz=region.stop_frequency_hz,
        )
        self._bump()

    def measure_channel_power(self, start_mhz: float, stop_mhz: float) -> None:
        def calculate(trace: SpectrumTrace, region: FrequencyRegion) -> Any:
            return channel_power(trace, region.start_frequency_hz, region.stop_frequency_hz)

        self._run_trace_analysis("Channel Power", calculate, start_mhz, stop_mhz)

    def measure_obw(self) -> None:
        self._run_trace_analysis(
            "Occupied Bandwidth 99%",
            lambda trace, region: occupied_bandwidth(trace, 0.99),
            needs_region=False,
        )

    def measure_noise(self, start_mhz: float, stop_mhz: float) -> None:
        def calculate(trace: SpectrumTrace, region: FrequencyRegion) -> Any:
            return noise_floor(trace, region.start_frequency_hz, region.stop_frequency_hz)

        self._run_trace_analysis("Noise Floor", calculate, start_mhz, stop_mhz)

    def measure_snr(self, start_mhz: float, stop_mhz: float) -> None:
        def calculate(trace: SpectrumTrace, region: FrequencyRegion) -> Any:
            return snr(trace, (region.start_frequency_hz, region.stop_frequency_hz))

        self._run_trace_analysis("SNR", calculate, start_mhz, stop_mhz)

    def measure_acpr(self, start_mhz: float, stop_mhz: float, offset_mhz: float, width_mhz: float) -> None:
        def calculate(trace: SpectrumTrace, region: FrequencyRegion) -> Any:
            return acpr(
                trace, region.center_frequency_hz, region.bandwidth_hz,
                offset_mhz * 1e6, width_mhz * 1e6,
            )

        self._run_trace_analysis("ACPR / ACLR", calculate, start_mhz, stop_mhz)

    def _run_trace_analysis(
        self,
        name: str,
        function: Callable[[SpectrumTrace, FrequencyRegion], Any],
        start_mhz: float | None = None,
        stop_mhz: float | None = None,
        needs_region: bool = True,
    ) -> None:
        session = self.active_session()
        trace = self._active_trace(session) if session else None
        if session is None or trace is None or not trace.is_frequency_trace:
            self.error.emit("Измерение", "Выберите частотную трассу")
            return
        if needs_region and start_mhz is not None and stop_mhz is not None:
            region = self.update_frequency_region(start_mhz, stop_mhz)
        else:
            region = session.frequency_regions[0] if session.frequency_regions else FrequencyRegion(
                start_frequency_hz=trace.start_frequency_hz, stop_frequency_hz=trace.stop_frequency_hz
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
        self.busy.emit(True, f"Расчёт: {name}")
        worker = TaskWorker(function, trace, region)
        worker.signals.result.connect(
            lambda value: self._analysis_ready(session.session_id, trace.trace_id, region.region_id, name, value)
        )
        self._start_worker(worker)

    def _analysis_ready(self, session_id: str, trace_id: str, region_id: str, name: str, value: Any) -> None:
        session = self.repository.get(session_id)
        if session is None:
            return
        rows: list[tuple[str, Any]] = []
        if isinstance(value, list):
            for index, item in enumerate(value):
                rows.extend((f"{index + 1}.{key}", val) for key, val in asdict(item).items())
        elif hasattr(value, "__dataclass_fields__"):
            rows = list(asdict(value).items())
        else:
            rows = [("result", value)]
        approximate = bool(getattr(value, "approximate", False))
        result = AnalysisResult(
            name, name,
            {key: self._json_value(val) for key, val in rows},
            trace_id, region_id, approximate,
        )
        session.analysis_results.append(result)
        LOGGER.info("Расчёт %s завершён", name)
        self._audit(
            "program",
            "trace_analysis_completed",
            analysis=name,
            trace_id=trace_id,
            region_id=region_id,
            approximate=approximate,
        )
        self._bump()

    def clear_measurement_results(self) -> None:
        session = self.active_session()
        if session is None:
            return
        count = len(session.analysis_results)
        session.analysis_results.clear()
        self._audit("user", "measurement_results_cleared", count=count)
        self._bump()

    def remove_analysis_result(self, result_id: str) -> None:
        session = self.active_session()
        if session is None:
            return
        session.analysis_results = [r for r in session.analysis_results if r.result_id != result_id]
        self._audit("user", "measurement_result_removed", result_id=result_id)
        self._bump()

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
        self._bump()

    def delete_frequency_region(self) -> None:
        session = self.active_session()
        if session is None:
            return
        region_ids = {region.region_id for region in session.frequency_regions}
        session.frequency_regions.clear()
        self.spectrum_renderer.set_regions([])
        self.waterfall_renderer.clear_frequency_region()
        self._audit("user", "frequency_regions_deleted", count=len(region_ids))
        self._bump()

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

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    @staticmethod
    def _source_revision(path: Path) -> str:
        try:
            stat = path.stat()
        except OSError:
            return "unavailable"
        return f"{stat.st_size}:{stat.st_mtime_ns}"
    # Time-gated Channel Power
    # ------------------------------------------------------------------
    def request_time_gated_power(
        self,
        request: ChannelPowerRequest | None = None,
        manual_override: np.ndarray | None = None,
        *,
        start_mhz: float | None = None,
        stop_mhz: float | None = None,
    ) -> bool:
        """Start one cancellable time-gated analysis for the active waterfall."""
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session is not None else None
        index = self._active_spectrogram_index(session)
        trace = self._active_frequency_trace(session) if session is not None else None
        if session is None or waterfall is None or index is None or not index.frame_count:
            self.error.emit("Channel Power", "Сначала дождитесь индекса waterfall")
            return False
        if request is None:
            start_hz = (
                float(start_mhz) * 1e6
                if start_mhz is not None
                else trace.start_frequency_hz if trace is not None else waterfall.start_frequency_hz
            )
            stop_hz = (
                float(stop_mhz) * 1e6
                if stop_mhz is not None
                else trace.stop_frequency_hz if trace is not None else waterfall.stop_frequency_hz
            )
            request = ChannelPowerRequest(
                session_id=session.session_id,
                trace_id=trace.trace_id if trace is not None else waterfall.waterfall_id,
                frequency_start_hz=min(start_hz, stop_hz),
                frequency_stop_hz=max(start_hz, stop_hz),
                mode=ChannelPowerMode.ENTIRE_RECORDING_ALL_FRAMES,
                frame_inclusion=FrameInclusion.ALL,
                activity_config=ActivityDetectionConfig(),
                power_semantics=(
                    PowerSemantics.RBW_FILTERED_POWER
                    if trace is not None and trace.rbw_hz
                    else PowerSemantics.UNKNOWN
                ),
                rbw_hz=trace.rbw_hz if trace is not None else None,
                source_revision=self._source_revision(session.source_path),
            )
        if request.session_id != session.session_id:
            self.error.emit("Channel Power", "Запрос относится к другой сессии")
            return False
        override = (
            np.asarray(manual_override, dtype=np.uint8).copy()
            if manual_override is not None
            else np.full(index.frame_count, ManualOverride.AUTO, dtype=np.uint8)
        )
        if override.size != index.frame_count:
            self.error.emit("Channel Power", "Маска активности имеет неверный размер")
            return False
        self._channel_power_generation += 1
        pending = (session, waterfall, index, request, override, self._channel_power_generation)
        if self._channel_power_worker is not None:
            self._channel_power_worker.cancel()
            self._pending_channel_power_request = pending
            self.busy.emit(True, "Предыдущий расчёт отменяется…")
            return True
        self._start_time_gated_request(*pending)
        return True

    def _start_time_gated_request(
        self,
        session: MeasurementSession,
        waterfall: WaterfallData,
        index: SpectrogramIndex,
        request: ChannelPowerRequest,
        override: np.ndarray,
        generation: int,
    ) -> None:
        if generation != self._channel_power_generation or session.session_id != self.active_session_id:
            return
        worker = TaskWorker(
            _analyze_time_gated_waterfall,
            self.time_gated_service,
            session.source_path,
            self._spectrogram_info(waterfall),
            waterfall.frequencies_hz,
            request,
            override,
            index,
            pass_progress=True,
            pass_cancel=True,
        )
        self._channel_power_worker = worker
        self._channel_power_session_id = session.session_id
        self._channel_power_waterfall_id = waterfall.waterfall_id
        worker.signals.result.connect(
            lambda result: self._time_gated_ready(
                session.session_id, waterfall.waterfall_id, generation, result
            )
        )
        worker.signals.error.connect(
            lambda message, _details: self.error.emit("Channel Power", message)
        )
        worker.signals.finished.connect(
            lambda finished_worker=worker: self._time_gated_worker_finished(finished_worker)
        )
        self.busy.emit(True, "Channel Power по времени…")
        self._start_worker(worker)

    def _time_gated_ready(
        self,
        session_id: str,
        waterfall_id: str,
        generation: int,
        result: TimeGatedChannelPowerResult,
    ) -> None:
        if generation != self._channel_power_generation:
            self._audit("program", "time_gated_result_discarded", reason="stale_generation")
            return
        if session_id != self.active_session_id or self.repository.get(session_id) is None:
            self._audit("program", "time_gated_result_discarded", reason="inactive_session")
            return
        self._channel_power_results[(session_id, waterfall_id)] = result
        self._audit(
            "program", "time_gated_channel_power_completed",
            waterfall_id=waterfall_id, valid_frames=result.frame_count_valid,
            events=len(result.events), quality=result.calculation_quality.value,
        )
        self.time_gated_ready.emit(result)
        self._bump()

    def _time_gated_worker_finished(self, worker: TaskWorker) -> None:
        if worker is not self._channel_power_worker:
            return
        self._channel_power_worker = None
        self._channel_power_session_id = None
        self._channel_power_waterfall_id = None
        pending = self._pending_channel_power_request
        self._pending_channel_power_request = None
        if pending is not None:
            QTimer.singleShot(0, lambda pending=pending: self._start_time_gated_request(*pending))

    def cancel_time_gated_power(self) -> None:
        self._channel_power_generation += 1
        self._pending_channel_power_request = None
        if self._channel_power_worker is not None:
            self._channel_power_worker.cancel()
    # Exports
    # ------------------------------------------------------------------
    def request_export(self, kind: str, path: str) -> bool:
        """Dispatch one export request; the selected presenter method owns its worker."""
        handlers: dict[str, Callable[[str], None]] = {
            "trace": self.export_active_trace,
            "traces": self.export_all_traces,
            "npz": self.export_npz,
            "waterfall": self.export_waterfall,
            "markers": self.export_markers,
            "results": self.export_results,
            "metadata": self.export_metadata,
            "animation": lambda target: self.export_animation(target, "gif"),
            "heatmap_png": self.export_heatmap_png,
            "heatmap_csv": self.export_heatmap_csv,
            "heatmap_npz": self.export_heatmap_npz,
            "heatmap_json": self.export_heatmap_json,
            "time_gated_summary": self.export_time_gated_summary,
            "time_gated_frames": self.export_time_gated_frames,
            "time_gated_events": self.export_time_gated_events,
            "time_gated_json": self.export_time_gated_json,
        }
        handler = handlers.get(kind)
        if handler is None:
            self.error.emit("Экспорт", f"Неизвестный тип экспорта: {kind}")
            return False
        handler(path)
        return True
    def export_active_trace(self, path: str) -> None:
        session = self.active_session()
        trace = self._active_trace(session) if session else None
        if trace is None:
            return
        self._start_export(export_trace_csv, trace, path)

    def export_all_traces(self, path: str) -> None:
        traces = [trace for session in self.repository.all() for trace in session.traces.values() if trace.enabled]
        if not traces:
            return
        worker = TaskWorker(export_traces_csv, traces, path, pass_progress=True, pass_cancel=True)
        self._start_worker(worker)

    def export_npz(self, path: str) -> None:
        session = self.active_session()
        if session:
            self._start_export(export_session_npz, session, path)

    def export_waterfall(self, path: str) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if waterfall:
            self._start_export(export_waterfall_region_csv, waterfall, path)

    def export_markers(self, path: str) -> None:
        session = self.active_session()
        if session:
            self._start_export(export_markers_csv, session.markers, path)

    def export_results(self, path: str) -> None:
        session = self.active_session()
        if session:
            self._start_export(export_results_csv, session.analysis_results, path)

    def export_metadata(self, path: str) -> None:
        session = self.active_session()
        if session:
            self._start_export(export_session_json, session, path)

    def export_animation(self, path: str, suffix: str) -> None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if session is None or waterfall is None or waterfall.values is None:
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
            int(round(self._frame_to_preview_row(
                waterfall, self._active_spectrogram_index(session), self._frame_nav.requested_frame
            ))),
            waterfall.values.shape[0] - 1,
            fps=float(self._playback_fps),
            max_frames=300,
            cmap=(
                "gray"
                if self.waterfall_colormap() == "Grayscale"
                else self.waterfall_colormap().casefold()
            ),
            vmin=self.waterfall_min_level(),
            vmax=self.waterfall_max_level(),
            pass_progress=True,
            pass_cancel=True,
        )
        self._audit(
            "user",
            "waterfall_animation_export_requested",
            format=suffix.lower(),
            path=str(path),
            fps=self._playback_fps,
        )
        self.busy.emit(True, f"Экспорт {suffix.upper()}…")
        self._start_worker(worker)

    def waterfall_colormap(self) -> str:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        return waterfall.colormap if waterfall is not None else "Turbo"

    def waterfall_min_level(self) -> float:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        return waterfall.min_level if waterfall is not None and waterfall.min_level is not None else -120.0

    def waterfall_max_level(self) -> float:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        return waterfall.max_level if waterfall is not None and waterfall.max_level is not None else -20.0

    def set_waterfall_levels(self, minimum: float, maximum: float) -> None:
        self.waterfall_renderer.set_levels(minimum, maximum)
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if waterfall is not None:
            waterfall.min_level, waterfall.max_level = minimum, maximum

    def set_waterfall_colormap(self, name: str) -> None:
        self.waterfall_renderer.set_colormap(name)
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if waterfall is not None:
            waterfall.colormap = name

    def export_heatmap_png(self, path: str) -> None:
        current = self._current_heatmap_result()
        if current is None:
            return
        session, _waterfall, result = current
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
                self.heatmap_opacity(),
                path,
                levels=self._heatmap_current_levels,
            )
        except Exception as exc:
            self.error.emit("Экспорт Heatmap", str(exc))
            return
        self._export_completed("export_heatmap_png", written)

    def export_heatmap_csv(self, path: str) -> None:
        current = self._current_heatmap_result()
        if current is None:
            return
        session, _waterfall, result = current
        self._start_long_export(export_heatmap_csv, result, path)

    def export_heatmap_npz(self, path: str) -> None:
        current = self._current_heatmap_result()
        if current is None:
            return
        session, _waterfall, result = current
        self._start_export(export_heatmap_npz, result, path)

    def export_heatmap_json(self, path: str) -> None:
        current = self._current_heatmap_result()
        if current is None:
            return
        session, waterfall, result = current
        assert self._heatmap_applied_key is not None
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
                display_config=self._heatmap_display_config(),
                persistence_snapshot=self._heatmap_applied_snapshot,
            )
        )

    def export_time_gated_summary(self, path: str) -> None:
        current = self._current_time_gated_result()
        if current is None:
            return
        session, _waterfall, result = current
        trace = self._active_frequency_trace(session)
        self._start_export(
            export_time_gated_summary_csv,
            result,
            path,
            session.source_path,
            trace.name if trace else result.request.trace_id,
        )

    def export_time_gated_frames(self, path: str) -> None:
        current = self._current_time_gated_result()
        if current is None:
            return
        session, _waterfall, result = current
        self._start_long_export(export_time_gated_frames_csv, result, path)

    def export_time_gated_events(self, path: str) -> None:
        current = self._current_time_gated_result()
        if current is None:
            return
        session, _waterfall, result = current
        self._start_export(export_time_gated_events_csv, result, path)

    def export_time_gated_json(self, path: str) -> None:
        current = self._current_time_gated_result()
        if current is None:
            return
        session, _waterfall, result = current
        trace = self._active_frequency_trace(session)
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
            self.error.emit("Экспорт Heatmap", "Сначала выполните расчёт Heatmap")
            return None
        if self._heatmap_controller.phase in (PersistencePhase.REBUILDING, PersistencePhase.STALE):
            self.error.emit("Экспорт Heatmap", "Результат устарел — дождитесь завершения пересчёта")
            return None
        if self._heatmap_applied_key[:2] != (session.session_id, waterfall.waterfall_id):
            self.error.emit(
                "Экспорт Heatmap", "Рассчитанный Heatmap относится к другой сессии или потоку"
            )
            return None
        return session, waterfall, result

    def _current_time_gated_result(self) -> tuple[
        MeasurementSession, WaterfallData, TimeGatedChannelPowerResult
    ] | None:
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session else None
        if session is None or waterfall is None:
            return None
        result = self._channel_power_results.get((session.session_id, waterfall.waterfall_id))
        if result is None:
            self.error.emit("Экспорт Channel Power", "Сначала выполните расчёт")
            return None
        return session, waterfall, result

    def _start_export(self, function: Callable[..., Any], *args: Any) -> None:
        self._audit(
            "user",
            "export_requested",
            exporter=getattr(function, "__name__", type(function).__name__),
            target=self._export_target(args),
        )
        self.busy.emit(True, "Экспорт…")
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
        self.busy.emit(True, "Экспорт…")
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

    # ------------------------------------------------------------------
    # Workspace persistence
    # ------------------------------------------------------------------
    def save_workspace(self, path: str | Path | None, ui_state: dict[str, Any] | None = None) -> Path | None:
        if path is None:
            return None
        target = Path(path)
        self._current_workspace = target
        write_workspace(
            target, self.repository.all(), self.active_session_id, ui_state or {}
        )
        LOGGER.info("Workspace сохранён: %s", target)
        self._audit("program", "workspace_saved", path=str(target))
        self._bump()
        return target

    def open_workspace(self, path: str | Path) -> None:
        source = Path(path)
        try:
            payload = read_workspace(source)
        except Exception as exc:
            self.error.emit("Workspace", str(exc))
            return
        self._current_workspace = source
        self._audit("user", "workspace_opened", path=str(source))
        self._workspace_payloads = {
            str(Path(item["source_path"]).resolve()).casefold(): item for item in payload["sessions"]
        }
        for item in payload["sessions"]:
            self.load_file(Path(item["source_path"]), item)
        self._bump()

    # ------------------------------------------------------------------
    # Heatmap / persistence
    # ------------------------------------------------------------------
    def heatmap_enable(self, config: PersistenceConfig | None = None) -> None:
        self._heatmap_enabled = True
        context = self._heatmap_controller_context()
        self._heatmap_controller.set_context(context)
        if context is None:
            self._heatmap_set_status("Нет данных: откройте DFL и дождитесь индекса waterfall", error=True)
            self._bump()
            return
        mode = self._heatmap_mode(config)
        current_frame = self._current_heatmap_frame()
        if mode in HEATMAP_LIVE_MODES:
            self._heatmap_controller.enable(
                config or self._heatmap_build_persistence_config(),
                current_frame,
                self._heatmap_frame_timestamp(current_frame),
            )
        else:
            self._heatmap_controller.request_fixed(
                self._heatmap_build_config(config), current_frame
            )
        self._bump()

    def heatmap_disable(self) -> None:
        self._heatmap_enabled = False
        self._heatmap_controller.disable()
        self._heatmap_reset_overlay()
        self._heatmap_set_status("Heatmap выключен")
        self._bump()

    def heatmap_recalculate(self, config: PersistenceConfig | None = None) -> None:
        if self._heatmap_mode(config) in HEATMAP_LIVE_MODES:
            self._heatmap_controller.recalculate()
            return
        self._heatmap_controller.request_fixed(
            self._heatmap_build_config(config), self._current_heatmap_frame()
        )
        self._bump()

    def heatmap_cancel(self) -> None:
        if self._heatmap_controller.active_ticket is None and self._heatmap_controller.pending_ticket is None:
            self._heatmap_set_status("Нет активного расчёта")
            return
        self._heatmap_set_status("Отмена расчёта…")
        self._heatmap_controller.cancel()

    def heatmap_clear(self) -> None:
        self._heatmap_controller.clear()
        self._heatmap_reset_overlay()
        self._heatmap_set_status("Heatmap очищен")
        self._audit("user", "heatmap_cleared")
        self._bump()

    def heatmap_opacity(self) -> float:
        return 0.85

    def _heatmap_set_status(self, text: str, *, error: bool = False) -> None:
        self._heatmap_status = text
        self._heatmap_status_error = error

    def _heatmap_mode(self, config: PersistenceConfig | None = None) -> PersistenceMode:
        if config is not None:
            return config.mode
        return PersistenceMode.ROLLING_EXACT

    def _heatmap_normalization(self) -> HeatmapNormalization:
        return HeatmapNormalization.COUNT

    def _heatmap_display_config(self) -> HeatmapDisplayConfig:
        return HeatmapDisplayConfig(
            normalization=self._heatmap_normalization(),
            palette="Turbo",
            opacity=self.heatmap_opacity(),
            color_scale_mode=ColorScaleMode.AUTO_CURRENT,
            color_min=None,
            color_max=None,
        )

    def _heatmap_build_config(self, config: PersistenceConfig | None = None) -> HeatmapConfig:
        mode = self._heatmap_mode(config)
        frame_start = frame_end = None
        range_mode = HeatmapRangeMode.FULL
        if mode is PersistenceMode.SELECTED_RANGE:
            range_mode = HeatmapRangeMode.SELECTED
            frame_start = 0
            frame_end = max(0, self._frame_nav.frame_count - 1)
        return HeatmapConfig(
            range_mode=range_mode,
            window_frames=config.window_frames if config is not None else 500,
            frame_start=frame_start,
            frame_end=frame_end,
            power_min_dbm=config.power_min_dbm if config is not None else -120.0,
            power_max_dbm=config.power_max_dbm if config is not None else -20.0,
            power_bins=config.power_bins if config is not None else 256,
            normalization=self._heatmap_normalization(),
            decay=1.0,
            sampling_policy=config.sampling_policy if config is not None else HeatmapSamplingPolicy.FULL_RANGE,
        )

    def _heatmap_build_persistence_config(self) -> PersistenceConfig:
        return PersistenceConfig(
            mode=PersistenceMode.ROLLING_EXACT,
            window_unit=WindowUnit.FRAMES,
            window_frames=500,
            window_seconds=None,
            half_life_seconds=None,
            decay_cutoff_epsilon=1e-3,
            follow_playhead=True,
            power_min_dbm=-120.0,
            power_max_dbm=-20.0,
            power_bins=256,
            sampling_policy=HeatmapSamplingPolicy.FULL_RANGE,
            minimum_window_frames=self._heatmap_window_minimum_frames,
            minimum_window_seconds=None,
        )

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

    def _heatmap_frame_timestamp(self, frame: int) -> float | None:
        session = self.active_session()
        index = self._active_spectrogram_index(session) if session is not None else None
        if index is not None and 0 <= frame < index.timestamps.size:
            value = float(index.timestamps[frame])
            return value if np.isfinite(value) else None
        return None

    def _current_heatmap_frame(self) -> int:
        session = self.active_session()
        return session.current_frame if session is not None else 0

    def _connect_heatmap_navigation(self) -> None:
        if self._heatmap_navigation_connected:
            return
        self._heatmap_navigation_connected = True
        self._frame_nav.span_event.connect(self._on_heatmap_frame_span)

    def _on_heatmap_frame_span(self, event: FrameSpanEvent) -> None:
        if not self._heatmap_enabled:
            return
        identity = self._heatmap_active_identity()
        controller_identity = self._heatmap_controller.context_identity
        if identity is None or controller_identity is None or identity != controller_identity:
            return
        self._heatmap_controller.on_frame_span(event)

    def _heatmap_context_changed(self) -> None:
        identity = self._heatmap_active_identity()
        if identity == self._heatmap_last_context_identity:
            return
        self._heatmap_last_context_identity = identity
        self._heatmap_reset_overlay()
        context = self._heatmap_controller_context()
        self._heatmap_controller.set_context(context)
        if not self._heatmap_enabled:
            return
        if context is None:
            self._heatmap_set_status("Нет данных: откройте DFL и дождитесь индекса waterfall")
            return
        self._heatmap_activate_current_mode("context_changed")

    def _heatmap_index_ready(self) -> None:
        if not self._heatmap_enabled:
            return
        context = self._heatmap_controller_context()
        if context is None:
            return
        self._heatmap_controller.set_context(context)
        self._heatmap_activate_current_mode("index_ready")

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
            self._heatmap_controller.request_fixed(
                self._heatmap_build_config(), current_frame
            )
        elif not self._heatmap_controller.try_show_cached(
            self._heatmap_build_config(), current_frame
        ):
            self._heatmap_set_status("Heatmap не рассчитан — нажмите «Пересчитать»")

    def _heatmap_on_session_removed(self, session_id: str) -> None:
        self._heatmap_controller.invalidate_session(session_id)
        if self._heatmap_applied_key is not None and self._heatmap_applied_key[0] == session_id:
            self._heatmap_reset_overlay()
        if self._heatmap_last_context_identity is not None and self._heatmap_last_context_identity[0] == session_id:
            self._heatmap_last_context_identity = None

    def _heatmap_reset_overlay(self) -> None:
        self.spectrum_renderer.clear_heatmap()
        self._heatmap_applied_snapshot = None
        self._heatmap_applied = None
        self._heatmap_applied_key = None
        self._heatmap_applied_range = None

    def _apply_persistence_snapshot(self, snapshot: PersistenceSnapshot) -> None:
        identity = (
            snapshot.source_key.session_id,
            snapshot.source_key.waterfall_id,
            snapshot.source_key.source_id,
        )
        if identity[:2] != self._heatmap_active_identity():
            return
        display = self._heatmap_display_config()
        if not self._apply_snapshot_image(snapshot, display=display):
            return
        self._heatmap_applied_snapshot = snapshot
        self._heatmap_applied = self._heatmap_result_from_snapshot(snapshot, display.normalization)
        self._heatmap_applied_key = identity
        self._heatmap_applied_range = (snapshot.frame_start, snapshot.frame_end)
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
        self._bump()

    def _apply_snapshot_image(
        self,
        snapshot: PersistenceSnapshot,
        display: HeatmapDisplayConfig | None = None,
    ) -> bool:
        if display is None:
            display = self._heatmap_display_config()
        image = self._normalize_snapshot(snapshot, display.normalization)
        levels = self._heatmap_compute_levels(image, display)
        config = snapshot.config
        try:
            left, right = self._heatmap_frequency_edges(snapshot)
        except ValueError as exc:
            self.spectrum_renderer.clear_heatmap()
            self._heatmap_set_status(
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
        span: float | None = None
        session = self.active_session()
        waterfall = self._active_waterfall(session) if session is not None else None
        if waterfall is not None and waterfall.frequency_step_hz > 0:
            span = float(waterfall.frequency_step_hz)
        return frequency_bin_edges(snapshot.frequencies_hz, single_bin_span_hz=span)

    def _heatmap_compute_levels(
        self, image: np.ndarray, display: HeatmapDisplayConfig
    ) -> tuple[float, float]:
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
            self._heatmap_set_status(status, error=event.phase is PersistencePhase.ERROR)
        self.heatmap_phase.emit(event)
        self._bump()

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

    # ------------------------------------------------------------------
    # Active helpers
    # ------------------------------------------------------------------
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
        active = OfflineDflPresenter._active_trace(session)
        if active is not None and active.is_frequency_trace:
            return active
        return next((trace for trace in session.traces.values() if trace.is_frequency_trace), None)

    def _frame_trace(
        self, session: MeasurementSession, waterfall: WaterfallData
    ) -> SpectrumTrace | None:
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

    # ------------------------------------------------------------------
    # Navigation wiring
    # ------------------------------------------------------------------
    def _connect_navigation(self) -> None:
        if self._navigation_connected:
            return
        self._navigation_connected = True

    # ------------------------------------------------------------------
    # Snapshot assembly
    # ------------------------------------------------------------------
    def _bump(self) -> None:
        self._generation += 1
        self.snapshot_ready.emit(self.snapshot())

    def snapshot(self) -> OfflineWorkspaceSnapshot:
        sessions: list[OfflineSessionSnapshot] = []
        for session in self.repository.all():
            index = self._active_spectrogram_index(session)
            frame_count = 0
            waterfall = self._active_waterfall(session)
            if waterfall is not None:
                if index is not None:
                    frame_count = index.frame_count
                elif waterfall.values is not None:
                    frame_count = waterfall.values.shape[0]
            sessions.append(
                OfflineSessionSnapshot(
                    session_id=session.session_id,
                    name=session.name,
                    source_path=str(session.source_path),
                    visible=session.visible,
                    source_type=(
                        session.source_descriptor.source_type.value
                        if session.source_descriptor is not None else "dfl_file"
                    ),
                    active_trace_id=session.active_trace_id,
                    active_waterfall_id=session.active_waterfall_id,
                    current_frame=session.current_frame,
                    frame_count=frame_count,
                    traces=tuple(
                        OfflineTraceSnapshot(
                            trace_id=trace.trace_id,
                            name=trace.name,
                            trace_mode=trace.trace_mode,
                            enabled=trace.enabled,
                        )
                        for trace in session.traces.values()
                    ),
                    waterfalls=tuple(
                        OfflineWaterfallSnapshot(
                            waterfall_id=item.waterfall_id,
                            name=item.name,
                            line_count=item.line_count,
                            point_count=item.point_count,
                        )
                        for item in session.waterfalls.values()
                    ),
                )
            )
        active = self.active_session()
        frame = self._frame_nav.requested_frame
        playback = OfflinePlaybackSnapshot(
            playing=self.playback_timer.isActive(),
            frame=frame,
            frame_count=self._frame_nav.frame_count,
            speed=self._playback_speed,
            fps=self._playback_fps,
            loop=self._playback_loop,
            no_skip=self._playback_no_skip,
        )
        heatmap = OfflineHeatmapSnapshot(
            enabled=self._heatmap_enabled,
            mode=self._heatmap_mode().value,
            phase=self._heatmap_controller.phase.value,
            status=self._heatmap_status,
            error=self._heatmap_status_error,
            applied=self._heatmap_applied is not None,
            stale=self._heatmap_controller.phase in (PersistencePhase.REBUILDING, PersistencePhase.STALE),
            can_cancel=(
                self._heatmap_controller.active_ticket is not None
                or self._heatmap_controller.pending_ticket is not None
                or self._heatmap_controller.phase in (PersistencePhase.UPDATING, PersistencePhase.REBUILDING, PersistencePhase.STALE)
            ),
        )
        trace = self._active_trace(active) if active is not None else None
        trace_summary = ""
        if trace is not None:
            span = abs(trace.stop_frequency_hz - trace.start_frequency_hz)
            rbw = f"{trace.rbw_hz:g} Hz" if trace.rbw_hz else "—"
            trace_summary = f"Span {span / 1e6:.6g} MHz · RBW {rbw} · {trace.point_count:,} точек"
        status = OfflineStatusSnapshot(
            source_path=str(active.source_path) if active is not None else "",
            trace_summary=trace_summary,
        )
        return OfflineWorkspaceSnapshot(
            generation=self._generation,
            active_session_id=self.active_session_id,
            sessions=tuple(sessions),
            playback=playback,
            heatmap=heatmap,
            status=status,
            workspace_path=str(self._current_workspace) if self._current_workspace is not None else None,
        )

    def markers_snapshot(self) -> tuple[OfflineMarkerSnapshot, ...]:
        session = self.active_session()
        if session is None:
            return ()
        lookup = {item.marker_id: item for item in session.markers}
        rows: list[OfflineMarkerSnapshot] = []
        for marker in session.markers:
            reference = lookup.get(marker.reference_marker_id or "")
            delta_f = marker.frequency_hz - reference.frequency_hz if reference else np.nan
            delta_l = marker.power - reference.power if reference else np.nan
            rows.append(
                OfflineMarkerSnapshot(
                    marker_id=marker.marker_id,
                    name=marker.name,
                    marker_type=_MARKER_TYPE_LABELS.get(marker.marker_type, marker.marker_type.value),
                    frequency_mhz=f"{marker.frequency_hz / 1e6:.6f} MHz",
                    power_dbm=f"{marker.power:.3f} dBm",
                    delta_f_mhz=f"{delta_f / 1e6:.6f} MHz" if np.isfinite(delta_f) else "",
                    delta_l_db=f"{delta_l:.3f} dB" if np.isfinite(delta_l) else "",
                    timestamp=(
                        datetime.fromtimestamp(marker.timestamp).isoformat()
                        if marker.timestamp else ""
                    ),
                    trace_id=marker.trace_id,
                    enabled=marker.enabled,
                    locked=marker.locked,
                )
            )
        return tuple(rows)

    def results_snapshot(self) -> tuple[OfflineResultSnapshot, ...]:
        session = self.active_session()
        if session is None:
            return ()
        rows: list[OfflineResultSnapshot] = []
        for result in session.analysis_results:
            for key, value in result.values.items():
                rows.append(
                    OfflineResultSnapshot(
                        result_id=result.result_id,
                        name=result.name,
                        key=str(key),
                        value=self._format_value(value),
                        enabled=result.enabled,
                    )
                )
        return tuple(rows)

    def metadata_text(self) -> str:
        session = self.active_session()
        if session is None:
            return ""
        metadata = session.metadata
        lines = [
            f"Файл: {session.source_path}",
            f"Прибор: {metadata.device_type}",
            f"Firmware: {metadata.firmware_version}",
            f"System: {metadata.system}",
            f"Каналы: {', '.join(metadata.channel_names)}",
            f"Режимы: {', '.join(metadata.modes)}",
            f"Потоков: {len(metadata.streams)}",
            f"Предупреждений: {len(metadata.warnings)}",
        ]
        if metadata.warnings:
            lines.extend(["", *metadata.warnings])
        return "\n".join(lines)

    def trace_properties_text(self, trace_id: str | None = None) -> str:
        session = self.active_session()
        if session is None:
            return ""
        trace = session.traces.get(trace_id or "") or self._active_trace(session)
        if trace is None:
            return ""
        return "\n".join(
            [
                f"Название: {trace.name}",
                f"Точек: {trace.point_count:,}",
                f"Ось: {trace.start_frequency_hz:g} … {trace.stop_frequency_hz:g} {trace.axis_unit}",
                f"Шаг: {trace.frequency_step_hz:g} Hz",
                f"Единица: {trace.unit}",
                f"Детектор: {trace.detector or 'нет данных'}",
                f"Режим: {trace.trace_mode}",
                f"RBW: {trace.rbw_hz if trace.rbw_hz is not None else 'нет данных'}",
                f"VBW: {trace.vbw_hz if trace.vbw_hz is not None else 'нет данных'}",
                f"Источник: {trace.source_stream}",
            ]
        )

    # ------------------------------------------------------------------
    # Worker plumbing
    # ------------------------------------------------------------------
    def _start_worker(self, worker: TaskWorker) -> None:
        self._workers.add(worker)
        self._audit(
            "program",
            "worker_started",
            function=getattr(worker.function, "__name__", type(worker.function).__name__),
            active_workers=len(self._workers),
        )
        worker.signals.finished.connect(lambda finished_worker=worker: self._worker_finished(finished_worker))
        self.thread_pool.start(worker)

    def _worker_finished(self, worker: TaskWorker) -> None:
        self._workers.discard(worker)
        self.busy.emit(False, "")

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
        line = f"{category}: {event}"
        self._activity_lines.append(line)
        self.activity_event.emit(line)
        log_event(LOGGER, category, event, level=level, **details)

    def close(self) -> None:
        self.cancel_time_gated_power()
        self.playback_timer.stop()
        self._frame_loader.close()
        self._heatmap_controller.shutdown()
        for worker in list(self._workers):
            worker.cancel()
        for reader in self._frame_readers.values():
            reader.close()
        self._frame_readers.clear()


__all__ = ["HEATMAP_LIVE_MODES", "HOLD_TRACE_MODES", "OfflineDflPresenter"]
