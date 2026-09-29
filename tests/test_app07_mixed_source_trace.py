"""APP-07 three heterogeneous sources plus one truly empty fourth slot."""

import unittest

import numpy as np

from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, RtbwFrameMetadata, bundle_from_sweep
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.pane_scheduler import (
    CaptureEpochCost, CaptureMeasurementMode, PaneCaptureProfile, PaneLayoutSlot,
    PaneScheduleError, SpectrumTracePaneProfile, compile_pane_layout, compile_pane_schedule,
)
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint,
    SpectrumTraceEndpoint, SweepPaneRequest,
)
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState, SweepQualitySchema
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepProvenance
from sdr_monitor.services.pane_resource_session import PaneCaptureAdmission, PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager

from tests.test_app07_pane_resource_session import FakeOwner


_DEVICE_KEY = "sha256:" + "a" * 64
_FIRMWARE_KEY = "sha256:" + "b" * 64
_COST = CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005)


def iq_profile(sample_rate_hz: float) -> PaneCaptureProfile:
    return PaneCaptureProfile(
        sample_rate_hz=sample_rate_hz, analog_bandwidth_hz=sample_rate_hz * 0.8,
        gain_mode="manual", manual_gain_db=20.0, fft_size=4, hop_size=2,
        window="hann", detector="peak", calibration_profile_id=None,
        usable_capture_span_hz=sample_rate_hz * 0.7, epoch_cost=_COST,
        measurement_mode=CaptureMeasurementMode.RTBW,
    )


def iq_bundle(source_id: str, center_hz: float, sample_rate_hz: float, epoch: int) -> AnalyzerFrameBundle:
    grid = center_hz + (np.arange(4) - 2) * (sample_rate_hz / 4)
    frame = LiveSpectrumFrame(
        sequence=1, timestamp_ns=1, center_frequency_hz=center_hz,
        sample_rate_hz=sample_rate_hz, fft_size=4, hop_size=2,
        frequencies_hz=grid, values=np.full(4, -70, dtype=np.float32),
        source_id=source_id, acquisition_epoch=epoch, config_generation=5,
    )
    return AnalyzerFrameBundle(frame, source_id + ":session", None, epoch,
                               RtbwFrameMetadata(center_hz, sample_rate_hz, 4, 2))


def trace_bundle(source_id: str, epoch: int, *, device_key: str = _DEVICE_KEY,
                 start_hz: int = 300_000_000) -> AnalyzerFrameBundle:
    provenance = TinySaSweepProvenance(
        selection_revision=1, configuration_generation=9, model_id="tinysa_ultra",
        device_identity_key=device_key, firmware_fingerprint=_FIRMWARE_KEY,
        start_hz=start_hz, stop_hz=start_hz + 4_000_000, points=4,
        observed_zero_db=0.0, host_elapsed_s=1.0,
    )
    line = SweepLineFrame(
        sequence=1, epoch=epoch, completed_at_ns=1, source_id=source_id,
        state=SweepLineState.COMPLETE,
        frequencies_hz=np.array([start_hz + index * 1_000_000 for index in range(4)]),
        values_db=np.array([-90, -80, -75, -92], dtype=np.float32),
        quality_flags=np.zeros(4, dtype=np.uint16),
        source_segment_indices=np.full(4, -1, dtype=np.int32),
        missing_segment_indices=(), segment_config_generations=(), gap_reasons=(),
        unit="dBm", quality_schema=SweepQualitySchema.INSTRUMENT_V1, instrument=provenance,
    )
    return bundle_from_sweep(line)


class FakeTraceOwner(FakeOwner):
    def start_capture(self, job) -> PaneCaptureAdmission:
        self.events.append(("start", job.capture_id))
        self.running = True
        epoch = self.admission_epoch
        self.admission_epoch += 1
        return PaneCaptureAdmission(
            job.capture_id, self.admission_source_id,
            CaptureMeasurementMode.INSTRUMENT_TRACE, "dBm",
            job.receiver_endpoint_ids, epoch, config_generation=9,
            trace_points=job.profile.points, instrument_model_id="tinysa_ultra",
            instrument_identity_key=_DEVICE_KEY,
            firmware_fingerprint=_FIRMWARE_KEY,
        )


