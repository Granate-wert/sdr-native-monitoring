"""One-process offscreen logical-DPI probe for the admitted V2 shell.

This helper is intentionally not a test module.  Each subprocess receives one
Qt scale before QApplication is created, which avoids mixing multiple logical
DPI values in one Qt process.  It is synthetic geometry evidence only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, cast


_PROJECT_ROOT = Path(__file__).parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_WORKSPACE_IDS = ("home", "live", "sweep", "calibration", "diagnostics", "replay", "tinysa")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", required=True, choices=("1", "1.5", "2", "3"))
    parser.add_argument("--width", required=True, type=int, choices=(1280, 1366))
    parser.add_argument("--height", required=True, type=int, choices=(720, 768))
    return parser.parse_args()


def main() -> int:
    """Build one inert V2 shell and report its visible semantic controls."""

    arguments = _arguments()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["QT_SCALE_FACTOR"] = arguments.scale

    from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
    from PySide6.QtWidgets import QApplication, QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QTableWidget, QWidget

    from sdr_monitor.ui.v2.product_live import compose_v2_live_product
    from sdr_monitor.ui.v2.shell import AppShellV2
    from sdr_monitor.ui.v2.view_models.replay_view_model import ReplayPresenterFactory
    from sdr_monitor.ui.v2.view_models.tinysa_view_model import (
        TinySaAnalyzerBindingFactory,
        TinySaSourceActivationPresenterFactory,
    )
    from tests.ui_v2.test_live_product_composition import (
        FakeCalibrationPresenter,
        FakeDiagnosticsPresenter,
        FakePresenter,
        FakeSweepPresenter,
    )

    app = QApplication.instance() or QApplication([])
    live = FakePresenter()
    sweep = FakeSweepPresenter()
    calibration = FakeCalibrationPresenter()
    diagnostics = FakeDiagnosticsPresenter()
    factory_calls = {"diagnostics": 0, "tinysa": 0, "replay": 0}

    def diagnostics_factory() -> FakeDiagnosticsPresenter:
        factory_calls["diagnostics"] += 1
        return diagnostics

    def tinysa_activation_factory() -> object:
        factory_calls["tinysa"] += 1
        raise AssertionError("logical-DPI navigation must not create tinySA presenter")

    def tinysa_analyzer_factory(_composed: object) -> object:
        raise AssertionError("logical-DPI navigation must not compose tinySA analyzer")

    def replay_factory() -> object:
        factory_calls["replay"] += 1
        raise AssertionError("logical-DPI navigation must not create Replay presenter")

    composition = compose_v2_live_product(
        live,
        sweep_presenter=sweep,
        calibration_presenter=calibration,
        diagnostics_presenter_factory=diagnostics_factory,
        replay_presenter_factory=cast(ReplayPresenterFactory, replay_factory),
        tinysa_activation_presenter_factory=cast(TinySaSourceActivationPresenterFactory, tinysa_activation_factory),
        tinysa_analyzer_binding_factory=cast(TinySaAnalyzerBindingFactory, tinysa_analyzer_factory),
        now_ns=lambda: 1,
    )

    with tempfile.TemporaryDirectory(prefix="ui2-11i-logical-dpi-") as temporary_directory:
        shell = AppShellV2(
            context=composition.context,
            settings=QSettings(temporary_directory + "/ui2.ini", QSettings.Format.IniFormat),
        )
        try:
            shell.resize(arguments.width, arguments.height)
            shell.show()
            _process_qt_events(app, QCoreApplication, QEvent)
            pages = []
            for workspace_id in _WORKSPACE_IDS:
                shell.select_workspace(workspace_id)
                _process_qt_events(app, QCoreApplication, QEvent)
                page = shell._workspace_pages[workspace_id]
                target = _primary_target(page, workspace_id)
                centre = target.mapTo(shell, target.rect().center())
                pages.append(
                    {
                        "workspace": workspace_id,
                        "primary_visible": target.isVisible(),
                        "primary_enabled": target.isEnabled(),
                        "primary_named": bool(target.accessibleName().strip() or getattr(target, "text", lambda: "")().strip()),
                        "primary_within_shell": shell.contentsRect().contains(centre),
                        "page_width": page.width(),
                        "page_height": page.height(),
                        "page_minimum_width": page.minimumSizeHint().width(),
                        "page_minimum_height": page.minimumSizeHint().height(),
                        "page_minimum_fits": (
                            page.minimumSizeHint().width() <= page.width()
                            and page.minimumSizeHint().height() <= page.height()
                        ),
                        "missing_semantic_names": _missing_semantic_names(page, Qt, QWidget, QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QTableWidget),
                    }
                )
            pixmap = shell.grab()
            if pixmap.isNull():
                raise RuntimeError("logical-DPI offscreen shell render returned a null pixmap")
            if factory_calls != {"diagnostics": 0, "tinysa": 0, "replay": 0}:
                raise RuntimeError("logical-DPI navigation constructed a deferred presenter")
            shell.close()
            _process_qt_events(app, QCoreApplication, QEvent)
            if (live.shutdown_calls, sweep.shutdown_calls, calibration.shutdown_calls, diagnostics.shutdown_calls) != (1, 1, 1, 0):
                raise RuntimeError("logical-DPI shell did not shut down owners exactly once")
            print(
                json.dumps(
                    {
                        "scale": arguments.scale,
                        "requested_size": [arguments.width, arguments.height],
                        "inspector_hidden_for_narrow_width": shell.inspector_hidden_for_narrow_width,
                        "pages": pages,
                        "pixmap_width": pixmap.width(),
                        "pixmap_height": pixmap.height(),
                    },
                    sort_keys=True,
                )
            )
            return 0
        finally:
            if shell.isVisible():
                shell.close()
            shell.deleteLater()
            _process_qt_events(app, QCoreApplication, QEvent)


def _process_qt_events(app: Any, qcore_application: Any, event: Any) -> None:
    app.processEvents()
    qcore_application.sendPostedEvents(None, event.Type.DeferredDelete)
    app.processEvents()


def _primary_target(page: Any, workspace_id: str) -> Any:
    if workspace_id == "home":
        return getattr(page, "_live_card").button
    if workspace_id == "live":
        return getattr(page, "_device_selector")
    if workspace_id == "sweep":
        return getattr(page, "_mode")
    if workspace_id == "calibration":
        return getattr(page, "_refresh_button")
    if workspace_id == "tinysa":
        return getattr(page, "_discover")
    if workspace_id == "replay":
        return getattr(page, "_path")
    return getattr(page, "_load_button")


def _missing_semantic_names(
    page: Any,
    qt: Any,
    widget: Any,
    abstract_button: Any,
    abstract_spin_box: Any,
    combo_box: Any,
    line_edit: Any,
    table_widget: Any,
) -> list[str]:
    missing: list[str] = []
    for child in page.findChildren(widget):
        if not child.isVisible() or not child.isEnabled() or child.focusPolicy() == qt.FocusPolicy.NoFocus:
            continue
        if isinstance(child, line_edit) and isinstance(child.parentWidget(), abstract_spin_box):
            continue
        if isinstance(child, abstract_button):
            named = child.accessibleName().strip() or child.text().strip()
        elif isinstance(child, (abstract_spin_box, combo_box, line_edit, table_widget)):
            named = child.accessibleName().strip()
        else:
            continue
        if not named:
            missing.append(type(child).__name__)
    return missing


if __name__ == "__main__":
    raise SystemExit(main())
