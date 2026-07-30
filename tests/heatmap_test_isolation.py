"""Shared QSettings isolation and control-reset helpers for heatmap tests.

Import from any heatmap test or harness to get a consistent, zero-pollution
fixture pattern:

    from heatmap_test_isolation import make_temp_settings, patched_qsettings, reset_heatmap_controls

    tmp = tempfile.TemporaryDirectory()
    settings = make_temp_settings(tmp.name)
    with patched_qsettings(settings):
        window = MainWindow()
    reset_heatmap_controls(window)
    # apply test-specific overrides after the reset
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from collections.abc import Generator
from typing import Protocol
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox


class HeatmapControls(Protocol):
    """Heatmap dock controls required by the deterministic reset helper."""

    heatmap_enabled: QCheckBox
    heatmap_range_mode: QComboBox
    heatmap_window_unit: QComboBox
    heatmap_window_frames_spin: QSpinBox
    heatmap_window_seconds_spin: QDoubleSpinBox
    heatmap_follow_playhead: QCheckBox
    heatmap_compute_mode: QComboBox
    heatmap_normalization: QComboBox
    heatmap_power_min: QDoubleSpinBox
    heatmap_power_max: QDoubleSpinBox
    heatmap_power_bins: QComboBox
    heatmap_opacity: QDoubleSpinBox
    heatmap_palette: QComboBox
    heatmap_half_life_spin: QDoubleSpinBox
    heatmap_half_life_unit: QComboBox
    heatmap_color_scale_mode: QComboBox
    heatmap_color_min: QDoubleSpinBox
    heatmap_color_max: QDoubleSpinBox
    heatmap_start_spin: QSpinBox
    heatmap_end_spin: QSpinBox


def make_temp_settings(tmpdir: str | Path) -> QSettings:
    """Per-test QSettings backed by a temp ini – never the developer's real one."""
    return QSettings(str(Path(tmpdir) / "test_settings.ini"), QSettings.Format.IniFormat)


@contextmanager
def patched_qsettings(settings: QSettings) -> Generator[None, None, None]:
    """Patch ``esw_dfl.gui.QSettings`` so ``MainWindow()`` restores from *settings*.

    Must wrap the ``MainWindow()`` constructor call so ``_restore_heatmap_settings``
    reads from the isolated store, not the developer's real registry/ini.
    """
    import esw_dfl.gui as _gui_module

    with patch.object(_gui_module, "QSettings", lambda *args, **kwargs: settings):
        yield


def reset_heatmap_controls(window: HeatmapControls) -> None:
    """Drive every heatmap dock control to its documented default (TZ §2.1).

    Call this immediately after constructing a ``MainWindow`` (inside or outside
    the ``patched_qsettings`` context) and before applying any test-specific
    overrides such as ``window.heatmap_window_frames_spin.setValue(WINDOW)``.

    Default values (all match ``_create_heatmap_dock`` initial state):
        enabled              False
        range_mode           0  (Rolling Exact)
        window_unit          0  (Frames)
        window_frames        500
        window_seconds       10.0
        follow_playhead      True
        compute_mode         0  (Точный / full-range)
        normalization        2  (Log Density)
        power_min/max        -120.0 / 0.0
        power_bins           256
        opacity              0.65
        palette              Viridis
        half_life_spin       1.0
        half_life_unit       s
        color_scale_mode     0  (AUTO_CURRENT)
        color_min/max        0.0 / 1.0
        start_spin/end_spin  1 / 1
    """
    window.heatmap_enabled.setChecked(False)
    window.heatmap_range_mode.setCurrentIndex(0)           # Rolling Exact
    window.heatmap_window_unit.setCurrentIndex(0)          # Frames
    window.heatmap_window_frames_spin.setValue(500)
    window.heatmap_window_seconds_spin.setValue(10.0)
    window.heatmap_follow_playhead.setChecked(True)
    window.heatmap_compute_mode.setCurrentIndex(0)         # Точный
    window.heatmap_normalization.setCurrentIndex(2)        # Log Density
    window.heatmap_power_min.setValue(-120.0)
    window.heatmap_power_max.setValue(0.0)
    window.heatmap_power_bins.setCurrentText("256")
    window.heatmap_opacity.setValue(0.65)
    window.heatmap_palette.setCurrentText("Viridis")
    window.heatmap_half_life_spin.setValue(1.0)
    window.heatmap_half_life_unit.setCurrentText("s")
    window.heatmap_color_scale_mode.setCurrentIndex(0)     # AUTO_CURRENT
    window.heatmap_color_min.setValue(0.0)
    window.heatmap_color_max.setValue(1.0)
    window.heatmap_start_spin.setValue(1)
    window.heatmap_end_spin.setValue(1)


def shutdown_window(window, app=None, timeout_ms: int = 5000) -> None:
    """Fully tear down a ``MainWindow`` created by a test.

    Two Qt/PySide6 behaviors make naive teardowns leak the whole widget tree
    (measured: ~900 widgets per MainWindow survive a plain ``deleteLater``):

    - ``QCoreApplication.sendPostedEvents()`` without an explicit event type
      does NOT deliver ``DeferredDelete`` events, so ``deleteLater()`` alone
      leaves the C++ object alive;
    - pyqtgraph's ``ViewBoxMenu`` (created by the default channel-power
      ``PlotWidget``) is an orphan top-level tree that ``closeEvent`` can
      only release through ``deleteLater()`` — which needs the same flush.

    This helper cancels background work, closes the window (``closeEvent``
    stops timers, cancels workers and shuts down the heatmap controller),
    flushes deferred deletions and drains the global thread pool. Use it in
    every GUI test teardown instead of ad-hoc ``close()``/``deleteLater()``
    sequences; leftover widgets make every later ``MainWindow`` (and Qt
    stylesheet/font propagation) progressively slower.
    """
    from PySide6.QtCore import QEvent, QThreadPool
    from PySide6.QtWidgets import QApplication

    application = app or QApplication.instance()
    window._frame_loader.cancel_all()
    window._frame_scheduler.stop()
    # Drain workers before deleting anything: late queued signals must not
    # arrive at already-destroyed sources.
    QThreadPool.globalInstance().waitForDone(timeout_ms)
    window.close()
    window.deleteLater()
    application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
    application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
