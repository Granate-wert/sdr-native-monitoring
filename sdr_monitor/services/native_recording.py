"""Native RTBW recording adapter composed with the native Live service.

The adapter has no writer and never receives I/Q/Spectrum frames.  It routes
only low-rate commands and health reads to the native Live service, so the C++
FixedBand engine remains the sole live capture data path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..domain import RecordingHealth, RecordingOptions, RecordingResult, RecordingState


class NativeRecordingLivePort(Protocol):
    def arm_native_recording(self, options: RecordingOptions) -> RecordingHealth: ...
    def start_native_recording_now(self, options: RecordingOptions) -> RecordingHealth: ...
    def stop_native_recording(self) -> RecordingHealth: ...
    def native_recording_health(self) -> RecordingHealth: ...


class NativeLiveRecordingService:
    """Recording-control port for native fixed-band (RTBW) Live only."""

    def __init__(self, live: NativeRecordingLivePort) -> None:
        self._live = live

    def start(self, options: RecordingOptions) -> None:
        """Safe default: arm for the next Live start; never restart Live."""

        _require_native_output_base(options)
        self._live.arm_native_recording(options)

    def start_now(self, options: RecordingOptions) -> None:
        """Execute the separately confirmed controlled RTBW restart."""

        _require_native_output_base(options)
        self._live.start_native_recording_now(options)

    def stop(self, timeout_s: float = 5.0) -> RecordingResult:
        # FixedBand owns writer finalization during the explicitly requested
        # Live shutdown.  Keep the general port timeout for compatibility.
        del timeout_s
        health = self._live.stop_native_recording()
        if health.state is RecordingState.RECORDING:
            raise RuntimeError("native recording did not finalize after Live stop")
        return RecordingResult(
            health.output_path or "",
            health.state,
            health.iq_blocks,
            health.spectrum_frames,
            health.drops,
            health.gaps,
            health.bytes_written,
            metadata={
                "recording_mode": health.recording_mode,
                "epoch": health.epoch,
                "recorded_iq_samples": health.recorded_iq_samples,
                "average_iq_sample_rate_hz": health.average_iq_sample_rate_hz,
                "iq_loss_rate": health.iq_loss_rate,
                "restart_gap_duration_ns": health.restart_gap_duration_ns,
            },
            error=health.error,
            drop_reasons=health.drop_reasons,
        )

    def health(self) -> RecordingHealth:
        return self._live.native_recording_health()

    def recover_partial(self, uri: str) -> dict[str, object]:
        # Reuse the R08-C0 scan-only operation.  It creates no writer and
        # remains strictly read-only for native `.part` artifacts.
        from .recording_session import RecordingService

        return RecordingService().recover_partial(uri)

    def close(self) -> None:
        # AppShell shuts down Live first, which finalizes native writers.
        return None


def _require_native_output_base(options: RecordingOptions) -> None:
    """Reject a historical filename that would obscure native artifact type."""

    name = Path(options.output_path).name
    removable_suffixes = (
        ".sigmf-meta",
        ".sigmf-index.jsonl",
        ".sigmf-gaps.jsonl",
        ".sdr-spectrum.meta",
        ".sdr-spectrum.bin",
        ".sdr-spectrum-index.jsonl",
        ".part",
    )
    while True:
        suffix = next(
            (item for item in removable_suffixes if name.casefold().endswith(item)),
            None,
        )
        if suffix is None:
            break
        name = name[: -len(suffix)]
    if name.casefold().endswith(".sdrrec"):
        raise ValueError(
            "legacy .sdrrec is read/reprocess compatibility only; "
            "choose a native recording base path without that extension"
        )


__all__ = ["NativeLiveRecordingService", "NativeRecordingLivePort"]
