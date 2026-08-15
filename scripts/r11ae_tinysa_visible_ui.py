"""Prepare or explicitly execute one human-visible R11-AE tinySA UI cell.

The normal mode writes only a default-disabled dry preflight.  PnP-ready
preparation and visible execution additionally require a per-process opt-in
and the exact user confirmation, so neither Qt nor a device is touched by
default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# This standalone runner must establish its repository-root import path first.
from sdr_monitor.r11ae_tinysa_visible_ui_evidence import (  # noqa: E402
    R11AE_CONFIRMATION,
    R11AEVisibleUiProfile,
    build_r11ae_preflight,
    build_r11ae_ready_preflight,
    claim_new_r11ae_evidence_output,
    publish_r11ae_evidence,
    r11ae_sha256,
    r11ae_visible_execution_is_enabled,
    require_r11ae_display_preflight_freshness,
    validate_r11ae_preflight,
    validate_r11ae_ready_preflight,
    write_r11ae_json,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--scale-percent", type=int, required=True)
    result.add_argument("--prepare-ready", action="store_true")
    result.add_argument("--prepare-execution-draft", action="store_true")
    result.add_argument("--probe-display", action="store_true")
    result.add_argument("--execute", action="store_true")
    result.add_argument("--confirm-visible", default="", metavar="PHRASE")
    result.add_argument("--draft-preflight", type=Path)
    result.add_argument("--ready-preflight", type=Path)
    result.add_argument("--display-preflight", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    enabled_modes = sum(
        int(flag)
        for flag in (
            arguments.prepare_ready,
            arguments.prepare_execution_draft,
            arguments.probe_display,
            arguments.execute,
        )
    )
    if enabled_modes > 1:
        parser().error("R11-AE accepts one execution mode at a time")
    if enabled_modes:
        # This guard intentionally precedes Qt/PnP imports, output claims and
        # every possible serial-port construction.
        if not r11ae_visible_execution_is_enabled():
            parser().error(
                "R11-AE visible execution is default-disabled; no window or device was opened"
            )
        if arguments.confirm_visible != R11AE_CONFIRMATION:
            parser().error("R11-AE requires the exact visible-cell confirmation")

    profile = R11AEVisibleUiProfile(arguments.scale_percent)
    if arguments.prepare_execution_draft:
        write_r11ae_json(
            arguments.output,
            build_r11ae_preflight(profile, visible_execution_enabled=True),
        )
        print(json.dumps({"status": "execution_draft_preflight", "scale_percent": profile.scale_percent}))
        return 0
    if arguments.prepare_ready:
        return _prepare_ready(arguments, profile)
    if arguments.probe_display:
        return _probe_display(arguments, profile)
    if arguments.execute:
        return _execute_visible_cell(arguments, profile)
    write_r11ae_json(arguments.output, build_r11ae_preflight(profile))
    print(json.dumps({"status": "preflight", "scale_percent": profile.scale_percent}))
    return 0


def _probe_display(arguments: argparse.Namespace, profile: R11AEVisibleUiProfile) -> int:
    """Observe actual Windows DPI before a final claim, PnP or serial work."""

    os.environ["SDR_AUTO_DISCOVER"] = "0"
    from PySide6.QtWidgets import QApplication

    from sdr_monitor.ui.r11ae_tinysa_display_preflight import (
        collect_r11ae_windows_display_preflight,
    )

    app = QApplication.instance() or QApplication([])
    del app
    preflight = collect_r11ae_windows_display_preflight(profile)
    write_r11ae_json(arguments.output, preflight.to_json())
    print(json.dumps({"status": "display_preflight", "scale_percent": profile.scale_percent}))
    return 0


def _prepare_ready(arguments: argparse.Namespace, profile: R11AEVisibleUiProfile) -> int:
    """Perform the one explicit PnP-only source binding after enablement."""

    if arguments.draft_preflight is None:
        raise ValueError("R11-AE ready preparation requires --draft-preflight")
    draft = _read_json(arguments.draft_preflight, "R11-AE draft preflight")
    validate_r11ae_preflight(draft, profile)
    if not isinstance(draft, dict) or draft.get("visible_execution_enabled") is not True:
        raise ValueError("R11-AE ready preparation requires an enabled execution draft")
    from sdr_monitor.services import TinySaSerialSourceBackend, TinySaSourceCompositionService

    composition = TinySaSourceCompositionService(TinySaSerialSourceBackend())
    discovered = composition.discover()
    if len(discovered.candidates) != 1:
        raise ValueError("R11-AE ready preparation requires exactly one tinySA candidate")
    candidate = discovered.candidates[0]
    ready = build_r11ae_ready_preflight(
        profile,
        draft_preflight_sha256=r11ae_sha256(arguments.draft_preflight),
        source_id=candidate.source_id,
        identity_assurance=candidate.identity_assurance.value,
    )
    write_r11ae_json(arguments.output, ready)
    print(json.dumps({"status": "ready_preflight", "scale_percent": profile.scale_percent}))
    return 0


def _execute_visible_cell(arguments: argparse.Namespace, profile: R11AEVisibleUiProfile) -> int:
    """Run one manual AppShell witness; input and acquisition remain human actions."""

    if (
        arguments.draft_preflight is None
        or arguments.ready_preflight is None
        or arguments.display_preflight is None
    ):
        raise ValueError("R11-AE visible execution requires draft, ready and fresh display preflights")
    draft = _read_json(arguments.draft_preflight, "R11-AE draft preflight")
    validate_r11ae_preflight(draft, profile)
    if not isinstance(draft, dict) or draft.get("visible_execution_enabled") is not True:
        raise ValueError("R11-AE visible execution requires an enabled execution draft")
    draft_sha256 = r11ae_sha256(arguments.draft_preflight)
    ready = _read_json(arguments.ready_preflight, "R11-AE ready preflight")
    validate_r11ae_ready_preflight(ready, profile)
    if not isinstance(ready, dict):
        raise TypeError("R11-AE ready preflight must be an object")
    if ready.get("draft_preflight_sha256") != draft_sha256:
        raise ValueError("R11-AE ready preflight does not bind the retained draft")
    binding = ready.get("source_binding")
    if not isinstance(binding, dict):
        raise TypeError("R11-AE ready preflight source binding is invalid")
    source_id = binding.get("source_id")
    assurance = binding.get("identity_assurance")
    if not isinstance(source_id, str) or not isinstance(assurance, str):
        raise TypeError("R11-AE ready preflight source binding is invalid")
    display = _read_json(arguments.display_preflight, "R11-AE display preflight")
    require_r11ae_display_preflight_freshness(
        display,
        profile,
        now_utc=datetime.now(UTC),
    )
    display_sha256 = r11ae_sha256(arguments.display_preflight)

    # Reserve the final result before a QApplication is constructed.  A later
    # trace failure leaves this claim immutable and therefore cannot look like
    # successful evidence.
    claim = claim_new_r11ae_evidence_output(arguments.output)
    os.environ["SDR_AUTO_DISCOVER"] = "0"
    try:
        from PySide6.QtWidgets import QApplication

        from sdr_monitor.ui.app_shell import SDRAppShell
        from sdr_monitor.ui.r11ae_tinysa_visible_witness import R11AEVisibleUiWitness

        app = QApplication.instance() or QApplication([])
        shell = SDRAppShell()
        shell.resize(profile.width, profile.height)
        witness = R11AEVisibleUiWitness(
            profile,
            draft_preflight_sha256=draft_sha256,
            ready_preflight_sha256=r11ae_sha256(arguments.ready_preflight),
            display_preflight_sha256=display_sha256,
            source_id=source_id,
            identity_assurance=assurance,
        )
        witness.attach(shell)
        shell.show()
        shell.raise_()
        shell.activateWindow()
        print(
            "R11-AE visible cell is open. Use mouse and keyboard to run "
            "Discover → Select → Verify identity → Use verified source, resize while "
            "the one trace is busy, acquire the locked trace once, then close the shell.",
            flush=True,
        )
        app.exec()
        evidence = witness.build_evidence()
        publish_r11ae_evidence(claim, evidence.to_json(), profile)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"R11-AE visible cell failed closed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "visible_evidence", "scale_percent": profile.scale_percent}))
    return 0


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"{label} cannot be read") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON") from error


if __name__ == "__main__":
    raise SystemExit(main())