class MixedSourcePaneTests(unittest.TestCase):
    def test_three_independent_sources_and_empty_fourth_slot(self) -> None:
        groups = (
            AcquisitionGroup("pluto:group", "pluto", (
                ReceiverEndpoint("pluto:rx1", "pluto:source", "pluto", ReceiverChainSelection.RX1),)),
            AcquisitionGroup("hackrf:group", "hackrf", (
                ReceiverEndpoint("hackrf:rx", "hackrf:source", "hackrf", ReceiverChainSelection.RX1),)),
            AcquisitionGroup("tinysa:group", "tinysa", (
                SpectrumTraceEndpoint("tinysa:trace", "tinysa:source", "tinysa"),)),
        )
        slots = (
            PaneLayoutSlot(1, SweepPaneRequest("pane-1", "pluto:rx1", 100e6, 102e6,
                                               profile_id="pluto", requested_binding_mode=ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(2, SweepPaneRequest("pane-2", "hackrf:rx", 910e6, 912e6,
                                               profile_id="hackrf", requested_binding_mode=ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(3, SweepPaneRequest("pane-3", "tinysa:trace", 300e6, 303e6,
                                               profile_id="tinysa", requested_binding_mode=ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(4),
        )
        profiles = {
            "pluto": iq_profile(61_440_000.0),
            "hackrf": iq_profile(20_000_000.0),
            "tinysa": SpectrumTracePaneProfile(4, 4_000_000.0, "ultra-low/rbw-auto", _COST),
        }
        layout = compile_pane_layout(slots, groups, profiles)
        self.assertEqual(layout.empty_slots, (4,))
        self.assertIsNotNone(layout.schedule)
        assert layout.schedule is not None
        self.assertEqual({item.physical_stream_resource_id for item in layout.schedule.resources},
                         {"pluto", "hackrf", "tinysa"})
        self.assertEqual(len(layout.schedule.pane_revisits), 3)

        pluto, hackrf, tinysa = FakeOwner("pluto"), FakeOwner("hackrf"), FakeTraceOwner("tinysa")
        for owner in (pluto, hackrf):
            owner.admission_mode = "rtbw"
            owner.admission_session_id = owner.admission_source_id + ":session"
            owner.admission_generation = 5
        leases = ReceiverLeaseManager(max_active_resources=4)
        session = PaneResourceSession(layout.schedule, groups,
                                      {"pluto": pluto, "hackrf": hackrf, "tinysa": tinysa},
                                      leases, now_s=lambda: 10.0)
        session.apply()
        activations = {resource: session.start_resource(resource) for resource in ("pluto", "hackrf", "tinysa")}
        self.assertEqual(session.active_resource_count, 3)
        self.assertEqual(leases.active_resource_count, 3)
        ad = iq_bundle("pluto:source", 101e6, 61_440_000.0, 7)
        hf = iq_bundle("hackrf:source", 911e6, 20_000_000.0, 7)
        ts = trace_bundle("tinysa:source", 7)
        for resource, endpoint, measurement, pane_id in (
            ("pluto", "pluto:rx1", ad, "pane-1"),
            ("hackrf", "hackrf:rx", hf, "pane-2"),
            ("tinysa", "tinysa:trace", ts, "pane-3"),
        ):
            deliveries = session.accept_frame(activations[resource], endpoint, measurement)
            self.assertEqual(tuple(item.pane_id for item in deliveries), (pane_id,))
            self.assertIs(deliveries[0].bundle, measurement)
        self.assertEqual(session.accept_frame(activations["pluto"], "pluto:rx1", hf), ())
        self.assertEqual(session.accept_frame(activations["tinysa"], "tinysa:trace",
                                              trace_bundle("tinysa:source", 7,
                                                           device_key="sha256:" + "c" * 64)), ())
        self.assertEqual(session.rejected_publications("pluto"), 1)
        self.assertEqual(session.rejected_publications("tinysa"), 1)
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(leases.active_resource_count, 0)

    def test_trace_and_iq_profiles_cannot_impersonate_each_other(self) -> None:
        trace_group = (AcquisitionGroup("trace:group", "trace", (
            SpectrumTraceEndpoint("trace:output", "trace:source", "trace"),)),)
        request = (SweepPaneRequest("pane-1", "trace:output", 300e6, 303e6,
                                    profile_id="capture", requested_binding_mode=ReceiverBindingMode.DEDICATED_PARALLEL),)
        with self.assertRaisesRegex(PaneScheduleError, "must not be interchanged"):
            compile_pane_schedule(trace_group, request, {"capture": iq_profile(20e6)})
        with self.assertRaisesRegex(PaneScheduleError, "must not invent I/Q"):
            PaneCaptureProfile(
                20e6, 16e6, "manual", 20.0, 4, 2, "hann", "peak", None,
                10e6, _COST, CaptureMeasurementMode.INSTRUMENT_TRACE,
            )
        with self.assertRaisesRegex(PaneScheduleError, r"\[2, 10001\]"):
            SpectrumTracePaneProfile(10002, 4e6, "ultra-low/rbw-auto", _COST)
        with self.assertRaisesRegex(PaneScheduleError, "only dBm"):
            SpectrumTracePaneProfile(4, 4e6, "ultra-low/rbw-auto", _COST, unit="dBFS/bin")
        with self.assertRaisesRegex(ValueError, "without I/Q geometry"):
            PaneCaptureAdmission("capture", "trace:source", CaptureMeasurementMode.INSTRUMENT_TRACE,
                                 "dBm", ("trace:output",), 1, sample_rate_hz=20e6,
                                 config_generation=9, trace_points=4, instrument_model_id="tinysa_ultra",
                                 instrument_identity_key=_DEVICE_KEY,
                                 firmware_fingerprint=_FIRMWARE_KEY)

    def test_one_trace_instrument_time_slices_two_ranges_with_new_epoch(self) -> None:
        group = AcquisitionGroup("tinysa:group", "tinysa", (
            SpectrumTraceEndpoint("tinysa:trace", "tinysa:source", "tinysa"),))
        requests = (
            SweepPaneRequest("low", "tinysa:trace", 300e6, 303e6,
                             profile_id="trace", requested_binding_mode=ReceiverBindingMode.TIME_SLICED),
            SweepPaneRequest("high", "tinysa:trace", 320e6, 323e6,
                             profile_id="trace", requested_binding_mode=ReceiverBindingMode.TIME_SLICED),
        )
        schedule = compile_pane_schedule(
            (group,), requests,
            {"trace": SpectrumTracePaneProfile(4, 4e6, "ultra-low/rbw-auto", _COST)},
        )
        self.assertEqual(len(schedule.resources[0].jobs), 2)
        owner = FakeTraceOwner("tinysa")
        leases = ReceiverLeaseManager(max_active_resources=4)
        session = PaneResourceSession(schedule, (group,), {"tinysa": owner}, leases, now_s=lambda: 10.0)
        session.apply()
        first = session.start_resource("tinysa")
        first_job = next(job for job in schedule.resources[0].jobs if job.capture_id == first.capture_id)
        self.assertEqual(tuple(item.pane_id for item in session.accept_frame(
            first, "tinysa:trace", trace_bundle("tinysa:source", 7,
                                                start_hz=int(first_job.start_hz)))),
                         (first_job.crops[0].pane_id,))
        second = session.advance_resource("tinysa")
        self.assertNotEqual(first.capture_id, second.capture_id)
        self.assertEqual(session.accept_frame(first, "tinysa:trace", trace_bundle("tinysa:source", 7)), ())
        second_job = next(job for job in schedule.resources[0].jobs if job.capture_id == second.capture_id)
        deliveries = session.accept_frame(second, "tinysa:trace", trace_bundle(
            "tinysa:source", 8, start_hz=int(second_job.start_hz)))
        self.assertEqual(tuple(item.pane_id for item in deliveries), (second_job.crops[0].pane_id,))
        self.assertEqual([event[0] for event in owner.events], ["start", "stop", "start"])
        self.assertEqual(session.stop_all(), ())
        self.assertEqual(leases.active_resource_count, 0)

    def test_all_empty_layout_has_no_capture_schedule(self) -> None:
        layout = compile_pane_layout(tuple(PaneLayoutSlot(index) for index in range(1, 5)), (), {})
        self.assertEqual(layout.empty_slots, (1, 2, 3, 4))
        self.assertIsNone(layout.schedule)
        with self.assertRaisesRegex(PaneScheduleError, "contiguous slots"):
            compile_pane_layout((PaneLayoutSlot(1), PaneLayoutSlot(3)), (), {})


if __name__ == "__main__":
    unittest.main()
