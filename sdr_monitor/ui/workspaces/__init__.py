"""Standalone workspace widgets, kept separate from service and domain logic."""

from .calibration import CalibrationWorkspace
from .home import HomeWorkspace
from .live import LiveMonitorWorkspace
from .sweep import SweepWorkspace

__all__ = [
    "CalibrationWorkspace","HomeWorkspace", "LiveMonitorWorkspace", "SweepWorkspace"]
