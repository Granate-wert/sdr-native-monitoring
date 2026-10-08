"""Pure preflight and SAME original Sweep creation join; no SDK, IQ or new owner."""
from typing import Any

from ..domain.hackrf_sweep import HackrfSweepRequest
from ..domain.layer_journal import LayerJournalSnapshot, LayerJournalState, SweepLayerScope
from ..domain.layer_ready import LayerReadyKind, LayerReadyReceipt
from ..domain.live import LiveAdmissionRejected
from ..domain.processing_policy import SdrProcessingPolicyV1
from ..domain.sweep_acquisition import SweepSegmentAcquisition
from ..domain.sweep_processing import HackrfSweepProcessingContextV1
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
