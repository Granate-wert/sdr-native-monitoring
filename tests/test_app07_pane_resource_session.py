"""APP-07 one-owner-per-resource control, fan-out and explicit gap tests."""

from contextlib import contextmanager
import unittest
from threading import Event, RLock, Thread

import numpy as np

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, RtbwFrameMetadata, bundle_from_sweep
from sdr_monitor.domain.device_capabilities import stable_identity_key
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, compile_pane_schedule
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverChainSelection, ReceiverEndpoint,
)
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.services.pane_resource_session import (
    PaneCaptureAdmission, PaneResourceError, PaneResourceSession,
)
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager

from tests.test_app07_shared_capture_schedule import group, pane, profile


def readonly(values, dtype):
    result = np.asarray(values, dtype=dtype)
    result.setflags(write=False)
    return result


def frame(source_id: str, epoch: int, start_hz: float, stop_hz: float):
    progress = SweepProgressFrame(
        source_id=source_id, sequence=1, epoch=epoch, revision=1, unit="dBFS/bin",
        frequencies_hz=readonly([start_hz, (start_hz + stop_hz) / 2, stop_hz], np.float64),
        values_db=readonly([-70.0, -71.0, np.nan], np.float32),
        quality_flags=readonly([0, 0, 4096], np.uint32),
        source_segment_indices=readonly([0, 0, -1], np.int32),
        acquired_segment_generations=((0, 11),), pending_segment_indices=(1,),
    )
    return bundle_from_sweep(progress)


