"""Source identity must follow bytes, additions and deletions, not Git HEAD."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("snapshot", Path(__file__).resolve().parents[1] / "scripts/sdr_source_snapshot.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SourceSnapshotTests(unittest.TestCase):
    def test_changes_additions_deletions_and_generated_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for tree in MODULE.TREES:
                (root / tree).mkdir(parents=True)
            for entry in MODULE.ENTRY_FILES:
                (root / entry).write_text("baseline", encoding="utf-8")
            source = root / "sdr_monitor/example.py"
            source.write_text("original", encoding="utf-8")
            baseline = MODULE.snapshot(root)
            MODULE.verify(root, baseline)
            source.write_text("modified", encoding="utf-8")
            with self.assertRaises(ValueError):
                MODULE.verify(root, baseline)
            source.write_text("original", encoding="utf-8")
            added = root / "native/sdr_core/untracked.cpp"
            added.write_text("untracked", encoding="utf-8")
            with self.assertRaises(ValueError):
                MODULE.verify(root, baseline)
            added.unlink()
            generated = root / "native/sdr_core/out"
            generated.mkdir()
            (generated / "generated.cpp").write_text("ignored", encoding="utf-8")
            MODULE.verify(root, baseline)
            source.unlink()
            with self.assertRaises(ValueError):
                MODULE.verify(root, baseline)

    def test_missing_required_input_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                MODULE.snapshot(Path(directory))
