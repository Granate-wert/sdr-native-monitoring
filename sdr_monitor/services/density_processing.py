"""SAME density journal join; no spectrum receipt, SDK call, IQ or per-frame JSON."""

from collections import OrderedDict
from hashlib import sha256
from math import isfinite
from struct import pack
from typing import Any

from ..domain.analytical_journal import OwnerJournalScope
from ..domain.density_processing import DensityProcessingContextV1
from ..domain.hackrf_live import HackrfLiveRequest
from ..domain.layer_journal import LayerJournalSnapshot, LayerJournalState
from ..domain.layer_ready import DensityLayerIdentity, LayerReadyKind, LayerReadyReceipt
from ..domain.live import BackendKind, LiveAdmissionRejected, LiveConfiguration, LiveSessionState, LiveSnapshot
from ..domain.processing_policy import SdrProcessingPolicyV1
from ..domain.receiver_topology import ReceiverChainSelection
from ..domain.source_processing import RfProcessingValue, RfValueAuthority
from .native_layer_journal import (
    DENSITY_CONTEXT_CACHE_RESERVATION, DENSITY_RECEIPT_WINDOW_RESERVATION, layer_journal_capacity,
)
from .native_owner_journal import _scalar_bytes


def admit_density_processing(native: object, policy: SdrProcessingPolicyV1,
                             *, enabled: bool, hackrf: bool = False) -> None:
    """Loaded-code-only, before claim/permit/open; default-OFF compatibility intact."""
    if not enabled or policy.is_off:
        return
    getter = getattr(native, "persistence_processing_contract", None)
    contract = getter() if callable(getter) else None
    expected = {"schema_version": 1, "scope": "native_contributing_density_metadata_only",
        "full_owner_context": False, "recipe_change_resets": True, "string_max_bytes": 256,
        "reserved_bytes": 8192}
    if (type(contract) is not dict or set(contract) != set(expected)
            or any(type(contract[k]) is not type(v) or contract[k] != v for k, v in expected.items())
            or not layer_journal_capacity(native, hackrf=hackrf)):
        raise LiveAdmissionRejected("processed density requires exact metadata and SAME layer journal contracts")
    if not hackrf and (getattr(native, "PlutoReceiverSelection", None) is None
            or not callable(getattr(getattr(native, "PlutoFixedBandEngine", None),
                                    "drain_density_layer_ready_events", None))):
        raise LiveAdmissionRejected("processed AD density requires the actual native owner drain before admission")