def live_frame(source_id: str, session_id: str, epoch: int, *,
               generation: int = 5, fft_size: int = 4096, hop_size: int = 2048,
               center_hz: float = 104e6, receiver_id: str | None = None):
    sample_rate_hz = 61_440_000.0
    grid = center_hz + (np.arange(fft_size) - fft_size // 2) * (sample_rate_hz / fft_size)
    spectrum = LiveSpectrumFrame(
        sequence=1, timestamp_ns=1, center_frequency_hz=center_hz,
        sample_rate_hz=sample_rate_hz, fft_size=fft_size, hop_size=hop_size,
        frequencies_hz=readonly(grid, np.float64),
        values=readonly(np.full(fft_size, -70), np.float32),
        source_id=source_id, config_generation=generation,
        receiver_id=receiver_id, acquisition_epoch=epoch,
    )
    return AnalyzerFrameBundle(
        spectrum, session_id, receiver_id, epoch,
        RtbwFrameMetadata(center_hz, sample_rate_hz, fft_size, hop_size),
    )


class FakeOwner:
    def __init__(self, resource_id: str) -> None:
        self.physical_stream_resource_id = resource_id
        self.events: list[tuple[str, str | None]] = []
        self.running = False
        self.recording = False
        self.fail_start = False
        self.fail_stop = False
        self.blocked_jobs: set[str] = set()
        self.fail_recording_query = False
        self.receiver_ids: dict[str, str | int] = {}
        self.admission_source_id = resource_id + ":source"
        self.admission_mode = "sweep"
        self.admission_unit = "dBFS/bin"
        self.admission_generation: int | None = None
        self.admission_session_id = "fake-live-session"
        self.admission_epoch = 7
        self.admission_sample_rate_hz: float | None = None
        self._control_lock = RLock()
        self.clock: list[float] | None = None
        self.start_cost_s = 0.0
        self.stop_cost_s = 0.0
        self.stop_entered: Event | None = None
        self.stop_release: Event | None = None
        self.publications: list[tuple[str, AnalyzerFrameBundle]] = []
        self.fail_poll = False

    def validate_endpoint(self, endpoint) -> None:
        del endpoint

    def validate_job(self, job) -> None:
        del job

    def release_control_claim(self) -> None:
        pass

    def poll_bundles(self) -> tuple[tuple[str, AnalyzerFrameBundle], ...]:
        if self.fail_poll:
            raise RuntimeError("raw native publication error")
        values = tuple(self.publications)
        self.publications.clear()
        return values

    def start_capture(self, job) -> PaneCaptureAdmission:
        self.events.append(("start", job.capture_id))
        if self.fail_start:
            raise RuntimeError("raw vendor startup error")
        self.running = True
        if self.clock is not None:
            self.clock[0] += self.start_cost_s
        epoch = self.admission_epoch
        self.admission_epoch += 1
        if self.admission_mode == "rtbw":
            return PaneCaptureAdmission(
                job.capture_id, self.admission_source_id, "rtbw", self.admission_unit,
                job.receiver_endpoint_ids, epoch,
                session_id=self.admission_session_id, config_generation=self.admission_generation,
                sample_rate_hz=(self.admission_sample_rate_hz or job.profile.sample_rate_hz),
                fft_size=job.profile.fft_size, hop_size=job.profile.hop_size,
            )
        return PaneCaptureAdmission(job.capture_id, self.admission_source_id,
                                    self.admission_mode, self.admission_unit,
                                    job.receiver_endpoint_ids, epoch,
                                    sample_rate_hz=(self.admission_sample_rate_hz or job.profile.sample_rate_hz),
                                    fft_size=job.profile.fft_size, hop_size=job.profile.hop_size)

    def stop_capture_and_wait(self) -> None:
        self.events.append(("stop", None))
        if self.stop_entered is not None:
            self.stop_entered.set()
        if self.stop_release is not None and not self.stop_release.wait(2.0):
            raise RuntimeError("fake Stop did not complete")
        if self.fail_stop:
            raise RuntimeError("raw vendor shutdown error")
        self.running = False
        if self.clock is not None:
            self.clock[0] += self.stop_cost_s

    def recording_active(self) -> bool:
        if self.fail_recording_query:
            raise RuntimeError("raw vendor recording response")
        return self.recording

    def recording_conflict(self, job) -> bool:
        if self.fail_recording_query:
            raise RuntimeError("raw vendor recording response")
        return job.capture_id in self.blocked_jobs

    def receiver_identity(self, endpoint_id: str) -> str | int | None:
        return self.receiver_ids.get(endpoint_id)

    @contextmanager
    def control_transaction(self):
        with self._control_lock:
            yield

    def start_recording(self) -> None:
        with self._control_lock:
            self.recording = True


class PaneResourceSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.leases = ReceiverLeaseManager(max_active_resources=4)
        self.clock = [10.0]

    def session(self, groups, requests, owners, *, usable_hz=36_000_000,
                mode=CaptureMeasurementMode.SWEEP):
        schedule = compile_pane_schedule(groups, requests, {"capture": profile(usable_hz, mode)})
        return PaneResourceSession(schedule, groups, owners, self.leases,
                                   source_identity_keys={endpoint.source_id: stable_identity_key(endpoint.source_id)
                                                         for group in groups for endpoint in group.endpoints},
                                   now_s=lambda: self.clock[0])

    def test_parallel_alias_or_missing_canonical_identity_refuses_before_lease(self) -> None:
        groups = (group("device-a", "rx-a"), group("device-b", "rx-b"))
        requests = (
            pane("first", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),
            pane("second", "rx-b", 120e6, 128e6, ReceiverBindingMode.DEDICATED_PARALLEL),
        )
        schedule = compile_pane_schedule(groups, requests, {"capture": profile(36_000_000)})
        owners = {"device-a": FakeOwner("device-a"), "device-b": FakeOwner("device-b")}
        with self.assertRaisesRegex(PaneResourceError, "canonical source identities"):
            PaneResourceSession(schedule, groups, owners, self.leases)
        same_physical = stable_identity_key("same physical device via USB and IP")
        with self.assertRaisesRegex(PaneResourceError, "alias one physical receiver"):
            PaneResourceSession(schedule, groups, owners, self.leases, source_identity_keys={
                "device-a:source": same_physical, "device-b:source": same_physical,
            })
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_unknown_identity_only_admitted_for_one_source_per_distinct_family(self) -> None:
        from sdr_monitor.domain.device_capabilities import DeviceFamily

        groups = (group("device-a", "rx-a"), group("device-b", "rx-b"))
        requests = (
            pane("first", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),
            pane("second", "rx-b", 120e6, 128e6, ReceiverBindingMode.DEDICATED_PARALLEL),
        )
        schedule = compile_pane_schedule(groups, requests, {"capture": profile(36_000_000)})
        owners = {"device-a": FakeOwner("device-a"), "device-b": FakeOwner("device-b")}
        identity = {"device-a:source": None, "device-b:source": stable_identity_key("hackrf")}
        same_family = {"device-a:source": DeviceFamily.AD936X,
                       "device-b:source": DeviceFamily.AD936X}
        with self.assertRaisesRegex(PaneResourceError, "unique selected family"):
            PaneResourceSession(schedule, groups, owners, self.leases,
                                source_identity_keys=identity, source_families=same_family)
        identity["device-b:source"] = None
        with self.assertRaisesRegex(PaneResourceError, "unique selected family"):
            PaneResourceSession(schedule, groups, owners, self.leases,
                                source_identity_keys=identity, source_families=same_family)
        distinct_family = {**same_family, "device-b:source": DeviceFamily.HACKRF}
        session = PaneResourceSession(schedule, groups, owners, self.leases,
                                      source_identity_keys=identity, source_families=distinct_family)
        self.assertEqual(len(session.preview()), 2)
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_two_resources_start_in_parallel_and_keep_publications_isolated(self) -> None:
        groups = (group("device-a", "rx-a"), group("device-b", "rx-b"))
        requests = (
            pane("first", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),
            pane("second", "rx-b", 120e6, 128e6, ReceiverBindingMode.DEDICATED_PARALLEL),
        )
        first_owner, second_owner = FakeOwner("device-a"), FakeOwner("device-b")
        second_owner.admission_epoch = 4
        session = self.session(groups, requests, {"device-a": first_owner, "device-b": second_owner})
        self.assertEqual(tuple(item.affected_pane_ids for item in session.preview()),
                         (("first",), ("second",)))
        session.apply()
        self.assertEqual(self.leases.active_resource_count, 2)
        self.assertEqual(first_owner.events + second_owner.events, [])
        first = session.start_resource("device-a")
        second = session.start_resource("device-b")
        self.assertTrue(first_owner.running and second_owner.running)
        self.assertEqual(session.active_resource_count, 2)
        measurement_a = frame("device-a:source", 7, 100e6, 108e6)
        measurement_b = frame("device-b:source", 4, 120e6, 128e6)
        delivered_a = session.accept_frame(first, "rx-a", measurement_a)
        self.assertEqual(tuple(item.pane_id for item in delivered_a), ("first",))
        self.assertIs(delivered_a[0].bundle, measurement_a)
        self.assertTrue(np.isnan(delivered_a[0].bundle.values[-1]))
        self.assertEqual(session.accept_frame(first, "rx-a", measurement_b), ())
        self.assertEqual(session.rejected_publications("device-a"), 1)
        self.clock[0] += 1.0
        delivered_b = session.accept_frame(second, "rx-b", measurement_b)
        self.assertEqual(tuple(item.pane_id for item in delivered_b), ("second",))
        self.clock[0] += 0.5
        self.assertAlmostEqual(session.pane_age_s("first"), 1.5)
        self.assertAlmostEqual(session.pane_age_s("second"), 0.5)
        self.assertEqual(session.stop_selected("first"), ("first",))
        self.assertFalse(first_owner.running)
        self.assertTrue(second_owner.running)
        self.assertEqual(self.leases.active_resource_count, 1)
        self.assertIsNone(session.pane_age_s("first"))
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_owner_poll_routes_current_bundle_without_reopening_capture(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("low", "rx-a", 100e6, 108e6),
                    pane("high", "rx-a", 120e6, 128e6))
        owner = FakeOwner("device-a")
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        session.start_resource("device-a")
        measurement = frame("device-a:source", 7, 100e6, 128e6)
        owner.publications.append(("rx-a", measurement))
        deliveries = session.poll_resource("device-a")
        self.assertEqual(tuple(item.pane_id for item in deliveries), ("high", "low"))
        self.assertTrue(all(item.bundle is measurement for item in deliveries))
        self.assertEqual([item[0] for item in owner.events], ["start"])
        self.assertEqual(session.poll_resource("device-a"), ())
        self.assertEqual(session.stop_all(), ())
        owner.publications.append(("rx-a", measurement))
        self.assertEqual(session.poll_resource("device-a"), ())

    def test_host_revisit_counts_accepted_visits_not_frames_or_stale_tokens(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("a-low", "rx-a", 100e6, 108e6, ReceiverBindingMode.TIME_SLICED),
                    pane("b-high", "rx-a", 140e6, 148e6, ReceiverBindingMode.TIME_SLICED))
        owner = FakeOwner("device-a")
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        first = session.start_resource("device-a")
        self.assertIsNone(session.pane_host_timing("a-low").frame_age_s)
        self.clock[0] = 10.1
        self.assertEqual(len(session.accept_frame(first, "rx-a",
                                                  frame("device-a:source", 7, 100e6, 108e6))), 1)
        self.clock[0] = 10.3
        self.assertEqual(len(session.accept_frame(first, "rx-a",
                                                  frame("device-a:source", 7, 100e6, 108e6))), 1)
        self.assertIsNone(session.pane_host_timing("a-low").last_revisit_s)
        self.clock[0] = 10.6
        second = session.advance_resource("device-a")
        self.clock[0] = 10.7
        self.assertEqual(len(session.accept_frame(second, "rx-a",
                                                  frame("device-a:source", 8, 140e6, 148e6))), 1)
        self.clock[0] = 11.2
        third = session.advance_resource("device-a")
        self.assertEqual(session.accept_frame(first, "rx-a",
                                              frame("device-a:source", 7, 100e6, 108e6)), ())
        self.clock[0] = 11.3
        self.assertEqual(len(session.accept_frame(third, "rx-a",
                                                  frame("device-a:source", 9, 100e6, 108e6))), 1)
        timing = session.pane_host_timing("a-low")
        self.assertAlmostEqual(timing.last_revisit_s, 1.2)
        self.assertAlmostEqual(timing.frame_age_s, 0.0)
        self.clock[0] = 11.5
        self.assertEqual(len(session.accept_frame(third, "rx-a",
                                                  frame("device-a:source", 9, 100e6, 108e6))), 1)
        self.assertAlmostEqual(session.pane_host_timing("a-low").last_revisit_s, 1.2)
        self.clock[0] = 10.0
        self.assertIsNone(session.pane_host_timing("a-low").frame_age_s)
        self.assertIsNone(session.pane_host_timing("a-low").last_revisit_s)
        self.clock[0] = 12.0
        self.assertIsNone(session.pane_host_timing("a-low").frame_age_s)
        self.assertEqual(len(session.accept_frame(third, "rx-a",
                                                  frame("device-a:source", 9, 100e6, 108e6))), 1)
        self.assertIsNone(session.pane_host_timing("a-low").last_revisit_s)
        self.assertEqual(session.stop_all(), ())
        self.assertIsNone(session.pane_host_timing("a-low").frame_age_s)
        with self.assertRaises(PaneResourceError):
            session.pane_host_timing("missing")

    def test_poll_failure_quarantines_publication_and_retains_owner_for_stop(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6,
                         ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        session.start_resource("device-a")
        owner.fail_poll = True
        with self.assertRaisesRegex(PaneResourceError, "publication failed"):
            session.poll_resource("device-a")
        self.assertEqual(session.active_resource_count, 0)
        self.assertEqual(session.retained_resource_count, 1)
        self.assertEqual(session.stop_all(), ())

    def test_one_resource_time_slices_nonadjacent_shared_pairs_with_epoch_gate(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (
            pane("a", "rx-a", 100e6, 101e6), pane("b", "rx-a", 101e6, 121e6),
            pane("c", "rx-a", 102e6, 103e6), pane("d", "rx-a", 120e6, 121e6),
        )
        owner = FakeOwner("device-a")
        owner.clock = self.clock
        owner.start_cost_s = 0.01
        owner.stop_cost_s = 0.02
        session = self.session(groups, requests, {"device-a": owner}, usable_hz=20e6)
        preview = session.preview()[0]
        self.assertEqual(preview.affected_pane_ids, ("a", "b", "c", "d"))
        self.assertEqual(preview.pane_assignments,
                         (("a", "rx-a"), ("b", "rx-a"), ("c", "rx-a"), ("d", "rx-a")))
        self.assertEqual(tuple(item.pane_id for item in preview.revisit_estimates),
                         ("a", "b", "c", "d"))
        self.assertEqual(preview.capture_job_count, 2)
        self.assertGreater(preview.planned_control_boundaries_per_cycle, 0)
        session.apply()
        first = session.start_resource("device-a")
        self.assertAlmostEqual(first.host_control_elapsed_s, 0.01)
        delivered = session.accept_frame(first, "rx-a",
                                         frame("device-a:source", 7, 100e6, 103e6))
        self.assertEqual(tuple(item.pane_id for item in delivered), ("a", "c"))
        self.assertIs(delivered[0].bundle, delivered[1].bundle)
        self.assertEqual(session.stop_impact("a"), ("a", "b", "c", "d"))
        with self.assertRaisesRegex(PaneResourceError, "confirm"):
            session.stop_selected("a")
        self.assertTrue(owner.running)
        second = session.advance_resource("device-a")
        self.assertIsNotNone(second.planned_control_gap)
        self.assertAlmostEqual(second.host_control_elapsed_s, 0.03)
        self.assertEqual(second.host_activation_serial, 2)
        self.assertEqual([event[0] for event in owner.events], ["start", "stop", "start"])
        self.assertEqual(session.accept_frame(first, "rx-a",
                                              frame("device-a:source", 7, 100e6, 103e6)), ())
        self.assertEqual(session.accept_frame(second, "rx-a",
                                              frame("device-a:source", 7, 101e6, 121e6)), ())
        delivered = session.accept_frame(second, "rx-a",
                                         frame("device-a:source", 8, 101e6, 121e6))
        self.assertEqual(tuple(item.pane_id for item in delivered), ("b", "d"))
        self.assertEqual(session.stop_selected("a", acknowledge_shared=True),
                         ("a", "b", "c", "d"))
        self.assertEqual(self.leases.active_resource_count, 0)
        self.assertEqual([event[0] for event in owner.events], ["start", "stop", "start", "stop"])

    def test_recording_conflict_refuses_before_leasing_or_start(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("low", "rx-a", 100e6, 108e6),
                    pane("high", "rx-a", 140e6, 148e6))
        # One shared-capture job cannot cover these spans. Use two explicit
        # time-sliced jobs so the plan itself declares a retune boundary.
        requests = tuple(pane(item.pane_id, item.receiver_endpoint_id, item.start_hz,
                              item.stop_hz, ReceiverBindingMode.TIME_SLICED) for item in requests)
        owner = FakeOwner("device-a")
        owner.recording = True
        session = self.session(groups, requests, {"device-a": owner})
        self.assertTrue(session.preview()[0].recording_conflict)
        with self.assertRaisesRegex(PaneResourceError, "recording conflicts"):
            session.apply()
        self.assertEqual(self.leases.active_resource_count, 0)
        self.assertEqual(owner.events, [])

    def test_existing_recording_does_not_trigger_second_start_for_one_capture(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6,
                         ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        owner.recording = True
        session = self.session(groups, requests, {"device-a": owner})
        self.assertTrue(session.preview()[0].recording_conflict)
        with self.assertRaisesRegex(PaneResourceError, "recording conflicts"):
            session.apply()
        self.assertEqual(self.leases.active_resource_count, 0)
        self.assertEqual(owner.events, [])

    def test_failed_stop_retains_owner_and_prevents_new_retune(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("low", "rx-a", 100e6, 108e6, ReceiverBindingMode.TIME_SLICED),
                    pane("high", "rx-a", 140e6, 148e6, ReceiverBindingMode.TIME_SLICED))
        owner = FakeOwner("device-a")
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        first = session.start_resource("device-a")
        owner.fail_stop = True
        with self.assertRaisesRegex(PaneResourceError, "no retune was sent"):
            session.advance_resource("device-a")
        self.assertEqual([event[0] for event in owner.events], ["start", "stop"])
        self.assertEqual(self.leases.active_resource_count, 1)
        self.assertEqual(session.accept_frame(first, "rx-a",
                                              frame("device-a:source", 7, 100e6, 108e6)), ())
        owner.fail_stop = False
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_failed_start_retains_lease_until_explicit_stop(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        owner.fail_start = True
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        with self.assertRaisesRegex(PaneResourceError, "explicit Stop is required"):
            session.start_resource("device-a")
        self.assertEqual(self.leases.active_resource_count, 1)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(self.leases.active_resource_count, 0)
        self.assertEqual([event[0] for event in owner.events], ["start", "stop"])

    def test_owner_admission_mismatch_retains_partial_start_for_explicit_stop(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        owner.admission_source_id = "other-source"
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        with self.assertRaisesRegex(PaneResourceError, "did not confirm admission"):
            session.start_resource("device-a")
        self.assertTrue(owner.running)
        self.assertEqual(session.retained_resource_count, 1)
        self.assertEqual(session.active_resource_count, 0)
        self.assertEqual(session.stop_all(), ())
        self.assertFalse(owner.running)
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_owner_receipt_gates_measurement_mode_and_unit(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        owner.admission_mode = "rtbw"
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        with self.assertRaisesRegex(PaneResourceError, "did not confirm admission"):
            session.start_resource("device-a")
        self.assertEqual(session.retained_resource_count, 1)
        self.assertEqual(session.stop_all(), ())

        owner = FakeOwner("device-a")
        owner.admission_unit = "dBm"
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        with self.assertRaisesRegex(PaneResourceError, "did not confirm admission"):
            session.start_resource("device-a")
        self.assertEqual(session.retained_resource_count, 1)
        self.assertEqual(session.stop_all(), ())

    def test_owner_receipt_with_wrong_applied_fs_is_rejected_before_publication(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        owner.admission_sample_rate_hz = 20_000_000.0
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        with self.assertRaisesRegex(PaneResourceError, "did not confirm admission"):
            session.start_resource("device-a")
        self.assertEqual(session.retained_resource_count, 1)
        self.assertEqual(session.stop_all(), ())

    def test_rtbw_receipt_gates_applied_geometry_generation_and_session(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        owner.admission_mode = "rtbw"
        owner.admission_generation = 5
        session = self.session(groups, requests, {"device-a": owner},
                               mode=CaptureMeasurementMode.RTBW)
        session.apply()
        activation = session.start_resource("device-a")
        valid = live_frame("device-a:source", "fake-live-session", 7)
        self.assertEqual(tuple(item.pane_id for item in session.accept_frame(
            activation, "rx-a", valid,
        )), ("one",))
        self.assertEqual(session.accept_frame(
            activation, "rx-a",
            live_frame("device-a:source", "other-session", 7),
        ), ())
        self.assertEqual(session.accept_frame(
            activation, "rx-a",
            live_frame("device-a:source", "fake-live-session", 7, generation=4),
        ), ())
        self.assertEqual(session.accept_frame(
            activation, "rx-a",
            live_frame("device-a:source", "fake-live-session", 7, fft_size=2048, hop_size=1024),
        ), ())
        self.assertEqual(session.rejected_publications("device-a"), 3)
        self.assertEqual(session.stop_all(), ())

    def test_new_rtbw_session_may_reset_numeric_epoch_after_explicit_gap(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("a-low", "rx-a", 100e6, 108e6, ReceiverBindingMode.TIME_SLICED),
                    pane("b-high", "rx-a", 140e6, 148e6, ReceiverBindingMode.TIME_SLICED))
        owner = FakeOwner("device-a")
        owner.admission_mode = "rtbw"
        owner.admission_session_id = "run-one"
        session = self.session(groups, requests, {"device-a": owner},
                               mode=CaptureMeasurementMode.RTBW)
        session.apply()
        first = session.start_resource("device-a")
        self.assertEqual(len(session.accept_frame(first, "rx-a",
                                                  live_frame("device-a:source", "run-one", 7))), 1)
        owner.admission_session_id = "run-two"
        owner.admission_epoch = 1
        second = session.advance_resource("device-a")
        self.assertEqual(len(session.accept_frame(
            second, "rx-a",
            live_frame("device-a:source", "run-two", 1, center_hz=144e6),
        )), 1)
        self.assertEqual(session.stop_all(), ())

    def test_old_activation_cannot_publish_after_cycle_returns_to_same_capture_id(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("a-low", "rx-a", 100e6, 108e6, ReceiverBindingMode.TIME_SLICED),
                    pane("b-high", "rx-a", 140e6, 148e6, ReceiverBindingMode.TIME_SLICED))
        owner = FakeOwner("device-a")
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        first = session.start_resource("device-a")
        self.assertEqual(len(session.accept_frame(
            first, "rx-a", frame("device-a:source", 7, 100e6, 108e6),
        )), 1)
        second = session.advance_resource("device-a")
        self.assertNotEqual(first.capture_id, second.capture_id)
        # No publication from the intervening capture; the last admitted
        # epoch alone cannot identify which occurrence of capture 0 produced a frame.
        third = session.advance_resource("device-a")
        self.assertEqual(first.capture_id, third.capture_id)
        self.assertNotEqual(first.host_activation_serial, third.host_activation_serial)
        future_epoch_old_token = frame("device-a:source", 9, 100e6, 108e6)
        self.assertEqual(session.accept_frame(first, "rx-a", future_epoch_old_token), ())
        self.assertEqual(session.accept_frame(third, "rx-a",
                                              frame("device-a:source", 7, 100e6, 108e6)), ())
        self.assertEqual(len(session.accept_frame(third, "rx-a", future_epoch_old_token)), 1)
        self.assertEqual(session.stop_all(), ())

    def test_retune_requires_new_epoch_even_when_first_capture_published_no_frame(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("a-low", "rx-a", 100e6, 108e6, ReceiverBindingMode.TIME_SLICED),
                    pane("b-high", "rx-a", 140e6, 148e6, ReceiverBindingMode.TIME_SLICED))
        owner = FakeOwner("device-a")
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        session.start_resource("device-a")
        owner.admission_epoch = 7  # Reused receipt, even though no frame was delivered.
        with self.assertRaisesRegex(PaneResourceError, "next receiver Start failed"):
            session.advance_resource("device-a")
        self.assertEqual([event[0] for event in owner.events], ["start", "stop", "start"])
        self.assertEqual(session.retained_resource_count, 1)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_gapless_shared_capture_slot_retags_without_reopening_or_leaking_old_token(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("low", "rx-a", 100e6, 108e6),
                    pane("high", "rx-a", 120e6, 128e6))
        owner = FakeOwner("device-a")
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        first = session.start_resource("device-a")
        next_slot = session.advance_resource("device-a")
        self.assertEqual(first.capture_id, next_slot.capture_id)
        self.assertEqual(first.host_activation_serial, next_slot.host_activation_serial)
        self.assertEqual([event[0] for event in owner.events], ["start"])
        measurement = frame("device-a:source", 7, 100e6, 128e6)
        self.assertEqual(session.accept_frame(first, "rx-a", measurement), ())
        self.assertEqual(tuple(item.pane_id for item in session.accept_frame(
            next_slot, "rx-a", measurement,
        )), ("high", "low"))
        self.assertEqual(session.stop_all(), ())

    def test_invalid_host_clock_makes_age_unknown_and_refuses_new_publication(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        activation = session.start_resource("device-a")
        self.assertEqual(len(session.accept_frame(
            activation, "rx-a", frame("device-a:source", 7, 100e6, 108e6),
        )), 1)
        self.clock[0] = float("nan")
        self.assertIsNone(session.pane_age_s("one"))
        self.assertEqual(session.accept_frame(
            activation, "rx-a", frame("device-a:source", 7, 100e6, 108e6),
        ), ())
        self.assertEqual(session.stop_all(), ())

    def test_recording_started_after_first_capture_blocks_retune_without_stop(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("a-low", "rx-a", 100e6, 108e6, ReceiverBindingMode.TIME_SLICED),
                    pane("b-high", "rx-a", 140e6, 148e6, ReceiverBindingMode.TIME_SLICED))
        owner = FakeOwner("device-a")
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        session.start_resource("device-a")
        owner.start_recording()
        with self.assertRaisesRegex(PaneResourceError, "recording blocks"):
            session.advance_resource("device-a")
        self.assertEqual([event[0] for event in owner.events], ["start"])
        self.assertEqual(session.stop_all(), ())

    def test_recording_start_waits_for_atomic_stop_retune_start_transaction(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("a-low", "rx-a", 100e6, 108e6, ReceiverBindingMode.TIME_SLICED),
                    pane("b-high", "rx-a", 140e6, 148e6, ReceiverBindingMode.TIME_SLICED))
        owner = FakeOwner("device-a")
        owner.stop_entered, owner.stop_release = Event(), Event()
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        session.start_resource("device-a")
        advance_errors: list[Exception] = []
        recorder_entered, recorder_done = Event(), Event()

        def advance() -> None:
            try:
                session.advance_resource("device-a")
            except Exception as error:
                advance_errors.append(error)

        def record() -> None:
            recorder_entered.set()
            owner.start_recording()
            recorder_done.set()

        advance_worker = Thread(target=advance)
        recorder_worker = Thread(target=record)
        advance_worker.start()
        try:
            self.assertTrue(owner.stop_entered.wait(2.0))
            recorder_worker.start()
            self.assertTrue(recorder_entered.wait(2.0))
            self.assertFalse(recorder_done.wait(0.05))
            self.assertFalse(owner.recording)
        finally:
            owner.stop_release.set()
            advance_worker.join(2.0)
            if recorder_worker.ident is not None:
                recorder_worker.join(2.0)
        self.assertFalse(advance_worker.is_alive())
        self.assertFalse(recorder_worker.is_alive())
        self.assertEqual(advance_errors, [])
        self.assertTrue(recorder_done.is_set())
        self.assertEqual([event[0] for event in owner.events], ["start", "stop", "start"])
        self.assertEqual(session.stop_all(), ())

    def test_recording_query_failure_refuses_before_claiming_resource(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        owner.fail_recording_query = True
        session = self.session(groups, requests, {"device-a": owner})
        with self.assertRaisesRegex(PaneResourceError, "recording state could not be confirmed") as error:
            session.apply()
        self.assertNotIn("raw vendor", str(error.exception))
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_owner_without_atomic_recording_guard_refuses_before_apply(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6,
                         ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        owner.control_transaction = None  # type: ignore[method-assign]
        with self.assertRaisesRegex(PaneResourceError, "recording guard"):
            self.session(groups, requests, {"device-a": owner})
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_late_frame_is_rejected_while_stop_waits_for_owner(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        owner.stop_entered, owner.stop_release = Event(), Event()
        session = self.session(groups, requests, {"device-a": owner})
        session.apply()
        activation = session.start_resource("device-a")
        errors: list[Exception] = []

        def stop() -> None:
            try:
                session.stop_selected("one")
            except Exception as error:
                errors.append(error)

        worker = Thread(target=stop)
        worker.start()
        try:
            self.assertTrue(owner.stop_entered.wait(2.0))
            self.assertEqual(session.accept_frame(activation, "rx-a",
                                                  frame("device-a:source", 7, 100e6, 108e6)), ())
            self.assertIsNone(session.pane_age_s("one"))
            self.assertEqual(self.leases.active_resource_count, 1)
        finally:
            owner.stop_release.set()
            worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_multi_resource_lease_failure_rolls_back_only_new_claims(self) -> None:
        groups = (group("device-a", "rx-a"), group("device-b", "rx-b"))
        requests = (
            pane("first", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),
            pane("second", "rx-b", 120e6, 128e6, ReceiverBindingMode.DEDICATED_PARALLEL),
        )
        occupied = self.leases.acquire(groups[1])
        owners = {"device-a": FakeOwner("device-a"), "device-b": FakeOwner("device-b")}
        session = self.session(groups, requests, owners)
        with self.assertRaisesRegex(PaneResourceError, "lease could not be acquired"):
            session.apply()
        self.assertEqual(self.leases.active_resource_count, 1)
        self.assertEqual(session.retained_resource_count, 0)
        self.assertEqual(owners["device-a"].events + owners["device-b"].events, [])
        occupied.release()
        session.apply()
        self.assertEqual(self.leases.active_resource_count, 2)
        self.assertEqual(session.stop_all(), ())

    def test_stop_all_releases_independent_owner_when_one_stop_fails(self) -> None:
        groups = (group("device-a", "rx-a"), group("device-b", "rx-b"))
        requests = (
            pane("first", "rx-a", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL),
            pane("second", "rx-b", 120e6, 128e6, ReceiverBindingMode.DEDICATED_PARALLEL),
        )
        first, second = FakeOwner("device-a"), FakeOwner("device-b")
        session = self.session(groups, requests, {"device-a": first, "device-b": second})
        session.apply()
        session.start_resource("device-a")
        session.start_resource("device-b")
        first.fail_stop = True
        self.assertEqual(session.stop_all(), ("device-a",))
        self.assertEqual(session.retained_resource_count, 1)
        self.assertTrue(first.running)
        self.assertFalse(second.running)
        first.fail_stop = False
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(session.retained_resource_count, 0)

    def test_two_digital_rx_endpoints_require_distinct_producer_identifiers(self) -> None:
        endpoints = (
            ReceiverEndpoint("rx1", "device-a:source", "device-a", ReceiverChainSelection.RX1),
            ReceiverEndpoint("rx2", "device-a:source", "device-a", ReceiverChainSelection.RX2),
        )
        groups = (AcquisitionGroup("device-a:group", "device-a", endpoints),)
        requests = (pane("first", "rx1", 100e6, 108e6),
                    pane("second", "rx2", 100e6, 108e6))
        owner = FakeOwner("device-a")
        owner.admission_mode = "rtbw"
        with self.assertRaisesRegex(PaneResourceError, "distinct observed producer receiver"):
            self.session(groups, requests, {"device-a": owner},
                         mode=CaptureMeasurementMode.RTBW)
        owner.receiver_ids = {"rx1": "producer-rx-1", "rx2": "producer-rx-2"}
        session = self.session(groups, requests, {"device-a": owner},
                               mode=CaptureMeasurementMode.RTBW)
        self.assertEqual(session.preview()[0].affected_pane_ids, ("first", "second"))
        self.assertEqual(self.leases.active_resource_count, 0)
        session.apply()
        activation = session.start_resource("device-a")
        first = live_frame("device-a:source", "fake-live-session", 7,
                           receiver_id="producer-rx-1")
        second = live_frame("device-a:source", "fake-live-session", 7,
                            receiver_id="producer-rx-2")
        self.assertEqual(tuple(item.pane_id for item in session.accept_frame(activation, "rx1", first)),
                         ("first",))
        self.assertEqual(tuple(item.pane_id for item in session.accept_frame(activation, "rx2", second)),
                         ("second",))
        self.assertEqual(session.accept_frame(activation, "rx1", second), ())
        self.assertEqual(session.stop_all(), ())

    def test_two_rx_sweep_refuses_before_apply_without_producer_identity(self) -> None:
        endpoints = (
            ReceiverEndpoint("rx1", "device-a:source", "device-a", ReceiverChainSelection.RX1),
            ReceiverEndpoint("rx2", "device-a:source", "device-a", ReceiverChainSelection.RX2),
        )
        groups = (AcquisitionGroup("device-a:group", "device-a", endpoints),)
        requests = (pane("first", "rx1", 100e6, 108e6),
                    pane("second", "rx2", 100e6, 108e6))
        owner = FakeOwner("device-a")
        owner.receiver_ids = {"rx1": "producer-rx-1", "rx2": "producer-rx-2"}
        with self.assertRaisesRegex(PaneResourceError, "Sweep producer cannot identify two RX"):
            self.session(groups, requests, {"device-a": owner})
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_single_rx_sweep_cannot_claim_receiver_identity_absent_from_bundle(self) -> None:
        groups = (group("device-a", "rx-a"),)
        requests = (pane("one", "rx-a", 100e6, 108e6,
                         ReceiverBindingMode.DEDICATED_PARALLEL),)
        owner = FakeOwner("device-a")
        owner.receiver_ids = {"rx-a": "producer-rx-1"}
        with self.assertRaisesRegex(PaneResourceError, "Sweep publication has no producer RX identity"):
            self.session(groups, requests, {"device-a": owner})
        self.assertEqual(self.leases.active_resource_count, 0)

    def test_combined_digital_rx_cannot_be_falsely_published_as_one_bundle(self) -> None:
        endpoint = ReceiverEndpoint("both", "device-a:source", "device-a",
                                    ReceiverChainSelection.BOTH)
        groups = (AcquisitionGroup("device-a:group", "device-a", (endpoint,)),)
        requests = (pane("one", "both", 100e6, 108e6,
                         ReceiverBindingMode.DEDICATED_PARALLEL),)
        with self.assertRaisesRegex(PaneResourceError, "one spectrum bundle"):
            self.session(groups, requests, {"device-a": FakeOwner("device-a")})


if __name__ == "__main__":
    unittest.main()
