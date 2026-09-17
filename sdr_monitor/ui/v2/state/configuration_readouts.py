"""Explicit distinction between prepared RF values and published readback."""

from ..i18n import text


def configuration_prefix(applied: object) -> str:
    fields = getattr(applied, "readback_fields", ())
    confirmed = all(name in fields for name in ("center_hz", "sample_rate_hz", "gain_db"))
    return text("analyzer.rf_readback_prefix" if confirmed else "analyzer.prepared_prefix")
