"""P16UI-05 Wideband Sweep workspace 60 Hz budget harness.

The harness never opens a DFL, recording, or SDR device: it drives the
Wideband Sweep presenter and workspace against :class:`FakeSweepService`, so
the numbers reflect GUI-thread cost only (poll + snapshot diff + render).

Measured budgets:

* idle presenter poll (no plan or worker publication);
* idle workspace poll (the offscreen timer hot path);
* workspace poll after a completed fake plan/run/render path; and
* full workspace render with a deliberately changed snapshot key.

Evidence is written atomically as JSON to an explicitly requested output
directory; nothing device- or measurement-specific is ever written.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import replace
import json
import os
from pathlib import Path
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from esw_dfl.sdr.contracts import SweepConfig
from esw_dfl.sdr.fake_sweep_service import FakeSweepConfig, FakeSweepService
from esw_dfl.ui.sweep_presenter import SweepPresenter
from esw_dfl.ui.sweep_state import SweepRunStatus
from esw_dfl.ui.sweep_workspace import SweepWorkspace


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


def _config() -> SweepConfig:
    return SweepConfig(
        start_frequency_hz=100.0e6,
        stop_frequency_hz=118.0e6,
        sample_rate_hz=16.0e6,
        analog_bandwidth_hz=12.0e6,
        overlap_hz=2.0e6,
        fft_size=256,
        hop_size=128,
        dwell_frames=1,
    )


def _presenter() -> SweepPresenter:
    fake_config = FakeSweepConfig(
        sample_rate_hz=16.0e6,
        fft_size=256,
        hop_size=128,
        dwell_frames=1,
    )
    return SweepPresenter(
        service_factory=lambda _uri: FakeSweepService(fake_config),
        poll_batch_size=8,
        idle_timeout_s=1.0,
        sweep_id=16,
    )


def _workspace(presenter: SweepPresenter) -> SweepWorkspace:
    return SweepWorkspace(
        presenter=presenter,
        locale="en",
        uri_provider=lambda: "ip:fake",
        poll_interval_ms=16,
    )


def _dispose_workspace(workspace: SweepWorkspace, app: QApplication) -> None:
    workspace.close()
    workspace.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="benchmark_p16_ui_sweep.py")
    parser.add_argument("--output-dir", type=Path, help="optional private evidence directory")
    parser.add_argument("--polls", type=_positive_int, default=2000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    existing_application = QApplication.instance()
    app = existing_application if isinstance(existing_application, QApplication) else QApplication([])
    payload: dict[str, object] = {"polls": args.polls}

    # 1. Idle presenter poll: cached immutable snapshot only.
    idle_presenter = _presenter()
    try:
        payload["idle_presenter_poll_seconds"] = _seconds_per_poll(idle_presenter.poll, args.polls)
    finally:
        idle_presenter.close()

    # 2. Workspace idle: timer hot path without a plan or device.
    workspace_idle = _workspace(_presenter())
    try:
        payload["idle_workspace_poll_seconds"] = _seconds_per_poll(
            workspace_idle._poll_presenter,  # noqa: SLF001 - benchmark probes timer slot
            args.polls,
        )
    finally:
        _dispose_workspace(workspace_idle, app)

    # 3. Complete one fake plan/run path, then measure the steady workspace
    # polling cost with the full stitched frame already rendered.
    presenter = _presenter()
    workspace = _workspace(presenter)
    try:
        presenter.plan(_config())
        started = time.monotonic()
        presenter.run("ip:fake")
        deadline = started + 5.0
        while presenter.poll().run.status not in {
            SweepRunStatus.COMPLETED,
            SweepRunStatus.CANCELLED,
            SweepRunStatus.FAILED,
        } and time.monotonic() < deadline:
            workspace._poll_presenter()  # noqa: SLF001 - offscreen timer never fires
            time.sleep(0.001)
        workspace._poll_presenter()  # noqa: SLF001 - publish terminal frame offscreen
        payload["completed_in_seconds"] = time.monotonic() - started
        payload["final_status"] = presenter.snapshot.run.status.value
        payload["running_workspace_poll_seconds"] = _seconds_per_poll(
            workspace._poll_presenter,  # noqa: SLF001 - benchmark probes timer slot
            args.polls,
        )

        # 4. Changed generation forces the complete snapshot renderer on every
        # call and represents the conservative GUI-thread upper bound.
        def full_render_poll() -> None:
            snapshot = presenter.snapshot
            workspace._refresh_from_snapshot(  # noqa: SLF001 - benchmark full renderer
                replace(snapshot, generation=snapshot.generation + 1),
            )

        payload["full_render_workspace_poll_seconds"] = _seconds_per_poll(
            full_render_poll,
            args.polls,
        )
    finally:
        _dispose_workspace(workspace, app)

    if args.output_dir is not None:
        evidence = args.output_dir / "p16ui_05_sweep_workspace_benchmark.json"
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
