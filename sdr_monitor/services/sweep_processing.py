"""Pure preflight and SAME original Sweep creation join; no SDK, IQ or new owner."""
from typing import Any

from ..domain.hackrf_sweep import HackrfSweepRequest
from ..domain.layer_journal import LayerJournalSnapshot, LayerJournalState, SweepLayerScope
from ..domain.layer_ready import LayerReadyKind, LayerReadyReceipt
from ..domain.live import LiveAdmissionRejected
from ..domain.processing_policy import SdrProcessingPolicyV1, HostDcMode
from ..domain.sweep_acquisition import SweepSegmentAcquisition
from ..domain.sweep_processing import HackrfSweepProcessingContextV1, AdSweepProcessingPlanV1, AdSweepProcessingContextV1
from ..domain.source_processing import RfValueAuthority
from .native_layer_journal import layer_journal_capacity, LAYER_RECEIPT_RESERVATION
from .native_owner_journal import _scalar_bytes


def hackrf_sweep_processing_reserved_bytes(request: HackrfSweepRequest) -> int:
    # Five retained outputs plus one converting/staging domain record set.
    # Original native metadata charge stays separate. This conservative Python
    # charge and two chunk-local f64 grid arrays use the SAME 128MiB ceiling.
    return 6 * 4096 * request.geometry.segment_count + 2 * 65536 * 8


def admit_hackrf_sweep_processing(native: object, policy: SdrProcessingPolicyV1) -> bool:
    getter = getattr(native, "sweep_processing_metadata_contract", None)
    expected = {"schema_version": 1, "scope": "native_contributing_sweep_metadata_only",
        "full_owner_context": False, "mixed_processing_line_refused": True,
        "string_max_bytes": 256, "segment_record_reserved_bytes": 1024}
    try:
        actual = getter() if callable(getter) else None
    except Exception:
        actual = None  # Loaded legacy code cannot grant processed admission.
    version = getattr(native, "HACKRF_SWEEP_PROCESSING_FACTORY_CONTRACT_VERSION", None)
    qualified = (type(actual) is dict and set(actual) == set(expected)
        and all(type(actual[k]) is type(v) and actual[k] == v for k, v in expected.items())
        and type(version) is int and version == 1
        and bool(layer_journal_capacity(native, hackrf=True)))
    if not qualified and not policy.is_off:
        raise LiveAdmissionRejected("processed Sweep requires exact metadata/factory and SAME layer journal contracts")
    return qualified


def hackrf_sweep_processing_receipt(raw: Any, records: tuple[SweepSegmentAcquisition, ...] | None, ready: LayerReadyReceipt | None,
        journal: LayerJournalSnapshot, request: HackrfSweepRequest) -> HackrfSweepProcessingContextV1 | None:
    complete = (type(ready) is LayerReadyReceipt and ready.owner_run_id is not None
        and type(journal.scope) is SweepLayerScope and journal.counters is not None
        and (journal.state is LayerJournalState.ACTIVE or journal.state is LayerJournalState.FINAL
             and journal.native_stop_confirmed and ready.kind is LayerReadyKind.SWEEP_TERMINAL)
        and records is not None and all(r.processing_metadata is not None for r in records))
    if not complete:
        if not request.processing_policy.is_off:
            raise ValueError("processed Sweep lacks current ACTIVE SAME owner/native context")
        return None
    assert ready is not None and isinstance(journal.scope, SweepLayerScope) and journal.counters is not None
    scope = journal.scope
    if (scope.owner_run_id != ready.owner_run_id or scope.session_id != ready.session_id
            or scope.clock_scope_id != ready.adapter_clock_scope_id or scope.host_process_id != ready.host_process_id
            or scope.source_id != request.source.device_id or scope.receiver_id is not None
            or scope.acquisition_epoch != request.epoch or raw.epoch != request.epoch
            or raw.source_id != request.source.device_id
            or journal.counters.producer_instance_id != ready.producer_instance_id
            or not any((event.kind, event.producer_instance_id, event.creation_sequence, event.ready_native_ns,
                        event.sweep_epoch, event.line_sequence, event.revision) ==
                (ready.kind, ready.producer_instance_id, ready.creation_sequence, ready.ready_native_ns,
                 raw.epoch, raw.line_sequence, getattr(raw, "revision", 0)) for event in journal.events)):
        raise ValueError("Sweep belongs to foreign/stale owner or absent original creation")
    result = HackrfSweepProcessingContextV1(str(request.source.device_id), request.selection_revision,
        ready, request.processing_policy, request.start_hz, request.stop_hz, request.fft_size)
    # Context and receipt share objects, but duplicate-account conservatively.
    if _scalar_bytes(result) + _scalar_bytes(ready) > 2 * LAYER_RECEIPT_RESERVATION + 256 * request.geometry.segment_count:
        raise ValueError("Sweep processing context exceeds original host scalar window")
    if records is not None and _scalar_bytes(records) > 4096 * max(1, request.geometry.segment_count):
        raise ValueError("Sweep contributing Python metadata exceeds admitted host reservation")
    return result


