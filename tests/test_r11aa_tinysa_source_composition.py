"""R11-AA pure source-bound tinySA composition tests; no serial action."""

from __future__ import annotations

import unittest

from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollection,
)
from sdr_monitor.services.tinysa_serial_version_probe import TinySaVersionObservation
from sdr_monitor.services.tinysa_source_composition import (
    TinySaIdentityAssurance,
    TinySaSourceCompositionError,
    TinySaSourceCompositionService,
    TinySaSourcePhase,
    TinySaSourceReason,
    TinySaTransportEndpoint,
    endpoint_identity_key,
)
from sdr_monitor.services.tinysa_sweep_settings_controller import (
    TinySaSettingsApplyResult,
    TinySaSweepSettingsPlan,
)


class _Collector:
    def collect(self, request: TinySaScanRawRequest) -> TinySaTraceCollection:
        del request
        raise AssertionError("pure composition test must not collect")


class _Settings:
    def apply(
        self,
        plan: TinySaSweepSettingsPlan,
        *,
        confirmation: str,
    ) -> TinySaSettingsApplyResult:
        del plan, confirmation
        raise AssertionError("pure composition test must not apply settings")


class _Backend:
    def __init__(self, *, fail_probe: bool = False) -> None:
        self.fail_probe = fail_probe
        self.calls: list[object] = []
        self.endpoints = (
            TinySaTransportEndpoint(
                "COM31",
                endpoint_identity_key("unit-a"),
                "tinySA USB CDC aaaaaaaa",
                TinySaIdentityAssurance.USB_SERIAL,
            ),
            TinySaTransportEndpoint(
                "COM32",
                endpoint_identity_key("endpoint-b"),
                "tinySA USB CDC bbbbbbbb",
                TinySaIdentityAssurance.PNP_ENDPOINT_ONLY,
            ),
        )

    def discover_endpoints(self) -> tuple[TinySaTransportEndpoint, ...]:
        self.calls.append("discover")
        return self.endpoints

    def probe_version(self, endpoint: TinySaTransportEndpoint) -> TinySaVersionObservation:
        self.calls.append(("probe", endpoint.identity_key))
        if self.fail_probe:
            raise RuntimeError("secret COM route")
        return TinySaVersionObservation(
            TinySaModel.ULTRA,
            "tinySA4_v1.4-test",
            "sha256:" + "a" * 64,
        )

    def make_collector(self, endpoint: TinySaTransportEndpoint, model: TinySaModel) -> _Collector:
        self.calls.append(("collector", endpoint.identity_key, model.value))
        return _Collector()

    def make_settings_executor(self, endpoint: TinySaTransportEndpoint) -> _Settings:
        self.calls.append(("settings", endpoint.identity_key))
        return _Settings()


class TinySaSourceCompositionTests(unittest.TestCase):
    def test_construction_discovery_and_selection_are_transport_inert(self) -> None:
        backend = _Backend()
        service = TinySaSourceCompositionService(backend)

        self.assertEqual(backend.calls, [])
        self.assertIs(service.current().phase, TinySaSourcePhase.READY)

        discovered = service.discover()
        self.assertEqual(backend.calls, ["discover"])
        self.assertEqual(len(discovered.candidates), 2)
        rendered = repr(discovered).casefold()
        self.assertNotIn("com31", rendered)
        self.assertNotIn("com32", rendered)

        selected = service.select(discovered.candidates[0].source_id)
        self.assertIs(selected.phase, TinySaSourcePhase.SELECTED)
        self.assertEqual(backend.calls, ["discover"])
        self.assertTrue(selected.can_verify)

    def test_verify_then_compose_is_explicit_cached_and_route_free(self) -> None:
        backend = _Backend()
        service = TinySaSourceCompositionService(backend)
        source_id = service.discover().candidates[0].source_id
        service.select(source_id)

        verified = service.verify_selected()

        self.assertEqual(verified.source_id, source_id)
        self.assertIs(verified.model, TinySaModel.ULTRA)
        self.assertFalse(verified.continuity_verified)
        self.assertNotIn("com", repr(verified).casefold())
        self.assertIs(service.current().phase, TinySaSourcePhase.VERIFIED)
        self.assertTrue(service.current().can_compose)

        composed = service.compose_selected()
        self.assertIs(service.compose_selected(), composed)
        self.assertIs(service.current().phase, TinySaSourcePhase.COMPOSED)
        self.assertFalse(service.current().can_discover)
        self.assertEqual(
            [call[0] for call in backend.calls if isinstance(call, tuple)],
            ["probe", "collector", "settings"],
        )

    def test_endpoint_only_identity_never_becomes_continuity_proof(self) -> None:
        backend = _Backend()
        service = TinySaSourceCompositionService(backend)
        candidate = service.discover().candidates[1]
        service.select(candidate.source_id)
        verified = service.verify_selected()

        self.assertIs(
            verified.identity_assurance,
            TinySaIdentityAssurance.PNP_ENDPOINT_ONLY,
        )
        self.assertFalse(verified.continuity_verified)

    def test_selection_and_probe_failures_are_redacted_and_finite(self) -> None:
        service = TinySaSourceCompositionService(_Backend())
        service.discover()
        with self.assertRaisesRegex(TinySaSourceCompositionError, "failed closed"):
            service.select("tinysa-0000000000000000")
        self.assertIs(service.current().reason, TinySaSourceReason.SOURCE_NOT_FOUND)

        backend = _Backend(fail_probe=True)
        service = TinySaSourceCompositionService(backend)
        service.select(service.discover().candidates[0].source_id)
        with self.assertRaisesRegex(
            TinySaSourceCompositionError,
            "identity verification failed closed",
        ) as captured:
            service.verify_selected()
        self.assertNotIn("secret", str(captured.exception).casefold())
        self.assertNotIn("com", str(captured.exception).casefold())
        self.assertIs(service.current().phase, TinySaSourcePhase.FAULTED)
        self.assertIs(service.current().reason, TinySaSourceReason.IDENTITY_PROBE_FAILED)
        with self.assertRaisesRegex(TinySaSourceCompositionError, "selection"):
            service.verify_selected()

    def test_composed_owner_cannot_be_replaced_by_implicit_rediscovery(self) -> None:
        service = TinySaSourceCompositionService(_Backend())
        candidate = service.discover().candidates[0]
        service.select(candidate.source_id)
        service.verify_selected()
        service.compose_selected()

        with self.assertRaisesRegex(TinySaSourceCompositionError, "released"):
            service.discover()


if __name__ == "__main__":
    unittest.main()
