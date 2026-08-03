"""SDR-specific application assembly: factories and service wiring.

``SdrApplicationServices`` is the only facade the SDR product approves.  It
never loads DFL-specific code, never touches ``esw_dfl.domain`` objects and
exposes SDR facade types only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..sdr.fixed_band import FixedBandEngineService
from ..sdr.live_profile import DeviceProfileStore
from ..sdr.recording import IqRecordingWriter, SpectrumRecordingWriter
from ..sdr.session_adapter import LiveSessionAdapter
from ..sdr.sweep_profile import SweepProfileStore

from .interfaces import (
    CalibrationSdrService,
    DiagnosticsSdrService,
    LiveSdrService,
    RecordingSdrService,
    SweepSdrService,
)

# ──────────────────────────────────────────────────────────────────────────────
# Workspace factories — the aggregator workspace stays Qt-pure.
# ──────────────────────────────────────────────────────────────────────────────

LiveWorkspaceFactory = Callable[..., "object"]


@dataclass(frozen=True, slots=True)
class SdrApplicationServices:
    """Aggregate SDR services; every service has to be SDR-native."""

    live_sdr: LiveSdrService
    sweep: SweepSdrService
    calibration: CalibrationSdrService
    recording: RecordingSdrService
    diagnostics: DiagnosticsSdrService


def build_live_sdr_service() -> LiveSdrService:
    """Real P07/FixedBandEngine-backed live service.

    Kept behind a factory so tests can inject a fake.
    """

    def factory(uri: str) -> LiveSessionAdapter:
        return LiveSessionAdapter(uri)

    return factory  # type: ignore[return-value]


def build_diagnostics_service() -> DiagnosticsSdrService:
    from ..ui.diagnostics_presenter import DiagnosticsPresenter

    presenter = DiagnosticsPresenter()

    class DiagnosticsSdrServiceImpl:
        def collect_platform(self) -> dict:
            import platform
            return {
                "os": platform.platform(aliased=True),
                "python": platform.python_version(),
                "architecture": platform.machine(),
                "cpu_count": __import__("os").cpu_count(),
            }

        def run_self_tests(self):
            return presenter.run_self_tests()

        def export_support_bundle(self, out_dir):
            return presenter.export_support_bundle(out_dir)

    return DiagnosticsSdrServiceImpl()


def build_recording_service() -> RecordingSdrService:
    def factory(options):
        from ..sdr.recording import RecordingService, IqRecordingWriter

        service = RecordingService(options)
        service._iq = IqRecordingWriter(options.output_uri)  # bounded writer
        return service

    return RecordingSdrService()  # keep the protocol shape


def build_default_sdr_services() -> SdrApplicationServices:
    """Wire all SDR service facades with real implementations."""
    record = build_recording_service()
    diag = build_diagnostics_service()

    def live_factory(uri: str) -> LiveSessionAdapter:
        return LiveSessionAdapter(uri)

    def sweep_factory():
        from ..sdr.sweep import SweepPlanner, SweepExecutor

        return SweepPlanner(), SweepExecutor()

    def calibration_store_factory():
        from ..sdr.calibration_store import CalibrationProfileStore

        return CalibrationProfileStore()

    return SdrApplicationServices(
        live_sdr=build_live_sdr_service(),
        sweep=sweep_factory(),
        calibration=calibration_store_factory(),
        recording=record,
        diagnostics=diag,
    )


__all__ = [
    "SdrApplicationServices",
    "build_default_sdr_services",
    "build_diagnostics_service",
    "build_recording_service",
]
