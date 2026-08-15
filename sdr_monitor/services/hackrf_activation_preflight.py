"""Identity-bound preflight for the future explicit HackRF native factory.

The service consumes an R11-M capability admission and one temporary,
enumeration-only official-lib observation.  It issues an opaque permit only
when the currently enumerated HackRF One has the same canonical identity as
the admitted snapshot.  It neither opens a HackRF device nor configures,
starts, or receives from it.
"""

from __future__ import annotations

from _thread import LockType
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
import threading

from ..domain import stable_identity_key
from .hackrf_capability_adapter import (
    HACKRF_LIBHACKRF_ADAPTER_ID,
    HackrfBoardKind,
)
from .hackrf_live_admission import (
    HackrfLiveActivationPlan,
    _is_issued_hackrf_live_activation_plan,
)


_PERMIT_ISSUER = object()


@dataclass(frozen=True, slots=True, repr=False)
class HackrfRuntimeIdentityProbe:
    """Minimal, adapter-private identity fact from an enumeration-only port."""

    board_kind: HackrfBoardKind
    serial_words: tuple[int, int, int, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "board_kind", HackrfBoardKind(self.board_kind))
        words = tuple(self.serial_words)
        if len(words) != 4 or any(
            isinstance(word, bool) or not isinstance(word, int) or not 0 <= word <= 0xFFFFFFFF
            for word in words
        ):
            raise ValueError("HackRF serial must contain four unsigned 32-bit words")
        object.__setattr__(self, "serial_words", words)


class HackrfActivationPreflightReason(StrEnum):
    """Finite, redacted failure outcomes before an effecting native factory."""

    PLAN_NOT_ADMITTED = "plan_not_admitted"
    RUNTIME_OBSERVATION = "runtime_observation"
    IDENTITY_MISMATCH = "identity_mismatch"


@dataclass(frozen=True, slots=True, init=False)
class HackrfActivationPermit:
    """Opaque hand-off issued only by a matching current identity observation."""

    plan: HackrfLiveActivationPlan
    _issuer: object = field(repr=False, compare=False)
    _claim_lock: LockType = field(repr=False, compare=False)
    _consumed: bool = field(repr=False, compare=False)

    @classmethod
    def _issue(cls, plan: HackrfLiveActivationPlan) -> HackrfActivationPermit:
        result = object.__new__(cls)
        object.__setattr__(result, "plan", plan)
        object.__setattr__(result, "_issuer", _PERMIT_ISSUER)
        object.__setattr__(result, "_claim_lock", threading.Lock())
        object.__setattr__(result, "_consumed", False)
        return result


@dataclass(frozen=True, slots=True)
class HackrfActivationPreflight:
    """Either one opaque permit or one explicit refusal reason."""

    permit: HackrfActivationPermit | None = None
    reason: HackrfActivationPreflightReason | None = None

    def __post_init__(self) -> None:
        if (self.permit is None) == (self.reason is None):
            raise ValueError("preflight must contain exactly one permit or refusal reason")

    @property
    def accepted(self) -> bool:
        return self.permit is not None


class HackrfRuntimeIdentityPort:
    """Finite enumeration seam; implementations must not open a device."""

    def probe(self) -> HackrfRuntimeIdentityProbe:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def _identity_key(probe: HackrfRuntimeIdentityProbe) -> str:
    if probe.board_kind is not HackrfBoardKind.HACKRF_ONE:
        raise ValueError("unsupported HackRF board kind")
    serial = "|".join(f"{word:08x}" for word in probe.serial_words)
    return stable_identity_key(f"hackrf-one-serial|{serial}")


def _is_issued_hackrf_activation_permit(value: object) -> bool:
    """Internal provenance check used by the subsequent effecting factory."""

    return (
        isinstance(value, HackrfActivationPermit)
        and getattr(value, "_issuer", None) is _PERMIT_ISSUER
        and _is_issued_hackrf_live_activation_plan(value.plan)
        and value.plan.adapter_id == HACKRF_LIBHACKRF_ADAPTER_ID
    )


def _claim_hackrf_activation_permit(value: object) -> bool:
    """Atomically consume a valid permit before one effecting factory call."""

    if not _is_issued_hackrf_activation_permit(value):
        return False
    permit = value
    claim_lock = getattr(permit, "_claim_lock", None)
    if not isinstance(claim_lock, LockType):
        return False
    with claim_lock:
        if getattr(permit, "_consumed", True):
            return False
        object.__setattr__(permit, "_consumed", True)
        return True


class HackrfActivationPreflightService:
    """Match one current official enumeration to an admitted immutable plan."""

    def __init__(self, port_factory: Callable[[], HackrfRuntimeIdentityPort]) -> None:
        if not callable(port_factory):
            raise ValueError("HackRF identity port factory must be callable")
        self._port_factory = port_factory

    def verify(self, plan: HackrfLiveActivationPlan) -> HackrfActivationPreflight:
        """Observe once, always close, and never propagate SDK/route details."""

        if (
            not _is_issued_hackrf_live_activation_plan(plan)
            or plan.adapter_id != HACKRF_LIBHACKRF_ADAPTER_ID
        ):
            return HackrfActivationPreflight(
                reason=HackrfActivationPreflightReason.PLAN_NOT_ADMITTED
            )
        port: HackrfRuntimeIdentityPort | None = None
        probe: HackrfRuntimeIdentityProbe | None = None
        failed = False
        try:
            port = self._port_factory()
            probe = port.probe()
            if not isinstance(probe, HackrfRuntimeIdentityProbe):
                failed = True
        except Exception:
            failed = True
        finally:
            if port is not None:
                try:
                    port.close()
                except Exception:
                    failed = True
        if failed or probe is None:
            return HackrfActivationPreflight(
                reason=HackrfActivationPreflightReason.RUNTIME_OBSERVATION
            )
        try:
            if _identity_key(probe) != plan.identity_key:
                return HackrfActivationPreflight(
                    reason=HackrfActivationPreflightReason.IDENTITY_MISMATCH
                )
        except Exception:
            return HackrfActivationPreflight(
                reason=HackrfActivationPreflightReason.RUNTIME_OBSERVATION
            )
        return HackrfActivationPreflight(permit=HackrfActivationPermit._issue(plan))


__all__ = [
    "HackrfActivationPreflight",
    "HackrfActivationPreflightReason",
    "HackrfActivationPreflightService",
    "HackrfActivationPermit",
    "HackrfRuntimeIdentityPort",
    "HackrfRuntimeIdentityProbe",
]
