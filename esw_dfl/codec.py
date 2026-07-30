from __future__ import annotations

import base64
import re
import struct
from typing import Any, Iterable

import numpy as np


COMMENT_VALUE_RE = re.compile(r"#([^#]*)#")
ATTR_RE = re.compile(rb'(\w+)="([^"]*)"')

UNIT_LABELS = {
    "FREQ_HZ": "Hz",
    "FREQ_SEC": "s",
    "LEVEL_DBM": "dBm",
    "LEVEL_DB": "dB",
    "LEVEL_DBUV": "dBµV",
    "LEVEL_DBMV": "dBmV",
    "LEVEL_V": "V",
    "LEVEL_A": "A",
    "LEVEL_W": "W",
    "ANGLE_RAD": "rad",
    "COUNT_PCT": "%",
    "LEVEL_PCT": "%",
    "NONE": "",
    "NOT_DEFINED": "",
}


def unit_label(unit_id: str | None) -> str:
    if not unit_id:
        return ""
    return UNIT_LABELS.get(unit_id, unit_id)


def parse_number(text: str) -> int | float | str:
    value = text.strip()
    if not value:
        return ""
    try:
        if not any(char in value.lower() for char in (".", "e")):
            return int(value)
        return float(value)
    except ValueError:
        return value


def scalar_value(encoded: str | None) -> Any:
    """Read R&S scalar values, preferring the human-readable #value# suffix."""
    if encoded is None:
        return None
    match = COMMENT_VALUE_RE.search(encoded)
    if match:
        return parse_number(match.group(1))
    return parse_number(encoded)


def decode_base64_payload(value: str) -> bytes:
    payload = value.split(" #", 1)[0].strip()
    return base64.b64decode(payload, validate=False)


def decode_scalar_double(value: str | None) -> float | None:
    if not value:
        return None
    parsed = scalar_value(value)
    if isinstance(parsed, (int, float)):
        return float(parsed)
    try:
        raw = decode_base64_payload(value)
        if len(raw) >= 8:
            return float(struct.unpack_from("<d", raw)[0])
    except (ValueError, struct.error):
        pass
    return None


def decode_numeric_blocks(
    values: Iterable[str], expected_items: int | None = None
) -> np.ndarray:
    raw = b"".join(decode_base64_payload(value) for value in values if value)
    if not raw:
        return np.empty(0, dtype=np.float32)
    count = expected_items or 0
    if count > 0 and len(raw) >= count * 8 and len(raw) < count * 12:
        result = np.frombuffer(raw, dtype="<f8")
    else:
        result = np.frombuffer(raw, dtype="<f4")
    if expected_items and result.size >= expected_items:
        result = result[:expected_items]
    return result.copy()


def decode_timestamp(value: str | None) -> float | None:
    """Decode the ESW 32-byte spectrogram timestamp to Unix seconds."""
    if not value:
        return None
    try:
        raw = decode_base64_payload(value)
        if len(raw) < 8:
            return None
        fields = struct.unpack("<" + "d" * (len(raw) // 8), raw[: len(raw) // 8 * 8])
        seconds = fields[0]
        fraction = fields[3] if len(fields) >= 4 and 0.0 <= fields[3] < 1.1 else 0.0
        if 946_684_800 <= seconds <= 4_102_444_800:
            return float(seconds + fraction)
    except (ValueError, struct.error):
        return None
    return None


def parse_tag_attributes(tag: bytes) -> dict[str, str]:
    return {
        key.decode("ascii", "replace"): value.decode("utf-8", "replace")
        for key, value in ATTR_RE.findall(tag)
    }

