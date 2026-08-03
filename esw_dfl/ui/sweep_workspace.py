"""Wideband Sweep workspace for planning, execution, and stitched results.

The widget is deliberately a thin GUI layer.  ``SweepPresenter`` owns all
device interaction, background execution, and stitching; this module renders
immutable snapshots and the presenter's already-stitched frame only.
"""

from __future__ import annotations

from collections.abc import Callable
import re

import numpy as np
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QCloseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..sdr.contracts import QualityFlag, SweepConfig, SweepSpectrumFrame
from ..sdr.sweep import SweepPlannerOptions
from ..sdr.sweep_profile import SweepProfile, SweepProfileStore
from .components import FrequencyInput, StatusBadge
from .design_tokens import StatusTone
from .i18n import LocaleId, Translator
from .live_workspace import _FFT_SIZES, _HOP_SIZES
from .sweep_presenter import SweepPresenter
from .sweep_state import SweepPlanSegmentSnapshot, SweepRunStatus, SweepSeamSnapshot, SweepWorkspaceSnapshot
from .units import format_frequency_hz, format_level


_DEFAULT_START_HZ = 915.0e6
_DEFAULT_STOP_HZ = 928.0e6
_DEFAULT_OVERLAP_HZ = 100.0e3
_DEFAULT_RATE_HZ = 3.0e6
_DEFAULT_BANDWIDTH_HZ = 3.0e6


class SweepSegmentDiagram(QWidget):
    """Small painter-based preview of planned coverage and overlap."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segments: tuple[SweepPlanSegmentSnapshot, ...] = ()
        self.setObjectName("sweepSegmentDiagram")
        self.setAccessibleName("sweep_segment_diagram")
        self.setMinimumHeight(92)

    def set_segments(self, segments: tuple[SweepPlanSegmentSnapshot, ...]) -> None:
        self._segments = tuple(segments)
        self.update()

    def segments(self) -> tuple[SweepPlanSegmentSnapshot, ...]:
        return self._segments

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        rect = self.rect().adjusted(10, 14, -10, -14)
        if not self._segments:
            painter.setPen(self.palette().text().color())
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "—")
            return
        start = min(item.requested_start_hz for item in self._segments)
        stop = max(item.requested_stop_hz for item in self._segments)
        width = max(stop - start, 1.0)
        row_height = max(8, rect.height() // max(1, len(self._segments)))
        for row, segment in enumerate(self._segments):
            y = rect.top() + row * row_height
            x0 = rect.left() + int((segment.requested_start_hz - start) / width * rect.width())
            x1 = rect.left() + int((segment.requested_stop_hz - start) / width * rect.width())
            bar = rect.adjusted(
                x0 - rect.left(),
                y - rect.top(),
                x1 - rect.right(),
                y + row_height - rect.bottom(),
            )
            painter.fillRect(bar, QColor("#3c8dbc"))
            painter.setPen(QPen(QColor("#1d4f73")))
            painter.drawRect(bar)
            if row:
                overlap = max(0.0, self._segments[row - 1].requested_stop_hz - segment.requested_start_hz)
                if overlap:
                    overlap_width = max(1, int(overlap / width * rect.width()))
                    painter.fillRect(bar.adjusted(0, 0, overlap_width - bar.width(), 0), QColor("#f4c542"))


class SweepSpectrumView(QWidget):
    """Render a supplied stitched grid and its explicit per-bin quality strip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frame: SweepSpectrumFrame | None = None
        self._frequencies = np.empty(0, dtype=np.float64)
        self._values = np.empty(0, dtype=np.float32)
        self._quality = np.empty(0, dtype=np.uint16)
        self.setObjectName("sweepSpectrumView")
        self.setAccessibleName("sweep_spectrum_view")
        self.setMinimumHeight(220)

    @property
    def frame(self) -> SweepSpectrumFrame | None:
        return self._frame

    def set_frame(self, frame: SweepSpectrumFrame | None) -> None:
        self._frame = frame
        if frame is None:
            self._frequencies = np.empty(0, dtype=np.float64)
            self._values = np.empty(0, dtype=np.float32)
            self._quality = np.empty(0, dtype=np.uint16)
        else:
            self._frequencies = self._readonly_copy(frame.frequencies_hz, np.float64)
            self._values = self._readonly_copy(frame.values, np.float32)
            self._quality = self._readonly_copy(frame.quality_flags_per_bin, np.uint16)
        self.update()

    @staticmethod
    def _readonly_copy(values: object, dtype: type[np.generic]) -> np.ndarray:
        copied = np.array(values, dtype=dtype, copy=True, order="C")
        copied.setflags(write=False)
        return copied

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        area = self.rect().adjusted(36, 12, -12, -30)
        strip_height = 14
        plot = area.adjusted(0, 0, 0, -(strip_height + 8))
        painter.setPen(QPen(self.palette().mid().color()))
        painter.drawRect(plot)
        if not self._frequencies.size:
            painter.setPen(self.palette().text().color())
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "—")
            return
        finite = np.isfinite(self._values)
        if np.any(finite):
            low = float(np.min(self._values[finite]))
            high = float(np.max(self._values[finite]))
            if high <= low:
                high = low + 1.0
            path = QPainterPath()
            span = max(1, self._values.size - 1)
            for index, value in enumerate(self._values):
                if not np.isfinite(value):
                    continue
                x = plot.left() + index / span * plot.width()
                y = plot.bottom() - (float(value) - low) / (high - low) * plot.height()
                point = QPointF(x, y)
                if index == 0 or not np.isfinite(self._values[index - 1]):
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            painter.setPen(QPen(QColor("#35c6ff"), 1.2))
            painter.drawPath(path)
            painter.setPen(self.palette().text().color())
            painter.drawText(4, plot.top() + 12, format_level(high, "dBFS", decimals=1))
            painter.drawText(4, plot.bottom(), format_level(low, "dBFS", decimals=1))
        strip = area.adjusted(0, area.height() - strip_height, 0, 0)
        count = max(1, self._quality.size)
        for index, flag in enumerate(self._quality):
            color = QColor("#37a169")
            if flag & np.uint16(QualityFlag.MISSING_SEGMENT):
                color = QColor("#d9534f")
            elif flag & np.uint16(QualityFlag.STITCH_OVERLAP):
                color = QColor("#f4c542")
            x0 = strip.left() + int(index / count * strip.width())
            x1 = strip.left() + int((index + 1) / count * strip.width())
            painter.fillRect(x0, strip.top(), max(1, x1 - x0), strip.height(), color)


