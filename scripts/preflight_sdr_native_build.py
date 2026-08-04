"""Validate a standalone _sdr_native extension without importing legacy packages."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--expect-cuda", action="store_true")
    parser.add_argument("--expect-cpu", action="store_true")
    args = parser.parse_args()
    module_path = args.module.resolve()
    manifest_path = args.manifest.resolve()
    if not module_path.is_file() or not manifest_path.is_file():
        raise SystemExit("standalone native module or manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    expected_cuda = args.expect_cuda and not args.expect_cpu
    if bool(manifest.get("cuda_compiled")) != expected_cuda:
        raise SystemExit("manifest cuda_compiled does not match requested lane")
    abi = re.search(r"^_sdr_native\.(.+)\.pyd$", module_path.name)
    if not abi or manifest.get("python_abi") != abi.group(1):
        raise SystemExit("native module ABI does not match manifest")
    spec = importlib.util.spec_from_file_location("_sdr_native", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load staged standalone native module")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_sdr_native"] = module
    spec.loader.exec_module(module)
    info = dict(module.build_info())
    if bool(info.get("cuda_compiled")) != expected_cuda:
        raise SystemExit("build_info cuda_compiled does not match manifest")
    if manifest.get("native_version") != info.get("version"):
        raise SystemExit("native version does not match manifest")
    outcome = dict(module.run_self_test())
    if not outcome.get("ok"):
        raise SystemExit("native self-test failed: " + str(outcome.get("message", "unknown")))
    print(json.dumps({"module": str(module_path), "manifest": manifest, "build_info": info, "self_test": outcome}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
