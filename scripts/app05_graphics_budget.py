"""Thread-bound aggregate nominal graphics admission, not a driver/RSS limit.

A frame reserves its full declared local envelope; between frames only retained
FBO/textures/buffers remain charged. No images, widgets or source owners stored.
Caller-owned CPU payload/readback lifetime outside render is not tracked here.
"""
from contextlib import contextmanager
import threading


class GraphicsBudget:
    def __init__(self, limit_bytes):
        if type(limit_bytes) is not int or limit_bytes <= 0:
            raise ValueError("graphics budget must be positive integer bytes")
        self.limit_bytes = limit_bytes
        self._thread = threading.get_ident()
        self._owners = {}
        self.peak_bytes = self.denials = 0

    def _guard(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError("graphics budget used from another thread")

    def register(self):
        self._guard()
        if len(self._owners) >= 64:
            raise MemoryError("prototype graphics owner bound exceeded")
        token = object()
        self._owners[token] = dict(retained=0, charged=0, active=False)
        return token

    @property
    def charged_bytes(self):
        return sum(row["charged"] for row in self._owners.values())

    def active(self, token):
        self._guard()
        return self._owners[token]["active"]

    def retain(self, token, size):
        self._guard()
        if type(size) is not int or size < 0:
            raise ValueError("invalid retained graphics bytes")
        row = self._owners[token]
        if size > row["charged"]:
            raise RuntimeError("retained storage exceeded admitted frame envelope")
        row["retained"] = size
        if not row["active"]:
            row["charged"] = size

    @contextmanager
    def frame(self, token, envelope):
        self._guard()
        row = self._owners[token]
        if row["active"]:
            raise RuntimeError("reentrant shared-budget frame")
        if type(envelope) is not int or envelope <= 0:
            raise ValueError("invalid graphics frame envelope")
        envelope = max(envelope, row["retained"])
        if self.charged_bytes - row["charged"] + envelope > self.limit_bytes:
            self.denials += 1
            raise MemoryError("aggregate graphics frame budget exceeded")
        row["charged"], row["active"] = envelope, True
        self.peak_bytes = max(self.peak_bytes, self.charged_bytes)
        try:
            yield
        finally:
            row["charged"], row["active"] = row["retained"], False

    def unregister(self, token):
        self._guard()
        row = self._owners[token]
        if row["active"] or row["retained"]:
            raise RuntimeError("cannot forget active or retained graphics storage")
        del self._owners[token]

    def snapshot(self):
        self._guard()
        return dict(limit_bytes=self.limit_bytes, charged_bytes=self.charged_bytes,
                    retained_bytes=sum(row["retained"] for row in self._owners.values()),
                    owners=len(self._owners), active_frames=sum(row["active"] for row in self._owners.values()),
                    peak_bytes=self.peak_bytes, denials=self.denials,
                    scope="declared render envelopes plus retained GL storage; not driver/RSS or caller CPU image lifetime")
