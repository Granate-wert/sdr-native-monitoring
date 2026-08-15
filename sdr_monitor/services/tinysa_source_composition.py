"""Route-redacted, source-bound tinySA discovery and composition contracts."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..domain.device_capabilities import stable_identity_key
from .tinysa_capability_adapter import TinySaModel
from .tinysa_serial_trace_collector import TinySaScanRawRequest, TinySaTraceCollection
from .tinysa_serial_version_probe import TinySaVersionObservation
from .tinysa_sweep_settings_controller import (
    TinySaSettingsApplyResult,
    TinySaSweepSettingsPlan,
)

MAX_TINYSA_DISCOVERED_SOURCES = 16
_GENERIC_DISCOVERY_FAILURE = "tinySA source discovery failed closed"
_GENERIC_PROBE_FAILURE = "tinySA source identity verification failed closed"
_GENERIC_COMPOSITION_FAILURE = "tinySA source composition failed closed"


class TinySaIdentityAssurance(StrEnum):
    """How strongly the USB endpoint can be re-identified before each action."""

    USB_SERIAL = "usb_serial"
    USB_LOCATION = "usb_location"
    PNP_ENDPOINT_ONLY = "pnp_endpoint_only_continuity_unverified"


class TinySaSourcePhase(StrEnum):
    READY = "ready"
    DISCOVERED = "discovered"
    SELECTED = "selected"
    VERIFIED = "verified"
    COMPOSED = "composed"
    FAULTED = "faulted"


class TinySaSourceReason(StrEnum):
    DISCOVERY_FAILED = "discovery_failed"
    SOURCE_NOT_FOUND = "source_not_found"
    IDENTITY_PROBE_FAILED = "identity_probe_failed"
    COMPOSITION_FAILED = "composition_failed"


class TinySaSourceCompositionError(RuntimeError):
    """Redacted source workflow failure; routes and vendor details stay private."""


@dataclass(frozen=True, slots=True, repr=False)
class TinySaTransportEndpoint:
    """Backend-private endpoint. It must never enter presenter/UI state."""

    route: str
    identity_key: str
    display_label: str
    assurance: TinySaIdentityAssurance

    def __post_init__(self) -> None:
        if not isinstance(self.route, str) or not self.route or len(self.route) > 32:
            raise ValueError("tinySA endpoint route is invalid")
        if not _is_sha256(self.identity_key):
            raise ValueError("tinySA endpoint identity must be an opaque sha256 digest")
        label = _safe_label(self.display_label)
        object.__setattr__(self, "display_label", label)
        object.__setattr__(self, "assurance", TinySaIdentityAssurance(self.assurance))


@dataclass(frozen=True, slots=True)
class TinySaSourceCandidate:
    """Presenter-safe discovery result with no COM route or USB serial text."""

    source_id: str
    label: str
    identity_assurance: TinySaIdentityAssurance
    identity_verified: bool = False

    def __post_init__(self) -> None:
        if not _is_source_id(self.source_id):
            raise ValueError("tinySA source id is invalid")
        object.__setattr__(self, "label", _safe_label(self.label))
        object.__setattr__(
            self,
            "identity_assurance",
            TinySaIdentityAssurance(self.identity_assurance),
        )
        if self.identity_verified:
            raise ValueError("discovery alone cannot verify a tinySA identity")


@dataclass(frozen=True, slots=True)
class TinySaVerifiedSource:
    """Route-free identity result from one explicit version observation."""

    source_id: str
    identity_key: str
    label: str
    model: TinySaModel
    firmware_fingerprint: str
    identity_assurance: TinySaIdentityAssurance
    continuity_verified: bool = False

    def __post_init__(self) -> None:
        if not _is_source_id(self.source_id):
            raise ValueError("tinySA verified source id is invalid")
        if not _is_sha256(self.identity_key) or not _is_sha256(
            self.firmware_fingerprint
        ):
            raise ValueError("tinySA verified identity must use opaque sha256 values")
        object.__setattr__(self, "label", _safe_label(self.label))
        object.__setattr__(self, "model", TinySaModel(self.model))
        object.__setattr__(
            self,
            "identity_assurance",
            TinySaIdentityAssurance(self.identity_assurance),
        )
        if self.continuity_verified:
            raise ValueError("tinySA PnP/version binding cannot prove device continuity")


class TinySaBoundTraceCollector(Protocol):
    def collect(self, request: TinySaScanRawRequest) -> TinySaTraceCollection: ...


class TinySaBoundSettingsExecutor(Protocol):
    def apply(
        self,
        plan: TinySaSweepSettingsPlan,
        *,
        confirmation: str,
    ) -> TinySaSettingsApplyResult: ...


@dataclass(frozen=True, slots=True, repr=False)
class TinySaComposedSource:
    """One verified source and its two inert, endpoint-revalidating adapters."""

    verified: TinySaVerifiedSource
    collector: TinySaBoundTraceCollector
    settings_executor: TinySaBoundSettingsExecutor

    def __post_init__(self) -> None:
        if not isinstance(self.verified, TinySaVerifiedSource):
            raise TypeError("tinySA composition requires a verified source")
        if not callable(getattr(self.collector, "collect", None)):
            raise TypeError("tinySA composition requires a collector")
        if not callable(getattr(self.settings_executor, "apply", None)):
            raise TypeError("tinySA composition requires a settings executor")


class TinySaSourceBackend(Protocol):
    """Concrete backend seam; construction must remain transport-free."""

    def discover_endpoints(self) -> tuple[TinySaTransportEndpoint, ...]: ...

    def probe_version(
        self, endpoint: TinySaTransportEndpoint
    ) -> TinySaVersionObservation: ...

    def make_collector(
        self,
        endpoint: TinySaTransportEndpoint,
        model: TinySaModel,
    ) -> TinySaBoundTraceCollector: ...

    def make_settings_executor(
        self,
        endpoint: TinySaTransportEndpoint,
    ) -> TinySaBoundSettingsExecutor: ...


@dataclass(frozen=True, slots=True)
class TinySaSourceSnapshot:
    phase: TinySaSourcePhase
    candidates: tuple[TinySaSourceCandidate, ...]
    selected_source_id: str | None
    verified: TinySaVerifiedSource | None
    reason: TinySaSourceReason | None = None

    def __post_init__(self) -> None:
        phase = TinySaSourcePhase(self.phase)
        candidates = tuple(self.candidates)
        if len(candidates) > MAX_TINYSA_DISCOVERED_SOURCES or any(
            not isinstance(item, TinySaSourceCandidate) for item in candidates
        ):
            raise ValueError("tinySA source snapshot candidates are invalid")
        if len({item.source_id for item in candidates}) != len(candidates):
            raise ValueError("tinySA source snapshot contains duplicate identities")
        if self.selected_source_id is not None and self.selected_source_id not in {
            item.source_id for item in candidates
        }:
            raise ValueError("tinySA selected source is absent from discovery")
        if self.verified is not None and self.selected_source_id != self.verified.source_id:
            raise ValueError("tinySA verified source must match selection")
        if phase in (TinySaSourcePhase.READY, TinySaSourcePhase.DISCOVERED):
            if self.selected_source_id is not None or self.verified is not None:
                raise ValueError("tinySA unselected phase cannot retain identity")
        elif phase is TinySaSourcePhase.SELECTED:
            if self.selected_source_id is None or self.verified is not None:
                raise ValueError("tinySA selected phase has invalid state")
        elif phase in (TinySaSourcePhase.VERIFIED, TinySaSourcePhase.COMPOSED) and self.verified is None:
            raise ValueError("tinySA verified/composed phase requires identity")
        object.__setattr__(self, "phase", phase)
        object.__setattr__(self, "candidates", candidates)
        if self.reason is not None:
            object.__setattr__(self, "reason", TinySaSourceReason(self.reason))

    @property
    def can_discover(self) -> bool:
        return self.phase is not TinySaSourcePhase.COMPOSED

    @property
    def can_select(self) -> bool:
        return self.phase in (TinySaSourcePhase.DISCOVERED, TinySaSourcePhase.SELECTED)

    @property
    def can_verify(self) -> bool:
        return self.phase is TinySaSourcePhase.SELECTED

    @property
    def can_compose(self) -> bool:
        return self.phase is TinySaSourcePhase.VERIFIED


class TinySaSourceCompositionService:
    """Discover → select → verify → compose with no implicit transition."""

    def __init__(self, backend: TinySaSourceBackend) -> None:
        required = (
            "discover_endpoints",
            "probe_version",
            "make_collector",
            "make_settings_executor",
        )
        if any(not callable(getattr(backend, name, None)) for name in required):
            raise ValueError("tinySA source backend is incomplete")
        self._backend = backend
        self._lock = threading.RLock()
        self._endpoints: dict[str, TinySaTransportEndpoint] = {}
        self._snapshot = TinySaSourceSnapshot(TinySaSourcePhase.READY, (), None, None)
        self._composed: TinySaComposedSource | None = None

    def current(self) -> TinySaSourceSnapshot:
        with self._lock:
            return self._snapshot

    def discover(self) -> TinySaSourceSnapshot:
        """Explicit inventory only; a correct backend opens no device here."""

        with self._lock:
            if self._snapshot.phase is TinySaSourcePhase.COMPOSED:
                raise TinySaSourceCompositionError(
                    "tinySA composed source must be released by its owner"
                )
        try:
            endpoints = tuple(self._backend.discover_endpoints())
            if len(endpoints) > MAX_TINYSA_DISCOVERED_SOURCES or any(
                not isinstance(item, TinySaTransportEndpoint) for item in endpoints
            ):
                raise ValueError("invalid tinySA discovery result")
            source_pairs = tuple((_source_id(item.identity_key), item) for item in endpoints)
            if len({source_id for source_id, _ in source_pairs}) != len(source_pairs):
                raise ValueError("duplicate tinySA source identity")
            candidates = tuple(
                TinySaSourceCandidate(
                    source_id,
                    endpoint.display_label,
                    endpoint.assurance,
                )
                for source_id, endpoint in source_pairs
            )
        except Exception as error:
            with self._lock:
                self._endpoints = {}
                self._composed = None
                self._snapshot = TinySaSourceSnapshot(
                    TinySaSourcePhase.FAULTED,
                    (),
                    None,
                    None,
                    TinySaSourceReason.DISCOVERY_FAILED,
                )
            raise TinySaSourceCompositionError(_GENERIC_DISCOVERY_FAILURE) from error
        with self._lock:
            self._endpoints = dict(source_pairs)
            self._composed = None
            self._snapshot = TinySaSourceSnapshot(
                TinySaSourcePhase.DISCOVERED,
                candidates,
                None,
                None,
            )
            return self._snapshot

    def select(self, source_id: str) -> TinySaSourceSnapshot:
        """Select from the latest inventory without opening or probing a port."""

        with self._lock:
            if self._snapshot.phase not in (
                TinySaSourcePhase.DISCOVERED,
                TinySaSourcePhase.SELECTED,
            ):
                raise TinySaSourceCompositionError(
                    "tinySA source selection requires a fresh discovery"
                )
            endpoint = self._endpoints.get(source_id)
            candidates = self._snapshot.candidates
            if endpoint is None or not _is_source_id(source_id):
                self._snapshot = TinySaSourceSnapshot(
                    TinySaSourcePhase.FAULTED,
                    candidates,
                    None,
                    None,
                    TinySaSourceReason.SOURCE_NOT_FOUND,
                )
                raise TinySaSourceCompositionError("tinySA source selection failed closed")
            self._composed = None
            self._snapshot = TinySaSourceSnapshot(
                TinySaSourcePhase.SELECTED,
                candidates,
                source_id,
                None,
            )
            return self._snapshot

    def verify_selected(self) -> TinySaVerifiedSource:
        """Explicitly issue one version observation for the selected endpoint."""

        with self._lock:
            if self._snapshot.phase is not TinySaSourcePhase.SELECTED:
                raise TinySaSourceCompositionError(
                    "tinySA source verification requires an explicit selection"
                )
            selected = self._snapshot.selected_source_id
            endpoint = self._endpoints.get(selected or "")
            candidates = self._snapshot.candidates
        if endpoint is None or selected is None:
            raise TinySaSourceCompositionError("tinySA source is not selected")
        try:
            observation = self._backend.probe_version(endpoint)
            if not isinstance(observation, TinySaVersionObservation):
                raise TypeError("invalid tinySA version observation")
            verified = TinySaVerifiedSource(
                source_id=selected,
                identity_key=endpoint.identity_key,
                label=_verified_label(observation.model),
                model=observation.model,
                firmware_fingerprint=observation.firmware_fingerprint,
                identity_assurance=endpoint.assurance,
            )
        except Exception as error:
            with self._lock:
                self._snapshot = TinySaSourceSnapshot(
                    TinySaSourcePhase.FAULTED,
                    candidates,
                    selected,
                    None,
                    TinySaSourceReason.IDENTITY_PROBE_FAILED,
                )
            raise TinySaSourceCompositionError(_GENERIC_PROBE_FAILURE) from error
        with self._lock:
            self._snapshot = TinySaSourceSnapshot(
                TinySaSourcePhase.VERIFIED,
                candidates,
                selected,
                verified,
            )
            return verified

    def compose_selected(self) -> TinySaComposedSource:
        """Create inert bound adapters; no endpoint is opened by this method."""

        with self._lock:
            if self._composed is not None:
                return self._composed
            if self._snapshot.phase is not TinySaSourcePhase.VERIFIED:
                raise TinySaSourceCompositionError(
                    "tinySA source composition requires identity verification"
                )
            verified = self._snapshot.verified
            endpoint = self._endpoints.get(self._snapshot.selected_source_id or "")
            candidates = self._snapshot.candidates
        if verified is None or endpoint is None:
            raise TinySaSourceCompositionError("tinySA source identity is not verified")
        try:
            composed = TinySaComposedSource(
                verified,
                self._backend.make_collector(endpoint, verified.model),
                self._backend.make_settings_executor(endpoint),
            )
        except Exception as error:
            with self._lock:
                self._snapshot = TinySaSourceSnapshot(
                    TinySaSourcePhase.FAULTED,
                    candidates,
                    verified.source_id,
                    verified,
                    TinySaSourceReason.COMPOSITION_FAILED,
                )
            raise TinySaSourceCompositionError(_GENERIC_COMPOSITION_FAILURE) from error
        with self._lock:
            self._composed = composed
            self._snapshot = TinySaSourceSnapshot(
                TinySaSourcePhase.COMPOSED,
                candidates,
                verified.source_id,
                verified,
            )
            return composed


def _source_id(identity_key: str) -> str:
    if not _is_sha256(identity_key):
        raise ValueError("tinySA identity key is invalid")
    return f"tinysa-{identity_key[7:23]}"


def _is_source_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("tinysa-")
        and len(value) == 23
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _safe_label(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError("tinySA source label must be normalized text")
    if len(value) > 96 or any(
        token in value.casefold() for token in ("com", "usb:", "serial:", "\\", "/")
    ):
        raise ValueError("tinySA source label must not expose a route or serial")
    return value


def _verified_label(model: TinySaModel) -> str:
    normalized = TinySaModel(model)
    if normalized is TinySaModel.ULTRA:
        return "tinySA Ultra spectrum analyzer"
    return "tinySA spectrum analyzer"


def endpoint_identity_key(identity_material: str) -> str:
    """Hash backend-owned USB identity material before control-plane use."""

    return stable_identity_key(f"tinysa-usb-source|{identity_material}")


__all__ = [
    "MAX_TINYSA_DISCOVERED_SOURCES",
    "TinySaBoundSettingsExecutor",
    "TinySaBoundTraceCollector",
    "TinySaComposedSource",
    "TinySaIdentityAssurance",
    "TinySaSourceBackend",
    "TinySaSourceCandidate",
    "TinySaSourceCompositionError",
    "TinySaSourceCompositionService",
    "TinySaSourcePhase",
    "TinySaSourceReason",
    "TinySaSourceSnapshot",
    "TinySaTransportEndpoint",
    "TinySaVerifiedSource",
    "endpoint_identity_key",
]
