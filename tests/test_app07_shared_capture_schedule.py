"""APP-07 planning: one capture may feed disjoint panes inside one RF window."""

from dataclasses import replace
import unittest

from sdr_monitor.domain.pane_scheduler import (
    CaptureEpochCost,
    CaptureMeasurementMode,
    PaneCaptureProfile,
    PaneScheduleError,
    compile_pane_schedule,
)
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup,
    PaneSchedulerPolicy,
    ReceiverBindingMode,
    ReceiverChainSelection,
    ReceiverEndpoint,
    SchedulerPolicyKind,
    SweepPaneRequest,
)


def profile(usable_hz: float, mode: CaptureMeasurementMode = CaptureMeasurementMode.SWEEP) -> PaneCaptureProfile:
    return PaneCaptureProfile(
        sample_rate_hz=61_440_000.0,
        analog_bandwidth_hz=56_000_000.0,
        gain_mode="manual",
        manual_gain_db=20.0,
        fft_size=4096,
        hop_size=2048,
        window="hann",
        detector="peak",
        calibration_profile_id=None,
        usable_capture_span_hz=usable_hz,
        epoch_cost=CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005),
        measurement_mode=mode,
    )


def group(resource_id: str, endpoint_id: str) -> AcquisitionGroup:
    endpoint = ReceiverEndpoint(endpoint_id, resource_id + ":source", resource_id,
                                ReceiverChainSelection.RX1)
    return AcquisitionGroup(resource_id + ":group", resource_id, (endpoint,))


def pane(name: str, endpoint_id: str, start_hz: float, stop_hz: float,
         mode: ReceiverBindingMode = ReceiverBindingMode.SHARED_CAPTURE) -> SweepPaneRequest:
    return SweepPaneRequest(name, endpoint_id, start_hz, stop_hz, profile_id="capture", requested_binding_mode=mode)


class SharedCaptureScheduleTests(unittest.TestCase):
    def test_disjoint_panes_inside_one_window_share_exactly_one_capture(self) -> None:
        schedule = compile_pane_schedule(
            (group("device-1", "rx1"),),
            (pane("low", "rx1", 100_000_000, 108_000_000),
             pane("high", "rx1", 120_000_000, 128_000_000)),
            {"capture": profile(36_000_000)},
        )
        resource = schedule.resources[0]
        self.assertEqual(len(resource.jobs), 1)
        capture = resource.jobs[0]
        self.assertIs(capture.mode, ReceiverBindingMode.SHARED_CAPTURE)
        self.assertEqual((capture.start_hz, capture.stop_hz), (100_000_000, 128_000_000))
        self.assertEqual({crop.pane_id: (crop.start_hz, crop.stop_hz) for crop in capture.crops},
                         {"low": (100_000_000, 108_000_000), "high": (120_000_000, 128_000_000)})
        self.assertEqual(len(resource.slots), 1)
        self.assertIsNone(resource.slots[0].control_gap_before)

    def test_finite_partition_does_not_strand_a_valid_pair(self) -> None:
        requests = (
            pane("a", "rx1", 100_000_000, 101_000_000),
            pane("b", "rx1", 100_100_000, 101_100_000),
            pane("c", "rx1", 101_200_000, 102_200_000),
            pane("d", "rx1", 102_300_000, 103_300_000),
        )
        schedule = compile_pane_schedule((group("device-1", "rx1"),), requests,
                                         {"capture": profile(2_200_000)})
        captures = schedule.resources[0].jobs
        self.assertEqual(len(captures), 2)
        self.assertEqual(tuple(tuple(crop.pane_id for crop in job.crops) for job in captures),
                         (("a", "b"), ("c", "d")))
        self.assertEqual(len(schedule.pane_revisits), 4)

    def test_nonadjacent_capture_pair_is_not_falsely_refused(self) -> None:
        # The broad b pane separates narrow a/c in start order. Captures a+c
        # and b+d are valid, but no contiguous two-and-two partition is.
        requests = (
            pane("a", "rx1", 100_000_000, 101_000_000),
            pane("b", "rx1", 101_000_000, 121_000_000),
            pane("c", "rx1", 102_000_000, 103_000_000),
            pane("d", "rx1", 120_000_000, 121_000_000),
        )
        schedule = compile_pane_schedule((group("device-1", "rx1"),), requests,
                                         {"capture": profile(20_000_000)})
        captures = schedule.resources[0].jobs
        self.assertEqual(tuple(tuple(crop.pane_id for crop in job.crops) for job in captures),
                         (("a", "c"), ("b", "d")))
        self.assertEqual(tuple((job.start_hz, job.stop_hz) for job in captures),
                         ((100_000_000, 103_000_000), (101_000_000, 121_000_000)))

    def test_shared_capture_outside_usable_window_refuses_before_owner(self) -> None:
        with self.assertRaisesRegex(PaneScheduleError, "shared-capture pane spans"):
            compile_pane_schedule(
                (group("device-1", "rx1"),),
                (pane("low", "rx1", 100_000_000, 108_000_000),
                 pane("high", "rx1", 140_000_000, 148_000_000)),
                {"capture": profile(36_000_000)},
            )

    def test_distinct_priorities_do_not_hide_in_one_shared_capture(self) -> None:
        high_priority = replace(
            pane("high", "rx1", 100_000_000, 108_000_000),
            scheduler_policy=PaneSchedulerPolicy(SchedulerPolicyKind.WEIGHTED, 2),
        )
        with self.assertRaisesRegex(PaneScheduleError, "shared-capture pane spans"):
            compile_pane_schedule(
                (group("device-1", "rx1"),),
                (high_priority, pane("normal", "rx1", 120_000_000, 128_000_000)),
                {"capture": profile(36_000_000)},
            )

    def test_separate_resource_keys_produce_separate_parallel_plans(self) -> None:
        schedule = compile_pane_schedule(
            (group("device-a", "rx-a"), group("device-b", "rx-b")),
            (pane("first", "rx-a", 100_000_000, 108_000_000, ReceiverBindingMode.DEDICATED_PARALLEL),
             pane("second", "rx-b", 120_000_000, 128_000_000, ReceiverBindingMode.DEDICATED_PARALLEL)),
            {"capture": profile(36_000_000)},
        )
        self.assertEqual(tuple(resource.physical_stream_resource_id for resource in schedule.resources),
                         ("device-a", "device-b"))
        self.assertTrue(all(len(resource.jobs) == 1 for resource in schedule.resources))


if __name__ == "__main__":
    unittest.main()