class SweepWorkspace(QWidget):
    """Plan and execute a Wideband Sweep through an injected presenter."""

    def __init__(
        self,
        presenter: SweepPresenter | None = None,
        *,
        locale: LocaleId = LocaleId.RU,
        uri_provider: Callable[[], str | None] | None = None,
        profile_store: SweepProfileStore | None = None,
        poll_interval_ms: int = 16,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tr = Translator(locale)
        self._locale = locale
        self._presenter = presenter if presenter is not None else SweepPresenter()
        self._uri_provider = uri_provider if uri_provider is not None else lambda: "ip:fake"
        self._profile_store = profile_store if profile_store is not None else SweepProfileStore()
        self._profiles: tuple[SweepProfile, ...] = ()
        self._last_key: tuple[object, ...] | None = None
        self.setObjectName("sweepWorkspace")
        self._build_ui()
        self._wire_signals()
        self._load_profiles()
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._poll_presenter)
        self._timer.start()
        self._refresh_from_snapshot(self._presenter.snapshot)

    @property
    def presenter(self) -> SweepPresenter:
        return self._presenter

    @property
    def spectrum_view(self) -> SweepSpectrumView:
        return self._spectrum_view

    @property
    def seam_view(self) -> QTableWidget:
        return self._seam_view

    @property
    def profile_combo(self) -> QComboBox:
        return self._profile_combo

    @property
    def last_frame(self) -> SweepSpectrumFrame | None:
        return self._presenter.last_frame

    def export_frame(self) -> SweepSpectrumFrame | None:
        return self._presenter.last_frame

    def segments(self) -> tuple[SweepPlanSegmentSnapshot, ...]:
        plan = self._presenter.plan_snapshot
        return plan.segments if plan is not None else ()

    def status_text(self) -> str:
        return self._status_text(self._presenter.snapshot.run.status)

    def build_config(self) -> SweepConfig | None:
        return self._build_config()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        left.addWidget(self._build_range_group())
        left.addWidget(self._build_expert_group())
        left.addWidget(self._build_profile_group())
        left.addStretch(1)
        root.addLayout(left, 1)
        right = QVBoxLayout()
        right.addWidget(self._build_plan_group())
        right.addWidget(self._build_run_group())
        right.addWidget(self._build_result_group(), 1)
        root.addLayout(right, 2)
        self.setAccessibleName("sweep_workspace")

    def _build_range_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("sweep.range"), self)
        form = QFormLayout(group)
        self.start_input = self._frequency_input(group, "sweepStartInput", _DEFAULT_START_HZ)
        self.stop_input = self._frequency_input(group, "sweepStopInput", _DEFAULT_STOP_HZ)
        self.overlap_input = self._frequency_input(group, "sweepOverlapInput", _DEFAULT_OVERLAP_HZ)
        self.rate_input = self._frequency_input(group, "sweepRateInput", _DEFAULT_RATE_HZ)
        self.bandwidth_input = self._frequency_input(group, "sweepBandwidthInput", _DEFAULT_BANDWIDTH_HZ)
        form.addRow(self._tr.text("sweep.range.start_frequency"), self.start_input)
        form.addRow(self._tr.text("sweep.range.stop_frequency"), self.stop_input)
        form.addRow(self._tr.text("sweep.range.overlap"), self.overlap_input)
        form.addRow(self._tr.text("sweep.range.sample_rate"), self.rate_input)
        form.addRow(self._tr.text("sweep.range.analog_bandwidth"), self.bandwidth_input)
        return group

    def _build_expert_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("sweep.range.expert"), self)
        form = QFormLayout(group)
        self.fft_combo = self._size_combo(group, "sweepFftCombo", _FFT_SIZES, 1024)
        self.hop_combo = self._size_combo(group, "sweepHopCombo", _HOP_SIZES, 512)
        self.dwell_spin = QSpinBox(group)
        self.dwell_spin.setObjectName("sweepDwellSpin")
        self.dwell_spin.setRange(1, 1_000_000)
        self.dwell_spin.setValue(1)
        self.settling_spin = QDoubleSpinBox(group)
        self.settling_spin.setObjectName("sweepSettlingSpin")
        self.settling_spin.setRange(0.0, 3_600.0)
        self.settling_spin.setDecimals(3)
        self.discard_spin = QSpinBox(group)
        self.discard_spin.setObjectName("sweepDiscardSpin")
        self.discard_spin.setRange(0, 1_000_000)
        self.edge_margin_input = self._frequency_input(group, "sweepEdgeMarginInput", 0.0)
        self.dc_exclusion_input = self._frequency_input(group, "sweepDcExclusionInput", 0.0)
        form.addRow(self._tr.text("sweep.range.fft_size"), self.fft_combo)
        form.addRow(self._tr.text("sweep.range.hop_size"), self.hop_combo)
        form.addRow(self._tr.text("sweep.range.dwell_frames"), self.dwell_spin)
        form.addRow(self._tr.text("sweep.range.settling_time"), self.settling_spin)
        form.addRow(self._tr.text("sweep.range.discard_blocks"), self.discard_spin)
        form.addRow(self._tr.text("sweep.range.edge_margin"), self.edge_margin_input)
        form.addRow(self._tr.text("sweep.range.dc_exclusion"), self.dc_exclusion_input)
        return group

    def _build_profile_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("sweep.profile"), self)
        layout = QHBoxLayout(group)
        self._profile_combo = QComboBox(group)
        self._profile_combo.setObjectName("sweepProfileCombo")
        self._profile_combo.setAccessibleName("sweep_profile_combo")
        self._profile_save_button = QPushButton(self._tr.text("sweep.profile.save"), group)
        self._profile_save_button.setObjectName("sweepProfileSaveButton")
        self._profile_load_button = QPushButton(self._tr.text("sweep.profile.load"), group)
        self._profile_load_button.setObjectName("sweepProfileLoadButton")
        self._profile_delete_button = QPushButton(self._tr.text("sweep.profile.delete"), group)
        self._profile_delete_button.setObjectName("sweepProfileDeleteButton")
        layout.addWidget(self._profile_combo, 1)
        layout.addWidget(self._profile_save_button)
        layout.addWidget(self._profile_load_button)
        layout.addWidget(self._profile_delete_button)
        return group

    def _build_plan_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("sweep.plan"), self)
        layout = QVBoxLayout(group)
        header = QHBoxLayout()
        self.plan_button = QPushButton(self._tr.text("sweep.plan.button"), group)
        self.plan_button.setObjectName("sweepPlanButton")
        self.plan_button.setAccessibleName("sweep_plan_button")
        self._plan_summary_label = QLabel(self._tr.text("sweep.plan.empty"), group)
        self._plan_summary_label.setObjectName("sweepPlanSummaryLabel")
        header.addWidget(self.plan_button)
        header.addWidget(self._plan_summary_label, 1)
        layout.addLayout(header)
        self._segment_diagram = SweepSegmentDiagram(group)
        layout.addWidget(self._segment_diagram)
        return group

    def _build_run_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("sweep.run"), self)
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.run_button = QPushButton(self._tr.text("sweep.run.start"), group)
        self.run_button.setObjectName("sweepRunButton")
        self.cancel_button = QPushButton(self._tr.text("sweep.run.cancel"), group)
        self.cancel_button.setObjectName("sweepCancelButton")
        self._status_badge = StatusBadge("—", StatusTone.NEUTRAL, group)
        self._status_badge.setObjectName("sweepStatusBadge")
        row.addWidget(self.run_button)
        row.addWidget(self.cancel_button)
        row.addWidget(self._status_badge, 1)
        layout.addLayout(row)
        self._progress_label = QLabel("—", group)
        self._progress_label.setObjectName("sweepProgressLabel")
        self._eta_label = QLabel("—", group)
        self._eta_label.setObjectName("sweepEtaLabel")
        layout.addWidget(self._progress_label)
        layout.addWidget(self._eta_label)
        return group

    def _build_result_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("sweep.result"), self)
        layout = QVBoxLayout(group)
        self._result_summary_label = QLabel(self._tr.text("sweep.result.empty"), group)
        self._result_summary_label.setObjectName("sweepResultSummaryLabel")
        layout.addWidget(self._result_summary_label)
        self._spectrum_view = SweepSpectrumView(group)
        layout.addWidget(self._spectrum_view, 1)
        self._seam_view = QTableWidget(0, 4, group)
        self._seam_view.setObjectName("sweepSeamView")
        self._seam_view.setAccessibleName("sweep_seam_view")
        self._seam_view.setHorizontalHeaderLabels((
            self._tr.text("sweep.result.column_seam"),
            self._tr.text("sweep.result.column_correction"),
            self._tr.text("sweep.result.column_p95"),
            self._tr.text("sweep.result.column_samples"),
        ))
        self._seam_view.verticalHeader().setVisible(False)
        self._seam_view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._seam_view.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._seam_view.setMaximumHeight(160)
        layout.addWidget(self._seam_view)
        self._error_label = QLabel("", group)
        self._error_label.setObjectName("sweepErrorLabel")
        self._error_label.setAccessibleName("sweep_error_label")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)
        return group

    def _wire_signals(self) -> None:
        self.plan_button.clicked.connect(self._on_plan_clicked)
        self.run_button.clicked.connect(self._on_run_clicked)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self._profile_save_button.clicked.connect(self._on_profile_save)
        self._profile_load_button.clicked.connect(self._on_profile_load)
        self._profile_delete_button.clicked.connect(self._on_profile_delete)

    def _on_plan_clicked(self) -> None:
        if self._presenter.running:
            return
        config = self._build_config()
        options = self._build_planner_options()
        if config is None or options is None:
            return
        errors = self._presenter.plan(config, options)
        if errors:
            self._show_error(self._tr.text("sweep.plan.error", error="; ".join(errors)))
        self._refresh_from_snapshot(self._presenter.snapshot)

    def _on_run_clicked(self) -> None:
        if self._presenter.running:
            return
        uri = self._uri_provider()
        if not uri:
            return
        errors = self._presenter.run(uri)
        if errors:
            self._show_error(self._tr.text("sweep.run.error", error="; ".join(errors)))
        self._refresh_from_snapshot(self._presenter.snapshot)

    def _on_cancel_clicked(self) -> None:
        self._presenter.cancel()
        self._refresh_from_snapshot(self._presenter.snapshot)

    def _on_profile_save(self) -> None:
        name, accepted = QInputDialog.getText(self, self._tr.text("sweep.profile.save"), self._tr.text("sweep.profile.name"))
        name = name.strip()
        if not accepted or not name:
            if accepted:
                self._show_error(self._tr.text("sweep.profile.invalid_name"))
            return
        config = self._build_config()
        options = self._build_planner_options()
        if config is None or options is None:
            return
        profile = SweepProfile(self._profile_id(name), name, config, options)
        updated = tuple(item for item in self._profiles if item.profile_id != profile.profile_id) + (profile,)
        try:
            self._profiles = self._profile_store.save(updated).profiles
        except ValueError as error:
            self._show_error(self._tr.text("sweep.profile.save_error", error=str(error)))
            return
        self._reload_profile_combo(profile.profile_id)
        self._error_label.setText(self._tr.text("sweep.profile.saved"))

    def _on_profile_load(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        try:
            self._apply_profile(profile)
        except ValueError as error:
            self._show_error(self._tr.text("sweep.profile.load_error", error=str(error)))

    def _on_profile_delete(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        try:
            self._profiles = self._profile_store.save(item for item in self._profiles if item.profile_id != profile.profile_id).profiles
        except ValueError as error:
            self._show_error(self._tr.text("sweep.profile.save_error", error=str(error)))
            return
        self._reload_profile_combo(None)

    def _build_config(self) -> SweepConfig | None:
        try:
            return SweepConfig(
                start_frequency_hz=self.start_input.frequency_hz(),
                stop_frequency_hz=self.stop_input.frequency_hz(),
                sample_rate_hz=self.rate_input.frequency_hz(),
                analog_bandwidth_hz=self.bandwidth_input.frequency_hz(),
                overlap_hz=self.overlap_input.frequency_hz(),
                fft_size=int(self.fft_combo.currentData()),
                hop_size=int(self.hop_combo.currentData()),
                dwell_frames=self.dwell_spin.value(),
                settling_time_seconds=self.settling_spin.value(),
                discard_blocks=self.discard_spin.value(),
            )
        except ValueError:
            self._show_error(self._tr.text("sweep.plan.invalid_range"))
            return None

    def _build_planner_options(self) -> SweepPlannerOptions | None:
        try:
            return SweepPlannerOptions(
                edge_margin_hz=self.edge_margin_input.frequency_hz(),
                dc_exclusion_hz=self.dc_exclusion_input.frequency_hz(),
            )
        except ValueError:
            self._show_error(self._tr.text("sweep.plan.invalid_range"))
            return None

    def _poll_presenter(self) -> None:
        self._refresh_from_snapshot(self._presenter.poll())

    def _refresh_from_snapshot(self, snapshot: SweepWorkspaceSnapshot) -> None:
        key = (
            snapshot.generation,
            snapshot.run,
            snapshot.plan,
            snapshot.result.present,
            snapshot.result.quality.missing_bins,
            snapshot.result.quality.overlap_bins,
            snapshot.result.quality.seams,
            snapshot.error,
            snapshot.stale,
        )
        if key == self._last_key:
            return
        self._last_key = key
        self._render_snapshot(snapshot)

    def _render_snapshot(self, snapshot: SweepWorkspaceSnapshot) -> None:
        plan = snapshot.plan
        if plan is None:
            self._plan_summary_label.setText(self._tr.text("sweep.plan.empty"))
            self._segment_diagram.set_segments(())
        elif plan.error:
            self._plan_summary_label.setText(self._tr.text("sweep.plan.error", error=plan.error))
            self._segment_diagram.set_segments(())
        else:
            self._plan_summary_label.setText(" | ".join((
                self._tr.text("sweep.plan.segments", count=plan.segment_count),
                self._tr.text("sweep.plan.usable_bandwidth", bandwidth=format_frequency_hz(plan.usable_bandwidth_hz, locale=self._locale)),
                self._tr.text("sweep.plan.expected_duration", seconds=f"{plan.expected_duration_s:.2f}"),
                self._tr.text("sweep.plan.coverage_gaps", count=len(plan.coverage_gaps_hz)),
            )))
            self._segment_diagram.set_segments(plan.segments)
        self._render_run(snapshot)
        self._render_result(snapshot)
        self._error_label.setText(snapshot.error or snapshot.run.error or "")
        blocked = self._presenter.running or snapshot.run.status in (
            SweepRunStatus.PLANNING,
            SweepRunStatus.RUNNING,
            SweepRunStatus.CANCELLING,
        )
        self.plan_button.setEnabled(not blocked)
        self.run_button.setEnabled(plan is not None and not plan.error and not blocked)
        self.cancel_button.setEnabled(snapshot.run.status in (SweepRunStatus.RUNNING, SweepRunStatus.CANCELLING))

    def _render_run(self, snapshot: SweepWorkspaceSnapshot) -> None:
        run = snapshot.run
        self._status_badge.set_status(self._status_text(run.status), self._status_tone(run.status))
        current = (run.current_segment_index + 1) if run.current_segment_index is not None else run.completed_segments
        self._progress_label.setText(self._tr.text("sweep.run.progress", current=current, total=run.total_segments))
        parts = [self._tr.text("sweep.run.elapsed", elapsed=self._duration(run.elapsed_s))]
        if run.eta_s is not None:
            parts.append(self._tr.text("sweep.run.eta", eta=self._duration(run.eta_s)))
        if run.stage:
            parts.append(self._stage_text(run.stage))
        self._eta_label.setText(" | ".join(parts))

    def _render_result(self, snapshot: SweepWorkspaceSnapshot) -> None:
        result = snapshot.result
        if not result.present:
            self._result_summary_label.setText(self._tr.text("sweep.result.empty"))
            self._seam_view.setRowCount(0)
            return
        quality = result.quality
        self._result_summary_label.setText(" | ".join((
            self._tr.text("sweep.result.missing_bins", count=quality.missing_bins),
            self._tr.text("sweep.result.overlap_bins", count=quality.overlap_bins),
            self._tr.text("sweep.result.unit", unit=quality.unit),
            self._tr.text("sweep.result.calibration", status=quality.calibration_status),
        )))
        self._render_seams(quality.seams)
        if snapshot.run.status in (SweepRunStatus.COMPLETED, SweepRunStatus.FAILED, SweepRunStatus.CANCELLED):
            # A failed/cancelled execution may still contain a partial stitched frame.
            # Render it so missing bins and quality flags remain visible.
            self._spectrum_view.set_frame(self._presenter.last_frame)

    def _render_seams(self, seams: tuple[SweepSeamSnapshot, ...]) -> None:
        self._seam_view.setRowCount(len(seams))
        for row, seam in enumerate(seams):
            self._set_item(self._seam_view, row, 0, self._tr.text(
                "sweep.result.seam", left=seam.left_segment_index, right=seam.right_segment_index, correction=seam.correction_db,
            ))
            self._set_item(self._seam_view, row, 1, f"{seam.correction_db:+.2f}")
            self._set_item(self._seam_view, row, 2, self._tr.text(
                "sweep.result.seam_detail", before=seam.before_p95_db, after=seam.after_p95_db, count=seam.sample_count,
            ))
            self._set_item(self._seam_view, row, 3, str(seam.sample_count))

    def _load_profiles(self) -> None:
        try:
            self._profiles = self._profile_store.load().profiles
        except ValueError as error:
            self._profiles = ()
            self._show_error(self._tr.text("sweep.profile.load_error", error=str(error)))
        self._reload_profile_combo(None)

    def _reload_profile_combo(self, selected_id: str | None) -> None:
        self._profile_combo.clear()
        self._profile_combo.addItem(self._tr.text("sweep.profile.default"), None)
        for profile in self._profiles:
            self._profile_combo.addItem(profile.display_name, profile.profile_id)
        if selected_id:
            index = self._profile_combo.findData(selected_id)
            self._profile_combo.setCurrentIndex(max(0, index))

    def _selected_profile(self) -> SweepProfile | None:
        profile_id = self._profile_combo.currentData()
        return next((item for item in self._profiles if item.profile_id == profile_id), None)

    def _apply_profile(self, profile: SweepProfile) -> None:
        config = profile.config
        self.start_input.set_frequency_hz(config.start_frequency_hz)
        self.stop_input.set_frequency_hz(config.stop_frequency_hz)
        self.overlap_input.set_frequency_hz(config.overlap_hz)
        self.rate_input.set_frequency_hz(config.sample_rate_hz)
        self.bandwidth_input.set_frequency_hz(config.analog_bandwidth_hz)
        self.fft_combo.setCurrentIndex(self.fft_combo.findData(config.fft_size))
        self.hop_combo.setCurrentIndex(self.hop_combo.findData(config.hop_size))
        self.dwell_spin.setValue(config.dwell_frames)
        self.settling_spin.setValue(config.settling_time_seconds)
        self.discard_spin.setValue(config.discard_blocks)
        self.edge_margin_input.set_frequency_hz(profile.options.edge_margin_hz)
        self.dc_exclusion_input.set_frequency_hz(profile.options.dc_exclusion_hz)

    def _show_error(self, message: str) -> None:
        self._error_label.setText(message)

    def _status_text(self, status: SweepRunStatus) -> str:
        return {
            SweepRunStatus.RUNNING: self._tr.text("sweep.run.running"),
            SweepRunStatus.CANCELLING: self._tr.text("sweep.run.cancelling"),
            SweepRunStatus.CANCELLED: self._tr.text("sweep.run.cancelled"),
            SweepRunStatus.COMPLETED: self._tr.text("sweep.run.completed"),
            SweepRunStatus.FAILED: self._tr.text("sweep.run.failed"),
        }.get(status, self._tr.text("sweep.run"))

    def _stage_text(self, stage: str) -> str:
        return self._tr.text(f"sweep.run.stage.{stage}") if stage in {"segment_start", "segment_complete", "finished"} else stage

    @staticmethod
    def _status_tone(status: SweepRunStatus) -> StatusTone:
        if status is SweepRunStatus.COMPLETED:
            return StatusTone.SUCCESS
        if status is SweepRunStatus.FAILED:
            return StatusTone.ERROR
        if status in (SweepRunStatus.RUNNING, SweepRunStatus.CANCELLING, SweepRunStatus.PLANNING):
            return StatusTone.INFO
        if status is SweepRunStatus.CANCELLED:
            return StatusTone.WARNING
        return StatusTone.NEUTRAL

    @staticmethod
    def _duration(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        return f"{total // 60}:{total % 60:02d}"

    def _frequency_input(self, parent: QWidget, name: str, value_hz: float) -> FrequencyInput:
        field = FrequencyInput(locale=self._locale, parent=parent)
        field.setObjectName(name)
        field.set_frequency_hz(value_hz)
        return field

    @staticmethod
    def _size_combo(parent: QWidget, name: str, values: tuple[int, ...], default: int) -> QComboBox:
        combo = QComboBox(parent)
        combo.setObjectName(name)
        for value in values:
            combo.addItem(str(value), value)
        combo.setCurrentIndex(values.index(default))
        return combo

    @staticmethod
    def _profile_id(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
        return slug or "sweep-profile"

    @staticmethod
    def _set_item(table: QTableWidget, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row, column, item)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._timer.stop()
        self._presenter.close()
        super().closeEvent(event)


__all__ = ["SweepSegmentDiagram", "SweepSpectrumView", "SweepWorkspace"]
