"""Reduced Sweep publication reordering/coalescing; fake native, no RX."""

from dataclasses import replace
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState
from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService
from sdr_monitor.ui.presenters.continuous_sweep_presenter import ContinuousSweepPresenter
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests.test_app01_product_analyzer import _progress
from tests.test_app04_sweep_failure_ownership import _Coordinator, _config


def _line(sequence=1):
    return SweepLineFrame(
        sequence=sequence, epoch=7, completed_at_ns=100, source_id="fake-sweep",
        state=SweepLineState.COMPLETE, frequencies_hz=np.array([100e6, 101e6]),
        values_db=np.array([-70., -60.]), quality_flags=np.zeros(2, np.uint16),
        source_segment_indices=np.array([0, 1]), missing_segment_indices=(),
        segment_config_generations=((0, 11), (1, 12)), gap_reasons=(), unit="dBFS/bin",
    )


def _native_line(sequence):
    frame = _line(sequence)
    return SimpleNamespace(
        source_id=frame.source_id, epoch=frame.epoch, line_sequence=frame.sequence,
        state=frame.state.value, completed_ns=frame.completed_at_ns,
        frequencies_hz=frame.frequencies_hz, values=frame.values_db,
        quality_flags_per_bin=frame.quality_flags, source_segment_indices=frame.source_segment_indices,
        missing_segment_indices=frame.missing_segment_indices,
        segment_config_generations=frame.segment_config_generations,
        gap_reasons=(), unit=frame.unit,
    )


def _native_progress(sequence):
    frame = replace(_progress(), sequence=sequence)
    return SimpleNamespace(
        source_id=frame.source_id, epoch=frame.epoch, line_sequence=frame.sequence,
        revision=frame.revision, unit=frame.unit, frequencies_hz=frame.frequencies_hz,
        values=frame.values_db, quality_flags_per_bin=frame.quality_flags,
        source_segment_indices=frame.source_segment_indices,
        acquired_segment_generations=frame.acquired_segment_generations,
        pending_segment_indices=frame.pending_segment_indices,
    )


class SweepPublicationOrderTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = _Coordinator()
        self.lines = ()
        self.progress = None
        self.coordinator.poll_lines = lambda: self.lines
        self.coordinator.poll_progress = lambda: self.progress
        native = SimpleNamespace(NativeContinuousSweepCoordinator=lambda *_: self.coordinator)
        self.service = NativeContinuousSweepDisplayService(native, "usb:fake")
        config = _config()
        config.epoch = 7
        config.segments[0].fixed_band.device.source_id = "fake-sweep"
        self.service.start(config)
        self.addCleanup(self.service.close)

    def test_reordered_batch_selects_highest_sequence_not_last_arrival(self):
        self.lines = (_native_line(3), _native_line(1), _native_line(2))
        snapshot = self.service.poll_latest()
        self.assertEqual(snapshot.line.sequence, 3)
        self.assertEqual(snapshot.metrics.ui_snapshots_superseded, 2)

    def test_duplicate_and_stale_terminal_are_not_republished(self):
        self.lines = (_native_line(3),)
        self.assertEqual(self.service.poll_latest().line.sequence, 3)
        for sequence in (3, 2, 1):
            self.lines = (_native_line(sequence),)
            snapshot = self.service.poll_latest()
            self.assertIsNone(snapshot.line)
            self.assertIsNone(snapshot.analyzer_bundle)

    def test_late_terminal_is_delivered_without_rolling_back_newer_preview(self):
        self.progress = _native_progress(3)
        initial = self.service.poll_latest().progress
        self.lines = (_native_line(2),)
        self.progress = None
        snapshot = self.service.poll_latest()
        self.assertEqual(snapshot.line.sequence, 2)  # terminal consumers still receive it
        self.assertIs(snapshot.analyzer_bundle.spectrum, initial)

    def test_terminal_supersedes_same_sequence_preview_then_rejects_repeat(self):
        self.progress = _native_progress(3)
        self.service.poll_latest()
        self.lines = (_native_line(3),)
        snapshot = self.service.poll_latest()
        self.assertIsNone(snapshot.progress)
        self.assertEqual(snapshot.analyzer_bundle.publication_kind.value, "sweep_complete")
        self.assertIsNone(self.service.poll_latest().analyzer_bundle)

    def test_rejected_packet_does_not_commit_publication_watermarks(self):
        self.lines = (_native_line(3),)
        self.progress = _native_progress(4)
        self.progress.source_id = "foreign"
        with self.assertRaisesRegex(RuntimeError, "source/epoch"):
            self.service.poll_latest()
        self.progress = None
        self.assertIsNotNone(self.service.poll_latest().line)

    def test_duplicate_older_and_skipped_progress_revisions(self):
        self.progress = _native_progress(3)
        self.assertIsNotNone(self.service.poll_latest().progress)
        for sequence in (2, 3):
            self.progress = _native_progress(sequence)
            self.assertIsNone(self.service.poll_latest().progress)
        self.progress = _native_progress(5)  # coalescing may skip a whole preview
        self.assertEqual(self.service.poll_latest().progress.sequence, 5)
        self.progress.revision = 2
        self.progress.acquired_segment_generations = ((0, 11), (1, 12))
        self.progress.pending_segment_indices = (2,)
        snapshot = self.service.poll_latest()
        self.assertEqual(snapshot.progress.revision, 2)
        self.assertTrue(np.isnan(snapshot.progress.values_db[-1]))
        self.assertIsNone(self.service.poll_latest().progress)

    def test_stop_gap_finalizes_partial_without_inventing_missing_bins(self):
        self.progress = _native_progress(3)
        self.service.poll_latest()
        gap = _native_line(3)
        gap.state = "gap"
        gap.gap_reasons = ("cancellation",)
        gap.values = np.array([-70., np.nan])
        gap.quality_flags_per_bin = np.array([0, 4096], np.uint32)
        gap.source_segment_indices = np.array([0, -1], np.int32)
        gap.missing_segment_indices = (1,)
        self.lines = (gap,)
        self.service.stop()
        snapshot = self.service.poll_latest()
        self.assertIsNone(snapshot.progress)
        self.assertEqual(snapshot.analyzer_bundle.publication_kind.value, "sweep_gap")
        self.assertTrue(snapshot.analyzer_bundle.terminal_sweep)
        self.assertTrue(np.isnan(snapshot.line.values_db[-1]))
        self.assertIsNone(self.service.poll_latest().line)

    def test_superseded_progress_is_rejected_before_full_grid_conversion(self):
        self.progress = _native_progress(3)
        self.service.poll_latest()
        from sdr_monitor.services.native_continuous_sweep import _to_domain_progress
        with patch("sdr_monitor.services.native_continuous_sweep._to_domain_progress",
                   wraps=_to_domain_progress) as convert:
            for sequence in (2, 3):
                self.progress = _native_progress(sequence)
                self.assertIsNone(self.service.poll_latest().progress)
            self.progress = _native_progress(4)
            self.lines = (_native_line(4),)
            self.assertIsNone(self.service.poll_latest().progress)
            self.assertEqual(convert.call_count, 0)
            self.progress = _native_progress(5)
            self.assertEqual(self.service.poll_latest().progress.sequence, 5)
            self.assertEqual(convert.call_count, 1)

    def test_stale_progress_still_checks_producer_and_scalar_identity(self):
        self.progress = _native_progress(3)
        self.service.poll_latest()
        for changes in ({"source_id": "foreign"}, {"epoch": 99},
                        {"line_sequence": -1}, {"revision": True}):
            self.progress = _native_progress(2)
            self.progress.__dict__.update(changes)
            with self.subTest(changes=changes), self.assertRaises((ValueError, RuntimeError)):
                self.service.poll_latest()

    def test_restart_clears_retained_preview_and_terminal_watermarks(self):
        self.progress = _native_progress(8)
        self.service.poll_latest()
        self.service.stop()
        config = _config()
        config.epoch = 7
        config.segments[0].fixed_band.device.source_id = "fake-sweep"
        self.service.start(config)
        self.progress = None
        self.lines = (_native_line(1),)
        self.assertEqual(self.service.poll_latest().analyzer_bundle.spectrum.sequence, 1)

    def test_snapshot_terminal_wins_same_or_older_preview(self):
        for sequence in (1, 2):
            with self.subTest(sequence=sequence):
                line = _line(2)
                snapshot = ContinuousSweepDisplaySnapshot(
                    line, ContinuousSweepDisplayMetrics(), replace(_progress(), sequence=sequence))
                self.assertIs(snapshot.analyzer_bundle.spectrum, line)

    def test_snapshot_keeps_terminal_and_next_preview_distinct(self):
        line = _line(2)
        progress = replace(_progress(), sequence=3)
        snapshot = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(), progress)
        self.assertIs(snapshot.line, line)
        self.assertIs(snapshot.analyzer_bundle.spectrum, progress)
        self.assertFalse(snapshot.analyzer_bundle.terminal_sweep)

    def test_snapshot_rejects_mixed_source_epoch_unit_or_grid(self):
        for update in ({"source_id": "other"}, {"epoch": 99}, {"unit": "dBm"},
                       {"frequencies_hz": np.array([200e6, 201e6])}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                ContinuousSweepDisplaySnapshot(
                    replace(_line(2), **update), ContinuousSweepDisplayMetrics(),
                    replace(_progress(), sequence=3),
                )


class SweepPublicationPresenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_conversion_failure_stops_owned_rx_without_emitting_half_packet(self):
        calls = []
        snapshot = ContinuousSweepDisplaySnapshot(_line(), ContinuousSweepDisplayMetrics())
        service = SimpleNamespace(
            start=lambda _: calls.append("start"),
            stop=lambda: calls.append("stop"),
            close=lambda: calls.append("close"),
            poll_latest=lambda: snapshot,
        )
        presenter = ContinuousSweepPresenter(service, max_poll_hz=1)
        metrics, lines, bundles, errors = [], [], [], []
        presenter.metrics_ready.connect(metrics.append)
        presenter.line_ready.connect(lines.append)
        presenter.analyzer_ready.connect(bundles.append)
        presenter.task_failed.connect(errors.append)
        try:
            presenter.start(object())
            deadline = time.monotonic() + 3
            while presenter.is_starting and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.001)
            self.assertFalse(presenter.is_starting)
            actual_bundle = type(snapshot).analyzer_bundle.fget
            bundle_calls = 0

            def fail_first_bundle(value):
                nonlocal bundle_calls
                bundle_calls += 1
                if bundle_calls == 1:
                    raise ValueError("injected bundle conversion failure")
                return actual_bundle(value)

            with patch.object(ContinuousSweepDisplaySnapshot, "analyzer_bundle", property(fail_first_bundle)):
                presenter._poll()  # must not escape the Qt timer callback
                while not presenter.can_close() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.001)
            self.assertTrue(presenter.can_close())
            self.assertEqual(calls, ["start", "stop"])
            self.assertEqual(errors, ["injected bundle conversion failure"])
            self.assertEqual(len(lines), 1)  # explicit final poll, after successful Stop
            self.assertEqual(len(metrics), 1)
            self.assertEqual(len(bundles), 1)
        finally:
            presenter.shutdown()

    def test_coalesced_terminal_and_preview_have_separate_signal_semantics(self):
        snapshot = ContinuousSweepDisplaySnapshot(
            _line(2), ContinuousSweepDisplayMetrics(completed_lines=2), replace(_progress(), sequence=3))
        presenter = ContinuousSweepPresenter(SimpleNamespace(close=lambda: None))
        lines, bundles = [], []
        presenter.line_ready.connect(lines.append)
        presenter.analyzer_ready.connect(bundles.append)
        try:
            presenter._emit_snapshot(snapshot)
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0].sequence, 2)
            self.assertEqual(len(bundles), 1)
            self.assertEqual(bundles[0].spectrum.sequence, 3)
            self.assertFalse(bundles[0].terminal_sweep)
        finally:
            presenter.shutdown()

    def test_native_adapter_presenter_and_v2_scene_do_not_regress_to_late_terminal(self):
        # Exercise the real publication layers into the real shared V2 canvas;
        # only the libiio coordinator is deterministic and has no hardware.
        harness = SweepPublicationOrderTests("runTest")
        harness.setUp()
        presenter = ContinuousSweepPresenter(harness.service)
        scene = SpectrumScene()
        lines = []
        presenter.line_ready.connect(lines.append)
        presenter.analyzer_ready.connect(scene.set_frame)
        try:
            harness.progress = _native_progress(3)
            presenter._emit_snapshot(harness.service.poll_latest())
            first = scene.latest_frame.spectrum
            harness.progress = None
            harness.lines = (_native_line(2),)
            presenter._emit_snapshot(harness.service.poll_latest())
            self.assertIs(scene.latest_frame.spectrum, first)
            self.assertTrue(np.isnan(scene.latest_frame.values[-1]))
            self.assertEqual([line.sequence for line in lines], [2])
            harness.lines = (_native_line(3),)
            presenter._emit_snapshot(harness.service.poll_latest())
            self.assertEqual(scene.latest_frame.publication_kind.value, "sweep_complete")
            self.assertTrue(np.isfinite(scene.latest_frame.values).all())
            presenter._emit_snapshot(harness.service.poll_latest())
            self.assertEqual([line.sequence for line in lines], [2, 3])
        finally:
            presenter.shutdown()
            scene.close()
            scene.deleteLater()
            harness.doCleanups()
