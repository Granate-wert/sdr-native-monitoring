"""R11-N explicit HackRF factory tests with no native module or device."""

from __future__ import annotations

from pathlib import Path
import unittest

from sdr_monitor.domain import BackendKind
from sdr_monitor.services import (
    HackrfBoardKind,
    HackrfCapabilityAdapter,
    HackrfActivationPreflightService,
    HackrfLiveActivationPlan,
    HackrfLiveRequest,
    HackrfNativeFactoryError,
    HackrfNativeFactoryFailure,
    HackrfNativeRuntimeFactory,
    HackrfReadOnlyProbe,
    HackrfRuntimeIdentityProbe,
    admit_hackrf_live,
)


ROOT = Path(__file__).resolve().parents[1]
SERVICE_SOURCE = ROOT / "sdr_monitor/services/hackrf_native_factory.py"
BINDING_SOURCE = ROOT / "native/sdr_core/bindings/hackrf_factory_binding.cpp"
CMAKE = ROOT / "native/sdr_core/CMakeLists.txt"


class _Port:
    def probe(self) -> HackrfReadOnlyProbe:
        return HackrfReadOnlyProbe(
            HackrfBoardKind.HACKRF_ONE,
            (0, 0, 0x010961DC, 0x2B78454F),
            "2024.02.1",
            0x0107,
        )

    def close(self) -> None:
        return None


def _plan() -> object:
    observed = HackrfCapabilityAdapter(_Port).observe()
    request = HackrfLiveRequest(
        center_frequency_hz=100e6,
        sample_rate_hz=10e6,
        baseband_filter_hz=8_000_000,
        lna_gain_db=16,
        vga_gain_db=20,
        fft_size=4096,
        hop_size=2048,
        window="hann",
        detector="sample",
        backend=BackendKind.CPU,
        slot_count=32,
        ready_capacity=24,
        dsp_output_capacity=8,
        presentation_capacity=4,
        configuration_generation=9,
        source_id="native.hackrf.live",
    )
    result = admit_hackrf_live(
        observed.snapshot, observed.calibration_identity, request
    )
    assert result.plan is not None
    return result.plan


class _IdentityPort:
    def probe(self) -> HackrfRuntimeIdentityProbe:
        return HackrfRuntimeIdentityProbe(
            HackrfBoardKind.HACKRF_ONE,
            (0, 0, 0x010961DC, 0x2B78454F),
        )

    def close(self) -> None:
        return None


def _permit() -> object:
    plan = _plan()
    assert isinstance(plan, HackrfLiveActivationPlan)
    preflight = HackrfActivationPreflightService(_IdentityPort).verify(plan)
    assert preflight.permit is not None
    return preflight.permit


class _WindowType:
    HANN = "window:hann"


class _DetectorType:
    SAMPLE = "detector:sample"


class _NativeFactory:
    WindowType = _WindowType
    DetectorType = _DetectorType

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.control = object()

    def create_hackrf_runtime_dsp_control(self, **values: object) -> object:
        self.calls.append(values)
        return self.control


