"""Typed, inert RTL-SDR RTBW intent; no vendor library or USB access.

The antenna's printed frequency range and the USB VID/PID are not tuner
capabilities. This request only bounds what the application is prepared to
ask a subsequently identified tuner to do. Native readback is still required.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .identity import SourceId, as_source_id
from .live import BackendKind


RTL_RATE_CHOICES_HZ = frozenset((2_048_000, 2_400_000))
RTL_FFT_CHOICES = frozenset((1024, 2048, 4096))


@dataclass(frozen=True, slots=True)
class RtlLiveRequest:
    """One RX1 CPU-DSP profile, automatic gain by default and no bias control.

    Automatic gain avoids pretending that the tuner-specific gain table or
    gain readback is known before a selected runtime probe. Bias-tee state is
    not observed here and this application never issues a bias command.
    """

    center_frequency_hz: int
    sample_rate_hz: int
    fft_size: int = 4096
    hop_size: int = 2048
    window: str = "hann"
    detector: str = "sample"
    backend: BackendKind = BackendKind.CPU
    slot_count: int = 8
    ready_capacity: int = 6
    dsp_output_capacity: int | None = None
    presentation_capacity: int = 4
    configuration_generation: int = 1
    source_id: SourceId = SourceId("native.rtl_sdr.live")
    # Exact selected-tuner table entry, not a continuous gain range/RF readback.
    manual_tuner_gain_tenth_db: int | None = None

    def __post_init__(self) -> None:
        gain = self.manual_tuner_gain_tenth_db
        if gain is not None and (type(gain) is not int or not -1000 <= gain <= 1000):
            raise ValueError("RTL manual gain must be a bounded integer in tenths of dB")
        if type(self.center_frequency_hz) is not int or not 1 <= self.center_frequency_hz <= 4_294_967_295:
            raise ValueError("RTL center must fit the vendor uint32 Hz control")
        if type(self.sample_rate_hz) is not int or self.sample_rate_hz not in RTL_RATE_CHOICES_HZ:
            raise ValueError("RTL sample rate must be an explicitly bounded profile")
        if type(self.fft_size) is not int or self.fft_size not in RTL_FFT_CHOICES:
            raise ValueError("RTL FFT must be a bounded physical transform choice")
        if type(self.hop_size) is not int or self.hop_size != self.fft_size // 2:
            raise ValueError("initial RTL DSP profile requires an exact half-FFT hop")
        if self.window != "hann" or self.detector not in {"sample", "peak"}:
            raise ValueError("RTL CPU DSP currently qualifies Hann and sample/peak only")
        if self.backend is not BackendKind.CPU:
            raise ValueError("RTL DSP requires the CPU backend")
        if (type(self.slot_count) is not int or not 2 <= self.slot_count <= 32
                or type(self.ready_capacity) is not int or not 1 <= self.ready_capacity < self.slot_count
                or type(self.presentation_capacity) is not int or not 1 <= self.presentation_capacity <= 64):
            raise ValueError("RTL acquisition and reduced-output queues must remain bounded")
        minimum_outputs = (16_384 + self.hop_size - 1) // self.hop_size + 2
        output_capacity = minimum_outputs if self.dsp_output_capacity is None else self.dsp_output_capacity
        if type(output_capacity) is not int or not minimum_outputs <= output_capacity <= 256:
            raise ValueError("RTL analytical queue cannot hold one full CU8 input burst")
        # Bounded queue-payload admission estimate, not whole-process RSS or
        # a native FFT-workspace/persistence allocation proof. No second IQ pool.
        if (self.slot_count * 32_768 +
                (2 * output_capacity + self.presentation_capacity) * (self.fft_size * 12 + 1024)
                > 64 * 1024 * 1024):
            raise ValueError("RTL requested queues exceed the 64 MiB component budget")
        if (type(self.configuration_generation) is not int
                or not 1 <= self.configuration_generation < (1 << 63)):
            raise ValueError("RTL generation must be a positive bounded integer")
        source = as_source_id(self.source_id)
        if len(source) > 128 or any(token in source.casefold() for token in ("usb:", "ip:", "\\", "/")):
            raise ValueError("RTL source ID must be opaque and route-free")
        object.__setattr__(self, "source_id", source)

    @property
    def spectrum_stall_timeout_s(self) -> float:
        # A host watchdog, not a promised RF frame cadence.
        value = 3.0 * (self.fft_size + self.hop_size) / self.sample_rate_hz
        return max(2.0, min(30.0, value if math.isfinite(value) else 30.0))

    @property
    def resolved_dsp_output_capacity(self) -> int:
        if self.dsp_output_capacity is not None:
            return self.dsp_output_capacity
        return (16_384 + self.hop_size - 1) // self.hop_size + 2

    @property
    def tuner_gain_mode(self) -> str:
        return "automatic" if self.manual_tuner_gain_tenth_db is None else "manual"


@dataclass(frozen=True, slots=True)
class RtlConfigurationPatch:
    request: RtlLiveRequest
    selection_revision: int
    expected_generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.request, RtlLiveRequest):
            raise TypeError("RTL patch requires its exact typed request")
        for name in ("selection_revision", "expected_generation"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value < (1 << 63):
                raise ValueError(f"RTL {name} is outside its bounded range")


__all__ = ["RTL_FFT_CHOICES", "RTL_RATE_CHOICES_HZ", "RtlConfigurationPatch", "RtlLiveRequest"]
