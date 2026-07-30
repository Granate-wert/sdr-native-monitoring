from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Any

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from esw_dfl.frame_navigation import (
    FrameLoadCoordinator,
    FrameNavigationController,
    FramePresentationScheduler,
    FrameSnapshot,
    NavigationConfig,
    NavigationReason,
    ScrollState,
)
from esw_dfl.spectrogram import SpectrogramRow


class _MockLoader(QObject):
    """Synchronous mock loader for scheduler tests."""

    snapshot_ready = Signal(object)
    error = Signal(str)
    diagnostics = Signal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[str, str, int, int, NavigationReason]] = []
        self._pending: list[tuple[str, str, int, int, NavigationReason]] = []
        self.delayed: bool = False
        self._delayed_rows: dict[int, SpectrogramRow] = {}

    def request(
        self,
        session_id: str,
        waterfall_id: str,
        frame_index: int,
        generation: int,
        reason: NavigationReason,
    ) -> None:
        self.requests.append((session_id, waterfall_id, frame_index, generation, reason))
        if self.delayed:
            self._pending.append((session_id, waterfall_id, frame_index, generation, reason))
            return
        self.snapshot_ready.emit(
            FrameSnapshot(
                session_id=session_id,
                waterfall_id=waterfall_id,
                frame_index=frame_index,
                generation=generation,
                reason=reason,
                row=SpectrogramRow(frame_index, float(frame_index), np.array([float(frame_index)])),
            )
        )

    def release_pending(self) -> None:
        for session_id, waterfall_id, frame_index, generation, reason in self._pending:
            self.snapshot_ready.emit(
                FrameSnapshot(
                    session_id=session_id,
                    waterfall_id=waterfall_id,
                    frame_index=frame_index,
                    generation=generation,
                    reason=reason,
                    row=SpectrogramRow(frame_index, float(frame_index), np.array([float(frame_index)])),
                )
            )
        self._pending.clear()

    def cancel_all(self) -> None:
        self._pending.clear()

    def clear_cache(self) -> None:
        pass