def admit_ad_sweep_processing(native: object, configuration: Any) -> bool:
    """Exact loaded code only; no context, RF, clock or owner acquisition."""
    from .live_processing import admit_ad_processing
    from .native_sweep_layer_journal import pluto_sweep_layer_capacity
    policy = configuration.processing_policy
    admit_ad_processing(native, configuration)
    getter = getattr(native, "sweep_processing_metadata_contract", None)
    try:
        actual = getter() if callable(getter) else None
    except Exception:
        actual = None
    expected = {"schema_version": 1, "scope": "native_contributing_sweep_metadata_only",
        "full_owner_context": False, "mixed_processing_line_refused": True,
        "string_max_bytes": 256, "segment_record_reserved_bytes": 1024}
    version = getattr(native, "PLUTO_SWEEP_PRODUCT_RESERVATION_PROTOCOL_VERSION", None)
    qualified = (type(actual) is dict and set(actual) == set(expected)
        and all(type(actual[k]) is type(v) and actual[k] == v for k, v in expected.items())
        and type(version) is int and version == 1 and bool(pluto_sweep_layer_capacity(native))
        and configuration.analog_bandwidth_hz is not None)
    if not qualified and not policy.is_off:
        raise LiveAdmissionRejected("processed AD Sweep requires exact metadata/host-budget and SAME native journal contracts")
    return qualified


def make_ad_sweep_processing_plan(native: object, source: Any, request: Any, preflight: Any,
        *, source_id: str, receiver_id: str, paired_request: Any = None) -> AdSweepProcessingPlanV1 | None:
    from .live_processing import ad_window_enum_name, ad_detector_enum_name
    live = source.live_configuration
    if not admit_ad_sweep_processing(native, live):
        return None  # Explicit legacy OFF observation, not fabricated ownership.
    if paired_request is None:
        resource, session, revision, kind = (source.owner_device_id, source.owner_session_id,
            source.owner_processing_revision, "native_processing")
    else:
        resource, session, revision, kind = (paired_request.resource_id, paired_request.pair.session_id,
            paired_request.selection_revision, "application_selection")
    if resource is None or session is None or revision is None:
        if live.processing_policy.is_off:
            return None
        raise LiveAdmissionRejected("processed AD Sweep requires actual selected owner/session/revision authority")
    return AdSweepProcessingPlanV1(resource, session, revision, kind, source_id, receiver_id,
        request, live.processing_policy, float(live.sample_rate_hz), float(live.analog_bandwidth_hz),
        live.fft_size, max(1, int(round(live.fft_size * (1. - live.overlap_ratio)))),
        preflight.fft_averaging_frames, ad_window_enum_name(live.window).lower(), ad_detector_enum_name(live.detector).lower())


def ad_sweep_processing_reserved_bytes(plan: AdSweepProcessingPlanV1, *, paired: bool = False) -> int:
    from ..domain.continuous_sweep_geometry import sweep_segment_count
    # Charged conservatively even for shared plans; native buffers are separate.
    # Retained queue + original conversion/current/preview windows, not all
    # external user-held histories or measured RSS. BOTH plans share one ceiling.
    slots = 2 * plan.request.output_queue_capacity + 5 if paired else plan.request.output_queue_capacity + 4
    return (slots * (16384 + 4096 * sweep_segment_count(plan.request))
            + 16384 + 16 * min(65536, plan.output_bins))


