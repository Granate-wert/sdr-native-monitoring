"""Deterministic synthetic complex-I/Q scenarios for SDR golden validation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

import numpy as np

from .contracts import QualityFlag


DEFAULT_SEED = 0x5344_525F_5030_3301
DEFAULT_SAMPLE_COUNT = 1024
DEFAULT_SAMPLE_RATE_HZ = 1_024_000.0
DEFAULT_CENTER_FREQUENCY_HZ = 100_000_000.0
SAMPLE_QUANTIZATION_BITS = 40
_UINT64_MASK = (1 << 64) - 1


class SyntheticScenario(StrEnum):
    EXACT_BIN_TONE = "exact_bin_tone"
    HALF_BIN_TONE = "half_bin_tone"
    TWO_TONES = "two_tones"
    CLOSE_TONES = "close_tones"
    BROADBAND_NOISE = "broadband_noise"
    DC_OFFSET = "dc_offset"
    IMPULSE = "impulse"
    CLIPPING = "clipping"
    IQ_IMBALANCE = "iq_imbalance"
    CHIRP = "chirp"
    HOPPING = "hopping"
    AMPLITUDE_BURST = "amplitude_burst"


@dataclass(frozen=True, slots=True)
class SyntheticConfig:
    sample_count: int = DEFAULT_SAMPLE_COUNT
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ
    center_frequency_hz: float = DEFAULT_CENTER_FREQUENCY_HZ
    seed: int = DEFAULT_SEED
    quantization_bits: int = SAMPLE_QUANTIZATION_BITS

    def __post_init__(self) -> None:
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int) or self.sample_count <= 0:
            raise ValueError("sample_count must be a positive integer")
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0.0:
            raise ValueError("sample_rate_hz must be finite and positive")
        if not math.isfinite(self.center_frequency_hz) or self.center_frequency_hz < 0.0:
            raise ValueError("center_frequency_hz must be finite and non-negative")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or not 0 <= self.seed <= _UINT64_MASK:
            raise ValueError("seed must be uint64")
        if (
            isinstance(self.quantization_bits, bool)
            or not isinstance(self.quantization_bits, int)
            or not 16 <= self.quantization_bits <= 48
        ):
            raise ValueError("quantization_bits must be in [16, 48]")

    @property
    def bin_width_hz(self) -> float:
        return self.sample_rate_hz / float(self.sample_count)


@dataclass(frozen=True, slots=True)
class SyntheticSignal:
    scenario: SyntheticScenario
    config: SyntheticConfig
    samples: np.ndarray
    quality_flags: QualityFlag = QualityFlag.NONE
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, SyntheticScenario):
            raise TypeError("scenario must be SyntheticScenario")
        if not isinstance(self.config, SyntheticConfig):
            raise TypeError("config must be SyntheticConfig")
        if not isinstance(self.quality_flags, QualityFlag):
            raise TypeError("quality_flags must be QualityFlag")
        values = np.asarray(self.samples, dtype=np.complex128)
        if values.ndim != 1 or values.size != self.config.sample_count:
            raise ValueError("samples must be one-dimensional and match config.sample_count")
        if not np.all(np.isfinite(values.real)) or not np.all(np.isfinite(values.imag)):
            raise ValueError("synthetic samples must be finite")
        private = np.array(values, dtype="<c16", order="C", copy=True)
        private.setflags(write=False)
        object.__setattr__(self, "samples", private)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def _quantize(values: np.ndarray, bits: int) -> np.ndarray:
    """Quantize to a binary Q grid wider than float32 for cross-platform hashes."""

    scale = float(1 << bits)
    real = np.rint(np.asarray(values.real, dtype=np.float64) * scale) / scale
    imag = np.rint(np.asarray(values.imag, dtype=np.float64) * scale) / scale
    result = np.asarray(real + 1j * imag, dtype="<c16")
    result.setflags(write=False)
    return result


def _make(
    scenario: SyntheticScenario,
    config: SyntheticConfig,
    values: np.ndarray,
    *,
    quality_flags: QualityFlag = QualityFlag.NONE,
    metadata: Mapping[str, object] | None = None,
) -> SyntheticSignal:
    return SyntheticSignal(
        scenario=scenario,
        config=config,
        samples=_quantize(np.asarray(values, dtype=np.complex128), config.quantization_bits),
        quality_flags=quality_flags,
        metadata={} if metadata is None else metadata,
    )


def _phase(config: SyntheticConfig, frequency_offset_hz: float, phase_rad: float = 0.0) -> np.ndarray:
    if not math.isfinite(frequency_offset_hz) or abs(frequency_offset_hz) >= config.sample_rate_hz / 2.0:
        raise ValueError("frequency_offset_hz must lie inside complex Nyquist bandwidth")
    if not math.isfinite(phase_rad):
        raise ValueError("phase_rad must be finite")
    n = np.arange(config.sample_count, dtype=np.float64)
    return phase_rad + (2.0 * np.pi * frequency_offset_hz / config.sample_rate_hz) * n


def complex_tone(
    config: SyntheticConfig,
    frequency_offset_hz: float,
    *,
    amplitude: float = 1.0,
    phase_rad: float = 0.0,
    scenario: SyntheticScenario = SyntheticScenario.EXACT_BIN_TONE,
) -> SyntheticSignal:
    if not math.isfinite(amplitude) or amplitude < 0.0:
        raise ValueError("amplitude must be finite and non-negative")
    values = amplitude * np.exp(1j * _phase(config, frequency_offset_hz, phase_rad))
    return _make(
        scenario,
        config,
        values,
        metadata={
            "frequency_offset_hz": float(frequency_offset_hz),
            "amplitude": float(amplitude),
            "phase_rad": float(phase_rad),
        },
    )


def multi_tone(
    config: SyntheticConfig,
    components: tuple[tuple[float, float, float], ...],
    *,
    scenario: SyntheticScenario = SyntheticScenario.TWO_TONES,
) -> SyntheticSignal:
    if not components:
        raise ValueError("at least one tone component is required")
    values = np.zeros(config.sample_count, dtype=np.complex128)
    metadata_components: list[dict[str, float]] = []
    for frequency_hz, amplitude, phase_rad in components:
        if not math.isfinite(amplitude) or amplitude < 0.0:
            raise ValueError("tone amplitudes must be finite and non-negative")
        values += amplitude * np.exp(1j * _phase(config, frequency_hz, phase_rad))
        metadata_components.append(
            {
                "frequency_offset_hz": float(frequency_hz),
                "amplitude": float(amplitude),
                "phase_rad": float(phase_rad),
            }
        )
    return _make(scenario, config, values, metadata={"components": metadata_components})


def _splitmix64(seed: int, count: int) -> np.ndarray:
    """Generate fixed uint64 words using only specified integer operations."""

    output = np.empty(count, dtype="<u8")
    state = seed & _UINT64_MASK
    for index in range(count):
        state = (state + 0x9E37_79B9_7F4A_7C15) & _UINT64_MASK
        word = state
        word = ((word ^ (word >> 30)) * 0xBF58_476D_1CE4_E5B9) & _UINT64_MASK
        word = ((word ^ (word >> 27)) * 0x94D0_49BB_1331_11EB) & _UINT64_MASK
        output[index] = (word ^ (word >> 31)) & _UINT64_MASK
    output.setflags(write=False)
    return output


def broadband_noise(
    config: SyntheticConfig,
    *,
    peak_component: float = 0.125,
) -> SyntheticSignal:
    """Return deterministic complex uniform white noise on an exact binary grid."""

    if not math.isfinite(peak_component) or peak_component <= 0.0 or peak_component > 1.0:
        raise ValueError("peak_component must be in (0, 1]")
    words = _splitmix64(config.seed, config.sample_count * 2)
    mantissas = np.right_shift(words, np.uint64(11)).astype(np.float64)
    uniform = mantissas / float(1 << 52) - 1.0
    values = peak_component * (
        uniform[0::2] + 1j * uniform[1::2]
    )
    return _make(
        SyntheticScenario.BROADBAND_NOISE,
        config,
        values,
        metadata={
            "distribution": "deterministic_uniform_complex",
            "peak_component": float(peak_component),
            "seed": config.seed,
        },
    )


def dc_offset(
    config: SyntheticConfig,
    *,
    i_offset: float = 0.25,
    q_offset: float = -0.125,
) -> SyntheticSignal:
    if not math.isfinite(i_offset) or not math.isfinite(q_offset):
        raise ValueError("DC offsets must be finite")
    values = np.full(config.sample_count, complex(i_offset, q_offset), dtype=np.complex128)
    return _make(
        SyntheticScenario.DC_OFFSET,
        config,
        values,
        metadata={"i_offset": float(i_offset), "q_offset": float(q_offset)},
    )


def impulse(
    config: SyntheticConfig,
    *,
    index: int | None = None,
    amplitude: complex = 1.0 + 0.0j,
) -> SyntheticSignal:
    selected = config.sample_count // 4 if index is None else index
    if isinstance(selected, bool) or not isinstance(selected, int) or not 0 <= selected < config.sample_count:
        raise ValueError("impulse index is outside the signal")
    if not math.isfinite(amplitude.real) or not math.isfinite(amplitude.imag):
        raise ValueError("impulse amplitude must be finite")
    values = np.zeros(config.sample_count, dtype=np.complex128)
    values[selected] = amplitude
    return _make(
        SyntheticScenario.IMPULSE,
        config,
        values,
        metadata={"index": selected, "amplitude_real": amplitude.real, "amplitude_imag": amplitude.imag},
    )


def clipped_tone(
    config: SyntheticConfig,
    *,
    frequency_offset_hz: float | None = None,
    amplitude: float = 1.5,
    component_limit: float = 1.0 - 2.0**-15,
) -> SyntheticSignal:
    frequency = 64.0 * config.bin_width_hz if frequency_offset_hz is None else frequency_offset_hz
    if not math.isfinite(amplitude) or amplitude <= 1.0:
        raise ValueError("clipping test amplitude must exceed full scale")
    if not math.isfinite(component_limit) or not 0.0 < component_limit < 1.0:
        raise ValueError("component_limit must be in (0, 1)")
    raw = amplitude * np.exp(1j * _phase(config, frequency))
    clipped = np.clip(raw.real, -component_limit, component_limit) + 1j * np.clip(
        raw.imag,
        -component_limit,
        component_limit,
    )
    clipped_count = int(np.count_nonzero((raw.real != clipped.real) | (raw.imag != clipped.imag)))
    return _make(
        SyntheticScenario.CLIPPING,
        config,
        clipped,
        quality_flags=QualityFlag.ADC_OVERLOAD,
        metadata={
            "frequency_offset_hz": float(frequency),
            "requested_amplitude": float(amplitude),
            "component_limit": float(component_limit),
            "clipped_sample_count": clipped_count,
        },
    )


def iq_imbalanced_tone(
    config: SyntheticConfig,
    *,
    frequency_offset_hz: float | None = None,
    amplitude: float = 0.75,
    gain_mismatch: float = 0.2,
    phase_error_rad: float = math.radians(8.0),
) -> SyntheticSignal:
    frequency = 96.0 * config.bin_width_hz if frequency_offset_hz is None else frequency_offset_hz
    if not math.isfinite(gain_mismatch) or abs(gain_mismatch) >= 2.0:
        raise ValueError("gain_mismatch magnitude must be less than 2")
    if not math.isfinite(phase_error_rad):
        raise ValueError("phase_error_rad must be finite")
    phase = _phase(config, frequency)
    i_values = amplitude * (1.0 + gain_mismatch / 2.0) * np.cos(phase)
    q_values = amplitude * (1.0 - gain_mismatch / 2.0) * np.sin(phase + phase_error_rad)
    return _make(
        SyntheticScenario.IQ_IMBALANCE,
        config,
        i_values + 1j * q_values,
        metadata={
            "frequency_offset_hz": float(frequency),
            "amplitude": float(amplitude),
            "gain_mismatch": float(gain_mismatch),
            "phase_error_rad": float(phase_error_rad),
        },
    )


def chirp(
    config: SyntheticConfig,
    *,
    start_offset_hz: float | None = None,
    stop_offset_hz: float | None = None,
    amplitude: float = 0.75,
) -> SyntheticSignal:
    start = -0.25 * config.sample_rate_hz if start_offset_hz is None else float(start_offset_hz)
    stop = 0.25 * config.sample_rate_hz if stop_offset_hz is None else float(stop_offset_hz)
    if not -config.sample_rate_hz / 2.0 < start < config.sample_rate_hz / 2.0:
        raise ValueError("chirp start is outside complex bandwidth")
    if not -config.sample_rate_hz / 2.0 < stop < config.sample_rate_hz / 2.0 or stop <= start:
        raise ValueError("chirp stop must be above start inside complex bandwidth")
    n = np.arange(config.sample_count, dtype=np.float64)
    duration = float(config.sample_count) / config.sample_rate_hz
    slope = (stop - start) / duration
    time = n / config.sample_rate_hz
    phase = 2.0 * np.pi * (start * time + 0.5 * slope * time * time)
    return _make(
        SyntheticScenario.CHIRP,
        config,
        amplitude * np.exp(1j * phase),
        metadata={
            "start_offset_hz": start,
            "stop_offset_hz": stop,
            "amplitude": float(amplitude),
        },
    )


def hopping(
    config: SyntheticConfig,
    *,
    offsets_hz: tuple[float, ...] | None = None,
    amplitude: float = 0.75,
) -> SyntheticSignal:
    if offsets_hz is None:
        frequencies = np.asarray((-192.0, -64.0, 64.0, 192.0), dtype=np.float64)
        frequencies *= config.bin_width_hz
    else:
        frequencies = np.asarray(offsets_hz, dtype=np.float64)
    if frequencies.size == 0 or np.any(np.abs(frequencies) >= config.sample_rate_hz / 2.0):
        raise ValueError("hop frequencies must lie inside complex bandwidth")
    segments = np.array_split(np.arange(config.sample_count), frequencies.size)
    per_sample_frequency = np.empty(config.sample_count, dtype=np.float64)
    for frequency, segment in zip(frequencies, segments, strict=True):
        per_sample_frequency[segment] = frequency
    phase = np.empty(config.sample_count, dtype=np.float64)
    phase[0] = 0.0
    if config.sample_count > 1:
        phase[1:] = 2.0 * np.pi * np.cumsum(per_sample_frequency[:-1], dtype=np.float64) / config.sample_rate_hz
    return _make(
        SyntheticScenario.HOPPING,
        config,
        amplitude * np.exp(1j * phase),
        metadata={
            "offsets_hz": [float(item) for item in frequencies],
            "amplitude": float(amplitude),
        },
    )


def amplitude_burst(
    config: SyntheticConfig,
    *,
    frequency_offset_hz: float | None = None,
    amplitude: float = 0.75,
    start_fraction: float = 0.375,
    duty_fraction: float = 0.25,
) -> SyntheticSignal:
    frequency = 128.0 * config.bin_width_hz if frequency_offset_hz is None else frequency_offset_hz
    if not 0.0 <= start_fraction < 1.0 or not 0.0 < duty_fraction <= 1.0:
        raise ValueError("burst fractions are invalid")
    start = int(math.floor(config.sample_count * start_fraction))
    stop = min(config.sample_count, start + max(1, int(math.floor(config.sample_count * duty_fraction))))
    envelope = np.zeros(config.sample_count, dtype=np.float64)
    envelope[start:stop] = amplitude
    values = envelope * np.exp(1j * _phase(config, frequency))
    return _make(
        SyntheticScenario.AMPLITUDE_BURST,
        config,
        values,
        metadata={
            "frequency_offset_hz": float(frequency),
            "amplitude": float(amplitude),
            "start_index": start,
            "stop_index_exclusive": stop,
        },
    )


def generate_scenario(
    scenario: SyntheticScenario,
    config: SyntheticConfig | None = None,
) -> SyntheticSignal:
    selected = SyntheticConfig() if config is None else config
    if not isinstance(scenario, SyntheticScenario):
        raise TypeError("scenario must be SyntheticScenario")
    bin_width = selected.bin_width_hz
    if scenario is SyntheticScenario.EXACT_BIN_TONE:
        return complex_tone(selected, 128.0 * bin_width)
    if scenario is SyntheticScenario.HALF_BIN_TONE:
        return complex_tone(
            selected,
            128.5 * bin_width,
            amplitude=0.75,
            scenario=SyntheticScenario.HALF_BIN_TONE,
        )
    if scenario is SyntheticScenario.TWO_TONES:
        return multi_tone(
            selected,
            ((96.0 * bin_width, 0.5, 0.0), (-144.0 * bin_width, 0.25, 0.25)),
        )
    if scenario is SyntheticScenario.CLOSE_TONES:
        return multi_tone(
            selected,
            ((100.0 * bin_width, 0.5, 0.0), (103.0 * bin_width, 0.4, 0.5)),
            scenario=SyntheticScenario.CLOSE_TONES,
        )
    if scenario is SyntheticScenario.BROADBAND_NOISE:
        return broadband_noise(selected)
    if scenario is SyntheticScenario.DC_OFFSET:
        return dc_offset(selected)
    if scenario is SyntheticScenario.IMPULSE:
        return impulse(selected)
    if scenario is SyntheticScenario.CLIPPING:
        return clipped_tone(selected)
    if scenario is SyntheticScenario.IQ_IMBALANCE:
        return iq_imbalanced_tone(selected)
    if scenario is SyntheticScenario.CHIRP:
        return chirp(selected)
    if scenario is SyntheticScenario.HOPPING:
        return hopping(selected)
    if scenario is SyntheticScenario.AMPLITUDE_BURST:
        return amplitude_burst(selected)
    raise ValueError(f"unsupported synthetic scenario: {scenario}")  # pragma: no cover


def signal_sha256(signal: SyntheticSignal) -> str:
    metadata = {
        "scenario": signal.scenario.value,
        "sample_count": signal.config.sample_count,
        "sample_rate_hz": signal.config.sample_rate_hz,
        "center_frequency_hz": signal.config.center_frequency_hz,
        "seed": signal.config.seed,
        "quantization_bits": signal.config.quantization_bits,
        "quality_flags": int(signal.quality_flags),
        "metadata": dict(signal.metadata),
    }
    digest = hashlib.sha256()
    digest.update(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(signal.samples.dtype.str.encode("ascii"))
    digest.update(np.asarray(signal.samples.shape, dtype="<u8").tobytes())
    digest.update(signal.samples.tobytes(order="C"))
    return digest.hexdigest().upper()


__all__ = [
    "DEFAULT_CENTER_FREQUENCY_HZ",
    "DEFAULT_SAMPLE_COUNT",
    "DEFAULT_SAMPLE_RATE_HZ",
    "DEFAULT_SEED",
    "SAMPLE_QUANTIZATION_BITS",
    "SyntheticConfig",
    "SyntheticScenario",
    "SyntheticSignal",
    "amplitude_burst",
    "broadband_noise",
    "chirp",
    "clipped_tone",
    "complex_tone",
    "dc_offset",
    "generate_scenario",
    "hopping",
    "impulse",
    "iq_imbalanced_tone",
    "multi_tone",
    "signal_sha256",
]
