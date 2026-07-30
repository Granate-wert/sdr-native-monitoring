from __future__ import annotations

import logging
import math
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from .spectrogram import (
    SpectrogramFrameReader,
    SpectrogramIndex,
    SpectrogramRow,
    read_spectrogram_frame,
)
from .workers import TaskWorker

LOGGER = logging.getLogger(__name__)


class NavigationReason(StrEnum):
    WHEEL = "wheel"
    TOUCHPAD = "touchpad"
    SLIDER = "slider"
    FRAME_INPUT = "frame_input"
    PLAYBACK = "playback"
    API = "api"


class ScrollState(StrEnum):
    IDLE = "idle"
    ACTIVE = "active"
    SETTLING = "settling"


class FramePolicy(StrEnum):
    ENDPOINTS_ONLY = "endpoints_only"
    FULL_RANGE = "full_range"
    TIME_WINDOW = "time_window"
    SAMPLED_RANGE = "sampled_range"


@dataclass(frozen=True, slots=True)
class FrameSnapshot:
    """Согласованный снимок кадра, готовый к атомарному применению в UI."""

    session_id: str
    waterfall_id: str
    frame_index: int
    generation: int
    reason: NavigationReason
    row: SpectrogramRow


@dataclass(frozen=True, slots=True)
class FrameSpanEvent:
    """Точка расширения для Persistence Spectrum / Heatmap.

    Аналитический pipeline не должен зависеть от количества UI-repaint.
    """

    previous_target: int
    new_target: int
    direction: int
    reason: NavigationReason
    generation: int


@dataclass(slots=True)
class NavigationConfig:
    wheel_step: int = 10
    touchpad_threshold: float = 20.0
    fps: int = 60
    settle_delay_ms: int = 120
    sequential_mode: bool = False
    alt_step: int = 1
    shift_multiplier: int = 10


def _trunc_toward_zero(value: float) -> int:
    return int(value)


def _clip(value: float | int, low: float | int, high: float | int) -> int:
    return int(max(low, min(high, value)))


@dataclass(slots=True)
class _LoadRequest:
    session_id: str
    waterfall_id: str
    frame_index: int
    generation: int
    reason: NavigationReason


