#include "sdr_core/spur_candidates.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <cmath>

namespace sdr_core {
namespace {

struct Peak {
    bool found{};
    double rf{};
    double offset{};
    float value{-std::numeric_limits<float>::infinity()};
};

void charge(std::size_t& visits, const std::size_t count) {
    if (count > spur_candidate_max_bin_visits - visits) {
        throw ConfigurationError("spur diagnostic bin visit budget exceeded");
    }
    visits += count;
}

bool qualifies(const SpectrumFrame& frame, const std::size_t bin,
               const SpurCandidateLimitsV1& limits, std::size_t& visits) {
    const auto& values = *frame.values;
    if (values[bin] < limits.minimum_peak_dbfs) { return false; }
    // Guard bins two away avoid treating the shoulder of a windowed tone as
    // another candidate. Flat wideband power is not a narrow peak.
    if (bin < 2 || bin + 2 >= values.size()) { return false; }
    charge(visits, 2);
    const auto background = std::max(values[bin - 2], values[bin + 2]);
    return static_cast<double>(values[bin]) - background >= limits.minimum_contrast_db;
}

std::pair<std::size_t, std::size_t> bin_interval(const SpectrumFrame& frame,
                                               const double start, const double stop) {
    const auto& axis = *frame.frequencies_hz;
    return {static_cast<std::size_t>(std::lower_bound(axis.begin(), axis.end(), start) - axis.begin()),
            static_cast<std::size_t>(std::lower_bound(axis.begin(), axis.end(), stop) - axis.begin())};
}

Peak peak(const SpectrumFrame& frame, const double start, const double stop,
          const SpurCandidateLimitsV1& limits, std::size_t& visits) {
    const auto [begin, end] = bin_interval(frame, start, stop);
    charge(visits, end - begin);
    Peak result;
    for (auto bin = begin; bin < end; ++bin) {
        if ((*frame.values)[bin] > result.value && qualifies(frame, bin, limits, visits)) {
            result = {true, (*frame.frequencies_hz)[bin],
                (*frame.frequencies_hz)[bin] - frame.center_frequency_hz, (*frame.values)[bin]};
        }
    }
    return result;
}

bool same_numerical_configuration(const SpectrumFrame& a, const SpectrumFrame& b) {
    return a.source.source_id == b.source.source_id && a.source.backend_id == b.source.backend_id &&
        a.source.device_serial == b.source.device_serial && a.source.uri == b.source.uri &&
        a.source.schema_version == b.source.schema_version && a.source.source_type == b.source.source_type &&
        a.config_generation == b.config_generation && a.sample_rate_hz == b.sample_rate_hz &&
        a.fft_size == b.fft_size && a.hop_size == b.hop_size && a.window == b.window &&
        a.detector == b.detector && a.precision_mode == b.precision_mode && a.unit == b.unit &&
        a.averaging_frames == b.averaging_frames && a.window_normalization_version == b.window_normalization_version &&
        a.dsp_processing_recipe->dc_removal() == b.dsp_processing_recipe->dc_removal();
}

void validate(const SpectrumFrame& frame, std::size_t& visits) {
    if (!frame.values || !frame.frequencies_hz || frame.fft_size < 256 || frame.fft_size > 262144 ||
        (frame.fft_size & (frame.fft_size - 1)) != 0 || frame.values->size() != frame.fft_size ||
        frame.frequencies_hz->size() != frame.fft_size || !std::isfinite(frame.center_frequency_hz) ||
        frame.center_frequency_hz <= 0 || !std::isfinite(frame.sample_rate_hz) || frame.sample_rate_hz <= 0 ||
        !frame.config_generation || frame.timestamp_ns <= 0 || frame.hop_size == 0 || frame.hop_size > frame.fft_size ||
        frame.unit != SpectrumUnit::DbfsBin || !frame.averaging_frames ||
        !frame.dsp_processing_recipe || frame.window_normalization_version != power_normalization_version ||
        frame.source.source_id.empty() || frame.source.source_id.size() > 128 ||
        frame.source.backend_id.size() > 128 || frame.source.uri.size() > 1024 ||
        frame.source.device_serial.size() > 256 || frame.source.schema_version != contract_schema_version) {
        throw ConfigurationError("spur diagnostics require complete original CPU FFT numerical provenance");
    }
    const double width = frame.sample_rate_hz / frame.fft_size;
    if (frame.fft_bin_width_hz != width) { throw ConfigurationError("spur diagnostic bin width mismatch"); }
    charge(visits, frame.fft_size);
    for (std::size_t i = 0; i < frame.fft_size; ++i) {
        const double expected = frame.center_frequency_hz +
            (static_cast<std::int64_t>(i) - static_cast<std::int64_t>(frame.fft_size / 2)) * width;
        const float value = (*frame.values)[i];
        // Native zero power is -inf; NaN/+inf is not a numerical observation.
        if ((*frame.frequencies_hz)[i] != expected ||
            (i > 0 && (*frame.frequencies_hz)[i] <= (*frame.frequencies_hz)[i - 1]) || std::isnan(value) ||
            value == std::numeric_limits<float>::infinity()) {
            throw ConfigurationError("spur diagnostics reject invalid or noncanonical FFT grid/power");
        }
    }
}

}  // namespace

std::string_view to_wire(const SpurCandidateEvidence value) {
    switch (value) {
    case SpurCandidateEvidence::UnknownCoverage: return "unknown_coverage";
    case SpurCandidateEvidence::Contaminated: return "contaminated";
    case SpurCandidateEvidence::NoPeak: return "no_peak";
    case SpurCandidateEvidence::InsufficientLoDiversity: return "insufficient_lo_diversity";
    case SpurCandidateEvidence::Intermittent: return "intermittent";
    case SpurCandidateEvidence::NearDcAmbiguous: return "near_dc_ambiguous";
    case SpurCandidateEvidence::StationaryRfAmbiguous: return "stationary_rf_ambiguous";
    case SpurCandidateEvidence::RfRegionPeakOnly: return "rf_region_peak_only";
    case SpurCandidateEvidence::OffsetInconsistent: return "offset_inconsistent";
    case SpurCandidateEvidence::RepeatedBasebandCandidate: return "repeated_baseband_candidate";
    }
    throw ConfigurationError("invalid spur diagnostic evidence enum");
}

SpurCandidateReportV1 evaluate_spur_candidates_v1(
    const std::span<const SpectrumFrame* const> observations,
    const std::span<const SpurCandidateZoneV1> zones, const SpurCandidateLimitsV1& limits) {
    if (observations.empty() || observations.size() > spur_candidate_max_observations ||
        zones.empty() || zones.size() > spur_candidate_max_zones ||
        !std::isfinite(limits.minimum_peak_dbfs) || limits.minimum_peak_dbfs > 0 ||
        limits.minimum_peak_dbfs < -300 || !std::isfinite(limits.minimum_contrast_db) ||
        limits.minimum_contrast_db <= 0 || limits.minimum_contrast_db > 120 ||
        limits.maximum_observation_span_ns <= 0) {
        throw ConfigurationError("invalid or unbounded spur diagnostic request");
    }
    SpurCandidateReportV1 report;
    report.zone_count = zones.size();
    for (const auto& zone : zones) {
        if ((zone.coordinate != SpurZoneCoordinate::Baseband && zone.coordinate != SpurZoneCoordinate::Rf) ||
            !std::isfinite(zone.start_hz) || !std::isfinite(zone.stop_hz) || zone.start_hz >= zone.stop_hz) {
            throw ConfigurationError("invalid spur diagnostic region");
        }
    }
    bool contaminated = false;
    constexpr auto contamination = QualityFlag::AdcOverload | QualityFlag::IqDropped | QualityFlag::FftDropped |
        QualityFlag::SettlingIncomplete | QualityFlag::MissingSegment | QualityFlag::BackendDiscontinuity |
        QualityFlag::GainModeAgc;
    std::int64_t previous_time = 0;
    for (const auto* frame : observations) {
        if (!frame) { throw ConfigurationError("null spur diagnostic observation"); }
        validate(*frame, report.bin_visits);
        if (!same_numerical_configuration(*observations.front(), *frame)) {
            throw ConfigurationError("mixed source/configuration/recipe spur observations");
        }
        if (previous_time != 0 && frame->timestamp_ns <= previous_time) {
            throw ConfigurationError("spur diagnostic observations must be strictly time ordered");
        }
        previous_time = frame->timestamp_ns;
        contaminated = contaminated ||
            (static_cast<std::uint32_t>(frame->quality_flags) & static_cast<std::uint32_t>(contamination)) != 0 ||
            frame->dropped_samples_before != 0 || frame->dropped_iq_blocks_before != 0 || frame->dropped_fft_frames_before != 0;
    }
    const bool stale = observations.back()->timestamp_ns - observations.front()->timestamp_ns >
        limits.maximum_observation_span_ns;
    for (std::size_t z = 0; z < zones.size(); ++z) {
        const auto& zone = zones[z];
        auto& result = report.zones[z];
        result.observations = static_cast<std::uint8_t>(observations.size());
        std::array<Peak, spur_candidate_max_observations> peaks{};
        double lo_min = observations.front()->center_frequency_hz, lo_max = lo_min;
        double offset_min = std::numeric_limits<double>::infinity(), offset_max = -offset_min;
        double rf_min = offset_min, rf_max = offset_max;
        bool near_dc = false, unknown_peak_neighborhood = false;
        for (std::size_t f = 0; f < observations.size(); ++f) {
            const auto& frame = *observations[f];
            const double base = zone.coordinate == SpurZoneCoordinate::Baseband ? frame.center_frequency_hz : 0;
            const double start = base + zone.start_hz, stop = base + zone.stop_hz;
            if (start >= frame.center_frequency_hz - frame.sample_rate_hz / 2 &&
                stop <= frame.center_frequency_hz + frame.sample_rate_hz / 2) {
                ++result.complete_fft_observations;
            }
            const auto [begin, end] = bin_interval(frame, start, stop);
            unknown_peak_neighborhood = unknown_peak_neighborhood || begin == end || begin < 2 || end > frame.fft_size - 2;
            peaks[f] = peak(frame, start, stop, limits, report.bin_visits);
            const auto& p = peaks[f];
            lo_min = std::min(lo_min, frame.center_frequency_hz);
            lo_max = std::max(lo_max, frame.center_frequency_hz);
            if (!p.found) { continue; }
            ++result.qualifying_peaks;
            if (result.qualifying_peaks == 1) { result.first_peak_rf_hz = p.rf; result.first_peak_offset_hz = p.offset; }
            result.strongest_peak_dbfs = std::max(result.strongest_peak_dbfs, p.value);
            offset_min = std::min(offset_min, p.offset); offset_max = std::max(offset_max, p.offset);
            rf_min = std::min(rf_min, p.rf); rf_max = std::max(rf_max, p.rf);
            near_dc = near_dc || std::abs(p.offset) <= 2 * frame.fft_bin_width_hz;
        }
        const double tolerance = observations.front()->fft_bin_width_hz;
        if (stale || unknown_peak_neighborhood || result.complete_fft_observations != result.observations) { continue; }
        if (contaminated) { result.evidence = SpurCandidateEvidence::Contaminated; continue; }
        if (!result.qualifying_peaks) { result.evidence = SpurCandidateEvidence::NoPeak; continue; }
        if (lo_max - lo_min < 4 * tolerance) { result.evidence = SpurCandidateEvidence::InsufficientLoDiversity; continue; }
        if (result.qualifying_peaks != result.observations) { result.evidence = SpurCandidateEvidence::Intermittent; continue; }
        if (near_dc) { result.evidence = SpurCandidateEvidence::NearDcAmbiguous; continue; }
        if (rf_max - rf_min <= tolerance) {
            result.evidence = SpurCandidateEvidence::StationaryRfAmbiguous; continue;
        }
        if (zone.coordinate == SpurZoneCoordinate::Rf) {
            result.evidence = SpurCandidateEvidence::RfRegionPeakOnly; continue;
        }
        if (offset_max - offset_min > tolerance) { result.evidence = SpurCandidateEvidence::OffsetInconsistent; continue; }
        // A coincident real RF signal must veto the candidate even when a
        // stronger offset-locked peak won the region maximum in other frames.
        bool rf_ambiguous = false, rf_unknown = false;
        for (std::size_t p = 0; p < observations.size(); ++p) {
            bool all_present = true;
            for (const auto* frame : observations) {
                const double rf = peaks[p].rf;
                if (rf - tolerance < frame->frequencies_hz->front() + 2 * tolerance ||
                    rf + tolerance > frame->center_frequency_hz + frame->sample_rate_hz / 2 - 2 * tolerance) {
                    rf_unknown = true; all_present = false; break;
                }
                if (!peak(*frame, rf - tolerance, rf + tolerance, limits, report.bin_visits).found) {
                    all_present = false; break;
                }
            }
            rf_ambiguous = rf_ambiguous || all_present;
        }
        result.evidence = rf_ambiguous ? SpurCandidateEvidence::StationaryRfAmbiguous :
            rf_unknown ? SpurCandidateEvidence::UnknownCoverage : SpurCandidateEvidence::RepeatedBasebandCandidate;
    }
    return report;
}

}  // namespace sdr_core
