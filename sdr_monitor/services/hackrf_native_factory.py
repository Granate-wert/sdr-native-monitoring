"""Explicit, capability-plan-only construction of the native HackRF owner.

This layer is intentionally not product Live integration.  Its only effecting
method is called by a future explicit user action and accepts an R11-O-issued
identity permit; it never discovers, retries, substitutes a backend or passes
raw I/Q to Python.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from importlib import import_module
from typing import cast

from .hackrf_capability_adapter import HACKRF_LIBHACKRF_ADAPTER_ID
from .native_owner_journal import EVENT_CAPACITY, owner_journal_capacity
from .native_layer_journal import LAYER_EVENT_CAPACITY, layer_journal_capacity
from .hackrf_activation_preflight import (
    HackrfActivationPermit,
    _claim_hackrf_activation_permit,
    _is_issued_hackrf_activation_permit,
)


class HackrfNativeFactoryFailure(StrEnum):
    """Redacted fail-closed outcomes for native owner construction."""

    PREFLIGHT_NOT_ADMITTED = "preflight_not_admitted"
    PERMIT_ALREADY_CONSUMED = "permit_already_consumed"
    NATIVE_FACTORY_UNAVAILABLE = "native_factory_unavailable"
    ACTIVATION_FAILED = "activation_failed"


class HackrfNativeFactoryError(RuntimeError):
    """A bounded failure without route, SDK or raw-device diagnostics."""

    def __init__(self, failure: HackrfNativeFactoryFailure) -> None:
        super().__init__(f"HackRF native factory failed: {failure.value}")
        self.failure = failure


def _load_canonical_native_module() -> object:
    return import_module("sdr_monitor._sdr_native")


def _native_window(native_module: object, value: str) -> object:
    names = {
        "rectangular": "RECTANGULAR",
        "hann": "HANN",
        "blackman_harris_4term": "BLACKMAN_HARRIS_4TERM",
        "flat_top": "FLAT_TOP",
        "nuttall": "NUTTALL",
        "kaiser": "KAISER",
    }
    enum_type = getattr(native_module, "WindowType", None)
    selected = getattr(enum_type, names.get(value, ""), None)
    if selected is None:
        raise HackrfNativeFactoryError(HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE)
    return selected


def _native_detector(native_module: object, value: str) -> object:
    names = {
        "sample": "SAMPLE",
        "peak": "PEAK",
        "negative_peak": "NEGATIVE_PEAK",
        "rms": "RMS",
        "average_power": "AVERAGE_POWER",
    }
    enum_type = getattr(native_module, "DetectorType", None)
    selected = getattr(enum_type, names.get(value, ""), None)
    if selected is None:
        raise HackrfNativeFactoryError(HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE)
    return selected


class HackrfNativeRuntimeFactory:
    """One explicit bridge from a preflight permit to coarse native control."""

    def __init__(self, native_loader: Callable[[], object] = _load_canonical_native_module,
                 *, analytical_event_capacity: int = 0, layer_event_capacity: int = 0) -> None:
        if not callable(native_loader):
            raise ValueError("native_loader must be callable")
        if type(analytical_event_capacity) is not int or analytical_event_capacity not in (0, EVENT_CAPACITY):
            raise ValueError("HackRF owner journal capacity was not admitted")
        self._native_loader = native_loader
        self._analytical_event_capacity = analytical_event_capacity
        if type(layer_event_capacity) is not int or layer_event_capacity not in (0, LAYER_EVENT_CAPACITY):
            raise ValueError("HackRF layer journal capacity was not admitted")
        self._layer_event_capacity = layer_event_capacity

    def create(self, permit: HackrfActivationPermit) -> object:
        """Construct a native owner once; no fallback or automatic retry exists."""

        if (
            not _is_issued_hackrf_activation_permit(permit)
            or permit.plan.adapter_id != HACKRF_LIBHACKRF_ADAPTER_ID
        ):
            raise HackrfNativeFactoryError(HackrfNativeFactoryFailure.PREFLIGHT_NOT_ADMITTED)
        plan = permit.plan
        try:
            native_module = self._native_loader()
        except Exception:
            raise HackrfNativeFactoryError(
                HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE
            ) from None
        try:
            native_factory = getattr(native_module, "create_hackrf_runtime_dsp_control", None)
            version = getattr(native_module, "HACKRF_FACTORY_CONTRACT_VERSION", None)
            if not callable(native_factory) or type(version) is not int or version != 2:
                raise HackrfNativeFactoryError(
                    HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE
                )
            request = plan.request
            extras: dict[str, object] = {}
            if self._analytical_event_capacity:
                if owner_journal_capacity(native_module) != self._analytical_event_capacity:
                    raise HackrfNativeFactoryError(HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE)
                extras["analytical_event_capacity"] = self._analytical_event_capacity
            if request.persistence_enabled:
                if self._layer_event_capacity:
                    if layer_journal_capacity(native_module, hackrf=True) != self._layer_event_capacity:
                        raise HackrfNativeFactoryError(HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE)
                    extras["layer_event_capacity"] = self._layer_event_capacity
                version = getattr(native_module, "HACKRF_PERSISTENCE_CONTRACT_VERSION", None)
                if type(version) is not int or version != 1:
                    raise HackrfNativeFactoryError(HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE)
                config_type = getattr(native_module, "PersistenceConfig", None)
                enum_type = getattr(native_module, "PersistenceMode", None)
                mode = getattr(enum_type, {"rolling-exact": "ROLLING_EXACT",
                    "exponential-decay": "EXPONENTIAL_DECAY"}.get(request.persistence_mode, ""), None)
                if not callable(config_type) or mode is None:
                    raise HackrfNativeFactoryError(HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE)
                # Validate/coerce the complete typed config BEFORE permit consume.
                extras["persistence"] = config_type(True, mode, request.persistence_window_frames,
                    request.persistence_half_life_s, request.persistence_power_min_db,
                    request.persistence_power_max_db, request.persistence_power_bins,
                    request.persistence_snapshot_rate_hz, 5)
            if request.averaging_frames != 1:
                dsp_version = getattr(native_module, "HACKRF_DSP_PROFILE_CONTRACT_VERSION", None)
                if type(dsp_version) is not int or dsp_version != 1:
                    raise HackrfNativeFactoryError(HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE)
                extras["averaging_frames"] = request.averaging_frames
            # Resolve all optional enums BEFORE consuming the identity permit.
            window = _native_window(native_module, request.window)
            detector = _native_detector(native_module, request.detector)
            if not _claim_hackrf_activation_permit(permit):
                raise HackrfNativeFactoryError(
                    HackrfNativeFactoryFailure.PERMIT_ALREADY_CONSUMED
                )
            control = cast(Callable[..., object], native_factory)(
                center_frequency_hz=request.center_frequency_hz,
                sample_rate_hz=request.sample_rate_hz,
                baseband_filter_hz=request.baseband_filter_hz,
                lna_gain_db=request.lna_gain_db,
                vga_gain_db=request.vga_gain_db,
                rf_amplifier_enabled=False,
                bias_tee_enabled=False,
                fft_size=request.fft_size,
                hop_size=request.hop_size,
                window=window,
                detector=detector,
                slot_count=request.slot_count,
                ready_capacity=request.ready_capacity,
                dsp_output_capacity=request.resolved_dsp_output_capacity,
                presentation_capacity=request.presentation_capacity,
                configuration_generation=request.configuration_generation,
                source_id=str(request.source_id),
                expected_serial_words=permit._serial_words,
                **extras,
            )
        except HackrfNativeFactoryError:
            raise
        except Exception:
            raise HackrfNativeFactoryError(
                HackrfNativeFactoryFailure.ACTIVATION_FAILED
            ) from None
        if control is None:
            raise HackrfNativeFactoryError(HackrfNativeFactoryFailure.ACTIVATION_FAILED)
        return control


__all__ = [
    "HackrfNativeFactoryError",
    "HackrfNativeFactoryFailure",
    "HackrfNativeRuntimeFactory",
]
