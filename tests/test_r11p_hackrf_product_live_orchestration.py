"""R11-P product-Live orchestration tests with fake native factory only."""

from __future__ import annotations

from pathlib import Path
import unittest

from sdr_monitor.domain import BackendKind
from sdr_monitor.services import (
    HackrfActivationPreflightService,
    HackrfBoardKind,
    HackrfCapabilityAdapter,
    HackrfLiveRequest,
    HackrfNativeRuntimeFactory,
    HackrfProductLiveCoordinator,
    HackrfProductLiveFailure,
    HackrfProductLiveState,
    HackrfReadOnlyProbe,
    HackrfRuntimeIdentityProbe,
    admit_hackrf_live,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sdr_monitor/services/hackrf_product_live.py"

_WORDS = (0, 0, 0x010961DC, 0x2B78454F)


class _CapabilityPort:
    def probe(self) -> HackrfReadOnlyProbe:
        return HackrfReadOnlyProbe(HackrfBoardKind.HACKRF_ONE, _WORDS, "2024.02.1", 0x0107)

    def close(self) -> None:
        return None


class _IdentityPort:
    def probe(self) -> HackrfRuntimeIdentityProbe:
        return HackrfRuntimeIdentityProbe(HackrfBoardKind.HACKRF_ONE, _WORDS)

    def close(self) -> None:
        return None


def _permit() -> object:
    observed = HackrfCapabilityAdapter(_CapabilityPort).observe()
    admission = admit_hackrf_live(
        observed.snapshot,
        observed.calibration_identity,
        HackrfLiveRequest(
            center_frequency_hz=100e6,
            sample_rate_hz=10e6,
            baseband_filter_hz=8_000_000,
            lna_gain_db=16,
            vga_gain_db=20,
            backend=BackendKind.CPU,
        ),
    )
    assert admission.plan is not None
    preflight = HackrfActivationPreflightService(_IdentityPort).verify(admission.plan)
    assert preflight.permit is not None
    return preflight.permit


class _StopResult:
    def __init__(self, complete: bool) -> None:
        self._complete = complete

    def complete(self) -> bool:
        return self._complete


class _Control:
    def __init__(self, *, stop_complete: bool = True) -> None:
        self.stop_complete = stop_complete
        self.stop_calls: list[int] = []

    def poll_spectrum_frames(self, max_items: int = 0) -> list[object]:
        return ["reduced-frame"][:max_items or 1]

    def metrics(self) -> object:
        return {"bounded": True}

    def stop(self, timeout_ms: int) -> _StopResult:
        self.stop_calls.append(timeout_ms)
        return _StopResult(self.stop_complete)


class _WindowType:
    HANN = "window:hann"


class _DetectorType:
    SAMPLE = "detector:sample"


class _NativeFactory:
    WindowType = _WindowType
    DetectorType = _DetectorType

    def __init__(self, control: object) -> None:
        self.control = control
        self.calls: list[dict[str, object]] = []

    def create_hackrf_runtime_dsp_control(self, **values: object) -> object:
        self.calls.append(values)
        return self.control


class R11PHackrfProductLiveOrchestrationTests(unittest.TestCase):
    def test_start_is_explicit_and_owns_one_bounded_coarse_control(self) -> None:
        native = _NativeFactory(_Control())
        coordinator = HackrfProductLiveCoordinator(HackrfNativeRuntimeFactory(lambda: native))
        permit = _permit()

        self.assertEqual(coordinator.snapshot().state, HackrfProductLiveState.IDLE)
        refused = coordinator.start_after_confirmation(permit, user_confirmed=False)  # type: ignore[arg-type]
        self.assertIs(refused.failure, HackrfProductLiveFailure.CONFIRMATION_REQUIRED)
        self.assertEqual(native.calls, [])

        started = coordinator.start_after_confirmation(permit, user_confirmed=True)  # type: ignore[arg-type]
        self.assertTrue(started.started)
        self.assertEqual(started.snapshot.state, HackrfProductLiveState.ACTIVE)
        self.assertEqual(len(native.calls), 1)
        self.assertEqual(coordinator.poll_spectrum_frames(1), ("reduced-frame",))
        self.assertEqual(coordinator.metrics(), {"bounded": True})

        duplicate = coordinator.start_after_confirmation(_permit(), user_confirmed=True)  # type: ignore[arg-type]
        self.assertIs(duplicate.failure, HackrfProductLiveFailure.ALREADY_ACTIVE)
        self.assertEqual(len(native.calls), 1)

        stopped = coordinator.stop(1_000)
        self.assertTrue(stopped.stopped)
        self.assertEqual(stopped.snapshot.state, HackrfProductLiveState.IDLE)
        self.assertEqual(native.control.stop_calls, [1_000])  # type: ignore[union-attr]

    def test_consumed_permit_and_factory_failure_have_no_implicit_retry(self) -> None:
        native = _NativeFactory(_Control())
        coordinator = HackrfProductLiveCoordinator(HackrfNativeRuntimeFactory(lambda: native))
        permit = _permit()
        self.assertTrue(coordinator.start_after_confirmation(permit, user_confirmed=True).started)  # type: ignore[arg-type]
        self.assertTrue(coordinator.stop(1_000).stopped)

        reused = coordinator.start_after_confirmation(permit, user_confirmed=True)  # type: ignore[arg-type]
        self.assertIs(reused.failure, HackrfProductLiveFailure.FACTORY_FAILED)
        self.assertEqual(len(native.calls), 1)

        class _FailingNative(_NativeFactory):
            def create_hackrf_runtime_dsp_control(self, **values: object) -> object:
                self.calls.append(values)
                raise RuntimeError("no SDK error detail")

        failing = _FailingNative(_Control())
        failed = HackrfProductLiveCoordinator(HackrfNativeRuntimeFactory(lambda: failing))
        result = failed.start_after_confirmation(_permit(), user_confirmed=True)  # type: ignore[arg-type]
        self.assertIs(result.failure, HackrfProductLiveFailure.FACTORY_FAILED)
        self.assertEqual(result.snapshot.state, HackrfProductLiveState.IDLE)
        self.assertEqual(len(failing.calls), 1)

    def test_stop_failure_retains_owner_and_rejects_new_start(self) -> None:
        control = _Control(stop_complete=False)
        native = _NativeFactory(control)
        coordinator = HackrfProductLiveCoordinator(HackrfNativeRuntimeFactory(lambda: native))
        self.assertTrue(coordinator.start_after_confirmation(_permit(), user_confirmed=True).started)  # type: ignore[arg-type]

        failed = coordinator.stop(1_000)
        self.assertIs(failed.failure, HackrfProductLiveFailure.STOP_FAILED)
        self.assertEqual(failed.snapshot.state, HackrfProductLiveState.ACTIVE)
        self.assertIs(
            coordinator.start_after_confirmation(_permit(), user_confirmed=True).failure,  # type: ignore[arg-type]
            HackrfProductLiveFailure.ALREADY_ACTIVE,
        )
        for timeout in (0, 5_001, True):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    coordinator.stop(timeout)  # type: ignore[arg-type]

    def test_module_has_no_sdk_or_raw_iq_or_product_ui_path(self) -> None:
        source = SOURCE.read_text(encoding="utf-8").lower()
        for forbidden in (
            "ctypes",
            "hackrf.h",
            "_sdr_native",
            "start_rx",
            "start_tx",
            "recording",
            "sweep",
            "pyside",
            "qwidget",
            "raw_iq",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
