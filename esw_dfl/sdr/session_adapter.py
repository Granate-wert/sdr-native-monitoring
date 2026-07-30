"""Convert bounded native live publications into existing DFL domain models."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from ..domain import MeasurementMetadata, MeasurementSession, SpectrumTrace, SourceDescriptor, WaterfallData
from .contracts import QualityFlag, SpectrumFrame, SourceType, SweepSpectrumFrame
from .controller import LiveControllerState, LiveControllerUpdate


def _readonly_copy(value: object, dtype: np.dtype[Any]) -> np.ndarray:
    result = np.array(value, dtype=dtype, copy=True, order="C")
    result.setflags(write=False)
    return result


def sweep_trace_from_frame(
    frame: SweepSpectrumFrame,
    *,
    source_stream: str = "sdr:sweep",
) -> SpectrumTrace:
    """Convert one immutable P13 full-span frame into the common trace model.

    The renderer receives the stitched frequency/value arrays directly.  Quality
    and seam evidence remain attached as metadata so the GUI can expose gaps,
    overlap coverage, calibration, and correction diagnostics without re-reading
    segment data.
    """

    if not isinstance(frame, SweepSpectrumFrame):
        raise TypeError("frame must be SweepSpectrumFrame")
    frequencies = _readonly_copy(frame.frequencies_hz, np.dtype(np.float64))
    values = _readonly_copy(frame.values, np.dtype(np.float32))
    flags = _readonly_copy(frame.quality_flags_per_bin, np.dtype(np.uint16))
    missing_bins = int(np.count_nonzero(flags & np.uint16(QualityFlag.MISSING_SEGMENT)))
    overlap_bins = int(np.count_nonzero(flags & np.uint16(QualityFlag.STITCH_OVERLAP)))
    edge_bins = int(np.count_nonzero(flags & np.uint16(QualityFlag.EDGE_BIN)))
    return SpectrumTrace(
        trace_id=f"sweep:{frame.sweep_id}",
        name="Stitched Full-span Sweep",
        start_frequency_hz=float(frequencies[0]) if frequencies.size else 0.0,
        stop_frequency_hz=float(frequencies[-1]) if frequencies.size else 0.0,
        frequency_step_hz=(
            float(np.median(np.diff(frequencies))) if frequencies.size > 1 else 0.0
        ),
        power_values=values,
        frequency_values=frequencies,
        axis_unit="Hz",
        unit=frame.unit.value,
        timestamp=float(frame.completed_ns) / 1.0e9,
        rbw_hz=float(frame.nominal_rbw_hz),
        detector="Sweep",
        trace_mode="P13 Stitched",
        source_stream=source_stream,
        color="#ffd24a",
        metadata={
            "sweep_id": int(frame.sweep_id),
            "config_generation": int(frame.config_generation),
            "requested_start_hz": float(frame.requested_start_hz),
            "requested_stop_hz": float(frame.requested_stop_hz),
            "actual_start_hz": float(frame.actual_start_hz),
            "actual_stop_hz": float(frame.actual_stop_hz),
            "calibration_status": frame.calibration_status.value,
            "calibration_profile_id": frame.calibration_profile_id,
            "missing_bins": missing_bins,
            "overlap_bins": overlap_bins,
            "edge_bins": edge_bins,
            "segment_count": len(frame.segments),
            "seam_count": len(frame.seam_metrics),
            "seams": tuple(
                {
                    "left_segment_index": int(item.left_segment_index),
                    "right_segment_index": int(item.right_segment_index),
                    "correction_db": float(item.correction_db),
                    "before_p95_db": float(item.before_p95_db),
                    "after_p95_db": float(item.after_p95_db),
                }
                for item in frame.seam_metrics
            ),
            "quality_flags_per_bin": flags,
            "uncertainty_db_per_bin": _readonly_copy(
                frame.uncertainty_db_per_bin, np.dtype(np.float32)
            ),
        },
    )

@dataclass(frozen=True, slots=True)
class LiveRenderState:
    """Immutable renderer input for one accepted controller publication."""

    generation: int
    state: LiveControllerState
    trace: SpectrumTrace | None
    waterfall: WaterfallData | None
    persistence_snapshot: Any | None
    metrics: Any | None
    events: tuple[Any, ...]
    error: str | None = None
    ignored_as_stale: bool = False


class LiveSessionAdapter:
    """Own live-session identity and bounded waterfall state.

    The adapter stores only normalized spectrum rows, never I/Q.  The history
    is a fixed-size deque, and every outgoing NumPy array is a private,
    read-only copy so renderer code cannot mutate controller/native state.
    """

    def __init__(
        self,
        *,
        source_id: str,
        display_name: str,
        uri: str,
        max_waterfall_rows: int = 512,
        session_id: str | None = None,
    ) -> None:
        if not source_id.strip() or not display_name.strip() or not uri.strip():
            raise ValueError("live source identity must not be empty")
        if max_waterfall_rows <= 0:
            raise ValueError("max_waterfall_rows must be positive")
        self.source_id = source_id
        self.display_name = display_name
        self.uri = uri
        self.max_waterfall_rows = int(max_waterfall_rows)
        self.session_id = session_id or f"live-{uuid4()}"
        self._accepted_generation: int | None = None
        self._frame_count = 0
        self._rows: deque[np.ndarray] = deque(maxlen=self.max_waterfall_rows)
        self._timestamps: deque[float] = deque(maxlen=self.max_waterfall_rows)
        self._line_indices: deque[int] = deque(maxlen=self.max_waterfall_rows)
        self._last_trace: SpectrumTrace | None = None
        self._last_waterfall: WaterfallData | None = None
        self._last_persistence: Any | None = None
        self._last_frame: SpectrumFrame | None = None

    def create_session(self) -> MeasurementSession:
        descriptor = SourceDescriptor(
            source_type=SourceType.LIVE_IQ,
            source_id=self.source_id,
            display_name=self.display_name,
            uri=self.uri,
            metadata={"transport": "fixed_band", "session_kind": "live"},
            backend_id="pluto-libiio",
        )
        metadata = MeasurementMetadata(
            device_type="PlutoSDR / AD936x",
            system="Live IQ",
            modes=["Live Spectrum", "Live Waterfall", "Live Persistence"],
            streams=[self.source_id],
            settings={"source": {"uri": self.uri, "source_id": self.source_id}},
        )
        session = MeasurementSession(
            session_id=self.session_id,
            source_path=Path(f"<live:{self.source_id}>"),
            name=self.display_name,
            metadata=metadata,
            source_descriptor=descriptor,
        )
        session.display_state["live"] = True
        return session

    @property
    def accepted_generation(self) -> int | None:
        return self._accepted_generation

    @property
    def latest_frame(self) -> SpectrumFrame | None:
        """The most recently accepted native frame, never a mixed snapshot."""
        return self._last_frame

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def apply(
        self,
        session: MeasurementSession,
        update: LiveControllerUpdate,
    ) -> LiveRenderState:
        if session.session_id != self.session_id:
            raise ValueError("update belongs to another live session")
        if self._accepted_generation is None:
            self._accepted_generation = update.generation
        if update.generation != self._accepted_generation:
            return LiveRenderState(
                generation=update.generation,
                state=update.state,
                trace=None,
                waterfall=None,
                persistence_snapshot=None,
                metrics=None,
                events=(),
                error=update.error,
                ignored_as_stale=True,
            )
        trace = self._last_trace
        waterfall = self._last_waterfall
        if update.spectrum_frames:
            for current_frame in update.spectrum_frames:
                self._last_frame = current_frame
                self._append_row(current_frame)
            frame = update.spectrum_frames[-1]
            trace = self._trace_from_frame(frame)
            self._last_trace = trace
            waterfall = self._waterfall_from_rows(frame)
            self._last_waterfall = waterfall
            session.traces[trace.trace_id] = trace
            session.waterfalls[waterfall.waterfall_id] = waterfall
            session.active_trace_id = trace.trace_id
            session.active_waterfall_id = waterfall.waterfall_id
            session.current_frame = max(0, waterfall.line_count - 1)
        if update.persistence_snapshots:
            self._last_persistence = update.persistence_snapshots[-1]
        session.metadata.settings.setdefault("live", {})["controller_state"] = update.state.value
        if update.metrics is not None:
            session.metadata.settings["live"]["metrics"] = _metrics_dict(update.metrics)
        if update.applied_config is not None:
            session.metadata.settings["live"]["applied_config"] = _applied_dict(update.applied_config)
        if update.error:
            if update.error not in session.metadata.warnings[-32:]:
                session.metadata.warnings.append(update.error)
                del session.metadata.warnings[:-32]
        return LiveRenderState(
            generation=update.generation,
            state=update.state,
            trace=trace,
            waterfall=waterfall,
            persistence_snapshot=self._last_persistence,
            metrics=update.metrics,
            events=update.events,
            error=update.error,
        )

    def _trace_from_frame(self, frame: SpectrumFrame) -> SpectrumTrace:
        frequencies = _readonly_copy(frame.frequencies_hz, np.dtype(np.float64))
        values = _readonly_copy(frame.values, np.dtype(np.float32))
        step = float(frame.fft_bin_width_hz)
        if frequencies.size > 1:
            step = float(np.median(np.diff(frequencies)))
        unit = frame.unit.value
        return SpectrumTrace(
            trace_id=f"{self.source_id}:spectrum",
            name="Live Spectrum",
            start_frequency_hz=float(frequencies[0]) if frequencies.size else 0.0,
            stop_frequency_hz=float(frequencies[-1]) if frequencies.size else 0.0,
            frequency_step_hz=step,
            power_values=values,
            frequency_values=frequencies,
            axis_unit="Hz",
            unit=unit,
            timestamp=float(frame.timestamp_ns) / 1.0e9,
            rbw_hz=float(frame.nominal_rbw_hz),
            detector=frame.detector.value,
            trace_mode="Live",
            source_stream=frame.source.source_id,
            color="#35c6ff",
            metadata={
                "source_type": frame.source.source_type.value,
                "source_id": frame.source.source_id,
                "frame_sequence": int(frame.frame_sequence),
                "first_sample_index": int(frame.first_sample_index),
                "config_generation": int(frame.config_generation),
                "center_frequency_hz": float(frame.center_frequency_hz),
                "sample_rate_hz": float(frame.sample_rate_hz),
                "analog_bandwidth_hz": float(frame.analog_bandwidth_hz),
                "fft_bin_width_hz": float(frame.fft_bin_width_hz),
                "enbw_hz": float(frame.enbw_hz),
                "calibration_status": frame.calibration_status.value,
                "calibration_profile_id": frame.calibration_profile_id,
                "estimated_uncertainty_db": float(frame.estimated_uncertainty_db),
                "dropped_samples_before": int(frame.dropped_samples_before),
                "dropped_iq_blocks_before": int(frame.dropped_iq_blocks_before),
                "dropped_fft_frames_before": int(frame.dropped_fft_frames_before),
                "quality_flags": int(frame.quality_flags),
            },
        )

    def _append_row(self, frame: SpectrumFrame) -> None:
        self._rows.append(_readonly_copy(frame.values, np.dtype(np.float32)))
        self._timestamps.append(float(frame.timestamp_ns) / 1.0e9)
        self._line_indices.append(self._frame_count)
        self._frame_count += 1

    def _waterfall_from_rows(self, frame: SpectrumFrame) -> WaterfallData:
        values = _readonly_copy(np.stack(tuple(self._rows)), np.dtype(np.float32))
        timestamps = _readonly_copy(tuple(self._timestamps), np.dtype(np.float64))
        indices = _readonly_copy(tuple(self._line_indices), np.dtype(np.int64))
        frequencies = np.asarray(frame.frequencies_hz, dtype=np.float64)
        step = float(frame.fft_bin_width_hz)
        if frequencies.size > 1:
            step = float(np.median(np.diff(frequencies)))
        waterfall = WaterfallData(
            waterfall_id=f"{self.source_id}:waterfall",
            name="Live Waterfall",
            line_count=max(self._frame_count, values.shape[0]),
            point_count=int(values.shape[1]) if values.ndim == 2 else 0,
            start_frequency_hz=float(frequencies[0]) if frequencies.size else 0.0,
            stop_frequency_hz=float(frequencies[-1]) if frequencies.size else 0.0,
            frequency_step_hz=step,
            source_stream=frame.source.source_id,
            unit=frame.unit.value,
            metadata={
                "live": True,
                "bounded_history_rows": self.max_waterfall_rows,
                "latest_frame_sequence": int(frame.frame_sequence),
                "calibration_status": frame.calibration_status.value,
                "persistence_source": "native_analytics",
            },
        )
        waterfall.set_preview(values, timestamps, indices)
        return waterfall


def _metrics_dict(value: Any) -> dict[str, object]:
    engine = getattr(value, "engine", None)
    fields = (
        "iq_samples_received", "iq_samples_dropped", "iq_blocks_received",
        "iq_blocks_dropped", "fft_frames_computed", "fft_frames_dropped",
        "analytical_fft_rate", "spectrum_snapshots_emitted", "persistence_updates",
        "cpu_processing_ms", "gpu_processing_ms", "h2d_ms", "d2h_ms",
        "end_to_end_latency_ms",
    )
    result = {"state": getattr(getattr(value, "state", None), "value", "unknown")}
    if engine is not None:
        result["engine"] = {name: getattr(engine, name) for name in fields if hasattr(engine, name)}
    for name in ("active_backend", "requested_backend", "backend_fallback_count", "spectrum_snapshots_superseded"):
        if hasattr(value, name):
            item = getattr(value, name)
            result[name] = getattr(item, "value", item)
    return result


def _applied_dict(value: Any) -> dict[str, object]:
    names = (
        "center_frequency_hz", "sample_rate_hz", "analog_bandwidth_hz",
        "manual_gain_db", "config_generation",
    )
    return {name: getattr(value, name) for name in names if hasattr(value, name)}


__all__ = ["LiveRenderState", "LiveSessionAdapter", "sweep_trace_from_frame"]
