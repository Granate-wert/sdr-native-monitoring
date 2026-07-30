from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.gui import MainWindow
from esw_dfl.time_gated_power import PowerSemantics


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(os.environ.get("ESW_DFL_MANUAL_OUTPUT", Path(tempfile.gettempdir()) / "sdr_native_monitoring_manual"))


def _external_path(env_name: str) -> Path:
    value = os.environ.get(env_name)
    if not value:
        raise RuntimeError(f"Set {env_name} to an external read-only DFL path")
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def wait_until(app: QApplication, predicate, timeout_s: float, label: str) -> float:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout_s:
        app.processEvents()
        if predicate():
            return time.perf_counter() - started
        QTest.qWait(25)
    raise TimeoutError(label)


def click(app: QApplication, widget) -> None:
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
    app.processEvents()


def button(window: MainWindow, text: str) -> QPushButton:
    return next(item for item in window.findChildren(QPushButton) if item.text() == text)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.showMaximized()
    app.processEvents()
    observations: list[dict[str, object]] = []

    small = _external_path("ESW_DFL_MANUAL_SWEPT")
    window.load_file(small)
    elapsed = wait_until(app, lambda: bool(window.repository.all()) and not window._workers, 30, "small DFL load")
    observations.append({"action": "open swept DFL", "result": "loaded", "seconds": elapsed})
    print(f"swept loaded in {elapsed:.3f}s", flush=True)
    window.grab().save(str(OUTPUT / "01_swept_loaded.png"))

    single_index = window.power_measurement_mode.findData("Single Channel Power")
    window.power_measurement_mode.setCurrentIndex(single_index)
    semantics_index = window.cp_semantics.findData(PowerSemantics.UNKNOWN)
    window.cp_semantics.setCurrentIndex(semantics_index)
    click(app, window.cp_recalculate_button)
    elapsed = wait_until(app, lambda: "завершён" in window.cp_recalc_status.text(), 15, "single CP")
    observations.append({
        "action": "Single Channel Power on swept Max Hold",
        "result": window.cp_recalc_status.text(),
        "quality": window.power_quality_label.text(),
        "warnings": window.power_warnings_label.text(),
        "rows": window.cp_result_table.rowCount(),
        "seconds": elapsed,
    })

    obw_index = window.power_measurement_mode.findData("Occupied Bandwidth")
    window.power_measurement_mode.setCurrentIndex(obw_index)
    click(app, window.cp_recalculate_button)
    wait_until(app, lambda: window.cp_recalc_status.text().startswith("Occupied Bandwidth:"), 15, "OBW")
    observations.append({
        "action": "OBW99 swept",
        "result": window.cp_recalc_status.text(),
        "quality": window.power_quality_label.text(),
        "rows": window.cp_result_table.rowCount(),
    })
    window.grab().save(str(OUTPUT / "02_swept_power_results.png"))

    large = _external_path("ESW_DFL_MANUAL_RT")
    window.load_file(large)
    elapsed = wait_until(
        app,
        lambda: (
            window.active_session() is not None
            and window.active_session().source_path == large.resolve()
        ),
        420,
        "RT DFL parse",
    )
    observations.append({"action": "open RT DFL", "result": "parsed", "seconds": elapsed})
    print(f"RT parsed in {elapsed:.3f}s; sessions={len(window.repository.all())}", flush=True)
    elapsed = wait_until(
        app,
        lambda: (
            window.active_session() is not None
            and window.active_session().source_path == large.resolve()
            and window._active_waterfall(window.active_session()) is not None
            and window._active_waterfall(window.active_session()).values is not None
            and not window._workers
        ),
        300,
        "RT waterfall preview",
    )
    session = window.active_session()
    waterfall = window._active_waterfall(session)
    observations.append({
        "action": "stream RT waterfall preview",
        "result": "ready",
        "seconds": elapsed,
        "frames": waterfall.line_count,
        "preview_rows": int(waterfall.values.shape[0]),
    })

    before = session.current_frame
    window.no_skip_check.setChecked(True)
    click(app, button(window, "▶"))
    QTest.qWait(700)
    app.processEvents()
    click(app, button(window, "Ⅱ"))
    playback_target = window.time_slider.value()
    playback_key = (session.session_id, waterfall.waterfall_id, playback_target)
    wait_until(
        app,
        lambda: session.current_frame == playback_target and playback_key in window._frame_loader._cache,
        20,
        "playback exact commit",
    )
    after = session.current_frame
    observations.append({
        "action": "Play then Pause waterfall",
        "frame_before": before + 1,
        "frame_after": after + 1,
        "slider": window.time_slider.value() + 1,
        "spin": window.frame_spin.value(),
        "cursor_label": window.waterfall_renderer.time_cursor.label.format,
        "changed": after != before,
    })

    target = min(4991, window.time_slider.maximum())
    window.time_slider.setValue(target)
    frame_key = (session.session_id, waterfall.waterfall_id, target)
    wait_until(
        app,
        lambda: session.current_frame == target and frame_key in window._frame_loader._cache,
        20,
        "exact frame",
    )
    displayed = window.spectrum_renderer.trace_data(session.active_trace_id)
    observations.append({
        "action": "seek exact frame",
        "requested": target + 1,
        "session_frame": session.current_frame + 1,
        "spin": window.frame_spin.value(),
        "cursor_label": window.waterfall_renderer.time_cursor.label.format,
        "spectrum_points": int(displayed[1].size) if displayed is not None else 0,
    })

    window.power_measurement_mode.setCurrentIndex(
        window.power_measurement_mode.findData("Time-Gated Channel Power")
    )
    window.cp_time_mode.setCurrentIndex(0)
    window.cp_semantics.setCurrentIndex(window.cp_semantics.findData(PowerSemantics.UNKNOWN))
    click(app, window.cp_recalculate_button)
    elapsed = wait_until(app, lambda: window._channel_power_worker is None and window.cp_result_table.rowCount() > 0, 60, "current-frame time gated")
    observations.append({
        "action": "Time-Gated current frame",
        "result": window.cp_recalc_status.text(),
        "rows": window.cp_result_table.rowCount(),
        "current_measurement": window.current_frame_measurement.text(),
        "seconds": elapsed,
    })
    window.grab().save(str(OUTPUT / "03_rt_playback_and_power.png"))

    (OUTPUT / "observations.json").write_text(
        json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    window.close()
    app.processEvents()
    print(json.dumps(observations, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