class DensityProcessingJoin:
    """At most four scalar geometry keys, charged within the original layer budget."""

    def __init__(self) -> None:
        self._grids: OrderedDict[tuple[object, ...], str] = OrderedDict()

    def clear(self) -> None:
        self._grids.clear()

    def receipt(self, family: str, raw: Any, density: Any, current: LiveSnapshot,
                journal: LayerJournalSnapshot, *, revision: int,
                paired_sources: tuple[str, str] | None = None) -> DensityProcessingContextV1 | None:
        if type(current) is not LiveSnapshot:
            return None  # Legacy converter-only observation, not an admitted owner.
        configuration: LiveConfiguration | HackrfLiveRequest
        if family == "ad936x":
            if current.applied is None:
                return None
            configuration = current.applied.applied
        elif family == "hackrf":
            if current.hackrf_request is None:
                return None
            configuration = current.hackrf_request
        else:
            raise ValueError("unsupported density owner family")
        policy = configuration.processing_policy
        ready, p = density.layer_ready, density.numerical_provenance
        try:
            metadata = getattr(raw, "processing_metadata", None)
        except Exception:  # noqa: BLE001 - legacy optional metadata is UNKNOWN, never guessed.
            metadata = None
        complete = (current.state is LiveSessionState.RUNNING and configuration.backend is BackendKind.CPU
            and type(journal) is LayerJournalSnapshot and journal.state is LayerJournalState.ACTIVE
            and type(journal.scope) is OwnerJournalScope and journal.counters is not None
            and type(ready) is LayerReadyReceipt and ready.owner_run_id is not None
            and p is not None and p.processing_recipe is not None and metadata is not None)
        if not complete:
            if not policy.is_off:
                raise ValueError("processed density lacks current ACTIVE SAME owner/native context")
            return None
        scope, counters = journal.scope, journal.counters
        assert scope is not None and counters is not None and ready is not None and p is not None
        assert isinstance(scope, OwnerJournalScope) and metadata is not None
        identity = DensityLayerIdentity(density.source_id, density.config_generation, density.update_sequence,
            density.source_frame_sequence, density.accumulation_id, density.receiver_id,
            density.acquisition_epoch, density.native_accumulation_sequence)
        if (ready.kind is not LayerReadyKind.DENSITY or ready.identity != identity
                or identity.native_accumulation_sequence is None
                or ready.producer_instance_id != counters.producer_instance_id
                or ready.owner_run_id != scope.owner_run_id or ready.session_id != scope.session_id
                or ready.adapter_clock_scope_id != scope.clock_scope_id or ready.host_process_id != scope.host_process_id
                or scope.source_id != density.source_id or scope.receiver_id != density.receiver_id
                or scope.session_id != current.session_id or scope.acquisition_epoch != current.acquisition_epoch
                or scope.configuration_generation != current.active_config_generation
                or scope.configuration_generation != density.config_generation
                or identity.accumulation_id != current.session_id or identity.acquisition_epoch != current.acquisition_epoch
                or type(density.config_generation) is not int or density.unit != "dBFS/bin"
                or p.processing_recipe.policy != policy or p.window_normalization_version != "power-norm-v1"
                or p.precision_mode != ("accurate_f32_f64_accum" if family == "ad936x" else "reference_f64")
                or p.calibration_status != "uncalibrated"):
            raise ValueError("density belongs to a foreign/stale owner, creation or numerical policy")
        # Authenticate the original retained creation, not merely a copied receipt.
        if not any(event.kind is LayerReadyKind.DENSITY
                and (event.producer_instance_id, event.creation_sequence, event.ready_native_ns,
                     event.config_generation, event.update_sequence, event.source_frame_sequence, event.accumulation_sequence)
                == (ready.producer_instance_id, ready.creation_sequence, ready.ready_native_ns,
                    identity.config_generation, identity.update_sequence, identity.source_frame_sequence,
                    identity.native_accumulation_sequence) for event in journal.events):
            raise ValueError("density original creation is absent from SAME active journal")
        if family == "ad936x":
            from .live_processing import ad_detector_enum_name, ad_window_enum_name
            assert isinstance(configuration, LiveConfiguration) and current.applied is not None
            topology = current.device.capabilities.receiver_topology if current.device is not None else None
            receiver = density.receiver_id
            source = (paired_sources[0 if receiver == "RX1" else 1] if paired_sources is not None
                      else current.active_source_id)
            if (topology is None or receiver not in ("RX1", "RX2")
                    or not topology.supports_selection(ReceiverChainSelection[receiver])
                    or (paired_sources is None and receiver != current.receiver_id)
                    or source != density.source_id
                    or not {"center_hz", "sample_rate_hz", "analog_bandwidth_hz"}.issubset(current.applied.readback_fields)):
                raise ValueError("density AD resource/RX/readback authority is unqualified")
            resource, center, rate, bandwidth = (topology.physical_stream_resource_id,
                configuration.center_hz, configuration.sample_rate_hz, configuration.analog_bandwidth_hz)
            if bandwidth is None:
                raise ValueError("density actual AD bandwidth is unknown")
            hop = max(1, int(round(configuration.fft_size * (1.0 - configuration.overlap_ratio))))
            window, detector = ad_window_enum_name(configuration.window).lower(), ad_detector_enum_name(configuration.detector).lower()
            authority = RfValueAuthority.READBACK
            if metadata.analog_bandwidth_hz != bandwidth:
                raise ValueError("density native AD bandwidth differs from actual readback")
        else:
            assert isinstance(configuration, HackrfLiveRequest)
            choice = current.source_choice
            if (choice is None or choice.family.value != "hackrf" or density.receiver_id is not None
                    or density.source_id != configuration.source_id or current.active_source_id != configuration.source_id
                    or current.active_config_generation != configuration.configuration_generation):
                raise ValueError("density HackRF selected owner authority differs")
            resource, center, rate, bandwidth = (str(choice.device_id), configuration.center_frequency_hz,
                configuration.sample_rate_hz, configuration.baseband_filter_hz)
            hop, window, detector = configuration.hop_size, configuration.window, configuration.detector
            authority = RfValueAuthority.SDK_APPLIED  # NOT native/hardware bandwidth readback.
        size = configuration.fft_size
        if (metadata.center_frequency_hz != center or metadata.sample_rate_hz != rate
                or type(metadata.fft_size) is not int or type(metadata.hop_size) is not int
                or metadata.fft_size != size or metadata.hop_size != hop
                or p.window != window or p.detector != detector or p.averaging_frames != configuration.averaging_frames
                or p.fft_bin_width_hz != rate / size or density.frequency_bins != size
                or len(density.frequencies_hz) != size or not isfinite(float(density.frequencies_hz[0]))
                or float(density.frequencies_hz[0]) != center - rate / 2
                or float(density.frequencies_hz[-1]) != center + rate / 2 - rate / size):
            raise ValueError("density contributing native FFT/profile/grid differs from admitted owner")
        grid = (float(center), float(rate), size, density.unit, "power-norm-v1")
        digest = self._grids.get(grid)
        if digest is None:
            digest = "sha256:" + sha256(b"regular-fftshift-grid-v1\0" + pack("!ddQ", center, rate, size)
                + b"dBFS/bin\0power-norm-v1").hexdigest()
            candidate = tuple((*self._grids.items(), (grid, digest)))[-4:]
            if _scalar_bytes(candidate) > DENSITY_CONTEXT_CACHE_RESERVATION:
                raise ValueError("density geometry cache exceeds admitted host reservation")
            self._grids[grid] = digest
            if len(self._grids) > 4:
                self._grids.popitem(last=False)
        result = DensityProcessingContextV1(family, str(resource), ready, digest,
            p.processing_recipe.policy_digest, revision, p,
            RfProcessingValue(float(center), authority), RfProcessingValue(float(rate), authority),
            RfProcessingValue(float(bandwidth), authority), size, hop, density.native_quality_flags)
        if _scalar_bytes(result) + _scalar_bytes(ready) > DENSITY_RECEIPT_WINDOW_RESERVATION:
            raise ValueError("density processing receipt exceeds admitted host window")
        return result
