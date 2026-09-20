"""Bounded exact-density conversion/GUI mapping observer; synthetic offscreen only.

Wraps the existing RTBW runner and owners, not a second pipeline. Conversion is
only native-density adaptation, not all worker preparation. Upload is synchronous
ImageItem submission, NOT Qt raster paint or DWM display. Instrumented times are
not latency acceptance. Records retain scalar data and weak ndarray refs only.
"""
import argparse
from collections import OrderedDict, deque
from contextlib import ExitStack
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
from time import perf_counter
from unittest.mock import patch
import weakref


class DensityRecords:
    def __init__(self, capacity=2048):
        self.capacity = capacity
        self.sources = OrderedDict()
        self.conversions = deque(maxlen=capacity)
        self.uploads = deque(maxlen=capacity)
        self.lock = threading.RLock()
        self.serial = self.missing = self.evictions = 0

    def converted(self, density, identity, began, ended, on_gui):
        with self.lock:
            self.serial += 1
            row = dict(serial=self.serial, identity=identity, begin=began, end=ended,
                       conversion_ms=(ended - began) * 1000, on_gui=on_gui,
                       shape=tuple(density.shape), uploads=0)
            self.sources[id(density)] = (weakref.ref(density), row)
            self.sources.move_to_end(id(density))
            self.conversions.append({k: v for k, v in row.items() if k != "uploads"})
            while len(self.sources) > self.capacity:
                self.sources.popitem(last=False)
                self.evictions += 1

    def uploaded(self, density, began, ended, mapping_ms, on_gui):
        with self.lock:
            entry = self.sources.get(id(density))
            if entry is None or entry[0]() is not density:
                self.missing += 1
                return
            source = entry[1]
            total = (ended - began) * 1000
            self.uploads.append(dict(serial=source["serial"], identity=source["identity"],
                first=source["uploads"] == 0, conversion_ms=source["conversion_ms"],
                conversion_to_upload_ms=(began - source["end"]) * 1000,
                mapping_ms=mapping_ms, upload_ms=total, other_upload_ms=total - mapping_ms,
                on_gui=on_gui))
            source["uploads"] += 1


def main():
    if not sys.flags.isolated:
        raise SystemExit("Python -I required")
    own = argparse.ArgumentParser(add_help=False)
    own.add_argument("--persistence-render-mode", choices=("direct", "visual"), default="direct")
    settings, remaining = own.parse_known_args()
    root = Path(remaining[remaining.index("--checkout") + 1]).resolve(strict=True)
    sys.path.insert(0, str(root))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import numpy as np
    from scripts import benchmark_app05_rtbw_observation as runner
    from sdr_monitor.ui.v2.state import analyzer_layer_cache
    from sdr_monitor.ui.v2.spectrum.persistence_overlay import PersistenceOverlay
    from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
    records, local = DensityRecords(), threading.local()
    gui = threading.get_ident()
    convert = analyzer_layer_cache.persistence_density_from_native
    initialize = PersistenceOverlay.__init__
    mapping = PersistenceOverlay._render_image
    uploading = PersistenceOverlay._upload

    @wraps(convert)
    def converted(frame, *args, **kwargs):
        began = perf_counter()
        result = convert(frame, *args, **kwargs)
        ended = perf_counter()
        if result is not None:
            identity = (int(frame.config_generation), int(frame.update_sequence),
                        int(frame.source_frame_sequence), int(frame.timestamp_ns))
            records.converted(result.density, identity, began, ended, threading.get_ident() == gui)
        return result

    @wraps(initialize)
    def initialized(overlay, *args, **kwargs):
        initialize(overlay, *args, **kwargs)
        overlay.set_render_mode(PersistenceRenderMode(settings.persistence_render_mode))

    @wraps(mapping)
    def mapped(overlay, view):
        began = perf_counter()
        result = mapping(overlay, view)
        if getattr(local, "upload", None) is not None:
            local.upload[0] += (perf_counter() - began) * 1000
        return result

    @wraps(uploading)
    def uploaded(overlay, view, *args, **kwargs):
        old = getattr(local, "upload", None)
        times = local.upload = [0.0]
        before = overlay.metrics.image_uploads
        began = perf_counter()
        try:
            result = uploading(overlay, view, *args, **kwargs)
            ended = perf_counter()
            if overlay.metrics.image_uploads > before:
                records.uploaded(view.density, began, ended, times[0], threading.get_ident() == gui)
            return result
        finally:
            local.upload = old

    with ExitStack() as stack:
        stack.enter_context(patch.object(analyzer_layer_cache, "persistence_density_from_native", converted))
        stack.enter_context(patch.object(PersistenceOverlay, "__init__", initialized))
        stack.enter_context(patch.object(PersistenceOverlay, "_render_image", mapped))
        stack.enter_context(patch.object(PersistenceOverlay, "_upload", uploaded))
        report, output, _, _ = runner.main(remaining)
    if not records.conversions or not records.uploads:
        raise AssertionError("no persistence conversion/upload evidence")
    if any(row["on_gui"] for row in records.conversions) or any(not row["on_gui"] for row in records.uploads):
        raise AssertionError("unexpected owner thread")

    def summary(values):
        return dict(count=len(values), **dict(zip(("p50", "p95", "p99", "max"),
            map(float, (*np.percentile(values, [50, 95, 99]), max(values)))))) if values else None

    rows = list(records.uploads)
    report["persistence_profile"] = dict(scope=__doc__, render_mode=settings.persistence_render_mode,
        profiler_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        capacity=records.capacity, conversions_total=records.serial, conversions_retained=len(records.conversions),
        source_evictions=records.evictions, missing_upload_sources=records.missing,
        conversion_ms=summary([row["conversion_ms"] for row in records.conversions]),
        paired_uploads=len(rows), first_uploads=sum(row["first"] for row in rows),
        paired_ms={name: summary([row[name] for row in rows]) for name in
                   ("conversion_ms", "conversion_to_upload_ms", "mapping_ms", "upload_ms", "other_upload_ms")},
        slow_uploads=sorted(rows, key=lambda row: row["upload_ms"], reverse=True)[:20])
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report["persistence_profile"]))


if __name__ == "__main__":
    main()
