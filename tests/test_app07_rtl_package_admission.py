"""Static RTL external-runtime packaging contracts; no vendor DLL load/RX."""

import hashlib
import json
import subprocess
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from scripts.preflight_sdr_rtl_runtime import (
    MAX_NOTICE,
    REQUIRED_EXPORTS,
    _pe_image,
    _safe_sibling,
    validate_rtl_inputs,
)
from scripts import preflight_sdr_rtl_runtime
from scripts.sdr_frozen_runtime_payload import deduplicate_rtl_runtime_binaries, deduplicate_shared_runtime_binaries
from scripts import freeze_sdr_official
from scripts.freeze_sdr_official import checked_rtl_spec_source
from sdr_monitor.frozen_shared_runtime import SHARED_COMPONENTS
from scripts.verify_sdr_frozen_rtl_runtime import verify_frozen_rtl_runtime

SOURCE_COMMIT = "9bd31a9dfaba06ddd013e6bcbe7c8501602e1bad"
EMPTY_ENTRIES: list[dict[str, str]] = []
SOURCE_HASH = hashlib.sha256(b"[]").hexdigest()
SOURCE_SNAPSHOT = {"schema": "sdr-source-snapshot-v1", "source_sha256": SOURCE_HASH, "entries": EMPTY_ENTRIES}


def _pe(
    imports: tuple[str, ...],
    *,
    machine: int = 0x8664,
    magic: int = 0x20B,
    is_dll: bool = True,
    delay: bool = False,
    forwarded: bool = False,
    invalid_target: str | None = None,
    wrong_case_export: bool = False,
    include_exports: bool = False,
) -> bytes:
    """Small deterministic one-section PE fixture, parsed only as bytes."""
    data = bytearray(0x1200)
    data[:2] = b"MZ"
    data[0x3C:0x40] = (0x80).to_bytes(4, "little")
    data[0x80:0x84] = b"PE\0\0"
    data[0x84:0x86] = machine.to_bytes(2, "little")
    data[0x86:0x88] = (1).to_bytes(2, "little")
    data[0x94:0x96] = (240).to_bytes(2, "little")
    data[0x96:0x98] = (0x2000 if is_dll else 0).to_bytes(2, "little")
    opt = 0x98
    data[opt : opt + 2] = magic.to_bytes(2, "little")
    data[opt + 108 : opt + 112] = (16).to_bytes(4, "little")
    section = opt + 240
    data[section : section + 8] = b".rdata\0\0"
    data[section + 8 : section + 12] = (0x1000).to_bytes(4, "little")
    data[section + 12 : section + 16] = (0x1000).to_bytes(4, "little")
    data[section + 16 : section + 20] = (0x1000).to_bytes(4, "little")
    data[section + 20 : section + 24] = (0x200).to_bytes(4, "little")
    raw = 0x200
    next_rva = 0x1080
    if imports:
        data[opt + 120 : opt + 124] = (0x1000).to_bytes(4, "little")
        import_size = (len(imports) + 1) * 20
        data[opt + 124 : opt + 128] = import_size.to_bytes(4, "little")
        for i, name in enumerate(imports):
            descriptor = raw + i * 20
            data[descriptor + 12 : descriptor + 16] = next_rva.to_bytes(4, "little")
            encoded = name.encode("ascii") + b"\0"
            offset = raw + next_rva - 0x1000
            data[offset : offset + len(encoded)] = encoded
            next_rva += len(encoded)
    if delay:
        data[opt + 112 + 13 * 8 : opt + 112 + 13 * 8 + 4] = (0x1100).to_bytes(4, "little")
        data[opt + 112 + 13 * 8 + 4 : opt + 112 + 13 * 8 + 8] = (8).to_bytes(4, "little")
    if include_exports:
        data[opt + 112 : opt + 116] = (0x1180).to_bytes(4, "little")
        data[opt + 116 : opt + 120] = (0x300).to_bytes(4, "little")
        export_at = raw + 0x180
        count = len(REQUIRED_EXPORTS)
        data[export_at + 20 : export_at + 24] = count.to_bytes(4, "little")
        data[export_at + 24 : export_at + 28] = count.to_bytes(4, "little")
        data[export_at + 28 : export_at + 32] = (0x11C0).to_bytes(4, "little")
        data[export_at + 32 : export_at + 36] = (0x1200).to_bytes(4, "little")
        data[export_at + 36 : export_at + 40] = (0x1240).to_bytes(4, "little")
        for i in range(count):
            target = 0x1800 + i
            if invalid_target == "zero" and i == 0:
                target = 0
            elif invalid_target == "unmapped" and i == 0:
                target = 0x3000
            data[raw + 0x1C0 + i * 4 : raw + 0x1C4 + i * 4] = target.to_bytes(4, "little")
            data[raw + 0x240 + i * 2 : raw + 0x242 + i * 2] = (i).to_bytes(2, "little")
            # Place each exported name in the remaining section.
            export_name = sorted(REQUIRED_EXPORTS)[i]
            if wrong_case_export and i == 0:
                export_name = export_name.upper()
            name = export_name.encode("ascii") + b"\0"
            name_rva = 0x1400 + i * 64
            data[raw + 0x200 + i * 4 : raw + 0x204 + i * 4] = name_rva.to_bytes(4, "little")
            begin = raw + name_rva - 0x1000
            data[begin : begin + len(name)] = name
            if forwarded and i == 0:
                data[raw + 0x1C0 : raw + 0x1C4] = (0x1190).to_bytes(4, "little")
    return bytes(data)


