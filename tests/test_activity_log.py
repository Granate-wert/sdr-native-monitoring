from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

from esw_dfl.activity_log import BoundedJsonlHandler, log_event


class ActivityLogTests(unittest.TestCase):
    def test_structured_log_is_persistent_and_capped_to_newest_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.jsonl"
            logger = logging.getLogger(f"activity-test-{id(self)}")
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = BoundedJsonlHandler(path, max_records=10_000, snapshot_interval_s=0.01)
            logger.addHandler(handler)
            try:
                for index in range(10_025):
                    log_event(
                        logger,
                        "user",
                        "frame_selected",
                        frame=index,
                        source=Path("sample.dfl"),
                    )
                handler.flush()
            finally:
                logger.removeHandler(handler)
                handler.close()

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 10_000)
            self.assertEqual(records[0]["details"]["frame"], 25)
            self.assertEqual(records[-1]["details"]["frame"], 10_024)
            self.assertEqual(records[-1]["category"], "user")
            self.assertEqual(records[-1]["event"], "frame_selected")
            self.assertEqual(records[-1]["details"]["source"], "sample.dfl")
            self.assertIn("timestamp", records[-1])

    def test_existing_oversized_file_is_trimmed_atomically_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.jsonl"
            path.write_text(
                "".join(f'{{"record":{index}}}\n' for index in range(12)),
                encoding="utf-8",
            )
            handler = BoundedJsonlHandler(path, max_records=5)
            handler.close()
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([item["record"] for item in records], [7, 8, 9, 10, 11])
            self.assertFalse(path.with_name(path.name + ".part").exists())


if __name__ == "__main__":
    unittest.main()
