"""Repeat real-Qt synthetic RTBW contexts; inspect normal post-close retention.

No receiver, forced GC, GC policy change or extra QObject deletion. Referrers
are a bounded scalar diagnostic, NOT a complete root graph or proof of a leak.
Inspection itself allocates and can trigger ordinary automatic GC. Timing from
this instrumented runner is not a latency acceptance or RSS plateau guarantee.
"""
import argparse
from dataclasses import asdict
import gc
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
import types


def _describe_referrer(value, target):
    kind = type(value)
    result = {"type": kind.__module__ + "." + kind.__qualname__}
    if isinstance(value, types.MethodType):
        result["method"] = value.__func__.__qualname__
    elif isinstance(value, types.FunctionType):
        result["function"] = value.__qualname__
    elif type(value) is dict:
        # Snapshot only scalar names; never return attribute values or repr(Qt).
        result["fields"] = [str(key) for key, item in value.copy().items()
                            if item is target and isinstance(key, (str, int))][:16]
    elif isinstance(value, types.CellType):
        functions = []
        for parent in gc.get_referrers(value):
            if type(parent) is not tuple:
                continue
            for function in gc.get_referrers(parent):
                if isinstance(function, types.FunctionType) and function.__closure__ is parent:
                    functions.append({"function": function.__qualname__, "captures": [
                        name for name, cell in zip(function.__code__.co_freevars, parent) if cell is value]})
                    if len(functions) == 16:
                        break
            if len(functions) == 16:
                break
        result["closure_functions"] = functions
    return result


def direct_referrers(reference):
    """Read direct Python referrers of one weak owner; retain scalar rows only.

    get_referrers creates temporary lists; CPython may expose those diagnostic
    containers. This intentionally does not classify an object as rooted or
    unreachable and cannot see every Qt/C++ reference. Full incoming scans are
    GC API work; only returned evidence (32 referrers, 16 cells) is bounded.
    """
    owner = reference()
    if owner is None:
        return {"alive": False, "referrers": [], "truncated": False}
    refs = gc.get_referrers(owner)
    return {"alive": True, "referrers": [_describe_referrer(ref, owner) for ref in refs[:32]],
            "truncated": len(refs) > 32}


def context_snapshot(contexts, *, inspect=False):
    # The strong ledgers contain weak array roots, never strong publications.
    rows = []
    for budget, owners in contexts:
        row = {"allocation_budget": asdict(budget.snapshot()),
               "weak_owner_alive": {name: reference() is not None for name, reference in owners.items()}}
        if inspect:
            row["direct_referrers"] = {name: direct_referrers(reference) for name, reference in owners.items()}
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--contexts", type=int, default=4)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--bins", type=int, default=65536)
    parser.add_argument("--power-bins", type=int, default=64)
    parser.add_argument("--inspect-referrers", action="store_true")
    args = parser.parse_args()
    if not sys.flags.isolated or not 2 <= args.contexts <= 16 or not 1 <= args.seconds <= 60:
        parser.error("Python -I; 2..16 contexts, 1..60 seconds per cycle required")
    if args.output.exists():
        parser.error("output must be new")
    root = args.checkout.resolve(strict=True)
    sys.path.insert(0, str(root))
    from scripts.benchmark_app05_rtbw_observation import main as run_context
    from scripts.benchmark_app04_poll_overload import run_qt_until
    from tests.ui_v2.run_app05_memory_inventory import process_memory

    contexts, runs, checkpoints = [], [], []
    initial_gc = gc.get_stats()
    for index in range(args.contexts):
        result, _, budget, owners = run_context([
            "--checkout", str(root), "--output", str(args.output), "--seconds", str(args.seconds),
            "--cycles", "2", "--bins", str(args.bins), "--persistence-power-bins", str(args.power_bins),
            "--persistence-every", "10", "--page-seconds", ".5", "--viewport-seconds", ".2",
            "--memory-seconds", ".5"])
        contexts.append((budget, owners))
        runs.append(result)
        until = perf_counter() + .5
        run_qt_until(lambda: perf_counter() >= until, 2)
        # Sample before scanning referrers; observation allocations can run GC.
        checkpoint = {"context": index + 1, "process": process_memory(),
                      "gc_stats": gc.get_stats(), "closed_contexts": context_snapshot(contexts)}
        if args.inspect_referrers:
            checkpoint["current_referrers"] = {name: direct_referrers(ref) for name, ref in owners.items()}
        checkpoints.append(checkpoint)
        if any(row["allocation_budget"]["reserved_bytes"] for row in checkpoint["closed_contexts"]):
            raise AssertionError("closed context retained a reservation")
        print(json.dumps({"completed_context": index + 1, "process": checkpoint["process"],
                          "closed_contexts": checkpoint["closed_contexts"]}), flush=True)
    report = {"scope": __doc__, "contexts": args.contexts, "initial_gc": initial_gc,
              "referrer_inspection": args.inspect_referrers, "checkpoints": checkpoints, "runs": runs,
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)


if __name__ == "__main__":
    main()
