from __future__ import annotations

from pathlib import Path
from uuid import uuid5, NAMESPACE_URL

import numpy as np

from .domain import MeasurementMetadata, MeasurementSession, SpectrumTrace, WaterfallData
from .models import AcquisitionMode, DflDocument, SpectrogramPreview, TraceMode
from .power_measurements import SpectrumFrame
from .time_gated_power import PowerSemantics


def _number(settings: dict[str, object], *names: str) -> float | None:
    for name in names:
        value = settings.get(name)
        if isinstance(value, (int, float)) and np.isfinite(value):
            return float(value)
    return None


class DflMeasurementAdapter:
    """Map the stable existing parser model into the application domain model."""

    def adapt(self, document: DflDocument) -> MeasurementSession:
        source = Path(document.path).resolve()
        session_id = str(uuid5(NAMESPACE_URL, str(source).casefold()))
        metadata = MeasurementMetadata(
            device_type=document.instrument.device_type,
            firmware_version=document.instrument.firmware_version,
            system=document.instrument.system,
            channel_names=list(document.instrument.channel_names),
            modes=list(document.instrument.modes),
            settings=document.settings,
            streams=list(document.streams),
            warnings=list(document.warnings),
        )
        session = MeasurementSession(
            session_id, source, source.stem, metadata,
            acquisition_timing=dict(document.acquisition_timing),
        )
        colors = ("#35c6ff", "#ffb347", "#7ee787", "#d2a8ff", "#ff7b72", "#79c0ff")
        for index, raw in enumerate(document.traces):
            settings = document.settings.get(raw.mode, {})
            x = np.asarray(raw.x, dtype=np.float64)
            is_frequency = raw.x_unit == "Hz" and x.size > 0
            step = float(np.median(np.diff(x))) if x.size > 1 else 0.0
            regular = bool(
                is_frequency
                and x.size > 2
                and np.allclose(np.diff(x), step, rtol=1e-8, atol=max(1e-9, abs(step) * 1e-8))
            )
            trace = SpectrumTrace(
                trace_id=raw.key,
                name=raw.title,
                start_frequency_hz=float(x[0]) if is_frequency else 0.0,
                stop_frequency_hz=float(x[-1]) if is_frequency else 0.0,
                frequency_step_hz=step if is_frequency else 0.0,
                power_values=raw.y,
                frequency_values=None if regular else (x if is_frequency else None),
                axis_values=None if is_frequency else x,
                axis_unit=raw.x_unit,
                unit=raw.y_unit,
                rbw_hz=_number(settings, "Rbw", "ResolutionBandwidth"),
                vbw_hz=_number(settings, "Vbw"),
                detector=raw.detector,
                trace_mode=raw.update_mode or raw.display_mode or raw.state,
                reference_level_dbm=_number(settings, "Level"),
                attenuation_db=_number(settings, "AttenuationValue"),
                source_stream=raw.source_stream,
                enabled=raw.active or not session.traces,
                color=colors[index % len(colors)],
                metadata={
                    **raw.metadata,
                    "measurement": raw.measurement,
                    "measurement_type": raw.measurement_type,
                    "mode": raw.mode,
                    "state": raw.state,
                    "display_mode": raw.display_mode,
                    "provenance": raw.source_stream,
                    "source_path": str(source),
                    "source_revision": self._source_revision(source),
                },
            )
            session.traces[trace.trace_id] = trace
            if trace.enabled and session.active_trace_id is None:
                session.active_trace_id = trace.trace_id
        for raw_spectrogram in document.spectrograms:
            step = (raw_spectrogram.stop_hz - raw_spectrogram.start_hz) / max(
                1, raw_spectrogram.point_count - 1
            )
            waterfall = WaterfallData(
                waterfall_id=raw_spectrogram.key,
                name=raw_spectrogram.title,
                line_count=raw_spectrogram.line_count,
                point_count=raw_spectrogram.point_count,
                start_frequency_hz=raw_spectrogram.start_hz,
                stop_frequency_hz=raw_spectrogram.stop_hz,
                frequency_step_hz=step,
                source_stream=raw_spectrogram.source_stream,
                unit=raw_spectrogram.y_unit,
                metadata={
                    **raw_spectrogram.metadata,
                    "mode": raw_spectrogram.mode,
                    "measurement": raw_spectrogram.measurement,
                    "measurement_type": raw_spectrogram.measurement_type,
                    "oldest_timestamp": raw_spectrogram.oldest_timestamp,
                    "newest_timestamp": raw_spectrogram.newest_timestamp,
                    "history_depth": raw_spectrogram.history_depth,
                    "provenance": raw_spectrogram.source_stream,
                },
            )
            session.waterfalls[waterfall.waterfall_id] = waterfall
            session.active_waterfall_id = session.active_waterfall_id or waterfall.waterfall_id
        session.active_trace_id = session.active_trace_id or next(iter(session.traces), None)
        return session

    @staticmethod
    def _source_revision(path: Path) -> str:
        try:
            stat = path.stat()
        except OSError:
            return "unavailable"
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    @staticmethod
    def attach_preview(session: MeasurementSession, preview: SpectrogramPreview) -> WaterfallData:
        waterfall = session.waterfalls[preview.info.key]
        waterfall.set_preview(preview.values, preview.timestamps, preview.line_indices)
        return waterfall

    @staticmethod
    def spectrum_frame(trace: SpectrumTrace) -> SpectrumFrame:
        """Create the GUI/parser-independent input used by power measurements."""
        mode_text = str(trace.metadata.get("mode", "")).casefold()
        acquisition = (
            AcquisitionMode.REAL_TIME if "real-time" in mode_text or "realtime" in mode_text
            else AcquisitionMode.SWEPT if "spectrum" in mode_text
            else AcquisitionMode.UNKNOWN
        )
        trace_text = trace.trace_mode.casefold().replace("/", " ")
        if "max" in trace_text and "hold" in trace_text:
            trace_mode = TraceMode.MAX_HOLD
        elif "min" in trace_text and "hold" in trace_text:
            trace_mode = TraceMode.MIN_HOLD
        elif "average" in trace_text:
            trace_mode = TraceMode.AVERAGE
        elif "clear" in trace_text or "write" in trace_text:
            trace_mode = TraceMode.CLEAR_WRITE
        else:
            trace_mode = TraceMode.UNKNOWN
        semantics = (
            PowerSemantics.PSD_PER_HZ if trace.unit.casefold() == "dbm/hz"
            else PowerSemantics.UNKNOWN
        )
        try:
            stat = Path(trace.metadata.get("source_path", "")).stat()
            revision = f"{stat.st_size}:{stat.st_mtime_ns}"
        except (OSError, TypeError):
            revision = str(trace.metadata.get("source_revision", ""))
        return SpectrumFrame(
            trace.frequencies_hz,
            trace.power_values,
            unit=trace.unit,
            timestamp_s=trace.timestamp,
            source_id=trace.trace_id,
            source_revision=revision,
            acquisition_mode=acquisition,
            trace_mode=trace_mode,
            detector=trace.detector,
            power_semantics=semantics,
            rbw_hz=trace.rbw_hz,
            provenance=trace.source_stream,
        )
