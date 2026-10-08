"""DCSP-01/02 pure request/receipt boundary; no SDK, DSP or application authority.

Recipe1 uses only integers, booleans and bounded text: canonical JSON has sorted
keys, ASCII escapes, no whitespace and no float spelling ambiguity. Profile
references identify a separately verified artifact, never authorize filtering.
Native wiring/support, profile algorithms and RF validity remain separate gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from math import isfinite
import re
from typing import Any

from .receiver_topology import ReceiverChain


PROCESSING_CONTRACT_VERSION = 1
MAX_POLICY_BYTES = 16_384  # Wire-input bound, NOT a native working-set/RSS promise.
MAX_VALIDITY_ZONES = 64  # Outcome metadata only; native reservation still required.
# Existing wire-schema5 bit, not a new enum. Compatibility asserted against the
# canonical Python/C++ definitions in tests, without importing optional native.
DC_REMOVED_MASK = 1 << 9
_UINT64_MAX = (1 << 64) - 1
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _text(value: object, name: str, maximum: int = 128) -> None:
    if (type(value) is not str or not value or value != value.strip() or len(value) > maximum
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError(f"{name} must be bounded canonical text")


def _uint(value: object, name: str, maximum: int = _UINT64_MAX, minimum: int = 1) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an exact bounded integer")


def _bool(value: object, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be an exact boolean")


def _digest(value: object, name: str) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA256 digest")


class HostDcMode(StrEnum):
    OFF = "off"
    BLOCK_MEAN = "block_mean_v1"


class HostSpurMode(StrEnum):
    OFF = "off"
    CANDIDATES = "candidates_v1"
    PROFILE_NOTCH = "profile_notch_v1"


@dataclass(frozen=True, slots=True)
class SpurProfileReference:
    """Identity only; DCSP-03 must verify full profile/configuration applicability."""

    profile_id: str
    revision: int
    digest: str
    applicability_digest: str

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        _uint(self.revision, "profile revision")
        _digest(self.digest, "profile digest")
        _digest(self.applicability_digest, "applicability digest")


@dataclass(frozen=True, slots=True)
class SdrProcessingPolicyV1:
    dc_mode: HostDcMode = HostDcMode.OFF
    spur_mode: HostSpurMode = HostSpurMode.OFF
    spur_profile: SpurProfileReference | None = None
    compare_raw: bool = False

    def __post_init__(self) -> None:
        if type(self.dc_mode) is not HostDcMode or type(self.spur_mode) is not HostSpurMode:
            raise ValueError("typed host algorithms required")
        _bool(self.compare_raw, "compare_raw")
        if self.spur_profile is not None and type(self.spur_profile) is not SpurProfileReference:
            raise ValueError("typed immutable profile reference required")
        if (self.spur_mode is HostSpurMode.OFF) != (self.spur_profile is None):
            raise ValueError("non-OFF spur request requires an explicit profile; OFF forbids one")

    @property
    def is_off(self) -> bool:
        return self.dc_mode is HostDcMode.OFF and self.spur_mode is HostSpurMode.OFF and not self.compare_raw

    def canonical_bytes(self) -> bytes:
        profile = self.spur_profile
        payload = {
            "schema": "sdr-processing-policy", "schema_version": PROCESSING_CONTRACT_VERSION,
            "dc_mode": self.dc_mode.value, "spur_mode": self.spur_mode.value,
            "compare_raw": self.compare_raw,
            "spur_profile": None if profile is None else {
                "profile_id": profile.profile_id, "revision": profile.revision,
                "digest": profile.digest, "applicability_digest": profile.applicability_digest,
            },
        }
        result = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                            allow_nan=False).encode("utf-8")
        if len(result) > MAX_POLICY_BYTES:
            raise ValueError("processing policy exceeds wire bound")
        return result

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_json(cls, payload: bytes) -> SdrProcessingPolicyV1:
        if type(payload) is not bytes or not 0 < len(payload) <= MAX_POLICY_BYTES:
            raise ValueError("bounded UTF-8 bytes required")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate processing policy key")
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite JSON constant {value}")

        try:
            value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object,
                               parse_constant=reject_constant)
        except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
            raise ValueError("invalid processing policy JSON") from error
        expected = {"schema", "schema_version", "dc_mode", "spur_mode", "spur_profile", "compare_raw"}
        if type(value) is not dict or set(value) != expected:
            raise ValueError("exact processing policy fields required")
        if value["schema"] != "sdr-processing-policy" or type(value["schema"]) is not str:
            raise ValueError("unknown processing policy schema")
        _uint(value["schema_version"], "schema_version", PROCESSING_CONTRACT_VERSION,
              PROCESSING_CONTRACT_VERSION)
        if type(value["dc_mode"]) is not str or type(value["spur_mode"]) is not str:
            raise ValueError("wire algorithms must be strings")
        profile = value["spur_profile"]
        if profile is not None:
            if type(profile) is not dict or set(profile) != {
                    "profile_id", "revision", "digest", "applicability_digest"}:
                raise ValueError("exact profile reference fields required")
            profile = SpurProfileReference(**profile)
        return cls(HostDcMode(value["dc_mode"]), HostSpurMode(value["spur_mode"]),
                   profile, value["compare_raw"])


@dataclass(frozen=True, slots=True)
class DspProcessingRecipeObservationV1:
    """Observed DSP stage only; no owner, RX, epoch or revision authority.

    Only the two qualified CPU recipes are representable here. Creating an
    observation does not admit a request or prove hardware DC tracking. Shared
    mapper instances cache canonical text/digest once, not for every frame.
    """

    dc_mode: HostDcMode
    policy: SdrProcessingPolicyV1 = field(init=False)
    canonical_policy_json: str = field(init=False)
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.dc_mode) is not HostDcMode:
            raise ValueError("typed observed DC algorithm required")
        policy = SdrProcessingPolicyV1(dc_mode=self.dc_mode)
        payload = policy.canonical_bytes()
        object.__setattr__(self, "policy", policy)
        object.__setattr__(self, "canonical_policy_json", payload.decode("ascii"))
        object.__setattr__(self, "policy_digest", "sha256:" + hashlib.sha256(payload).hexdigest())

    @property
    def schema_version(self) -> int:
        return PROCESSING_CONTRACT_VERSION

    @property
    def scope(self) -> str:
        return "dsp_recipe_only"

    @property
    def whole_frame_modified(self) -> bool:
        return self.dc_mode is HostDcMode.BLOCK_MEAN

    @property
    def hardware_dc_tracking(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class NativeProcessingSupport:
    """Pure capability declaration, not a probe or proof of applied processing."""

    contract_version: int | None = None
    dc_modes: frozenset[HostDcMode] = frozenset()
    spur_modes: frozenset[HostSpurMode] = frozenset()
    comparison: bool = False

    def __post_init__(self) -> None:
        if self.contract_version is not None:
            _uint(self.contract_version, "native processing version")
        for modes, kind in ((self.dc_modes, HostDcMode), (self.spur_modes, HostSpurMode)):
            if type(modes) is not frozenset or any(type(mode) is not kind for mode in modes):
                raise ValueError("typed immutable native mode set required")
        _bool(self.comparison, "comparison support")


def validate_processing_support(policy: SdrProcessingPolicyV1, support: NativeProcessingSupport) -> None:
    """Capability check only, NOT profile/owner admission or application authority.

    No state mutation. Unsupported non-OFF requests cannot silently fall back.
    DCSP-03 must separately verify the referenced profile's contents/applicability.
    """
    if type(policy) is not SdrProcessingPolicyV1 or type(support) is not NativeProcessingSupport:
        raise ValueError("typed processing request and support required")
    if policy.is_off:
        return  # Legacy OFF operation is unchanged; this creates NO receipt.
    if support.contract_version != PROCESSING_CONTRACT_VERSION:
        raise ValueError("native processing contract unavailable or incompatible")
    if policy.dc_mode is not HostDcMode.OFF and policy.dc_mode not in support.dc_modes:
        raise ValueError("requested native DC mode unsupported")
    if policy.spur_mode is not HostSpurMode.OFF and policy.spur_mode not in support.spur_modes:
        raise ValueError("requested native spur mode unsupported")
    if policy.compare_raw and not support.comparison:
        raise ValueError("raw/processed comparison unavailable")


@dataclass(frozen=True, slots=True)
class ProcessingFrameKey:
    resource_id: str
    source_id: str
    receiver: ReceiverChain
    config_generation: int
    acquisition_epoch: int
    grid_digest: str
    unit: str
    normalization_version: str
    backend_id: str
    actual_lo_hz: float
    sample_rate_hz: float
    analog_bandwidth_hz: float

    def __post_init__(self) -> None:
        for name in ("resource_id", "source_id", "normalization_version", "backend_id"):
            _text(getattr(self, name), name)
        if type(self.receiver) is not ReceiverChain:
            raise ValueError("one typed receiver required, never BOTH or a device-name suffix")
        _uint(self.config_generation, "config generation")
        _uint(self.acquisition_epoch, "acquisition epoch")
        _digest(self.grid_digest, "grid digest")
        if type(self.unit) is not str or self.unit not in {"dBFS/bin", "dBFS/Hz"}:
            raise ValueError("native digital power unit required")
        for name in ("actual_lo_hz", "sample_rate_hz", "analog_bandwidth_hz"):
            value = getattr(self, name)
            if type(value) is not float or not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite positive binary64")


class ValidityReason(StrEnum):
    MODIFIED = "modified"
    EXCLUDED = "excluded"
    UNKNOWN = "unknown"
    MEASUREMENT_LIMITED = "measurement_limited"


@dataclass(frozen=True, slots=True)
class ProcessingZone:
    """Absolute RF interval; an excluded interval is NOT measured zero power."""

    start_hz: float
    stop_hz: float
    reason: ValidityReason

    def __post_init__(self) -> None:
        if any(type(v) is not float or not isfinite(v) for v in (self.start_hz, self.stop_hz)):
            raise ValueError("finite binary64 zone coordinates required")
        if not 0 <= self.start_hz < self.stop_hz or type(self.reason) is not ValidityReason:
            raise ValueError("ordered RF interval and typed validity reason required")


@dataclass(frozen=True, slots=True)
class AppliedProcessingContextV1:
    """Value receipt only. Owner authority/native producer verification is external."""

    policy_digest: str
    processing_revision: int
    frame_key: ProcessingFrameKey
    dc_mode: HostDcMode
    spur_mode: HostSpurMode
    profile_digest: str | None
    whole_frame_modified: bool
    zones: tuple[ProcessingZone, ...]
    native_quality_flags: int
    hardware_dc_tracking: bool | None = None
    comparison_applied: bool = False

    def __post_init__(self) -> None:
        _digest(self.policy_digest, "policy digest")
        _uint(self.processing_revision, "processing revision")
        if (type(self.frame_key) is not ProcessingFrameKey or type(self.dc_mode) is not HostDcMode
                or type(self.spur_mode) is not HostSpurMode):
            raise ValueError("typed applied processing context required")
        if self.profile_digest is not None:
            _digest(self.profile_digest, "profile digest")
        if (self.spur_mode is HostSpurMode.OFF) != (self.profile_digest is None):
            raise ValueError("applied profile digest inconsistent with spur mode")
        _bool(self.whole_frame_modified, "whole_frame_modified")
        _bool(self.comparison_applied, "comparison_applied")
        if (type(self.zones) is not tuple or len(self.zones) > MAX_VALIDITY_ZONES
                or any(type(zone) is not ProcessingZone for zone in self.zones)):
            raise ValueError("bounded immutable validity zones required")
        _uint(self.native_quality_flags, "native quality flags", (1 << 32) - 1, 0)
        if self.hardware_dc_tracking is not None:
            _bool(self.hardware_dc_tracking, "hardware_dc_tracking")
        filtered = self.dc_mode is not HostDcMode.OFF or self.spur_mode is HostSpurMode.PROFILE_NOTCH
        if filtered != self.whole_frame_modified:
            raise ValueError("native filtering requires explicit whole-frame modification")
        if self.spur_mode is HostSpurMode.PROFILE_NOTCH and not self.zones:
            raise ValueError("profile notch requires declared affected RF intervals")
        # Quality bits are cumulative: an OFF stage may inherit DcRemoved.
        # Never erase ingress evidence or infer the current recipe from a bit.
        if self.dc_mode is HostDcMode.BLOCK_MEAN and not self.native_quality_flags & DC_REMOVED_MASK:
            raise ValueError("DC_REMOVED missing for actual BlockMean algorithm")


def validate_processing_receipt(policy: SdrProcessingPolicyV1, expected_key: ProcessingFrameKey,
                                expected_revision: int, receipt: AppliedProcessingContextV1) -> None:
    """Exact metadata join; cannot substitute for the existing owner's admission."""
    if (type(policy) is not SdrProcessingPolicyV1 or type(expected_key) is not ProcessingFrameKey
            or type(receipt) is not AppliedProcessingContextV1):
        raise ValueError("typed processing receipt join required")
    _uint(expected_revision, "expected processing revision")
    expected_profile = policy.spur_profile.digest if policy.spur_profile is not None else None
    if (receipt.policy_digest != policy.digest or receipt.frame_key != expected_key
            or receipt.processing_revision != expected_revision or receipt.dc_mode is not policy.dc_mode
            or receipt.spur_mode is not policy.spur_mode or receipt.profile_digest != expected_profile
            or receipt.comparison_applied != policy.compare_raw):
        raise ValueError("processing receipt does not belong to this policy/frame/revision")


@dataclass(frozen=True, slots=True)
class LegacyProcessingObservation:
    """Recipe remains UNKNOWN even when a legacy quality bit reports DC removal."""

    native_quality_flags: int | None

    def __post_init__(self) -> None:
        if self.native_quality_flags is not None:
            _uint(self.native_quality_flags, "legacy quality flags", (1 << 32) - 1, 0)

    @property
    def dc_removed_reported(self) -> bool | None:
        return (None if self.native_quality_flags is None
                else bool(self.native_quality_flags & DC_REMOVED_MASK))

    @property
    def known_recipe(self) -> None:
        return None


def policy_from_saved_settings(payload: bytes | None) -> SdrProcessingPolicyV1:
    """Absent saved setting -> host OFF request, not historical file provenance."""
    return SdrProcessingPolicyV1() if payload is None else SdrProcessingPolicyV1.from_json(payload)
