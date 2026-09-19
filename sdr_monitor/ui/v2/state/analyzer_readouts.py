"""Scalar Analyzer labels; no acquisition, reductions or GUI ownership."""

from __future__ import annotations

from math import isfinite

from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.domain.sweep_progress import SweepProgressFrame

from ..i18n import text
from ..view_models.analyzer_view_model import AnalyzerViewState


def analyzer_status(state: AnalyzerViewState) -> str:
    bundle = state.bundle
    if state.configuration_pending:
        phase = text("analyzer.applying")
    elif state.starting:
        phase = text("analyzer.starting")
    elif state.stopping:
        phase = text("analyzer.stopping")
    elif state.error:
        phase = text("analyzer.failed_last" if bundle is not None else "analyzer.failed")
    elif state.live.busy:
        phase = text("analyzer.discovering" if state.live.discovery_pending else "analyzer.busy")
        if bundle is not None:
            phase += " · " + text("analyzer.retained_frame")
    elif state.running:
        phase = text("analyzer.running")
    else:
        phase = text("analyzer.stopped_last" if bundle is not None else "analyzer.idle")
        if state.live.discovery_count is not None:
            phase += " · " + (text("analyzer.discovery_empty") if state.live.discovery_count == 0
                               else text("analyzer.discovery_found", count=state.live.discovery_count))
    if bundle is None:
        return phase + " · " + text("analyzer.unavailable")
    frame = bundle.spectrum
    parts = [phase, text(
        "analyzer.provenance", source=frame.source_id, rx=bundle.receiver_id or "—",
        epoch=bundle.acquisition_epoch if bundle.acquisition_epoch is not None else "—",
        unit=bundle.unit,
    )]
    if isinstance(frame, LiveSpectrumFrame):
        parts.append(state.live.data_age_label)
        if state.live.measurement_unavailable_reason == "presentation_memory_budget":
            parts.append(text("analyzer.memory_limited"))
        parts.append(analyzer_rtbw_rates(state))
        parts.append(text("analyzer.quality_unknown") if frame.native_quality_flags is None else
                     text("analyzer.quality_mask", mask=f"0x{frame.native_quality_flags:08X}"))
        if any((frame.dropped_samples_before, frame.dropped_iq_blocks_before, frame.dropped_fft_frames_before)):
            parts.append(text("analyzer.frame_loss", samples=frame.dropped_samples_before,
                              blocks=frame.dropped_iq_blocks_before, fft=frame.dropped_fft_frames_before))
    else:
        parts.append(text("analyzer.sweep_time"))
        statistics = bundle.sweep_statistics
        if statistics is not None:
            parts.append(text("analyzer.sweep_statistics", passes=statistics.retained_passes,
                              sequence=statistics.newest_pass_sequence,
                              lag=frame.sequence - statistics.newest_pass_sequence,
                              columns=statistics.density_observations.size))
        if isinstance(frame, SweepProgressFrame):
            received = len(frame.acquired_segment_generations)
            parts.append(text("analyzer.progress", received=received,
                              total=received + len(frame.pending_segment_indices), revision=frame.revision))
            parts.append(text("analyzer.partial"))
        elif isinstance(frame, SweepLineFrame):
            # Some producers include expected-but-missing generations, others
            # publish acquired generations only. Neither may count a missing
            # segment as an observation.
            missing = set(frame.missing_segment_indices)
            acquired = {index for index, _generation in frame.segment_config_generations} - missing
            total = len(acquired | missing)
            parts.append(text("analyzer.terminal_progress", received=len(acquired),
                              total=total))
            parts.append(text("analyzer.gapped" if not frame.is_complete else
                              "analyzer.completed"))
            if frame.gap_reasons:
                parts.append(", ".join(reason.value for reason in frame.gap_reasons))
    if bundle.coherence_issues:
        parts.append(", ".join(bundle.coherence_issues))
    return " · ".join(parts)


def analyzer_rtbw_rates(state: AnalyzerViewState) -> str:
    """Show producer observations, never substitute configured Fs or UI FPS."""
    metrics = getattr(state.live.snapshot, "performance", None)
    interval = getattr(metrics, "rate_observation_interval_s", None)
    valid_interval = (isinstance(interval, (int, float)) and not isinstance(interval, bool)
                      and isfinite(interval) and interval > 0)

    def value(name: str, divisor: float = 1.0) -> str:
        number = getattr(metrics, name, None)
        if (not valid_interval or not isinstance(number, (int, float))
                or isinstance(number, bool) or not isfinite(number) or number < 0):
            return "—"
        return f"{number / divisor:.3g}"

    return text("analyzer.rtbw_rates", fft=value("analytical_fft_rate_hz"),
                publications=value("spectrum_snapshot_rate_hz"),
                iq=value("iq_sample_rate_hz", 1e6))


def spectrum_numerical_readout(frame: object | None) -> str:
    """Producer metadata only: never derive RBW/calibration from UI drafts."""
    metadata = getattr(frame, "numerical_provenance", None)
    if metadata is None:
        return text("analyzer.numerical_unknown")

    def scalar(name: str) -> str:
        value = getattr(metadata, name)
        return "—" if value is None else f"{value:g}" if isinstance(value, (int, float)) else value

    return text("analyzer.numerical_metadata", window=scalar("window"),
                detector=scalar("detector"), precision=scalar("precision_mode"),
                averaging=scalar("averaging_frames"), bin_width=scalar("fft_bin_width_hz"),
                enbw=scalar("enbw_hz"), rbw=scalar("nominal_rbw_hz"),
                calibration=scalar("calibration_status"), profile=scalar("calibration_profile_id"),
                uncertainty=scalar("estimated_uncertainty_db"))
