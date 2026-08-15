"""One-command tinySA USB-CDC version observation with no measurement action."""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Protocol

from serial import EIGHTBITS, PARITY_NONE, STOPBITS_ONE, Serial
from serial.tools import list_ports

from .tinysa_capability_adapter import TINYSA_READ_ONLY_ADAPTER_ID, TinySaModel

TINYSA_VERSION_CONFIRMATION = "I CONFIRM TINYSA VERSION READ-ONLY"
TINYSA_VERSION_PREFLIGHT_SCHEMA = "sdr-native-tinysa-version-preflight-v1"
TINYSA_VERSION_EVIDENCE_SCHEMA = "sdr-native-tinysa-version-evidence-v1"
TINYSA_USB_VID = 0x0483
TINYSA_USB_PID = 0x5740
_PORT_PATTERN = re.compile(r"COM(?:[1-9]|[1-9][0-9]|[12][0-9]{2})", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class TinySaPnpObservation:
    port: str
    vendor_id: int
    product_id: int


@dataclass(frozen=True, slots=True)
class TinySaVersionObservation:
    model: TinySaModel
    normalized_version: str
    firmware_fingerprint: str


class TinySaVersionSerialPort(Protocol):
    is_open: bool
    dtr: bool
    rts: bool

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...


def observe_tinysa_pnp(port: str) -> TinySaPnpObservation:
    normalized = _validated_port(port)
    matches = [item for item in list_ports.comports() if item.device.casefold() == normalized.casefold()]
    if len(matches) != 1:
        raise ValueError("tinySA USB CDC candidate is not uniquely present")
    candidate = matches[0]
    if candidate.vid != TINYSA_USB_VID or candidate.pid != TINYSA_USB_PID:
        raise ValueError("USB CDC candidate does not match the tinySA firmware VID/PID")
    return TinySaPnpObservation(normalized.upper(), candidate.vid, candidate.pid)


def build_tinysa_version_preflight(port: str) -> dict[str, object]:
    observed = observe_tinysa_pnp(port)
    return {
        "schema": TINYSA_VERSION_PREFLIGHT_SCHEMA,
        "adapter_id": TINYSA_READ_ONLY_ADAPTER_ID,
        "transport": {
            "kind": "usb_cdc_serial",
            "port": observed.port,
            "vendor_id": f"{observed.vendor_id:04X}",
            "product_id": f"{observed.product_id:04X}",
        },
        "serial_policy": {
            "baud": 115200,
            "data_bits": 8,
            "parity": "none",
            "stop_bits": 1,
            "flow_control": "none",
            "dtr": False,
            "rts": False,
            "break": False,
        },
        "command": "version\\r",
        "command_count": 1,
        "response_limit_bytes": 4096,
        "response_timeout_seconds": 2.0,
        "retry_count": 0,
        "prohibited": [
            "sweep",
            "scan",
            "scanraw",
            "retune",
            "reset",
            "firmware",
            "dfu",
        ],
        "confirmation": TINYSA_VERSION_CONFIRMATION,
    }


def validate_tinysa_version_preflight(payload: object, port: str) -> dict[str, object]:
    expected = build_tinysa_version_preflight(port)
    if payload != expected:
        raise ValueError("tinySA version preflight does not match current PnP evidence")
    return expected


def _validated_port(port: str) -> str:
    if not isinstance(port, str) or not _PORT_PATTERN.fullmatch(port):
        raise ValueError("tinySA serial port must be one normalized COM name")
    return port.upper()


def _make_serial(port: str) -> TinySaVersionSerialPort:
    serial_port = Serial(
        port=None,
        baudrate=115200,
        bytesize=EIGHTBITS,
        parity=PARITY_NONE,
        stopbits=STOPBITS_ONE,
        timeout=0.05,
        write_timeout=1.0,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )
    serial_port.dtr = False
    serial_port.rts = False
    serial_port.port = _validated_port(port)
    return serial_port


def _read_bounded_response(
    serial_port: TinySaVersionSerialPort,
    *,
    deadline_seconds: float,
    limit: int,
) -> bytes:
    deadline = time.monotonic() + deadline_seconds
    response = bytearray()
    while time.monotonic() < deadline:
        chunk = serial_port.read(min(256, limit - len(response) + 1))
        if chunk:
            response.extend(chunk)
            if len(response) > limit:
                raise ValueError("tinySA version response exceeded the fixed bound")
            if b"ch> " in response:
                break
        else:
            time.sleep(0.01)
    return bytes(response)


def _parse_version(response: bytes) -> TinySaVersionObservation:
    if not response or len(response) > 4096:
        raise ValueError("tinySA version response is empty or oversized")
    try:
        decoded = response.decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("tinySA version response is not bounded ASCII") from error
    cleaned = decoded.replace("\r", "\n").replace("ch> ", "")
    lines = [line.strip() for line in cleaned.split("\n") if line.strip()]
    lines = [line for line in lines if line.casefold() != "version"]
    normalized = " | ".join(lines)
    if not normalized or len(normalized) > 256:
        raise ValueError("tinySA version text is not bounded")
    folded = normalized.casefold()
    if any(token in folded for token in ("tinysa4", "tiny sa4", "ultra", "zs405", "zs406", "zs407")):
        model = TinySaModel.ULTRA
    elif "tinysa" in folded or "tiny sa" in folded:
        model = TinySaModel.BASIC
    else:
        raise ValueError("tinySA model cannot be classified from version response")
    return TinySaVersionObservation(
        model=model,
        normalized_version=normalized,
        firmware_fingerprint="sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def probe_tinysa_version(
    port: str,
    *,
    serial_factory: object | None = None,
) -> TinySaVersionObservation:
    normalized = _validated_port(port)
    serial_port = (
        _make_serial(normalized)
        if serial_factory is None
        else serial_factory(normalized)  # type: ignore[operator]
    )
    if not hasattr(serial_port, "open"):
        raise ValueError("tinySA serial factory returned an invalid port")
    try:
        serial_port.dtr = False
        serial_port.rts = False
        serial_port.open()
        # Drain only a bounded startup banner/prompt. No command is sent here.
        startup_banner = _read_bounded_response(
            serial_port, deadline_seconds=0.25, limit=4096
        )
        del startup_banner
        written = serial_port.write(b"version\r")
        if written != len(b"version\r"):
            raise ValueError("tinySA version command was not written completely")
        serial_port.flush()
        return _parse_version(
            _read_bounded_response(serial_port, deadline_seconds=2.0, limit=4096)
        )
    finally:
        if getattr(serial_port, "is_open", False):
            serial_port.close()


def tinysa_version_evidence(
    pnp: TinySaPnpObservation, observation: TinySaVersionObservation
) -> dict[str, object]:
    return {
        "schema": TINYSA_VERSION_EVIDENCE_SCHEMA,
        "status": "OBSERVED",
        "adapter_id": TINYSA_READ_ONLY_ADAPTER_ID,
        "transport": {
            "kind": "usb_cdc_serial",
            "port": pnp.port,
            "vendor_id": f"{pnp.vendor_id:04X}",
            "product_id": f"{pnp.product_id:04X}",
        },
        "result": {
            "family": "tinysa",
            "model": observation.model.value,
            "firmware_version": observation.normalized_version,
            "firmware_fingerprint": observation.firmware_fingerprint,
            "acquisition_kind": "spectrum_trace",
            "raw_iq_available": False,
            "reported_unit": "dBm",
            "device_calibration_provenance": "device_reported_builtin_unverified",
            "external_correction": "optional_separate_profile",
        },
        "hardware_actions": {
            "version_commands": 1,
            "measurement_commands": 0,
            "retunes": 0,
            "stream_starts": 0,
            "firmware_writes": 0,
            "resets": 0,
            "retries": 0,
        },
        "not_verified": [
            "metrological_accuracy",
            "external_correction",
            "sweep_trace",
            "update_rate",
        ],
    }


__all__ = [
    "TINYSA_VERSION_CONFIRMATION",
    "TINYSA_VERSION_EVIDENCE_SCHEMA",
    "TINYSA_VERSION_PREFLIGHT_SCHEMA",
    "TinySaPnpObservation",
    "TinySaVersionObservation",
    "build_tinysa_version_preflight",
    "observe_tinysa_pnp",
    "probe_tinysa_version",
    "tinysa_version_evidence",
    "validate_tinysa_version_preflight",
]
