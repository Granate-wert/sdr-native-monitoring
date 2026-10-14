"""Exact native Waterfall exporter admission and retained-byte accounting."""

import gc
from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from types import ModuleType
import unittest

import numpy as np

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.retained_bytes import retained_arrays, retained_roots
from sdr_monitor.ui.v2.waterfall.protected_storage import (
    WaterfallStorageSupport,
    admit_loaded_waterfall_storage,
)
from tests.native_test_dependencies import native_test_dll_directory

_EXPLICIT_NATIVE = None
_EXPLICIT_SUPPORT = None
_CANONICAL_NATIVE_SLOT = "sdr_monitor._sdr_native"
_MISSING_SLOT = object()


def explicit_native_support(owner):
    """Use one explicitly selected extension under a class-scoped canonical alias."""
    global _EXPLICIT_NATIVE, _EXPLICIT_SUPPORT
    selected = os.environ.get("SDR_APP07_READY_NATIVE")
    if not selected:
        raise unittest.SkipTest("requires explicitly selected 173 accounting native; no fallback")
    path = Path(selected).resolve(strict=True)
    scope = native_test_dll_directory(str(path))
    previous = sys.modules.get(_CANONICAL_NATIVE_SLOT, _MISSING_SLOT)
    owner._native_alias_previous = previous
    owner._native_dll_scope = scope
    owner._native_dll_scope_entered = False
    try:
        scope.__enter__()
        owner._native_dll_scope_entered = True
        if _EXPLICIT_NATIVE is None:
            previous_path = getattr(previous, "__file__", None)
            if (type(previous) is ModuleType and isinstance(previous_path, str)
                    and Path(previous_path).resolve() == path):
                module = previous
            else:
                spec = importlib.util.spec_from_file_location(_CANONICAL_NATIVE_SLOT, path)
                if spec is None or spec.loader is None:
                    raise RuntimeError("explicit native module has no extension loader")
                module = importlib.util.module_from_spec(spec)
                sys.modules[_CANONICAL_NATIVE_SLOT] = module
                spec.loader.exec_module(module)
            if Path(module.__file__).resolve(strict=True) != path:
                raise AssertionError("native loader did not load the explicitly selected file")
            protocol = getattr(module, "WATERFALL_PROTECTED_ACCOUNTING_PROTOCOL_VERSION", None)
            if type(protocol) is not int or protocol != 1:
                raise AssertionError("explicit selected 172 native lacks required 173 accounting protocol")
            support = admit_loaded_waterfall_storage(module)
            if support is None:
                raise AssertionError("explicit selected native failed exact loaded-extension admission")
            _EXPLICIT_NATIVE, _EXPLICIT_SUPPORT = module, support
        else:
            module = _EXPLICIT_NATIVE
            if Path(module.__file__).resolve(strict=True) != path:
                raise AssertionError("selected native changed during this test process")
            sys.modules[_CANONICAL_NATIVE_SLOT] = module
        return _EXPLICIT_NATIVE, _EXPLICIT_SUPPORT
    except BaseException:
        try:
            restore_explicit_native_support(owner)
        except BaseException:
            pass
        raise


def restore_explicit_native_support(owner):
    """Restore the exact prior canonical slot and close this class's DLL scope."""
    previous = getattr(owner, "_native_alias_previous", _MISSING_SLOT)
    scope = getattr(owner, "_native_dll_scope", None)
    scope_entered = getattr(owner, "_native_dll_scope_entered", False)
    try:
        if previous is _MISSING_SLOT:
            sys.modules.pop(_CANONICAL_NATIVE_SLOT, None)
        else:
            sys.modules[_CANONICAL_NATIVE_SLOT] = previous
    finally:
        owner._native_alias_previous = _MISSING_SLOT
        owner._native_dll_scope = None
        owner._native_dll_scope_entered = False
        if scope is not None and scope_entered:
            scope.__exit__(None, None, None)


