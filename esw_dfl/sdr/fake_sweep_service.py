"""Deterministic in-process sweep service for tests, benchmarks and fake GUI mode.

``FakeSweepService`` implements the :class:`~esw_dfl.sdr.sweep.SweepService`
protocol without Qt or native dependencies.  It produces deterministic frames
and follows the same immutable-config conventions as ``FakeLiveService``.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from ..domain import SourceDescriptor
from .contracts import (
    CalibrationStatus,
    ComputeBackendKind,
    QualityFlag,
    SourceType,
    SpectrumFrame,
    SpectrumUnit,
)
from .fake_live_service import FakeAppliedConfig
from .fixed_band import FixedBandOptions


FakeSweepAppliedConfig = FakeAppliedConfig


@dataclass(frozen=True, slots=True)
class FakeSweepConfig:
    """Immutable behavior knobs for :class:`FakeSweepService`."""

    sample_rate_hz: float = 3.0e6
    fft_size: int = 1024
    hop_size: int = 512
    dwell_frames: int = 2
    level_db: float = -70.0
    settling_time_seconds: float = 0.0
    discard_blocks: int = 0
    fail_reconfigure_at: int | None = None
    emit_quality_flags: int = 0

    def __post_init__(self) -> None:
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0.0:
            raise ValueError("sample_rate_hz must be finite and positive")
        if self.fft_size < 16 or self.fft_size & (self.fft_size - 1):
            raise ValueError("fft_size must be a power of two and at least 16")
        if not 0 < self.hop_size <= self.fft_size:
            raise ValueError("hop_size must be in [1, fft_size]")
        if self.dwell_frames < 1:
            raise ValueError("dwell_frames must be at least 1")
        if not math.isfinite(self.level_db):
            raise ValueError("level_db must be finite")
        if not math.isfinite(self.settling_time_seconds) or self.settling_time_seconds < 0.0:
            raise ValueError("settling_time_seconds must be finite and non-negative")
        if self.discard_blocks < 0:
            raise ValueError("discard_blocks must be non-negative")
        if self.fail_reconfigure_at is not None and self.fail_reconfigure_at < 0:
            raise ValueError("fail_reconfigure_at must be non-negative or None")
        QualityFlag(self.emit_quality_flags)


class FakeSweepService:
    """Deterministic :class:`SweepService` implementation for in-process use."""

    def __init__(self, config: FakeSweepConfig | None = None) -> None:
        self.config = config or FakeSweepConfig()
        self.streaming = False
        self.configured_centers: list[float] = []
        self.current: FixedBandOptions | None = None
        self.generation = 0
        self.poll_count = 0
        self.frame_count = 0
        self.sample_count = 0
        self._applied: FakeSweepAppliedConfig | None = None

    def configure(self, options: FixedBandOptions) -> FakeSweepAppliedConfig:
        """Apply requested options and return their deterministic readback."""

        self.current = options
        self.generation += 1
        return self._apply(options)

    def reconfigure(self, options: FixedBandOptions) -> FakeSweepAppliedConfig:
        """Retune a segment without advancing the sweep config generation.

        A wideband sweep changes only the center frequency between segments,
        so the configuration identity (``config_generation``) stays fixed
        across the whole sweep: ``stitch_sweep`` requires a single generation
        over all completed segments. The retune-failure knob is still honored
        using the configured-center count, matching the P12 fake semantics.
        """

        if (
            self.config.fail_reconfigure_at is not None
            and len(self.configured_centers) == self.config.fail_reconfigure_at
        ):
            raise RuntimeError("mock retune failure")
        self.streaming = False
        self.current = options
        self._applied = self._apply(options)
        self.streaming = True
        return self._applied

    def _apply(self, options: FixedBandOptions) -> FakeSweepAppliedConfig:
        """Record the configured center and build the deterministic readback."""

        self.configured_centers.append(options.device.center_frequency_hz)
        self._applied = FakeSweepAppliedConfig(
            center_frequency_hz=options.device.center_frequency_hz,
            sample_rate_hz=options.device.sample_rate_hz,
            analog_bandwidth_hz=options.device.analog_bandwidth_hz,
            gain_mode=options.device.gain_mode,
            manual_gain_db=options.device.manual_gain_db,
            config_generation=self.generation,
            active_backend=ComputeBackendKind.CPU,
        )
        return self._applied

    def start(self) -> None:
        """Mark the deterministic source as streaming."""

        self.streaming = True

    def request_stop(self) -> None:
        """Mark the deterministic source as stopped."""

        self.streaming = False

    def join(self) -> None:
        """Complete immediately because the fake owns no threads."""

    def disconnect(self) -> None:
        """Complete immediately because the fake owns no device connection."""

    def poll_spectrum(self, max_items: int = 0) -> tuple[SpectrumFrame, ...]:
        """Return deterministic frames for the currently configured segment."""

        if self.current is None:
            return ()
        count = max_items or 1
        self.poll_count += count
        options = self.current
        frames: list[SpectrumFrame] = []
        for _ in range(count):
            frames.append(self._make_frame(options, self.frame_count, self.sample_count))
            self.frame_count += 1
            self.sample_count += options.dsp.hop_size
        return tuple(frames)

    @property
    def applied(self) -> FakeSweepAppliedConfig | None:
        """Return the most recent applied configuration."""

        return self._applied

    def _make_frame(
        self,
        options: FixedBandOptions,
        sequence: int,
        first_sample_index: int,
    ) -> SpectrumFrame:
        fft_size = options.dsp.fft_size
        sample_rate = options.device.sample_rate_hz
        center = options.device.center_frequency_hz
        bin_width = sample_rate / fft_size
        frequencies = center - sample_rate / 2.0 + np.arange(fft_size, dtype=np.float64) * bin_width
        values = np.full(fft_size, self.config.level_db, dtype=np.float32)
        return SpectrumFrame(
            source=SourceDescriptor(SourceType.LIVE_IQ, "fake-sweep", "Fake sweep", uri="mock:"),
            frame_sequence=sequence,
            first_sample_index=first_sample_index,
            timestamp_ns=1_700_000_000_000_000_000 + sequence,
            config_generation=self.generation,
            center_frequency_hz=center,
            sample_rate_hz=sample_rate,
            analog_bandwidth_hz=options.device.analog_bandwidth_hz,
            fft_bin_width_hz=bin_width,
            enbw_hz=bin_width,
            nominal_rbw_hz=bin_width,
            fft_size=fft_size,
            hop_size=options.dsp.hop_size,
            window=options.dsp.window,
            detector=options.dsp.detector,
            precision_mode=options.dsp.precision_mode,
            unit=SpectrumUnit.DBFS_BIN,
            frequencies_hz=frequencies,
            values=values,
            calibration_status=CalibrationStatus.UNCALIBRATED,
            quality_flags=QualityFlag.UNCALIBRATED | QualityFlag(self.config.emit_quality_flags),
        )


__all__ = ["FakeSweepAppliedConfig", "FakeSweepConfig", "FakeSweepService"]