class RtlPackageAdmissionTests(unittest.TestCase):
    def test_static_pe_parser_rejects_arch_delay_and_forwarder(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "rtlsdr.dll"
            for data, text in (
                (_pe((), machine=0x14C, include_exports=True), "x64"),
                (_pe((), is_dll=False, include_exports=True), "x64"),
                (_pe((), delay=True, include_exports=True), "delay"),
                (_pe((), forwarded=True, include_exports=True), "forwarded"),
                (_pe((), invalid_target="zero", include_exports=True), "no function target"),
                (_pe((), invalid_target="unmapped", include_exports=True), "RVA is outside"),
                (_pe((), wrong_case_export=True, include_exports=True), "missing required"),
            ):
                path.write_bytes(data)
                with self.subTest(reason=text), self.assertRaisesRegex(ValueError, text):
                    _pe_image(path, data)
            path.write_bytes(_pe((), include_exports=True)[:300])
            with self.assertRaisesRegex(ValueError, r"PE32\+"):
                _pe_image(path, path.read_bytes())

    def test_notice_limits_reserved_names_and_path_escape_fail_before_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "NOTICE.txt").write_bytes(b"n" * (MAX_NOTICE + 1))
            with self.assertRaisesRegex(ValueError, "size bound"):
                _safe_sibling(root, "NOTICE.txt", MAX_NOTICE)
            for bad in ("../NOTICE.txt", "C:evil.dll", "CON.dll", "NUL.txt"):
                with self.subTest(name=bad), self.assertRaises(ValueError):
                    _safe_sibling(root, bad, MAX_NOTICE)

    def test_input_preflight_rejects_missing_wrong_hash_bad_alias_and_bad_json(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, native, libiio, out = (root / n for n in ("runtime", "native", "libiio", "out"))
            for folder in (runtime, native, libiio):
                folder.mkdir()
            dll = _pe(("kernel32.dll",), include_exports=True)
            (runtime / "rtlsdr.dll").write_bytes(dll)
            notice = b"vendor notice\n"
            (runtime / "NOTICE.txt").write_bytes(notice)
            (libiio / "libusb-1.0.dll").write_bytes(b"selected usb")
            module = native / "_sdr_native.cp313-win_amd64.pyd"
            module.write_bytes(b"mock native image")
            nmanifest = native / "native_build_manifest.json"
            nmanifest.write_text("{}", encoding="utf-8")
            manifest = runtime / "input.json"
            valid = {
                "schema": "app07-rtl-runtime-input-v1",
                "library": hashlib.sha256(dll).hexdigest(),
                "dependencies": {},
                "origin": "local archive",
                "notices": {"NOTICE.txt": hashlib.sha256(notice).hexdigest()},
            }
            manifest.write_text(json.dumps(valid), encoding="utf-8")
            snapshot_path = root / "source_inputs.json"
            snapshot_path.write_text(json.dumps(SOURCE_SNAPSHOT), encoding="utf-8")
            common = (module, nmanifest, runtime, manifest, libiio, snapshot_path)
            with (
                patch("scripts.preflight_sdr_rtl_runtime.validate_manifest"),
                patch("scripts.preflight_sdr_rtl_runtime._load_native_module", return_value=object()),
                patch("scripts.preflight_sdr_rtl_runtime.validate_rtl_factory"),
                patch(
                    "scripts.preflight_sdr_rtl_runtime._read_manifest",
                    return_value={
                        "rtl_official_compiled": True,
                        "rtl_control_contract_version": 1,
                        "source_commit": SOURCE_COMMIT,
                    },
                ),
                patch("scripts.preflight_sdr_rtl_runtime.snapshot", return_value=SOURCE_SNAPSHOT),
                patch("scripts.preflight_sdr_rtl_runtime.subprocess.check_output", return_value=SOURCE_COMMIT),
            ):
                admitted = validate_rtl_inputs(*common)
                self.assertTrue(admitted["passed"])
                self.assertEqual(admitted["runtime_sha256"]["rtlsdr.dll"], hashlib.sha256(dll).hexdigest())
                (runtime / "rtlsdr.dll").unlink()
                with self.assertRaisesRegex(ValueError, "regular non-symlink"):
                    validate_rtl_inputs(*common)
                (runtime / "rtlsdr.dll").write_bytes(dll)
                for bad in (
                    {**valid, "library": "0" * 64},
                    {**valid, "dependencies": {"RTLSdr.DLL": "0" * 64}},
                    {**valid, "schema": "wrong"},
                ):
                    manifest.write_text(json.dumps(bad), encoding="utf-8")
                    with self.subTest(bad=bad), self.assertRaises(ValueError):
                        validate_rtl_inputs(*common)
                manifest.write_text('{"schema":"app07-rtl-runtime-input-v1","schema":"duplicate"}', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "duplicate"):
                    validate_rtl_inputs(*common)

    def test_dependency_closure_system_shadow_and_shared_usb_are_exact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "rt"
            native = root / "native"
            libiio = root / "iio"
            runtime.mkdir()
            native.mkdir()
            libiio.mkdir()
            module = native / "_sdr_native.pyd"
            module.write_bytes(b"native")
            nmanifest = native / "native_build_manifest.json"
            nmanifest.write_text("{}", encoding="utf-8")
            (runtime / "NOTICE.txt").write_bytes(b"notice")
            (libiio / "libusb-1.0.dll").write_bytes(b"selected usb")
            notice_hash = hashlib.sha256(b"notice").hexdigest()

            snapshot_path = root / "source_inputs.json"
            snapshot_path.write_text(json.dumps(SOURCE_SNAPSHOT), encoding="utf-8")

            def preflight(primary_imports, dependencies):
                primary = _pe(tuple(primary_imports), include_exports=True)
                (runtime / "rtlsdr.dll").write_bytes(primary)
                for name, imports in dependencies.items():
                    (runtime / name).write_bytes(_pe(tuple(imports)))
                spec = {
                    "schema": "app07-rtl-runtime-input-v1",
                    "library": hashlib.sha256(primary).hexdigest(),
                    "dependencies": {
                        name: hashlib.sha256((runtime / name).read_bytes()).hexdigest() for name in dependencies
                    },
                    "origin": "fixture",
                    "notices": {"NOTICE.txt": notice_hash},
                }
                path = runtime / "input.json"
                path.write_text(json.dumps(spec), encoding="utf-8")
                with (
                    patch("scripts.preflight_sdr_rtl_runtime.validate_manifest"),
                    patch("scripts.preflight_sdr_rtl_runtime._load_native_module", return_value=object()),
                    patch("scripts.preflight_sdr_rtl_runtime.validate_rtl_factory"),
                    patch(
                        "scripts.preflight_sdr_rtl_runtime._read_manifest",
                        return_value={
                            "rtl_official_compiled": True,
                            "rtl_control_contract_version": 1,
                            "source_commit": SOURCE_COMMIT,
                        },
                    ),
                    patch("scripts.preflight_sdr_rtl_runtime.snapshot", return_value=SOURCE_SNAPSHOT),
                    patch("scripts.preflight_sdr_rtl_runtime.subprocess.check_output", return_value=SOURCE_COMMIT),
                ):
                    return validate_rtl_inputs(module, nmanifest, runtime, path, libiio, snapshot_path)

            with self.assertRaisesRegex(ValueError, "unused"):
                preflight(("kernel32.dll",), {"helper.dll": ("kernel32.dll",)})
            with self.assertRaisesRegex(ValueError, "unused"):
                preflight(("helper.dll",), {"helper.dll": ("cycle.dll",), "cycle.dll": ("helper.dll",), "unused.dll": ()})
            with self.assertRaisesRegex(ValueError, "not declared"):
                preflight(("unknown.dll",), {})
            with self.assertRaisesRegex(ValueError, "shared libusb collision"):
                preflight(("libusb-1.0.dll",), {"libusb-1.0.dll": ("kernel32.dll",)})
            with self.assertRaisesRegex(ValueError, "shadowed"):
                (runtime / "kernel32.dll").write_bytes(b"shadow")
                preflight(("kernel32.dll",), {})

    def test_rtl_toc_checks_all_duplicates_and_retains_one_canonical_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload = root / "rtlsdr.dll"
            payload.write_bytes(b"rtl")
            notice = root / "NOTICE.txt"
            notice.write_bytes(b"notice")
            selected = {"rtlsdr.dll": payload}
            notices = {"NOTICE.txt": notice}
            entries = [
                ("sdr_monitor/rtlsdr.dll", str(payload), "BINARY"),
                ("rtlsdr.dll", str(payload), "BINARY"),
                ("sdr_monitor/rtl_notices/NOTICE.txt", str(notice), "DATA"),
            ]
            result = deduplicate_rtl_runtime_binaries(entries, selected, notices)
            self.assertEqual(
                [item[0] for item in result], ["sdr_monitor/rtlsdr.dll", "sdr_monitor/rtl_notices/NOTICE.txt"]
            )
            wrong = root / "other.dll"
            wrong.write_bytes(b"different")
            with self.assertRaisesRegex(ValueError, "incompatible"):
                deduplicate_rtl_runtime_binaries(
                    entries + [("root/rtlsdr.dll", str(wrong), "BINARY")], selected, notices
                )
            with self.assertRaisesRegex(ValueError, "incompatible"):
                deduplicate_rtl_runtime_binaries(
                    [("sdr_monitor/rtlsdr.dll", str(payload), "DATA"), entries[2]], selected, notices
                )
            with self.assertRaisesRegex(ValueError, "lost"):
                deduplicate_rtl_runtime_binaries(entries[1:], selected, notices)

    def test_preflight_outputs_are_refuse_overwrite_and_emit_runtime_sidecar(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "out"
            output.mkdir()
            report_path = output / "rtl_runtime_inputs.json"
            report_path.write_text("preserve", encoding="utf-8")
            fake = {
                "schema": "app07-rtl-runtime-input-v1",
                "passed": True,
                "source_commit": SOURCE_COMMIT,
                "source_sha256": SOURCE_HASH,
                "native_sha256": "a" * 64,
                "native_source_commit": "abc",
                "native_manifest_sha256": "b" * 64,
                "input_manifest_sha256": "c" * 64,
                "origin": "fixture",
                "library": "rtlsdr.dll",
                "runtime_sha256": {"rtlsdr.dll": "d" * 64, "helper.dll": "e" * 64},
                "notices_sha256": {"NOTICE.txt": "f" * 64},
                "imports": {},
                "scope": "static input admission only; no ABI, RX, SDK load or license approval",
                "_dependencies": {"helper.dll": "e" * 64},
            }
            args = [
                "preflight",
                "--module",
                "m",
                "--native-manifest",
                "n",
                "--runtime-directory",
                "r",
                "--runtime-manifest",
                "i",
                "--libiio-directory",
                "l",
                "--source-snapshot",
                "s",
                "--output-directory",
                str(output),
            ]
            with (
                patch.object(sys, "argv", args),
                patch.object(preflight_sdr_rtl_runtime, "validate_rtl_inputs", return_value=fake),
            ):
                with self.assertRaises(SystemExit):
                    preflight_sdr_rtl_runtime.main()
            self.assertEqual(report_path.read_text(encoding="utf-8"), "preserve")
            report_path.unlink()
            with (
                patch.object(sys, "argv", args),
                patch.object(preflight_sdr_rtl_runtime, "validate_rtl_inputs", return_value=fake),
            ):
                self.assertEqual(preflight_sdr_rtl_runtime.main(), 0)
            sidecar = json.loads((output / "rtl_external_runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar, {"library": "d" * 64, "dependencies": {"helper.dll": "e" * 64}})

    def test_checked_rtl_spec_preserves_console_and_routes_both_runtime_shapes(self):
        source = "a = Analysis([])\npyz = PYZ(a.pure)\nexe = EXE(pyz, console=True, hide_console='hide-early')\n"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dll = root / "rtlsdr.dll"
            dll.write_bytes(b"x")
            notice = root / "NOTICE.md"
            notice.write_bytes(b"notice")
            generated = checked_rtl_spec_source(source, root, {"rtlsdr.dll": dll}, {"NOTICE.md": notice})
            self.assertIn("deduplicate_rtl_runtime_binaries", generated)
            self.assertIn("console=True", generated)
            self.assertIn("hide_console='hide-early'", generated)
            with self.assertRaisesRegex(ValueError, "console policy"):
                checked_rtl_spec_source(
                    source.replace("console=True", "console=False"), root, {"rtlsdr.dll": dll}, {"NOTICE.md": notice}
                )

            shared = {}
            binaries = []
            for name in SHARED_COMPONENTS:
                path = root / name
                path.write_bytes((name + " payload").encode())
                shared[name] = path
                binaries.append(("sdr_monitor/" + name, str(path), "BINARY"))
            selected = {**shared, "rtlsdr.dll": dll}
            binaries.append(("sdr_monitor/rtlsdr.dll", str(dll), "BINARY"))
            notices = {"NOTICE.md": notice}
            toc_source = "a = Analysis([])\npyz = PYZ(a.pure)\nexe = EXE(pyz, console=True, hide_console='hide-early')\n"
            generated = checked_rtl_spec_source(toc_source, root, selected, notices, shared)

            class AnalysisResult:
                binaries: list[tuple[str, str, str]]
                datas: list[tuple[str, str, str]]
                pure = []
                datas = [("sdr_monitor/rtl_notices/NOTICE.md", str(notice), "DATA")]

            analyzed = []
            def make_analysis(*args, **kwargs):
                value = AnalysisResult()
                value.binaries = list(binaries)
                analyzed.append(value)
                return value

            namespace = {
                "Analysis": make_analysis,
                "PYZ": lambda *args, **kwargs: object(),
                "EXE": lambda *args, **kwargs: object(),
                "deduplicate_shared_runtime_binaries": deduplicate_shared_runtime_binaries,
                "deduplicate_rtl_runtime_binaries": deduplicate_rtl_runtime_binaries,
            }
            exec(compile(generated, "generated-test.spec", "exec"), namespace)
            self.assertEqual(
                {entry[0] for entry in analyzed[0].binaries},
                {"sdr_monitor/" + name for name in selected},
            )
            self.assertEqual(analyzed[0].datas[0][0], "sdr_monitor/rtl_notices/NOTICE.md")

    def test_preflight_and_verifier_cli_bootstrap_from_neutral_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            scripts = Path(__file__).resolve().parents[1] / "scripts"
            for name in ("preflight_sdr_rtl_runtime.py", "verify_sdr_frozen_rtl_runtime.py"):
                result = subprocess.run(
                    [sys.executable, "-I", str(scripts / name), "--help"],
                    cwd=temp,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())

    def test_freeze_rejects_duplicate_and_oversized_metadata_before_freezer(self):
        for metadata_case in (
            "duplicate-report", "oversized-report", "duplicate-sidecar", "oversized-sidecar"
        ):
            with self.subTest(metadata_case=metadata_case), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                native_dir, libiio_dir, runtime_dir = (root / name for name in ("native", "libiio", "runtime"))
                for directory in (native_dir, libiio_dir, runtime_dir):
                    directory.mkdir()
                module = native_dir / "_sdr_native.cp313-win_amd64.pyd"
                module.write_bytes(b"module")
                (native_dir / "native_build_manifest.json").write_text("{}", encoding="utf-8")
                report = root / "rtl_runtime_inputs.json"
                sidecar = root / "rtl_external_runtime.json"
                admission = {"runtime_sha256": {"rtlsdr.dll": "d" * 64}, "_runtime_files": {}, "_notice_files": {}}
                report.write_text(json.dumps({"runtime_sha256": admission["runtime_sha256"]}), encoding="utf-8")
                sidecar.write_text(json.dumps({"library": "d" * 64, "dependencies": {}}), encoding="utf-8")
                target = report if "report" in metadata_case else sidecar
                if metadata_case.startswith("duplicate"):
                    target.write_text('{"runtime_sha256":{},"runtime_sha256":{}}', encoding="utf-8")
                else:
                    target.write_bytes(b" " * (freeze_sdr_official.MAX_RTL_METADATA + 1))
                with (
                    patch.object(freeze_sdr_official, "_read_manifest", return_value={"rtl_official_compiled": True}),
                    patch("scripts.preflight_sdr_native_build.validate_manifest"),
                    patch("scripts.preflight_sdr_native_build.validate_rtl_factory"),
                    patch("scripts.preflight_sdr_native_build._load_native_module", return_value=object()),
                    patch("scripts.preflight_sdr_rtl_runtime.validate_rtl_inputs", return_value=admission),
                    patch.object(freeze_sdr_official, "verify_freezer_version") as freezer_sentinel,
                ):
                    expected = "duplicate" if metadata_case.startswith("duplicate") else "size bound"
                    with self.assertRaisesRegex(ValueError, expected):
                        freeze_sdr_official.freeze_rtl(
                            root, native_dir, libiio_dir, root / "dist", root / "build",
                            runtime_dir, root / "input.json", report, sidecar, root / "source.json",
                            include_hackrf=False,
                        )
                    freezer_sentinel.assert_not_called()

    def test_release_powershell_requires_explicit_inputs_before_tool_resolution(self):
        import os

        if os.name != "nt":
            self.skipTest("PowerShell entry guard is Windows-only")
        script = (Path(__file__).resolve().parents[1] / "build_sdr_release.ps1").read_text(encoding="utf-8")
        self.assertIn("$rtlInputRequested -and (-not $RtlRuntimeDirectory -or -not $RtlRuntimeManifest)", script)
        self.assertIn("if ($rtlInputRequested) {", script)
        self.assertIn("if ($EnableRtlOfficial) { @('--add-data', \"$freezeNativeManifestPath;sdr_monitor\") }", script)

    def test_frozen_verifier_binds_manifest_report_sidecar_notice_and_native(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "_internal" / "sdr_monitor"
            notices = runtime / "rtl_notices"
            notices.mkdir(parents=True)
            native = runtime / "_sdr_native.cp313-win_amd64.pyd"
            native.write_bytes(b"module")
            nmanifest = runtime / "native_build_manifest.json"
            nmanifest.write_text(
                json.dumps({"rtl_official_compiled": True, "rtl_control_contract_version": 1, "source_commit": SOURCE_COMMIT}),
                encoding="utf-8",
            )
            rtl_bytes = _pe(("kernel32.dll",), include_exports=True)
            dll = runtime / "rtlsdr.dll"
            dll.write_bytes(rtl_bytes)
            notice = notices / "NOTICE.txt"
            notice.write_bytes(b"notice")
            hashes = {"rtlsdr.dll": hashlib.sha256(rtl_bytes).hexdigest()}
            notice_hash = {"NOTICE.txt": hashlib.sha256(b"notice").hexdigest()}
            (runtime / "rtl_external_runtime.json").write_text(
                json.dumps({"library": hashes["rtlsdr.dll"], "dependencies": {}}), encoding="utf-8"
            )
            (root / "source_inputs.json").write_text(json.dumps(SOURCE_SNAPSHOT), encoding="utf-8")
            (root / "build_provenance.json").write_text(
                json.dumps({
                    "schema": "sdr-pipeline-provenance-v1",
                    "source_sha256": SOURCE_HASH,
                    "source_commit": SOURCE_COMMIT,
                    "native_sha256": hashlib.sha256(b"module").hexdigest(),
                    "rtl_official_requested": True,
                    "rtl_source_commit": SOURCE_COMMIT,
                    "rtl_runtime_input_sha256": "a" * 64,
                }), encoding="utf-8"
            )
            report = {
                "schema": "app07-rtl-runtime-input-v1",
                "passed": True,
                "source_commit": SOURCE_COMMIT,
                "source_sha256": SOURCE_HASH,
                "native_sha256": hashlib.sha256(b"module").hexdigest(),
                "native_source_commit": SOURCE_COMMIT,
                "native_manifest_sha256": hashlib.sha256(nmanifest.read_bytes()).hexdigest(),
                "input_manifest_sha256": "a" * 64,
                "origin": "local source",
                "library": "rtlsdr.dll",
                "runtime_sha256": hashes,
                "notices_sha256": notice_hash,
                "imports": {"rtlsdr.dll": ["kernel32.dll"]},
                "scope": "static input admission only; no ABI, RX, SDK load or license approval",
            }
            (root / "rtl_runtime_inputs.json").write_text(json.dumps(report), encoding="utf-8")
            release = root / "release_manifest.json"
            release.write_text("{}", encoding="utf-8")
            with (
                patch("scripts.verify_sdr_frozen_rtl_runtime.verify_manifest", return_value={"files": []}),
                patch("scripts.verify_sdr_frozen_rtl_runtime.validate_manifest"),
            ):
                result = verify_frozen_rtl_runtime(root, release, "0.16.10")
                self.assertFalse(result["rx_attempted"])
                report_path = root / "rtl_runtime_inputs.json"
                report_bytes = report_path.read_bytes()
                provenance_path = root / "build_provenance.json"
                provenance_bytes = provenance_path.read_bytes()
                provenance_path.write_bytes(b"\xef\xbb\xbf" + provenance_bytes)
                self.assertFalse(verify_frozen_rtl_runtime(root, release, "0.16.10")["rx_attempted"])
                provenance_path.write_bytes(provenance_bytes)
                report_path.write_bytes(b"\xef\xbb\xbf" + report_bytes)
                with self.assertRaises(json.JSONDecodeError):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")
                report_path.write_bytes(report_bytes)
                mutated = dict(report)
                mutated["source_sha256"] = "0" * 64
                report_path.write_text(json.dumps(mutated), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "provenance"):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")
                report_path.write_bytes(report_bytes)
                sidecar_path = runtime / "rtl_external_runtime.json"
                sidecar_bytes = sidecar_path.read_bytes()
                sidecar_path.write_bytes(b"\xef\xbb\xbf" + sidecar_bytes)
                with self.assertRaises(json.JSONDecodeError):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")
                sidecar_path.write_bytes(sidecar_bytes)
                sidecar_path.write_text(json.dumps({"library": "0" * 64, "dependencies": {}}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "sidecar differs"):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")
                sidecar_path.write_bytes(sidecar_bytes)
                notice.write_bytes(b"mutated notice")
                with self.assertRaisesRegex(ValueError, "notice hash differs"):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")
                notice.write_bytes(b"notice")
                source_path = root / "source_inputs.json"
                source_bytes = source_path.read_bytes()
                source_mutated = {**SOURCE_SNAPSHOT, "source_sha256": "0" * 64}
                source_path.write_text(json.dumps(source_mutated), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "provenance"):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")
                source_path.write_bytes(source_bytes)
                nmanifest_bytes = nmanifest.read_bytes()
                nmanifest.write_text(json.dumps({
                    "rtl_official_compiled": True, "rtl_control_contract_version": 1,
                    "source_commit": "0" * 40,
                }), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "not bound"):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")
                nmanifest.write_bytes(nmanifest_bytes)
                too_many = dict(report)
                too_many["runtime_sha256"] = {**hashes, **{f"extra{i}.dll": "0" * 64 for i in range(9)}}
                report_path.write_text(json.dumps(too_many), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "closure is absent"):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")
                report_path.write_bytes(report_bytes)
                duplicate = root / "root"
                duplicate.mkdir()
                (duplicate / "rtlsdr.dll").write_bytes(rtl_bytes)
                with self.assertRaisesRegex(ValueError, "duplicated"):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")

    def test_frozen_verifier_rejects_unreachable_cycle_and_provenance_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / "_internal" / "sdr_monitor"
            runtime.mkdir(parents=True)
            native = runtime / "_sdr_native.cp313-win_amd64.pyd"
            native.write_bytes(b"module")
            nmanifest = runtime / "native_build_manifest.json"
            nmanifest.write_text(json.dumps({
                "rtl_official_compiled": True, "rtl_control_contract_version": 1, "source_commit": SOURCE_COMMIT
            }), encoding="utf-8")
            primary = _pe(("kernel32.dll",), include_exports=True)
            helper = _pe(("cycle.dll",))
            cycle = _pe(("helper.dll",))
            (runtime / "rtlsdr.dll").write_bytes(primary)
            (runtime / "helper.dll").write_bytes(helper)
            (runtime / "cycle.dll").write_bytes(cycle)
            notice_dir = runtime / "rtl_notices"
            notice_dir.mkdir()
            (notice_dir / "NOTICE.txt").write_bytes(b"notice")
            hashes = {name: hashlib.sha256(data).hexdigest() for name, data in {
                "rtlsdr.dll": primary, "helper.dll": helper, "cycle.dll": cycle
            }.items()}
            report = {
                "schema": "app07-rtl-runtime-input-v1", "passed": True,
                "source_commit": SOURCE_COMMIT, "source_sha256": SOURCE_HASH,
                "native_sha256": hashlib.sha256(b"module").hexdigest(),
                "native_source_commit": SOURCE_COMMIT,
                "native_manifest_sha256": hashlib.sha256(nmanifest.read_bytes()).hexdigest(),
                "input_manifest_sha256": "a" * 64, "origin": "fixture", "library": "rtlsdr.dll",
                "runtime_sha256": hashes, "notices_sha256": {"NOTICE.txt": hashlib.sha256(b"notice").hexdigest()},
                "imports": {"rtlsdr.dll": ["kernel32.dll"], "helper.dll": ["cycle.dll"], "cycle.dll": ["helper.dll"]},
                "scope": "static input admission only; no ABI, RX, SDK load or license approval",
            }
            (root / "rtl_runtime_inputs.json").write_text(json.dumps(report), encoding="utf-8")
            (runtime / "rtl_external_runtime.json").write_text(json.dumps({
                "library": hashes["rtlsdr.dll"], "dependencies": {"helper.dll": hashes["helper.dll"], "cycle.dll": hashes["cycle.dll"]}
            }), encoding="utf-8")
            (root / "source_inputs.json").write_text(json.dumps(SOURCE_SNAPSHOT), encoding="utf-8")
            provenance = {
                "schema": "sdr-pipeline-provenance-v1", "source_sha256": SOURCE_HASH,
                "source_commit": SOURCE_COMMIT,
                "native_sha256": report["native_sha256"], "rtl_official_requested": True,
                "rtl_source_commit": SOURCE_COMMIT, "rtl_runtime_input_sha256": "a" * 64,
            }
            provenance_path = root / "build_provenance.json"
            provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
            release = root / "release_manifest.json"
            release.write_text("{}", encoding="utf-8")
            with (
                patch("scripts.verify_sdr_frozen_rtl_runtime.verify_manifest", return_value={"files": []}),
                patch("scripts.verify_sdr_frozen_rtl_runtime.validate_manifest"),
            ):
                with self.assertRaisesRegex(ValueError, "closure"):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")
                provenance["rtl_source_commit"] = "0" * 40
                provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "provenance"):
                    verify_frozen_rtl_runtime(root, release, "0.16.10")


if __name__ == "__main__":
    unittest.main()
