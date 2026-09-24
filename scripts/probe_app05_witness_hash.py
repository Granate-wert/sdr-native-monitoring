"""Isolated full-content Visual witness hash comparison on the normal V2 runner.

Only the worker's digest factory is substituted. The same complete density,
edge and metadata stream, bounded cancellation checks, history identity gate,
pixel mapping and product owners remain in place. This observer does not wire
an alternative algorithm into the application or its release dependencies.
"""

import argparse
from contextlib import ExitStack, nullcontext
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from time import perf_counter
from unittest.mock import patch


def witness_hash_context(algorithm, persistence_projection):
    if algorithm == "sha256":
        return nullcontext(), None
    if algorithm != "blake3":
        raise ValueError("unsupported witness algorithm")
    from blake3 import blake3

    # persistence_input_witness is imported by the GUI overlay, but its
    # globals resolve in persistence_projection. The only local hashlib
    # use is digest construction; do not patch the process-wide module.
    return patch.object(persistence_projection, "hashlib", SimpleNamespace(sha256=blake3)), version("blake3")


def append_post_context_inventory(result, budget, owners):
    """Preserve the runner's post-return memory gate for opt-in inventory runs."""
    if "memory" not in result:
        return
    from scripts.benchmark_app04_poll_overload import run_qt_until
    from tests.ui_v2.run_app05_memory_inventory import process_memory, qt_wrapper_counts

    until = perf_counter() + .5
    run_qt_until(lambda: perf_counter() >= until, 2)
    result["memory"]["after_context_return"] = dict(
        process=process_memory(), allocation_budget=asdict(budget.snapshot()),
        weak_owner_alive={name: reference() is not None for name, reference in owners.items()},
        qt_census=qt_wrapper_counts(),
        scope="After benchmark locals return and 500ms real Qt loop; normal GC, no forced collection or product state clearing",
    )
    if result["memory"]["collect_after_context"]:
        import gc

        collected = gc.collect()
        until = perf_counter() + .5
        run_qt_until(lambda: perf_counter() >= until, 2)
        result["memory"]["diagnostic_after_collection"] = dict(
            collected=collected, process=process_memory(),
            allocation_budget=asdict(budget.snapshot()),
            weak_owner_alive={name: reference() is not None for name, reference in owners.items()},
            qt_census=qt_wrapper_counts(),
            scope="Explicit diagnostic GC AFTER unmodified normal samples; not product behavior, release deadline or leak fix",
        )


def main(argv: list[str] | None = None) -> int:
    if not sys.flags.isolated:
        raise SystemExit("Python -I required")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--witness-algorithm", choices=("sha256", "blake3"), required=True)
    parser.add_argument("--profile-substages", action="store_true")
    options, remaining = parser.parse_known_args(argv)
    if "--checkout" not in remaining:
        raise SystemExit("--checkout required")
    root = Path(remaining[remaining.index("--checkout") + 1]).resolve(strict=True)
    sys.path.insert(0, str(root))
    from scripts import benchmark_app05_rtbw_observation as runner
    from sdr_monitor.ui.v2.spectrum import persistence_projection, projection

    context, candidate_version = witness_hash_context(options.witness_algorithm, persistence_projection)
    recorder = None
    with ExitStack() as stack:
        stack.enter_context(context)
        if options.profile_substages:
            from scripts.profile_app05_visual_substages import SubstageRecorder

            recorder = SubstageRecorder()
            stack.enter_context(recorder.instrument(persistence_projection, projection))
        result, output, budget, owners = runner.main(remaining)
    append_post_context_inventory(result, budget, owners)
    result["witness_hash_probe"] = dict(
        algorithm=options.witness_algorithm,
        blake3_version=candidate_version,
        scope="Observer-only digest factory for full density/edges/metadata; normal UI V2 pipeline",
        probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    )
    if recorder is not None:
        result["persistence_substage_profile"] = recorder.report()
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps({"output": str(output), "algorithm": options.witness_algorithm,
                      "generated": result["generated"], "remaining_workers": result["remaining_workers"]}))
    return runner.persistence_catchup_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
