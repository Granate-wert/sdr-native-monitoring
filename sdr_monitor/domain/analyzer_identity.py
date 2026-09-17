"""Measurement identity and coherence checks shared by analyzer consumers.

Unknown producer metadata is represented by ``None``.  It is never promoted to
an arbitrary receiver, epoch or clock domain, and therefore cannot authorize
joining independently published analytical layers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import numpy as np


def _known(value: object | None) -> str | int | None:
    if value is None or isinstance(value, bool):
        return None
    raw = getattr(value, "value", value)
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    text = str(raw).strip()
    return None if not text or text.casefold() == "unknown" else text


@dataclass(frozen=True, slots=True)
class MeasurementIdentity:
    source_id: str | int | None
    session_id: str | int | None
    receiver_id: str | int | None
    acquisition_epoch: str | int | None
    config_generation: str | int | None
    clock_domain: str | int | None
    accumulation_id: str | int | None
    source_frame_sequence: int | None
    unit: str | None
    frequencies_hz: np.ndarray

    @classmethod
    def from_frame(cls, frame: object, *, session_id: object | None = None) -> "MeasurementIdentity":
        frequencies = np.asarray(getattr(frame, "frequencies_hz", ()), dtype=np.float64).reshape(-1)
        frequencies.setflags(write=False)
        sequence = getattr(frame, "source_frame_sequence", getattr(frame, "sequence", None))
        raw_unit = getattr(frame, "unit", getattr(frame, "unit_label", None))
        return cls(
            source_id=_known(getattr(frame, "source_id", None)), session_id=_known(session_id),
            receiver_id=_known(getattr(frame, "receiver_id", None)),
            acquisition_epoch=_known(getattr(frame, "acquisition_epoch", None)),
            config_generation=_known(getattr(frame, "config_generation", None)),
            clock_domain=_known(getattr(frame, "clock_domain", None)),
            accumulation_id=_known(getattr(frame, "accumulation_id", None)),
            source_frame_sequence=(int(sequence) if isinstance(sequence, int) and not isinstance(sequence, bool) else None),
            unit=(str(raw_unit).strip() or None) if raw_unit is not None else None,
            frequencies_hz=frequencies,
        )

    @property
    def producer_known(self) -> bool:
        """Minimum positive evidence for a producer measurement identity.

        RX, acquisition epoch and clock may legitimately be unknown on older
        producers.  Unknown values do not prove a match, but neither are they
        silently converted into a conflict.
        """
        return self.source_id is not None and self.config_generation is not None


def identities_compatible(measurement: MeasurementIdentity, layer: MeasurementIdentity) -> bool:
    """Require explicit producer identity, unit and the same physical centers."""
    if not measurement.producer_known or not layer.producer_known:
        return False
    for name in ("source_id", "receiver_id", "acquisition_epoch", "config_generation", "clock_domain",
                 "accumulation_id"):
        left, right = getattr(measurement, name), getattr(layer, name)
        if left is not None and right is not None and left != right:
            return False
    if measurement.session_id is not None and layer.session_id not in (None, measurement.session_id):
        return False
    if measurement.unit is None or layer.unit != measurement.unit:
        return False
    if measurement.frequencies_hz.shape != layer.frequencies_hz.shape:
        return False
    if not np.array_equal(measurement.frequencies_hz, layer.frequencies_hz):
        return False
    return layer.source_frame_sequence in (None, measurement.source_frame_sequence)


def identities_equal(left: MeasurementIdentity, right: MeasurementIdentity) -> bool:
    """Array-safe exact equality for an injected bundle identity."""
    fields = ("source_id", "session_id", "receiver_id", "acquisition_epoch",
              "config_generation", "clock_domain", "accumulation_id",
              "source_frame_sequence", "unit")
    return all(getattr(left, name) == getattr(right, name) for name in fields) and np.array_equal(
        left.frequencies_hz, right.frequencies_hz,
    )


def matches_active_identity(measurement: MeasurementIdentity, snapshot: object) -> bool:
    """Reject stale frames only where the application publishes an active value."""
    active = {
        "source_id": _known(getattr(snapshot, "active_source_id", None)),
        "session_id": _known(getattr(snapshot, "session_id", None)),
        "receiver_id": _known(getattr(snapshot, "receiver_id", None)),
        "acquisition_epoch": _known(getattr(snapshot, "acquisition_epoch", None)),
        "clock_domain": _known(getattr(snapshot, "clock_domain", None)),
        "config_generation": _known(getattr(snapshot, "active_config_generation", None)),
    }
    for name, expected in active.items():
        actual = getattr(measurement, name)
        if expected is not None and actual is not None and expected != actual:
            return False
    return True


def layer_matches_measurement(measurement: MeasurementIdentity, layer: object) -> bool:
    """Validate a separately published layer without relying on arrival order."""
    if getattr(layer, "producer_identity_available", True) is not True:
        return False
    identity = MeasurementIdentity.from_frame(layer, session_id=getattr(layer, "session_id", None))
    # A native persistence snapshot accumulates through source_frame_sequence;
    # it is not another instantaneous trace. Independent publication cadence
    # means it may legitimately end before the latest spectrum. Do not admit
    # future or unknown endpoints, or relax generic trace/Waterfall matching.
    from .live import LivePersistenceFrame

    if isinstance(layer, LivePersistenceFrame):
        endpoint = identity.source_frame_sequence
        current = measurement.source_frame_sequence
        if endpoint is None or current is None or endpoint > current:
            return False
        identity = replace(identity, source_frame_sequence=current)
    return identities_compatible(measurement, identity)


def persistence_is_pending(measurement: MeasurementIdentity, layer: object) -> bool:
    """Same-measurement histogram arrived before its endpoint spectrum."""
    from .live import LivePersistenceFrame

    if not isinstance(layer, LivePersistenceFrame) or not layer.producer_identity_available:
        return False
    identity = MeasurementIdentity.from_frame(layer, session_id=getattr(layer, "session_id", None))
    endpoint, current = identity.source_frame_sequence, measurement.source_frame_sequence
    return (endpoint is not None and current is not None and endpoint > current
            and identities_compatible(measurement, replace(identity, source_frame_sequence=current)))
