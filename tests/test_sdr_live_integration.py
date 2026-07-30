"""P10 live controller/session adapter integration tests."""
from __future__ import annotations

from types import SimpleNamespace
import time
import unittest

import numpy as np

from esw_dfl.sdr.contracts import (
    CalibrationStatus,
    ComputeBackendKind,
    DetectorType,
    DeviceConfig,
    DspConfig,
    PrecisionMode,
    QualityFlag,
    SourceDescriptor,
    SourceType,
    SpectrumFrame,
    SpectrumUnit,
    WindowType,
)
from esw_dfl.sdr.controller import (
    LiveControllerState,
    LiveSdrController,
    LiveSessionConfig,
)
from esw_dfl.sdr.fixed_band import FixedBandOptions
from esw_dfl.sdr.session_adapter import LiveSessionAdapter


def make_frame(sequence: int, points: int = 8) -> SpectrumFrame:
    frequencies = 2_400_000_000.0 + np.arange(points, dtype=np.float64) * 10_000.0
    values = np.full(points, -80.0 + sequence, dtype=np.float32)
    return SpectrumFrame(
        source=SourceDescriptor(
            source_type=SourceType.LIVE_IQ,
            source_id="test-pluto",
            display_name="Test Pluto",
            uri="mock:",
            backend_id="mock",
        ),
        frame_sequence=sequence,
        first_sample_index=sequence * 512,
        timestamp_ns=1_700_000_000_000_000_000 + sequence * 1_000_000,
        config_generation=1,
        center_frequency_hz=2_400_000_000.0,
        sample_rate_hz=2_000_000.0,
        analog_bandwidth_hz=1_000_000.0,
        fft_bin_width_hz=10_000.0,
        enbw_hz=15_000.0,
        nominal_rbw_hz=15_000.0,
        fft_size=points,
        hop_size=points // 2,
        window=WindowType.HANN,
        detector=DetectorType.SAMPLE,
        precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
        unit=SpectrumUnit.DBFS_BIN,
        frequencies_hz=frequencies,
        values=values,
        calibration_status=CalibrationStatus.UNCALIBRATED,
        quality_flags=QualityFlag.UNCALIBRATED,
    )


def options() -> FixedBandOptions:
    return FixedBandOptions(
        device=DeviceConfig(
            source_id="test-pluto",
            context_uri="mock:",
            center_frequency_hz=2_400_000_000.0,
            sample_rate_hz=2_000_000.0,
            analog_bandwidth_hz=1_000_000.0,
            buffer_samples=4096,
        ),
        dsp=DspConfig(fft_size=1024, hop_size=512),
        backend=ComputeBackendKind.CPU,
    )


class FakeService:
    def __init__(self, _uri: str) -> None:
        self.frames = [make_frame(index) for index in range(100)]
        self.index = 0
        self.stop_requested = False
        self.disconnected = False

    def configure(self, _options: FixedBandOptions) -> object:
        return SimpleNamespace(
            center_frequency_hz=2_400_000_000.0,
            sample_rate_hz=2_000_000.0,
            config_generation=1,
        )

    def start(self) -> None:
        return None

    def request_stop(self) -> None:
        self.stop_requested = True

    def join(self) -> None:
        return None

    def disconnect(self) -> None:
        self.disconnected = True

    def poll_spectrum(self, max_items: int = 0) -> tuple[SpectrumFrame, ...]:
        if self.stop_requested:
            return ()
        count = max_items or 1
        result = tuple(self.frames[self.index:self.index + count])
        self.index = min(len(self.frames), self.index + count)
        return result

    def poll_events(self, _max_items: int = 0) -> tuple[object, ...]:
        return ()

    def metrics(self) -> object:
        return SimpleNamespace(
            state=LiveControllerState.RUNNING,
            engine=SimpleNamespace(
                fft_frames_computed=self.index,
                spectrum_snapshots_emitted=self.index,
                persistence_updates=0,
                iq_blocks_dropped=0,
                fft_frames_dropped=0,
            ),
        )


