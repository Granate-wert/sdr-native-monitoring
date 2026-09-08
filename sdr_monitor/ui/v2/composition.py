"""Explicit UI V2 composition helpers; intentionally no AppShell wiring."""

from __future__ import annotations

from collections.abc import Callable

from .view_models.live_view_model import LivePresenterPort, LiveViewModel


def compose_live_view_model(
    presenter: LivePresenterPort,
    *,
    now_ns: Callable[[], int],
) -> LiveViewModel:
    """Create one presentation adapter over an externally owned presenter."""

    return LiveViewModel(presenter, now_ns=now_ns)
