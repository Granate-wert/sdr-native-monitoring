"""Sparse configuration intent bound to the snapshot the editor observed."""

from dataclasses import dataclass, fields, replace
import math

from .live import LiveConfiguration, LiveSnapshot


class StaleConfigurationPatch(ValueError):
    """The editor's source/session/configuration is no longer current."""


@dataclass(frozen=True, slots=True)
class LiveConfigurationPatch:
    expected_session_id: str
    expected_generation: int
    expected_source_id: str
    changes: tuple[tuple[str, object], ...]

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.expected_session_id, self.expected_source_id)):
            raise ValueError("configuration patch requires session and source identity")
        if type(self.expected_generation) is not int or self.expected_generation < 0:
            raise ValueError("configuration patch generation must be non-negative")
        allowed = {field.name for field in fields(LiveConfiguration)}
        changes = tuple(tuple(pair) for pair in self.changes)
        if any(len(pair) != 2 or not isinstance(pair[0], str) for pair in changes):
            raise ValueError("configuration patch entries must be field/value pairs")
        names = [name for name, _ in changes]
        if len(names) != len(set(names)) or any(name not in allowed for name in names):
            raise ValueError("configuration patch has duplicate or unknown fields")
        if any(not isinstance(value, (str, int, float, bool, type(None))) for _, value in changes):
            raise ValueError("configuration patch values must be immutable scalars")
        if any(isinstance(value, float) and not math.isfinite(value) for _, value in changes):
            raise ValueError("configuration patch numeric values must be finite")
        object.__setattr__(self, "changes", changes)

    def resolve(self, current: LiveSnapshot) -> LiveConfiguration:
        """Resolve in the serialized control owner immediately before Apply.

        This pure operation is not an atomic device transaction. The caller
        must own command serialization across the check and device mutation.
        """
        source = current.device.device_id if current.device is not None else None
        if (current.session_id != self.expected_session_id
                or current.generation != self.expected_generation
                or source != self.expected_source_id):
            raise StaleConfigurationPatch("configuration changed; refresh the draft")
        if current.applied is None:
            raise StaleConfigurationPatch("no confirmed configuration to patch")
        return replace(current.applied.applied, **dict(self.changes))
