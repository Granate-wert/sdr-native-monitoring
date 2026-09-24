"""Explicit distinction between prepared RF values and published readback."""

from ..i18n import text


def configuration_prefix(applied: object) -> str:
    fields = getattr(applied, "readback_fields", ())
    confirmed = all(name in fields for name in ("center_hz", "sample_rate_hz", "gain_db"))
    return text("analyzer.rf_readback_prefix" if confirmed else "analyzer.prepared_prefix")


def rf_bandwidth_summary(value_hz: float | None) -> str:
    """Do not infer an RF passband from the independently applied sample rate."""
    if value_hz is None:
        return text("analyzer.rf_bandwidth.unknown")
    return text("analyzer.rf_bandwidth.value", value=f"{value_hz / 1e6:g}")