class R11NHackrfNativeFactoryTests(unittest.TestCase):
    def test_issued_preflight_permit_is_the_only_route_to_one_explicit_native_call(self) -> None:
        native = _NativeFactory()
        factory = HackrfNativeRuntimeFactory(lambda: native)

        control = factory.create(_permit())  # type: ignore[arg-type]

        self.assertIs(control, native.control)
        self.assertEqual(len(native.calls), 1)
        values = native.calls[0]
        self.assertEqual(values["center_frequency_hz"], 100e6)
        self.assertEqual(values["sample_rate_hz"], 10e6)
        self.assertEqual(values["baseband_filter_hz"], 8_000_000)
        self.assertEqual(values["lna_gain_db"], 16)
        self.assertEqual(values["vga_gain_db"], 20)
        self.assertFalse(values["rf_amplifier_enabled"])
        self.assertFalse(values["bias_tee_enabled"])
        self.assertEqual(values["fft_size"], 4096)
        self.assertEqual(values["hop_size"], 2048)
        self.assertEqual(values["window"], "window:hann")
        self.assertEqual(values["detector"], "detector:sample")
        self.assertEqual(values["slot_count"], 32)
        self.assertEqual(values["ready_capacity"], 24)
        self.assertEqual(values["dsp_output_capacity"], 8)
        self.assertEqual(values["presentation_capacity"], 4)
        self.assertEqual(values["configuration_generation"], 9)
        self.assertEqual(values["source_id"], "native.hackrf.live")

    def test_unissued_or_unavailable_preflight_fails_closed_without_native_call(self) -> None:
        native = _NativeFactory()
        factory = HackrfNativeRuntimeFactory(lambda: native)
        forged = object.__new__(HackrfLiveActivationPlan)

        with self.assertRaises(HackrfNativeFactoryError) as rejected:
            factory.create(forged)
        self.assertIs(
            rejected.exception.failure,
            HackrfNativeFactoryFailure.PREFLIGHT_NOT_ADMITTED,
        )
        self.assertEqual(native.calls, [])

        missing = HackrfNativeRuntimeFactory(lambda: object())
        with self.assertRaises(HackrfNativeFactoryError) as unavailable:
            missing.create(_permit())  # type: ignore[arg-type]
        self.assertIs(
            unavailable.exception.failure,
            HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE,
        )

        with self.assertRaises(HackrfNativeFactoryError) as load_failed:
            HackrfNativeRuntimeFactory(
                lambda: (_ for _ in ()).throw(ImportError("not packaged"))
            ).create(_permit())  # type: ignore[arg-type]
        self.assertIs(
            load_failed.exception.failure,
            HackrfNativeFactoryFailure.NATIVE_FACTORY_UNAVAILABLE,
        )

    def test_same_permit_has_one_native_factory_call_only(self) -> None:
        native = _NativeFactory()
        factory = HackrfNativeRuntimeFactory(lambda: native)
        permit = _permit()

        self.assertIs(factory.create(permit), native.control)  # type: ignore[arg-type]
        with self.assertRaises(HackrfNativeFactoryError) as consumed:
            factory.create(permit)  # type: ignore[arg-type]

        self.assertIs(
            consumed.exception.failure,
            HackrfNativeFactoryFailure.PERMIT_ALREADY_CONSUMED,
        )
        self.assertEqual(len(native.calls), 1)

    def test_native_exception_is_redacted_and_never_falls_back(self) -> None:
        class _FailingNative(_NativeFactory):
            def create_hackrf_runtime_dsp_control(self, **values: object) -> object:
                self.calls.append(values)
                raise RuntimeError("route and SDK details must not cross R11-N")

        native = _FailingNative()
        with self.assertRaises(HackrfNativeFactoryError) as failed:
            HackrfNativeRuntimeFactory(lambda: native).create(_permit())  # type: ignore[arg-type]
        self.assertIs(
            failed.exception.failure,
            HackrfNativeFactoryFailure.ACTIVATION_FAILED,
        )
        self.assertIsNone(failed.exception.__cause__)
        self.assertEqual(len(native.calls), 1)

    def test_public_surface_is_gated_and_excludes_product_live_or_raw_iq(self) -> None:
        service = SERVICE_SOURCE.read_text(encoding="utf-8").lower()
        binding = BINDING_SOURCE.read_text(encoding="utf-8")
        cmake = CMAKE.read_text(encoding="utf-8")
        for forbidden in (
            "hackrf.h",
            "ctypes",
            "start_tx",
            "native_live_session",
            "recording",
            "sweep",
            "pyside",
            "qwidget",
            "raw_iq",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, service)
        self.assertIn("create_hackrf_runtime_dsp_control", binding)
        self.assertIn("bindings/hackrf_factory_binding.cpp", cmake)
        self.assertIn("SDR_CORE_HACKRF_OFFICIAL_COMPILED", cmake)
        self.assertIn("SDR_CORE_ENABLE_HACKRF_OFFICIAL", cmake)
        self.assertIn("sdr_core::sdr_hackrf_official", cmake)


if __name__ == "__main__":
    unittest.main()
