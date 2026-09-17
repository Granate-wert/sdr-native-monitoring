"""Fail-closed preflight for the canonical staged and active native module.

The check deliberately parses the canonical Python contract source instead of
importing ``esw_dfl``.  This keeps the standalone build boundary free of legacy
package side effects while detecting a native artifact that is older than the
contract source it is about to serve.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TypeAlias


ContractValue: TypeAlias = str | int
ContractEnums: TypeAlias = dict[str, dict[str, ContractValue]]


class ContractSurfaceError(ValueError):
    """Raised when the staged native module differs from the Python contract."""


@dataclass(frozen=True, slots=True)
class ContractExpectations:
    """Static contract values extracted without importing a product package."""

    schema_name: str
    schema_version: int
    enums: ContractEnums


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _literal_value(node: ast.AST) -> ContractValue:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.LShift):
        left = _literal_value(node.left)
        right = _literal_value(node.right)
        if isinstance(left, int) and isinstance(right, int) and left >= 0 and right >= 0:
            return left << right
    raise ContractSurfaceError("contract source contains a non-literal enum value")


def _module_assignment(tree: ast.Module, name: str) -> ast.AST:
    for statement in tree.body:
        targets: list[ast.expr] = []
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
            value = statement.value
        if value is not None and any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return value
    raise ContractSurfaceError(f"contract source does not define {name}")


def _enum_member_names(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, (ast.Tuple, ast.List)):
        raise ContractSurfaceError("STRING_ENUM_TYPES must be a literal tuple or list")
    names: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Name):
            raise ContractSurfaceError("STRING_ENUM_TYPES must contain only enum class names")
        names.append(item.id)
    return tuple(names)


def _class_members(node: ast.ClassDef) -> dict[str, ContractValue]:
    values: dict[str, ContractValue] = {}
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets = [statement.target]
            value = statement.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = _literal_value(value)
    if not values:
        raise ContractSurfaceError(f"contract enum {node.name} has no literal members")
    return values


def load_contract_expectations(contract_source: Path) -> ContractExpectations:
    """Read the canonical Python enum/schema surface without importing it."""

    try:
        tree = ast.parse(contract_source.read_text(encoding="utf-8"), filename=str(contract_source))
    except OSError as error:
        raise ContractSurfaceError(f"cannot read contract source: {contract_source}") from error
    except SyntaxError as error:
        raise ContractSurfaceError(f"cannot parse contract source: {contract_source}") from error

    schema_name = _literal_value(_module_assignment(tree, "CONTRACT_SCHEMA_NAME"))
    schema_version = _literal_value(_module_assignment(tree, "CONTRACT_SCHEMA_VERSION"))
    if not isinstance(schema_name, str) or not isinstance(schema_version, int):
        raise ContractSurfaceError("contract schema name/version must be literal str/int values")

    enum_nodes = {statement.name: statement for statement in tree.body if isinstance(statement, ast.ClassDef)}
    enum_names = (*_enum_member_names(_module_assignment(tree, "STRING_ENUM_TYPES")), "QualityFlag")
    enums: ContractEnums = {}
    for enum_name in enum_names:
        try:
            enums[enum_name] = _class_members(enum_nodes[enum_name])
        except KeyError as error:
            raise ContractSurfaceError(f"contract source does not define enum {enum_name}") from error
    return ContractExpectations(schema_name, schema_version, enums)


def _native_contract_schema(module: ModuleType) -> tuple[str, int, ContractEnums]:
    try:
        raw_schema = module.contract_schema()
        schema = dict(raw_schema)
        raw_enums = schema["enums"]
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ContractSurfaceError("native module does not expose a readable contract_schema") from error
    if not isinstance(raw_enums, Mapping):
        raise ContractSurfaceError("native contract_schema enums are not a mapping")
    enums: ContractEnums = {}
    for enum_name, raw_members in raw_enums.items():
        if not isinstance(enum_name, str) or not isinstance(raw_members, Mapping):
            raise ContractSurfaceError("native contract_schema contains an invalid enum mapping")
        enums[enum_name] = dict(raw_members)
    schema_name = schema.get("schema")
    schema_version = schema.get("schema_version")
    if not isinstance(schema_name, str) or not isinstance(schema_version, int):
        raise ContractSurfaceError("native contract_schema has an invalid schema name/version")
    return schema_name, schema_version, enums


def validate_contract_surface(module: ModuleType, expectations: ContractExpectations) -> None:
    """Reject an artifact whose exported contract differs from Python source."""

    schema_name, schema_version, native_enums = _native_contract_schema(module)
    if schema_name != expectations.schema_name or schema_version != expectations.schema_version:
        raise ContractSurfaceError(
            "native contract schema does not match Python source "
            f"({schema_name!r}/{schema_version} != {expectations.schema_name!r}/{expectations.schema_version})"
        )
    if getattr(module, "CONTRACT_SCHEMA_NAME", None) != expectations.schema_name:
        raise ContractSurfaceError("native CONTRACT_SCHEMA_NAME does not match Python source")
    if getattr(module, "CONTRACT_SCHEMA_VERSION", None) != expectations.schema_version:
        raise ContractSurfaceError("native CONTRACT_SCHEMA_VERSION does not match Python source")
    for enum_name, expected_members in expectations.enums.items():
        actual_members = native_enums.get(enum_name)
        if actual_members != expected_members:
            raise ContractSurfaceError(f"native {enum_name} wire mapping does not match Python source")

    native_quality = getattr(module, "QualityFlag", None)
    if native_quality is None:
        raise ContractSurfaceError("native module does not expose QualityFlag")
    for member_name, expected_value in expectations.enums["QualityFlag"].items():
        try:
            actual_value = int(getattr(native_quality, member_name))
        except (AttributeError, TypeError, ValueError) as error:
            raise ContractSurfaceError(f"native QualityFlag is missing {member_name}") from error
        if actual_value != expected_value:
            raise ContractSurfaceError(f"native QualityFlag.{member_name} does not match Python source")


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractSurfaceError(f"cannot read native manifest: {path}") from error
    if not isinstance(value, dict):
        raise ContractSurfaceError("native manifest must be a JSON object")
    return value


def validate_manifest(module_path: Path, manifest: Mapping[str, object], expected_cuda: bool) -> None:
    """Check identity, ABI and immutable staged-artifact hash before activation."""

    if bool(manifest.get("cuda_compiled")) != expected_cuda:
        raise ContractSurfaceError("manifest cuda_compiled does not match requested lane")
    abi = re.search(r"^_sdr_native\.(.+)\.pyd$", module_path.name)
    if not abi or manifest.get("python_abi") != abi.group(1):
        raise ContractSurfaceError("native module ABI does not match manifest")
    expected_hash = manifest.get("artifact_sha256")
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ContractSurfaceError("manifest artifact_sha256 is missing or invalid")
    if _file_sha256(module_path) != expected_hash:
        raise ContractSurfaceError("staged native module does not match manifest artifact_sha256")


def validate_active_artifact(
    module_path: Path,
    manifest: Mapping[str, object],
    active_module_path: Path,
    active_manifest: Mapping[str, object],
) -> None:
    """Verify the atomically installed release copy is exactly the staged artifact."""

    if active_module_path.name != module_path.name:
        raise ContractSurfaceError("active native module ABI/name differs from staged artifact")
    if _file_sha256(active_module_path) != _file_sha256(module_path):
        raise ContractSurfaceError("active native module hash differs from staged artifact")
    if dict(active_manifest) != dict(manifest):
        raise ContractSurfaceError("active native manifest differs from staged manifest")


def _load_native_module(module_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_sdr_native", module_path)
    if spec is None or spec.loader is None:
        raise ContractSurfaceError("cannot load staged standalone native module")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_sdr_native"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--contract-source", type=Path, default=Path(__file__).resolve().parents[1] / "esw_dfl/sdr/contracts.py")
    parser.add_argument("--active-module", type=Path)
    parser.add_argument("--active-manifest", type=Path)
    parser.add_argument("--expect-cuda", action="store_true")
    parser.add_argument("--expect-cpu", action="store_true")
    args = parser.parse_args()
    if args.expect_cuda == args.expect_cpu:
        raise SystemExit("select exactly one of --expect-cuda or --expect-cpu")
    if (args.active_module is None) != (args.active_manifest is None):
        raise SystemExit("--active-module and --active-manifest must be supplied together")

    module_path = args.module.resolve()
    manifest_path = args.manifest.resolve()
    contract_source = args.contract_source.resolve()
    if not module_path.is_file() or not manifest_path.is_file() or not contract_source.is_file():
        raise SystemExit("standalone native module, manifest, or contract source is missing")
    manifest = _read_manifest(manifest_path)
    try:
        validate_manifest(module_path, manifest, args.expect_cuda)
        module = _load_native_module(module_path)
        info = dict(module.build_info())
        if bool(info.get("cuda_compiled")) != args.expect_cuda:
            raise ContractSurfaceError("build_info cuda_compiled does not match manifest")
        if manifest.get("native_version") != info.get("version"):
            raise ContractSurfaceError("native version does not match manifest")
        validate_contract_surface(module, load_contract_expectations(contract_source))
        outcome = dict(module.run_self_test())
        if not outcome.get("ok"):
            raise ContractSurfaceError("native self-test failed: " + str(outcome.get("message", "unknown")))
        if args.active_module is not None and args.active_manifest is not None:
            active_module_path = args.active_module.resolve()
            active_manifest_path = args.active_manifest.resolve()
            if not active_module_path.is_file() or not active_manifest_path.is_file():
                raise ContractSurfaceError("active native module or manifest is missing")
            validate_active_artifact(
                module_path,
                manifest,
                active_module_path,
                _read_manifest(active_manifest_path),
            )
    except ContractSurfaceError as error:
        raise SystemExit(str(error)) from error

    result: dict[str, object] = {
        "module": str(module_path),
        "module_sha256": _file_sha256(module_path),
        "manifest": manifest,
        "build_info": info,
        "contract_schema": {
            "schema": getattr(module, "CONTRACT_SCHEMA_NAME"),
            "schema_version": getattr(module, "CONTRACT_SCHEMA_VERSION"),
        },
        "self_test": outcome,
    }
    if args.active_module is not None:
        result["active_module"] = str(args.active_module.resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
