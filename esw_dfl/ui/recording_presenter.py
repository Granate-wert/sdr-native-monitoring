"""Qt-free presenter for the Recording & Replay workspace.

The GUI thread calls only ``configure`` / ``start_recording`` / ``stop_recording`` /
``recover_partial`` / ``open_replay`` / ``play`` / ``pause`` / ``seek_fraction`` /
``stop_replay`` / ``reprocess_iq`` / ``poll`` / ``close``.  Recording and replay
run on ``threading.Thread`` workers owned here; ``poll()`` is a cheap cached read
safe for a 60 Hz GUI timer.

Recording goes through the P14 :class:`RecordingService`; the worker thread feeds
it deterministic synthetic I/Q blocks so the bounded writer, gap metadata,
``.part`` safety and drop accounting are exercised end-to-end from the UI without
claiming live hardware.  Replay reads recordings through the P14 ``IqReplay`` /
``SpectrumReplay`` iterators.  IQ reprocess goes through ``replay_iq_through_native``
on the selected CPU/CUDA backend; an unavailable native runtime or missing CUDA is
surfaced as a typed error string, never an exception.

Privacy: the presenter never publishes the full output URI into snapshots; only
the user-facing basename crosses to :mod:`recording_state`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from ..sdr.contracts import (
    ComputeBackendKind,
    DspConfig,
    IqBlock,
    SampleFormat,
)
from ..sdr.recording import (
    IqReplay,
    RecordingError,
    RecordingOptions,
    RecordingService,
    SpectrumReplay,
    StorageForecast,
    estimate_storage,
    recover_iq_recording,
    replay_iq_through_native,
)
from ..sdr.synthetic import SyntheticConfig, SyntheticScenario, generate_scenario
from .recording_state import (
    RecordingHealthSnapshot,
    RecordingRunState,
    RecordingSetupSnapshot,
    RecordingWorkspaceSnapshot,
    ReplayRunState,
    ReplaySourceKind,
    ReplayStateSnapshot,
)

_SAMPLE_FORMAT = SampleFormat.COMPLEX_FLOAT32_LE
_DEFAULT_SAMPLE_RATE_HZ = 3_000_000.0
_DEFAULT_BLOCK_SAMPLES = 65_536
_REPLAY_BATCH = 64
_RECORD_SCENARIOS: tuple[SyntheticScenario, ...] = (
    SyntheticScenario.EXACT_BIN_TONE,
    SyntheticScenario.TWO_TONES,
    SyntheticScenario.BROADBAND_NOISE,
)


def _fmt_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    amount = float(abs(value))
    unit = "B"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024.0 or unit == "TiB":
            break
        amount /= 1024.0
    if unit == "B":
        return f"{sign}{int(amount)} {unit}"
    return f"{sign}{amount:.2f} {unit}"


def _fmt_int(value: int) -> str:
    return f"{value:,}".replace(",", " ") if value >= 100_000 else str(value)


def _fmt_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def _basename(uri: str | Path | None) -> str:
    if uri is None:
        return ""
    text = str(uri).replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] if "/" in text else text


def _scenario_available(name: str) -> SyntheticScenario:
    try:
        return SyntheticScenario(name)
    except ValueError:  # pragma: no cover - defensive, enum is pinned above
        return SyntheticScenario.EXACT_BIN_TONE


class RecordingPresenter:
    """Owns the recording/replay lifecycle and publishes immutable snapshots."""

    def __init__(
        self,
        *,
        sample_rate_hz: float = _DEFAULT_SAMPLE_RATE_HZ,
        block_samples: int = _DEFAULT_BLOCK_SAMPLES,
        replay_batch: int = _REPLAY_BATCH,
        service_factory: Callable[[RecordingOptions], RecordingService] | None = None,
        iq_producer: Callable[[int, int, float], IqBlock] | None = None,
    ) -> None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if block_samples <= 0:
            raise ValueError("block_samples must be positive")
        if replay_batch <= 0:
            raise ValueError("replay_batch must be positive")
        self._sample_rate_hz = float(sample_rate_hz)
        self._block_samples = int(block_samples)
        self._replay_batch = int(replay_batch)
        self._service_factory = service_factory
        self._iq_producer = iq_producer

        self._lock = threading.Lock()
        self._generation = 0
        self._setup: RecordingOptions | None = None
        self._forecast: StorageForecast | None = None
        self._duration_s = 0.0
        self._recording_state = RecordingRunState.IDLE

        self._service: RecordingService | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started_monotonic = 0.0
        self._last_stats: Any = None

        self._replay_state = ReplayRunState.EMPTY
        self._replay: Any = None
        self._replay_thread: threading.Thread | None = None
        self._replay_cancel = threading.Event()
        self._replay_pause = threading.Event()
        self._replay_position = 0
        self._replay_total = 0
        self._replay_name = ""
        self._replay_kind = ReplaySourceKind.NONE
        self._replay_gap_count = 0
        self._replay_calibrated = False
        self._reprocess_backend: str | None = None

        self._last_error: str | None = None

    # -- properties ---------------------------------------------------------

    @property
    def snapshot(self) -> RecordingWorkspaceSnapshot:
        with self._lock:
            return self._rebuild_locked()

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    @property
    def recording_running(self) -> bool:
        with self._lock:
            return self._recording_state is RecordingRunState.RUNNING

    # -- recording ----------------------------------------------------------

    def configure(
        self,
        *,
        output_uri: str | Path,
        record_iq: bool = True,
        record_spectrum: bool = False,
        duration_s: float = 10.0,
        queue_capacity: int = 8,
    ) -> list[str]:
        """Validate the setup and pre-compute a storage forecast.

        Returns human-readable error strings; empty means accepted.  Never
        raises for user input problems.
        """

        try:
            options = RecordingOptions(
                output_uri=output_uri,
                record_iq=record_iq,
                record_spectrum=record_spectrum,
                queue_capacity=queue_capacity,
            )
        except (ValueError, TypeError) as exc:
            return self._reject(str(exc))
        if duration_s <= 0:
            return self._reject("duration must be positive")
        forecast = estimate_storage(
            sample_rate_hz=self._sample_rate_hz,
            duration_seconds=duration_s,
            sample_format=_SAMPLE_FORMAT,
            spectrum_frames_per_second=60.0 if record_spectrum else 0.0,
            spectrum_bins=1024 if record_spectrum else 0,
            record_iq=record_iq,
            record_spectrum=record_spectrum,
            output_uri=output_uri,
            reserve_bytes=options.free_space_reserve_bytes,
        )
        with self._lock:
            self._setup = options
            self._forecast = forecast
            self._duration_s = float(duration_s)
            self._recording_state = RecordingRunState.CONFIGURED
            self._last_error = None
            self._generation += 1
        return []

    def start_recording(self) -> list[str]:
        """Start recording deterministic synthetic blocks.  Runs the P14 preflight
        in the foreground so an immediate ``InsufficientStorageError`` is returned
        synchronously, then delegates streaming to a worker thread.
        """

        with self._lock:
            if self._recording_state is RecordingRunState.RUNNING:
                return ["recording is already running"]
            if self._setup is None or self._forecast is None:
                return ["no recording setup is configured"]
            options = self._setup
            forecast = self._forecast
        factory = self._service_factory
        try:
            service = factory(options) if factory is not None else RecordingService(options)
            service.start(forecast=forecast)
        except Exception as exc:  # InsufficientStorageError / RecordingError / OSError
            with self._lock:
                self._recording_state = RecordingRunState.FAILED
                self._last_error = str(exc)
                self._generation += 1
            return [str(exc)]
        self._stop_event.clear()
        thread = threading.Thread(target=self._record_worker, name="p16-recording", daemon=True)
        with self._lock:
            self._service = service
            self._thread = thread
            self._started_monotonic = time.monotonic()
            self._last_stats = None
            self._recording_state = RecordingRunState.RUNNING
            self._last_error = None
            self._generation += 1
        thread.start()
        return []

    def _make_iq_block(self, index: int) -> IqBlock:
        if self._iq_producer is not None:
            return self._iq_producer(index, self._block_samples, self._sample_rate_hz)
        scenario = _RECORD_SCENARIOS[index % len(_RECORD_SCENARIOS)]
        config = SyntheticConfig(
            sample_count=self._block_samples,
            sample_rate_hz=self._sample_rate_hz,
            seed=index,
        )
        signal = generate_scenario(scenario, config)
        values = np.asarray(signal.samples, dtype=np.complex64)
        raw = np.empty(values.size * 2, dtype=np.float32)
        raw[0::2] = values.real
        raw[1::2] = values.imag
        return IqBlock(
            source_sequence=index,
            first_sample_index=index * self._block_samples,
            timestamp_ns=time.monotonic_ns(),
            center_frequency_hz=signal.config.center_frequency_hz,
            sample_rate_hz=self._sample_rate_hz,
            sample_format=_SAMPLE_FORMAT,
            sample_count=values.size,
            flags=signal.quality_flags,
            samples=raw.view(np.uint8),
            config_generation=0,
        )

    def _record_worker(self) -> None:
        block_index = 0
        finalized = True
        stats: Any = None
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    service = self._service
                    duration_s = self._duration_s
                    options = self._setup
                    elapsed = time.monotonic() - self._started_monotonic
                if service is None or options is None:
                    break
                if duration_s > 0 and elapsed >= duration_s:
                    break
                if options.record_iq:
                    block = self._make_iq_block(block_index)
                    try:
                        accepted = service.submit_iq(block, timeout_s=0.1)
                    except RecordingError as exc:
                        with self._lock:
                            self._last_error = str(exc)
                            self._recording_state = RecordingRunState.FAILED
                            self._generation += 1
                        finalized = False
                        break
                    if not accepted and service.options.stop_on_overflow:
                        break
                block_index += 1
        finally:
            with self._lock:
                service = self._service
                was_running = self._recording_state in (
                    RecordingRunState.RUNNING,
                    RecordingRunState.STOPPING,
                )
            if service is not None:
                try:
                    stats = service.stop(finalize=finalized, cancel=not finalized)
                except Exception as exc:  # keep worker exit clean; surface in snapshot
                    stats = None
                    with self._lock:
                        self._last_error = str(exc)
            with self._lock:
                if stats is not None:
                    self._last_stats = stats
                if self._recording_state is RecordingRunState.RUNNING:
                    self._recording_state = (
                        RecordingRunState.FAILED if self._last_error else RecordingRunState.COMPLETED
                    )
                elif was_running and self._recording_state is RecordingRunState.STOPPING:
                    self._recording_state = RecordingRunState.COMPLETED
                self._generation += 1

    def stop_recording(self) -> None:
        """Request a graceful stop (finalize).  Non-blocking; poll observes end."""

        with self._lock:
            if self._recording_state is not RecordingRunState.RUNNING:
                return
            self._recording_state = RecordingRunState.STOPPING
            self._generation += 1
        self._stop_event.set()

    def recover_partial(self, output_uri: str | Path) -> list[str]:
        """Recover a safe prefix from a crashed ``.part`` IQ recording."""

        try:
            result = recover_iq_recording(output_uri, finalize=True)
        except (RecordingError, OSError) as exc:
            with self._lock:
                self._last_error = str(exc)
                self._generation += 1
            return [str(exc)]
        if result.truncated_bytes <= 0 and not result.finalized:
            message = "no recovery was needed (recording intact)"
        else:
            message = (
                f"recovered {result.retained_iq_blocks} IQ blocks, "
                f"truncated {result.truncated_bytes} bytes"
            )
        with self._lock:
            self._last_error = None
            self._generation += 1
        return [message]

    # -- replay -------------------------------------------------------------

    def open_replay(self, output_uri: str | Path, *, kind: ReplaySourceKind) -> list[str]:
        """Open an IQ or spectrum recording for replay.  Returns error strings."""

        if kind is ReplaySourceKind.NONE:
            return ["select IQ or spectrum recording to open"]
        self.stop_replay()
        try:
            if kind is ReplaySourceKind.IQ:
                replay: Any = IqReplay(output_uri)
                sdr = replay.metadata.get("sdr")
                sdr_map = sdr if isinstance(sdr, dict) else {}
                total = int(sdr_map.get("sample_count", 0) or 0)
                gap_count = int(sdr_map.get("gap_count", 0) or 0)
                calibrated = False
            else:
                replay = SpectrumReplay(output_uri)
                total = int(replay.metadata.get("frame_count", 0) or 0)
                gap_count = int(replay.metadata.get("gap_count", 0) or 0)
                calibrated = bool(replay.metadata.get("calibration_profile_id"))
        except (RecordingError, OSError, ValueError) as exc:
            with self._lock:
                self._last_error = str(exc)
                self._replay_state = ReplayRunState.FAILED
                self._generation += 1
            return [str(exc)]
        with self._lock:
            self._replay = replay
            self._replay_position = 0
            self._replay_total = total
            self._replay_name = _basename(output_uri)
            self._replay_kind = kind
            self._replay_gap_count = gap_count
            self._replay_calibrated = calibrated
            self._replay_state = ReplayRunState.LOADED
            self._last_error = None
            self._generation += 1
        return []

    def play(self) -> None:
        """Start or resume replay playback on a worker thread."""

        with self._lock:
            if self._replay_state is ReplayRunState.PAUSED:
                self._replay_pause.clear()
                self._replay_state = ReplayRunState.PLAYING
                self._generation += 1
                return
            if self._replay_state not in (ReplayRunState.LOADED, ReplayRunState.FINISHED):
                return
            self._replay_state = ReplayRunState.PLAYING
            self._last_error = None
            self._generation += 1
            replay = self._replay
            kind = self._replay_kind
        if self._replay_thread is not None and self._replay_thread.is_alive():
            return
        self._replay_cancel.clear()
        self._replay_pause.clear()
        thread = threading.Thread(
            target=self._replay_worker, args=(replay, kind), name="p16-replay", daemon=True
        )
        self._replay_thread = thread
        thread.start()

    def _replay_worker(self, replay: Any, kind: ReplaySourceKind) -> None:
        """Stream frames/blocks; position accounting only, bounded batch."""

        try:
            iterator = (
                replay.iter_blocks(cancel=self._replay_cancel)
                if kind is ReplaySourceKind.IQ
                else replay.iter_frames(cancel=self._replay_cancel)
            )
            count = 0
            for _ in iterator:
                if self._replay_cancel.is_set():
                    break
                while self._replay_pause.is_set() and not self._replay_cancel.is_set():
                    time.sleep(0.02)
                count += 1
                if count % self._replay_batch == 0:
                    with self._lock:
                        self._replay_position = count
                        self._generation += 1
            with self._lock:
                self._replay_position = count
                if not self._replay_cancel.is_set() and self._replay_state is ReplayRunState.PLAYING:
                    self._replay_state = ReplayRunState.FINISHED
                self._generation += 1
        except RecordingError as exc:
            with self._lock:
                self._last_error = str(exc)
                self._replay_state = ReplayRunState.FAILED
                self._generation += 1

    def pause(self) -> None:
        with self._lock:
            if self._replay_state is not ReplayRunState.PLAYING:
                return
            self._replay_state = ReplayRunState.PAUSED
            self._generation += 1
        self._replay_pause.set()

    def seek_fraction(self, fraction: float) -> None:
        with self._lock:
            if self._replay_total <= 0:
                return
            target = int(max(0.0, min(1.0, float(fraction))) * self._replay_total)
            self._replay_position = target
            self._generation += 1

    def stop_replay(self) -> None:
        self._replay_cancel.set()
        self._replay_pause.clear()
        thread = self._replay_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            if self._replay_state in (ReplayRunState.PLAYING, ReplayRunState.PAUSED):
                self._replay_state = ReplayRunState.LOADED
            self._generation += 1

    def reprocess_iq(
        self,
        output_uri: str | Path,
        *,
        backend: ComputeBackendKind,
        dsp_config: DspConfig | None = None,
    ) -> list[str]:
        """Reprocess a recorded IQ capture through the native CPU/CUDA path.

        Returns a single human-readable status/error string.  An unavailable
        native runtime or missing CUDA is a typed error, not an exception.
        """

        if backend not in (ComputeBackendKind.CPU, ComputeBackendKind.CUDA):
            return ["reprocess supports only explicit CPU or CUDA backend"]
        try:
            replay = IqReplay(output_uri)
        except (RecordingError, OSError) as exc:
            with self._lock:
                self._last_error = str(exc)
                self._generation += 1
            return [str(exc)]
        config = dsp_config if dsp_config is not None else DspConfig(fft_size=1024, hop_size=512)
        frames = 0
        try:
            for _ in replay_iq_through_native(replay, config, backend=backend.value, cancel=None):
                frames += 1
        except Exception as exc:  # unavailable native runtime, missing CUDA, DSP error
            with self._lock:
                self._last_error = str(exc)
                self._generation += 1
            return [str(exc)]
        message = f"reprocess on {backend.value}: {frames} frames"
        with self._lock:
            self._reprocess_backend = backend.value
            self._last_error = None
            self._generation += 1
        return [message]

    # -- polling / lifecycle ------------------------------------------------

    def poll(self) -> RecordingWorkspaceSnapshot:
        with self._lock:
            return self._rebuild_locked()

    def _reject(self, message: str) -> list[str]:
        with self._lock:
            self._last_error = message
            self._generation += 1
        return [message]

    def _rebuild_locked(self) -> RecordingWorkspaceSnapshot:
        return RecordingWorkspaceSnapshot(
            generation=self._generation,
            recording_state=self._recording_state,
            replay_state=self._replay_state,
            setup=self._build_setup_locked(),
            health=self._build_health_locked(),
            replay=self._build_replay_locked(),
            confirmation_required=(
                self._recording_state is RecordingRunState.CONFIGURED
                and self._forecast is not None
                and self._forecast.sufficient is False
            ),
            error=self._last_error,
            stale=False,
        )

    def _build_setup_locked(self) -> RecordingSetupSnapshot | None:
        if self._setup is None:
            return None
        forecast = self._forecast
        estimated = _fmt_bytes(forecast.estimated_bytes) if forecast is not None else None
        free = _fmt_bytes(forecast.free_bytes) if forecast is not None else None
        sufficient: str | None = None
        if forecast is not None:
            sufficient = (
                "yes" if forecast.sufficient else ("no" if forecast.sufficient is False else "unknown")
            )
        return RecordingSetupSnapshot(
            record_iq=self._setup.record_iq,
            record_spectrum=self._setup.record_spectrum,
            duration_s=f"{self._duration_s:.1f}",
            filename_template=_basename(self._setup.output_uri),
            estimated_bytes=estimated,
            free_bytes=free,
            sufficient=sufficient,
            queue_capacity=self._setup.queue_capacity,
            error=None,
        )

    def _build_health_locked(self) -> RecordingHealthSnapshot | None:
        stats = self._last_stats
        service = self._service
        if (
            stats is None
            and service is not None
            and self._recording_state is RecordingRunState.RUNNING
        ):
            try:
                stats = service.stats()
            except Exception:
                stats = None
        elapsed = (
            time.monotonic() - self._started_monotonic
            if self._recording_state in (RecordingRunState.RUNNING, RecordingRunState.STOPPING)
            else 0.0
        )
        if stats is None:
            if self._recording_state in (RecordingRunState.IDLE, RecordingRunState.CONFIGURED):
                return None
            return RecordingHealthSnapshot(error=self._last_error)
        return RecordingHealthSnapshot(
            enqueued=_fmt_int(stats.enqueued_items),
            written_iq_samples=_fmt_int(stats.written_iq_samples),
            written_spectrum_frames=_fmt_int(stats.written_spectrum_frames),
            dropped_items=_fmt_int(stats.dropped_items),
            gap_count=_fmt_int(stats.gap_count),
            queue_depth=_fmt_int(stats.queue_depth),
            queue_high_water=_fmt_int(stats.queue_high_water),
            queue_capacity=_fmt_int(self._setup.queue_capacity if self._setup else 0),
            elapsed_s=f"{elapsed:.1f}",
            stopped_on_overflow=stats.stopped_on_overflow,
            error=stats.error or self._last_error,
        )

    def _build_replay_locked(self) -> ReplayStateSnapshot | None:
        if self._replay is None:
            return None
        total = self._replay_total
        position = self._replay_position
        label = f"{_fmt_int(position)} / {_fmt_int(total)}"
        duration_s = (
            total / self._sample_rate_hz
            if self._replay_kind is ReplaySourceKind.IQ and self._sample_rate_hz > 0
            else 0.0
        )
        return ReplayStateSnapshot(
            kind=self._replay_kind,
            name=self._replay_name,
            sample_count=_fmt_int(total if self._replay_kind is ReplaySourceKind.IQ else 0),
            frame_count=_fmt_int(total if self._replay_kind is ReplaySourceKind.SPECTRUM else 0),
            position_label=label,
            duration_label=_fmt_duration(duration_s),
            gap_count=_fmt_int(self._replay_gap_count),
            reprocess_backend=self._reprocess_backend,
            calibrated=self._replay_calibrated,
            detail=None,
        )

    def close(self) -> None:
        """Cancel recording and replay, join workers with a bounded timeout."""

        self.stop_recording()
        self._stop_event.set()
        self.stop_replay()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            service = self._service
            self._service = None
        if service is not None:
            try:
                service.close()
            except Exception:
                pass


__all__ = ["RecordingPresenter"]