class WaterfallAccountingAdmissionTests(unittest.TestCase):
    def test_non_extension_and_incoherent_modules_are_refused(self):
        self.assertIsNone(admit_loaded_waterfall_storage(ModuleType("not_native")))
        self.assertFalse(PresentationAllocationBudget(1024).install_waterfall_storage_support(
            WaterfallStorageSupport(ModuleType(_CANONICAL_NATIVE_SLOT), type, type, type),
        ))

    def test_class_fixture_cleanup_restores_existing_or_absent_canonical_slot(self):
        original = sys.modules.get(_CANONICAL_NATIVE_SLOT, _MISSING_SLOT)
        previous = ModuleType("prior_slot")
        exited = []

        class Scope:
            def __exit__(self, *_args):
                exited.append(True)

        try:
            sys.modules[_CANONICAL_NATIVE_SLOT] = ModuleType("temporary_alias")
            owner = SimpleNamespace(
                _native_alias_previous=previous,
                _native_dll_scope=Scope(),
                _native_dll_scope_entered=True,
            )
            restore_explicit_native_support(owner)
            self.assertIs(sys.modules[_CANONICAL_NATIVE_SLOT], previous)
            self.assertEqual(exited, [True])
            sys.modules.pop(_CANONICAL_NATIVE_SLOT, None)
            owner._native_alias_previous = _MISSING_SLOT
            owner._native_dll_scope = None
            restore_explicit_native_support(owner)
            self.assertNotIn(_CANONICAL_NATIVE_SLOT, sys.modules)
        finally:
            if original is _MISSING_SLOT:
                sys.modules.pop(_CANONICAL_NATIVE_SLOT, None)
            else:
                sys.modules[_CANONICAL_NATIVE_SLOT] = original


class ProtectedWaterfallAccountingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native, cls.support = explicit_native_support(cls)

    @classmethod
    def tearDownClass(cls):
        restore_explicit_native_support(cls)

    def storage(self, rows=3, columns=4):
        return self.native._WaterfallProtectedStorage(rows, columns)

    def test_exact_probe_and_per_budget_runtime_identity(self):
        self.assertEqual(self.native.waterfall_storage_types(), (
            self.native._WaterfallProtectedStorage,
            self.native._WaterfallFloatBuffer,
            self.native._WaterfallRevisionBuffer,
        ))
        budget = PresentationAllocationBudget(4096)
        self.assertTrue(budget.install_waterfall_storage_support(self.support))
        self.assertTrue(budget.install_waterfall_storage_support(self.support))
        conflicting = WaterfallStorageSupport(
            ModuleType("sdr_monitor._sdr_native"), self.support.controller_type,
            self.support.float_exporter_type, self.support.revision_exporter_type,
        )
        self.assertFalse(budget.install_waterfall_storage_support(conflicting))
        self.assertIs(budget.waterfall_storage_support().module, self.native)

    def test_forged_support_refuses_before_exporter_or_support_getters(self):
        calls = []

        class GetterSpy:
            @property
            def float_exporter_type(self):
                calls.append("float")
                raise AssertionError("unissued support was inspected")

            @property
            def revision_exporter_type(self):
                calls.append("revision")
                raise AssertionError("unissued support was inspected")

        class DuckExporter:
            @property
            def allocation_nbytes(self):
                calls.append("nbytes")
                raise AssertionError("forged exporter was inspected")

        forged = WaterfallStorageSupport(
            self.native, self.support.controller_type,
            self.support.float_exporter_type, self.support.revision_exporter_type,
        )
        copied = replace(self.support, float_exporter_type=DuckExporter)
        budget = PresentationAllocationBudget(4096)
        self.assertFalse(budget.install_waterfall_storage_support(forged))
        self.assertEqual(retained_roots(DuckExporter(), support=forged), {})
        self.assertEqual(retained_arrays(DuckExporter(), support=forged), {})
        self.assertFalse(budget.install_waterfall_storage_support(copied))
        self.assertEqual(retained_roots(DuckExporter(), support=copied), {})
        self.assertEqual(retained_arrays(DuckExporter(), support=copied), {})
        self.assertFalse(budget.install_waterfall_storage_support(GetterSpy()))
        self.assertEqual(retained_roots(DuckExporter(), support=GetterSpy()), {})
        self.assertEqual(retained_arrays(DuckExporter(), support=GetterSpy()), {})
        self.assertEqual(calls, [])

    def test_full_native_extent_typed_keys_alias_witnesses_and_retirement(self):
        storage = self.storage()
        data = storage.data
        data_second = storage.data
        revisions = storage.row_revisions
        float_exporter = data.base.obj if isinstance(data.base, memoryview) else data.base
        float_exporter_second = (data_second.base.obj if isinstance(data_second.base, memoryview)
                                 else data_second.base)
        revision_exporter = revisions.base.obj if isinstance(revisions.base, memoryview) else revisions.base
        budget = PresentationAllocationBudget(4096)
        self.assertTrue(budget.install_waterfall_storage_support(self.support))
        budget.observe(data, data_second, revisions)
        self.assertEqual(budget.snapshot().observed_bytes, storage.storage_bytes)
        self.assertEqual(float_exporter.allocation_id, float_exporter_second.allocation_id)
        self.assertEqual(sum(retained_arrays(data, revisions, support=self.support).values()),
                         storage.storage_bytes)
        direct_exporter_roots = retained_roots(float_exporter, support=self.support)
        self.assertEqual(set(direct_exporter_roots), {
            (self.native._WaterfallFloatBuffer, float_exporter.allocation_id),
        })
        direct_budget = PresentationAllocationBudget(4096)
        self.assertTrue(direct_budget.install_waterfall_storage_support(self.support))
        direct_budget.observe(float_exporter)
        self.assertEqual(direct_budget.snapshot().observed_bytes, data.nbytes)
        del direct_exporter_roots, direct_budget
        source_budget = PresentationAllocationBudget(storage.storage_bytes)
        self.assertTrue(source_budget.install_waterfall_storage_support(self.support))
        self.assertTrue(source_budget.admit_sources(data, revisions))
        self.assertEqual(source_budget.snapshot().observed_bytes, storage.storage_bytes)
        self.assertFalse(source_budget.admit_sources(np.ones(1, dtype=np.float32)))
        budget.admit_sources(memoryview(float_exporter))
        self.assertEqual(budget.snapshot().observed_bytes, storage.storage_bytes)
        output = np.empty(4, dtype=np.float32)
        reservation = budget.reserve(output.nbytes, revisions)
        reservation.commit(output)
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.assertEqual(budget.snapshot().observed_bytes, storage.storage_bytes + output.nbytes)
        del output
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, storage.storage_bytes)
        roots = retained_roots(data, revisions, support=self.support)
        self.assertEqual(set(roots), {
            (self.native._WaterfallFloatBuffer, float_exporter.allocation_id),
            (self.native._WaterfallRevisionBuffer, revision_exporter.allocation_id),
        })

        alias = np.frombuffer(memoryview(float_exporter), dtype=np.float32, count=1)
        self.assertEqual(alias.nbytes, 4)
        budget.observe(alias)
        del roots, data, float_exporter
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, storage.storage_bytes)
        del alias
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, storage.storage_bytes)
        del data_second, float_exporter_second
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, revisions.nbytes)
        del revisions, revision_exporter, storage
        gc.collect()
        self.assertEqual(budget.snapshot().observed_bytes, 0)

    def test_generic_budget_and_unsupported_publications_keep_legacy_accounting(self):
        array = np.arange(64, dtype=np.float32)
        legacy = retained_roots(array)
        self.assertEqual(retained_roots(memoryview(array)), {})
        budget = PresentationAllocationBudget(4096)
        budget.observe(array)
        self.assertEqual(budget.snapshot().observed_bytes, array.nbytes)
        self.assertEqual(sum(size for size, _aliases in legacy.values()), array.nbytes)
        fake = ModuleType("sdr_monitor._sdr_native")
        fake.WATERFALL_PROTECTED_STORAGE_PROTOCOL_VERSION = 1
        self.assertIsNone(admit_loaded_waterfall_storage(fake))


if __name__ == "__main__":
    unittest.main()
