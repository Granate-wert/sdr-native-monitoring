#include "sdr_core/spur_candidates.hpp"
#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"

#include <cmath>
#include <cstring>
#include <iostream>
#include <stdexcept>

namespace {
using namespace sdr_core;
constexpr std::uint32_t size = 1024;
constexpr double rate = 1'024'000;
constexpr double lo = 100'000'000;
constexpr double offset = 40'000;

void require(const bool condition, const char* message) {
    if (!condition) { throw std::runtime_error(message); }
}

template<class F> void refuses(F action, const char* message) {
    try { action(); } catch (const ConfigurationError&) { return; }
    throw std::runtime_error(message);
}

// Real native CPU DSP, not a hand-authored SpectrumFrame. Test-generated IQ
// never crosses into Python or an SDR. No hardware/owner authority is claimed.
SpectrumFrame frame(const double center, const std::int64_t timestamp,
                    const std::initializer_list<std::pair<double, double>> tones,
                    const std::uint32_t count = size) {
    CpuDspOptions options;
    options.source.source_id = "synthetic-spur-oracle";
    options.source.backend_id = "cpu-pocketfft";
    auto backend = make_cpu_dsp_backend(options);
    DspConfig config;
    config.fft_size = count; config.hop_size = count;
    config.window = WindowType::Rectangular; config.precision_mode = PrecisionMode::ReferenceF64;
    backend->configure(config);
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(count * 8);
    for (std::size_t i = 0; i < count; ++i) {
        double real = 0, imag = 0;
        for (const auto& [frequency, amplitude] : tones) {
            const double phase = 6.283185307179586476925286766559 * frequency * i / rate;
            real += amplitude * std::cos(phase); imag += amplitude * std::sin(phase);
        }
        const auto re = static_cast<float>(real), im = static_cast<float>(imag);
        std::memcpy(bytes->data() + 8 * i, &re, 4);
        std::memcpy(bytes->data() + 8 * i + 4, &im, 4);
    }
    IqBlock block;
    block.timestamp_ns = timestamp; block.center_frequency_hz = center; block.sample_rate_hz = rate;
    block.sample_format = SampleFormat::ComplexFloat32Le; block.sample_count = count;
    block.samples = bytes; block.config_generation = 1;
    backend->push_iq(block);
    auto frames = backend->poll_spectrum(1);
    require(frames.size() == 1, "native CPU did not produce oracle frame");
    require(frames.front().timestamp_ns == timestamp, "oracle timestamp mismatch");
    return std::move(frames.front());
}

const std::array<SpurCandidateZoneV1, 1> baseband{{{SpurZoneCoordinate::Baseband, 35'000, 65'000}}};

SpurCandidateZoneReportV1 evaluate(const SpectrumFrame& a, const SpectrumFrame& b,
                                  const std::span<const SpurCandidateZoneV1> zones = baseband) {
    const std::array<const SpectrumFrame*, 2> frames{&a, &b};
    return evaluate_spur_candidates_v1(frames, zones).zones[0];
}

void repeated_baseband_and_immutable() {
    const auto a = frame(lo, 1'000'000, {{offset, 0.2}});
    const auto b = frame(lo + 20'000, 2'000'000, {{offset, 0.2}});
    const auto values_a = *a.values, values_b = *b.values;
    const auto result = evaluate(a, b);
    require(result.evidence == SpurCandidateEvidence::RepeatedBasebandCandidate, "baseband repetition missing");
    require(result.qualifying_peaks == 2 && result.complete_fft_observations == 2, "counts wrong");
    require(result.first_peak_offset_hz == offset && result.first_peak_rf_hz == lo + offset, "actual LO ignored");
    require(*a.values == values_a && *b.values == values_b, "diagnostics changed FFT powers");
    require(a.dsp_processing_recipe->dc_removal() == DcRemovalMode::Off, "diagnostics modified recipe");
}

void genuine_rf() {
    const auto a = frame(lo, 1'000'000, {{60'000, 0.2}});
    const auto b = frame(lo + 20'000, 2'000'000, {{40'000, 0.2}});
    require(evaluate(a, b).evidence == SpurCandidateEvidence::StationaryRfAmbiguous, "real RF classified internal");
    const std::array<SpurCandidateZoneV1, 1> rf{{{SpurZoneCoordinate::Rf, lo + 55'000, lo + 65'000}}};
    require(evaluate(a, b, rf).evidence == SpurCandidateEvidence::StationaryRfAmbiguous, "RF zone claimed spur");
}

void coincident_rf() {
    // True fixed RF overlaps the baseband candidate only at first LO; at next
    // LO it remains weaker elsewhere. Region maxima alone would miss it.
    const auto a = frame(lo, 1'000'000, {{40'000, 0.2}});
    const auto b = frame(lo + 20'000, 2'000'000, {{40'000, 0.2}, {20'000, 0.1}});
    require(evaluate(a, b).evidence == SpurCandidateEvidence::StationaryRfAmbiguous, "coincident RF veto missing");
}

void burst_and_no_power() {
    const auto a = frame(lo, 1'000'000, {{40'000, 0.2}}), b = frame(lo + 20'000, 2'000'000, {});
    require(evaluate(a, b).evidence == SpurCandidateEvidence::Intermittent, "burst inferred internal spur");
    const auto zero = frame(lo, 1'000'000, {});
    require(evaluate(zero, b).evidence == SpurCandidateEvidence::NoPeak, "zero/-inf rejected or counted peak");
}

void diversity_and_old_windows() {
    const auto a = frame(lo, 1'000'000, {{40'000, 0.2}}), b = frame(lo, 2'000'000, {{40'000, 0.2}});
    require(evaluate(a, b).evidence == SpurCandidateEvidence::InsufficientLoDiversity, "same LO proves spur");
    const auto old = frame(lo + 20'000, 1'000'000'000, {{40'000, 0.2}});
    require(evaluate(a, old).evidence == SpurCandidateEvidence::UnknownCoverage, "time-separated windows prove spur");
    const std::array<const SpectrumFrame*, 2> duplicate{&a, &a};
    refuses([&] { static_cast<void>(evaluate_spur_candidates_v1(duplicate, baseband)); }, "duplicate observation accepted");
    refuses([&] { static_cast<void>(evaluate(b, a)); }, "old observation order accepted");
}

void incomplete_and_near_dc() {
    const auto a = frame(lo, 1'000'000, {{40'000, 0.2}}), b = frame(lo + 20'000, 2'000'000, {{40'000, 0.2}});
    const std::array<SpurCandidateZoneV1, 1> partial{{{SpurZoneCoordinate::Baseband, 35'000, rate}}};
    require(evaluate(a, b, partial).evidence == SpurCandidateEvidence::UnknownCoverage, "partial FFT coverage proves spur");
    const std::array<SpurCandidateZoneV1, 1> guard_edge{{{SpurZoneCoordinate::Baseband, -rate / 2, -rate / 2 + 2000}}};
    const auto guard_report = evaluate(a, b, guard_edge);
    require(guard_report.complete_fft_observations == 2 && guard_report.evidence == SpurCandidateEvidence::UnknownCoverage,
            "unobserved peak neighborhood interpreted as no signal");
    const std::array<SpurCandidateZoneV1, 1> between_bins{{{SpurZoneCoordinate::Baseband, 40'001, 40'999}}};
    require(evaluate(a, b, between_bins).evidence == SpurCandidateEvidence::UnknownCoverage,
            "empty sub-bin interval interpreted as measured absence");
    const auto dc_a = frame(lo, 1'000'000, {{0, 0.2}}), dc_b = frame(lo + 20'000, 2'000'000, {{0, 0.2}});
    const std::array<SpurCandidateZoneV1, 1> dc{{{SpurZoneCoordinate::Baseband, -2000, 2000}}};
    require(evaluate(dc_a, dc_b, dc).evidence == SpurCandidateEvidence::NearDcAmbiguous, "DC leakage inferred confirmed spur");
}

void contaminated_and_mixed() {
    const auto a = frame(lo, 1'000'000, {{40'000, 0.2}});
    auto b = frame(lo + 20'000, 2'000'000, {{40'000, 0.2}});
    for (const auto flag : {QualityFlag::AdcOverload, QualityFlag::IqDropped, QualityFlag::SettlingIncomplete,
                           QualityFlag::GainModeAgc, QualityFlag::BackendDiscontinuity}) {
        b.quality_flags = flag;
        require(evaluate(a, b).evidence == SpurCandidateEvidence::Contaminated, "contaminated observation accepted");
    }
    b.quality_flags = QualityFlag::None;
    b.config_generation = 2;
    refuses([&] { static_cast<void>(evaluate(a, b)); }, "generation mixed");
    b.config_generation = 1; b.source.source_id = "other-rx";
    refuses([&] { static_cast<void>(evaluate(a, b)); }, "source/RX mixed");
    b.source.source_id = a.source.source_id;
    b.dsp_processing_recipe = DspProcessingRecipeV1::from_dc_mode(DcRemovalMode::BlockMean);
    refuses([&] { static_cast<void>(evaluate(a, b)); }, "recipe mixed");
    b.dsp_processing_recipe.reset();
    refuses([&] { static_cast<void>(evaluate(a, b)); }, "unknown recipe inferred");
}

void invalid_geometry_and_bounds() {
    const auto a = frame(lo, 1'000'000, {{40'000, 0.2}});
    auto b = frame(lo + 20'000, 2'000'000, {{40'000, 0.2}});
    auto powers = std::make_shared<std::vector<float>>(*b.values);
    (*powers)[0] = std::numeric_limits<float>::quiet_NaN(); b.values = powers;
    refuses([&] { static_cast<void>(evaluate(a, b)); }, "NaN accepted");
    (*powers)[0] = std::numeric_limits<float>::infinity();
    refuses([&] { static_cast<void>(evaluate(a, b)); }, "+inf accepted");
    b.values = a.values;
    auto axis = std::make_shared<std::vector<double>>(*b.frequencies_hz);
    (*axis)[500] += 1; b.frequencies_hz = axis;
    refuses([&] { static_cast<void>(evaluate(a, b)); }, "irregular FFT axis accepted");
    const std::array<const SpectrumFrame*, 9> too_many{&a, &a, &a, &a, &a, &a, &a, &a, &a};
    refuses([&] { static_cast<void>(evaluate_spur_candidates_v1(too_many, baseband)); }, "unbounded observations accepted");
    const std::array<const SpectrumFrame*, 1> one{&a};
    const std::array<SpurCandidateZoneV1, 65> too_many_zones{};
    refuses([&] { static_cast<void>(evaluate_spur_candidates_v1(one, too_many_zones)); }, "unbounded zones accepted");
    const std::array<SpurCandidateZoneV1, 1> reversed{{{SpurZoneCoordinate::Baseband, 1, 0}}};
    refuses([&] { static_cast<void>(evaluate_spur_candidates_v1(one, reversed)); }, "reversed zone accepted");
}

void drifting_rf_and_unobserved_rf_veto() {
    const auto a = frame(lo, 1'000'000, {{40'000, 0.2}});
    const auto b = frame(lo + 20'000, 2'000'000, {{60'000, 0.2}});
    const std::array<SpurCandidateZoneV1, 1> rf{{{SpurZoneCoordinate::Rf, lo + 35'000, lo + 85'000}}};
    require(evaluate(a, b, rf).evidence == SpurCandidateEvidence::RfRegionPeakOnly,
            "changing RF peak incorrectly described as stationary");
    require(evaluate(a, b).evidence == SpurCandidateEvidence::OffsetInconsistent,
            "drifting baseband peak counted as repeated candidate");
    const auto edge_a = frame(lo, 1'000'000, {{400'000, 0.2}});
    const auto edge_b = frame(lo + 300'000, 2'000'000, {{400'000, 0.2}});
    const std::array<SpurCandidateZoneV1, 1> edge{{{SpurZoneCoordinate::Baseband, 395'000, 405'000}}};
    require(evaluate(edge_a, edge_b, edge).evidence == SpurCandidateEvidence::UnknownCoverage,
            "unobserved RF veto coverage claimed known");
}

void bounded_work_and_collapsed_grid() {
    auto a = frame(lo, 1'000'000, {{40'000, 0.2}}, 262144);
    std::array<SpectrumFrame, 8> copies;
    std::array<const SpectrumFrame*, 8> observations;
    for (std::size_t i = 0; i < copies.size(); ++i) {
        copies[i] = a; copies[i].timestamp_ns += static_cast<std::int64_t>(i);
        observations[i] = &copies[i];
    }
    std::array<SpurCandidateZoneV1, 64> zones;
    zones.fill({SpurZoneCoordinate::Baseband, -rate / 2, rate / 2});
    refuses([&] { static_cast<void>(evaluate_spur_candidates_v1(observations, zones)); },
            "diagnostic work bound exceeded without refusal");
    a = frame(1e20, 1'000'000, {{40'000, 0.2}});
    const std::array<const SpectrumFrame*, 1> collapsed{&a};
    refuses([&] { static_cast<void>(evaluate_spur_candidates_v1(collapsed, baseband)); },
            "binary64-collapsed FFT grid accepted");
}

}  // namespace

int main() {
    try {
        const std::pair<const char*, void(*)()> tests[]{
            {"baseband/immutability", repeated_baseband_and_immutable}, {"genuine RF", genuine_rf},
            {"coincident RF", coincident_rf}, {"burst/zero", burst_and_no_power},
            {"LO diversity/time", diversity_and_old_windows}, {"coverage/DC", incomplete_and_near_dc},
            {"quality/config/recipe", contaminated_and_mixed}, {"geometry/bounds", invalid_geometry_and_bounds},
            {"drifting/unknown RF", drifting_rf_and_unobserved_rf_veto}, {"bounded work/grid", bounded_work_and_collapsed_grid},
        };
        for (const auto& [name, test] : tests) { test(); std::cout << "PASS " << name << '\n'; }
    } catch (const std::exception& error) {
        std::cerr << "FAIL " << error.what() << '\n'; return 1;
    }
}