def ad_sweep_processing_receipt(raw: Any, records: tuple[SweepSegmentAcquisition, ...] | None,
        ready: LayerReadyReceipt | None, journal: LayerJournalSnapshot,
        plan: AdSweepProcessingPlanV1) -> AdSweepProcessingContextV1 | None:
    if (type(ready) is not LayerReadyReceipt or type(journal.scope) is not SweepLayerScope
            or journal.counters is None or records is None
            or not (journal.state is LayerJournalState.ACTIVE or journal.state is LayerJournalState.FINAL
                    and journal.native_stop_confirmed and ready.kind is LayerReadyKind.SWEEP_TERMINAL)):
        if plan.policy.is_off:
            return None  # Optional legacy OFF diagnostics do not upgrade unknown ownership.
        raise ValueError("AD Sweep lacks current SAME owner/journal context")
    scope = journal.scope
    if (scope.owner_run_id != ready.owner_run_id or scope.session_id != plan.session_id
            or scope.session_id != ready.session_id or scope.source_id != plan.source_id
            or scope.receiver_id != plan.receiver_id or scope.acquisition_epoch != raw.epoch
            or raw.source_id != plan.source_id
            or scope.clock_scope_id != ready.adapter_clock_scope_id or scope.host_process_id != ready.host_process_id
            or journal.counters.producer_instance_id != ready.producer_instance_id
            or not any((event.kind, event.producer_instance_id, event.creation_sequence, event.ready_native_ns,
                        event.sweep_epoch, event.line_sequence, event.revision) ==
                (ready.kind, ready.producer_instance_id, ready.creation_sequence, ready.ready_native_ns,
                 raw.epoch, raw.line_sequence, getattr(raw, "revision", 0)) for event in journal.events)):
        raise ValueError("AD Sweep belongs to foreign/stale owner or absent original creation")
    result = AdSweepProcessingContextV1(plan, ready,
        RfValueAuthority.READBACK if records else RfValueAuthority.UNKNOWN)
    count = len(records)
    if (_scalar_bytes(result) + _scalar_bytes(ready) > 16384 + 256 * count
            or _scalar_bytes(records) > 4096 * max(1, count)):
        raise ValueError("AD Sweep processing context exceeds admitted HOST reservation")
    return result


def validate_ad_sweep_processing_config(plan: AdSweepProcessingPlanV1, config: Any) -> None:
    """Exact frozen plan BEFORE Configure/Start; no SDK or native-owner call."""
    from ..domain.continuous_sweep_geometry import sweep_segment_count, sweep_step_geometry
    request = plan.request
    if ((config.display_start_hz, config.display_stop_hz, config.usable_window_hz,
         config.analysis_bins_per_usable_window, config.output_queue_capacity,
         config.segment_frame_timeout_ms, config.line_snapshot_rate_hz) !=
            (request.start_hz, request.stop_hz, request.usable_window_hz,
             request.analysis_bins_per_usable_window, request.output_queue_capacity,
             request.segment_frame_timeout_ms, request.line_snapshot_rate_hz or 0.)
            or config.epoch != request.epoch or len(config.segments) != sweep_segment_count(request)
            or config.layer_event_capacity != 64):
        raise ValueError("AD Sweep native config differs from admitted processing plan")
    for index, segment in enumerate(config.segments):
        fixed, geometry = segment.fixed_band, sweep_step_geometry(request, index)
        device, dsp = fixed.device, fixed.dsp
        if ((segment.usable_start_hz, segment.usable_stop_hz) !=
                (geometry.usable_start_hz, geometry.usable_stop_hz)
                or (device.source_id, device.center_frequency_hz, device.sample_rate_hz,
                    device.analog_bandwidth_hz, device.buffer_samples) !=
                    (plan.source_id, geometry.center_hz, plan.sample_rate_hz,
                     plan.analog_bandwidth_hz, request.acquisition_buffer_samples)
                or (dsp.fft_size, dsp.hop_size, dsp.averaging_frames) !=
                    (plan.fft_size, plan.hop_size, plan.averaging_frames)
                or dsp.window.name.lower() != plan.window or dsp.detector.name.lower() != plan.detector
                or dsp.unit.name != "DBFS_BIN" or dsp.precision_mode.name != "ACCURATE_F32_F64_ACCUM"
                or fixed.receiver_selection.name != plan.receiver_id
                or fixed.dc_removal_block_mean != (plan.policy.dc_mode is HostDcMode.BLOCK_MEAN)):
            raise ValueError("AD Sweep contributing native config differs from SAME owner/RF/recipe")
