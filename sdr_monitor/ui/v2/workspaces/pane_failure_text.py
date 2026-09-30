"""Localized presentation of immutable, finite pane failure codes only."""

from __future__ import annotations

from sdr_monitor.services.pane_resource_diagnostics import PaneResourceFailure

from ..i18n import text


def pane_failure_text(failure: PaneResourceFailure) -> str:
    if type(failure) is not PaneResourceFailure:
        raise TypeError("pane failure text requires fixed scalar codes")
    prefix = "analyzer.independent.failure"
    stage = text(f"{prefix}.stage.{failure.stage.value}")
    reason = text(f"{prefix}.reason.{failure.reason.value}")
    codes = f"{failure.stage.value}/{failure.reason.value}"
    instrument = failure.instrument
    if instrument is not None:
        phase = text(f"{prefix}.instrument_phase.{instrument.phase.value}")
        reported = text(f"{prefix}.instrument_reason.{instrument.reason.value}")
        reason = text(f"{prefix}.instrument", phase=phase, reason=reported)
        codes += f"; {instrument.phase.value}/{instrument.reason.value}"
    return text(f"{prefix}.detail", stage=stage, reason=reason, codes=codes)


__all__ = ["pane_failure_text"]
