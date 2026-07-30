"""P04 engine lifecycle and bounded-transport contract tests."""

from __future__ import annotations

import time
import unittest

from esw_dfl.sdr import native_api
from esw_dfl.sdr.contracts import (
    CONTRACT_SCHEMA_VERSION,
    EngineState,
    EventSeverity,
    OverflowPolicy,
)


def _native_engine_available() -> bool:
    if not native_api.native_availability().available:
        return False
    try:
        module = native_api.require_native()
    except native_api.NativeModuleUnavailableError:
        return False
    return hasattr(module, "SyntheticEngine")


class LifecycleFallbackTests(unittest.TestCase):
    """The Python side never fabricates an engine without the native module."""

    def test_missing_module_reports_controlled_unavailability(self) -> None:
        def failing_importer(name: str) -> object:
            raise ImportError(f"no module named {name!r}")

        availability, _module = native_api.probe_native(
            "definitely_missing_sdr_native",
            importer=failing_importer,
        )
        self.assertFalse(availability.available)
        self.assertTrue(availability.reason)

    def test_engine_is_native_only(self) -> None:
        if _native_engine_available():
            self.skipTest("native engine is available on this machine")
        with self.assertRaises(native_api.NativeModuleUnavailableError):
            native_api.require_native()


@unittest.skipUnless(
    _native_engine_available(),
    "compiled P04 _sdr_native module is unavailable",
)
class NativeLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native = native_api.require_native()

    def _engine(self, **overrides: object) -> object:
        params: dict[str, object] = {
            "acquisition_queue_capacity": 16,
            "dsp_queue_capacity": 16,
            "pool_block_count": 32,
            "block_size_samples": 256,
            "snapshot_interval_blocks": 16,
        }
        params.update(overrides)
        engine = self.native.SyntheticEngine()
        engine.configure(self.native.EngineConfig(**params))
        return engine

    def test_new_wire_enums_match_python_contracts(self) -> None:
        # Member names match one-to-one; wire values are verified through
        # contract_schema() parity in tests.test_sdr_contracts.
        self.assertEqual(
            {str(member.name) for member in self.native.EngineState.__members__.values()},
            {item.name for item in EngineState},
        )
        self.assertEqual(
            {str(member.name) for member in self.native.OverflowPolicy.__members__.values()},
            {item.name for item in OverflowPolicy},
        )
        self.assertEqual(
            {str(member.name) for member in self.native.EventSeverity.__members__.values()},
            {item.name for item in EventSeverity},
        )

    def test_engine_config_is_validated_and_immutable(self) -> None:
        with self.assertRaises(self.native.ConfigurationError):
            self.native.EngineConfig(pool_block_count=0)
        config = self.native.EngineConfig()
        with self.assertRaises(AttributeError):
            config.pool_block_count = 8
        self.assertEqual(config.schema_version, CONTRACT_SCHEMA_VERSION)

    def test_state_machine_and_invalid_transitions(self) -> None:
        engine = self.native.SyntheticEngine()
        self.assertEqual(engine.state(), self.native.EngineState.CREATED)
        with self.assertRaises(self.native.ConfigurationError):
            engine.start()
        with self.assertRaises(self.native.ConfigurationError):
            engine.request_stop()

        engine.configure(self.native.EngineConfig())
        self.assertEqual(engine.state(), self.native.EngineState.CONFIGURED)
        self.assertEqual(engine.config_generation(), 1)

        engine.start()
        self.assertEqual(engine.state(), self.native.EngineState.RUNNING)
        with self.assertRaises(self.native.ConfigurationError):
            engine.start()
        with self.assertRaises(self.native.ConfigurationError):
            engine.join()

        engine.request_stop()
        self.assertEqual(engine.state(), self.native.EngineState.STOPPING)
        engine.join()
        self.assertEqual(engine.state(), self.native.EngineState.STOPPED)

    def test_run_stop_and_exact_counters(self) -> None:
        total = 5000
        engine = self._engine(max_blocks=total)
        engine.start()
        deadline = time.monotonic() + 60.0
        while engine.state() == self.native.EngineState.RUNNING and time.monotonic() < deadline:
            time.sleep(0.005)
        engine.join()
        self.assertEqual(engine.state(), self.native.EngineState.STOPPED)

        metrics = engine.metrics()
        self.assertEqual(metrics.iq_blocks_received, total)
        dsp = engine.queue_stats(self.native.QueueId.DSP)
        self.assertEqual(
            metrics.iq_blocks_received,
            dsp.popped + metrics.iq_blocks_dropped,
            "received must equal consumed plus exactly counted drops",
        )
        self.assertLessEqual(
            engine.pool_stats().high_water,
            engine.pool_stats().capacity,
        )

    def test_repeated_start_stop_cycles(self) -> None:
        engine = self.native.SyntheticEngine()
        for generation in range(1, 4):
            engine.configure(
                self.native.EngineConfig(block_size_samples=256, pool_block_count=32)
            )
            self.assertEqual(engine.config_generation(), generation)
            engine.start()
            time.sleep(0.02)
            engine.stop()
            self.assertEqual(engine.state(), self.native.EngineState.STOPPED)

    def test_events_and_snapshots_are_bounded(self) -> None:
        engine = self._engine(
            max_blocks=20000,
            acquisition_queue_capacity=2,
            dsp_queue_capacity=2,
            pool_block_count=4,
        )
        engine.start()
        deadline = time.monotonic() + 60.0
        while engine.state() == self.native.EngineState.RUNNING and time.monotonic() < deadline:
            time.sleep(0.005)
        engine.join()

        codes = {str(event.code) for event in engine.poll_events(0)}
        self.assertIn("engine_configured", codes)
        self.assertIn("engine_started", codes)
        self.assertIn("engine_stopped", codes)

        snapshots = engine.poll_snapshots(0)
        self.assertTrue(snapshots)
        self.assertLessEqual(len(snapshots), engine.config().snapshot_queue_capacity)
        for snapshot in snapshots:
            self.assertGreaterEqual(snapshot.iq_blocks_received, 0)

    def test_metrics_fields_and_types(self) -> None:
        engine = self._engine(max_blocks=1000)
        engine.start()
        engine.stop()
        metrics = engine.metrics()
        for name in (
            "iq_samples_received",
            "iq_samples_dropped",
            "iq_blocks_received",
            "iq_blocks_dropped",
            "fft_frames_computed",
            "fft_frames_dropped",
            "spectrum_snapshots_emitted",
        ):
            self.assertIsInstance(getattr(metrics, name), int, name)
        self.assertGreaterEqual(metrics.fft_frames_computed, 0)
        self.assertGreaterEqual(metrics.fft_frames_dropped, 0)
        with self.assertRaises(AttributeError):
            metrics.iq_blocks_received = 0

    def test_destructor_stops_active_engine(self) -> None:
        engine = self._engine()
        engine.start()
        self.assertEqual(engine.state(), self.native.EngineState.RUNNING)
        del engine  # destructor must stop and join without hanging


if __name__ == "__main__":
    unittest.main()
