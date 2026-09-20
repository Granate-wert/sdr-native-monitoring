"""Immutable persistence-density shapes and numerical presentation mapping."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from enum import StrEnum
from functools import lru_cache

import numpy as np

from ..design.tokens import density_lookup_table


# Fixed display transfer, not adaptive normalization of measured probability.
# log10(1 + 9999*p) / 4: zero stays zero, one stays one, rare observations
# remain visible across approximately four decades without per-frame pumping.
LOG_DENSITY_GAIN = 9999.0
_MAPPING_BATCH = 65_536
_VALIDATION_BATCH = 65_536


class DensityValueMode(StrEnum):
    """What each native density value means; UI never guesses this semantic."""

    PROBABILITY = "probability"
    COUNT = "count"


class PersistenceRenderMode(StrEnum):
    """Direct is measurement-faithful; Visual is explicitly presentation-only."""

    DIRECT = "direct"
    VISUAL = "visual"


@dataclass(frozen=True, slots=True)
class PersistenceDensityFrame:
    """External immutable density with physical bin edges for one shared ViewBox."""

    density: np.ndarray
    frequency_edges_hz: np.ndarray
    level_edges: np.ndarray
    value_mode: DensityValueMode
    level_unit: str

    def __post_init__(self) -> None:
        density = np.asarray(self.density)
        frequencies = np.asarray(self.frequency_edges_hz)
        levels = np.asarray(self.level_edges)
        if density.ndim != 2 or density.size == 0 or not np.issubdtype(density.dtype, np.number):
            raise ValueError("persistence density must be a non-empty numeric two-dimensional array")
        if frequencies.ndim != 1 or levels.ndim != 1:
            raise ValueError("persistence bin edges must be one-dimensional")
        if frequencies.size != density.shape[1] + 1 or levels.size != density.shape[0] + 1:
            raise ValueError("persistence bin edge counts do not match density shape")
        if not self.level_unit.strip():
            raise ValueError("persistence level unit must be explicit")
        _validate_regular_edges(frequencies, "frequency")
        _validate_regular_edges(levels, "level")
        error = density_range_error(density, self.value_mode)
        if error is not None:
            raise ValueError(error)
        object.__setattr__(self, "density", density)
        object.__setattr__(self, "frequency_edges_hz", frequencies)
        object.__setattr__(self, "level_edges", levels)


def density_range_error(density: np.ndarray, mode: DensityValueMode) -> str | None:
    """Full numeric range check for a caller-validated nonempty 2D numeric array.

    Both public construction and native adaptation validate independently; no
    identity/readonly certificate skips cells. Nonfinite values retain their
    existing missing semantics. Negative values take priority over >1 anywhere
    in the matrix, even when that negative occurs in a later block.
    """
    above_one = False
    for batch in _density_validation_batches(density):
        finite = np.isfinite(batch)
        if np.any(finite & (batch < 0.0)):
            return "persistence density must not contain negative values"
        if mode is DensityValueMode.PROBABILITY and np.any(finite & (batch > 1.0)):
            above_one = True
    return "probability density must not exceed one" if above_one else None


def _density_validation_batches(density: np.ndarray) -> Iterator[np.ndarray]:
    if density.flags.c_contiguous or density.flags.f_contiguous:
        values = density.reshape(-1, order="A")
        for first in range(0, values.size, _VALIDATION_BATCH):
            yield values[first:first + _VALIDATION_BATCH]
    else:
        # Bounded rectangular views cover reversed/transposed/strided sources
        # without a full contiguous copy or one Python iteration per tiny row.
        rows = max(1, _VALIDATION_BATCH // density.shape[1])
        for row in range(0, density.shape[0], rows):
            for column in range(0, density.shape[1], _VALIDATION_BATCH):
                yield density[row:row + rows, column:column + _VALIDATION_BATCH]


@dataclass(frozen=True, slots=True)
class PersistenceDensityView:
    """Identity-preserving frame view used by upload suppression."""

    source_frame: object
    density: np.ndarray
    frequency_edges_hz: np.ndarray
    level_edges: np.ndarray
    value_mode: DensityValueMode
    level_unit: str

    @property
    def physical_rect(self) -> tuple[float, float, float, float]:
        """Physical x/y rectangle derived only from declared bin edges."""

        left = float(self.frequency_edges_hz[0])
        bottom = float(self.level_edges[0])
        return (
            left,
            bottom,
            float(self.frequency_edges_hz[-1]) - left,
            float(self.level_edges[-1]) - bottom,
        )

    @property
    def quantitative_labels(self) -> tuple[str, str]:
        if self.value_mode is DensityValueMode.PROBABILITY:
            return ("0.000 probability", "1.000 probability")
        finite = self.density[np.isfinite(self.density)]
        maximum = 0.0 if finite.size == 0 else float(np.max(finite))
        return ("0 count", f"{maximum:.0f} count")


def adapt_persistence_density(frame: object) -> PersistenceDensityView:
    """Accept a declared V2 density shape without importing backend owners."""

    if isinstance(frame, PersistenceDensityFrame):
        source = frame
    else:
        source = PersistenceDensityFrame(
            density=np.asarray(getattr(frame, "density", None)),
            frequency_edges_hz=np.asarray(getattr(frame, "frequency_edges_hz", None)),
            level_edges=np.asarray(getattr(frame, "level_edges", None)),
            value_mode=DensityValueMode(str(getattr(frame, "value_mode", ""))),
            level_unit=str(getattr(frame, "level_unit", "")),
        )
    return PersistenceDensityView(
        source_frame=frame,
        density=source.density,
        frequency_edges_hz=source.frequency_edges_hz,
        level_edges=source.level_edges,
        value_mode=source.value_mode,
        level_unit=source.level_unit,
    )


def map_density_for_display(
    view: PersistenceDensityView,
    *,
    logarithmic: bool,
    out: np.ndarray | None = None,
) -> np.ndarray:
    """Map native probability/count to a bounded image with transparent zero value.

    The source density remains immutable.  The returned image is the sole
    presentation buffer retained by a Direct overlay update.
    """

    mapped: np.ndarray = np.zeros(view.density.shape, dtype=np.float32) if out is None else out
    if mapped.shape != view.density.shape or mapped.dtype != np.float32:
        raise ValueError("persistence display output must be float32 with the density shape")
    _map_density_values_into(
        view.density,
        value_mode=view.value_mode,
        logarithmic=logarithmic,
        count_maximum=_count_maximum(view),
        out=mapped,
    )
    return mapped


def map_density_row_for_display(
    values: np.ndarray,
    *,
    value_mode: DensityValueMode,
    logarithmic: bool,
    count_maximum: float,
    out: np.ndarray,
) -> None:
    """Map one row into caller-owned scratch memory for Visual mode."""

    _map_density_values_into(
        values,
        value_mode=value_mode,
        logarithmic=logarithmic,
        count_maximum=count_maximum,
        out=out,
    )


@lru_cache(maxsize=1)
def inferno_lookup_table() -> np.ndarray:
    """Return deterministic Inferno-like RGBA LUT; zero density has zero alpha."""

    return density_lookup_table()


def _validate_regular_edges(edges: np.ndarray, name: str) -> None:
    if edges.size < 2 or not np.issubdtype(edges.dtype, np.number) or not np.all(np.isfinite(edges)):
        raise ValueError(f"persistence {name} edges must be finite numeric values")
    spacing = np.diff(edges)
    if np.any(spacing <= 0.0):
        raise ValueError(f"persistence {name} edges must be strictly increasing")
    if not np.allclose(spacing, spacing[0], rtol=1e-9, atol=1e-9):
        raise ValueError(f"one ImageItem requires regular physical {name} bin edges")


def _count_maximum(view: PersistenceDensityView) -> float:
    if view.value_mode is not DensityValueMode.COUNT:
        return 1.0
    finite = view.density[np.isfinite(view.density)]
    return 0.0 if finite.size == 0 else float(np.max(finite))


def _map_density_values_into(
    values: np.ndarray,
    *,
    value_mode: DensityValueMode,
    logarithmic: bool,
    count_maximum: float,
    out: np.ndarray,
) -> None:
    if out.shape != values.shape or out.dtype != np.float32:
        raise ValueError("persistence mapping output shape or dtype is invalid")
    if values.size <= _MAPPING_BATCH:
        _map_density_chunk(values, value_mode, logarithmic, count_maximum, out)
    elif values.flags.c_contiguous and out.flags.c_contiguous:
        source, target = values.reshape(-1), out.reshape(-1)
        for first in range(0, values.size, _MAPPING_BATCH):
            _map_density_chunk(source[first:first + _MAPPING_BATCH], value_mode, logarithmic,
                               count_maximum, target[first:first + _MAPPING_BATCH])
    elif values.ndim == 1:
        for first in range(0, values.size, _MAPPING_BATCH):
            _map_density_chunk(values[first:first + _MAPPING_BATCH], value_mode, logarithmic,
                               count_maximum, out[first:first + _MAPPING_BATCH])
    else:
        # Preserve arbitrary input/output strides without flattening copies.
        # Narrow transposed rows are grouped, avoiding one Python call per bin.
        rows = max(1, _MAPPING_BATCH // values.shape[-1])
        for prefix in np.ndindex(values.shape[:-2]):
            for row in range(0, values.shape[-2], rows):
                for column in range(0, values.shape[-1], _MAPPING_BATCH):
                    index: tuple[int | slice, ...] = (
                        *prefix, slice(row, row + rows), slice(column, column + _MAPPING_BATCH))
                    _map_density_chunk(values[index], value_mode, logarithmic, count_maximum, out[index])


def _map_density_chunk(values: np.ndarray, value_mode: DensityValueMode, logarithmic: bool,
                       count_maximum: float, out: np.ndarray) -> None:
    """Same transfer in bounded scratch; log(1 + 0) is exactly zero.

    This skips only numerical work on all-zero display chunks, never measured
    cells, source validation, resolution, or update cadence. Signed zeros stay
    in the output. Dense/nonfinite chunks use the original arithmetic order.
    """
    out.fill(0.0)
    np.copyto(out, values, where=np.isfinite(values))
    if value_mode is DensityValueMode.COUNT and count_maximum > 0.0:
        out /= count_maximum
    if logarithmic and np.any(out):
        np.multiply(out, LOG_DENSITY_GAIN, out=out)
        np.log1p(out, out=out)
        out /= np.log1p(LOG_DENSITY_GAIN)
    np.clip(out, 0.0, 1.0, out=out)
