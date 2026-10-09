#pragma once

#include "sdr_core/types.hpp"

#include <array>
#include <span>

namespace sdr_core {

inline constexpr std::size_t spur_candidate_max_observations = 8;
inline constexpr std::size_t spur_candidate_max_zones = 64;
inline constexpr std::size_t spur_candidate_max_bin_visits = 4'194'304;

enum class SpurZoneCoordinate : std::uint8_t { Baseband, Rf };
enum class SpurCandidateEvidence : std::uint8_t {
    UnknownCoverage, Contaminated, NoPeak, InsufficientLoDiversity,
    Intermittent, NearDcAmbiguous, StationaryRfAmbiguous, RfRegionPeakOnly, OffsetInconsistent,
    RepeatedBasebandCandidate,
};

// Numerical observations only. Neither this input nor its result authenticates
// hardware/firmware/gain/owner/session/RX or permits a processing policy.
// The original owner must supply that separate, currently unqualified join.
struct SpurCandidateZoneV1 {
    SpurZoneCoordinate coordinate{SpurZoneCoordinate::Baseband};
    double start_hz{};
    double stop_hz{};  // half-open
};

struct SpurCandidateLimitsV1 {
    double minimum_peak_dbfs{-60.0};
    double minimum_contrast_db{6.0};
    // Acquisition timestamps are compared numerically, not interpreted as
    // true RF exposure or probability. Estimated timestamps remain estimated.
    std::int64_t maximum_observation_span_ns{100'000'000};
};

struct SpurCandidateZoneReportV1 {
    SpurCandidateEvidence evidence{SpurCandidateEvidence::UnknownCoverage};
    std::uint8_t observations{};
    std::uint8_t complete_fft_observations{};
    std::uint8_t qualifying_peaks{};
    double first_peak_rf_hz{std::numeric_limits<double>::quiet_NaN()};
    double first_peak_offset_hz{std::numeric_limits<double>::quiet_NaN()};
    float strongest_peak_dbfs{-std::numeric_limits<float>::infinity()};
};

struct SpurCandidateReportV1 {
    std::array<SpurCandidateZoneReportV1, spur_candidate_max_zones> zones{};
    std::size_t zone_count{};
    std::size_t bin_visits{};
};

static_assert(sizeof(SpurCandidateReportV1) <= 4096);

// Fixed report/working arrays, no retained frames or dynamic allocation. OFF
// producers are untouched. Explicit callers must reserve/report sizeof(report)
// before integrating diagnostics into an owner queue; this function is NOT an
// uncharged background consumer, an RF classifier, a notch or calibrated Pd.
// Invalid numerical provenance/mixed source or generation refuses. Quality
// gaps, partial coverage and old/time-separated observations never confirm spur.
[[nodiscard]] SpurCandidateReportV1 evaluate_spur_candidates_v1(
    std::span<const SpectrumFrame* const> observations,
    std::span<const SpurCandidateZoneV1> zones,
    const SpurCandidateLimitsV1& limits = {});

[[nodiscard]] std::string_view to_wire(SpurCandidateEvidence value);

}  // namespace sdr_core
