"""Reduced paired publication helpers; no SDK opener, threads, control or raw I/Q."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
from typing import Any

from sdr_monitor.domain.live import LivePersistenceFrame, LiveSnapshot, LiveSpectrumFrame, LiveQuality
from sdr_monitor.domain.paired_live import (
    PairedLivePerformance, PairedLivePublication, PairedLiveRequest, PairedReceiverPerformance,
)


def pair_performance(value: Any, request: PairedLiveRequest) -> PairedLivePerformance:
    # FixedBandMetrics repeats the ONE acquisition stream for compatibility.
    # Read it once, never sum two device/acquisition views.
    primary, secondary = value.primary, value.secondary
    for channel, expected in ((primary, "RX1"), (secondary, "RX2")):
        if channel.receiver_selection.name != expected:
            raise ValueError("paired metrics have a foreign receiver selection")
    def channel_metrics(channel: Any, source: str, receiver: str) -> PairedReceiverPerformance:
        return PairedReceiverPerformance(source, receiver,
            channel.engine.fft_frames_computed, channel.engine.fft_frames_dropped,
            channel.engine.persistence_updates, channel.persistence_snapshots_superseded)
    return PairedLivePerformance(
        primary.engine.iq_samples_received, primary.engine.iq_blocks_received,
        primary.acquisition_queue_samples_dropped, primary.acquisition_queue_blocks_dropped,
        value.paired_snapshots_emitted, value.paired_snapshots_superseded,
        value.paired_snapshots_abandoned, value.dsp.shared_input_gaps,
        value.paired_processing_ms,
        channel_metrics(primary, request.primary_source_id, "RX1"),
        channel_metrics(secondary, request.secondary_source_id, "RX2"))


def pair_publication(
    value: Any,
    context: LiveSnapshot,
    request: PairedLiveRequest,
    convert_spectrum: Callable[..., LiveSpectrumFrame],
    densities: tuple[LivePersistenceFrame | None, LivePersistenceFrame | None],
    convert_quality: Callable[[LiveSpectrumFrame, LiveQuality], LiveQuality],
) -> PairedLivePublication:
    """Validate native evidence before conversion; never rewrite producer identity."""
    snapshots = []
    for frame, source, receiver, density in (
            (value.primary, request.primary_source_id, "RX1", densities[0]),
            (value.secondary, request.secondary_source_id, "RX2", densities[1])):
        if (frame.source.source_id != source
                or json.loads(frame.source.metadata_json["receiver_selection"]) != receiver
                or frame.config_generation != value.config_generation
                or frame.config_generation != context.active_config_generation
                or frame.first_sample_index != value.first_sample_index
                or frame.timestamp_ns != value.timestamp_ns):
            raise ValueError("paired native publication has foreign source/chain/index/epoch")
        spectrum = convert_spectrum(frame, context, source, receiver_id=receiver)
        if density is not None:
            if (not density.producer_identity_available or density.source_id != spectrum.source_id
                    or density.config_generation != spectrum.config_generation
                    or density.acquisition_epoch != spectrum.acquisition_epoch
                    or density.clock_domain != spectrum.clock_domain
                    or density.unit != spectrum.unit or density.frequency_bins != spectrum.fft_size):
                raise ValueError("paired persistence has foreign producer/epoch/unit/geometry")
            # A newer analytical density may be polled before its UI spectrum.
            # Keep the bounded cache but never attach future density to an old frame.
            if density.source_frame_sequence > spectrum.sequence:
                density = None
            else:
                density = replace(density, receiver_id=receiver)
        quality = convert_quality(spectrum, context.quality)
        snapshots.append(replace(context, spectrum=spectrum, persistence=density,
            active_source_id=spectrum.source_id, receiver_id=receiver,
            unit=spectrum.unit, quality=quality))
    return PairedLivePublication(snapshots[0], snapshots[1],
        value.synchronization_epoch, value.first_sample_index, value.shared_input_gaps_before)


__all__ = ["pair_performance", "pair_publication"]
