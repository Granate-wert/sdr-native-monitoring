"""Qt-free, no-I/O device-family and capability evidence contracts."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from itertools import islice


class DeviceFamily(StrEnum):
    UNKNOWN = "unknown"
    AD936X = "ad936x"
    HACKRF = "hackrf"
    RTL_SDR = "rtl_sdr"
    ADRV9009_ZYNQ = "adrv9009_zynq"
    TINYSA = "tinysa"
    GENERIC_IQ = "generic_iq"
    GENERIC_INSTRUMENT = "generic_instrument"


class AcquisitionKind(StrEnum):
    COMPLEX_IQ = "complex_iq"
    SPECTRUM_TRACE = "spectrum_trace"


class CapabilityField(StrEnum):
    MODEL = "model"
    TRANSPORT = "transport"
    ACQUISITION_KIND = "acquisition_kind"
    TUNING_RANGE = "tuning_range"
    SAMPLE_RATE_RANGE = "sample_rate_range"
    ANALOG_BANDWIDTH_RANGE = "analog_bandwidth_range"
    GAIN_RANGE = "gain_range"
    RX_CHANNEL_COUNT = "rx_channel_count"
    SHARED_RX_LO = "shared_rx_lo"
    RAW_IQ = "raw_iq"
    HARDWARE_TIMESTAMP = "hardware_timestamp"
    HARDWARE_OVERFLOW_COUNTER = "hardware_overflow_counter"
    RUNTIME_CONTROL_CONTRACT = "runtime_control_contract"


class CapabilityEvidenceOrigin(StrEnum):
    RUNTIME_READBACK = "runtime_readback"
    RUNTIME_TOPOLOGY = "runtime_topology"
    VENDOR_DECLARATION = "vendor_declaration"
    USER_DECLARATION = "user_declaration"
    UNKNOWN = "unknown"


class CapabilityTransport(StrEnum):
    USB = "usb"
    ETHERNET = "ethernet"
    PCIE = "pcie"
    MANUAL_INSTRUMENT = "manual_instrument"
    REPLAY = "replay"


class AdapterRuntimeAvailability(StrEnum):
    """Observed contract surface, NOT hardware support or package acceptance."""

    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    AVAILABLE = "available"


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be text")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _opaque_identifier(value: object, label: str) -> str:
    normalized = _required_text(value, label)
    if len(normalized) > 128:
        raise ValueError(f"{label} must be at most 128 characters")
    if normalized.startswith("sha256:"):
        digest = normalized[7:]
        if len(digest) == 64 and all(character in "0123456789abcdef" for character in digest):
            return normalized
    if not normalized[0].isalnum() or any(
        not (character.isascii() and (character.isalnum() or character in "._-"))
        for character in normalized
    ):
        raise ValueError(f"{label} must be an opaque identifier, not a URI, path or serialised route")
    return normalized


def stable_identity_key(raw_identity: object) -> str:
    """Hash an adapter-owned stable identity before it enters domain evidence."""

    normalized = _required_text(raw_identity, "raw identity")
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class CapabilityRange:
    minimum: float
    maximum: float
    unit: str

    def __post_init__(self) -> None:
        if isinstance(self.minimum, bool) or isinstance(self.maximum, bool):
            raise TypeError("capability range endpoints must be numeric")
        try:
            minimum = float(self.minimum)
            maximum = float(self.maximum)
        except (TypeError, ValueError) as error:
            raise ValueError("capability range endpoints must be numeric") from error
        if not math.isfinite(minimum) or not math.isfinite(maximum):
            raise ValueError("capability range endpoints must be finite")
        if maximum < minimum:
            raise ValueError("capability range must be ordered")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        object.__setattr__(self, "unit", _required_text(self.unit, "capability range unit"))


@dataclass(frozen=True, slots=True)
class CapabilityEvidence:
    field: CapabilityField
    origin: CapabilityEvidenceOrigin
    reference: str = "not-recorded"

    def __post_init__(self) -> None:
        object.__setattr__(self, "field", CapabilityField(self.field))
        object.__setattr__(self, "origin", CapabilityEvidenceOrigin(self.origin))
        object.__setattr__(
            self,
            "reference",
            _opaque_identifier(self.reference, "capability evidence reference"),
        )


@dataclass(frozen=True, slots=True)
class DeviceCapabilitySnapshot:
    """One immutable low-rate snapshot; construction performs no discovery."""

    device_id: str
    identity_key: str
    label: str
    family: DeviceFamily
    adapter_id: str
    transports: tuple[CapabilityTransport, ...]
    acquisition_kinds: tuple[AcquisitionKind, ...]
    tuning_ranges_hz: tuple[CapabilityRange, ...] = ()
    sample_rate_ranges_hz: tuple[CapabilityRange, ...] = ()
    analog_bandwidth_ranges_hz: tuple[CapabilityRange, ...] = ()
    gain_ranges_db: tuple[CapabilityRange, ...] = ()
    rx_channel_count: int | None = None
    shared_rx_lo: bool | None = None
    raw_iq_available: bool | None = None
    hardware_timestamp_available: bool | None = None
    hardware_overflow_counter_available: bool | None = None
    evidence: tuple[CapabilityEvidence, ...] = ()
    # Adapter-observed discriminator, not parsing the human label.
    model_id: str | None = None
    # Exact adapter-mapped protocol discriminator, not generic runtime availability.
    runtime_control_contract: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", _opaque_identifier(self.device_id, "device_id"))
        identity_key = _opaque_identifier(self.identity_key, "identity_key")
        if not identity_key.startswith("sha256:"):
            raise ValueError("identity_key must be an opaque sha256 digest")
        object.__setattr__(self, "identity_key", identity_key)
        object.__setattr__(self, "adapter_id", _opaque_identifier(self.adapter_id, "adapter_id"))
        label = _required_text(self.label, "label")
        if len(label) > 128 or any(token in label.casefold() for token in ("usb:", "ip:", "\\", "/")):
            raise ValueError("label must be short and must not contain a URI or path")
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "family", DeviceFamily(self.family))
        if self.model_id is not None:
            object.__setattr__(self, "model_id", _opaque_identifier(self.model_id, "model_id"))
        if self.runtime_control_contract is not None:
            object.__setattr__(self, "runtime_control_contract",
                               _opaque_identifier(self.runtime_control_contract, "runtime_control_contract"))
        transports = tuple(CapabilityTransport(item) for item in self.transports)
        acquisitions = tuple(AcquisitionKind(item) for item in self.acquisition_kinds)
        if not transports or len(set(transports)) != len(transports):
            raise ValueError("capability snapshot requires unique transports")
        if not acquisitions or len(set(acquisitions)) != len(acquisitions):
            raise ValueError("capability snapshot requires unique acquisition kinds")
        if self.rx_channel_count is not None:
            if isinstance(self.rx_channel_count, bool) or not isinstance(self.rx_channel_count, int):
                raise ValueError("rx_channel_count must be an integer or unknown")
            if not 1 <= self.rx_channel_count <= 16:
                raise ValueError("rx_channel_count must be in [1, 16]")
        if self.raw_iq_available is True and AcquisitionKind.COMPLEX_IQ not in acquisitions:
            raise ValueError("raw I/Q availability requires complex-I/Q acquisition")
        for name in (
            "shared_rx_lo",
            "raw_iq_available",
            "hardware_timestamp_available",
            "hardware_overflow_counter_available",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be true, false or unknown")
        evidence = tuple(self.evidence)
        if len(evidence) > len(CapabilityField):
            raise ValueError("capability evidence exceeds the finite field set")
        if any(not isinstance(item, CapabilityEvidence) for item in evidence):
            raise ValueError("capability evidence must contain CapabilityEvidence values")
        fields = tuple(item.field for item in evidence)
        if len(set(fields)) != len(fields):
            raise ValueError("capability evidence fields must be unique")
        ranges = (
            ("tuning_ranges_hz", tuple(self.tuning_ranges_hz), "Hz"),
            ("sample_rate_ranges_hz", tuple(self.sample_rate_ranges_hz), "Hz"),
            ("analog_bandwidth_ranges_hz", tuple(self.analog_bandwidth_ranges_hz), "Hz"),
            ("gain_ranges_db", tuple(self.gain_ranges_db), "dB"),
        )
        for name, values, _unit in ranges:
            if any(not isinstance(item, CapabilityRange) for item in values):
                raise ValueError(f"{name} must contain CapabilityRange values")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must contain unique ranges")
            object.__setattr__(self, name, values)
        for _name, values, unit in ranges:
            if any(item.unit != unit for item in values):
                raise ValueError(f"capability range must use {unit}")
        declared_fields = {
            CapabilityField.MODEL: self.model_id is not None,
            CapabilityField.TRANSPORT: bool(transports),
            CapabilityField.ACQUISITION_KIND: bool(acquisitions),
            CapabilityField.TUNING_RANGE: bool(self.tuning_ranges_hz),
            CapabilityField.SAMPLE_RATE_RANGE: bool(self.sample_rate_ranges_hz),
            CapabilityField.ANALOG_BANDWIDTH_RANGE: bool(self.analog_bandwidth_ranges_hz),
            CapabilityField.GAIN_RANGE: bool(self.gain_ranges_db),
            CapabilityField.RX_CHANNEL_COUNT: self.rx_channel_count is not None,
            CapabilityField.SHARED_RX_LO: self.shared_rx_lo is not None,
            CapabilityField.RAW_IQ: self.raw_iq_available is not None,
            CapabilityField.HARDWARE_TIMESTAMP: self.hardware_timestamp_available is not None,
            CapabilityField.HARDWARE_OVERFLOW_COUNTER: self.hardware_overflow_counter_available is not None,
            CapabilityField.RUNTIME_CONTROL_CONTRACT: self.runtime_control_contract is not None,
        }
        admitted_evidence = {
            item.field for item in evidence if item.origin is not CapabilityEvidenceOrigin.UNKNOWN
        }
        missing_evidence = tuple(
            field.value
            for field, declared in declared_fields.items()
            if declared and field not in admitted_evidence
        )
        if missing_evidence:
            raise ValueError("declared capabilities require evidence origins: " + ", ".join(missing_evidence))
        declared = {field for field, is_declared in declared_fields.items() if is_declared}
        orphan_evidence = tuple(
            item.field.value
            for item in evidence
            if item.origin is not CapabilityEvidenceOrigin.UNKNOWN and item.field not in declared
        )
        if orphan_evidence:
            raise ValueError(
                "capability evidence cannot outlive an absent fact: " + ", ".join(orphan_evidence)
            )
        object.__setattr__(self, "transports", transports)
        object.__setattr__(self, "acquisition_kinds", acquisitions)
        object.__setattr__(self, "evidence", evidence)

    def evidence_for(self, field: CapabilityField) -> CapabilityEvidence:
        normalized = CapabilityField(field)
        return next(
            (item for item in self.evidence if item.field is normalized),
            CapabilityEvidence(normalized, CapabilityEvidenceOrigin.UNKNOWN),
        )


@dataclass(frozen=True, slots=True)
class DeviceCapabilityInventory:
    snapshots: tuple[DeviceCapabilitySnapshot, ...]
    maximum_devices: int = 32
    bindings: tuple[DeviceCapabilityBinding, ...] = ()
    runtimes: tuple[AdapterRuntimeSnapshot, ...] = ()

    def __post_init__(self) -> None:
        _validate_inventory_limit(self.maximum_devices)
        snapshots = _bounded_inventory_values(self.snapshots, self.maximum_devices)
        bindings = _bounded_inventory_values(self.bindings, self.maximum_devices)
        runtimes = _bounded_inventory_values(self.runtimes, self.maximum_devices)
        if any(not isinstance(item, DeviceCapabilitySnapshot) for item in snapshots):
            raise ValueError("device capability inventory must contain capability snapshots")
        device_ids = tuple(item.device_id for item in snapshots)
        identity_keys = tuple(item.identity_key for item in snapshots)
        if len(set(device_ids)) != len(device_ids):
            raise ValueError("device capability inventory requires unique device ids")
        if len(set(identity_keys)) != len(identity_keys):
            raise ValueError("device capability inventory requires unique identity keys")
        if any(not isinstance(item, DeviceCapabilityBinding) for item in bindings):
            raise ValueError("inventory bindings must contain DeviceCapabilityBinding values")
        if any(not isinstance(item, AdapterRuntimeSnapshot) for item in runtimes):
            raise ValueError("inventory runtimes must contain AdapterRuntimeSnapshot values")
        if len({item.source_id for item in bindings}) != len(bindings):
            raise ValueError("inventory bindings require unique operational source ids")
        if len({item.adapter_id for item in runtimes}) != len(runtimes):
            raise ValueError("inventory runtimes require unique adapter ids")
        snapshots_by_key = {item.identity_key: item for item in snapshots}
        identities: dict[str, DeviceCalibrationIdentity] = {}
        families: dict[str, DeviceFamily] = {}
        observations: tuple[DeviceCapabilitySnapshot | DeviceCapabilityBinding | AdapterRuntimeSnapshot, ...] = (
            *snapshots, *bindings, *runtimes,
        )
        for item in observations:
            prior_family = families.setdefault(item.adapter_id, item.family)
            if prior_family is not item.family:
                raise ValueError("inventory adapter family facts conflict")
        for binding in bindings:
            if binding.snapshot is None:
                continue  # An operational route is not canonical identity evidence.
            if snapshots_by_key.get(binding.snapshot.identity_key) != binding.snapshot:
                raise ValueError("inventory binding has orphan or conflicting capability facts")
            assert binding.calibration_identity is not None
            prior = identities.setdefault(binding.snapshot.identity_key, binding.calibration_identity)
            if prior != binding.calibration_identity:
                raise ValueError("inventory alias calibration firmware facts conflict")
        object.__setattr__(self, "snapshots", snapshots)
        object.__setattr__(self, "bindings", bindings)
        object.__setattr__(self, "runtimes", runtimes)

    def binding_for_source(self, source_id: str) -> DeviceCapabilityBinding | None:
        normalized = _catalog_source_id(source_id)
        return next((item for item in self.bindings if item.source_id == normalized), None)

    def runtime_for_adapter(self, adapter_id: str) -> AdapterRuntimeSnapshot | None:
        normalized = _opaque_identifier(adapter_id, "adapter_id")
        return next((item for item in self.runtimes if item.adapter_id == normalized), None)


@dataclass(frozen=True, slots=True)
class DeviceCalibrationIdentity:
    """Opaque adapter/device/firmware axes required before absolute units."""

    family: DeviceFamily
    adapter_id: str
    device_identity_key: str
    firmware_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "family", DeviceFamily(self.family))
        object.__setattr__(self, "adapter_id", _opaque_identifier(self.adapter_id, "adapter_id"))
        for name in ("device_identity_key", "firmware_fingerprint"):
            value = _opaque_identifier(getattr(self, name), name)
            if not value.startswith("sha256:"):
                raise ValueError(f"{name} must be an opaque sha256 digest")
            object.__setattr__(self, name, value)

    def signature_fields(self) -> dict[str, str]:
        return {
            "device_family": self.family.value,
            "adapter_id": self.adapter_id,
            "device_identity_key": self.device_identity_key,
            "firmware_fingerprint": self.firmware_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class DeviceCapabilityBinding:
    """Explicit operational→canonical join referencing existing immutable facts.

    Unknown routes remain selectable by their owner but have no stable key or
    calibration identity. Construction does not establish hardware provenance.
    """

    source_id: str
    family: DeviceFamily
    adapter_id: str
    snapshot: DeviceCapabilitySnapshot | None = None
    calibration_identity: DeviceCalibrationIdentity | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _catalog_source_id(self.source_id))
        object.__setattr__(self, "family", DeviceFamily(self.family))
        object.__setattr__(self, "adapter_id", _opaque_identifier(self.adapter_id, "adapter_id"))
        if (self.snapshot is None) != (self.calibration_identity is None):
            raise ValueError("canonical binding requires both capability and calibration identity")
        if self.snapshot is None:
            return
        if not isinstance(self.snapshot, DeviceCapabilitySnapshot):
            raise TypeError("binding requires an existing capability snapshot")
        identity = self.calibration_identity
        if not isinstance(identity, DeviceCalibrationIdentity):
            raise TypeError("binding requires an existing calibration identity")
        if (self.family is not self.snapshot.family or self.adapter_id != self.snapshot.adapter_id
                or identity.family is not self.family or identity.adapter_id != self.adapter_id
                or identity.device_identity_key != self.snapshot.identity_key):
            raise ValueError("binding canonical identity, family and adapter must agree")

    @property
    def identity_key(self) -> str | None:
        return self.snapshot.identity_key if self.snapshot is not None else None


@dataclass(frozen=True, slots=True)
class AdapterRuntimeSnapshot:
    """Low-rate no-load contract observation, distinct from device capabilities.

    AVAILABLE is not RF-path/physical/mode/throughput or frozen-DLL acceptance.
    Missing observation is unknown; never infer availability from a snapshot.
    """

    adapter_id: str
    family: DeviceFamily
    availability: AdapterRuntimeAvailability = AdapterRuntimeAvailability.UNKNOWN
    reference: str = "not-recorded"

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter_id", _opaque_identifier(self.adapter_id, "adapter_id"))
        object.__setattr__(self, "family", DeviceFamily(self.family))
        object.__setattr__(self, "availability", AdapterRuntimeAvailability(self.availability))
        object.__setattr__(self, "reference", _opaque_identifier(self.reference, "runtime reference"))
        if self.availability is not AdapterRuntimeAvailability.UNKNOWN and self.reference == "not-recorded":
            raise ValueError("observed runtime availability requires its own reference")


def _catalog_source_id(value: object) -> str:
    normalized = _required_text(value, "operational source id")
    if (normalized != value or len(normalized) > 128 or not normalized[0].isalnum()
            or normalized.casefold().startswith(("usb:", "ip:", "manual:", "serial:"))
            or any(not (character.isascii() and (character.isalnum() or character in ".:_-"))
                   for character in normalized)):
        raise ValueError("operational source id must be opaque and route-free")
    return normalized


def _validate_inventory_limit(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("maximum_devices must be an integer")
    if not 1 <= value <= 256:
        raise ValueError("maximum_devices must be in [1, 256]")


def _bounded_inventory_values[T](values: Iterable[T], limit: int) -> tuple[T, ...]:
    result = tuple(islice(values, limit + 1))
    if len(result) > limit:
        raise ValueError("device capability inventory exceeds its finite bound")
    return result


def build_device_capability_inventory(
    snapshots: Iterable[DeviceCapabilitySnapshot], *, maximum_devices: int = 32,
    bindings: Iterable[DeviceCapabilityBinding] = (), runtimes: Iterable[AdapterRuntimeSnapshot] = (),
) -> DeviceCapabilityInventory:
    """Freeze already observed facts without importing or calling an adapter."""

    _validate_inventory_limit(maximum_devices)
    return DeviceCapabilityInventory(
        _bounded_inventory_values(snapshots, maximum_devices), maximum_devices,
        _bounded_inventory_values(bindings, maximum_devices),
        _bounded_inventory_values(runtimes, maximum_devices),
    )


def merge_device_capability_inventories(
    inventories: Iterable[DeviceCapabilityInventory], *, maximum_devices: int = 32,
) -> DeviceCapabilityInventory:
    """Merge already owned facts exactly; conflicting/stale facts never win last.

    No factory, discovery, SDK load, request dispatch or transport merge occurs.
    Family providers own alias/provenance validation before this boundary.
    """
    _validate_inventory_limit(maximum_devices)
    snapshots: dict[str, DeviceCapabilitySnapshot] = {}
    bindings: dict[str, DeviceCapabilityBinding] = {}
    runtimes: dict[str, AdapterRuntimeSnapshot] = {}
    for inventory in _bounded_inventory_values(inventories, 256):
        if not isinstance(inventory, DeviceCapabilityInventory):
            raise TypeError("common catalog requires existing capability inventories")
        for item in inventory.snapshots:
            if snapshots.setdefault(item.identity_key, item) != item:
                raise ValueError("common catalog capability facts conflict")
        for binding in inventory.bindings:
            if bindings.setdefault(binding.source_id, binding) != binding:
                raise ValueError("common catalog operational identity facts conflict")
        for runtime in inventory.runtimes:
            if runtimes.setdefault(runtime.adapter_id, runtime) != runtime:
                raise ValueError("common catalog runtime observations conflict")
        if max(len(snapshots), len(bindings), len(runtimes)) > maximum_devices:
            raise ValueError("common catalog exceeds its finite bound")
    return build_device_capability_inventory(snapshots.values(), maximum_devices=maximum_devices,
                                            bindings=bindings.values(), runtimes=runtimes.values())


__all__ = [
    "AcquisitionKind",
    "AdapterRuntimeAvailability",
    "AdapterRuntimeSnapshot",
    "CapabilityEvidence",
    "CapabilityEvidenceOrigin",
    "CapabilityField",
    "CapabilityRange",
    "CapabilityTransport",
    "DeviceCalibrationIdentity",
    "DeviceCapabilityBinding",
    "DeviceCapabilityInventory",
    "DeviceCapabilitySnapshot",
    "DeviceFamily",
    "build_device_capability_inventory",
    "merge_device_capability_inventories",
    "stable_identity_key",
]
