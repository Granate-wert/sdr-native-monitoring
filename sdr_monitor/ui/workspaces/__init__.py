"""Standalone workspace widgets, kept separate from service and domain logic."""

from .calibration import CalibrationWorkspace
from .home import HomeWorkspace
from .live import LiveMonitorWorkspace
from .recording import RecordingWorkspace
from .sweep import SweepWorkspace

__all__ = [
    "CalibrationWorkspace",
    "RecordingWorkspace","HomeWorkspace", "LiveMonitorWorkspace", "SweepWorkspace"]
