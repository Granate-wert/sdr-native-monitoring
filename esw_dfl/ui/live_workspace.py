"""Live Monitor workspace: connect, control and monitor a fixed-band session.

P16UI-04 delivers the primary Pluto monitoring workflow:

* device discovery / connect entry point (dialog injected, never blocking);
* Basic controls (centre frequency, sample rate, bandwidth, gain);
* Expert controls (FFT size, hop, window, detector, backend, persistence);
* validate-before-connect with capability-aware ranges;
* requested/applied comparison table;
* backend / calibration / quality indicators;
* visualisation preset selector (rendering itself lands in P10);
* markers and quick delta measurement;
* start/stop state machine and a recording *action hook* (no recorder UI
  yet, by package contract);
* 60 Hz polling that never rebuilds the widget tree when nothing changed.

Threading contract: the workspace only touches the presenter on the GUI
thread; the presenter owns the controller worker thread.  No raw I/Q ever
reaches this widget.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..sdr.contracts import (
    ComputeBackendKind,
    DetectorType,
    DeviceConfig,
    DspConfig,
    GainMode,
    NumericRange,
    PersistenceConfig,
    PersistenceMode,
    WindowType,
)
from ..sdr.fixed_band import FixedBandOptions
from .components import FrequencyInput, StatusBadge
from .design_tokens import StatusTone
from .i18n import LocaleId, Translator
from .live_presenter import LiveMonitorPresenter
from .live_state import (
    BackendBadge,
    CalibrationBadge,
    LiveMonitorSnapshot,
    QualityFlagItem,
    RequestedAppliedValue,
)
from .units import format_frequency_hz

_FFT_SIZES = (256, 512, 1024, 2048, 4096, 8192, 16384)
_HOP_SIZES = (64, 128, 256, 512, 1024, 2048, 4096, 8192)
_BACKENDS = (
    ComputeBackendKind.AUTO,
    ComputeBackendKind.CPU,
    ComputeBackendKind.CUDA,
    ComputeBackendKind.HIP,
)

DiscoveryResult = tuple[str, str, str]  # (source_id, display_name, uri)


class LiveMonitorWorkspace(QWidget):
    """Primary Pluto fixed-band monitoring workspace.

    The presenter is injected so tests can supply a fake service; when
    ``None`` a default CPU-backed presenter is created.
    """

    def __init__(
        self,
        presenter: LiveMonitorPresenter | None = None,
        *,
        locale: LocaleId = LocaleId.RU,
        discovery: Callable[[], DiscoveryResult | None] | None = None,
        poll_interval_ms: int = 16,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tr = Translator(locale)
        self._locale = locale
        self._presenter = presenter if presenter is not None else LiveMonitorPresenter()
        self._discovery = discovery
        self._last_key: tuple[object, ...] | None = None
        self._markers: list[float] = []

        self._build_ui()
        self._wire_signals()

        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._poll_presenter)
        self._timer.start()
        self._refresh_from_snapshot(self._presenter.snapshot)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def presenter(self) -> LiveMonitorPresenter:
        return self._presenter

    @property
    def markers(self) -> tuple[float, ...]:
        return tuple(self._markers)

    def selected_backend(self) -> ComputeBackendKind:
        return ComputeBackendKind(str(self.backend_combo.currentData()))

    def selected_fft_size(self) -> int:
        return int(self.fft_combo.currentData())

    def selected_hop_size(self) -> int:
        return int(self.hop_combo.currentData())

    def selected_gain_mode(self) -> GainMode:
        return GainMode(str(self.gain_mode_combo.currentData()))

    def selected_window(self) -> WindowType:
        return WindowType(str(self.window_combo.currentData()))

    def selected_detector(self) -> DetectorType:
        return DetectorType(str(self.detector_combo.currentData()))

    def build_options(self, *, source_id: str = "pending", context_uri: str = "pending") -> FixedBandOptions:
        """Assemble the requested configuration from the current controls."""

        device = DeviceConfig(
            source_id=source_id,
            context_uri=context_uri,
            center_frequency_hz=self.center_input.frequency_hz(),
            sample_rate_hz=self.rate_input.frequency_hz(),
            analog_bandwidth_hz=self.bandwidth_input.frequency_hz(),
            gain_mode=self.selected_gain_mode(),
            manual_gain_db=self.gain_spin.value(),
            buffer_samples=4096,
        )
        dsp = DspConfig(
            fft_size=self.selected_fft_size(),
            hop_size=self.selected_hop_size(),
            window=self.selected_window(),
            detector=self.selected_detector(),
        )
        persistence = PersistenceConfig(
            enabled=self.persistence_check.isChecked(),
            mode=PersistenceMode.ROLLING_EXACT if self.persistence_check.isChecked() else PersistenceMode.DISABLED,
        )
        return FixedBandOptions(
            device=device,
            dsp=dsp,
            persistence=persistence,
            backend=self.selected_backend(),
            allow_runtime_fallback=True,
        )

    def apply_capability_ranges(self, caps: object | None) -> None:
        """Clamp expert gain range and narrow gain-mode choices to the device.

        Frequency fields keep free-form SI input; validation still happens
        before connect, so out-of-range values are rejected, never clamped
        silently.
        """

        if caps is None:
            return
        gain_range: NumericRange | None = getattr(caps, "gain_range_db", None)
        if gain_range is not None and gain_range.minimum is not None and gain_range.maximum is not None:
            self.gain_spin.setRange(float(gain_range.minimum), float(gain_range.maximum))
            step = getattr(gain_range, "step", None)
            if step:
                self.gain_spin.setSingleStep(float(step))
        modes = getattr(caps, "gain_modes", None)
        if modes:
            self.gain_mode_combo.clear()
            for mode in modes:
                self.gain_mode_combo.addItem(mode.value, mode.value)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(self._build_basic_group())
        left.addWidget(self._build_expert_group())
        left.addStretch(1)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(self._build_status_row())
        self._requested_applied_view = self._build_requested_applied_view()
        right.addWidget(self._requested_applied_view)
        self.quality_panel = self._build_quality_panel()
        right.addWidget(self.quality_panel)
        right.addWidget(self._build_visualization_row())
        right.addWidget(self._build_markers_panel())
        right.addStretch(1)
        root.addLayout(right, 2)

        self.setAccessibleName("live_monitor_workspace")

    def _build_basic_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("live.basic"), self)
        form = QFormLayout(group)

        self.device_label = QLabel(self._tr.text("live.no_device"), group)
        self.device_label.setObjectName("liveDeviceLabel")
        self.connect_button = QPushButton(self._tr.text("live.connect"), group)
        self.connect_button.setObjectName("liveConnectButton")

        device_row = QHBoxLayout()
        device_row.addWidget(self.device_label, 1)
        device_row.addWidget(self.connect_button)
        form.addRow(device_row)

        self.center_input = FrequencyInput(locale=self._locale, parent=group)
        self.center_input.setObjectName("liveCenterInput")
        self.center_input.set_frequency_hz(_DEFAULT_CENTER_HZ)
        form.addRow(self._tr.text("live.center_frequency"), self.center_input)

        self.rate_input = FrequencyInput(locale=self._locale, parent=group)
        self.rate_input.setObjectName("liveRateInput")
        self.rate_input.set_frequency_hz(_DEFAULT_RATE_HZ)
        form.addRow(self._tr.text("live.sample_rate"), self.rate_input)

        self.bandwidth_input = FrequencyInput(locale=self._locale, parent=group)
        self.bandwidth_input.setObjectName("liveBandwidthInput")
        self.bandwidth_input.set_frequency_hz(_DEFAULT_BANDWIDTH_HZ)
        form.addRow(self._tr.text("live.bandwidth"), self.bandwidth_input)

        self.gain_mode_combo = QComboBox(group)
        self.gain_mode_combo.setObjectName("liveGainModeCombo")
        for mode in _GAIN_MODES:
            self.gain_mode_combo.addItem(mode.value, mode.value)
        form.addRow(self._tr.text("live.gain_mode"), self.gain_mode_combo)

        self.gain_spin = QDoubleSpinBox(group)
        self.gain_spin.setObjectName("liveGainSpin")
        self.gain_spin.setRange(0.0, 74.5)
        self.gain_spin.setSingleStep(0.5)
        self.gain_spin.setSuffix(" dB")
        form.addRow(self._tr.text("live.gain"), self.gain_spin)

        self.apply_button = QPushButton(self._tr.text("live.apply"), group)
        self.apply_button.setObjectName("liveApplyButton")
        self.start_button = QPushButton(self._tr.text("live.start"), group)
        self.start_button.setObjectName("liveStartButton")
        self.record_button = QPushButton(self._tr.text("live.record"), group)
        self.record_button.setObjectName("liveRecordButton")
        self.record_button.setCheckable(True)

        action_row = QHBoxLayout()
        action_row.addWidget(self.apply_button)
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.record_button)
        form.addRow(action_row)

        return group

    def _build_expert_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("live.expert"), self)
        form = QFormLayout(group)

        self.fft_combo = QComboBox(group)
        self.fft_combo.setObjectName("liveFftCombo")
        for size in _FFT_SIZES:
            self.fft_combo.addItem(str(size), size)
        self.fft_combo.setCurrentIndex(_FFT_SIZES.index(1024))
        form.addRow(self._tr.text("live.fft_size"), self.fft_combo)

        self.hop_combo = QComboBox(group)
        self.hop_combo.setObjectName("liveHopCombo")
        for size in _HOP_SIZES:
            self.hop_combo.addItem(str(size), size)
        self.hop_combo.setCurrentIndex(_HOP_SIZES.index(512))
        form.addRow(self._tr.text("live.hop_size"), self.hop_combo)

        self.window_combo = QComboBox(group)
        self.window_combo.setObjectName("liveWindowCombo")
        for window in _WINDOWS:
            self.window_combo.addItem(window.value, window.value)
        form.addRow(self._tr.text("live.window"), self.window_combo)

        self.detector_combo = QComboBox(group)
        self.detector_combo.setObjectName("liveDetectorCombo")
        for detector in _DETECTORS:
            self.detector_combo.addItem(detector.value, detector.value)
        form.addRow(self._tr.text("live.detector"), self.detector_combo)

        self.backend_combo = QComboBox(group)
        self.backend_combo.setObjectName("liveBackendCombo")
        for backend in _BACKENDS:
            self.backend_combo.addItem(backend.value, backend.value)
        form.addRow(self._tr.text("live.backend"), self.backend_combo)

        self.persistence_check = QCheckBox(self._tr.text("live.persistence"), group)
        self.persistence_check.setObjectName("livePersistenceCheck")
        form.addRow(self.persistence_check)

        return group

    def _build_status_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)

        self.backend_badge = StatusBadge(self._tr.text("live.backend_not_active"), StatusTone.NEUTRAL, row)
        self.backend_badge.setObjectName("liveBackendBadge")
        self.calibration_badge = StatusBadge(self._tr.text("live.calibration.none"), StatusTone.NEUTRAL, row)
        self.calibration_badge.setObjectName("liveCalibrationBadge")
        self.health_badge = StatusBadge(self._tr.text("live.health.unknown"), StatusTone.NEUTRAL, row)
        self.health_badge.setObjectName("liveHealthBadge")

        layout.addWidget(self.backend_badge)
        layout.addWidget(self.calibration_badge)
        layout.addWidget(self.health_badge, 1)
        return row

    def _build_requested_applied_view(self) -> QTableWidget:
        view = QTableWidget(0, 5, self)
        view.setObjectName("liveRequestedAppliedView")
        view.setHorizontalHeaderLabels(
            (
                self._tr.text("live.field"),
                self._tr.text("live.requested"),
                self._tr.text("live.applied"),
                self._tr.text("live.pending"),
                self._tr.text("live.unsupported"),
            )
        )
        view.verticalHeader().setVisible(False)
        view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        view.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        view.setMaximumHeight(220)
        return view

    def _build_quality_panel(self) -> QTableWidget:
        panel = QTableWidget(0, 3, self)
        panel.setObjectName("liveQualityPanel")
        panel.setHorizontalHeaderLabels(
            (
                self._tr.text("live.quality.item"),
                self._tr.text("live.quality.value"),
                self._tr.text("live.quality.severity"),
            )
        )
        panel.verticalHeader().setVisible(False)
        panel.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        panel.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        panel.setMaximumHeight(160)
        return panel

    def _build_visualization_row(self) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)

        label = QLabel(self._tr.text("live.visual_preset"), row)
        self.preset_combo = QComboBox(row)
        self.preset_combo.setObjectName("livePresetCombo")
        self.preset_combo.addItem(self._tr.text("live.preset.spectrum"), "spectrum")
        self.preset_combo.addItem(self._tr.text("live.preset.waterfall"), "spectrum_waterfall")
        self.preset_combo.addItem(self._tr.text("live.preset.persistence"), "persistence")
        self.preset_combo.setCurrentIndex(1)

        self.frame_rate_label = QLabel("—", row)
        self.frame_rate_label.setObjectName("liveFrameRateLabel")

        layout.addWidget(label)
        layout.addWidget(self.preset_combo, 1)
        layout.addWidget(self.frame_rate_label)
        return row

    def _build_markers_panel(self) -> QWidget:
        row = QWidget(self)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.addWidget(QLabel(self._tr.text("live.markers"), row))
        header.addStretch(1)
        self.add_marker_button = QPushButton(self._tr.text("live.marker.add"), row)
        self.add_marker_button.setObjectName("liveAddMarkerButton")
        self.remove_marker_button = QPushButton(self._tr.text("live.marker.remove"), row)
        self.remove_marker_button.setObjectName("liveRemoveMarkerButton")
        header.addWidget(self.add_marker_button)
        header.addWidget(self.remove_marker_button)
        layout.addLayout(header)

        self.markers_list = QListWidget(row)
        self.markers_list.setObjectName("liveMarkersList")
        layout.addWidget(self.markers_list)

        self.marker_delta_label = QLabel("", row)
        self.marker_delta_label.setObjectName("liveMarkerDeltaLabel")
        layout.addWidget(self.marker_delta_label)
        return row

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        self.connect_button.clicked.connect(self._on_connect_clicked)
        self.apply_button.clicked.connect(self._on_apply_clicked)
        self.start_button.clicked.connect(self._on_start_stop_clicked)
        self.record_button.toggled.connect(self._on_record_toggled)
        self.add_marker_button.clicked.connect(self._on_add_marker)
        self.remove_marker_button.clicked.connect(self._on_remove_marker)
        self.fft_combo.currentIndexChanged.connect(self._sync_hop_choices)
        self.center_input.frequency_accepted.connect(self._on_center_changed)
        self._sync_hop_choices()

    def _sync_hop_choices(self) -> None:
        fft_size = self.selected_fft_size()
        model = cast(QStandardItemModel, self.hop_combo.model())
        for index in range(self.hop_combo.count()):
            data = int(self.hop_combo.itemData(index))
            disabled = data > fft_size
            item = model.item(index)
            if item is not None:
                item.setEnabled(not disabled)
        if int(self.hop_combo.currentData()) > fft_size:
            self.hop_combo.setCurrentIndex(0)

    def _on_center_changed(self, value_hz: float) -> None:
        self._add_marker_at(value_hz)

    def _on_connect_clicked(self) -> None:
        if self._presenter.connected:
            self._presenter.disconnect()
            self._refresh_from_snapshot(self._presenter.snapshot)
            return
        result = self._discovery() if self._discovery is not None else None
        if result is None:
            return
        source_id, display_name, uri = result
        options = self.build_options(source_id=source_id, context_uri=uri)
        errors = self._presenter.connect(
            source_id=source_id,
            display_name=display_name,
            uri=uri,
            options=options,
        )
        if errors:
            self._show_error("; ".join(errors))
            return
        caps = self._presenter.capabilities
        self.apply_capability_ranges(caps)
        self.connect_button.setText(self._tr.text("live.disconnect"))
        self.device_label.setText(display_name)
        self._refresh_from_snapshot(self._presenter.snapshot)

    def _on_apply_clicked(self) -> None:
        if not self._presenter.connected:
            return
        options = self.build_options()
        errors = self._presenter.apply_requested(options)
        if errors:
            self._show_error("; ".join(errors))
            return
        self._refresh_from_snapshot(self._presenter.snapshot)

    def _on_start_stop_clicked(self) -> None:
        if not self._presenter.connected:
            return
        state = self._presenter.controller_state
        running = state.value in ("starting", "running")
        if running:
            self._presenter.stop()
        else:
            try:
                self._presenter.start()
            except RuntimeError as error:
                self._show_error(str(error))
        self._refresh_from_snapshot(self._presenter.snapshot)

    def _on_record_toggled(self, active: bool) -> None:
        self._presenter.request_recording(active)

    def _on_add_marker(self) -> None:
        try:
            center_hz = self.center_input.frequency_hz()
        except ValueError:
            return  # empty or unparsable input must not crash the workspace
        self._add_marker_at(center_hz)

    def _on_remove_marker(self) -> None:
        row = self.markers_list.currentRow()
        if 0 <= row < len(self._markers):
            del self._markers[row]
            self._refresh_markers()

    def _add_marker_at(self, frequency_hz: float) -> None:
        self._markers.append(float(frequency_hz))
        if len(self._markers) > 32:
            del self._markers[0]
        self._refresh_markers()

    def _refresh_markers(self) -> None:
        self.markers_list.clear()
        for frequency_hz in self._markers:
            self.markers_list.addItem(format_frequency_hz(frequency_hz, locale=self._locale))
        if len(self._markers) >= 2:
            delta = max(self._markers) - min(self._markers)
            self.marker_delta_label.setText(
                self._tr.text("live.marker.delta", value=format_frequency_hz(delta, locale=self._locale))
            )
        else:
            self.marker_delta_label.setText("")

    def _show_error(self, message: str) -> None:
        self.health_badge.set_status(self._tr.text("live.error"), StatusTone.ERROR)
        self.health_badge.setToolTip(message)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    def _poll_presenter(self) -> None:
        if not self._presenter.connected:
            return
        self._refresh_from_snapshot(self._presenter.poll())

    def _refresh_from_snapshot(self, snapshot: LiveMonitorSnapshot) -> None:
        key = (
            snapshot.generation,
            snapshot.state.value,
            snapshot.error,
            snapshot.stale,
            snapshot.frame_rate_hz,
            snapshot.requested_applied,
            snapshot.backend,
            snapshot.calibration,
            snapshot.quality,
            snapshot.recording,
        )
        if key == self._last_key:
            return
        self._last_key = key
        self._render_snapshot(snapshot)

    def _render_snapshot(self, snapshot: LiveMonitorSnapshot) -> None:
        self._render_requested_applied(snapshot.requested_applied)
        self._render_backend_badge(snapshot.backend)
        self._render_calibration_badge(snapshot.calibration)
        self._render_quality(snapshot.quality)
        self._render_health(snapshot)
        self._render_recording(snapshot.recording.active if snapshot.recording else False)
        self._render_frame_rate(snapshot.frame_rate_hz)

        state = snapshot.state.value
        running = state in ("starting", "running")
        self.start_button.setText(self._tr.text("live.stop") if running else self._tr.text("live.start"))
        self.start_button.setEnabled(self._presenter.connected)
        self.record_button.setEnabled(running)
        self.apply_button.setEnabled(self._presenter.connected)
        if not self._presenter.connected:
            self.connect_button.setText(self._tr.text("live.connect"))
            self.device_label.setText(self._tr.text("live.no_device"))

    def _render_requested_applied(self, rows: tuple[RequestedAppliedValue, ...]) -> None:
        view = self._requested_applied_view
        view.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            self._set_item(view, row_index, 0, row.field)
            self._set_item(view, row_index, 1, row.requested)
            self._set_item(view, row_index, 2, row.applied if row.applied is not None else "—")
            self._set_item(view, row_index, 3, self._tr.text("live.yes") if row.pending else "")
            self._set_item(view, row_index, 4, self._tr.text("live.yes") if row.unsupported else "")
            tooltip = row.reason or ""
            for column in range(5):
                item = view.item(row_index, column)
                if item is not None:
                    item.setToolTip(tooltip)

    def _render_backend_badge(self, badge: BackendBadge | None) -> None:
        if badge is None:
            self.backend_badge.set_status(self._tr.text("live.backend_not_active"), StatusTone.NEUTRAL)
            return
        parts = [self._tr.text("live.backend.active", value=badge.active.value if badge.active else "—")]
        parts.append(self._tr.text("live.backend.requested", value=badge.requested.value))
        if badge.fallback_count:
            parts.append(self._tr.text("live.backend.fallbacks", count=badge.fallback_count))
        if badge.note:
            parts.append(badge.note)
        tone = StatusTone.SUCCESS if badge.active is not None else StatusTone.WARNING
        self.backend_badge.set_status(" | ".join(parts), tone)

    def _render_calibration_badge(self, badge: CalibrationBadge | None) -> None:
        if badge is None:
            self.calibration_badge.set_status(self._tr.text("live.calibration.none"), StatusTone.NEUTRAL)
            return
        calibrated = badge.status.value in ("applied", "interpolated", "extrapolated")
        if calibrated and badge.applicable:
            text = self._tr.text("live.calibration.calibrated")
            tone = StatusTone.SUCCESS
        elif calibrated:
            text = self._tr.text("live.calibration.calibrated_not_applicable")
            tone = StatusTone.WARNING
        else:
            text = self._tr.text("live.calibration.uncalibrated")
            tone = StatusTone.WARNING
        if badge.profile_id:
            text += f" ({badge.profile_id})"
        if badge.note:
            text += f" — {badge.note}"
        self.calibration_badge.set_status(text, tone)

    def _render_quality(self, items: tuple[QualityFlagItem, ...]) -> None:
        panel = self.quality_panel
        panel.setRowCount(len(items))
        tones = {"ok": StatusTone.SUCCESS, "warn": StatusTone.WARNING, "error": StatusTone.ERROR}
        for row_index, item in enumerate(items):
            self._set_item(panel, row_index, 0, item.label)
            self._set_item(panel, row_index, 1, item.value)
            self._set_item(panel, row_index, 2, item.severity)
            severity_item = panel.item(row_index, 0)
            if severity_item is not None:
                severity_item.setToolTip(tones.get(item.severity, StatusTone.NEUTRAL).value)

    def _render_health(self, snapshot: LiveMonitorSnapshot) -> None:
        if snapshot.error:
            self.health_badge.set_status(snapshot.error, StatusTone.ERROR)
            self.health_badge.setToolTip(snapshot.error)
            return
        if snapshot.stale:
            self.health_badge.set_status(self._tr.text("live.stale"), StatusTone.WARNING)
            return
        state = snapshot.state.value
        if state == "running":
            self.health_badge.set_status(self._tr.text("live.health.ok"), StatusTone.SUCCESS)
        elif state in ("starting", "stopping"):
            self.health_badge.set_status(self._tr.text("live.health.busy"), StatusTone.INFO)
        else:
            self.health_badge.set_status(self._tr.text("live.health.idle"), StatusTone.NEUTRAL)

    def _render_recording(self, active: bool) -> None:
        self.record_button.setChecked(active)

    def _render_frame_rate(self, rate_hz: float) -> None:
        if rate_hz > 0.0:
            self.frame_rate_label.setText(f"{rate_hz:,.1f} {self._tr.text('live.fft_per_second')}")
        else:
            self.frame_rate_label.setText("—")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _set_item(self, table: QTableWidget, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, column, item)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._timer.stop()
        self._presenter.close()
        super().closeEvent(event)


_GAIN_MODES = (
    GainMode.MANUAL,
    GainMode.SLOW_ATTACK,
    GainMode.FAST_ATTACK,
    GainMode.HYBRID,
)
_DEFAULT_CENTER_HZ = 2.401e9
_DEFAULT_RATE_HZ = 3.0e6
_DEFAULT_BANDWIDTH_HZ = 3.0e6
_WINDOWS = (
    WindowType.RECTANGULAR,
    WindowType.HANN,
    WindowType.BLACKMAN_HARRIS_4TERM,
    WindowType.FLAT_TOP,
    WindowType.NUTTALL,
    WindowType.KAISER,
)
_DETECTORS = (
    DetectorType.SAMPLE,
    DetectorType.PEAK,
    DetectorType.NEGATIVE_PEAK,
    DetectorType.RMS,
    DetectorType.AVERAGE_POWER,
)

__all__ = [
    "LiveMonitorWorkspace",
]
