"""S15 standalone bounded activity-logging tests."""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

from sdr_monitor.activity_log import BoundedJsonlHandler, default_activity_log_path, install_activity_file_logging, log_event


class StandaloneActivityLogTests(unittest.TestCase):
    def test_structured_log_is_persistent_and_capped_to_newest_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.jsonl"
            logger = logging.getLogger(f"sdr-activity-test-{id(self)}")
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = BoundedJsonlHandler(path, max_records=10_000, snapshot_interval_s=0.01)
            logger.addHandler(handler)
            try:
                for index in range(10_025):
                    log_event(
                        logger,
                        "user",
                        "workspace_selected",
                        workspace="live",
                        source=Path("sample.dfl"),
                    )
                handler.flush()
            finally:
                logger.removeHandler(handler)
                handler.close()

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 10_000)
            self.assertEqual(records[0]["details"]["source"], "sample.dfl")
            self.assertEqual(records[-1]["category"], "user")
            self.assertEqual(records[-1]["event"], "workspace_selected")
            self.assertEqual(records[-1]["details"]["workspace"], "live")
            self.assertIn("timestamp", records[-1])
            self.assertEqual(records[-2]["details"]["source"], "sample.dfl")

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

    def test_formatter_handles_path_enum_and_exception(self) -> None:
        from enum import Enum

        class Tone(Enum):
            NEUTRAL = "neutral"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.jsonl"
            logger = logging.getLogger(f"sdr-activity-fmt-{id(self)}")
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = BoundedJsonlHandler(path, max_records=100, snapshot_interval_s=0.01)
            logger.addHandler(handler)
            try:
                try:
                    raise ValueError("boom")
                except ValueError:
                    logger.exception("failure", extra={"event_category": "program", "event_name": "error", "event_details": {"tone": Tone.NEUTRAL}})
                handler.flush()
            finally:
                logger.removeHandler(handler)
                handler.close()

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["category"], "program")
            self.assertEqual(records[0]["event"], "error")
            self.assertEqual(records[0]["details"]["tone"], "neutral")
            self.assertIn("ValueError: boom", records[0]["exception"])

    def test_log_event_without_handler_does_not_raise(self) -> None:
        logger = logging.getLogger(f"sdr-activity-none-{id(self)}")
        logger.propagate = False
        logger.setLevel(logging.INFO)
        log_event(logger, "user", "workspace_selected", workspace="home")

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.jsonl"
            logger = logging.getLogger(f"sdr-activity-install-{id(self)}")
            logger.propagate = False
            logger.setLevel(logging.INFO)
            first = install_activity_file_logging(logger, path=path, max_records=50)
            second = install_activity_file_logging(logger, path=path, max_records=50)
            try:
                self.assertIs(first, second)
                self.assertEqual(len([h for h in logger.handlers if isinstance(h, BoundedJsonlHandler)]), 1)
            finally:
                logger.removeHandler(first)
                first.close()

    def test_close_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.jsonl"
            handler = BoundedJsonlHandler(path, max_records=100)
            handler.close()
            handler.close()

    def test_default_path_uses_override(self) -> None:
        import os

        try:
            os.environ["SDR_MONITOR_ACTIVITY_LOG"] = str(Path("custom") / "dir" / "activity.jsonl")
            resolved = default_activity_log_path()
            self.assertTrue(str(resolved).endswith("custom\\dir\\activity.jsonl") or str(resolved).endswith("custom/dir/activity.jsonl"))
        finally:
            os.environ.pop("SDR_MONITOR_ACTIVITY_LOG", None)


if __name__ == "__main__":
    unittest.main()