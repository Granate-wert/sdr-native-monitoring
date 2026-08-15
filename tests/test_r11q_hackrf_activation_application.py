"""R11-Q application adapter tests with fake preflight/factory only."""

from __future__ import annotations

from pathlib import Path
import unittest

from sdr_monitor.application import (
    HackrfActivationApplicationReason,
    HackrfActivationApplicationState,
    HackrfLiveActivationApplicationService,
)
from sdr_monitor.domain import BackendKind
from sdr_monitor.services import (
    HackrfActivationPreflight,
    HackrfActivationPreflightReason,
    HackrfActivationPreflightService,
    HackrfBoardKind,
    HackrfCapabilityAdapter,
    HackrfLiveRequest,
    HackrfNativeRuntimeFactory,
    HackrfProductLiveCoordinator,
    HackrfReadOnlyProbe,
    HackrfRuntimeIdentityProbe,
    admit_hackrf_live,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sdr_monitor/application/hackrf_live_activation.py"
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


def _plan() -> object:
    observed = HackrfCapabilityAdapter(_CapabilityPort).observe()
    result = admit_hackrf_live(
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
    assert result.plan is not None
    return result.plan


class _Preflight:
    def __init__(self, result: HackrfActivationPreflight | None = None) -> None:
        self.calls: list[object] = []
        self._result = result
        self._real = HackrfActivationPreflightService(_IdentityPort)

    def verify(self, plan: object) -> HackrfActivationPreflight:
        self.calls.append(plan)
        if self._result is not None:
            return self._result
        return self._real.verify(plan)  # type: ignore[arg-type]


class _Stop:
    def __init__(self, complete: bool) -> None:
        self._complete = complete

    def complete(self) -> bool:
        return self._complete


class _Control:
    def __init__(self, complete: bool = True) -> None:
        self._complete = complete
        self.stop_calls: list[int] = []

    def poll_spectrum_frames(self, max_items: int = 0) -> list[object]:
        return ["frame"][:max_items or 1]

    def metrics(self) -> object:
        return {"scalar": True}

    def stop(self, timeout_ms: int) -> _Stop:
        self.stop_calls.append(timeout_ms)
        return _Stop(self._complete)


class _WindowType:
    HANN = "window:hann"


class _DetectorType:
    SAMPLE = "detector:sample"


class _Native:
    WindowType = _WindowType
    DetectorType = _DetectorType

    def __init__(self, control: _Control) -> None:
        self.control = control
        self.calls: list[dict[str, object]] = []

    def create_hackrf_runtime_dsp_control(self, **values: object) -> _Control:
        self.calls.append(values)
        return self.control


def _service(
    *,
    preflight: _Preflight | None = None,
    control: _Control | None = None,
) -> tuple[HackrfLiveActivationApplicationService, _Preflight, _Native]:
    selected_preflight = preflight or _Preflight()
    native = _Native(control or _Control())
    coordinator = HackrfProductLiveCoordinator(HackrfNativeRuntimeFactory(lambda: native))
    return HackrfLiveActivationApplicationService(selected_preflight, coordinator), selected_preflight, native


class R11QHackrfActivationApplicationTests(unittest.TestCase):
    def test_explicit_preflight_then_confirmation_is_the_only_start_route(self) -> None:
        service, preflight, native = _service()
        self.assertEqual(service.current().state, HackrfActivationApplicationState.READY_FOR_PREFLIGHT)
        self.assertIs(
            service.confirm_start(user_confirmed=True).reason,
            HackrfActivationApplicationReason.NO_PENDING_PERMIT,
        )
        self.assertEqual(preflight.calls, [])
        self.assertEqual(native.calls, [])

        awaiting = service.request_preflight(_plan())  # type: ignore[arg-type]
        self.assertEqual(awaiting.state, HackrfActivationApplicationState.AWAITING_CONFIRMATION)
        self.assertTrue(awaiting.can_confirm_start)
        self.assertEqual(len(preflight.calls), 1)
        self.assertEqual(native.calls, [])

        repeated = service.request_preflight(_plan())  # type: ignore[arg-type]
        self.assertEqual(repeated.state, HackrfActivationApplicationState.AWAITING_CONFIRMATION)
        self.assertIs(repeated.reason, HackrfActivationApplicationReason.PREFLIGHT_ALREADY_PENDING)
        self.assertEqual(len(preflight.calls), 1)

        unconfirmed = service.confirm_start(user_confirmed=False)
        self.assertEqual(unconfirmed.state, HackrfActivationApplicationState.AWAITING_CONFIRMATION)
        self.assertIs(unconfirmed.reason, HackrfActivationApplicationReason.CONFIRMATION_REQUIRED)
        self.assertEqual(native.calls, [])

        active = service.confirm_start(user_confirmed=True)
        self.assertEqual(active.state, HackrfActivationApplicationState.ACTIVE)
        self.assertEqual(len(native.calls), 1)
        self.assertEqual(service.poll_spectrum_frames(1), ("frame",))
        self.assertEqual(service.metrics(), {"scalar": True})

        active_again = service.request_preflight(_plan())  # type: ignore[arg-type]
        self.assertEqual(active_again.state, HackrfActivationApplicationState.ACTIVE)
        self.assertIs(active_again.reason, HackrfActivationApplicationReason.ALREADY_ACTIVE)
        self.assertEqual(len(preflight.calls), 1)

        stopped = service.stop(1_000)
        self.assertEqual(stopped.state, HackrfActivationApplicationState.READY_FOR_PREFLIGHT)

    def test_preflight_and_incomplete_stop_are_visible_without_a_hidden_retry(self) -> None:
        rejected = _Preflight(
            HackrfActivationPreflight(
                reason=HackrfActivationPreflightReason.IDENTITY_MISMATCH
            )
        )
        service, preflight, native = _service(preflight=rejected)
        refused = service.request_preflight(_plan())  # type: ignore[arg-type]
        self.assertEqual(refused.state, HackrfActivationApplicationState.READY_FOR_PREFLIGHT)
        self.assertIs(refused.reason, HackrfActivationApplicationReason.PREFLIGHT_IDENTITY_MISMATCH)
        self.assertEqual(len(preflight.calls), 1)
        self.assertEqual(native.calls, [])

        control = _Control(complete=False)
        live, _accepted, running_native = _service(control=control)
        self.assertEqual(
            live.request_preflight(_plan()).state,  # type: ignore[arg-type]
            HackrfActivationApplicationState.AWAITING_CONFIRMATION,
        )
        self.assertEqual(
            live.confirm_start(user_confirmed=True).state,
            HackrfActivationApplicationState.ACTIVE,
        )
        stopping = live.stop(1_000)
        self.assertEqual(stopping.state, HackrfActivationApplicationState.ACTIVE)
        self.assertIs(stopping.reason, HackrfActivationApplicationReason.STOP_FAILED)
        self.assertEqual(len(running_native.calls), 1)

    def test_malformed_preflight_cannot_create_a_pending_activation(self) -> None:
        malformed = _Preflight(HackrfActivationPreflight(permit=object()))  # type: ignore[arg-type]
        service, _preflight, native = _service(preflight=malformed)

        result = service.request_preflight(_plan())  # type: ignore[arg-type]

        self.assertEqual(result.state, HackrfActivationApplicationState.READY_FOR_PREFLIGHT)
        self.assertIs(result.reason, HackrfActivationApplicationReason.PREFLIGHT_RUNTIME_OBSERVATION)
        self.assertEqual(native.calls, [])

    def test_module_does_not_import_sdk_qt_or_default_composition_paths(self) -> None:
        source = SOURCE.read_text(encoding="utf-8").lower()
        for forbidden in (
            "ctypes",
            "hackrf.h",
            "_sdr_native",
            "native_live",
            "sdrapplicationservices",
            "pyside",
            "qwidget",
            "start_rx",
            "start_tx",
            "raw_iq",
            "recording",
            "sweep",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
