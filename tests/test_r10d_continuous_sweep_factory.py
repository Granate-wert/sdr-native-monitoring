"""R10-D2B-B preflight plan factory tests: no I/O before explicit start."""

from __future__ import annotations

import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.services.native_continuous_sweep_factory import (
    ContinuousSweepPlanRequest,
    NativeLiveContinuousSweepDisplayService,
    NativeContinuousSweepPlanFactory,
)
from sdr_monitor.services.native_sweep import NativeSweepLease, NativeSweepSource


class _Native:
    @staticmethod
    def build_info() -> dict[str, object]:
        return {"version": "0.6.0-test", "platform": "Windows", "pluto_compiled": True}

    class ContinuousSweepSegmentConfig:
        def __init__(self, fixed: object, start: float, stop: float) -> None:
            self.fixed, self.start, self.stop = fixed, start, stop

    class ContinuousSweepCoordinatorConfig:
        def __init__(self, epoch: int, start: float, stop: float, segments: list[object], **kwargs: object) -> None:
            self.epoch, self.start, self.stop, self.segments, self.kwargs = epoch, start, stop, segments, kwargs


class R10DContinuousSweepFactoryTests(unittest.TestCase):
    def test_draft_preflight_needs_no_factory_or_receiver_lease(self):
        profile = LiveConfiguration(
            center_hz=2.45e9, sample_rate_hz=61.44e6, analog_bandwidth_hz=56e6,
            fft_size=8192, backend=BackendKind.CPU,
        )
        request = ContinuousSweepPlanRequest(100e6, 136e6, analysis_bins_per_usable_window=4096)
        with patch.object(NativeContinuousSweepPlanFactory, "from_native_live") as acquire:
            with patch("sdr_monitor.services.native_continuous_sweep_factory.build_native_fixed_band_config") as build:
                result = NativeContinuousSweepPlanFactory.preflight_profile(profile, request)
                acquire.assert_not_called()
                build.assert_not_called()
        self.assertEqual(result.reduced.output_bins, 4096)
        lease, _events = self._lease()
        factory = NativeContinuousSweepPlanFactory(lease)
        try:
            self.assertEqual(result, factory.preflight(request))
        finally:
            factory.close()

    def test_preflight_returns_geometry_without_native_construction(self):
        lease, events = self._lease()
        factory = NativeContinuousSweepPlanFactory(lease)
        with patch("sdr_monitor.services.native_continuous_sweep_factory.build_native_fixed_band_config") as build:
            result = factory.preflight(ContinuousSweepPlanRequest(
                100e6, 136e6, analysis_bins_per_usable_window=4096,
            ))
            build.assert_not_called()
        self.assertEqual(result.reduced.output_bins, 4096)
        self.assertEqual(result.output_spacing_hz, 36e6 / 4096)
        self.assertEqual(result.physical_fft_size, 8192)
        self.assertEqual(result.usable_window_hz, 36e6)
        self.assertEqual(result.analysis_bins_per_usable_window, 4096)
        self.assertEqual(result.physical_bin_spacing_hz, 61.44e6 / 8192)
        self.assertEqual(events, ["assert"])
        factory.close()

    def _lease(self) -> tuple[NativeSweepLease, list[str]]:
        events: list[str] = []
        source = NativeSweepSource(
            "usb:mock",
            "source",
            LiveConfiguration(
                center_hz=2.45e9,
                sample_rate_hz=61.44e6,
                analog_bandwidth_hz=56e6,
                fft_size=8192,
                backend=BackendKind.CPU,
            ),
        )
        return NativeSweepLease(_Native, source, lambda: events.append("assert"), lambda: events.append("release")), events

    def test_build_uses_only_applied_profile_and_releases_lease_explicitly(self) -> None:
        lease, events = self._lease()
        factory = NativeContinuousSweepPlanFactory(lease)
        with patch("sdr_monitor.services.native_continuous_sweep_factory.build_native_fixed_band_config", side_effect=lambda _native, live, _uri, **_kwargs: live) as build:
            plan = factory.build(ContinuousSweepPlanRequest(2.40e9, 2.48e9, epoch=4))
        self.assertEqual(events, ["assert"])
        self.assertEqual(plan.epoch, 4)
        self.assertGreater(len(plan.segments), 1)
        self.assertEqual(build.call_count, len(plan.segments))
        self.assertTrue(all(item.fixed.backend is BackendKind.CPU for item in plan.segments))
        self.assertTrue(all(
            item.kwargs["device_buffer_samples"] == 262_144 and
            item.kwargs["snapshot_rate_hz"] is None
            for item in build.call_args_list
        ))
        factory.close()
        self.assertEqual(events, ["assert", "release"])
        with self.assertRaises(RuntimeError):
            factory.build(ContinuousSweepPlanRequest(2.40e9, 2.42e9))

    def test_rejects_window_beyond_applied_profile_before_native_config_creation(self) -> None:
        lease, _events = self._lease()
        factory = NativeContinuousSweepPlanFactory(lease)
        with patch("sdr_monitor.services.native_continuous_sweep_factory.build_native_fixed_band_config") as build:
            with self.assertRaisesRegex(ValueError, "exceeds"):
                factory.build(ContinuousSweepPlanRequest(2.40e9, 2.42e9, usable_window_hz=57e6))
        build.assert_not_called()

    def test_output_bins_and_backlog_fail_before_native_segment_creation(self) -> None:
        lease, _events = self._lease()
        factory = NativeContinuousSweepPlanFactory(lease)
        high_resolution_profile = LiveConfiguration(
            center_hz=2.45e9, sample_rate_hz=61.44e6, analog_bandwidth_hz=56e6,
            fft_size=262144, backend=BackendKind.CPU,
            persistence_enabled=False, persistence_mode="disabled",
        )
        requests = (
            # W/N remains no denser than the held Fs/F.  These fail only at
            # the intended whole-grid/resource admissions, not late native
            # physical-FFT validation.
            (ContinuousSweepPlanRequest(100e6, 3.6e9, usable_window_hz=56e6,
                                        overlap_hz=0.0, analysis_bins_per_usable_window=131072), "bin limit"),
            (ContinuousSweepPlanRequest(100e6, 800e6, usable_window_hz=56e6,
                                        overlap_hz=0.0, analysis_bins_per_usable_window=131072,
                                        output_queue_capacity=64), "memory budget"),
            (ContinuousSweepPlanRequest(100e6, 100e6 + 1000), "bin limit"),
        )
        try:
            with patch("sdr_monitor.services.native_continuous_sweep_factory.build_native_fixed_band_config") as build:
                for index, (request, reason) in enumerate(requests):
                    with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                        if index < 2:
                            NativeContinuousSweepPlanFactory.preflight_profile(high_resolution_profile, request)
                        else:
                            factory.build(request)
                build.assert_not_called()
        finally:
            factory.close()

    def test_analysis_grid_cannot_be_denser_than_the_held_physical_fft(self) -> None:
        profile = LiveConfiguration(
            sample_rate_hz=61.44e6, analog_bandwidth_hz=56e6,
            fft_size=1024, backend=BackendKind.CPU,
        )
        request = ContinuousSweepPlanRequest(100e6, 136e6, analysis_bins_per_usable_window=4096)
        with self.assertRaisesRegex(ValueError, "denser physical FFT"):
            NativeContinuousSweepPlanFactory.preflight_profile(profile, request)

    def test_preflight_rejects_forged_frozen_request_geometry_before_native_build(self) -> None:
        request = object.__new__(ContinuousSweepPlanRequest)
        for name, value in (
            ("start_hz", True), ("stop_hz", 136e6), ("usable_window_hz", 36e6),
            ("overlap_hz", 2e6), ("output_queue_capacity", 4),
            ("analysis_bins_per_usable_window", 0),
        ):
            object.__setattr__(request, name, value)
        profile = LiveConfiguration(backend=BackendKind.CPU)
        with self.assertRaisesRegex(ValueError, "non-finite geometry"):
            NativeContinuousSweepPlanFactory.preflight_profile(profile, request)

    def test_forwards_explicit_bounded_line_cadence_without_changing_live_rf_profile(self) -> None:
        lease, _events = self._lease()
        factory = NativeContinuousSweepPlanFactory(lease)
        request = ContinuousSweepPlanRequest(
            2.40e9, 2.42e9, acquisition_buffer_samples=4096, line_snapshot_rate_hz=1200.0
        )
        with patch(
            "sdr_monitor.services.native_continuous_sweep_factory.build_native_fixed_band_config",
            side_effect=lambda _native, live, _uri, **_kwargs: live,
        ) as build:
            plan = factory.build(request)
        self.assertEqual(build.call_args.kwargs["device_buffer_samples"], 4096)
        self.assertEqual(build.call_args.kwargs["snapshot_rate_hz"], 1200.0)
        self.assertEqual(build.call_args.args[1].sample_rate_hz, 61.44e6)
        self.assertEqual(plan.kwargs["line_snapshot_rate_hz"], 1200.0)
        self.assertNotEqual(
            plan.kwargs["line_snapshot_rate_hz"], plan.segments[0].fixed.snapshot_rate_hz
        )

    def test_r10d5_geometry_is_explicit_and_cannot_relax_general_sweep_requests(self) -> None:
        with self.assertRaisesRegex(ValueError, "power of two"):
            ContinuousSweepPlanRequest(2.40e9, 2.42e9, acquisition_buffer_samples=308_224)
        request = ContinuousSweepPlanRequest(
            2.40e9,
            2.42e9,
            acquisition_buffer_samples=308_224,
            allow_r10d5_evidence_buffer_geometry=True,
        )
        lease, _events = self._lease()
        factory = NativeContinuousSweepPlanFactory(lease)
        with patch(
            "sdr_monitor.services.native_continuous_sweep_factory.build_native_fixed_band_config",
            side_effect=lambda _native, live, _uri, **_kwargs: live,
        ) as build:
            factory.build(request)
        self.assertEqual(build.call_args.kwargs["device_buffer_samples"], 308_224)
        self.assertTrue(build.call_args.kwargs["allow_r10d5_evidence_buffer_geometry"])
        factory.close()

    def test_evidence_build_identity_is_small_and_route_free(self) -> None:
        lease, _events = self._lease()
        factory = NativeContinuousSweepPlanFactory(lease)
        self.assertEqual(
            factory.evidence_build_info(),
            {
                "native_module": "sdr_monitor._sdr_native",
                "native_version": "0.6.0-test",
                "native_platform": "Windows",
                "native_pluto_compiled": "True",
            },
        )

    def test_evidence_factory_requires_explicit_capability_digest(self) -> None:
        lease, _events = self._lease()
        unbound = NativeContinuousSweepPlanFactory(lease)
        with self.assertRaisesRegex(RuntimeError, "lacks a capability snapshot digest"):
            unbound.capability_evidence_sha256()
        unbound.close()

        bound_lease, _events = self._lease()
        bound = NativeContinuousSweepPlanFactory(
            bound_lease,
            capability_evidence_sha256="c" * 64,
        )
        self.assertEqual(bound.capability_evidence_sha256(), "c" * 64)
        bound.close()

    def test_failed_start_closes_display_before_releasing_lease(self) -> None:
        events = []

        class Display:
            def start(self, config):
                events.append("start-failed")
                raise RuntimeError("configure failed")

            def close(self):
                events.append("display-close")

        class Factory:
            @classmethod
            def from_native_live(cls, live):
                events.append("lease")
                return cls()

            def build(self, request):
                return object()

            def create_display_service(self):
                return Display()

            def close(self):
                events.append("release")

        service = NativeLiveContinuousSweepDisplayService(object())
        with patch("sdr_monitor.services.native_continuous_sweep_factory.NativeContinuousSweepPlanFactory", Factory):
            with self.assertRaisesRegex(RuntimeError, "configure failed"):
                service.start(ContinuousSweepPlanRequest(2.40e9, 2.42e9))
        self.assertEqual(events, ["lease", "start-failed", "display-close", "release"])
        self.assertIsNone(service._display)
        events.clear()
        uncertain = NativeLiveContinuousSweepDisplayService(object())
        with patch("sdr_monitor.services.native_continuous_sweep_factory.NativeContinuousSweepPlanFactory", Factory):
            with patch.object(Display, "close", side_effect=RuntimeError("close failed")):
                with self.assertRaisesRegex(RuntimeError, "close failed"):
                    uncertain.start(ContinuousSweepPlanRequest(2.40e9, 2.42e9))
            self.assertNotIn("release", events)
            self.assertIsNotNone(uncertain._display)
            self.assertIsNotNone(uncertain._factory)
            with self.assertRaisesRegex(RuntimeError, "already running"):
                uncertain.start(ContinuousSweepPlanRequest(2.40e9, 2.42e9))

    def test_deferred_live_service_acquires_only_on_start_and_releases_on_stop_or_failure(self) -> None:
        events: list[str] = []

        class Display:
            def start(self, config: object) -> None:
                events.append(f"display-start:{config}")

            def stop(self) -> None:
                events.append("display-stop")

            def close(self) -> None:
                events.append("display-close")

            def poll_latest(self) -> object:
                return object()

        class Factory:
            @classmethod
            def from_native_live(cls, _live: object) -> "Factory":
                events.append("lease")
                return cls()

            def build(self, _request: object) -> str:
                events.append("build")
                return "config"

            def create_display_service(self) -> Display:
                events.append("display")
                return Display()

            def close(self) -> None:
                events.append("release")

        service = NativeLiveContinuousSweepDisplayService(object())
        with patch("sdr_monitor.services.native_continuous_sweep_factory.NativeContinuousSweepPlanFactory", Factory):
            self.assertEqual(events, [])
            service.start(ContinuousSweepPlanRequest(2.40e9, 2.42e9))
            self.assertEqual(events, ["lease", "build", "display", "display-start:config"])
            service.stop()
        self.assertEqual(events[-3:], ["display-stop", "display-close", "release"])
        events.clear()
        entered = threading.Event()
        release = threading.Event()

        def delayed_stop():
            events.append("display-stop")
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test barrier timed out")

        with patch("sdr_monitor.services.native_continuous_sweep_factory.NativeContinuousSweepPlanFactory", Factory):
            service.start(ContinuousSweepPlanRequest(2.40e9, 2.42e9))
            with patch.object(service._display, "stop", side_effect=delayed_stop):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(service.stop)
                    try:
                        self.assertTrue(entered.wait(1))
                        for operation in (
                            lambda: service.start(ContinuousSweepPlanRequest(2.40e9, 2.42e9)),
                            service.stop, service.poll_latest, service.close,
                        ):
                            with self.assertRaisesRegex(RuntimeError, "operation is pending"):
                                operation()
                        self.assertNotIn("release", events)
                    finally:
                        release.set()
                    pending.result(timeout=2)
            self.assertEqual(events.count("display-stop"), 1)
            self.assertEqual(events.count("display-close"), 1)
            self.assertEqual(events.count("release"), 1)
            service.start(ContinuousSweepPlanRequest(2.40e9, 2.42e9))
            service.close()


if __name__ == "__main__":
    unittest.main()
