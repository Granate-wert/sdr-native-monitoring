#pragma once

#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/sweep_line_assembler.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <complex>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>

namespace app04_geometry_test {
using namespace sdr_core;
constexpr double rate = 61'440'000.0;
constexpr double window_hz = 36'000'000.0;

inline void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

inline SpectrumFrame spectrum(std::uint32_t bins, WindowType window, SpectrumUnit unit,
                              std::uint32_t segment_index) {
    const auto physical_fft = 2U * bins;
    const double center = segment_index == 0 ? 2'430'000'000.0 : 2'460'000'000.0;
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(physical_fft * 8U);
    const std::array<double, 3> tones{2'442'000'000.0, 2'445'000'000.0, 2'448'000'000.0};
    const std::array<double, 3> amplitude{0.08, 0.3, 0.05};
    for (std::size_t i = 0; i < physical_fft; ++i) {
        std::complex<double> sample{};
        for (std::size_t tone = 0; tone < tones.size(); ++tone) {
            const double phase = 2 * 3.14159265358979323846 * (tones[tone] - center) * static_cast<double>(i) / rate;
            sample += amplitude[tone] * std::complex<double>(std::cos(phase), std::sin(phase));
        }
        const float re = static_cast<float>(sample.real());
        const float im = static_cast<float>(sample.imag());
        std::memcpy(bytes->data() + 8 * i, &re, 4);
        std::memcpy(bytes->data() + 8 * i + 4, &im, 4);
    }
    IqBlock block;
    block.first_sample_index = segment_index * 100'000U;
    block.timestamp_ns = 123'000 + segment_index * 10'000;
    block.center_frequency_hz = center; block.sample_rate_hz = rate;
    block.sample_format = SampleFormat::ComplexFloat32Le;
    block.sample_count = physical_fft; block.samples = bytes;
    block.config_generation = 11 + segment_index;
    block.flags = segment_index == 0 ? QualityFlag::None : QualityFlag::IqDropped;
    CpuDspOptions options;
    options.source.source_id = "app04-61m44-36m-synthetic";
    options.source.display_name = "Synthetic geometry, no physical RX";
    auto backend = make_cpu_dsp_backend(options);
    DspConfig config;
    config.fft_size = physical_fft; config.hop_size = physical_fft;
    config.window = window; config.detector = DetectorType::Sample;
    config.unit = unit; config.precision_mode = PrecisionMode::ReferenceF64;
    config.batch_size = 1; config.averaging_frames = 1;
    backend->configure(config);
    backend->push_iq(block);
    return backend->poll_spectrum(0).at(0);
}

// Independent slow reference: search each requested frequency afresh and
// interpolate LINEAR power. No production accumulator/reducer is reused.
inline double interpolated_power(const SpectrumFrame& frame, double frequency) {
    const auto& x = *frame.frequencies_hz;
    const auto upper = std::lower_bound(x.begin(), x.end(), frequency);
    require(upper != x.end(), "oracle target outside native FFT");
    const auto right = static_cast<std::size_t>(upper - x.begin());
    const auto power = [&frame](std::size_t i) { return std::pow(10.0, (*frame.values)[i] / 10.0); };
    if (*upper == frequency) return power(right);
    require(right != 0, "oracle target before native FFT");
    const auto left = right - 1;
    const auto fraction = (frequency - x[left]) / (x[right] - x[left]);
    return (1 - fraction) * power(left) + fraction * power(right);
}

inline void run() {
    std::uint64_t checked_bins{};
    for (const auto bins : {1024U, 4096U, 16384U}) {
        for (const auto window : {WindowType::Rectangular, WindowType::Hann}) {
            for (const auto unit : {SpectrumUnit::DbfsBin, SpectrumUnit::DbfsHz}) {
                const std::array<SpectrumFrame, 2> frames{spectrum(bins, window, unit, 0), spectrum(bins, window, unit, 1)};
                SweepLineDefinition definition;
                definition.source = frames[0].source; definition.epoch = 108;
                definition.start_frequency_hz = 2'412'000'000.0; definition.stop_frequency_hz = 2'478'000'000.0;
                definition.target_spacing_hz = window_hz / bins;
                definition.analysis_window_hz = window_hz; definition.analysis_bins_per_usable_window = bins;
                definition.physical_fft_bin_width_hz = rate / (2 * bins);
                definition.physical_fft_size = 2 * bins; definition.unit = unit;
                definition.segments = {{0, 11, 2'412'000'000.0, 2'448'000'000.0},
                                       {1, 12, 2'442'000'000.0, 2'478'000'000.0}};
                std::vector<float> forward;
                for (const auto first : {0U, 1U}) {
                    ContinuousSweepLineAssembler assembler(definition);
                    require(assembler.admit(1, frames[first].timestamp_ns, {first, frames[first]}).empty(),
                            "one segment prematurely completed two-window line");
                    const auto preview = *assembler.preview(1);
                    const auto second = 1 - first;
                    const auto result = assembler.admit(1, frames[second].timestamp_ns, {second, frames[second]}).at(0);
                    require(result.state == SweepLineState::Complete && result.acquired_segments.size() == 2 &&
                            result.values->size() == static_cast<std::size_t>(std::ceil(66e6 / definition.target_spacing_hz)),
                            "61.44M/36M geometry did not complete");
                    for (std::size_t i = 0; i < result.values->size(); ++i) {
                        const double f = definition.start_frequency_hz + i * definition.target_spacing_hz;
                        double sum = 0;
                        unsigned contributors = 0, quality = 0;
                        int source = -1;
                        for (unsigned segment = 0; segment < 2; ++segment) {
                            const auto& range = definition.segments[segment];
                            if (f < range.usable_start_hz || f > range.usable_stop_hz) continue;
                            sum += interpolated_power(frames[segment], f);
                            ++contributors; quality |= static_cast<unsigned>(frames[segment].quality_flags);
                            if (source < 0) source = static_cast<int>(segment);
                        }
                        require(contributors != 0, "unexpected planned grid hole");
                        if (contributors == 2) quality |= static_cast<unsigned>(QualityFlag::StitchOverlap);
                        const auto expected = sum == 0 ? -std::numeric_limits<double>::infinity()
                                                      : 10 * std::log10(sum / contributors);
                        const auto actual = (*result.values)[i];
                        require((actual == expected || std::abs(actual - expected) <= 1e-4) &&
                                (*result.frequencies_hz)[i] == f && (*result.quality_flags_per_bin)[i] == quality &&
                                (*result.source_segment_indices)[i] == source, "resampled seam differs from independent power oracle");
                        ++checked_bins;
                    }
                    if (first == 0) forward = *result.values;
                    else require(forward == *result.values, "segment arrival order changed seam values");
                    const auto peak = static_cast<std::size_t>(std::max_element(result.values->begin(), result.values->end()) - result.values->begin());
                    require(std::abs((*result.frequencies_hz)[peak] - 2'445'000'000.0) <= definition.target_spacing_hz,
                            "seam tone peak moved beyond one output bin");
                    require(result.acquired_segments[0].first_sample_index == frames[0].first_sample_index &&
                            result.acquired_segments[1].timestamp_ns == frames[1].timestamp_ns &&
                            preview.pending_segment_indices.size() == 1,
                            "seam assembly lost provenance or mutated retained preview");
                }
            }
        }
    }
    std::cout << "APP-04 61.44M/36M seam: 12 DSP profiles x 2 arrival orders, " << checked_bins
              << " bins vs independent linear-power oracle; tolerance 1e-4 dB\n";
}
}  // namespace app04_geometry_test
