"""Strict manual GUI acceptance smoke-run for Heatmap Spectrum (ТЗ §24, P6).

Standalone script (NOT collected by pytest, like manual_gui_acceptance.py):
real GUI via QTest on external read-only DFL files. Input paths and the
optional output directory are supplied through environment variables.

STRICT contract (HMP-PERSIST-006): the absence of an exception is NOT a
success. Every step must assert concrete conditions through ``check()``; any
false condition marks the step failed and the script exits 1. Observations
record the asserted condition labels per step plus the §10.2 metrics.

Scenario on an externally supplied large real-time recording: open,
Rolling Exact (incremental updates), z-order, opacity/palette, rolling seek,
playback with density-hash assertions and Pause drain, Full Recording fixed
during playback, Exponential Decay half-life, full-file compute + Cancel,
session switch mid-compute, session removal, close with worker assertions.

Run from the repository root:

    $env:ESW_DFL_MANUAL_RT = "<path-to-real-time-recording.dfl>"
    $env:ESW_DFL_MANUAL_SECOND = "<path-to-second-recording.dfl>"
    py tests\\manual_heatmap_acceptance.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QSettings, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl.gui import MainWindow
from esw_dfl.heatmap import density_hash
from heatmap_test_isolation import patched_qsettings, reset_heatmap_controls


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(
    os.environ.get(
        "ESW_DFL_MANUAL_OUTPUT",
        Path(tempfile.gettempdir()) / "sdr_native_monitoring_manual",
    )
)
RT_DFL = Path(
    os.environ.get("ESW_DFL_MANUAL_RT", "__missing_ESW_DFL_MANUAL_RT__")
).expanduser()
SECOND_DFL = Path(
    os.environ.get("ESW_DFL_MANUAL_SECOND", "__missing_ESW_DFL_MANUAL_SECOND__")
).expanduser()


# --- strict evaluation helpers (also exercised by the contract test) -----------
def evaluate_playback_results(
    *,
    density_changed: bool,
    applied_target: int | None,
    desired_target: int,
    lag_frames: float,
    phase_name: str,
) -> list[str]:
    """Playback step conditions; a non-empty result means the step FAILED."""
    failures: list[str] = []
    if not density_changed:
        failures.append("density_hash_unchanged")
    if applied_target != desired_target:
        failures.append("applied_target_mismatch")
    if lag_frames != 0:
        failures.append("lag_not_zero")
    if phase_name != "CURRENT":
        failures.append("phase_not_current")
    return failures


def evaluate_close(workers_empty: bool, thread_pool_idle: bool) -> list[str]:
    """Close step conditions; a non-empty result means the step FAILED."""
    failures: list[str] = []
    if not workers_empty:
        failures.append("workers_not_empty")
    if not thread_pool_idle:
        failures.append("thread_pool_not_idle")
    return failures


class StepRecorder:
    """Per-step observations with mandatory asserted conditions."""

    def __init__(self, observations: list[dict], failed_steps: list[str]) -> None:
        self.observations = observations
        self.failed_steps = failed_steps
        self._checks: list[str] = []

    def check(self, condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(label)
        self._checks.append(label)

    def record(self, step: str, action: str, ok: bool = True, **state: object) -> None:
        if not ok:
            self.failed_steps.append(step)
        checks = self._checks
        self._checks = []
        self.observations.append(
            {"step": step, "action": action, "ok": ok, "asserted": True, "checks": checks, **state}
        )
        print(f"[{'OK' if ok else 'FAIL'}] {step}: {action} checks={checks} {state}", flush=True)


def run_step(
    recorder: StepRecorder,
    step: str,
    action: str,
    body,
    *,
    abort_on_fail: bool = False,
) -> bool:
    """Run one acceptance step; a false condition fails the step, never passes silently."""
    try:
        state = body() or {}
        recorder.record(step, action, True, **state)
        return True
    except Exception as exc:  # noqa: BLE001 — recorded as a failed step, not a crash
        recorder.record(step, action, False, error=repr(exc))
        if abort_on_fail:
            raise
        return False


def wait_until(app: QApplication, predicate, timeout_s: float, label: str) -> float:
    """Pump Qt events while yielding the GIL to Python QRunnable workers."""
    started = time.perf_counter()
    while time.perf_counter() - started < timeout_s:
        app.processEvents()
        if predicate():
            return time.perf_counter() - started
        time.sleep(0.025)
    raise TimeoutError(label)


def pump_events_for(app: QApplication, duration_s: float, interval_s: float = 0.025) -> int:
    """Keep the GUI responsive for a duration while yielding to Python workers."""
    started = time.perf_counter()
    iterations = 0
    while time.perf_counter() - started < duration_s:
        app.processEvents()
        iterations += 1
        time.sleep(interval_s)
    app.processEvents()
    return iterations


def click(app: QApplication, widget) -> None:
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    app.processEvents()


def button(window: MainWindow, text: str) -> QPushButton:
    return next(item for item in window.findChildren(QPushButton) if item.text() == text)


def screenshot(window: MainWindow, name: str) -> str:
    path = OUTPUT / name
    window.grab().save(str(path))
    return path.name


def heatmap_applied_fresh(window: MainWindow) -> bool:
    controller = window._heatmap_controller
    snapshot = controller.applied_snapshot
    return (
        controller.active_ticket is None
        and controller.pending_ticket is None
        and snapshot is not None
        and snapshot.generation == controller.generation
        and window._heatmap_applied_snapshot is snapshot
    )


def snapshot_real_heatmap_settings() -> dict[str, object]:
    """Read-only snapshot of the developer's real ``heatmap`` QSettings group.

    Never writes: this is used to prove the harness (running on isolated
    settings) does not touch the real registry/ini. Returns a plain dict so the
    before/after comparison is a stable value, not a live QSettings handle.
    """
    real = QSettings("RohdeSchwarzTools", "R&S DFL parcer")
    real.beginGroup("heatmap")
    snapshot = {key: real.value(key) for key in real.allKeys()}
    real.endGroup()
    return snapshot


def main() -> int:
    for env_name, path in (
        ("ESW_DFL_MANUAL_RT", RT_DFL),
        ("ESW_DFL_MANUAL_SECOND", SECOND_DFL),
    ):
        if not path.is_file():
            raise RuntimeError(f"Set {env_name} to an external read-only DFL path")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])

    # --- P1.2 / P2.1: isolated QSettings for the entire harness run -----------
    # Snapshot the real heatmap group BEFORE construction so we can assert it
    # is unchanged AFTER the run (settings_isolation_ok observation).
    real_heatmap_before = snapshot_real_heatmap_settings()

    _isolated_dir = tempfile.TemporaryDirectory(prefix="heatmap_acceptance_")
    from PySide6.QtCore import QSettings as _QSettings  # local alias
    _acceptance_settings = _QSettings(
        str(Path(_isolated_dir.name) / "acceptance_settings.ini"),
        _QSettings.Format.IniFormat,
    )
    with patched_qsettings(_acceptance_settings):
        window = MainWindow()
    # Replace the instance so all persist-on-change calls in this run write to
    # the isolated INI, not to the developer's real registry.
    window.settings = _acceptance_settings

    # Deterministic start: full reset to documented defaults, then any overrides.
    reset_heatmap_controls(window)
    window.no_skip_check.setChecked(False)
    window._frame_nav.config.sequential_mode = False
    window._frame_scheduler.set_sequential_mode(False)

    window.showMaximized()
    app.processEvents()
    observations: list[dict[str, object]] = []
    failed_steps: list[str] = []
    recorder = StepRecorder(observations, failed_steps)
    controller = window._heatmap_controller

    try:
        # -- 1: open the real-time DFL -----------------------------------------
        def step_open() -> dict[str, object]:
            window.load_file(RT_DFL)
            try:
                wait_until(
                    app,
                    lambda: (
                        window.active_session() is not None
                        and window.active_session().source_path == RT_DFL.resolve()
                    ),
                    420,
                    "RT DFL parse",
                )
            except TimeoutError as exc:
                active = window.active_session()
                raise TimeoutError(
                    "RT DFL parse; "
                    f"sessions={len(window.repository.all())}; "
                    f"active_path={active.source_path if active is not None else None}; "
                    f"workers={len(window._workers)}; "
                    f"threads={window.thread_pool.activeThreadCount()}; "
                    f"status_file={window.status_file.text()!r}; "
                    f"status_bar={window.statusBar().currentMessage()!r}"
                ) from exc
            session = window.active_session()
            assert session is not None
            wait_until(
                app,
                lambda: window._active_spectrogram_index(session) is not None
                and window._active_waterfall(session) is not None
                and window._active_waterfall(session).values is not None
                and not window._workers,
                300,
                "RT waterfall preview",
            )
            index = window._active_spectrogram_index(session)
            assert index is not None
            recorder.check(index.frame_count == 100_000, "frame_count_100k")
            recorder.check(
                window._active_waterfall(session).point_count == 1001, "point_count_1001"
            )
            return {
                "frames": index.frame_count,
                "points": window._active_waterfall(session).point_count,
                "png": screenshot(window, "h01_rt_loaded.png"),
            }

        run_step(recorder, "1", "open RT DFL (100 000 x 1001)", step_open, abort_on_fail=True)
        session = window.active_session()
        assert session is not None
        session_a_id = session.session_id

        # -- 2-4: Rolling Exact last 500 frames, density layer appears ----------
        def step_enable() -> dict[str, object]:
            window.time_slider.setValue(min(5000, window.time_slider.maximum()))
            wait_until(
                app,
                lambda: session.current_frame == min(5000, window.time_slider.maximum()),
                30,
                "seek to frame 5001",
            )
            window.heatmap_enabled.setChecked(True)
            elapsed = wait_until(app, lambda: heatmap_applied_fresh(window), 180, "rolling last 500")
            snapshot = controller.applied_snapshot
            assert snapshot is not None
            recorder.check(window.spectrum_renderer.heatmap_visible, "layer_visible")
            recorder.check(snapshot.exact, "snapshot_exact")
            recorder.check(snapshot.processed_frames == 500, "processed_500")
            recorder.check(
                int(snapshot.density.sum()) == 500 * 1001, "density_sum_500k"
            )
            return {
                "seconds": elapsed,
                "processed": snapshot.processed_frames,
                "density_sum": int(snapshot.density.sum()),
                "exact": snapshot.exact,
                "status": window.heatmap_status.text(),
                "png": screenshot(window, "h02_heatmap_last500.png"),
            }

        ok = run_step(recorder, "2-4", "Rolling Exact last 500 frames", step_enable)
        if not ok:
            raise RuntimeError("heatmap layer never appeared; aborting dependent steps")

        # -- 5: live trace above the layer --------------------------------------
        def step_zorder() -> dict[str, object]:
            renderer = window.spectrum_renderer
            heatmap_z = renderer.heatmap_image.zValue()
            visible = {tid: item for tid, item in renderer.items.items() if item.isVisible()}
            recorder.check(bool(visible), "traces_present")
            recorder.check(all(item.zValue() > heatmap_z for item in visible.values()), "z_order")
            return {"heatmap_z": heatmap_z, "visible_traces": sorted(visible)}

        run_step(recorder, "5", "live trace visible above heatmap", step_zorder)

        # -- 6-7: opacity / palette without recompute ---------------------------
        def step_visual() -> dict[str, object]:
            generation_before = controller.generation
            reads_before = int(window.heatmap_diagnostics()["heatmap_frames_decoded"])
            window.heatmap_opacity.setValue(0.35)
            window.heatmap_palette.setCurrentText("Inferno")
            app.processEvents()
            recorder.check(controller.generation == generation_before, "no_generation_bump")
            recorder.check(
                int(window.heatmap_diagnostics()["heatmap_frames_decoded"]) == reads_before,
                "no_rereads",
            )
            recorder.check(
                window.spectrum_renderer.heatmap_image.opacity() == 0.35, "opacity_applied"
            )
            return {
                "opacity": window.spectrum_renderer.heatmap_image.opacity(),
                "palette": window.heatmap_palette.currentText(),
                "png": screenshot(window, "h03_palette_inferno.png"),
            }

        run_step(recorder, "6-7", "opacity/palette without recompute or reread", step_visual)

        # -- 8-9: seek triggers a rolling update --------------------------------
        def step_rolling() -> dict[str, object]:
            generation_before = controller.generation
            target = min(9000, window.time_slider.maximum())
            window.time_slider.setValue(target)
            elapsed = wait_until(
                app,
                lambda: heatmap_applied_fresh(window)
                and controller.applied_snapshot.target_frame == target,
                180,
                "rolling heatmap update",
            )
            snapshot = controller.applied_snapshot
            assert snapshot is not None
            recorder.check(controller.generation > generation_before, "new_generation")
            recorder.check(snapshot.frame_end == target, "range_follows_frame")
            recorder.check(snapshot.exact, "rolling_exact")
            return {
                "seconds": elapsed,
                "frame_range": [snapshot.frame_start, snapshot.frame_end],
                "status": window.heatmap_status.text(),
                "png": screenshot(window, "h04_rolling_updated.png"),
            }

        run_step(recorder, "8-9", "seek to another frame, rolling update", step_rolling)

        # -- 10-11: playback ~3 s with strict density/Pause assertions -----------
        def step_playback() -> dict[str, object]:
            snapshot_before = controller.applied_snapshot
            assert snapshot_before is not None
            hash_before = density_hash(snapshot_before.density)
            frame_before = session.current_frame
            click(app, button(window, "▶"))
            started = time.perf_counter()
            iterations = 0
            max_tickets = 0
            while time.perf_counter() - started < 3.0:
                app.processEvents()
                iterations += 1
                max_tickets = max(
                    max_tickets,
                    int(controller.active_ticket is not None)
                    + int(controller.pending_ticket is not None),
                )
                time.sleep(0.025)
            wall = time.perf_counter() - started
            click(app, button(window, "Ⅱ"))
            # Pause drain: the controller must catch up to the latest target.
            desired = window._frame_nav.requested_frame
            wait_until(
                app,
                lambda: controller.applied_snapshot is not None
                and controller.applied_snapshot.target_frame == desired
                and controller.active_ticket is None
                and controller.pending_ticket is None,
                180,
                "pause drain to latest target",
            )
            snapshot_after = controller.applied_snapshot
            assert snapshot_after is not None
            diag = window.heatmap_diagnostics()
            failures = evaluate_playback_results(
                density_changed=density_hash(snapshot_after.density) != hash_before,
                applied_target=snapshot_after.target_frame,
                desired_target=desired,
                lag_frames=float(diag["heatmap_lag_frames"]),
                phase_name=controller.phase.name,
            )
            for failure in failures:
                recorder.check(False, failure)
            recorder.check(wall < 8.0 and iterations >= 8, "ui_responsive")
            recorder.check(max_tickets <= 2, "bounded_active_plus_pending")
            return {
                "frame_before": frame_before + 1,
                "frame_after": session.current_frame + 1,
                "desired": desired + 1,
                "loop_wall_s": round(wall, 2),
                "lag_frames": diag["heatmap_lag_frames"],
                "max_active_plus_pending": max_tickets,
                "png": screenshot(window, "h05_playback_rolling.png"),
            }

        run_step(recorder, "10-11", "playback 3 s: density changes, Pause drains to lag 0", step_playback)

        # -- 12: Full Recording stays fixed during playback -----------------------
        def step_full_fixed() -> dict[str, object]:
            window.heatmap_range_mode.setCurrentIndex(3)  # Full Recording
            window.heatmap_compute_mode.setCurrentIndex(1)  # preview for speed
            click(app, window.heatmap_recalculate_button)
            wait_until(
                app,
                lambda: heatmap_applied_fresh(window)
                and controller.applied_snapshot is not None
                and controller.applied_snapshot.processed_frames > 0,
                600,
                "full recording preview compute",
            )
            fixed = controller.applied_snapshot
            assert fixed is not None
            hash_before = density_hash(fixed.density)
            range_before = (fixed.frame_start, fixed.frame_end)
            recorder.check(
                "playback does not change this layer" in window.heatmap_status.text(),
                "full_status_fixed_before_playback",
            )
            click(app, button(window, "▶"))
            pump_events_for(app, 1.5)
            click(app, button(window, "Ⅱ"))
            wait_until(
                app,
                lambda: "playback does not change this layer" in window.heatmap_status.text(),
                10,
                "Full Recording fixed status after playback",
            )
            fixed_after = controller.applied_snapshot
            assert fixed_after is not None
            recorder.check(fixed_after is fixed, "full_snapshot_unchanged")
            recorder.check(
                density_hash(fixed_after.density) == hash_before, "full_density_fixed"
            )
            recorder.check(
                (fixed_after.frame_start, fixed_after.frame_end) == range_before,
                "full_range_fixed",
            )
            recorder.check(
                "playback does not change this layer" in window.heatmap_status.text(),
                "full_status_fixed",
            )
            return {
                "processed": fixed_after.processed_frames,
                "exact": fixed_after.exact,
                "status": window.heatmap_status.text(),
                "png": screenshot(window, "h06_full_fixed.png"),
            }

        run_step(recorder, "12", "Full Recording · Fixed during playback", step_full_fixed)

        # -- 13: Exponential Decay half-life --------------------------------------
        def step_decay() -> dict[str, object]:
            window.heatmap_range_mode.setCurrentIndex(1)  # Exponential Decay
            window.heatmap_half_life_spin.setValue(2.0)
            window.heatmap_half_life_unit.setCurrentText("s")
            window.heatmap_enabled.setChecked(True)
            wait_until(app, lambda: heatmap_applied_fresh(window), 300, "decay compute")
            snapshot = controller.applied_snapshot
            assert snapshot is not None
            recorder.check(snapshot.approximate, "decay_approximate")
            recorder.check(not snapshot.exact, "decay_not_exact")
            recorder.check(snapshot.half_life_seconds == 2.0, "half_life_2s")
            recorder.check("half-life" in window.heatmap_status.text(), "decay_status")
            return {
                "half_life": snapshot.half_life_seconds,
                "epsilon": snapshot.decay_cutoff_epsilon,
                "status": window.heatmap_status.text(),
                "png": screenshot(window, "h07_decay.png"),
            }

        run_step(recorder, "13", "Exponential Decay half-life 2 s (approximate)", step_decay)

        # -- 14: full-file exact compute progress + Cancel ------------------------
        def step_full_cancel() -> dict[str, object]:
            window.heatmap_range_mode.setCurrentIndex(3)  # Full Recording
            window.heatmap_compute_mode.setCurrentIndex(0)  # exact
            cancel_before = int(window.heatmap_diagnostics()["heatmap_cancel_count"])
            frame_total = window.time_slider.maximum() + 1
            click(app, window.heatmap_recalculate_button)
            wait_until(app, lambda: controller.active_ticket is not None, 30, "full compute start")
            wait_until(
                app,
                lambda: int(window.heatmap_diagnostics()["heatmap_total_frames"]) == frame_total,
                240,
                "full compute progress",
            )
            progress = {
                "status": window.heatmap_status.text(),
                "processed": int(window.heatmap_diagnostics()["heatmap_processed_frames"]),
                "total": int(window.heatmap_diagnostics()["heatmap_total_frames"]),
            }
            png = screenshot(window, "h08_full_exact_progress.png")
            click(app, window.heatmap_cancel_button)
            wait_until(app, lambda: controller.active_ticket is None, 180, "cancel settle")
            cancel_after = int(window.heatmap_diagnostics()["heatmap_cancel_count"])
            recorder.check(cancel_after > cancel_before, "cancel_count_grew")
            recorder.check("Отменено" in window.heatmap_status.text(), "cancel_status_settled")
            return {
                **progress,
                "png": png,
                "final_status": window.heatmap_status.text(),
            }

        run_step(recorder, "14", "full-file exact: progress visible, Cancel cancels", step_full_cancel)

        # -- 15: switch session mid-compute, then remove it -----------------------
        def step_second_session() -> dict[str, object]:
            window.load_file(SECOND_DFL)
            wait_until(
                app,
                lambda: (
                    window.active_session() is not None
                    and window.active_session().source_path == SECOND_DFL.resolve()
                ),
                300,
                "second DFL parse",
            )
            session_b = window.active_session()
            assert session_b is not None
            wait_until(
                app,
                lambda: window._active_spectrogram_index(session_b) is not None and not window._workers,
                300,
                "second DFL preview",
            )
            return {"session_b": session_b.session_id, "sessions": len(window.repository.all())}

        run_step(recorder, "15a", "open second DFL (60 000 frames)", step_second_session, abort_on_fail=True)
        session_b_id = window.active_session().session_id

        def step_switch_mid_compute() -> dict[str, object]:
            window.set_active_session(session_a_id)
            app.processEvents()
            window.heatmap_range_mode.setCurrentIndex(3)
            window.heatmap_compute_mode.setCurrentIndex(0)
            cancel_before = int(window.heatmap_diagnostics()["heatmap_cancel_count"])
            click(app, window.heatmap_recalculate_button)
            wait_until(app, lambda: controller.active_ticket is not None, 30, "restart full on A")
            window.set_active_session(session_b_id)
            app.processEvents()
            wait_until(app, lambda: controller.active_ticket is None, 300, "mid-compute switch settle")
            recorder.check(
                int(window.heatmap_diagnostics()["heatmap_cancel_count"]) > cancel_before,
                "cancelled_on_switch",
            )
            recorder.check(
                not window.spectrum_renderer.heatmap_visible, "foreign_overlay_hidden"
            )
            recorder.check(controller.applied_snapshot is None, "applied_cleared")
            return {"status": window.heatmap_status.text()}

        run_step(recorder, "15b", "switch session during computation", step_switch_mid_compute)

        def step_remove_session() -> dict[str, object]:
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                window._remove_session(session_b_id)
            app.processEvents()
            remaining = [item.session_id for item in window.repository.all()]
            recorder.check(session_b_id not in remaining, "session_removed")
            recorder.check(controller.pending_ticket is None, "pending_dropped")
            recorder.check(
                window._heatmap_applied_key is None
                or window._heatmap_applied_key[0] != session_b_id,
                "no_applied_of_removed",
            )
            return {"remaining_sessions": remaining}

        run_step(recorder, "15c", "remove session frees heatmap resources", step_remove_session)

        # -- 16-17: close, no active workers ---------------------------------------
        def step_close() -> dict[str, object]:
            metrics = dict(window.heatmap_diagnostics())
            window.close()
            app.processEvents()
            idle = QThreadPool.globalInstance().waitForDone(10_000)
            failures = evaluate_close(not window._workers, idle)
            for failure in failures:
                recorder.check(False, failure)
            return {"metrics": metrics}

        run_step(recorder, "16-17", "close application, no active workers", step_close, abort_on_fail=True)

    except Exception as exc:  # noqa: BLE001 — top-level guard: record, never crash silently
        recorder.record("abort", "aborted by prerequisite failure", False, error=repr(exc))
        traceback.print_exc()
        try:
            window.close()
            app.processEvents()
        except Exception:  # noqa: BLE001
            pass

    # --- P1.2 п.3: assert the real QSettings heatmap group is untouched -------
    # Compare the before/after read-only snapshots; the run used an isolated
    # INI, so the real group must be byte-identical (isolation held).
    def step_isolation() -> dict[str, object]:
        real_heatmap_after = snapshot_real_heatmap_settings()
        recorder.check(
            real_heatmap_after == real_heatmap_before,
            "real_qsettings_unchanged",
        )
        recorder.check(not real_heatmap_after, "real_qsettings_heatmap_empty")
        return {
            "settings_isolation_ok": True,
            "real_heatmap_keys_before": sorted(real_heatmap_before),
            "real_heatmap_keys_after": sorted(real_heatmap_after),
        }

    run_step(recorder, "iso", "settings_isolation_ok", step_isolation)

    _isolated_dir.cleanup()

    (OUTPUT / "observations_heatmap.json").write_text(
        json.dumps(observations, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(observations, ensure_ascii=False, indent=2, default=str), flush=True)
    print(f"steps failed: {failed_steps or 'none'}", flush=True)
    return 1 if failed_steps else 0


if __name__ == "__main__":
    raise SystemExit(main())
