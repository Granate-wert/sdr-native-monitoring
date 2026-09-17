"""R10-B bounded native physical-sweep adapter tests with no SDR hardware."""

from __future__ import annotations

from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from sdr_monitor.domain import BackendKind, LiveConfiguration, SweepConfiguration, SweepExecutionMode, SweepRateEvidence, SweepState
from sdr_monitor.services.native_sweep import NativeSweepService, NativeSweepSource


class _ConfigRecord:
    def __init__(self, *args: object) -> None:
        self.args = args


class _Engine:
    def __init__(self, _uri: str, _timeout_ms: int) -> None:
        self.generation = 40
        self.center_hz = 0.0
        self.configure_calls = 0
        self.reconfigure_calls = 0
        self.start_calls = 0
        self.request_stop_calls = 0
        self.join_calls = 0
        self.disconnect_calls = 0
        self.device_samples = 0
        self.transient_blocks_discarded = 0

    def configure(self, config: _ConfigRecord) -> object:
        self.configure_calls += 1
        self.center_hz = float(config.args[0].args[2])
        self.transient_blocks_discarded += 1
        return SimpleNamespace(
            config_generation=self.generation,
            center_frequency_hz=self.center_hz,
            sample_rate_hz=100.0,
            analog_bandwidth_hz=100.0,
            manual_gain_db=18.0,
        )

    def reconfigure(self, config: _ConfigRecord) -> object:
        self.reconfigure_calls += 1
        self.generation += 1
        self.center_hz = float(config.args[0].args[2])
        self.transient_blocks_discarded += 1
        return SimpleNamespace(
            config_generation=self.generation,
            center_frequency_hz=self.center_hz,
            sample_rate_hz=100.0,
            analog_bandwidth_hz=100.0,
            manual_gain_db=18.0,
        )

    def config_generation(self) -> int:
        return self.generation

    def start(self) -> None:
        self.start_calls += 1

    def poll_spectrum_frames(self, _max_items: int) -> tuple[object, ...]:
        self.device_samples += 512
        frequencies = np.linspace(self.center_hz - 50.0, self.center_hz + 50.0, 257, dtype=np.float64)
        stale = SimpleNamespace(
            config_generation=self.generation - 1,
            frequencies_hz=frequencies,
            values=np.full(frequencies.size, -20.0, dtype=np.float32),
        )
        current = SimpleNamespace(
            config_generation=self.generation,
            frequencies_hz=frequencies,
            values=np.full(frequencies.size, -30.0 + self.reconfigure_calls, dtype=np.float32),
        )
        return stale, current

    def metrics(self) -> object:
        return SimpleNamespace(
            engine=SimpleNamespace(
                analytical_fft_rate=321.0,
                fft_frames_dropped=0,
                end_to_end_latency_ms=1.25,
            ),
            device=SimpleNamespace(
                samples_received=self.device_samples,
                output_blocks_dropped=0,
                short_reads=0,
                refill_errors=0,
                output_pool_exhaustions=0,
                estimated_dropped_samples=0,
            ),
            acquisition_queue_blocks_dropped=0,
            transient_blocks_discarded=self.transient_blocks_discarded,
            acquisition_queue=SimpleNamespace(high_water=2, capacity=16),
            spectrum_queue=SimpleNamespace(high_water=1, capacity=4),
        )

    def request_stop(self) -> None:
        self.request_stop_calls += 1

    def join(self) -> None:
        self.join_calls += 1

    def disconnect(self) -> None:
        self.disconnect_calls += 1


class _Native:
    class GainMode:
        MANUAL = "manual"

    class WindowType:
        HANN = "hann"

    class DetectorType:
        SAMPLE = "sample"

    class SpectrumUnit:
        DBFS_BIN = "dbfs_bin"

    class PrecisionMode:
        ACCURATE_F32_F64_ACCUM = "accurate"

    class CalibrationStatus:
        UNCALIBRATED = "uncalibrated"

    class PersistenceMode:
        DISABLED = "disabled"
        EXPONENTIAL_DECAY = "exponential"
        ROLLING_EXACT = "rolling"

    class ComputeBackendKind:
        CPU = "cpu"

    class OverflowPolicy:
        DROP_NEWEST = "drop_newest"

    DeviceConfig = _ConfigRecord
    DspConfig = _ConfigRecord
    PersistenceConfig = _ConfigRecord
    FixedBandConfig = _ConfigRecord

    def __init__(self) -> None:
        self.engines: list[_Engine] = []

    def PlutoFixedBandEngine(self, uri: str, timeout_ms: int) -> _Engine:
        engine = _Engine(uri, timeout_ms)
        self.engines.append(engine)
        return engine


def _source() -> NativeSweepSource:
    return NativeSweepSource(
        "ip:fake-pluto",
        "native-sweep-test",
        LiveConfiguration(
            center_hz=2.4e9,
            sample_rate_hz=100.0,
            analog_bandwidth_hz=100.0,
            fft_size=256,
            backend=BackendKind.CPU,
            persistence_enabled=False,
            persistence_mode="disabled",
        ),
    )


