"""Explicit, capability-plan-only construction of the native HackRF owner.

This layer is intentionally not product Live integration. Its only effecting
method is an explicit future user action over an R11-M-issued plan; it never
discovers, retries, substitutes a backend or passes raw I/Q to Python.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from importlib import import_module
from typing import cast

from .hackrf_capability_adapter import HACKRF_LIBHACKRF_ADAPTER_ID
from .hackrf_live_admission import (
    HackrfLiveActivationPlan,
    _is_issued_hackrf_live_activation_plan,
)


class HackrfNativeFactoryFailure(StrEnum):
    """Redacted fail-closed outcomes for native owner construction."""

    PLAN_NOT_ADMITTED = "plan_not_admitted"
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
    """One explicit bridge from an admitted plan to coarse native control."""

    def __init__(self, native_loader: Callable[[], object] = _load_canonical_native_module) -> None:
        if not callable(native_loader):
            raise ValueError("native_loader must be callable")
        self._native_loader = native_loader

    def create(self, plan: HackrfLiveActivationPlan) -> object:
        """Construct a native owner once; no fallback or automatic retry exists."""

        if (
            not _is_issued_hackrf_live_activation_plan(plan)
            or plan.adapter_id != HACKRF_LIBHACKRF_ADAPTER_ID
        ):
            raise HackrfNativeFactoryError(HackrfNativeFactoryFailure.PLAN_NOT_ADMITTED)
        try:
            native_module = self._native_loader()
        except Exception:
            raise HackrfNativeFactoryError(
                HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE
            ) from None
        try:
            native_factory = getattr(native_module, "create_hackrf_runtime_dsp_control", None)
            if not callable(native_factory):
                raise HackrfNativeFactoryError(
                    HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE
                )
            request = plan.request
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
                window=_native_window(native_module, request.window),
                detector=_native_detector(native_module, request.detector),
                slot_count=request.slot_count,
                ready_capacity=request.ready_capacity,
                dsp_output_capacity=request.dsp_output_capacity,
                presentation_capacity=request.presentation_capacity,
                configuration_generation=request.configuration_generation,
                source_id=str(request.source_id),
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