class FrameNavigationInputTests(unittest.TestCase):
    def test_default_wheel_step_is_ten(self) -> None:
        """Production NavigationConfig default wheel_step must remain 10."""
        config = NavigationConfig()
        self.assertEqual(config.wheel_step, 10)

    def test_wheel_one_notch_moves_step(self) -> None:
        ctrl = FrameNavigationController(NavigationConfig(wheel_step=10))
        ctrl.set_frame_count(100_000)
        ctrl.handle_wheel(120, None, None)
        self.assertEqual(ctrl.requested_frame, 10)
        self.assertEqual(ctrl.navigation_reason, NavigationReason.WHEEL)

    def test_wheel_two_notches_move_two_steps(self) -> None:
        ctrl = FrameNavigationController(NavigationConfig(wheel_step=5))
        ctrl.set_frame_count(100_000)
        ctrl.handle_wheel(240, None, None)
        self.assertEqual(ctrl.requested_frame, 10)

    def test_wheel_fractional_accumulates(self) -> None:
        ctrl = FrameNavigationController(NavigationConfig(wheel_step=10))
        ctrl.set_frame_count(100_000)
        ctrl.handle_wheel(60, None, None)
        self.assertEqual(ctrl.requested_frame, 0)
        ctrl.handle_wheel(60, None, None)
        self.assertEqual(ctrl.requested_frame, 10)

    def test_wheel_direction_reversal_clears_accumulator(self) -> None:
        ctrl = FrameNavigationController(NavigationConfig(wheel_step=10))
        ctrl.set_frame_count(100_000)
        ctrl.seek(100, NavigationReason.WHEEL)
        ctrl.handle_wheel(60, None, None)
        self.assertEqual(ctrl.requested_frame, 100)
        ctrl.handle_wheel(-120, None, None)
        self.assertEqual(ctrl.requested_frame, 90)
        # Remainder should be 0 after exact step.
        self.assertEqual(ctrl._angle_accumulator, 0.0)

    def test_touchpad_threshold_accumulates(self) -> None:
        ctrl = FrameNavigationController(NavigationConfig(wheel_step=10, touchpad_threshold=20.0))
        ctrl.set_frame_count(100_000)

        class _Point:
            def __init__(self, y: float) -> None:
                self._y = y

            def y(self) -> float:
                return self._y

            def x(self) -> float:
                return 0.0

        ctrl.handle_wheel(0, _Point(15.0), None)
        self.assertEqual(ctrl.requested_frame, 0)
        ctrl.handle_wheel(0, _Point(15.0), None)
        self.assertEqual(ctrl.requested_frame, 10)

    def test_alt_modifier_uses_one_frame_step(self) -> None:
        from PySide6.QtCore import Qt

        ctrl = FrameNavigationController(NavigationConfig(wheel_step=10, alt_step=1))
        ctrl.set_frame_count(100_000)
        ctrl.handle_wheel(120, None, Qt.KeyboardModifier.AltModifier)
        self.assertEqual(ctrl.requested_frame, 1)

    def test_shift_modifier_uses_multiplier(self) -> None:
        from PySide6.QtCore import Qt

        ctrl = FrameNavigationController(NavigationConfig(wheel_step=10, shift_multiplier=10))
        ctrl.set_frame_count(100_000)
        ctrl.handle_wheel(120, None, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(ctrl.requested_frame, 100)

    def test_delta_target_is_clipped_at_bounds(self) -> None:
        ctrl = FrameNavigationController(NavigationConfig(wheel_step=10))
        ctrl.set_frame_count(15)
        ctrl.handle_wheel(120, None, None)
        self.assertEqual(ctrl.requested_frame, 10)
        ctrl.handle_wheel(120, None, None)
        self.assertEqual(ctrl.requested_frame, 14)


class FrameNavigationControllerStateTests(unittest.TestCase):
    def test_seek_sets_requested_frame(self) -> None:
        ctrl = FrameNavigationController()
        ctrl.set_frame_count(100_000)
        ctrl.seek(50_000, NavigationReason.SLIDER)
        self.assertEqual(ctrl.requested_frame, 50_000)
        self.assertEqual(ctrl.scroll_state, ScrollState.ACTIVE)

    def test_set_displayed_frame_moves_to_settling_when_matches(self) -> None:
        ctrl = FrameNavigationController()
        ctrl.set_frame_count(100_000)
        ctrl.seek(1000, NavigationReason.WHEEL)
        ctrl.set_displayed_frame(1000)
        self.assertEqual(ctrl.displayed_frame, 1000)
        self.assertEqual(ctrl.scroll_state, ScrollState.SETTLING)

    def test_settle_becomes_idle_when_matching(self) -> None:
        ctrl = FrameNavigationController()
        ctrl.set_frame_count(100_000)
        ctrl.seek(1000, NavigationReason.WHEEL)
        ctrl.set_displayed_frame(1000)
        ctrl.settle()
        self.assertEqual(ctrl.scroll_state, ScrollState.IDLE)

    def test_generation_increments_on_target_change(self) -> None:
        ctrl = FrameNavigationController()
        ctrl.set_frame_count(100_000)
        gen_before = ctrl.generation
        ctrl.handle_wheel(120, None, None)
        self.assertEqual(ctrl.generation, gen_before + 1)
        ctrl.handle_wheel(120, None, None)
        self.assertEqual(ctrl.generation, gen_before + 2)

    def test_span_event_emitted(self) -> None:
        ctrl = FrameNavigationController(NavigationConfig(wheel_step=10))
        ctrl.set_frame_count(100_000)
        events: list[Any] = []
        ctrl.span_event.connect(events.append)
        ctrl.handle_wheel(120, None, None)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].previous_target, 0)
        self.assertEqual(events[0].new_target, 10)
        self.assertEqual(events[0].reason, NavigationReason.WHEEL)

    def test_invalidate_bumps_generation_without_changing_target(self) -> None:
        ctrl = FrameNavigationController()
        ctrl.set_frame_count(100_000)
        ctrl.seek(1000, NavigationReason.WHEEL)
        gen_before = ctrl.generation
        target_before = ctrl.requested_frame
        ctrl.invalidate()
        self.assertEqual(ctrl.generation, gen_before + 1)
        self.assertEqual(ctrl.requested_frame, target_before)


class FramePresentationSchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.config = NavigationConfig(fps=60, settle_delay_ms=0)
        self.controller = FrameNavigationController(self.config)
        self.controller.set_frame_count(100_000)
        self.loader = _MockLoader()
        self.scheduler = FramePresentationScheduler(
            self.controller, self.loader, self.config
        )
        self.scheduler.set_active_context("session", "waterfall")
        self.applied: list[FrameSnapshot] = []
        self.scheduler.apply_snapshot.connect(self.applied.append)

    def test_latest_target_wins_on_wheel_burst(self) -> None:
        self.controller.handle_wheel(120, None, None)  # +10
        self.controller.handle_wheel(120, None, None)  # +20
        self.controller.handle_wheel(120, None, None)  # +30
        self.scheduler.schedule(immediate=True)
        self.app.processEvents()
        self.assertEqual(self.controller.requested_frame, 30)
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(self.applied[0].frame_index, 30)

    def test_sequential_mode_advances_one_frame_per_tick(self) -> None:
        self.scheduler.set_sequential_mode(True)
        self.controller.seek(10, NavigationReason.WHEEL)
        self.scheduler.schedule(immediate=True)
        self.app.processEvents()
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(self.applied[0].frame_index, 1)
        self.scheduler.schedule(immediate=True)
        self.app.processEvents()
        self.assertEqual(len(self.applied), 2)
        self.assertEqual(self.applied[1].frame_index, 2)

    def test_playback_lagged_snapshot_is_applied_before_latest_target(self) -> None:
        self.loader.delayed = True
        self.scheduler.set_playback_active(True)
        self.controller.seek(100, NavigationReason.PLAYBACK)
        self.scheduler.schedule(immediate=True)
        self.controller.seek(1000, NavigationReason.PLAYBACK)
        self.scheduler.schedule(immediate=True)

        self.loader.release_pending()
        self.app.processEvents()

        self.assertEqual([snapshot.frame_index for snapshot in self.applied], [100, 1000])
        self.assertEqual(self.controller.displayed_frame, 1000)

    def test_playback_lag_is_discarded_after_pause_invalidation(self) -> None:
        self.loader.delayed = True
        self.scheduler.set_playback_active(True)
        self.controller.seek(100, NavigationReason.PLAYBACK)
        self.scheduler.schedule(immediate=True)
        self.scheduler.cancel_and_invalidate()

        self.loader.release_pending()
        self.app.processEvents()

        self.assertEqual(self.applied, [])

    def test_manual_seek_supersedes_inflight_playback_snapshot(self) -> None:
        self.loader.delayed = True
        self.scheduler.set_playback_active(True)
        self.controller.seek(100, NavigationReason.PLAYBACK)
        self.scheduler.schedule(immediate=True)
        self.controller.seek(5000, NavigationReason.WHEEL)
        self.scheduler.schedule(immediate=True)

        self.loader.release_pending()
        self.app.processEvents()

        self.assertEqual([snapshot.frame_index for snapshot in self.applied], [5000])

    def test_stale_result_not_applied(self) -> None:
        self.loader.delayed = True
        self.controller.seek(1000, NavigationReason.WHEEL)
        first_generation = self.controller.generation
        self.scheduler.schedule(immediate=True)
        self.app.processEvents()
        self.controller.seek(5000, NavigationReason.WHEEL)
        self.scheduler.schedule(immediate=True)
        self.app.processEvents()
        # Simulate an old result arriving after the user has already moved on.
        self.loader.snapshot_ready.emit(
            FrameSnapshot(
                session_id="session",
                waterfall_id="waterfall",
                frame_index=1000,
                generation=first_generation,
                reason=NavigationReason.WHEEL,
                row=SpectrogramRow(1000, 1000.0, np.array([1000.0])),
            )
        )
        self.app.processEvents()
        self.assertEqual(len(self.applied), 0)
        # Now the result for the latest target arrives.
        self.loader.snapshot_ready.emit(
            FrameSnapshot(
                session_id="session",
                waterfall_id="waterfall",
                frame_index=5000,
                generation=self.controller.generation,
                reason=NavigationReason.WHEEL,
                row=SpectrogramRow(5000, 5000.0, np.array([5000.0])),
            )
        )
        self.app.processEvents()
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(self.applied[0].frame_index, 5000)

    def test_repeated_same_target_not_reapplied(self) -> None:
        self.controller.seek(100, NavigationReason.WHEEL)
        self.scheduler.schedule(immediate=True)
        self.app.processEvents()
        self.assertEqual(len(self.applied), 1)
        self.scheduler.schedule(immediate=True)
        self.app.processEvents()
        self.assertEqual(len(self.applied), 1)

    def test_cancel_and_invalidate_stops_timer_and_clears_pending(self) -> None:
        self.loader.delayed = True
        self.controller.seek(100, NavigationReason.PLAYBACK)
        self.scheduler.schedule(immediate=True)
        self.app.processEvents()
        self.assertTrue(self.scheduler._timer.isActive())
        self.assertEqual(len(self.loader._pending), 1)
        gen_before = self.controller.generation
        self.scheduler.cancel_and_invalidate()
        self.assertFalse(self.scheduler._timer.isActive())
        self.assertEqual(len(self.loader._pending), 0)
        self.assertEqual(self.controller.generation, gen_before + 1)

    def test_cancel_and_invalidate_discards_late_results(self) -> None:
        self.loader.delayed = True
        self.controller.seek(100, NavigationReason.PLAYBACK)
        old_generation = self.controller.generation
        self.scheduler.schedule(immediate=True)
        self.app.processEvents()
        self.scheduler.cancel_and_invalidate()
        # Simulate a frame result that was already in flight before cancel.
        self.loader.snapshot_ready.emit(
            FrameSnapshot(
                session_id="session",
                waterfall_id="waterfall",
                frame_index=100,
                generation=old_generation,
                reason=NavigationReason.PLAYBACK,
                row=SpectrogramRow(100, 100.0, np.array([100.0])),
            )
        )
        self.app.processEvents()
        self.assertEqual(len(self.applied), 0)

    def test_cancel_and_invalidate_disables_sequential_mode(self) -> None:
        self.scheduler.set_sequential_mode(True)
        self.controller.seek(500, NavigationReason.PLAYBACK)
        self.controller.set_displayed_frame(500)
        self.scheduler.cancel_and_invalidate()
        # Simulate Stop -> first_frame: a direct seek to 0 must jump, not walk
        # back one frame per tick because sequential mode was left enabled.
        self.controller.seek(0, NavigationReason.API)
        self.scheduler.schedule(immediate=True)
        self.app.processEvents()
        self.assertEqual(self.controller.displayed_frame, 0)
        self.assertEqual(self.applied[-1].frame_index, 0)


class FrameLoadCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _context(self, session_id: str, waterfall_id: str):
        index = None

        class _Reader:
            def read_frame(self, frame_index: int) -> SpectrogramRow:
                return SpectrogramRow(frame_index, float(frame_index), np.array([float(frame_index)]))

        return (Path("dummy.dfl"), index, _Reader())

    def test_small_indexed_frame_uses_interactive_fast_path(self) -> None:
        class _Reader:
            def frame_payload_size(self, frame_index: int) -> int:
                return 6168

            def read_frame(self, frame_index: int) -> SpectrogramRow:
                return SpectrogramRow(
                    frame_index,
                    float(frame_index),
                    np.array([float(frame_index)]),
                )

        reader = _Reader()
        loader = FrameLoadCoordinator(
            lambda _s, _w: (Path("dummy.dfl"), None, reader),
            interactive_read_limit_bytes=64 * 1024,
        )
        ready: list[Any] = []
        loader.snapshot_ready.connect(ready.append)

        loader.request("session", "waterfall", 5, 1, NavigationReason.PLAYBACK)

        self.assertEqual([snapshot.frame_index for snapshot in ready], [5])
        self.assertIsNone(loader._worker)
        self.assertEqual(loader._diagnostics["interactive_loads"], 1)
        self.assertEqual(loader._diagnostics["worker_loads"], 0)

    def test_cache_hit_avoids_worker(self) -> None:
        from PySide6.QtCore import QThreadPool

        loader = FrameLoadCoordinator(self._context, max_cache=4)
        loader.request("session", "waterfall", 5, 1, NavigationReason.WHEEL)
        QThreadPool.globalInstance().waitForDone()
        self.app.processEvents()
        # Second request for same frame is a cache hit.
        ready: list[Any] = []
        loader.snapshot_ready.connect(ready.append)
        loader.request("session", "waterfall", 5, 2, NavigationReason.WHEEL)
        self.app.processEvents()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].frame_index, 5)
        self.assertEqual(ready[0].generation, 2)

    def test_error_signal_on_missing_context(self) -> None:
        loader = FrameLoadCoordinator(lambda _s, _w: (Path("dummy.dfl"), None, None))
        errors: list[str] = []
        loader.error.connect(errors.append)
        loader.request("session", "waterfall", 0, 1, NavigationReason.WHEEL)
        self.app.processEvents()
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
