"""Observed native metadata -> owner-bound paired reduced publication.

Called by the admitted coordinator only. No hardware ownership, IQ or clock
inference here. BOTH scalar receipts are checked before either grid converts.
"""

from typing import Any

from ..domain.identity import TimestampQuality
from ..domain.paired_sweep import (
    PairedSweepRunIdentity, PairedSweepStepIdentity, PairedSweepStepObservation,
    PairedSweepStepPair, PairedSweepGainMode, PairedSweepGainReadback,
)
from ..domain.paired_sweep_publication import PairedSweepPublication
from ..domain.receiver_topology import ReceiverChainSelection
from ..domain.continuous_sweep_geometry import sweep_step_geometry, sweep_segment_count
from .ad936x_identity_admission import normalized_pluto_serial
from .native_continuous_sweep import _to_domain_line, _to_domain_progress


def observed_paired_publication(native: Any, run: PairedSweepRunIdentity,
                                *, progress: bool = False) -> PairedSweepPublication:
    """Conversion alone does not authorize a run or hardware."""
    request = run.request
    a, b = native.primary, native.secondary
    device = run.start_snapshot.device
    if device is None:
        raise ValueError("paired Sweep run has no admitted device")
    selected_serial = normalized_pluto_serial(device.serial)
    if selected_serial is None:
        raise ValueError("paired Sweep run requires a known normalized physical serial")
    if (native.resource_id != request.resource_id
            or (a.epoch, a.line_sequence) != (b.epoch, b.line_sequence)
            or type(a.epoch) is not int or a.epoch < request.sweep.epoch
            or type(a.line_sequence) is not int or a.line_sequence < 0):
        raise ValueError("native paired Sweep resource/epoch/sequence mismatch")
    for frame, producer, selection in ((a, request.pair.primary_source_id, "RX1"),
                                       (b, request.pair.secondary_source_id, "RX2")):
        if (frame.source_id != producer or frame.source.source_id != producer
                or normalized_pluto_serial(frame.source.device_serial) != selected_serial
                or frame.source.metadata_json.get("receiver_selection") != f'"{selection}"'):
            raise ValueError("native paired Sweep producer/physical serial mismatch")
    receipts = tuple(native.steps)
    acquired = (tuple(a.segment_acquisition), tuple(b.segment_acquisition))
    if any(len(chain) != len(receipts) for chain in acquired):
        raise ValueError("native paired Sweep receipt/acquisition count mismatch")
    if not progress and a.state == "complete" and len(receipts) != sweep_segment_count(request.sweep):
        raise ValueError("complete paired Sweep lacks a full observed step prefix")
    pairs = []
    for index, receipt in enumerate(receipts):
        if type(receipt.step_index) is not int or receipt.step_index != index:
            raise ValueError("native paired Sweep acquired prefix is not contiguous")
        geometry = sweep_step_geometry(request.sweep, index)
        identity = PairedSweepStepIdentity(request.resource_id, request.pair.session_id,
            a.epoch, a.line_sequence, receipt.step_index, receipt.config_generation,
            run.acquisition_epoch, receipt.synchronization_epoch, request.sweep,
            request.pair.configuration, request.selection_revision,
            geometry.usable_start_hz, geometry.usable_stop_hz,
            receipt.center_frequency_hz, receipt.sample_rate_hz,
            receipt.analog_bandwidth_hz, receipt.fft_size)
        observations = []
        readbacks = getattr(receipt, "receiver_gains", None)
        if type(readbacks) is not tuple or len(readbacks) != 2:
            raise ValueError("native paired Sweep actual gain readbacks require ordered RX1/RX2")
        gains = []
        for readback, selection in zip(readbacks, (ReceiverChainSelection.RX1, ReceiverChainSelection.RX2)):
            if (getattr(getattr(readback, "receiver", None), "name", None) != selection.name
                    or getattr(getattr(readback, "gain_mode", None), "name", None) != "MANUAL"):
                raise ValueError("native paired Sweep actual gain readback chain/mode mismatch")
            # Untrusted SDK scalar; the typed value constructor refuses
            # missing/bool/string/nonfinite data, never fills it from intent.
            actual_gain: Any = getattr(readback, "manual_gain_db", None)
            gains.append(PairedSweepGainReadback(selection, PairedSweepGainMode.MANUAL, actual_gain))
        for gain, (chain, selection, producer) in zip(gains, (
                (acquired[0], ReceiverChainSelection.RX1, request.pair.primary_source_id),
                (acquired[1], ReceiverChainSelection.RX2, request.pair.secondary_source_id))):
            segment = chain[index]
            if ((segment.segment_index, segment.config_generation, segment.frame_sequence,
                 segment.first_sample_index, segment.timestamp_ns, segment.sample_rate_hz, segment.fft_size)
                    != (index, receipt.config_generation, receipt.frame_sequence,
                        receipt.first_sample_index, receipt.timestamp_ns, receipt.sample_rate_hz, receipt.fft_size)):
                raise ValueError("native paired Sweep receipt differs from chain acquisition")
            # Existing wire retains producer time/quality flags, not timestamp
            # provenance or clock domain. Never upgrade to hardware/estimated.
            observations.append(PairedSweepStepObservation(identity, selection, producer,
                receipt.center_frequency_hz, receipt.sample_rate_hz, receipt.fft_size,
                receipt.frame_sequence, receipt.first_sample_index, receipt.timestamp_ns,
                TimestampQuality.UNKNOWN, None, segment.quality_flags, receipt.shared_input_gaps_before, gain))
        pair = PairedSweepStepPair(*observations)
        pair.validate_active(request, identity)
        pairs.append(pair)
    convert = _to_domain_progress if progress else _to_domain_line
    return PairedSweepPublication(run, tuple(pairs), convert(a), convert(b))
