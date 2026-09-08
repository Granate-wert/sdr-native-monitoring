"""R10-D2 snapshot bridge tests: UI consumes only final bounded line snapshots."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

import numpy as np

from sdr_monitor.domain import SweepLineState
from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService


def _line(sequence: int, *, gap: bool = False) -> SimpleNamespace:
    values = np.array([np.nan, -93.0], dtype=np.float32) if gap else np.array([-94.0, -93.0], dtype=np.float32)
    return SimpleNamespace(
        line_sequence=sequence,
        epoch=9,
        completed_ns=1000 + sequence,
        source_id="r10d-test",
        state="gap" if gap else "complete",
        frequencies_hz=np.array([100.0, 101.0], dtype=np.float64),
        values=values,
        quality_flags_per_bin=np.array([1, 0], dtype=np.uint32) if gap else np.array([0, 0], dtype=np.uint32),
        source_segment_indices=np.array([-1, 1], dtype=np.int32) if gap else np.array([0, 1], dtype=np.int32),
        missing_segment_indices=(0,) if gap else (),
        segment_config_generations=((0, 51), (1, 52)),
        gap_reasons=("cancellation",) if gap else (),
        unit="dBFS/bin",
    )


class _FakeCoordinator:
    def __init__(self, _uri: str, _timeout_ms: int) -> None:
        self.lines: list[SimpleNamespace] = []
        self.progress = None
        self.metrics_value = SimpleNamespace(
            completed_lines=0,
            gapped_lines=0,
            terminal_control_gaps=0,
            output_snapshots_superseded=0,
            output_queue=SimpleNamespace(depth=0, capacity=4),
            has_error=False,
        )
        self.started = False
        self.disconnected = False

    def configure(self, config: object) -> None:
        self.config = config

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def disconnect(self) -> None:
        self.disconnected = True

    def poll_lines(self) -> list[SimpleNamespace]:
        lines, self.lines = self.lines, []
        return lines

    def metrics(self) -> SimpleNamespace:
        return self.metrics_value

    def poll_progress(self):
        result, self.progress = self.progress, None
        return result


class _Native:
    NativeContinuousSweepCoordinator = _FakeCoordinator


def _config():
    return SimpleNamespace(epoch=9, segments=[SimpleNamespace(
        fixed_band=SimpleNamespace(device=SimpleNamespace(source_id="r10d-test")),
    )])


class R10DDisplayServiceTests(unittest.TestCase):
    def test_foreign_source_or_epoch_fails_before_publication(self):
        service = NativeContinuousSweepDisplayService(_Native, "usb:test")
        service.start(_config())
        try:
            for field, value in (("source_id", "another-device"), ("epoch", 8)):
                line = _line(1)
                setattr(line, field, value)
                service._coordinator.lines = [line]
                with self.assertRaisesRegex(RuntimeError, "active request"):
                    service.poll_latest()
                service._coordinator.progress = line
                with self.assertRaisesRegex(RuntimeError, "active request"):
                    service.poll_latest()
        finally:
            service.close()

    def test_stop_keeps_terminal_cancellation_available_once(self):
        service = NativeContinuousSweepDisplayService(_Native, "usb:test")
        service.start(_config())
        coordinator = service._coordinator
        service.stop()
        coordinator.lines = [_line(4, gap=True)]
        # Preview is intentionally malformed: a stopped service must not poll it.
        coordinator.progress = object()
        final = service.poll_latest()
        self.assertIsNotNone(final.line)
        self.assertEqual(final.line.sequence, 4)
        self.assertIs(final.line.state, SweepLineState.GAP)
        self.assertIsNone(final.progress)
        self.assertIsNone(service.poll_latest().line)
        self.assertIsNotNone(coordinator.progress)
        service.close()

    def test_late_and_duplicate_preview_cannot_revive_terminal_pass(self):
        service = NativeContinuousSweepDisplayService(_Native, "usb:test")
        service.start(_config())
        coordinator = service._coordinator
        def preview(sequence):
            result = _line(sequence, gap=True)
            result.revision = 1
            result.acquired_segment_generations = ((1, 52),)
            result.pending_segment_indices = (0,)
            result.quality_flags_per_bin[0] = 4096
            for field in ("frequencies_hz", "values", "quality_flags_per_bin", "source_segment_indices"):
                getattr(result, field).setflags(write=False)
            return result
        coordinator.progress = preview(1)
        self.assertIsNotNone(service.poll_latest().progress)
        coordinator.progress = preview(1)
        self.assertIsNone(service.poll_latest().progress)
        coordinator.lines = [_line(2)]
        service.poll_latest()
        coordinator.progress = preview(2)
        self.assertIsNone(service.poll_latest().progress)
        coordinator.progress = preview(3)
        self.assertIsNotNone(service.poll_latest().progress)
        service.stop()
        service.start(_config())
        coordinator.progress = preview(1)
        self.assertIsNotNone(service.poll_latest().progress)
        service.close()

    def test_drains_to_latest_line_and_keeps_line_lps_separate_from_ui_supersession(self) -> None:
        service = NativeContinuousSweepDisplayService(_Native, "usb:test")
        service.start(_config())
        coordinator = service._coordinator  # test-only observation of fake state
        coordinator.lines = [_line(1), _line(2)]
        coordinator.metrics_value.completed_lines = 5
        first = service.poll_latest(now_s=10.0)
        self.assertEqual(first.line.sequence if first.line else None, 2)
        self.assertIs(first.line.state if first.line else None, SweepLineState.COMPLETE)
        self.assertEqual(first.metrics.completed_line_lps, 0.0)
        self.assertEqual(first.metrics.ui_snapshots_superseded, 1)
        self.assertEqual(first.metrics.native_queue_capacity, 4)

        coordinator.metrics_value.completed_lines = 7
        second = service.poll_latest(now_s=11.0)
        self.assertIsNone(second.line)
        self.assertEqual(second.metrics.completed_line_lps, 2.0)
        self.assertEqual(second.metrics.ui_snapshots_superseded, 1)

    def test_explicit_gap_is_preserved_and_close_never_exposes_raw_iq(self) -> None:
        service = NativeContinuousSweepDisplayService(_Native, "usb:test")
        service.start(_config())
        coordinator = service._coordinator
        coordinator.lines = [_line(3, gap=True)]
        coordinator.metrics_value.gapped_lines = 1
        coordinator.metrics_value.terminal_control_gaps = 1
        snapshot = service.poll_latest(now_s=1.0)
        self.assertIsNotNone(snapshot.line)
        assert snapshot.line is not None
        self.assertIs(snapshot.line.state, SweepLineState.GAP)
        self.assertEqual(tuple(reason.value for reason in snapshot.line.gap_reasons), ("cancellation",))
        self.assertFalse(snapshot.line.values_db.flags.writeable)
        service.close()
        self.assertTrue(coordinator.disconnected)
        with self.assertRaises(RuntimeError):
            service.poll_latest(now_s=2.0)


if __name__ == "__main__":
    unittest.main()
