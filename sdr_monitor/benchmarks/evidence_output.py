"""Fail-closed output claims for confirmation-gated physical evidence."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

R10D4_EVIDENCE_OUTPUT_CLAIM_SCHEMA = "sdr-native-r10d4-evidence-output-claim-v1"
PHYSICAL_EVIDENCE_FAILURE_SIDECAR_SCHEMA = "sdr-native-physical-evidence-failure-sidecar-v1"
_FAILURE_STAGES = frozenset(
    (
        "native_import",
        "capability_capture",
        "capability_admission",
        "live_execution",
        "publication",
    )
)


@dataclass(frozen=True, slots=True)
class EvidenceOutputClaim:
    destination: Path
    token: str

    def payload(self) -> dict[str, str]:
        return {
            "schema": R10D4_EVIDENCE_OUTPUT_CLAIM_SCHEMA,
            "state": "reserved_before_physical_factory",
            "token": self.token,
        }


def claim_new_json_evidence_output(path: Path) -> EvidenceOutputClaim:
    """Atomically reserve a new physical-evidence destination without overwrite."""

    destination = Path(path)
    if destination.suffix.casefold() != ".json":
        raise ValueError("physical evidence output must use .json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    if part.exists():
        raise FileExistsError("physical evidence partial output already exists")
    claim = EvidenceOutputClaim(destination, secrets.token_hex(16))
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        descriptor = os.open(destination, flags, 0o600)
    except FileExistsError:
        raise FileExistsError("physical evidence output already exists") from None
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(claim.payload(), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return claim


def verify_json_evidence_output_claim(claim: EvidenceOutputClaim) -> None:
    """Refuse publication if the exclusive reservation was changed or removed."""

    try:
        payload = json.loads(claim.destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("physical evidence output claim is missing or invalid") from error
    if payload != claim.payload():
        raise RuntimeError("physical evidence output claim changed before publication")


def write_redacted_failure_sidecar(
    claim: EvidenceOutputClaim,
    *,
    source_evidence_reference: str,
    stage: str,
    failure_class: str,
) -> Path:
    """Publish one terminal route-free failure sidecar without replacing a claim.

    A claimed primary output remains immutable on a physical failure.  The
    sidecar is separately exclusive and records only a finite stage and a
    caller-supplied category, never exception text, route, serial or path.
    """

    if stage not in _FAILURE_STAGES:
        raise ValueError("physical evidence failure stage is unsupported")
    if not source_evidence_reference or source_evidence_reference != source_evidence_reference.strip():
        raise ValueError("physical evidence failure source reference is invalid")
    if not failure_class or failure_class != failure_class.strip():
        raise ValueError("physical evidence failure class is invalid")
    verify_json_evidence_output_claim(claim)
    destination = claim.destination.with_suffix(".failure.json")
    part = destination.with_suffix(destination.suffix + ".part")
    if destination.exists() or part.exists():
        raise FileExistsError("physical evidence failure sidecar already exists")
    payload = {
        "schema": PHYSICAL_EVIDENCE_FAILURE_SIDECAR_SCHEMA,
        "state": "execution_failed_before_final_evidence",
        "source_evidence_reference": source_evidence_reference,
        "stage": stage,
        "failure_class": failure_class,
    }
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    descriptor = os.open(destination, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return destination


def read_redacted_json(path: Path, label: str) -> Any:
    """Read JSON without echoing a private path or operating-system message."""

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"{label} cannot be read") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON") from error


__all__ = [
    "PHYSICAL_EVIDENCE_FAILURE_SIDECAR_SCHEMA",
    "R10D4_EVIDENCE_OUTPUT_CLAIM_SCHEMA",
    "EvidenceOutputClaim",
    "claim_new_json_evidence_output",
    "read_redacted_json",
    "verify_json_evidence_output_claim",
    "write_redacted_failure_sidecar",
]
