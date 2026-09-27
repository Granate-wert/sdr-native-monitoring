"""Scalar control-plane source readout; never formats in a per-FFT adapter."""

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.device_capabilities import DeviceFamily

from ..i18n import text


def source_selection_readout(choice: AnalyzerSourceChoice) -> str:
    runtime = choice.runtime.availability.value if choice.runtime is not None else "unknown"
    identity = text("analyzer.source.identity.observed" if choice.binding.identity_key is not None
                    else "analyzer.source.identity.unverified")
    result = text("analyzer.source.summary", family=choice.family.value,
                  transport=choice.transport_label, runtime=runtime, identity=identity)
    if choice.family is not DeviceFamily.AD936X:
        result += " " + text("analyzer.source.family_path_pending")
    return result


__all__ = ["source_selection_readout"]
