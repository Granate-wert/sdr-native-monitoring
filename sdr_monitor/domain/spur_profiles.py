"""DCSP-03 bounded profile artifacts and diagnostic geometry, NOT suppression.

Reference identity uses the existing processing-policy contract. A matching
artifact/configuration is not proof of an internal spur, hardware readback, an
active owner, or permission to remove a signal. Original native admission stays
closed until candidate/owner algorithms have separate qualification.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any

from .processing_policy import SpurProfileReference


MAX_SPUR_PROFILE_BYTES = 16_384  # Artifact wire bound, NOT owner/RSS reservation.
MAX_SPUR_PROFILE_ZONES = 64  # Version1 limit, independent of measurement masks.
_MAX_HZ = (1 << 53) - 1  # Exact integer boundary for existing double Hz metadata.


def _integer(value: object, name: str, minimum: int = 1, maximum: int = _MAX_HZ) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} requires an exact bounded integer")


def _text(value: object, name: str) -> None:
    if (type(value) is not str or not value or value != value.strip() or len(value) > 128
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError(f"{name} requires canonical bounded text")


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _digest(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class SpurApplicabilityV1:
    """Exact expected configuration signature; never fabricated RF authority.

    Firmware and gain signatures must identify bounded canonical observation
    material. Unknown values cannot be represented as a compatible signature.
    This type does not attest that its caller actually observed those values.
    """

    family: str
    device_identity: str
    receiver: str
    firmware_signature: str
    gain_signature: str
    sample_rate_hz: int
    analog_bandwidth_hz: int
    lo_min_hz: int
    lo_max_hz: int

    def __post_init__(self) -> None:
        for name in ("family", "device_identity", "receiver", "firmware_signature", "gain_signature"):
            _text(getattr(self, name), name)
        if self.family not in ("ad936x", "hackrf", "rtl_sdr"):
            raise ValueError("unsupported spur profile family")
        expected = ("rx1", "rx2") if self.family == "ad936x" else ("single",)
        if self.receiver not in expected:
            raise ValueError("spur profile receiver is incompatible with family")
        # Reuse the existing SHA256 validator; these are signatures, not names.
        SpurProfileReference("applicability", 1, self.firmware_signature, self.gain_signature)
        for name in ("sample_rate_hz", "analog_bandwidth_hz", "lo_min_hz", "lo_max_hz"):
            _integer(getattr(self, name), name)
        if self.lo_max_hz < self.lo_min_hz:
            raise ValueError("invalid spur profile RF interval")

    @property
    def digest(self) -> str:
        return _digest(_canonical(asdict(self)))


@dataclass(frozen=True, slots=True)
class SpurProfileZoneV1:
    """A diagnostic candidate region, not a notch or measured/excluded interval."""

    zone_id: str
    coordinate: str
    start_hz: int
    stop_hz: int
    evidence_kind: str

    def __post_init__(self) -> None:
        _text(self.zone_id, "zone_id")
        if type(self.coordinate) is not str or self.coordinate not in ("baseband", "rf"):
            raise ValueError("explicit RF/baseband coordinate required")
        _integer(self.start_hz, "zone start", -_MAX_HZ)
        _integer(self.stop_hz, "zone stop", -_MAX_HZ)
        if self.stop_hz <= self.start_hz or (self.coordinate == "rf" and self.start_hz < 0):
            raise ValueError("invalid half-open spur zone")
        if type(self.evidence_kind) is not str or self.evidence_kind not in (
                "user_marked", "calibration_capture", "multi_lo_observation"):
            raise ValueError("explicit diagnostic evidence kind required")


@dataclass(frozen=True, slots=True)
class SpurProfileV1:
    profile_id: str
    revision: int
    applicability: SpurApplicabilityV1
    zones: tuple[SpurProfileZoneV1, ...]

    def __post_init__(self) -> None:
        _text(self.profile_id, "profile_id")
        _integer(self.revision, "revision", maximum=(1 << 64) - 1)
        if type(self.applicability) is not SpurApplicabilityV1:
            raise ValueError("typed profile applicability required")
        if (type(self.zones) is not tuple or not 1 <= len(self.zones) <= MAX_SPUR_PROFILE_ZONES
                or any(type(zone) is not SpurProfileZoneV1 for zone in self.zones)
                or len({zone.zone_id for zone in self.zones}) != len(self.zones)):
            raise ValueError("bounded distinct typed profile zones required")
        for zone in self.zones:
            if zone.coordinate == "baseband" and (
                    2 * zone.start_hz < -self.applicability.sample_rate_hz
                    or 2 * zone.stop_hz > self.applicability.sample_rate_hz):
                raise ValueError("baseband zone outside profile sample-rate coverage")
        self.canonical_bytes()  # Enforce escaped wire bound before admission/storage.

    def canonical_bytes(self) -> bytes:
        payload = _canonical({"schema": "sdr-spur-profile", "schema_version": 1,
                              "profile_id": self.profile_id, "revision": self.revision,
                              "applicability": asdict(self.applicability),
                              "zones": [asdict(zone) for zone in self.zones]})
        if len(payload) > MAX_SPUR_PROFILE_BYTES:
            raise ValueError("spur profile exceeds bounded input")
        return payload

    @property
    def reference(self) -> SpurProfileReference:
        return SpurProfileReference(self.profile_id, self.revision, _digest(self.canonical_bytes()),
                                    self.applicability.digest)

    @classmethod
    def from_json(cls, payload: bytes) -> SpurProfileV1:
        if type(payload) is not bytes or not 0 < len(payload) <= MAX_SPUR_PROFILE_BYTES:
            raise ValueError("bounded UTF-8 spur profile required")

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate spur profile key")
                result[key] = value
            return result

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite spur profile constant: {value}")

        try:
            data = json.loads(payload.decode("utf-8"), object_pairs_hook=unique,
                              parse_constant=reject_constant)
        except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
            raise ValueError("invalid spur profile JSON") from error
        fields = {"schema", "schema_version", "profile_id", "revision", "applicability", "zones"}
        if (type(data) is not dict or set(data) != fields or data["schema"] != "sdr-spur-profile"
                or type(data["schema_version"]) is not int or data["schema_version"] != 1):
            raise ValueError("exact spur profile schema1 required")
        app = data["applicability"]
        if type(app) is not dict or set(app) != set(SpurApplicabilityV1.__dataclass_fields__):
            raise ValueError("exact applicability fields required")
        zones = data["zones"]
        if type(zones) is not list or not 1 <= len(zones) <= MAX_SPUR_PROFILE_ZONES:
            raise ValueError("bounded spur profile zone list required")
        if any(type(zone) is not dict or set(zone) != set(SpurProfileZoneV1.__dataclass_fields__)
               for zone in zones):
            raise ValueError("exact spur profile zone fields required")
        return cls(data["profile_id"], data["revision"], SpurApplicabilityV1(**app),
                   tuple(SpurProfileZoneV1(**zone) for zone in zones))


@dataclass(frozen=True, slots=True)
class SpurDiagnosticBinZoneV1:
    zone_id: str
    begin_bin: int
    end_bin: int
    complete_fft_coverage: bool  # Geometry ONLY, not analog usability or RF validity.

    def __post_init__(self) -> None:
        _text(self.zone_id, "zone_id")
        _integer(self.begin_bin, "begin bin", 0, 262143)
        _integer(self.end_bin, "end bin", 1, 262144)
        if self.begin_bin >= self.end_bin or type(self.complete_fft_coverage) is not bool:
            raise ValueError("bounded half-open diagnostic bin interval required")


def project_spur_profile_zones(profile: SpurProfileV1, configuration: SpurApplicabilityV1,
                              *, center_hz: int, fft_size: int) -> tuple[SpurDiagnosticBinZoneV1, ...]:
    """Pure diagnostic preflight. No spectrum mutation, detector or owner authority.

    Half-open fftshift bins, exact rational arithmetic. RF regions stay fixed in
    RF; baseband regions move with each actual LO, never panorama-center guessing.
    Unknown/foreign configuration refuses instead of a permissive family match.
    """
    if type(profile) is not SpurProfileV1 or type(configuration) is not SpurApplicabilityV1:
        raise ValueError("typed profile/configuration required")
    if configuration != profile.applicability:
        raise ValueError("spur profile configuration mismatch")
    _integer(center_hz, "actual LO", configuration.lo_min_hz, configuration.lo_max_hz)
    _integer(fft_size, "FFT size", 256, 262144)
    if fft_size & (fft_size - 1):
        raise ValueError("power-of-two physical FFT required")
    fs = configuration.sample_rate_hz
    result = []
    for zone in profile.zones:
        offset = center_hz if zone.coordinate == "rf" else 0
        # ceil(((f-center)+Fs/2)*N/Fs), without float/half-Hz ambiguity.
        first = -(-((2 * (zone.start_hz - offset) + fs) * fft_size) // (2 * fs))
        last = -(-((2 * (zone.stop_hz - offset) + fs) * fft_size) // (2 * fs))
        begin, end = max(0, first), min(fft_size, last)
        if begin < end:
            complete = (2 * (zone.start_hz - offset) >= -fs
                        and 2 * (zone.stop_hz - offset) <= fs)
            result.append(SpurDiagnosticBinZoneV1(zone.zone_id, begin, end, complete))
    return tuple(result)
