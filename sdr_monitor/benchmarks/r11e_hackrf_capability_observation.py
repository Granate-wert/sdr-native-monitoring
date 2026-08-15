"""Route-free preflight and redacted evidence for finite R11-E HackRF probing."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from typing import Any

from ..services.hackrf_capability_adapter import HackrfCapabilityAdapter


R11E_PREFLIGHT_SCHEMA = "sdr-native-r11e-hackrf-capability-preflight-v1"
R11E_EVIDENCE_SCHEMA = "sdr-native-r11e-hackrf-capability-evidence-v1"


def build_r11e_preflight(runtime_digest: str, source_revision: str) -> dict[str, object]:
    digest = _sha256(runtime_digest, "runtime_digest")
    revision = _sha256(source_revision, "source_revision")
    return {
        "schema": R11E_PREFLIGHT_SCHEMA,
        "execution": "dry_run_no_hardware_access",
        "cell_id": "hackrf-one-usb-a",
        "runtime": {
            "kind": "portable_official_libhackrf",
            "runtime_sha256": digest,
            "official_source_revision": revision,
        },
        "allowed_symbols": [
            "hackrf_init",
            "hackrf_device_list",
            "hackrf_device_list_open",
            "hackrf_board_id_read",
            "hackrf_board_partid_serialno_read",
            "hackrf_version_string_read",
            "hackrf_usb_api_version_read",
            "hackrf_close",
            "hackrf_device_list_free",
            "hackrf_exit",
        ],
        "forbidden_operations": [
            "start RX, TX or Sweep",
            "allocate asynchronous transfers or callbacks",
            "set frequency, sample rate, filter, gain or antenna power",
            "write firmware, CPLD, flash or registers",
            "change the installed Windows USB driver",
            "retry a failed device observation",
            "serialize USB path, serial words, firmware text or native exception",
        ],
        "not_verified": [
            "No DLL load, libhackrf init, device list or device open occurred.",
            "The preflight does not prove physical readiness, RX or throughput.",
        ],
    }


def validate_r11e_preflight(payload: object, runtime_digest: str, source_revision: str) -> None:
    if payload != build_r11e_preflight(runtime_digest, source_revision):
        raise ValueError("R11-E preflight does not exactly match the selected runtime")


def execute_r11e_observation(
    preflight: object,
    adapter: HackrfCapabilityAdapter,
    *,
    runtime_digest: str,
    source_revision: str,
) -> dict[str, object]:
    validate_r11e_preflight(preflight, runtime_digest, source_revision)
    assert isinstance(preflight, dict)
    try:
        observation = adapter.observe()
        snapshot = observation.snapshot
        status = "OBSERVED"
        result: dict[str, object] = {
            "family": snapshot.family.value,
            "adapter_id": snapshot.adapter_id,
            "identity_key": snapshot.identity_key,
            "firmware_fingerprint": observation.calibration_identity.firmware_fingerprint,
            "transports": [value.value for value in snapshot.transports],
            "acquisition_kinds": [value.value for value in snapshot.acquisition_kinds],
            "tuning_ranges_hz": _ranges(snapshot.tuning_ranges_hz),
            "sample_rate_ranges_hz": _ranges(snapshot.sample_rate_ranges_hz),
            "analog_bandwidth_ranges_hz": _ranges(snapshot.analog_bandwidth_ranges_hz),
            "gain_ranges_db": _ranges(snapshot.gain_ranges_db),
            "rx_channel_count": snapshot.rx_channel_count,
            "raw_iq_available": snapshot.raw_iq_available,
            "hardware_timestamp_available": snapshot.hardware_timestamp_available,
            "hardware_overflow_counter_available": snapshot.hardware_overflow_counter_available,
        }
    except Exception:
        status = "NOT_OBSERVED"
        result = {"reason": "read_only_observation_failed_closed"}
    return {
        "schema": R11E_EVIDENCE_SCHEMA,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "execution": "finite_read_only_hackrf_observation",
        "cell_id": "hackrf-one-usb-a",
        "runtime": dict(preflight["runtime"]),
        "status": status,
        "result": result,
        "hardware_actions": {
            "probe_attempts": 1,
            "stream_starts": 0,
            "retunes": 0,
            "firmware_writes": 0,
            "driver_changes": 0,
        },
        "not_verified": [
            "No RX/TX/Sweep, applied-setting, throughput, loss or continuity claim is made.",
            "No calibrated dBm or device-overflow-counter claim is made.",
        ],
    }


def validate_r11e_evidence(payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != R11E_EVIDENCE_SCHEMA:
        raise ValueError("R11-E evidence schema is invalid")
    if set(payload) != {
        "schema",
        "created_at_utc",
        "execution",
        "cell_id",
        "runtime",
        "status",
        "result",
        "hardware_actions",
        "not_verified",
    }:
        raise ValueError("R11-E evidence root fields are invalid")
    if payload.get("execution") != "finite_read_only_hackrf_observation":
        raise ValueError("R11-E execution kind is invalid")
    if payload.get("cell_id") != "hackrf-one-usb-a":
        raise ValueError("R11-E cell identity is invalid")
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != {
        "kind",
        "runtime_sha256",
        "official_source_revision",
    }:
        raise ValueError("R11-E runtime provenance is invalid")
    if runtime.get("kind") != "portable_official_libhackrf":
        raise ValueError("R11-E runtime kind is invalid")
    _sha256(runtime.get("runtime_sha256"), "runtime_sha256")
    _sha256(runtime.get("official_source_revision"), "official_source_revision")
    status = payload.get("status")
    if status not in ("OBSERVED", "NOT_OBSERVED"):
        raise ValueError("R11-E status is invalid")
    actions = payload.get("hardware_actions")
    if not isinstance(actions, dict) or actions != {
        "probe_attempts": 1,
        "stream_starts": 0,
        "retunes": 0,
        "firmware_writes": 0,
        "driver_changes": 0,
    }:
        raise ValueError("R11-E action accounting is invalid")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("R11-E result must be an object")
    if status == "NOT_OBSERVED":
        if result != {"reason": "read_only_observation_failed_closed"}:
            raise ValueError("R11-E failed result is invalid")
    else:
        if set(result) != {
            "family",
            "adapter_id",
            "identity_key",
            "firmware_fingerprint",
            "transports",
            "acquisition_kinds",
            "tuning_ranges_hz",
            "sample_rate_ranges_hz",
            "analog_bandwidth_ranges_hz",
            "gain_ranges_db",
            "rx_channel_count",
            "raw_iq_available",
            "hardware_timestamp_available",
            "hardware_overflow_counter_available",
        }:
            raise ValueError("R11-E observed result fields are invalid")
        if result.get("family") != "hackrf" or result.get("transports") != ["usb"]:
            raise ValueError("R11-E observed family/transport is invalid")
        for name in ("identity_key", "firmware_fingerprint"):
            _sha256(result.get(name), name, prefix=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).casefold()
    if any(marker in encoded for marker in ("usb:", "vid_", "pid_", "instanceid")):
        raise ValueError("R11-E evidence contains a USB route-shaped value")


def write_new_json(path: Path, payload: object) -> None:
    destination = Path(path)
    if destination.suffix.casefold() != ".json":
        raise ValueError("R11-E output must use .json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    if destination.exists() or part.exists():
        raise FileExistsError("R11-E output already exists")
    descriptor = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.link(part, destination)
    part.unlink()


def _sha256(value: object, label: str, *, prefix: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be SHA-256 text")
    candidate = value[7:] if prefix and value.startswith("sha256:") else value
    if not re.fullmatch(r"[0-9a-f]{64}", candidate):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return candidate


def _ranges(values: Any) -> list[dict[str, float | str]]:
    return [
        {"minimum": value.minimum, "maximum": value.maximum, "unit": value.unit}
        for value in values
    ]


__all__ = [
    "R11E_EVIDENCE_SCHEMA",
    "R11E_PREFLIGHT_SCHEMA",
    "build_r11e_preflight",
    "execute_r11e_observation",
    "validate_r11e_evidence",
    "validate_r11e_preflight",
    "write_new_json",
]
