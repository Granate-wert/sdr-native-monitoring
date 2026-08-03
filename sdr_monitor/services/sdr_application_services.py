"""Standalone service assembly with no dependency on the DFL product tree."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .interfaces import CalibrationSdrService, DiagnosticsSdrService, LiveSdrService, RecordingSdrService, SweepSdrService


class UnavailableLiveService:
    """Safe pre-S05 live service: no hardware I/O occurs until a device is selected."""
    def __init__(self) -> None:
        self._running = False

    def open_live(self, config: Any) -> None:
        self._running = True

    def close_live(self) -> None:
        self._running = False

    def poll_frames(self) -> list[Any]:
        return []

    def poll_live_metrics(self, timeout_s: float) -> dict[str, Any]:
        return {"running": self._running, "device": None, "timeout_s": timeout_s}

    def is_running(self) -> bool:
        return self._running

    def stop_and_wait(self, timeout_s: float) -> None:
        self._running = False


class UnavailableSweepService:
    def plan(self, config: Any) -> dict[str, Any]:
        return {"config": config, "segments": ()}

    def execute(self, config: Any, progress: Any) -> dict[str, Any]:
        progress({"percent": 0, "state": "not_configured"})
        return {"status": "not_configured"}

    def cancel(self) -> None:
        return None

    def close(self) -> None:
        return None


class InMemoryCalibrationService:
    def list_profiles(self) -> tuple[Any, ...]:
        return ()

    def compare_applicability(self, profile: Any, current: Any) -> bool:
        return False

    def finalize_profile(self, profile: Any) -> Any:
        return profile

    def preview_csv(self, data: str) -> tuple[str, ...]:
        return tuple(line for line in data.splitlines() if line.strip())


class InMemoryRecordingService:
    def __init__(self) -> None:
        self._active = False

    def start(self, options: Any) -> None:
        self._active = True

    def stop(self) -> dict[str, bool]:
        self._active = False
        return {"stopped": True}

    def health(self) -> dict[str, Any]:
        return {"recording": self._active, "queue_depth": 0, "drops": 0}

    def recover_partial(self, uri: Any) -> dict[str, Any]:
        return {"uri": uri, "recovered": False}

    def open_replay(self, uri: Any, *, kind: Any) -> dict[str, Any]:
        return {"uri": uri, "kind": kind}

    def seek(self, fraction: float) -> None:
        if not 0 <= fraction <= 1:
            raise ValueError("seek fraction must be between zero and one")

    def reprocess_iq(self, uri: Any, backend: Any) -> dict[str, Any]:
        return {"uri": uri, "backend": backend, "scheduled": False}


class PlatformDiagnosticsService:
    def collect_platform(self) -> dict[str, Any]:
        import platform
        return {"os": platform.platform(aliased=True), "python": platform.python_version(), "architecture": platform.machine()}

    def run_self_tests(self) -> list[str]:
        return ["standalone service boundary: passed"]

    def run_offline_validation(self, **kwargs: Any) -> dict[str, Any]:
        return {"validated": False, "options": kwargs}

    def export_support_bundle(self, output_dir: Any) -> dict[str, Any]:
        return {"output_dir": output_dir, "created": False}


@dataclass(frozen=True, slots=True)
class SdrApplicationServices:
    live_sdr: LiveSdrService = field(default_factory=UnavailableLiveService)
    sweep: SweepSdrService = field(default_factory=UnavailableSweepService)
    calibration: CalibrationSdrService = field(default_factory=InMemoryCalibrationService)
    recording: RecordingSdrService = field(default_factory=InMemoryRecordingService)
    diagnostics: DiagnosticsSdrService = field(default_factory=PlatformDiagnosticsService)


def build_default_sdr_services() -> SdrApplicationServices:
    """Build the safe standalone composition root; no device access at startup."""
    return SdrApplicationServices()


__all__ = ["SdrApplicationServices", "build_default_sdr_services"]
