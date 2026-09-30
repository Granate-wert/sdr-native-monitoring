"""Current common AD936x Sweep pane composition, synthetic reduced frames.

No hardware, native factory or second DSP path is exercised by this fixture.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest

import numpy as np

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.domain.sweep_acquisition import SweepSegmentPosition
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState, SweepQualitySchema
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.pane_resource_session import PaneResourceError
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session

from tests.ui_v2.test_app07_three_concrete_owners import _ObservedReadbackNative


def _readonly(values, dtype):
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


class FakeAdSweep:
    """One existing display port; publishes progress BEFORE terminal coverage."""

    def __init__(self, live) -> None:
        self.live = live
        self.request = None
        self.events: list[str] = []
        self.publications: list[ContinuousSweepDisplaySnapshot] = []
        self.fail_start = False
        self.fail_stop = False
        self.hold_terminal = False
        self.progress: SweepProgressFrame | None = None
        self.line: SweepLineFrame | None = None

    def start(self, request: ContinuousSweepPlanRequest) -> None:
        self.events.append("start")
        self.request = request
        if self.fail_start:
            raise RuntimeError("synthetic partly-started Sweep")
        snapshot = self.live.latest_snapshot()
        assert snapshot.applied is not None and snapshot.device is not None
        geometry = NativeContinuousSweepPlanFactory.preflight_profile(snapshot.applied.applied, request)
        n, count = geometry.reduced.output_bins, geometry.segment_count
        grid = _readonly(request.start_hz + np.arange(n) * geometry.output_spacing_hz, np.float64)
        source = f"continuous-sweep:native-sweep:{snapshot.device.device_id}"
        metrics = ContinuousSweepDisplayMetrics()
        generations = tuple((index, index + 1) for index in range(count))
        self.publications = []
        self.progress = None
        if count > 1:
            known = grid < request.start_hz + request.usable_window_hz
            self.progress = SweepProgressFrame(
                source, 1, request.epoch, 1, "dBFS/bin", grid,
                _readonly(np.where(known, -80.0, np.nan), np.float32),
                _readonly(np.where(known, 0x2001, 0x1000), np.uint32),
                _readonly(np.where(known, 0, -1), np.int32),
                ((0, 1),), tuple(range(1, count)),
                last_admitted_segment=SweepSegmentPosition(
                    0, 1, request.start_hz, request.start_hz + request.usable_window_hz))
            self.publications.append(ContinuousSweepDisplaySnapshot(None, metrics, self.progress))
        indices = np.minimum((grid - request.start_hz) // geometry.segment_stride_hz, count - 1)
        final_start = request.start_hz + (count - 1) * geometry.segment_stride_hz
        self.line = SweepLineFrame(
            1, request.epoch, 1, source, SweepLineState.COMPLETE, grid,
            _readonly(np.full(n, -75.0), np.float32),
            _readonly(np.full(n, 0x2001), np.uint32), _readonly(indices, np.int32),
            (), generations, (), "dBFS/bin", request.usable_window_hz,
            request.analysis_bins_per_usable_window, geometry.physical_bin_spacing_hz,
            geometry.physical_fft_size, SweepQualitySchema.NATIVE_V5,
            last_admitted_segment=SweepSegmentPosition(
                count - 1, count, final_start, min(request.stop_hz, final_start + request.usable_window_hz)))
        self.publications.append(ContinuousSweepDisplaySnapshot(self.line, metrics))

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        self.events.append("poll")
        if self.hold_terminal and self.publications and self.publications[0].line is not None:
            return ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics())
        return (self.publications.pop(0) if self.publications else
                ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics()))

    def stop(self) -> None:
        self.events.append("stop")
        if self.fail_stop:
            raise RuntimeError("synthetic unconfirmed Sweep close")


def ad_sweep_graph():
    native = _ObservedReadbackNative(uri="ip:ad-sweep-fixture.local", serial="ad-sweep-fixture")
    live = NativeLiveSessionService(native)
    fake = FakeAdSweep(live)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(live),),
                                      control_transaction=live.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=live, analyzer_display=fake, device_catalog=catalog))
    return native, fake, graph


class Ad936xSweepPaneOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.native, self.fake, self.graph = ad_sweep_graph()
        self.source = self.graph.live.discover(startup=True)[0].device_id
        self.prepared = None

    def tearDown(self) -> None:
        self.fake.fail_stop = False
        if self.prepared is not None:
            handle = self.prepared.handle
            if handle.applied:
                for future in handle.pump.stop_all().values():
                    future.result(timeout=5)
            handle.shutdown_after_stop()
        else:
            self.graph.live.shutdown()

    def prepare(self, *, mixed=False, stop=220e6):
        drafts = [PaneSlotDraft(1, self.source, 100e6, stop, sample_rate_hz=61.44e6,
                    fft_size=4096, measurement_mode=CaptureMeasurementMode.SWEEP)]
        if mixed:
            drafts.insert(0, PaneSlotDraft(1, self.source, 100e6, 108e6))
            drafts[1] = replace(drafts[1], number=2)
        pool = PaneProductGraphPool(lambda _resource: self.graph)
        self.prepared = prepare_user_pane_session(tuple(drafts), pool_factory=lambda: pool)
        return self.prepared.handle.session

    def start(self):
        session = self.prepare()
        apply_user_pane_session(self.prepared)
        session.start_resource("pane-resource-1")
        return session

    def test_stage_apply_are_inert_progress_precedes_terminal_on_same_owner(self) -> None:
        session = self.prepare()
        self.assertEqual(self.fake.events, [])
        self.assertEqual(self.native.engines, [])
        apply_user_pane_session(self.prepared)
        self.assertEqual(self.fake.events, [])
        self.assertEqual(self.native.engines, [])
        session.start_resource("pane-resource-1")
        progressive = session.poll_resource("pane-resource-1")
        self.assertEqual(len(progressive), 1)
        frame = progressive[0].bundle
        self.assertEqual(frame.publication_kind.value, "sweep_progress")
        self.assertEqual(frame.identity.source_id, self.source)
        self.assertEqual(frame.identity.acquisition_epoch, self.fake.request.epoch)
        self.assertTrue(np.isnan(frame.values).any())
        self.assertEqual(frame.spectrum.last_admitted_segment, self.fake.progress.last_admitted_segment)
        np.testing.assert_array_equal(frame.spectrum.quality_flags, self.fake.progress.quality_flags)
        terminal = session.poll_resource("pane-resource-1")
        self.assertEqual(terminal[0].bundle.publication_kind.value, "sweep_complete")
        self.assertEqual(terminal[0].bundle.spectrum.physical_fft_size, 8192)
        self.assertEqual(terminal[0].bundle.spectrum.analysis_bins_per_usable_window, 4096)
        self.assertEqual(session.poll_resource("pane-resource-1"), ())
        self.assertEqual(self.fake.events.count("start"), 1)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(self.fake.events.count("stop"), 1)

    def test_explicit_stop_then_start_uses_new_sweep_epoch_and_no_hidden_retry(self) -> None:
        session = self.start()
        first = self.fake.request.epoch
        self.assertEqual(session.stop_all(), ())
        session.rearm_resource("pane-resource-1")
        session.start_resource("pane-resource-1")
        self.assertGreater(self.fake.request.epoch, first)
        self.assertEqual(self.fake.events[:3], ["start", "stop", "start"])
        self.assertEqual(session.poll_resource("pane-resource-1")[0].bundle.identity.acquisition_epoch,
                         self.fake.request.epoch)

    def test_rtbw_to_sweep_has_confirmed_stop_on_one_control_graph(self) -> None:
        session = self.prepare(mixed=True)
        apply_user_pane_session(self.prepared)
        first = session.start_resource("pane-resource-1")
        self.assertEqual(len(self.native.engines), 1)
        self.assertEqual(self.fake.events, [])
        second = session.advance_resource("pane-resource-1")
        self.assertNotEqual(first.host_activation_serial, second.host_activation_serial)
        self.assertEqual(self.native.engines[0].disconnect_calls, 1)
        self.assertEqual(self.fake.events, ["start"])
        self.assertEqual(session.poll_resource("pane-resource-1")[0].bundle.mode, "sweep")

    def test_sweep_admission_exposes_physical_fft_but_not_continuous_hop(self) -> None:
        session = self.start()
        admission = session._runtimes["pane-resource-1"].admission
        self.assertEqual((admission.sample_rate_hz, admission.fft_size), (61.44e6, 8192))
        self.assertIsNone(admission.hop_size)

    def test_source_prefix_matches_the_existing_native_live_sweep_lease(self) -> None:
        session = self.prepare()
        apply_user_pane_session(self.prepared)
        lease = self.graph.services.live_sdr.acquire_native_sweep_lease()
        try:
            expected_source = f"continuous-sweep:{lease.source.source_id}"
        finally:
            lease.release()
        self.assertEqual(self.native.engines, [])
        session.start_resource("pane-resource-1")
        self.assertEqual(self.fake.progress.source_id, expected_source)
        self.assertEqual(session.poll_resource("pane-resource-1")[0].bundle.identity.source_id,
                         self.source)

    def test_foreign_source_epoch_and_grid_fail_closed(self) -> None:
        for field, value in (("source_id", "continuous-sweep:foreign"),
                             ("epoch", 12345), ("unit", "dBm"),
                             ("frequencies_hz", "shifted")):
            with self.subTest(field=field):
                if self.prepared is None:
                    session = self.start()
                else:
                    session = self.prepared.handle.session
                    session.rearm_resource("pane-resource-1")
                    session.start_resource("pane-resource-1")
                progress = self.fake.progress
                if field == "frequencies_hz":
                    value = _readonly(progress.frequencies_hz + 1e6, np.float64)
                altered = replace(progress, **{field: value})
                self.fake.publications = [ContinuousSweepDisplaySnapshot(
                    None, ContinuousSweepDisplayMetrics(), altered)]
                with self.assertRaises(PaneResourceError):
                    session.poll_resource("pane-resource-1")
                self.assertEqual(session.stop_all(), ())

    def test_terminal_physical_fft_contract_is_not_relabelled(self) -> None:
        session = self.start()
        altered = replace(self.fake.line, physical_fft_size=4096)
        self.fake.publications = [ContinuousSweepDisplaySnapshot(altered, ContinuousSweepDisplayMetrics())]
        with self.assertRaises(PaneResourceError):
            session.poll_resource("pane-resource-1")
        self.assertEqual(session.stop_all(), ())

    def test_progress_partition_and_last_admitted_segment_cannot_change(self) -> None:
        session = self.start()
        position = replace(self.fake.progress.last_admitted_segment, usable_start_hz=99e6)
        altered = replace(self.fake.progress, last_admitted_segment=position)
        self.fake.publications = [ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), altered)]
        with self.assertRaises(PaneResourceError):
            session.poll_resource("pane-resource-1")
        self.assertEqual(session.stop_all(), ())

    def test_repeated_progress_and_progress_after_its_terminal_are_not_redelivered(self) -> None:
        session = self.start()
        progressive, terminal = self.fake.publications
        self.fake.publications = [progressive, progressive, terminal, progressive, terminal]
        self.assertEqual([len(session.poll_resource("pane-resource-1")) for _ in range(5)],
                         [1, 0, 1, 0, 0])

    def test_partial_start_and_failed_stop_keep_same_owner_until_explicit_cleanup(self) -> None:
        session = self.prepare()
        apply_user_pane_session(self.prepared)
        self.fake.fail_start = True
        with self.assertRaises(PaneResourceError):
            session.start_resource("pane-resource-1")
        self.fake.fail_stop = True
        self.assertTrue(session.stop_all())
        self.assertIs(session._runtimes["pane-resource-1"].owner._sweep_router, self.graph.sweep_router)
        self.assertEqual(self.fake.events.count("start"), 1)
        self.fake.fail_stop = False
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(self.fake.events.count("start"), 1)


if __name__ == "__main__":
    unittest.main()
