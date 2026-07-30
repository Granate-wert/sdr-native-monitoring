from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PowerMeasurementProfile:
    name: str
    main_bandwidth_hz: float
    adjacent_offsets_hz: tuple[float, ...] = ()
    adjacent_bandwidths_hz: tuple[float, ...] = ()
    obw_percent: float = 99.0
    default_time_mode: str = "current"
    default_activity_threshold_db: float = 10.0
    default_span_hz: float | None = None
    warnings: tuple[str, ...] = ()


def _profile(name: str, bandwidth_hz: float, *, offset_factor: float = 1.0) -> PowerMeasurementProfile:
    return PowerMeasurementProfile(
        name,
        bandwidth_hz,
        (bandwidth_hz * offset_factor,),
        (bandwidth_hz,),
        default_span_hz=bandwidth_hz * 5,
    )


BUILTIN_POWER_PROFILES: tuple[PowerMeasurementProfile, ...] = (
    PowerMeasurementProfile("Custom", 0.0),
    _profile("Wi-Fi 20 MHz", 20e6),
    _profile("Wi-Fi 40 MHz", 40e6),
    _profile("Wi-Fi 80 MHz", 80e6),
    _profile("Wi-Fi 160 MHz", 160e6),
    _profile("Wi-Fi 320 MHz", 320e6),
    _profile("LTE 1.4 MHz", 1.4e6),
    _profile("LTE 3 MHz", 3e6),
    _profile("LTE 5 MHz", 5e6),
    _profile("LTE 10 MHz", 10e6),
    _profile("LTE 15 MHz", 15e6),
    _profile("LTE 20 MHz", 20e6),
    PowerMeasurementProfile("5G NR Custom", 0.0),
    _profile("NB-IoT 180 kHz", 180e3),
    _profile("LTE-M 1.4 MHz", 1.4e6),
    _profile("GSM 200 kHz", 200e3),
    _profile("UMTS 5 MHz", 5e6),
    _profile("Bluetooth BR/EDR", 1e6),
    _profile("Bluetooth LE", 2e6),
    _profile("Zigbee", 2e6),
    _profile("LoRa 125 kHz", 125e3),
    _profile("LoRa 250 kHz", 250e3),
    _profile("LoRa 500 kHz", 500e3),
    _profile("TETRA 25 kHz", 25e3),
    _profile("DMR 12.5 kHz", 12.5e3),
    _profile("dPMR 6.25 kHz", 6.25e3),
    PowerMeasurementProfile("Generic Narrowband", 0.0),
    PowerMeasurementProfile("Generic OFDM", 0.0),
)


def profile_by_name(name: str) -> PowerMeasurementProfile:
    return next(profile for profile in BUILTIN_POWER_PROFILES if profile.name == name)
