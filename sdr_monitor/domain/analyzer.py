"""Shared analyzer publication envelope; measurement metadata stays with data.

RTBW is not a completed sweep. Unknown RX/epoch are explicit until the
producer publishes them; the adapter never manufactures receiver identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
import numpy as np

from .live import LiveSnapshot, LiveSpectrumFrame
from .sweep_lines import SweepLineFrame
from .sweep_progress import SweepProgressFrame
from .analyzer_identity import (
    MeasurementIdentity, identities_equal, layer_matches_measurement, matches_active_identity,
    persistence_is_pending,
)


class AnalyzerPublicationKind(str, Enum):
    """Measurement progress, independent of UI age or paint cadence."""

    RTBW_FRAME = "rtbw_frame"
    SWEEP_PROGRESS = "sweep_progress"
    SWEEP_COMPLETE = "sweep_complete"
    SWEEP_GAP = "sweep_gap"


@dataclass(frozen=True, slots=True)
class RtbwFrameMetadata:
    center_frequency_hz: float
    sample_rate_hz: float
    fft_size: int
    hop_size: int


@dataclass(frozen=True, slots=True)
class AnalyzerFrameBundle:
    """One coherent spectrum publication, without inferred auxiliary layers.

    Spectrum owns physical frequency centers, unit, source generation,
    timestamp quality and the unmodified native quality mask. Persistence
    and Waterfall are intentionally not joined by arrival order.
    """

    spectrum: LiveSpectrumFrame | SweepLineFrame | SweepProgressFrame
    session_id: str | None
    receiver_id: str | None
    acquisition_epoch: int | None
    rtbw: RtbwFrameMetadata | None
    identity: MeasurementIdentity | None = None
    persistence: object | None = None
    waterfall_line: object | None = None
    coherence_issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.spectrum, LiveSpectrumFrame):
            if self.rtbw is None:
                raise ValueError("RTBW publication requires RTBW metadata")
            frame = self.spectrum
            frequencies = frame.frequencies_hz
            values = frame.values
            if frequencies.flags.writeable or values.flags.writeable:
                raise ValueError("RTBW measurement arrays must be read-only")
            if not np.all(np.isfinite(frequencies)) or np.any(np.diff(frequencies) <= 0.0):
                raise ValueError("RTBW frequency grid must be finite and strictly ascending")
            if np.any(np.isinf(values)):
                raise ValueError("RTBW values may contain NaN gaps but not infinity")
            if (not isfinite(frame.center_frequency_hz) or not isfinite(frame.sample_rate_hz)
                    or frame.sample_rate_hz <= 0.0 or frame.fft_size <= 0
                    or frame.hop_size <= 0 or frame.hop_size > frame.fft_size):
                raise ValueError("RTBW physical metadata is invalid")
            if frequencies.size != frame.fft_size:
                raise ValueError("RTBW frequency grid size must match physical FFT")
            spacing = frame.sample_rate_hz / frame.fft_size
            expected_grid = frame.center_frequency_hz + (
                np.arange(frame.fft_size, dtype=np.float64) - frame.fft_size // 2
            ) * spacing
            # Use a bin-relative tolerance, not an RF-center-relative rtol
            # which would admit substantial shifts at GHz center frequencies.
            tolerance = max(spacing * 1e-7,
                            abs(float(np.spacing(frame.center_frequency_hz))) * 8)
            if not np.allclose(frequencies, expected_grid, rtol=0.0, atol=tolerance):
                raise ValueError("RTBW frequency grid must match center and Fs/FFT")
            expected = RtbwFrameMetadata(
                frame.center_frequency_hz, frame.sample_rate_hz, frame.fft_size, frame.hop_size,
            )
            if self.rtbw != expected:
                raise ValueError("RTBW metadata must match its measurement frame")
            if self.receiver_id != frame.receiver_id or self.acquisition_epoch != frame.acquisition_epoch:
                raise ValueError("RTBW envelope receiver/epoch must match its measurement frame")
            identity = self.identity or MeasurementIdentity.from_frame(
                frame, session_id=self.session_id,
            )
            derived = MeasurementIdentity.from_frame(frame, session_id=self.session_id)
            if not identities_equal(identity, derived):
                raise ValueError("bundle identity must be derived from its measurement frame")
            object.__setattr__(self, "identity", identity)
            if self.persistence is not None and not layer_matches_measurement(identity, self.persistence):
                raise ValueError("persistence identity/grid/unit does not match measurement")
            if self.waterfall_line is not None and not layer_matches_measurement(identity, self.waterfall_line):
                raise ValueError("waterfall identity/grid/unit does not match measurement")
        elif isinstance(self.spectrum, (SweepLineFrame, SweepProgressFrame)):
            if self.session_id is not None or self.receiver_id is not None:
                raise ValueError("Sweep producer does not declare session or receiver identity")
            if self.rtbw is not None or self.acquisition_epoch != self.spectrum.epoch:
                raise ValueError("Sweep publication must retain its own epoch and no RTBW metadata")
            if self.persistence is not None or self.waterfall_line is not None:
                raise ValueError("Sweep auxiliary layers require a separately validated producer contract")
            base = MeasurementIdentity.from_frame(self.spectrum)
            derived = MeasurementIdentity(
                source_id=base.source_id, session_id=None, receiver_id=None,
                acquisition_epoch=self.spectrum.epoch,
                # Segment generation/revision provenance stays on each
                # publication; it is not a single configuration identity.
                config_generation=None, clock_domain=None,
                accumulation_id=f"epoch:{self.spectrum.epoch}",
                source_frame_sequence=base.source_frame_sequence,
                unit=base.unit, frequencies_hz=base.frequencies_hz,
            )
            if self.identity is not None and not identities_equal(self.identity, derived):
                raise ValueError("bundle identity must be derived from its Sweep measurement")
            object.__setattr__(self, "identity", derived)
        else:
            raise TypeError("unsupported analyzer publication")

    @property
    def mode(self) -> str:
        return "rtbw" if isinstance(self.spectrum, LiveSpectrumFrame) else "sweep"

    @property
    def publication_kind(self) -> AnalyzerPublicationKind:
        if isinstance(self.spectrum, LiveSpectrumFrame):
            return AnalyzerPublicationKind.RTBW_FRAME
        if isinstance(self.spectrum, SweepProgressFrame):
            return AnalyzerPublicationKind.SWEEP_PROGRESS
        return (AnalyzerPublicationKind.SWEEP_COMPLETE if self.spectrum.is_complete
                else AnalyzerPublicationKind.SWEEP_GAP)

    @property
    def terminal_sweep(self) -> bool:
        """A terminal gap is terminal too; RTBW is never a completed sweep."""
        return isinstance(self.spectrum, SweepLineFrame)

    @property
    def frequencies_hz(self) -> np.ndarray:
        return self.spectrum.frequencies_hz

    @property
    def values(self) -> np.ndarray:
        return (self.spectrum.values if isinstance(self.spectrum, LiveSpectrumFrame)
                else self.spectrum.values_db)

    @property
    def unit(self) -> str:
        return self.spectrum.unit


def bundle_from_sweep(line: SweepLineFrame | SweepProgressFrame) -> AnalyzerFrameBundle:
    """Retain a progressive or terminal Sweep publication and its provenance.

    completed_at_ns is not an RF acquisition timestamp. This adapter does
    not create partial revisions or claim that all frequencies were sampled
    simultaneously. Producer session/RX are unavailable in this contract.
    """
    return AnalyzerFrameBundle(
        spectrum=line, session_id=None, receiver_id=None,
        acquisition_epoch=line.epoch, rtbw=None,
    )


def bundle_from_live(snapshot: LiveSnapshot) -> AnalyzerFrameBundle | None:
    """Adapt actual domain publications, retaining native-backed arrays.

    Snapshot generation is a control revision, not a replacement for the
    spectrum's producer config_generation. Timestamp is likewise untouched.
    """
    frame = snapshot.spectrum
    if frame is None:
        return None
    identity = MeasurementIdentity.from_frame(frame, session_id=snapshot.session_id)
    if not matches_active_identity(identity, snapshot):
        return None
    persistence = getattr(snapshot, "persistence", None)
    issues: list[str] = []
    if persistence is not None and not layer_matches_measurement(identity, persistence):
        pending = persistence_is_pending(identity, persistence)
        persistence = None
        issues.append("persistence_pending" if pending else "persistence_identity_mismatch")
    waterfall = getattr(snapshot, "waterfall_line", None)
    if waterfall is not None and not layer_matches_measurement(identity, waterfall):
        waterfall = None
        issues.append("waterfall_identity_mismatch")
    return AnalyzerFrameBundle(
        spectrum=frame,
        session_id=str(snapshot.session_id),
        receiver_id=frame.receiver_id,
        acquisition_epoch=frame.acquisition_epoch,
        rtbw=RtbwFrameMetadata(
            frame.center_frequency_hz, frame.sample_rate_hz,
            frame.fft_size, frame.hop_size,
        ),
        identity=identity, persistence=persistence, waterfall_line=waterfall,
        coherence_issues=tuple(issues),
    )