class FrameLoadCoordinator(QObject):
    """Ограничивает загрузку кадров: не более одного active и одного pending.

    Результат с устаревшим generation допускается в LRU-кэш, но не эмитится
    как ready. UI-уровень (scheduler) отфильтровывает по актуальному generation.
    """

    snapshot_ready = Signal(object)  # FrameSnapshot
    error = Signal(str)              # human-readable message
    diagnostics = Signal(dict)

    def __init__(
        self,
        context_callback: Callable[[str, str], tuple[str | Path, SpectrogramIndex | None, SpectrogramFrameReader | None]],
        max_cache: int = 256,
        interactive_read_limit_bytes: int = 64 * 1024,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._context_callback = context_callback
        self._max_cache = max(max_cache, 1)
        self._interactive_read_limit_bytes = max(0, int(interactive_read_limit_bytes))
        self._cache: OrderedDict[tuple[str, str, int], SpectrogramRow] = OrderedDict()
        self._active: _LoadRequest | None = None
        self._pending: _LoadRequest | None = None
        self._worker: TaskWorker | None = None
        self._diagnostics = {
            "cache_hits": 0,
            "cache_misses": 0,
            "stale_discarded": 0,
            "active_loads": 0,
            "pending_loads": 0,
            "interactive_loads": 0,
            "worker_loads": 0,
        }

    def request(
        self,
        session_id: str,
        waterfall_id: str,
        frame_index: int,
        generation: int,
        reason: NavigationReason,
    ) -> None:
        key = (session_id, waterfall_id, frame_index)
        if key in self._cache:
            self._diagnostics["cache_hits"] += 1
            self.snapshot_ready.emit(
                FrameSnapshot(
                    session_id=session_id,
                    waterfall_id=waterfall_id,
                    frame_index=frame_index,
                    generation=generation,
                    reason=reason,
                    row=self._cache[key],
                )
            )
            self.diagnostics.emit(self._diagnostics.copy())
            return

        self._diagnostics["cache_misses"] += 1
        req = _LoadRequest(
            session_id=session_id,
            waterfall_id=waterfall_id,
            frame_index=frame_index,
            generation=generation,
            reason=reason,
        )

        if self._active is None:
            self._launch(req)
        else:
            # latest-pending-wins: новый запрос заменяет старый pending
            self._pending = req
            self._diagnostics["pending_loads"] = 1 if self._pending else 0

        self.diagnostics.emit(self._diagnostics.copy())

    def cancel_all(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        self._pending = None
        self._diagnostics["pending_loads"] = 0
        self.diagnostics.emit(self._diagnostics.copy())

    def clear_cache(self) -> None:
        self._cache.clear()

    def _launch(self, req: _LoadRequest) -> None:
        self._active = req
        self._diagnostics["active_loads"] = 1
        self._diagnostics["pending_loads"] = 1 if self._pending else 0
        self.diagnostics.emit(self._diagnostics.copy())

        source_path, index, reader = self._context_callback(req.session_id, req.waterfall_id)
        if index is None and reader is None:
            self._finish_active()
            self.error.emit(f"Нет индекса для чтения кадра {req.frame_index}")
            return

        if reader is not None and self._can_read_interactively(reader, req.frame_index):
            # A real-time 1001-point SgramLine is only about 6 KiB and the
            # native read/decode path is comfortably below one UI frame. A
            # QRunnable per displayed frame costs far more than the read itself
            # and capped presentation near 64 Hz. Keep larger rows asynchronous.
            self._diagnostics["interactive_loads"] += 1
            try:
                self._on_result(reader.read_frame(req.frame_index), req)
            except Exception as exc:
                self._on_error(str(exc), req)
            finally:
                self._worker = None
                self._finish_active()
            return

        self._diagnostics["worker_loads"] += 1
        if reader is not None:
            self._worker = TaskWorker(reader.read_frame, req.frame_index)
        else:
            self._worker = TaskWorker(
                read_spectrogram_frame, source_path, index, req.frame_index
            )
        self._worker.signals.result.connect(lambda row, req=req: self._on_result(row, req))
        self._worker.signals.error.connect(lambda msg, tb, req=req: self._on_error(msg, req))
        self._worker.signals.finished.connect(self._on_finished)
        from PySide6.QtCore import QThreadPool

        QThreadPool.globalInstance().start(self._worker)

    def _can_read_interactively(
        self,
        reader: SpectrogramFrameReader,
        frame_index: int,
    ) -> bool:
        if self._interactive_read_limit_bytes <= 0:
            return False
        try:
            payload_size = reader.frame_payload_size(frame_index)
        except (AttributeError, IndexError, TypeError, ValueError):
            return False
        return 0 <= payload_size <= self._interactive_read_limit_bytes

    def _on_result(self, row: SpectrogramRow, req: _LoadRequest) -> None:
        key = (req.session_id, req.waterfall_id, req.frame_index)
        self._cache[key] = row
        if len(self._cache) > self._max_cache:
            self._cache.popitem(last=False)
        self.snapshot_ready.emit(
            FrameSnapshot(
                session_id=req.session_id,
                waterfall_id=req.waterfall_id,
                frame_index=req.frame_index,
                generation=req.generation,
                reason=req.reason,
                row=row,
            )
        )

    def _on_error(self, message: str, req: _LoadRequest) -> None:
        self.error.emit(f"Ошибка чтения кадра {req.frame_index}: {message}")

    def _on_finished(self) -> None:
        self._worker = None
        self._finish_active()

    def _finish_active(self) -> None:
        self._active = None
        self._diagnostics["active_loads"] = 0
        if self._pending is not None:
            pending = self._pending
            self._pending = None
            self._launch(pending)
        else:
            self.diagnostics.emit(self._diagnostics.copy())


class FrameNavigationController(QObject):
    """Преобразует пользовательский ввод в целевой кадр."""

    target_changed = Signal()
    span_event = Signal(object)  # FrameSpanEvent

    def __init__(
        self,
        config: NavigationConfig | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config or NavigationConfig()
        self._frame_count: int = 0
        self.requested_frame: int = 0
        self.displayed_frame: int = 0
        self.scroll_state: ScrollState = ScrollState.IDLE
        self.navigation_reason: NavigationReason = NavigationReason.API
        self._generation: int = 0
        self._angle_accumulator: float = 0.0
        self._pixel_accumulator: float = 0.0
        self._last_direction: int = 0

    def set_frame_count(self, count: int) -> None:
        self._frame_count = max(0, count)
        self.requested_frame = int(
            _clip(self.requested_frame, 0, max(0, self._frame_count - 1))
        )
        self.displayed_frame = int(
            _clip(self.displayed_frame, 0, max(0, self._frame_count - 1))
        )

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def generation(self) -> int:
        return self._generation

    def handle_wheel(
        self,
        angle_delta: int,
        pixel_delta: Any,
        modifiers: Any,
    ) -> None:
        reason = NavigationReason.TOUCHPAD if pixel_delta is not None and (
            pixel_delta.x() or pixel_delta.y()
        ) else NavigationReason.WHEEL

        if reason == NavigationReason.TOUCHPAD:
            self._pixel_accumulator += float(pixel_delta.y())
            steps = _trunc_toward_zero(
                self._pixel_accumulator / self.config.touchpad_threshold
            )
            if steps:
                self._pixel_accumulator -= steps * self.config.touchpad_threshold
        else:
            # Сброс дробного остатка при резкой смене направления.
            if self._angle_accumulator and angle_delta:
                if (self._angle_accumulator > 0) != (angle_delta > 0):
                    self._angle_accumulator = 0.0
            self._angle_accumulator += float(angle_delta)
            steps = _trunc_toward_zero(self._angle_accumulator / 120.0)
            if steps:
                self._angle_accumulator -= steps * 120.0

        if steps == 0:
            return

        effective_step = self._effective_step(modifiers)
        delta_frames = int(steps * effective_step)
        if delta_frames == 0:
            delta_frames = int(math.copysign(1, steps))
        self.request_frame_delta(delta_frames, reason)

    def _effective_step(self, modifiers: Any) -> int:
        from PySide6.QtCore import Qt

        if modifiers is None:
            mod_int = 0
        else:
            mod_int = int(modifiers.value) if hasattr(modifiers, "value") else int(modifiers)
        if mod_int & Qt.KeyboardModifier.AltModifier.value:
            return self.config.alt_step
        if mod_int & Qt.KeyboardModifier.ShiftModifier.value:
            return max(1, self.config.wheel_step) * self.config.shift_multiplier
        return max(1, self.config.wheel_step)

    def request_frame_delta(
        self,
        delta_frames: int,
        reason: NavigationReason,
    ) -> None:
        if self._frame_count == 0:
            return
        new_target = int(
            _clip(
                self.requested_frame + delta_frames,
                0,
                self._frame_count - 1,
            )
        )
        self._set_target(new_target, reason)

    def seek(self, frame: int, reason: NavigationReason) -> None:
        if self._frame_count == 0:
            return
        new_target = int(_clip(frame, 0, self._frame_count - 1))
        self._set_target(new_target, reason)

    def invalidate(self) -> None:
        """Invalidate any in-flight results by bumping generation without moving target."""
        self._generation += 1

    def _set_target(self, new_target: int, reason: NavigationReason) -> None:
        if new_target == self.requested_frame and reason == self.navigation_reason:
            self.scroll_state = ScrollState.ACTIVE
            self.target_changed.emit()
            return

        previous = self.requested_frame
        direction = int(math.copysign(1, new_target - previous)) if new_target != previous else 0
        self.requested_frame = new_target
        self.navigation_reason = reason
        self.scroll_state = ScrollState.ACTIVE
        self._generation += 1
        self._last_direction = direction
        self.span_event.emit(
            FrameSpanEvent(
                previous_target=previous,
                new_target=new_target,
                direction=direction,
                reason=reason,
                generation=self._generation,
            )
        )
        self.target_changed.emit()

    def set_displayed_frame(self, frame: int) -> None:
        self.displayed_frame = int(_clip(frame, 0, max(0, self._frame_count - 1)))
        if self.displayed_frame == self.requested_frame:
            self.scroll_state = ScrollState.SETTLING
        else:
            self.scroll_state = ScrollState.ACTIVE

    def settle(self) -> None:
        if self.displayed_frame == self.requested_frame:
            self.scroll_state = ScrollState.IDLE

    def reset(self, frame: int = 0) -> None:
        self.requested_frame = int(_clip(frame, 0, max(0, self._frame_count - 1)))
        self.displayed_frame = self.requested_frame
        self.scroll_state = ScrollState.IDLE
        self._generation += 1
        self._angle_accumulator = 0.0
        self._pixel_accumulator = 0.0
        self._last_direction = 0


class FramePresentationScheduler(QObject):
    """Latest-target-wins scheduler с частотой UI FPS."""

    apply_snapshot = Signal(object)  # FrameSnapshot
    settled = Signal()

    def __init__(
        self,
        controller: FrameNavigationController,
        loader: FrameLoadCoordinator,
        config: NavigationConfig | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._loader = loader
        self.config = config or NavigationConfig()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._on_settle)
        self._last_applied_generation: int = -1
        self._last_applied_frame: int = -1
        self._sequential_mode: bool = False
        # Timestamp-driven playback advances the logical target much faster
        # than the UI can repaint. A completed read from the same playback
        # run remains useful even when a newer target is already queued.
        self._playback_active: bool = False
        self._playback_generation_floor: int = 0
        self._session_id: str = ""
        self._waterfall_id: str = ""

        self._loader.snapshot_ready.connect(self._on_snapshot_ready)
        self._controller.target_changed.connect(self._on_target_changed)

    def set_active_context(self, session_id: str, waterfall_id: str) -> None:
        self._session_id = session_id
        self._waterfall_id = waterfall_id

    def set_fps(self, fps: int) -> None:
        self.config.fps = max(1, fps)
        if self._timer.isActive():
            self._timer.setInterval(max(1, round(1000.0 / self.config.fps)))

    def set_settle_delay_ms(self, ms: int) -> None:
        self.config.settle_delay_ms = max(0, ms)

    def set_sequential_mode(self, enabled: bool) -> None:
        self._sequential_mode = bool(enabled)

    def set_playback_active(self, enabled: bool) -> None:
        """Enable lag-tolerant snapshot acceptance for timestamp playback."""
        enabled = bool(enabled)
        if enabled and not self._playback_active:
            self._playback_generation_floor = self._controller.generation
            self._last_applied_frame = self._controller.displayed_frame
        self._playback_active = enabled

    def reset_playback_progress(self) -> None:
        """Start a new forward playback epoch (for example after Loop)."""
        self._playback_generation_floor = self._controller.generation
        self._last_applied_frame = -1

    def _on_target_changed(self) -> None:
        if self._controller.navigation_reason != NavigationReason.PLAYBACK:
            # A manual seek supersedes all in-flight playback reads.
            self._playback_generation_floor = self._controller.generation
            self._last_applied_frame = self._controller.displayed_frame

    def schedule(self, immediate: bool = False) -> None:
        if immediate:
            self._tick()
        if not self._timer.isActive():
            self._timer.start(max(1, round(1000.0 / self.config.fps)))

    def stop(self) -> None:
        self._timer.stop()

    def cancel_and_invalidate(self) -> None:
        """Stop presentation, cancel pending frame loads, bump generation so
        any in-flight snapshot is discarded, and disable sequential mode so the
        next seek is a direct jump rather than a frame-by-frame walk. Used by
        Pause/Stop to freeze the UI promptly and to let Stop return to frame 1
        deterministically.
        """
        self._timer.stop()
        self._settle_timer.stop()
        self._loader.cancel_all()
        self._controller.invalidate()
        self._sequential_mode = False
        self._playback_active = False
        self._playback_generation_floor = self._controller.generation

    def _tick(self) -> None:
        target = self._controller.requested_frame
        displayed = self._controller.displayed_frame

        if target == displayed and self._controller.scroll_state != ScrollState.ACTIVE:
            self._enter_settle()
            return

        if self._sequential_mode:
            step = int(math.copysign(1, target - displayed)) if target != displayed else 0
            if step == 0:
                self._enter_settle()
                return
            target = int(_clip(displayed + step, 0, self._controller.frame_count - 1))

        self._restart_settle_timer()
        self._request_frame(target)

    def _request_frame(self, frame: int) -> None:
        self._loader.request(
            session_id=self._session_id,
            waterfall_id=self._waterfall_id,
            frame_index=frame,
            generation=self._controller.generation,
            reason=self._controller.navigation_reason,
        )

    def _on_snapshot_ready(self, snapshot: FrameSnapshot) -> None:
        # latest-target-wins: отбрасываем результаты, не соответствующие актуальному запросу
        if snapshot.generation < self._controller.generation:
            playback_lag = (
                self._playback_active
                and snapshot.reason == NavigationReason.PLAYBACK
                and snapshot.generation >= self._playback_generation_floor
            )
            if not playback_lag:
                return
            # In forward playback the newest completed frame can legitimately
            # lag behind the logical target. Do not render future or backward
            # frames from the same playback epoch.
            if snapshot.frame_index > self._controller.requested_frame:
                return
            if self._last_applied_frame >= 0 and snapshot.frame_index < self._last_applied_frame:
                return
        elif not self._sequential_mode and snapshot.frame_index != self._controller.requested_frame:
            return
        if snapshot.generation == self._last_applied_generation and snapshot.frame_index == self._last_applied_frame:
            return

        self._last_applied_generation = snapshot.generation
        self._last_applied_frame = snapshot.frame_index
        self._controller.set_displayed_frame(snapshot.frame_index)
        self.apply_snapshot.emit(snapshot)

        if self._controller.requested_frame == self._controller.displayed_frame:
            self._enter_settle()

    def _enter_settle(self) -> None:
        if self._controller.scroll_state == ScrollState.SETTLING:
            return
        self._controller.scroll_state = ScrollState.SETTLING
        self._settle_timer.start(self.config.settle_delay_ms)

    def _restart_settle_timer(self) -> None:
        if self._settle_timer.isActive():
            self._settle_timer.stop()

    def _on_settle(self) -> None:
        self._controller.settle()
        self.settled.emit()
        if self._controller.requested_frame == self._controller.displayed_frame:
            self._timer.stop()


class FrameRangeAnalysisBridge(QObject):
    """Заглушка-расширение для Persistence Spectrum / Heatmap.

    Получает FrameSpanEvent и policy, но не влияет на UI repaint.
    """

    analysis_requested = Signal(object, object)  # FrameSpanEvent, FramePolicy

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._policy = FramePolicy.ENDPOINTS_ONLY

    def set_policy(self, policy: FramePolicy) -> None:
        self._policy = policy

    def on_span(self, event: FrameSpanEvent) -> None:
        LOGGER.debug(
            "Frame span: %s -> %s (%s), policy=%s",
            event.previous_target,
            event.new_target,
            event.reason,
            self._policy,
        )
        self.analysis_requested.emit(event, self._policy)