class LiveP10Tests(unittest.TestCase):
    def test_live_session_identity_and_exact_coordinates(self) -> None:
        adapter = LiveSessionAdapter(
            source_id="test-pluto",
            display_name="Test Pluto",
            uri="mock:",
            max_waterfall_rows=3,
        )
        session = adapter.create_session()
        self.assertEqual(session.source_descriptor.source_type, SourceType.LIVE_IQ)
        self.assertEqual(session.source_path.name, "<live:test-pluto>")

        update = SimpleNamespace(
            generation=1,
            state=LiveControllerState.RUNNING,
            spectrum_frames=(make_frame(7),),
            events=(),
            metrics=None,
            applied_config=None,
            persistence_snapshots=(),
            error=None,
        )
        rendered = adapter.apply(session, update)
        self.assertIsNotNone(rendered.trace)
        self.assertEqual(rendered.trace.unit, "dBFS/bin")
        np.testing.assert_array_equal(rendered.trace.frequencies_hz, make_frame(7).frequencies_hz)
        self.assertEqual(rendered.trace.metadata["frame_sequence"], 7)
        self.assertIs(adapter.latest_frame, update.spectrum_frames[-1])
        self.assertEqual(adapter.latest_frame.config_generation, 1)
        self.assertEqual(rendered.waterfall.point_count, 8)

    def test_waterfall_batch_is_bounded_and_not_iq(self) -> None:
        adapter = LiveSessionAdapter(
            source_id="test-pluto",
            display_name="Test Pluto",
            uri="mock:",
            max_waterfall_rows=3,
        )
        session = adapter.create_session()
        update = SimpleNamespace(
            generation=1,
            state=LiveControllerState.RUNNING,
            spectrum_frames=tuple(make_frame(index) for index in range(8)),
            events=(),
            metrics=None,
            applied_config=None,
            persistence_snapshots=(),
            error=None,
        )
        rendered = adapter.apply(session, update)
        self.assertEqual(adapter.row_count, 3)
        self.assertEqual(rendered.waterfall.values.shape, (3, 8))
        self.assertFalse(hasattr(rendered, "iq"))
        self.assertFalse(rendered.waterfall.values.flags.writeable)

    def test_stale_generation_is_ignored(self) -> None:
        adapter = LiveSessionAdapter(
            source_id="test-pluto",
            display_name="Test Pluto",
            uri="mock:",
        )
        session = adapter.create_session()
        base = SimpleNamespace(
            generation=3,
            state=LiveControllerState.RUNNING,
            spectrum_frames=(make_frame(1),),
            events=(),
            metrics=None,
            applied_config=None,
            persistence_snapshots=(),
            error=None,
        )
        adapter.apply(session, base)
        stale = SimpleNamespace(**{**base.__dict__, "generation": 2, "spectrum_frames": (make_frame(2),)})
        result = adapter.apply(session, stale)
        self.assertTrue(result.ignored_as_stale)
        self.assertEqual(session.traces[session.active_trace_id].metadata["frame_sequence"], 1)

    def test_controller_lifecycle_and_bounded_slow_ui_queue(self) -> None:
        created: list[FakeService] = []

        def factory(uri: str) -> FakeService:
            service = FakeService(uri)
            created.append(service)
            return service

        controller = LiveSdrController(
            LiveSessionConfig("test-pluto", "Test Pluto", "mock:", options()),
            service_factory=factory,
            poll_interval_s=0.001,
            spectrum_batch_size=1,
            update_queue_capacity=2,
        )
        generation = controller.start()
        self.assertEqual(generation, 1)
        self.assertTrue(controller.wait_for_state(LiveControllerState.RUNNING, 1.0))
        time.sleep(0.03)
        self.assertLessEqual(len(controller.poll_updates()), 2)
        controller.close(timeout_s=1.0)
        self.assertIn(controller.state, (LiveControllerState.STOPPED, LiveControllerState.CLOSED))
        self.assertTrue(created[0].stop_requested)
        self.assertTrue(created[0].disconnected)


if __name__ == "__main__":
    unittest.main()
