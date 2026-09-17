"""Run V2 tests from this checkout, with native scope explicitly selected.

Default: source/fake/offscreen tests only, reporting the exact compiled test
deferred. --include-native additionally requires a built canonical extension.
Neither mode is a physical RX or visible Windows acceptance test.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import unittest


COMPILED_TESTS = frozenset({
    "test_app03_numerical_composition.NumericalCompositionTests."
    "test_native_semantics_reach_canvas_and_accessible_description",
})


def flatten(suite: unittest.TestSuite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-native", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, str(root))
    tests = list(flatten(unittest.defaultTestLoader.discover(str(root / "tests/ui_v2"))))
    deferred = sorted(test.id() for test in tests if not args.include_native
                      and test.id() in COMPILED_TESTS)
    if not args.include_native and set(deferred) != COMPILED_TESTS:
        raise RuntimeError("Compiled test identity changed; review source gate scope")
    print("COMPILED_TESTS_DEFERRED", deferred, flush=True)
    selected = unittest.TestSuite(test for test in tests if test.id() not in deferred)
    result = unittest.TextTestRunner(verbosity=1).run(selected)
    outside = sorted(name for name, module in tuple(sys.modules.items())
                     if name.split(".")[0] in {"sdr_monitor", "esw_dfl"}
                     and isinstance(file_path := getattr(module, "__file__", None), str)
                     and not Path(file_path).resolve().is_relative_to(root))
    print("PRODUCT_MODULES_OUTSIDE_CHECKOUT", outside, flush=True)
    return 0 if result.wasSuccessful() and not outside else 1


if __name__ == "__main__":
    raise SystemExit(main())
