"""Recording & Replay workspace for bounded I/Q and spectrum capture.

The widget is a thin GUI layer: :class:`RecordingPresenter` owns the recording
service, workers, replay iterators and cancellation.  This module renders
immutable :class:`RecordingWorkspaceSnapshot` values poll-driven by a QTimer and
forwards user intents to the presenter.  Full recording paths never leave the
presenter; the UI shows basenames only.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..sdr.contracts import ComputeBackendKind
from .i18n import LocaleId, Translator
from .recording_presenter import RecordingPresenter
from .recording_state import (
    RecordingRunState,
    RecordingWorkspaceSnapshot,
    ReplayRunState,
    ReplaySourceKind,
)


class RecordingWorkspace(QWidget):
    """Recording setup/health and replay controls for the AppShell."""

    def __init__(
        self,
        *,
        presenter: RecordingPresenter | None = None,
        locale: LocaleId = LocaleId.RU,
        poll_interval_ms: int = 16,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("p16RecordingWorkspace")
        self._locale = locale
        self._tr = Translator(locale)
        self._presenter = presenter if presenter is not None else RecordingPresenter()
        self._last_key: tuple[object, ...] | None = None
        self._last_snapshot: RecordingWorkspaceSnapshot | None = None

        root = QVBoxLayout(self)
        self._status = QLabel(self._tr.text("recording.status.idle"), self)
        self._status.setObjectName("recordingStatus")
        root.addWidget(self._status)

        root.addWidget(self._build_setup_group())
        root.addWidget(self._build_health_group())
        root.addWidget(self._build_replay_group())
        root.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._poll_presenter)
        self._timer.start()

    # -- construction -------------------------------------------------------

    def _build_setup_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("recording.setup.title"), self)
        form = QFormLayout(group)

        self._iq_check = QCheckBox(self._tr.text("recording.setup.iq"), group)
        self._iq_check.setChecked(True)
        self._spectrum_check = QCheckBox(self._tr.text("recording.setup.spectrum"), group)
        form.addRow(self._iq_check)
        form.addRow(self._spectrum_check)

        target_row = QHBoxLayout()
        self._target_edit = QLineEdit(group)
        self._target_edit.setObjectName("recordingTarget")
        self._target_edit.setPlaceholderText(self._tr.text("recording.setup.target"))
        browse = QPushButton(self._tr.text("recording.setup.browse"), group)
        browse.clicked.connect(self._on_browse)
        target_row.addWidget(self._target_edit, 1)
        target_row.addWidget(browse)
        form.addRow(self._tr.text("recording.setup.target_label"), target_row)

        self._duration_spin = QDoubleSpinBox(group)
        self._duration_spin.setRange(1.0, 3600.0)
        self._duration_spin.setValue(30.0)
        self._duration_spin.setSuffix(" s")
        form.addRow(self._tr.text("recording.setup.duration"), self._duration_spin)

        self._queue_spin = QSpinBox(group)
        self._queue_spin.setRange(1, 256)
        self._queue_spin.setValue(8)
        form.addRow(self._tr.text("recording.setup.queue"), self._queue_spin)

        self._forecast_label = QLabel("—", group)
        self._forecast_label.setObjectName("recordingForecast")
        self._forecast_label.setWordWrap(True)
        form.addRow(self._tr.text("recording.setup.forecast"), self._forecast_label)

        buttons = QHBoxLayout()
        self._configure_btn = QPushButton(self._tr.text("recording.setup.estimate"), group)
        self._configure_btn.setObjectName("recordingEstimate")
        self._configure_btn.clicked.connect(self._on_configure)
        self._start_btn = QPushButton(self._tr.text("recording.action.start"), group)
        self._start_btn.setObjectName("recordingStart")
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn = QPushButton(self._tr.text("recording.action.stop"), group)
        self._stop_btn.setObjectName("recordingStop")
        self._stop_btn.clicked.connect(self._presenter.stop_recording)
        self._stop_btn.setEnabled(False)
        buttons.addWidget(self._configure_btn)
        buttons.addWidget(self._start_btn)
        buttons.addWidget(self._stop_btn)
        buttons.addStretch(1)
        form.addRow(buttons)
        return group

    def _build_health_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("recording.health.title"), self)
        form = QFormLayout(group)
        self._health_labels: dict[str, QLabel] = {}
        for key in (
            "enqueued",
            "written_iq",
            "written_spectrum",
            "dropped",
            "gaps",
            "queue",
            "elapsed",
        ):
            label = QLabel("—", group)
            label.setObjectName(f"recordingHealth.{key}")
            self._health_labels[key] = label
            form.addRow(self._tr.text(f"recording.health.{key}"), label)
        return group

    def _build_replay_group(self) -> QGroupBox:
        group = QGroupBox(self._tr.text("recording.replay.title"), self)
        form = QFormLayout(group)

        source_row = QHBoxLayout()
        self._replay_kind = QComboBox(group)
        self._replay_kind.setObjectName("replayKind")
        self._replay_kind.addItem(self._tr.text("recording.replay.kind_iq"), ReplaySourceKind.IQ)
        self._replay_kind.addItem(
            self._tr.text("recording.replay.kind_spectrum"), ReplaySourceKind.SPECTRUM
        )
        open_btn = QPushButton(self._tr.text("recording.replay.open"), group)
        open_btn.setObjectName("replayOpen")
        open_btn.clicked.connect(self._on_open_replay)
        source_row.addWidget(self._replay_kind)
        source_row.addWidget(open_btn)
        source_row.addStretch(1)
        form.addRow(self._tr.text("recording.replay.source"), source_row)

        self._replay_info = QLabel("—", group)
        self._replay_info.setObjectName("replayInfo")
        self._replay_info.setWordWrap(True)
        form.addRow(self._tr.text("recording.replay.info"), self._replay_info)

        self._position_label = QLabel("0 / 0", group)
        self._position_label.setObjectName("replayPosition")
        form.addRow(self._tr.text("recording.replay.position"), self._position_label)

        self._seek = QSlider(Qt.Orientation.Horizontal, group)
        self._seek.setObjectName("replaySeek")
        self._seek.setRange(0, 100)
        self._seek.sliderReleased.connect(self._on_seek)
        form.addRow(self._tr.text("recording.replay.seek"), self._seek)

        controls = QHBoxLayout()
        self._play_btn = QPushButton(self._tr.text("recording.replay.play"), group)
        self._play_btn.setObjectName("replayPlay")
        self._play_btn.clicked.connect(self._presenter.play)
        self._pause_btn = QPushButton(self._tr.text("recording.replay.pause"), group)
        self._pause_btn.setObjectName("replayPause")
        self._pause_btn.clicked.connect(self._presenter.pause)
        self._replay_stop_btn = QPushButton(self._tr.text("recording.replay.stop"), group)
        self._replay_stop_btn.setObjectName("replayStop")
        self._replay_stop_btn.clicked.connect(self._presenter.stop_replay)
        for button in (self._play_btn, self._pause_btn, self._replay_stop_btn):
            controls.addWidget(button)
        controls.addStretch(1)
        form.addRow(controls)

        self._backend_combo = QComboBox(group)
        self._backend_combo.setObjectName("replayBackend")
        self._backend_combo.addItem("CPU", ComputeBackendKind.CPU)
        self._backend_combo.addItem("CUDA", ComputeBackendKind.CUDA)
        reprocess_btn = QPushButton(self._tr.text("recording.replay.reprocess"), group)
        reprocess_btn.setObjectName("replayReprocess")
        reprocess_btn.clicked.connect(self._on_reprocess)
        backend_row = QHBoxLayout()
        backend_row.addWidget(self._backend_combo)
        backend_row.addWidget(reprocess_btn)
        backend_row.addStretch(1)
        form.addRow(self._tr.text("recording.replay.backend"), backend_row)
        return group

    # -- actions ------------------------------------------------------------

    def _current_target(self) -> Path:
        text = self._target_edit.text().strip()
        return Path(text) if text else Path("recording")

    def _on_browse(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(
            self, self._tr.text("recording.setup.browse"), "", "Recording (*.sigmf-meta)"
        )
        if chosen:
            self._target_edit.setText(chosen)

    def _on_configure(self) -> None:
        errors = self._presenter.configure(
            output_uri=self._current_target(),
            record_iq=self._iq_check.isChecked(),
            record_spectrum=self._spectrum_check.isChecked(),
            duration_s=float(self._duration_spin.value()),
            queue_capacity=int(self._queue_spin.value()),
        )
        if errors and not self._presenter.poll().setup:
            self._show_errors(errors)

    def _on_start(self) -> None:
        snapshot = self._presenter.poll()
        if snapshot.confirmation_required:
            answer = QMessageBox.question(
                self,
                self._tr.text("recording.action.start"),
                self._tr.text("recording.confirm.insufficient"),
            )
            if answer is not QMessageBox.StandardButton.Yes:
                return
        errors = self._presenter.start_recording()
        if errors:
            self._show_errors(errors)

    def _on_open_replay(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, self._tr.text("recording.replay.open"), "", "Recording (*)"
        )
        if not chosen:
            return
        kind = self._replay_kind.currentData()
        base = chosen
        for suffix in (".sigmf-meta", ".spectrum.json"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        errors = self._presenter.open_replay(base, kind=kind)
        if errors:
            self._show_errors(errors)

    def _on_seek(self) -> None:
        self._presenter.seek_fraction(self._seek.value() / 100.0)

    def _on_reprocess(self) -> None:
        backend = self._backend_combo.currentData()
        errors = self._presenter.reprocess_iq(self._current_target(), backend=backend)
        if errors:
            self._show_errors(errors)

    def _show_errors(self, errors: list[str]) -> None:
        QMessageBox.warning(self, self._tr.text("recording.title"), "\n".join(errors))

    # -- polling ------------------------------------------------------------

    def _poll_presenter(self) -> None:
        self._refresh_from_snapshot(self._presenter.poll())

    def _refresh_from_snapshot(self, snapshot: RecordingWorkspaceSnapshot) -> None:
        key = (
            snapshot.generation,
            snapshot.recording_state,
            snapshot.replay_state,
            snapshot.health.queue_depth if snapshot.health else None,
            snapshot.health.dropped_items if snapshot.health else None,
            snapshot.replay.position_label if snapshot.replay else None,
            snapshot.error,
        )
        if key == self._last_key:
            return
        self._last_key = key
        self._last_snapshot = snapshot
        self._render_snapshot(snapshot)

    def _render_snapshot(self, snapshot: RecordingWorkspaceSnapshot) -> None:
        state_text = {
            RecordingRunState.IDLE: "recording.status.idle",
            RecordingRunState.CONFIGURED: "recording.status.configured",
            RecordingRunState.RUNNING: "recording.status.running",
            RecordingRunState.STOPPING: "recording.status.stopping",
            RecordingRunState.COMPLETED: "recording.status.completed",
            RecordingRunState.FAILED: "recording.status.failed",
        }[snapshot.recording_state]
        self._status.setText(self._tr.text(state_text))

        running = snapshot.recording_state is RecordingRunState.RUNNING
        self._start_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)

        if snapshot.setup is not None:
            setup = snapshot.setup
            suff = setup.sufficient or "unknown"
            self._forecast_label.setText(
                self._tr.text(
                    "recording.setup.forecast_value",
                    estimated=setup.estimated_bytes or "—",
                    free=setup.free_bytes or "—",
                    sufficient=self._tr.text(f"recording.setup.sufficient.{suff}"),
                )
            )

        if snapshot.health is not None:
            health = snapshot.health
            self._health_labels["enqueued"].setText(health.enqueued)
            self._health_labels["written_iq"].setText(health.written_iq_samples)
            self._health_labels["written_spectrum"].setText(health.written_spectrum_frames)
            self._health_labels["dropped"].setText(health.dropped_items)
            self._health_labels["gaps"].setText(health.gap_count)
            self._health_labels["queue"].setText(
                f"{health.queue_depth} / {health.queue_capacity}"
            )
            self._health_labels["elapsed"].setText(health.elapsed_s)

        if snapshot.replay is not None:
            replay = snapshot.replay
            count_key = (
                "recording.replay.samples"
                if replay.kind is ReplaySourceKind.IQ
                else "recording.replay.frames"
            )
            count_value = replay.sample_count if replay.kind is ReplaySourceKind.IQ else replay.frame_count
            self._replay_info.setText(
                self._tr.text(
                    "recording.replay.info_value",
                    name=replay.name,
                    count=self._tr.text(count_key, value=count_value),
                    gaps=replay.gap_count,
                    calibrated=self._tr.text(
                        "recording.replay.calibrated."
                        + ("yes" if replay.calibrated else "no")
                    ),
                )
            )
            self._position_label.setText(replay.position_label)
            playing = snapshot.replay_state is ReplayRunState.PLAYING
            self._play_btn.setEnabled(not playing)
            self._pause_btn.setEnabled(playing)

        if snapshot.error:
            self._status.setText(
                f"{self._status.text()} — {snapshot.error}"
            )

    # -- teardown -----------------------------------------------------------

    def request_shutdown(self) -> None:
        self._timer.stop()
        self._presenter.close()
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._timer.stop()
        self._presenter.close()
        super().closeEvent(event)


__all__ = ["RecordingWorkspace"]
