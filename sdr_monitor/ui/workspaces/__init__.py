"""Standalone workspace widgets, kept separate from service and domain logic."""

from .home import HomeWorkspace
from .live import LiveMonitorWorkspace
from .sweep import SweepWorkspace

__all__ = ["HomeWorkspace", "LiveMonitorWorkspace", "SweepWorkspace"]
