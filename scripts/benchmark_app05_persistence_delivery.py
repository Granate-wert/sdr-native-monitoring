"""Normal synthetic RTBW paint-age runner with explicit UI persistence mode.

No conversion/mapping/upload stage wrappers. Uses the existing paint witness,
controls and real Qt event loop. Mode is set through the completed scene's public
UI setter before acquisition, not through a backend command or histogram change.
Offscreen/fake only: this is not RF, DWM, EXE or physical latency acceptance.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


@contextmanager
def diagnostic_switch_interval(milliseconds):
    """Child-process sensitivity experiment only; never a product default.

    Restored even on failed runs. None leaves the runtime untouched. Explicit
    factors are limited to the two planned comparison values, not tuning input.
    """
    if milliseconds not in (None, 1.0, 5.0):
        raise ValueError("diagnostic switch interval must be 1 or 5 ms")
    previous = sys.getswitchinterval()
    record = dict(diagnostic_only=milliseconds is not None, requested_ms=milliseconds,
                  previous_ms=previous * 1000, active_ms=previous * 1000,
                  scope="Isolated synthetic-process scheduling sensitivity, NOT product or release acceptance")
    try:
        if milliseconds is not None:
            sys.setswitchinterval(milliseconds / 1000)
        record["active_ms"] = sys.getswitchinterval() * 1000
        yield record
    finally:
        if milliseconds is not None:
            sys.setswitchinterval(previous)
        record["restored_ms"] = sys.getswitchinterval() * 1000


def main():
    own = argparse.ArgumentParser(add_help=False)
    own.add_argument("--persistence-render-mode", choices=("direct", "visual"), required=True)
    own.add_argument("--diagnostic-switch-ms", type=float, choices=(1.0, 5.0))
    settings, remaining = own.parse_known_args()
    if not sys.flags.isolated:
        raise SystemExit("Python -I required")
    root = Path(remaining[remaining.index("--checkout") + 1]).resolve(strict=True)
    sys.path.insert(0, str(root))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from scripts import benchmark_app05_rtbw_observation as runner
    from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
    from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
    constructed_modes = Counter()
    original = SpectrumScene.__init__

    @wraps(original)
    def initialized(scene, *args, **kwargs):
        original(scene, *args, **kwargs)
        scene.set_persistence_render_mode(PersistenceRenderMode(settings.persistence_render_mode))
        actual = scene._persistence.render_mode.value
        if actual != settings.persistence_render_mode or scene._persistence_mode.currentData() != actual:
            raise AssertionError("requested persistence mode not applied to UI and overlay")
        constructed_modes[actual] += 1

    with diagnostic_switch_interval(settings.diagnostic_switch_ms) as scheduling:
        with patch.object(SpectrumScene, "__init__", initialized):
            report, output, _, _ = runner.main(remaining)
    if not report["persistence"]["enabled"] or not constructed_modes:
        raise AssertionError("normal persistence observation requires a real density scene")
    report["persistence_delivery"] = dict(scope=__doc__, render_mode=settings.persistence_render_mode,
        constructed_modes=dict(constructed_modes), stage_instrumentation=False,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    report["diagnostic_scheduling"] = scheduling
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(dict(head=report["checkout_head"], mode=settings.persistence_render_mode,
        age=report["host_publication_to_first_paint_ms"], controls=report["control_distributions"],
        persistence=report["persistence"], workers=report["remaining_workers"])))


if __name__ == "__main__":
    main()
