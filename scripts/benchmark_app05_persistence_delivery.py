"""Normal synthetic RTBW paint-age runner with explicit UI persistence mode.

No conversion/mapping/upload stage wrappers. Uses the existing paint witness,
controls and real Qt event loop. Mode is set through the completed scene's public
UI setter before acquisition, not through a backend command or histogram change.
Offscreen/fake only: this is not RF, DWM, EXE or physical latency acceptance.
"""
import argparse
from collections import Counter
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


def main():
    own = argparse.ArgumentParser(add_help=False)
    own.add_argument("--persistence-render-mode", choices=("direct", "visual"), required=True)
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

    with patch.object(SpectrumScene, "__init__", initialized):
        report, output, _, _ = runner.main(remaining)
    if not report["persistence"]["enabled"] or not constructed_modes:
        raise AssertionError("normal persistence observation requires a real density scene")
    report["persistence_delivery"] = dict(scope=__doc__, render_mode=settings.persistence_render_mode,
        constructed_modes=dict(constructed_modes), stage_instrumentation=False,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(dict(head=report["checkout_head"], mode=settings.persistence_render_mode,
        age=report["host_publication_to_first_paint_ms"], controls=report["control_distributions"],
        persistence=report["persistence"], workers=report["remaining_workers"])))


if __name__ == "__main__":
    main()