class R10BNativeSweepTests(unittest.TestCase):
    def test_native_sweep_is_generation_explicit_bounded_and_cpu_only(self) -> None:
        native = _Native()
        exclusive_checks = 0

        def assert_exclusive() -> None:
            nonlocal exclusive_checks
            exclusive_checks += 1

        service = NativeSweepService(native, _source(), assert_exclusive=assert_exclusive)
        result = service.execute(
            SweepConfiguration(
                start_hz=1_000.0,
                stop_hz=1_140.0,
                dc_margin_hz=5.0,
                settling_s=0.001,
                dwell_s=0.001,
                discard_blocks=2,
                execution_mode=SweepExecutionMode.NATIVE,
            ),
            lambda _progress: None,
        )

        self.assertIs(result.state, SweepState.COMPLETED)
        self.assertEqual(len(result.segment_evidence), 2)
        self.assertEqual([item.config_generation for item in result.segment_evidence], [40, 41])
        self.assertTrue(all(item.rejected_stale_frames > 0 for item in result.segment_evidence))
        self.assertTrue(all(item.transient_blocks_discarded == 1 for item in result.segment_evidence))
        self.assertTrue(all(item.observed_device_iq_sample_rate_hz is not None for item in result.segment_evidence))
        self.assertTrue(all(item.observed_device_iq_samples and item.observed_device_iq_samples > 0 for item in result.segment_evidence))
        self.assertTrue(all(item.acquisition_queue_high_water == 2 for item in result.segment_evidence))
        self.assertTrue(all(item.acquisition_queue_capacity == 16 for item in result.segment_evidence))
        self.assertTrue(all(item.spectrum_queue_high_water == 1 for item in result.segment_evidence))
        self.assertTrue(all(item.spectrum_queue_capacity == 4 for item in result.segment_evidence))
        self.assertTrue(all(item.end_to_end_latency_ms == 1.25 for item in result.segment_evidence))
        self.assertTrue(all(item.applied_sample_rate_hz == 100.0 for item in result.segment_evidence))
        self.assertTrue(all(item.applied_analog_bandwidth_hz == 100.0 for item in result.segment_evidence))
        self.assertTrue(all(item.applied_gain_db == 18.0 for item in result.segment_evidence))
        self.assertIsNotNone(result.stitched_grid)
        assert result.stitched_grid is not None
        self.assertIsNone(result.stitched_grid.config_generation)
        self.assertEqual(result.stitched_grid.segment_config_generations, ((0, 40), (1, 41)))
        self.assertIsNotNone(result.rate_metrics)
        assert result.rate_metrics is not None
        self.assertIs(result.rate_metrics.evidence, SweepRateEvidence.MEASURED)
        self.assertEqual(result.rate_metrics.fft_lps, 321.0)
        self.assertGreater(exclusive_checks, 2)
        self.assertEqual(len(native.engines), 1)
        engine = native.engines[0]
        self.assertEqual((engine.configure_calls, engine.reconfigure_calls, engine.start_calls), (1, 1, 1))
        self.assertEqual((engine.request_stop_calls, engine.join_calls, engine.disconnect_calls), (1, 1, 1))
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "native-sweep.json"
            self.assertEqual(service.export_result(result, target), target)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["configuration"]["execution_mode"], "native")
            self.assertEqual([item["config_generation"] for item in payload["segments"]], [40, 41])
            self.assertTrue(all(item["observed_device_iq_samples"] > 0 for item in payload["segments"]))
            self.assertEqual(payload["segments"][0]["acquisition_queue_capacity"], 16)
            self.assertEqual(payload["segments"][0]["applied_sample_rate_hz"], 100.0)

    def test_exclusive_lease_is_checked_before_engine_construction(self) -> None:
        native = _Native()

        def deny() -> None:
            raise RuntimeError("stop Live before starting Sweep")

        service = NativeSweepService(native, _source(), assert_exclusive=deny)
        with self.assertRaisesRegex(RuntimeError, "stop Live"):
            service.execute(
                SweepConfiguration(start_hz=1_000.0, stop_hz=1_100.0, dc_margin_hz=5.0, execution_mode=SweepExecutionMode.NATIVE),
                lambda _progress: None,
            )
        self.assertEqual(native.engines, [])

    def test_unverified_auto_backend_is_rejected_before_native_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "verified CPU"):
            NativeSweepSource(
                "ip:fake-pluto",
                "native-sweep-test",
                LiveConfiguration(
                    sample_rate_hz=100.0,
                    analog_bandwidth_hz=100.0,
                    fft_size=256,
                    persistence_enabled=False,
                    persistence_mode="disabled",
                ),
            )


if __name__ == "__main__":
    unittest.main()
