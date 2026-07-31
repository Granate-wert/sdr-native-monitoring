"""P16UI-04 Live Monitor workspace 60 Hz budget harness.

The harness never opens a DFL, recording, or SDR device: it drives the
Live Monitor presenter and workspace against :class:`FakeLiveService`, so
the numbers reflect GUI-thread cost only (poll + snapshot diff + render).

Measured budgets:

* idle presenter poll (no controller publication) — the 60 Hz timer
  hot path when nothing changed;
* workspace poll with a running fake session (frame-rate/health/quality
  badge renders) — the worst regular 60 Hz path;
* workspace poll with markers, requested/applied table and recording
  hook — the full render path.

Evidence is written atomically as JSON to an explicitly requested output
directory; nothing device- or measurement-specific is ever written.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from esw_dfl.sdr.contracts import CalibrationStatus
from esw_dfl.sdr.fake_live_service import (
    FakeLiveConfig,
    FakeLiveService,
    fake_capabilities,
)
from esw_dfl.ui.live_presenter import LiveMonitorPresenter
from esw_dfl.ui.live_workspace import LiveMonitorWorkspace


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _seconds_per_poll(call: Callable[[], object], count: int) -> float:
    """Measure *count* calls of ``call`` and return seconds per call."""

    started = time.perf_counter()
    for _ in range(count):
        call()
    return (time.perf_counter() - started) / count


def _presenter(
    config: FakeLiveConfig,
    *,
    poll_interval_s: float = 0.01,
) -> LiveMonitorPresenter:
    return LiveMonitorPresenter(
        service_factory=lambda uri: FakeLiveService(uri, config=config),
        capabilities_provider=lambda uri: fake_capabilities(config),
        poll_interval_s=poll_interval_s,
    )


def _workspace(presenter: LiveMonitorPresenter) -> LiveMonitorWorkspace:
    workspace = LiveMonitorWorkspace(
        presenter=presenter,
        discovery=lambda: ("fake-1", "Fake Pluto", "ip:fake"),
        poll_interval_ms=16,
    )
    return workspace


def _dispose_workspace(workspace: LiveMonitorWorkspace, app: QApplication) -> None:
    workspace.close()
    workspace.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="benchmark_p16_ui_live_monitor.py")
    parser.add_argument("--output-dir", type=Path, help="optional private evidence directory")
    parser.add_argument("--polls", type=_positive_int, default=5000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    existing_application = QApplication.instance()
    app = existing_application if isinstance(existing_application, QApplication) else QApplication([])

    payload: dict[str, object] = {"polls": args.polls}

    # 1. Idle presenter poll: no controller, no publication, pure deque peek.
    idle_presenter = _presenter(FakeLiveConfig())
    try:
        payload["idle_presenter_poll_seconds"] = _seconds_per_poll(
            idle_presenter.poll,
            args.polls,
        )
    finally:
        idle_presenter.close()

    # 2. Workspace idle: timer hot path without a device.
    workspace_idle = _workspace(_presenter(FakeLiveConfig()))
    try:
        payload["idle_workspace_poll_seconds"] = _seconds_per_poll(
            workspace_idle._poll_presenter,  # noqa: SLF001 - benchmark probes the timer slot
            args.polls,
        )
    finally:
        _dispose_workspace(workspace_idle, app)

    # 3. Running session with markers, requested/applied table and a
    #    recording hook toggled: full regular render path at 60 Hz.
    config = FakeLiveConfig(
        frames_per_poll=1,
        max_frames=64,
        calibration_status=CalibrationStatus.UNCALIBRATED,
    )
    presenter = _presenter(config)
    workspace = _workspace(presenter)
    try:
        workspace.connect_button.click()
        workspace.start_button.click()
        workspace.add_marker_button.click()
        workspace.add_marker_button.click()
        workspace.record_button.setChecked(True)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            workspace._poll_presenter()  # noqa: SLF001
            if presenter.snapshot.state.value == "running" and presenter.snapshot.frame_rate_hz > 0.0:
                break
        payload["running_workspace_poll_seconds"] = _seconds_per_poll(
            workspace._poll_presenter,  # noqa: SLF001
            args.polls,
        )
        payload["running_snapshot_state"] = presenter.snapshot.state.value
        payload["running_frame_rate_hz"] = presenter.snapshot.frame_rate_hz

        # 3b. Worst regular case: every poll carries a *changed* snapshot
        #     key, so the full render path (requested/applied table,
        #     backend/calibration/quality badges, health, frame rate)
        #     executes on every call — this is the 60 Hz upper bound.
        from dataclasses import replace

        def full_render_poll() -> object:
            workspace._refresh_from_snapshot(
                replace(presenter.snapshot, frame_rate_hz=presenter.snapshot.frame_rate_hz + 1.0)
            )
            return None

        payload["full_render_workspace_poll_seconds"] = _seconds_per_poll(
            full_render_poll,
            args.polls,
        )
    finally:
        workspace.record_button.setChecked(False)
        presenter.disconnect()
        presenter.close()
        _dispose_workspace(workspace, app)

    if args.output_dir is not None:
        evidence = args.output_dir / "p16ui_04_live_monitor_benchmark.json"
        args.output_dir.mkdir(parents=True, exist_ok=True)
        temporary = Path(f"{evidence}.part")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
            os.replace(temporary, evidence)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        payload["evidence_path"] = str(evidence)
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
