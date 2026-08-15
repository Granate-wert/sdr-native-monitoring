"""Dry-run-first evidence contract for one bounded tinySA Ultra trace."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..services.tinysa_capability_adapter import TinySaModel
from ..services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollection,
)
from ..services.tinysa_serial_version_probe import observe_tinysa_pnp
from ..services.tinysa_sweep_policy import TINYSA_FIRMWARE_SOURCE_COMMIT
from .evidence_output import EvidenceOutputClaim, verify_json_evidence_output_claim

R11V_TINYSA_PREFLIGHT_SCHEMA = "sdr-native-r11v-tinysa-trace-preflight-v2"
R11V_TINYSA_EVIDENCE_SCHEMA = "sdr-native-r11v-tinysa-trace-evidence-v2"
R11V_TINYSA_CONFIRMATION = "R11V-TINYSA-3100-ZERO-READBACK-UNCHANGED"
R11V_TINYSA_PHYSICAL_EXECUTION_ENABLED = False
R11V_TINYSA_PROFILE_ID = "r11v-tinysa-ultra-fm-3100-zero-readback-unchanged"
R11V_SOURCE_REFERENCE = "r11t-tinysa-ultra-version-observation"
_R11V_FIXED_REQUEST = TinySaScanRawRequest(
    model=TinySaModel.ULTRA,
    start_frequency_hz=87_500_000,
    stop_frequency_hz=108_000_000,
    points=3_100,
    deadline_seconds=120.0,
)


@dataclass(frozen=True, slots=True)
class R11VTinySaProfile:
    request: TinySaScanRawRequest = _R11V_FIXED_REQUEST

    def __post_init__(self) -> None:
        if self.request != _R11V_FIXED_REQUEST:
            raise ValueError("R11-V physical profile is fixed and cannot be overridden")

    def to_json(self) -> dict[str, object]:
        return {
            "profile_id": R11V_TINYSA_PROFILE_ID,
            "model": self.request.model.value,
            "start_frequency_hz": self.request.start_frequency_hz,
            "stop_frequency_hz": self.request.stop_frequency_hz,
            "points": self.request.points,
            "scanraw_option": self.request.option,
            "response_deadline_seconds": self.request.deadline_seconds,
            "sweep_policy": "unchanged",
            "zero_offset_query": "zero ?\\r",
            "zero_offset_required_for_dbm": True,
            "firmware_source_commit": TINYSA_FIRMWARE_SOURCE_COMMIT,
            "unit": "dBm",
            "value_provenance": "device_reported_trace",
            "calibration_provenance": "device_reported_builtin",
        }


def build_r11v_tinysa_preflight(
    port: str,
    source_version_evidence: Path,
) -> dict[str, object]:
    profile = R11VTinySaProfile()
    pnp = observe_tinysa_pnp(port)
    return {
        "schema": R11V_TINYSA_PREFLIGHT_SCHEMA,
        "status": "PHYSICAL_CELL_CONSUMED",
        "physical_execution_enabled": R11V_TINYSA_PHYSICAL_EXECUTION_ENABLED,
        "confirmation": R11V_TINYSA_CONFIRMATION,
        "hypothesis": (
            "one read-only zero-offset query followed by one non-continuous bounded "
            "scanraw request returns exactly one complete device-reported dBm frame "
            "without changing sweep settings and closes its USB-CDC transport"
        ),
        "profile": profile.to_json(),
        "source_evidence": _r11t_binding(source_version_evidence),
        "transport_admission": {
            "kind": "usb_cdc",
            "vendor_id": f"{pnp.vendor_id:04X}",
            "product_id": f"{pnp.product_id:04X}",
            "route_serialized": False,
        },
        "thresholds": {
            "required_complete_frames": 1,
            "required_points": profile.request.points,
            "required_finite_values": profile.request.points,
            "required_clean_close": True,
            "maximum_measurement_commands": 1,
            "required_readback_commands": 1,
            "maximum_retries": 0,
        },
        "execution_policy": {
            "one_confirmed_invocation": True,
            "new_exclusive_output_before_open": True,
            "retry_authority": False,
            "failure_sidecar": "required_after_claim",
        },
        "prohibited": [
            "continuous_scanraw",
            "reset",
            "firmware_or_dfu",
            "persistent_configuration",
            "raw_trace_evidence",
            "raw_iq",
        ],
    }


def validate_r11v_tinysa_preflight(
    payload: object,
    port: str,
    source_version_evidence: Path,
) -> dict[str, object]:
    expected = build_r11v_tinysa_preflight(port, source_version_evidence)
    if payload != expected:
        raise ValueError("R11-V preflight does not match current PnP/source evidence")
    return expected


def r11v_preflight_sha256(path: Path) -> str:
    return _sha256(Path(path))


def build_r11v_tinysa_evidence(
    collection: TinySaTraceCollection,
    *,
    preflight_sha256: str,
) -> dict[str, object]:
    if not isinstance(preflight_sha256, str) or len(preflight_sha256) != 64:
        raise ValueError("R11-V preflight digest is invalid")
    try:
        int(preflight_sha256, 16)
    except ValueError as error:
        raise ValueError("R11-V preflight digest is invalid") from error
    profile = R11VTinySaProfile()
    trace = collection.trace
    if (
        trace.model is not TinySaModel.ULTRA
        or trace.start_frequency_hz != profile.request.start_frequency_hz
        or trace.stop_frequency_hz != profile.request.stop_frequency_hz
        or trace.values_dbm.size != profile.request.points
        or trace.scanraw_zero_offset_db != collection.scanraw_zero_offset_db
        or not collection.port_closed
        or collection.measurement_commands != 1
        or collection.readback_commands != 1
        or collection.retries != 0
    ):
        raise ValueError("R11-V collection does not satisfy the fixed physical profile")
    finite_count = int(np.count_nonzero(np.isfinite(trace.values_dbm)))
    if finite_count != profile.request.points:
        raise ValueError("R11-V trace contains non-finite device values")
    elapsed = float(collection.elapsed_seconds)
    points_per_second = profile.request.points / elapsed if elapsed > 0.0 else 0.0
    if not math.isfinite(points_per_second):
        raise ValueError("R11-V point delivery rate is not finite")
    return {
        "schema": R11V_TINYSA_EVIDENCE_SCHEMA,
        "status": "OBSERVED_WITH_LIMITATIONS",
        "preflight_sha256": preflight_sha256.lower(),
        "profile_id": R11V_TINYSA_PROFILE_ID,
        "source_evidence_reference": R11V_SOURCE_REFERENCE,
        "claim_scope": "one_shot_tinysa_ultra_device_dbm_trace",
        "result": {
            "model": trace.model.value,
            "point_count": int(trace.values_dbm.size),
            "finite_value_count": finite_count,
            "start_frequency_hz": trace.start_frequency_hz,
            "stop_frequency_hz": trace.stop_frequency_hz,
            "unit": trace.unit,
            "value_provenance": trace.value_provenance,
            "calibration_provenance": trace.calibration_provenance,
            "scanraw_zero_offset_db": trace.scanraw_zero_offset_db,
            "sweep_policy": "unchanged",
            "minimum_level_dbm": float(np.min(trace.values_dbm)),
            "maximum_level_dbm": float(np.max(trace.values_dbm)),
            "mean_level_dbm": float(np.mean(trace.values_dbm, dtype=np.float64)),
            "elapsed_seconds": elapsed,
            "observed_points_per_second": points_per_second,
            "complete_frames": 1,
            "port_closed": collection.port_closed,
        },
        "transport_metrics": {
            "command_bytes": collection.command_bytes,
            "response_bytes_read": collection.response_bytes_read,
            "discarded_prefix_bytes": collection.discarded_prefix_bytes,
            "read_calls": collection.read_calls,
            "zero_response_bytes": collection.zero_response_bytes,
            "zero_read_calls": collection.zero_read_calls,
        },
        "hardware_actions": {
            "measurement_commands": collection.measurement_commands,
            "readback_commands": collection.readback_commands,
            "device_scans_requested": collection.device_scans_requested,
            "retries": collection.retries,
            "resets": 0,
            "firmware_writes": 0,
            "persistent_configuration_writes": 0,
        },
        "not_verified": [
            "metrological_accuracy",
            "external_correction",
            "device_internal_sweep_continuity",
            "continuous_trace_rate",
            "ui_rendering",
            "spectrozir_parity",
        ],
    }


def publish_r11v_tinysa_evidence(
    claim: EvidenceOutputClaim,
    payload: dict[str, object],
) -> None:
    _validate_route_free_scalar_evidence(payload)
    verify_json_evidence_output_claim(claim)
    part = claim.destination.with_suffix(claim.destination.suffix + ".part")
    if part.exists():
        raise FileExistsError("R11-V evidence partial output already exists")
    descriptor = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    verify_json_evidence_output_claim(claim)
    os.replace(part, claim.destination)


def _r11t_binding(path: Path) -> dict[str, str]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("R11-T tinySA evidence cannot be read") from error
    if not isinstance(payload, dict):
        raise TypeError("R11-T tinySA evidence must be an object")
    if payload.get("schema") != "sdr-native-tinysa-version-evidence-v1":
        raise ValueError("R11-T tinySA evidence schema is invalid")
    if payload.get("status") != "OBSERVED":
        raise ValueError("R11-T tinySA identity was not observed")
    result = payload.get("result")
    actions = payload.get("hardware_actions")
    if not isinstance(result, dict) or result.get("model") != TinySaModel.ULTRA.value:
        raise ValueError("R11-T evidence does not identify tinySA Ultra")
    firmware_version = result.get("firmware_version")
    if (
        not isinstance(firmware_version, str)
        or TINYSA_FIRMWARE_SOURCE_COMMIT[:7].casefold() not in firmware_version.casefold()
    ):
        raise ValueError("R11-T evidence is not bound to the source-matched firmware")
    if not isinstance(actions, dict) or actions.get("version_commands") != 1:
        raise ValueError("R11-T evidence has invalid identity action accounting")
    if any(
        actions.get(key) != 0
        for key in (
            "measurement_commands",
            "retunes",
            "stream_starts",
            "firmware_writes",
            "resets",
            "retries",
        )
    ):
        raise ValueError("R11-T evidence contains an unexpected physical action")
    return {"schema": str(payload["schema"]), "sha256": _sha256(source)}


def _validate_route_free_scalar_evidence(payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != R11V_TINYSA_EVIDENCE_SCHEMA:
        raise ValueError("R11-V evidence schema is invalid")
    rendered = json.dumps(payload, sort_keys=True).casefold()
    for token in ('"port"', '"route"', '"values_dbm"', '"frequencies_hz"', '"raw_frame"'):
        if token in rendered:
            raise ValueError("R11-V evidence contains a forbidden route or array field")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "R11V_SOURCE_REFERENCE",
    "R11V_TINYSA_CONFIRMATION",
    "R11V_TINYSA_EVIDENCE_SCHEMA",
    "R11V_TINYSA_PHYSICAL_EXECUTION_ENABLED",
    "R11V_TINYSA_PREFLIGHT_SCHEMA",
    "R11V_TINYSA_PROFILE_ID",
    "R11VTinySaProfile",
    "build_r11v_tinysa_evidence",
    "build_r11v_tinysa_preflight",
    "publish_r11v_tinysa_evidence",
    "r11v_preflight_sha256",
    "validate_r11v_tinysa_preflight",
]
