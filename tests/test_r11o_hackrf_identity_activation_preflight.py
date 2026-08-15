"""R11-O identity-bound HackRF activation preflight tests; fake-only."""

from __future__ import annotations

from pathlib import Path
import unittest

from sdr_monitor.domain import BackendKind
from sdr_monitor.services import (
    HackrfActivationPreflightReason,
    HackrfActivationPreflightService,
    HackrfBoardKind,
    HackrfCapabilityAdapter,
    HackrfLiveActivationPlan,
    HackrfLiveRequest,
    HackrfReadOnlyProbe,
    HackrfRuntimeIdentityProbe,
    admit_hackrf_live,
)
from sdr_monitor.services.libhackrf_runtime_identity import _serial_words


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SOURCE = ROOT / "sdr_monitor/services/hackrf_activation_preflight.py"
RUNTIME_SOURCE = ROOT / "sdr_monitor/services/libhackrf_runtime_identity.py"

_WORDS = (0, 0, 0x010961DC, 0x2B78454F)


class _CapabilityPort:
    def probe(self) -> HackrfReadOnlyProbe:
        return HackrfReadOnlyProbe(HackrfBoardKind.HACKRF_ONE, _WORDS, "2024.02.1", 0x0107)

    def close(self) -> None:
        return None


def _plan() -> HackrfLiveActivationPlan:
    observed = HackrfCapabilityAdapter(_CapabilityPort).observe()
    request = HackrfLiveRequest(
        center_frequency_hz=100e6,
        sample_rate_hz=10e6,
        baseband_filter_hz=8_000_000,
        lna_gain_db=16,
        vga_gain_db=20,
        backend=BackendKind.CPU,
        configuration_generation=4,
    )
    result = admit_hackrf_live(observed.snapshot, observed.calibration_identity, request)
    assert result.plan is not None
    return result.plan


class _IdentityPort:
    def __init__(self, probe: object, *, close_fails: bool = False) -> None:
        self._probe = probe
        self._close_fails = close_fails
        self.calls: list[str] = []

    def probe(self) -> object:
        self.calls.append("probe")
        if isinstance(self._probe, BaseException):
            raise self._probe
        return self._probe

    def close(self) -> None:
        self.calls.append("close")
        if self._close_fails:
            raise RuntimeError("redacted close detail")


class R11OHackrfIdentityActivationPreflightTests(unittest.TestCase):
    def test_matching_identity_issues_a_permit_and_closes_once(self) -> None:
        port = _IdentityPort(HackrfRuntimeIdentityProbe(HackrfBoardKind.HACKRF_ONE, _WORDS))

        result = HackrfActivationPreflightService(lambda: port).verify(_plan())

        self.assertTrue(result.accepted)
        self.assertIsNone(result.reason)
        self.assertIsNotNone(result.permit)
        self.assertEqual(port.calls, ["probe", "close"])

    def test_runtime_failures_or_identity_mismatch_fail_closed_and_close_once(self) -> None:
        cases = (
            (
                _IdentityPort(HackrfRuntimeIdentityProbe(HackrfBoardKind.HACKRF_ONE, (1, 2, 3, 4))),
                HackrfActivationPreflightReason.IDENTITY_MISMATCH,
            ),
            (_IdentityPort(RuntimeError("vendor detail")), HackrfActivationPreflightReason.RUNTIME_OBSERVATION),
            (_IdentityPort(object()), HackrfActivationPreflightReason.RUNTIME_OBSERVATION),
            (
                _IdentityPort(HackrfRuntimeIdentityProbe(HackrfBoardKind.HACKRF_ONE, _WORDS), close_fails=True),
                HackrfActivationPreflightReason.RUNTIME_OBSERVATION,
            ),
        )
        for port, reason in cases:
            with self.subTest(reason=reason):
                result = HackrfActivationPreflightService(lambda: port).verify(_plan())
                self.assertFalse(result.accepted)
                self.assertIsNone(result.permit)
                self.assertIs(result.reason, reason)
                self.assertEqual(port.calls, ["probe", "close"])

    def test_unissued_plan_is_rejected_before_a_port_is_created(self) -> None:
        calls: list[str] = []
        forged = object.__new__(HackrfLiveActivationPlan)
        result = HackrfActivationPreflightService(lambda: calls.append("create")).verify(forged)

        self.assertFalse(result.accepted)
        self.assertIs(result.reason, HackrfActivationPreflightReason.PLAN_NOT_ADMITTED)
        self.assertEqual(calls, [])

    def test_enumeration_serial_is_canonicalized_to_the_existing_identity_words(self) -> None:
        self.assertEqual(_serial_words(b"0000000000000000010961DC2B78454F"), _WORDS)
        for malformed in (b"", b"not-a-hackrf-serial", b"0" * 31, b"g" * 32):
            with self.subTest(malformed=malformed):
                with self.assertRaises(RuntimeError):
                    _serial_words(malformed)

    def test_modules_are_preflight_or_enumeration_only(self) -> None:
        preflight = PREFLIGHT_SOURCE.read_text(encoding="utf-8").lower()
        runtime = RUNTIME_SOURCE.read_text(encoding="utf-8").lower()
        for forbidden in (
            "start_rx",
            "start_tx",
            "device_list_open",
            "hackrf_open",
            "hackrf_close",
            "recording",
            "sweep",
            "pyside",
            "qwidget",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, preflight)
                self.assertNotIn(forbidden, runtime)
        self.assertIn("hackrf_device_list", runtime)
        self.assertIn("hackrf_device_list_free", runtime)
        self.assertIn("hackrf_init", runtime)
        self.assertIn("hackrf_exit", runtime)


if __name__ == "__main__":
    unittest.main()
