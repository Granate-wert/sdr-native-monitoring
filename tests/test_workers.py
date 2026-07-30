from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.workers import TaskWorker


class WorkerShutdownTests(unittest.TestCase):
    def test_deleted_qt_signal_is_ignored_during_shutdown(self) -> None:
        signal = Mock()
        signal.emit.side_effect = RuntimeError("Signal source has been deleted")
        TaskWorker._emit(signal, "value")
        signal.emit.assert_called_once_with("value")


if __name__ == "__main__":
    unittest.main()
