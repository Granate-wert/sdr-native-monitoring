"""Standalone service assembly with no dependency on the DFL product tree."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, cast

from .interfaces import CalibrationSdrService, DiagnosticsSdrService, LiveSdrService, RecordingSdrService, ReplaySdrService, SweepSdrService
from .live_session import InMemoryLiveSessionService
from .native_live import NativeLiveSessionService, build_optional_native_live_service
from .native_recording import NativeLiveRecordingService, NativeRecordingLivePort
from .native_sweep import NativeLiveSweepService
from .profile_store import LiveProfileStore
from .calibration_service import CalibrationService
from .calibration_store import CalibrationProfileStore
from .sweep_session import InMemorySweepService
from .recording_session import RecordingService
from .replay_session import ReplayService
from .diagnostics_session import DiagnosticsService


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

    def discover_devices(self) -> tuple[Any, ...]:
        return ()

    def discover_startup_devices(self) -> tuple[Any, ...]:
        return ()

    def select_device(self, device_id: str) -> Any:
        raise RuntimeError(f"live service is unavailable: {device_id}")

    def select_manual_uri(self, uri: str) -> Any:
        raise RuntimeError(f"live service is unavailable: {uri}")

    def apply_configuration(self, requested: Any) -> Any:
        return {"requested": requested}

    def start(self) -> Any:
        self._running = True
        return {"running": True}

    def stop(self) -> Any:
        self._running = False
        return {"running": False}

    def latest_snapshot(self) -> Any:
        return {"running": self._running}


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

    def export_result(self, result: Any, output: Any) -> dict[str, Any]:
        return {"result": result, "output": output, "exported": False}


class InMemoryCalibrationService(CalibrationService):
    """Backward-compatible name for the safe standalone calibration service."""


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


class UnavailableNativeRecordingService:
    """Fail closed when the native RTBW capture path is unavailable.

    ``RecordingService`` remains a reader/recovery compatibility implementation
    for historical ``.sdrrec`` fixtures and files.  It is deliberately not the
    normal standalone application's Live recorder: its JSON/base64 I/Q queue is
    Python-owned and cannot represent a Pluto native RTBW capture.  Keeping the
    fallback explicit prevents a disconnected native extension from silently
    changing the recording format or the I/Q ownership boundary.
    """

    _UNAVAILABLE_MESSAGE = (
        "native RTBW recording is unavailable: select a native SDR receiver "
        "and start Live before arming a native recording"
    )

    def start(self, options: Any) -> None:
        del options
        raise RuntimeError(self._UNAVAILABLE_MESSAGE)

    def start_now(self, options: Any) -> None:
        del options
        raise RuntimeError(self._UNAVAILABLE_MESSAGE)

    def stop(self, timeout_s: float = 5.0) -> Any:
        del timeout_s
        from ..domain import RecordingResult, RecordingState

        return RecordingResult("", RecordingState.IDLE, 0, 0, 0, 0, 0)

    def health(self) -> Any:
        from ..domain import RecordingHealth, RecordingState

        return RecordingHealth(
            RecordingState.IDLE,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            error=self._UNAVAILABLE_MESSAGE,
            recording_mode="native_unavailable",
        )

    def recover_partial(self, uri: Any) -> dict[str, Any]:
        # Recovery of historical .sdrrec.part remains read-only compatibility
        # work.  The native scanner remains scan-only as well.
        return RecordingService().recover_partial(uri)

    def close(self) -> None:
        return None


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


def _default_calibration_service() -> CalibrationService:
    root = Path(os.environ.get("LOCALAPPDATA", Path.cwd())) / "SDR Native Monitoring" / "calibration_profiles"
    return CalibrationService(CalibrationProfileStore(root))

def _default_profile_store() -> LiveProfileStore:
    root = Path(os.environ.get("LOCALAPPDATA", Path.cwd())) / "SDR Native Monitoring"
    return LiveProfileStore(root / "live_profiles.json")

def _default_live_service() -> LiveSdrService:
    native = build_optional_native_live_service()
    return native if native is not None else InMemoryLiveSessionService()
@dataclass(frozen=True, slots=True)
class SdrApplicationServices:
    live_sdr: LiveSdrService = field(default_factory=_default_live_service)
    sweep: SweepSdrService = field(default_factory=InMemorySweepService)
    calibration: CalibrationSdrService = field(default_factory=_default_calibration_service)
    recording: RecordingSdrService = field(default_factory=UnavailableNativeRecordingService)
    replay: ReplaySdrService = field(default_factory=ReplayService)
    diagnostics: DiagnosticsSdrService = field(default_factory=DiagnosticsService)
    profiles: LiveProfileStore = field(default_factory=_default_profile_store)


def build_default_sdr_services() -> SdrApplicationServices:
    """Build the safe standalone composition root; no device access at startup."""
    live = _default_live_service()
    recording: RecordingSdrService
    if all(
        callable(getattr(live, attribute, None))
        for attribute in (
            "arm_native_recording",
            "start_native_recording_now",
            "stop_native_recording",
            "native_recording_health",
        )
    ):
        recording = NativeLiveRecordingService(cast(NativeRecordingLivePort, live))
    else:
        recording = UnavailableNativeRecordingService()
    sweep: SweepSdrService = NativeLiveSweepService(live) if isinstance(live, NativeLiveSessionService) else InMemorySweepService()
    return SdrApplicationServices(live_sdr=live, sweep=sweep, recording=recording)


def build_offscreen_smoke_sdr_services() -> SdrApplicationServices:
    """Build the R12-G AppShell composition without native Live admission.

    This is deliberately not a normal-product fallback and is used only by the
    private packaged offscreen lifecycle command.  Its Live, Sweep and recording
    ports cannot discover, configure or start a receiver, so AppShell creation
    can be verified without importing/instantiating the optional native Live
    service.  Other ports retain their existing inert construction and are not
    invoked while the shell remains on Home.
    """

    return SdrApplicationServices(
        live_sdr=UnavailableLiveService(),
        sweep=UnavailableSweepService(),
        recording=UnavailableNativeRecordingService(),
    )


__all__ = [
    "SdrApplicationServices",
    "UnavailableNativeRecordingService",
    "build_default_sdr_services",
    "build_offscreen_smoke_sdr_services",
]
