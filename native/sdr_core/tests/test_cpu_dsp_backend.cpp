#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/dual_rx_dsp.hpp"
#include "sdr_core/engine.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/window.hpp"

#include <algorithm>
#include <cmath>
#include <chrono>
#include <complex>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

constexpr double two_pi = 6.28318530717958647692528676655900577;

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void expect_close(
    const double actual,
    const double expected,
    const double tolerance,
    const std::string& message
) {
    if (!(std::fabs(actual - expected) <= tolerance)) {
        throw std::runtime_error(
            message + ": actual=" + std::to_string(actual) +
            " expected=" + std::to_string(expected) +
            " tolerance=" + std::to_string(tolerance)
        );
    }
}

std::vector<std::complex<double>> tone(
    const std::uint32_t count,
    const double sample_rate,
    const double frequency_offset,
    const double amplitude
) {
    std::vector<std::complex<double>> result(count);
    for (std::uint32_t k = 0U; k < count; ++k) {
        const double phase = two_pi * frequency_offset * static_cast<double>(k) / sample_rate;
        result[k] = amplitude * std::complex<double>(std::cos(phase), std::sin(phase));
    }
    return result;
}

sdr_core::IqBlock make_cf32_block(
    const std::vector<std::complex<double>>& samples,
    const std::uint64_t first_sample_index,
    const double sample_rate,
    const double center_frequency
) {
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(samples.size() * 8U);
    for (std::size_t index = 0; index < samples.size(); ++index) {
        const float re = static_cast<float>(samples[index].real());
        const float im = static_cast<float>(samples[index].imag());
        std::memcpy(bytes->data() + index * 8U, &re, sizeof(re));
        std::memcpy(bytes->data() + index * 8U + 4U, &im, sizeof(im));
    }
    sdr_core::IqBlock block;
    block.source_sequence = 0U;
    block.first_sample_index = first_sample_index;
    block.timestamp_ns = 1;
    block.center_frequency_hz = center_frequency;
    block.sample_rate_hz = sample_rate;
    block.sample_format = sdr_core::SampleFormat::ComplexFloat32Le;
    block.sample_count = static_cast<std::uint32_t>(samples.size());
    block.flags = sdr_core::QualityFlag::None;
    block.samples = std::move(bytes);
    block.config_generation = 1U;
    return block;
}

sdr_core::IqBlock make_ci16_block(
    const std::vector<std::pair<std::int16_t, std::int16_t>>& samples,
    const std::uint64_t first_sample_index,
    const double sample_rate,
    const double center_frequency,
    const sdr_core::SampleFormat format = sdr_core::SampleFormat::ComplexInt16Le
) {
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(samples.size() * 4U);
    for (std::size_t index = 0; index < samples.size(); ++index) {
        const auto re = static_cast<std::uint16_t>(samples[index].first);
        const auto im = static_cast<std::uint16_t>(samples[index].second);
        (*bytes)[index * 4U] = static_cast<std::uint8_t>(re & 0xFFU);
        (*bytes)[index * 4U + 1U] = static_cast<std::uint8_t>(re >> 8U);
        (*bytes)[index * 4U + 2U] = static_cast<std::uint8_t>(im & 0xFFU);
        (*bytes)[index * 4U + 3U] = static_cast<std::uint8_t>(im >> 8U);
    }
    sdr_core::IqBlock block;
    block.source_sequence = 0U;
    block.first_sample_index = first_sample_index;
    block.timestamp_ns = 1;
    block.center_frequency_hz = center_frequency;
    block.sample_rate_hz = sample_rate;
    block.sample_format = format;
    block.sample_count = static_cast<std::uint32_t>(samples.size());
    block.flags = sdr_core::QualityFlag::None;
    block.samples = std::move(bytes);
    block.config_generation = 1U;
    return block;
}

sdr_core::IqBlock make_ci8_block(
    const std::vector<std::pair<std::int8_t, std::int8_t>>& samples,
    const std::uint64_t first_sample_index,
    const double sample_rate,
    const double center_frequency
) {
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(samples.size() * 2U);
    for (std::size_t index = 0; index < samples.size(); ++index) {
        std::memcpy(bytes->data() + index * 2U, &samples[index].first, 1U);
        std::memcpy(bytes->data() + index * 2U + 1U, &samples[index].second, 1U);
    }
    sdr_core::IqBlock block;
    block.first_sample_index = first_sample_index;
    block.timestamp_ns = 1;
    block.center_frequency_hz = center_frequency;
    block.sample_rate_hz = sample_rate;
    block.sample_format = sdr_core::SampleFormat::ComplexInt8Interleaved;
    block.sample_count = static_cast<std::uint32_t>(samples.size());
    block.samples = std::move(bytes);
    block.config_generation = 1U;
    return block;
}

sdr_core::DspConfig make_config(
    const std::uint32_t fft_size,
    const std::uint32_t hop_size,
    const sdr_core::WindowType window,
    const sdr_core::DetectorType detector = sdr_core::DetectorType::Sample,
    const sdr_core::SpectrumUnit unit = sdr_core::SpectrumUnit::DbfsBin,
    const sdr_core::PrecisionMode precision = sdr_core::PrecisionMode::ReferenceF64,
    const std::uint32_t averaging = 1U
) {
    sdr_core::DspConfig config;
    config.fft_size = fft_size;
    config.hop_size = hop_size;
    config.window = window;
    config.detector = detector;
    config.unit = unit;
    config.precision_mode = precision;
    config.averaging_frames = averaging;
    return config;
}

std::size_t peak_bin(const sdr_core::SpectrumFrame& frame) {
    std::size_t best = 0U;
    float best_value = -std::numeric_limits<float>::infinity();
    for (std::size_t k = 0; k < frame.values->size(); ++k) {
        if ((*frame.values)[k] > best_value) {
            best_value = (*frame.values)[k];
            best = k;
        }
    }
    return best;
}

// Exact-bin tone must land at 0 dBFS/bin for every window (oracle semantics).
void test_exact_bin_tone_all_windows() {
    expect(!sdr_core::SpectrumFrame{}.window_normalization_version.has_value(),
           "historical frame fabricated normalization semantics");
    constexpr std::uint32_t n = 1024U;
    constexpr double rate = 1'024'000.0;
    constexpr double center = 100'000'000.0;
    constexpr std::uint32_t bin = 321U;
    const auto samples = tone(n, rate, 1000.0 * static_cast<double>(bin), 1.0);
    const sdr_core::WindowType windows[] = {
        sdr_core::WindowType::Rectangular,
        sdr_core::WindowType::Hann,
        sdr_core::WindowType::BlackmanHarris4Term,
        sdr_core::WindowType::FlatTop,
        sdr_core::WindowType::Nuttall,
        sdr_core::WindowType::Kaiser,
    };
    for (const auto window : windows) {
        auto backend = sdr_core::make_cpu_dsp_backend({});
        backend->configure(make_config(n, n, window));
        backend->push_iq(make_cf32_block(samples, 0U, rate, center));
        const auto frames = backend->poll_spectrum(0U);
        expect(frames.size() == 1U, "expected exactly one frame");
        expect(frames.front().window_normalization_version == "power-norm-v1",
               "CPU tone producer omitted exact power normalization version");
        sdr_core::validate(frames.front());
        expect(
            peak_bin(frames.front()) == n / 2U + bin,
            std::string("peak bin identity failed for window ") +
                std::string(sdr_core::to_wire(window))
        );
        // The SpectrumFrame contract stores float32 dB values; the peak
        // tolerance accounts for the f32 output quantization (the tighter
        // golden bound of 5e-5 dB is checked in the Python parity tests).
        expect_close(
            static_cast<double>((*frames.front().values)[n / 2U + bin]),
            0.0,
            1e-6,
            std::string("exact-bin peak must be 0 dBFS/bin for window ") +
                std::string(sdr_core::to_wire(window))
        );
    }
}

void test_parseval_psd_integration() {
    constexpr std::uint32_t n = 1024U;
    constexpr double rate = 1'024'000.0;
    constexpr double center = 100'000'000.0;
    const auto window = sdr_core::WindowType::BlackmanHarris4Term;
    auto first = tone(n, rate, 100'000.0, 0.5);
    const auto second = tone(n, rate, -200'000.0, 0.25);
    for (std::uint32_t k = 0U; k < n; ++k) {
        first[k] += second[k];
    }
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(
        make_config(n, n, window, sdr_core::DetectorType::Sample, sdr_core::SpectrumUnit::DbfsHz)
    );
    backend->push_iq(make_cf32_block(first, 0U, rate, center));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "expected one PSD frame");
    expect(frames.front().window_normalization_version == "power-norm-v1",
           "CPU PSD producer omitted exact power normalization version");

    const auto metrics = sdr_core::window_metrics(window, n, rate);
    double windowed_power = 0.0;
    double sum_w2 = 0.0;
    for (std::uint32_t k = 0U; k < n; ++k) {
        const auto weighted = first[k] * metrics.coefficients[k];
        windowed_power += std::norm(weighted);
        sum_w2 += metrics.coefficients[k] * metrics.coefficients[k];
    }
    const double expected_power = windowed_power / sum_w2;

    const double bin_width = rate / static_cast<double>(n);
    double integrated = 0.0;
    for (const float db : *frames.front().values) {
        integrated += std::pow(10.0, static_cast<double>(db) / 10.0);
    }
    integrated *= bin_width;
    // float32 dB roundtrip limits precision; tolerance documented in the report.
    expect_close(
        integrated,
        expected_power,
        expected_power * 5e-6,
        "PSD integration must recover windowed power (Parseval)"
    );
}

void test_detectors_linear_domain() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    constexpr std::uint32_t bin = 10U;
    const std::pair<sdr_core::DetectorType, double> cases[] = {
        {sdr_core::DetectorType::Sample, 0.0625},
        {sdr_core::DetectorType::Peak, 1.0},
        {sdr_core::DetectorType::NegativePeak, 0.0625},
        {sdr_core::DetectorType::AveragePower, (1.0 + 0.25 + 0.0625) / 3.0},
        {sdr_core::DetectorType::Rms, (1.0 + 0.25 + 0.0625) / 3.0},
    };
    for (const auto& [detector, expected_power] : cases) {
        auto backend = sdr_core::make_cpu_dsp_backend({});
        backend->configure(make_config(n, n, sdr_core::WindowType::Rectangular, detector,
                                       sdr_core::SpectrumUnit::DbfsBin,
                                       sdr_core::PrecisionMode::ReferenceF64, 3U));
        const double amplitudes[] = {1.0, 0.5, 0.25};
        for (std::uint32_t block_index = 0U; block_index < 3U; ++block_index) {
            backend->push_iq(make_cf32_block(
                tone(n, rate, 1000.0 * bin, amplitudes[block_index]),
                block_index * n,
                rate,
                center
            ));
        }
        const auto frames = backend->poll_spectrum(0U);
        expect(frames.size() == 1U, "averaging must emit exactly one frame");
        const double measured =
            std::pow(10.0, static_cast<double>((*frames.front().values)[n / 2U + bin]) / 10.0);
        expect_close(
            measured,
            expected_power,
            expected_power * 1e-6 + 1e-12,
            std::string("detector mismatch for ") + std::string(sdr_core::to_wire(detector))
        );
    }
}

// R04 allocates only the accumulator selected by detector and precision mode.
// Exercise every valid combination so an unallocated inactive accumulator can
// neither be read nor accidentally become part of the numerical path.
void test_detectors_all_precision_modes() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    constexpr std::uint32_t bin = 10U;
    const std::pair<sdr_core::DetectorType, double> cases[] = {
        {sdr_core::DetectorType::Sample, 0.0625},
        {sdr_core::DetectorType::Peak, 1.0},
        {sdr_core::DetectorType::NegativePeak, 0.0625},
        {sdr_core::DetectorType::AveragePower, (1.0 + 0.25 + 0.0625) / 3.0},
        {sdr_core::DetectorType::Rms, (1.0 + 0.25 + 0.0625) / 3.0},
    };
    const sdr_core::PrecisionMode modes[] = {
        sdr_core::PrecisionMode::ReferenceF64,
        sdr_core::PrecisionMode::AccurateF32F64Accum,
        sdr_core::PrecisionMode::FastF32,
    };
    const double amplitudes[] = {1.0, 0.5, 0.25};

    for (const auto mode : modes) {
        for (const auto& [detector, expected_power] : cases) {
            auto backend = sdr_core::make_cpu_dsp_backend({});
            backend->configure(make_config(
                n,
                n,
                sdr_core::WindowType::Rectangular,
                detector,
                sdr_core::SpectrumUnit::DbfsBin,
                mode,
                3U
            ));
            for (std::uint32_t block_index = 0U; block_index < 3U; ++block_index) {
                backend->push_iq(make_cf32_block(
                    tone(n, rate, 1'000.0 * bin, amplitudes[block_index]),
                    block_index * n,
                    rate,
                    center
                ));
            }
            const auto frames = backend->poll_spectrum(0U);
            expect(frames.size() == 1U, "detector/precision averaging must emit one frame");
            const double measured = std::pow(
                10.0,
                static_cast<double>((*frames.front().values)[n / 2U + bin]) / 10.0
            );
            const double tolerance = mode == sdr_core::PrecisionMode::FastF32
                                         ? expected_power * 5e-5 + 1e-9
                                         : expected_power * 2e-6 + 1e-12;
            expect_close(
                measured,
                expected_power,
                tolerance,
                std::string("detector/precision mismatch for ") +
                    std::string(sdr_core::to_wire(detector)) + "/" +
                    std::string(sdr_core::to_wire(mode))
            );
        }
    }
}

void test_overlap_continuity() {
    constexpr std::uint32_t n = 256U;
    constexpr std::uint32_t hop = 128U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, hop, sdr_core::WindowType::Rectangular));
    const auto samples = tone(n, rate, 10'000.0, 1.0);
    const auto first = std::vector<std::complex<double>>(samples.begin(), samples.begin() + hop);
    const auto second = std::vector<std::complex<double>>(samples.begin() + hop, samples.end());
    backend->push_iq(make_cf32_block(first, 0U, rate, center));
    expect(backend->poll_spectrum(0U).empty(), "no frame before fft_size samples");
    backend->push_iq(make_cf32_block(second, hop, rate, center));
    auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "frame must appear at fft_size samples");
    expect(frames.front().first_sample_index == 0U, "frame 0 index");
    backend->push_iq(make_cf32_block(first, n, rate, center));
    frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "frame must appear every hop");
    expect(frames.front().first_sample_index == hop, "frame 1 index");
    backend->push_iq(make_cf32_block(second, n + hop, rate, center));
    frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "frame stream must stay continuous");
    expect(frames.front().first_sample_index == 2U * hop, "frame 2 index");
    sdr_core::validate(frames.front());
}

void test_repeated_blocks_deterministic() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, n, sdr_core::WindowType::Hann));
    const auto samples = tone(n, rate, 32'000.0, 0.75);
    backend->push_iq(make_cf32_block(samples, 0U, rate, center));
    backend->push_iq(make_cf32_block(samples, n, rate, center));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 2U, "expected two frames");
    for (std::size_t k = 0; k < n; ++k) {
        expect(
            (*frames[0].values)[k] == (*frames[1].values)[k],
            "identical input must produce bit-identical frames"
        );
    }
}

void test_fft_timestamps_follow_sample_offsets() {
    constexpr std::uint32_t n = 256U;
    constexpr std::uint32_t hop = 64U;
    constexpr double rate = 256'000.0;
    constexpr std::int64_t block_start_ns = 1'000'000'000LL;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, hop, sdr_core::WindowType::Hann));
    auto block = make_cf32_block(
        tone(512U, rate, 32'000.0, 0.5),
        0U,
        rate,
        50'000'000.0
    );
    block.timestamp_ns = block_start_ns;
    backend->push_iq(block);
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 5U, "expected five overlapping FFT frames");
    for (const auto& frame : frames) {
        const auto expected = block_start_ns + static_cast<std::int64_t>(
            std::llround(
                static_cast<double>(frame.first_sample_index) * 1.0e9 / rate
            )
        );
        expect(
            frame.timestamp_ns == expected,
            "FFT timestamp does not follow first_sample_index"
        );
    }
}

void test_reset() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, n, sdr_core::WindowType::Hann));
    const auto samples = tone(n, rate, 32'000.0, 0.75);
    backend->push_iq(make_cf32_block(
        std::vector<std::complex<double>>(samples.begin(), samples.begin() + 100),
        0U,
        rate,
        center
    ));
    backend->reset();
    expect(backend->poll_spectrum(0U).empty(), "reset must drop pending state");
    backend->push_iq(make_cf32_block(samples, 0U, rate, center));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "stream must restart after reset");
    expect(frames.front().first_sample_index == 0U, "index map must rebase after reset");
}

void test_non_finite_block_dropped() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, n, sdr_core::WindowType::Hann));
    auto samples = tone(n, rate, 32'000.0, 0.75);
    samples[42] = std::complex<double>(
        std::numeric_limits<double>::quiet_NaN(),
        0.0
    );
    backend->push_iq(make_cf32_block(samples, 0U, rate, center));
    expect(backend->poll_spectrum(0U).empty(), "non-finite block must not produce frames");
    expect(backend->metrics().fft_frames_dropped == 1U, "drop must be counted");
    expect(backend->metrics().samples_processed == 0U, "bad samples must not be counted");
}

void test_stage_timing_contract() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, n, sdr_core::WindowType::Hann));
    backend->push_iq(make_cf32_block(tone(n, rate, 10'000.0, 0.5), 0U, rate, center));
    static_cast<void>(backend->poll_spectrum(0U));
    const auto metrics = backend->metrics();
    const auto required =
        sdr_core::stage_timing_mask(sdr_core::DspStageTimingFlag::InputUnpack) |
        sdr_core::stage_timing_mask(sdr_core::DspStageTimingFlag::Window) |
        sdr_core::stage_timing_mask(sdr_core::DspStageTimingFlag::Fft) |
        sdr_core::stage_timing_mask(sdr_core::DspStageTimingFlag::Detector);
    if (metrics.stage_timing_mask == 0U) {
        expect(
            metrics.input_unpack_ns == 0U && metrics.window_ns == 0U &&
                metrics.fft_ns == 0U && metrics.detector_ns == 0U,
            "disabled CPU profiler must not report ambiguous stage durations"
        );
        return;
    }
    expect(
        (metrics.stage_timing_mask & required) == required,
        "enabled CPU profiler must mark every measured DSP stage"
    );
    expect(
        metrics.input_unpack_ns > 0U && metrics.window_ns > 0U &&
            metrics.fft_ns > 0U && metrics.detector_ns > 0U,
        "enabled CPU profiler must report positive cumulative stage durations"
    );
}

void test_ci16_unpack_and_clipping_flag() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, n, sdr_core::WindowType::Rectangular));
    const std::vector<std::pair<std::int16_t, std::int16_t>> samples(
        n, {std::int16_t{16384}, std::int16_t{0}}
    );
    backend->push_iq(make_ci16_block(samples, 0U, rate, center));
    auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "expected one frame");
    // DC component: (16384/32768)^2 = 0.25 -> -6.0206 dBFS/bin.
    expect_close(
        static_cast<double>((*frames.front().values)[n / 2U]),
        10.0 * std::log10(0.25),
        1e-3,
        "ci16 unpack normalization"
    );

    const std::vector<std::pair<std::int16_t, std::int16_t>> clipped(n, {32767, -32768});
    backend->push_iq(make_ci16_block(clipped, n, rate, center));
    frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "expected second frame");
    expect(
        sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::AdcOverload),
        "clipping must set ADC_OVERLOAD"
    );
}

void test_dc_removal_block_mean() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto samples = tone(n, rate, 40'000.0, 0.5);
    for (auto& sample : samples) {
        sample += std::complex<double>(0.5, 0.0);
    }
    sdr_core::CpuDspOptions options;
    options.dc_removal = sdr_core::DcRemovalMode::BlockMean;
    auto backend = sdr_core::make_cpu_dsp_backend(options);
    backend->configure(make_config(n, n, sdr_core::WindowType::Rectangular));
    backend->push_iq(make_cf32_block(samples, 0U, rate, center));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "expected one frame");
    expect(
        sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::DcRemoved),
        "DC removal must be flagged"
    );
    expect(
        static_cast<double>((*frames.front().values)[n / 2U]) < -80.0,
        "BLOCK_MEAN must suppress the DC bin"
    );
    expect(peak_bin(frames.front()) == n / 2U + 40U, "tone bin must survive DC removal");
}

// Independent DCSP oracle: direct DFT, independent symmetric windows and
// long-double arithmetic. Do not reuse the producer's FFT/window/unpack code.
// Inputs below are the ACTUAL quantized samples sent to the backend, not the
// original pre-quantization waveform. Compare power, since dB error at zero is
// undefined. PSD is converted to power per bin using the actual Fs/N.
using OracleComplex = std::complex<long double>;
constexpr long double oracle_pi = 3.14159265358979323846264338327950288L;

long double oracle_window(const sdr_core::WindowType window, const std::size_t k,
                          const std::size_t n) {
    const long double phase = 2.0L * oracle_pi * k / (n - 1U);
    switch (window) {
    case sdr_core::WindowType::Rectangular: return 1.0L;
    case sdr_core::WindowType::Hann: return (1.0L - std::cos(phase)) / 2.0L;
    case sdr_core::WindowType::BlackmanHarris4Term:
        return 0.35875L - 0.48829L * std::cos(phase) +
               0.14128L * std::cos(2.0L * phase) - 0.01168L * std::cos(3.0L * phase);
    case sdr_core::WindowType::FlatTop:
        return 0.21557895L - 0.41663158L * std::cos(phase) +
               0.277263158L * std::cos(2.0L * phase) -
               0.083578947L * std::cos(3.0L * phase) + 0.006947368L * std::cos(4.0L * phase);
    case sdr_core::WindowType::Nuttall:
        return 0.355768L - 0.487396L * std::cos(phase) +
               0.144232L * std::cos(2.0L * phase) - 0.012604L * std::cos(3.0L * phase);
    case sdr_core::WindowType::Kaiser: {
        const long double position = 2.0L * k / (n - 1U) - 1.0L;
        // Library Bessel implementation, not the producer's power series.
        return std::cyl_bessel_i(0.0L, 8.6L * std::sqrt(std::max(0.0L, 1.0L - position * position))) /
               std::cyl_bessel_i(0.0L, 8.6L);
    }
    }
    throw std::runtime_error("unknown oracle window");
}

std::vector<double> oracle_power(const std::vector<std::complex<double>>& samples,
                                const sdr_core::WindowType window,
                                const bool block_mean, const sdr_core::SpectrumUnit unit,
                                const std::vector<std::size_t>& selected = {}) {
    const auto n = samples.size();
    OracleComplex mean{};
    if (block_mean) {
        for (const auto value : samples) { mean += OracleComplex(value.real(), value.imag()); }
        mean /= static_cast<long double>(n);
    }
    std::vector<OracleComplex> staged(n);
    long double sum_w = 0.0L;
    long double sum_w2 = 0.0L;
    for (std::size_t k = 0; k < n; ++k) {
        const auto coefficient = oracle_window(window, k, n);
        staged[k] = (OracleComplex(samples[k].real(), samples[k].imag()) - mean) * coefficient;
        sum_w += coefficient;
        sum_w2 += coefficient * coefficient;
    }
    const long double denominator = unit == sdr_core::SpectrumUnit::DbfsHz
        ? static_cast<long double>(n) * sum_w2 : sum_w * sum_w;
    // PSD * (Fs/N) cancels Fs; this normalized linear power is the comparison
    // domain for both units. The producer still receives the requested Fs.
    std::vector<double> result(n);
    for (std::size_t bin = 0; bin < n; ++bin) {
        if (!selected.empty() && std::find(selected.begin(), selected.end(), bin) == selected.end()) { continue; }
        const auto frequency = static_cast<std::int64_t>(bin) - static_cast<std::int64_t>(n / 2U);
        OracleComplex value{};
        for (std::size_t k = 0; k < n; ++k) {
            const long double phase = -2.0L * oracle_pi * frequency * k / n;
            value += staged[k] * OracleComplex(std::cos(phase), std::sin(phase));
        }
        result[bin] = static_cast<double>(std::norm(value) / denominator);
    }
    return result;
}

void expect_oracle(const sdr_core::SpectrumFrame& frame, const std::vector<double>& expected,
                   const std::string& cell, const std::vector<std::size_t>& selected = {}) {
    expect(frame.values && frame.values->size() == expected.size(), cell + " grid size");
    // Frozen before any producer change. Includes float32 dB serialization,
    // float32 window/mean/FFT and quantized input (already decoded in oracle).
    // Absolute floor is in normalized POWER, not an ignored-bin/dB mask.
    const bool reference = frame.precision_mode == sdr_core::PrecisionMode::ReferenceF64;
    const double relative = reference ? 5e-6 : 2e-5;
    const double absolute = reference ? 2e-14 : 1e-11;
    for (std::size_t k = 0; k < expected.size(); ++k) {
        if (!selected.empty() && std::find(selected.begin(), selected.end(), k) == selected.end()) { continue; }
        const double db = (*frame.values)[k];
        expect(!std::isnan(db) && db != std::numeric_limits<double>::infinity(), cell + " nonfinite");
        double power = std::pow(10.0, db / 10.0);
        if (frame.unit == sdr_core::SpectrumUnit::DbfsHz) { power *= frame.sample_rate_hz / frame.fft_size; }
        expect_close(power, expected[k], absolute + relative * expected[k],
                     cell + " bin=" + std::to_string(k));
    }
}

std::vector<std::complex<double>> dc_fixture(const std::uint32_t n, const unsigned fixture) {
    std::vector<std::complex<double>> result(n);
    std::uint32_t seed = 0x6d2b79f5U;
    for (std::uint32_t k = 0; k < n; ++k) {
        const auto sinusoid = [n, k](const double bin, const double amplitude) {
            const double phase = two_pi * bin * k / n;
            return amplitude * std::complex<double>(std::cos(phase), std::sin(phase));
        };
        seed = 1664525U * seed + 1013904223U;
        const double re = static_cast<double>((seed >> 8U) & 0xffffU) / 65535.0 - 0.5;
        seed = 1664525U * seed + 1013904223U;
        const double im = static_cast<double>((seed >> 8U) & 0xffffU) / 65535.0 - 0.5;
        switch (fixture) {
        case 0U: result[k] = {0.25, -0.125}; break; // Genuine RF at LO is ALSO removed.
        case 1U:
            result[k] = std::complex<double>(0.2, 0.1) + sinusoid(31.0, 0.15) +
                        sinusoid(0.75, 0.025) + 0.025 * std::complex<double>(re, im);
            break;
        case 2U:
            result[k] = sinusoid(-0.45, 0.3) + sinusoid(20.25, 0.12) + sinusoid(22.9, 0.012);
            break; // No injected DC: finite-block mean of genuine near-DC tone is nonzero.
        case 3U:
            result[k] = 0.08 * std::complex<double>(re, im);
            if (k >= n / 7U && k < n / 3U) { result[k] += sinusoid(0.35, 0.35); }
            break; // Short near-DC burst + broadband across center.
        default: throw std::runtime_error("unknown DC fixture");
        }
    }
    return result;
}

struct OracleInput {
    sdr_core::IqBlock block;
    std::vector<std::complex<double>> decoded;
};

OracleInput quantized_input(const std::vector<std::complex<double>>& samples,
                            const sdr_core::SampleFormat format, const double rate,
                            const std::uint64_t first = 0U) {
    OracleInput result;
    result.decoded.reserve(samples.size());
    if (format == sdr_core::SampleFormat::ComplexFloat32Le) {
        for (const auto value : samples) {
            result.decoded.emplace_back(static_cast<float>(value.real()), static_cast<float>(value.imag()));
        }
        result.block = make_cf32_block(result.decoded, first, rate, 100'000'000.0);
    } else if (format == sdr_core::SampleFormat::ComplexInt8Interleaved) {
        std::vector<std::pair<std::int8_t, std::int8_t>> integer;
        for (const auto value : samples) {
            const auto re = static_cast<std::int8_t>(std::lround(value.real() * 128.0));
            const auto im = static_cast<std::int8_t>(std::lround(value.imag() * 128.0));
            integer.emplace_back(re, im);
            result.decoded.emplace_back(re / 128.0, im / 128.0);
        }
        result.block = make_ci8_block(integer, first, rate, 100'000'000.0);
    } else {
        const double scale = format == sdr_core::SampleFormat::ComplexInt12InInt16Le ? 2048.0 : 32768.0;
        std::vector<std::pair<std::int16_t, std::int16_t>> integer;
        for (const auto value : samples) {
            const auto re = static_cast<std::int16_t>(std::lround(value.real() * scale));
            const auto im = static_cast<std::int16_t>(std::lround(value.imag() * scale));
            integer.emplace_back(re, im);
            result.decoded.emplace_back(re / scale, im / scale);
        }
        result.block = make_ci16_block(integer, first, rate, 100'000'000.0, format);
    }
    return result;
}

void test_dc_removal_independent_oracle_matrix() {
    const sdr_core::WindowType windows[] = {sdr_core::WindowType::Rectangular, sdr_core::WindowType::Hann,
        sdr_core::WindowType::BlackmanHarris4Term, sdr_core::WindowType::FlatTop,
        sdr_core::WindowType::Nuttall, sdr_core::WindowType::Kaiser};
    const sdr_core::SampleFormat formats[] = {sdr_core::SampleFormat::ComplexInt8Interleaved,
        sdr_core::SampleFormat::ComplexInt12InInt16Le, sdr_core::SampleFormat::ComplexInt16Le,
        sdr_core::SampleFormat::ComplexFloat32Le};
    const sdr_core::PrecisionMode precisions[] = {sdr_core::PrecisionMode::ReferenceF64,
        sdr_core::PrecisionMode::AccurateF32F64Accum, sdr_core::PrecisionMode::FastF32};
    const sdr_core::SpectrumUnit units[] = {sdr_core::SpectrumUnit::DbfsBin, sdr_core::SpectrumUnit::DbfsHz};
    std::size_t cells = 0U;
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 61'440'000.0; // Clock/grid test only, NOT physical throughput.
    for (const auto format : formats) {
        for (unsigned fixture = 0U; fixture < 4U; ++fixture) {
            const auto input = quantized_input(dc_fixture(n, fixture), format, rate);
            const auto unchanged_raw = *input.block.samples;
            for (const auto window : windows) {
                for (const auto unit : units) {
                    for (const bool dc : {false, true}) {
                        const auto oracle = oracle_power(input.decoded, window, dc, unit);
                        for (const auto precision : precisions) {
                            sdr_core::DspOptions options;
                            options.dc_removal = dc ? sdr_core::DcRemovalMode::BlockMean : sdr_core::DcRemovalMode::Off;
                            auto backend = sdr_core::make_cpu_dsp_backend(options);
                            backend->configure(make_config(n, n, window, sdr_core::DetectorType::Sample, unit, precision));
                            backend->push_iq(input.block);
                            const auto frames = backend->poll_spectrum(0U);
                            expect(frames.size() == 1U, "DC oracle expected one frame");
                            expect_oracle(frames.front(), oracle, "DC matrix cell=" + std::to_string(cells));
                            expect(sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::DcRemoved) == dc,
                                   "DC quality flag must match actual selected mode");
                            expect(frames.front().dsp_processing_recipe.has_value() &&
                                   frames.front().dsp_processing_recipe->dc_removal() == options.dc_removal &&
                                   frames.front().dsp_processing_recipe->whole_frame_modified() == dc,
                                   "actual CPU recipe must accompany each analytical frame");
                            expect(*input.block.samples == unchanged_raw, "DC DSP overwrote raw IQ tap");
                            ++cells;
                        }
                    }
                }
            }
        }
    }
    expect(cells == 1152U, "incomplete DC oracle matrix");
    std::cout << "DC oracle matrix: " << cells << " full spectra checked\n";
}

void test_dc_removal_overlap_group_flush_and_reset() {
    constexpr std::uint32_t n = 1024U;
    constexpr std::uint32_t hop = 333U; // Non-divisor, not only half-hop.
    constexpr double rate = 20'000'000.0;
    const auto waveform = dc_fixture(n, 3U);
    std::vector<std::complex<double>> stream = waveform;
    stream.insert(stream.end(), waveform.begin(), waveform.end());
    const auto input = quantized_input(stream, sdr_core::SampleFormat::ComplexFloat32Le, rate);
    auto config = make_config(n, hop, sdr_core::WindowType::Hann, sdr_core::DetectorType::AveragePower,
                              sdr_core::SpectrumUnit::DbfsBin, sdr_core::PrecisionMode::AccurateF32F64Accum, 3U);
    config.batch_size = 4U;
    sdr_core::DspOptions options;
    options.dc_removal = sdr_core::DcRemovalMode::BlockMean;
    auto backend = sdr_core::make_cpu_dsp_backend(options);
    backend->configure(config);
    backend->push_iq(input.block); // Four transforms: one group of 3 + an incomplete group.
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "incomplete detector group must not manufacture an average");
    std::vector<double> average(n, 0.0);
    for (std::size_t start : {0U, 333U, 666U}) {
        const std::vector<std::complex<double>> window(input.decoded.begin() + start,
                                                       input.decoded.begin() + start + n);
        const auto power = oracle_power(window, config.window, true, config.unit);
        for (std::size_t k = 0; k < n; ++k) { average[k] += power[k] / 3.0; }
    }
    expect_oracle(frames.front(), average, "overlap/linear detector group");
    backend->reset(); // Cancel the fourth transform's incomplete average.
    expect(backend->poll_spectrum(0U).empty(), "reset leaked old DC detector state");

    config.detector = sdr_core::DetectorType::Sample;
    config.averaging_frames = 1U;
    backend->configure(config);
    const auto short_input = quantized_input(waveform, sdr_core::SampleFormat::ComplexFloat32Le, rate, 5000U);
    backend->push_iq(short_input.block);
    expect(backend->poll_spectrum(0U, false).empty(), "batch=4 must remain staged until explicit flush");
    const auto flushed = backend->poll_spectrum(0U, true);
    expect(flushed.size() == 1U && flushed.front().first_sample_index == 5000U, "partial FFT batch flush");
    expect_oracle(flushed.front(), oracle_power(short_input.decoded, config.window, true, config.unit),
                  "partial batch after reset");

    auto retuned = short_input.block;
    retuned.first_sample_index = 9000U;
    retuned.source_sequence = 9U;
    retuned.config_generation = 2U;
    retuned.center_frequency_hz = 101'000'000.0;
    retuned.sample_rate_hz = 16'000'000.0;
    retuned.flags = sdr_core::QualityFlag::IqDropped;
    backend->push_iq(retuned);
    const auto fresh = backend->poll_spectrum(0U, true);
    expect(fresh.size() == 1U && fresh.front().first_sample_index == 9000U &&
           fresh.front().config_generation == 2U && fresh.front().center_frequency_hz == 101'000'000.0 &&
           fresh.front().sample_rate_hz == 16'000'000.0, "gap/retune used stale DC grid/history");
    expect(sdr_core::has_flag(fresh.front().quality_flags, sdr_core::QualityFlag::IqDropped),
           "DC processing hid an input gap");
    expect_oracle(fresh.front(), oracle_power(short_input.decoded, config.window, true, config.unit),
                  "gap/retune fresh transform");
}

void test_dc_removal_large_fft_selected_bins_and_parseval() {
    std::size_t cells = 0U;
    for (const std::uint32_t n : {1024U, 4096U, 16384U}) {
        const std::vector<std::size_t> bins = {n / 2U - 2U, n / 2U - 1U, n / 2U,
                                               n / 2U + 1U, n / 2U + 2U, n / 2U + 31U, n / 4U};
        for (const auto format : {sdr_core::SampleFormat::ComplexInt8Interleaved,
                                  sdr_core::SampleFormat::ComplexInt12InInt16Le}) {
            const auto input = quantized_input(dc_fixture(n, 1U), format, 61'440'000.0);
            for (const auto window : {sdr_core::WindowType::Hann, sdr_core::WindowType::FlatTop}) {
                const auto expected = oracle_power(input.decoded, window, true, sdr_core::SpectrumUnit::DbfsBin, bins);
                OracleComplex mean{};
                for (const auto sample : input.decoded) { mean += OracleComplex(sample.real(), sample.imag()); }
                mean /= static_cast<long double>(n);
                long double energy = 0.0L;
                long double sum_w = 0.0L;
                for (std::uint32_t k = 0; k < n; ++k) {
                    const auto w = oracle_window(window, k, n);
                    energy += std::norm((OracleComplex(input.decoded[k].real(), input.decoded[k].imag()) - mean) * w);
                    sum_w += w;
                }
                const double integrated = static_cast<double>(n * energy / (sum_w * sum_w));
                for (const auto precision : {sdr_core::PrecisionMode::ReferenceF64,
                                             sdr_core::PrecisionMode::AccurateF32F64Accum,
                                             sdr_core::PrecisionMode::FastF32}) {
                    sdr_core::DspOptions options;
                    options.dc_removal = sdr_core::DcRemovalMode::BlockMean;
                    auto backend = sdr_core::make_cpu_dsp_backend(options);
                    backend->configure(make_config(n, n, window, sdr_core::DetectorType::Sample,
                                                    sdr_core::SpectrumUnit::DbfsBin, precision));
                    backend->push_iq(input.block);
                    const auto frames = backend->poll_spectrum(0U);
                    expect(frames.size() == 1U, "large FFT DC frame count");
                    expect_oracle(frames.front(), expected, "large FFT=" + std::to_string(n), bins);
                    double actual = 0.0;
                    for (const auto db : *frames.front().values) { actual += std::pow(10.0, db / 10.0); }
                    expect_close(actual, integrated, 1e-11 + 2e-5 * integrated, "large FFT DC Parseval");
                    ++cells;
                }
            }
        }
    }
    expect(cells == 36U, "incomplete large FFT DC cells");
    std::cout << "DC large FFT: " << cells << " selected-bin/Parseval spectra checked (not full-bin oracle)\n";
}

void test_dc_removal_paired_independent_means() {
    constexpr std::uint32_t n = 256U;
    auto dsp = make_config(n, n, sdr_core::WindowType::Hann, sdr_core::DetectorType::AveragePower,
                           sdr_core::SpectrumUnit::DbfsBin, sdr_core::PrecisionMode::ReferenceF64, 2U);
    dsp.batch_size = 3U;
    sdr_core::DualRxDspConfig config;
    config.primary.dsp = dsp;
    config.secondary.dsp = dsp;
    config.primary.source = {.source_type = sdr_core::SourceType::LiveIq, .source_id = "oracle-rx1",
        .display_name = "oracle RX1", .uri = "mock:dc-oracle", .backend_id = "oracle",
        .schema_version = sdr_core::contract_schema_version};
    config.secondary.source = config.primary.source;
    config.secondary.source.source_id = "oracle-rx2";
    config.secondary.source.display_name = "oracle RX2";
    config.dc_removal_block_mean = true; // Existing common policy; do not add independent policy support.
    config.max_input_samples_per_push = 3U * n;
    const auto a = quantized_input(dc_fixture(3U * n, 1U), sdr_core::SampleFormat::ComplexFloat32Le, 30'720'000.0);
    const auto b = quantized_input(dc_fixture(3U * n, 3U), sdr_core::SampleFormat::ComplexFloat32Le, 30'720'000.0);
    const auto expected = [&](const OracleInput& input) {
        std::vector<double> average(n);
        for (std::size_t start : {0U, 256U}) {
            const std::vector<std::complex<double>> samples(input.decoded.begin() + start,
                                                           input.decoded.begin() + start + n);
            const auto power = oracle_power(samples, dsp.window, true, dsp.unit);
            for (std::size_t k = 0; k < n; ++k) { average[k] += power[k] / 2.0; }
        }
        return average;
    };
    const auto expected_a = expected(a);
    const auto expected_b = expected(b);
    const auto reservation = sdr_core::dual_rx_dsp_resource_budget(config);
    const auto slots = static_cast<std::uint64_t>(reservation.analytical_output_capacity) +
                       config.output_queue_capacity + 1U;
    expect(reservation.spectrum_backlog_bytes == slots *
           (32U * n + 2U * sizeof(std::optional<sdr_core::AnalyticalReadyRef>) +
            2U * sdr_core::dsp_processing_recipe_slot_reserved_bytes) +
           2U * sdr_core::analytical_ready_reserved_bytes(config.analytical_event_capacity),
           "paired backlog omitted inline recipe/padding storage");
    sdr_core::DualRxDspPublisher publisher;
    publisher.configure(config);
    std::size_t deliveries = 0U;
    publisher.set_analytical_consumer([&](sdr_core::DualRxSpectrumFrame& pair,
                                        const sdr_core::DspBackendMetrics&, const sdr_core::DspBackendMetrics&) {
        expect_oracle(pair.primary, expected_a, "paired RX1 before coalescing");
        expect_oracle(pair.secondary, expected_b, "paired RX2 before coalescing");
        expect(pair.primary.dsp_processing_recipe.has_value() && pair.secondary.dsp_processing_recipe.has_value() &&
               pair.primary.dsp_processing_recipe->dc_removal() == sdr_core::DcRemovalMode::BlockMean &&
               pair.secondary.dsp_processing_recipe->dc_removal() == sdr_core::DcRemovalMode::BlockMean,
               "paired recipe missing before analytical consumers/coalescing");
        expect(pair.primary.source.source_id == "oracle-rx1" && pair.secondary.source.source_id == "oracle-rx2",
               "DC oracle lost per-chain source identity");
        ++deliveries;
        return true;
    });
    publisher.push(a.block, b.block);
    publisher.flush();
    auto pairs = publisher.poll_spectrum_frames(0U);
    // Existing detector receipt is anchored to the LAST transform in its
    // averaging group (emit_frame(meta_batch_[frame])), not the first one.
    expect(pairs.size() == 1U && deliveries == 1U && pairs.front().first_sample_index == n,
           "paired DC partial group output/last-transform anchor");
    expect(sdr_core::has_flag(pairs.front().primary.quality_flags, sdr_core::QualityFlag::DcRemoved) &&
           sdr_core::has_flag(pairs.front().secondary.quality_flags, sdr_core::QualityFlag::DcRemoved),
           "both paired DC chains must report processing");
    const auto epoch = pairs.front().synchronization_epoch;
    publisher.mark_shared_gap();
    auto next_a = a.block;
    auto next_b = b.block;
    next_a.first_sample_index = next_b.first_sample_index = 9000U;
    next_a.source_sequence = next_b.source_sequence = 9U;
    next_a.config_generation = next_b.config_generation = 2U;
    publisher.push(next_a, next_b);
    publisher.flush();
    pairs = publisher.poll_spectrum_frames(0U);
    expect(pairs.size() == 1U && deliveries == 2U && pairs.front().synchronization_epoch > epoch &&
           pairs.front().config_generation == 2U && pairs.front().first_sample_index == 9000U + n,
           "paired DC reset mixed old epoch/partial detector group");
    expect(publisher.metrics().primary.fft_frames_dropped == 0U &&
           publisher.metrics().secondary.fft_frames_dropped == 0U, "paired DC oracle dropped analytical frames");
}

void test_dc_off_default_parity() {
    auto config = make_config(256U, 128U, sdr_core::WindowType::FlatTop);
    config.batch_size = 2U;
    auto defaults = sdr_core::make_cpu_dsp_backend({});
    sdr_core::DspOptions off_options;
    off_options.dc_removal = sdr_core::DcRemovalMode::Off;
    auto explicit_off = sdr_core::make_cpu_dsp_backend(off_options);
    defaults->configure(config);
    explicit_off->configure(config);
    const auto input = quantized_input(dc_fixture(1024U, 1U), sdr_core::SampleFormat::ComplexFloat32Le,
                                        30'720'000.0);
    defaults->push_iq(input.block);
    explicit_off->push_iq(input.block);
    const auto a = defaults->poll_spectrum(0U);
    const auto b = explicit_off->poll_spectrum(0U);
    expect(a.size() == 7U && a.size() == b.size(), "OFF parity frame count");
    for (std::size_t k = 0; k < a.size(); ++k) {
        expect(*a[k].values == *b[k].values && *a[k].frequencies_hz == *b[k].frequencies_hz &&
               a[k].quality_flags == b[k].quality_flags && a[k].first_sample_index == b[k].first_sample_index &&
               a[k].dropped_fft_frames_before == b[k].dropped_fft_frames_before,
               "default vs explicit OFF parity");
        expect(!sdr_core::has_flag(a[k].quality_flags, sdr_core::QualityFlag::DcRemoved), "default is not OFF");
    }
}

void test_processing_recipe_input_and_historical_unknown() {
    expect(!sdr_core::SpectrumFrame{}.dsp_processing_recipe, "historical frame guessed a processing recipe");
    const auto off = sdr_core::DspProcessingRecipeV1::from_dc_mode(sdr_core::DcRemovalMode::Off);
    const auto dc = sdr_core::DspProcessingRecipeV1::from_dc_mode(sdr_core::DcRemovalMode::BlockMean);
    expect(off.dc_algorithm() == "off" && !off.whole_frame_modified() &&
           dc.dc_algorithm() == "block_mean_v1" && dc.whole_frame_modified(), "native recipe modes");
    expect(sdr_core::DspProcessingRecipeV1::from_canonical_policy(off.canonical_policy()).policy_digest() ==
           off.policy_digest(), "native OFF canonical roundtrip");
    expect(sdr_core::DspProcessingRecipeV1::from_canonical_policy(dc.canonical_policy()).policy_digest() ==
           dc.policy_digest(), "native DC canonical roundtrip");
    for (const auto bytes : {std::string{}, std::string("{}"), std::string(off.canonical_policy()) + " ",
                             std::string(16'385U, 'x'), std::string(1U, '\0')}) {
        bool refused = false;
        try { static_cast<void>(sdr_core::DspProcessingRecipeV1::from_canonical_policy(bytes)); }
        catch (const sdr_core::ConfigurationError&) { refused = true; }
        expect(refused, "unsupported/noncanonical recipe accepted");
    }
    sdr_core::DspOptions invalid;
    invalid.dc_removal = static_cast<sdr_core::DcRemovalMode>(255U);
    bool refused = false;
    try { static_cast<void>(sdr_core::make_cpu_dsp_backend(invalid)); }
    catch (const sdr_core::ConfigurationError&) { refused = true; }
    expect(refused, "unknown native DC enum silently fell back to OFF");

    // An ingress quality bit can describe a prior stage. It must survive, but
    // cannot relabel the actual OFF stage as a newly applied BlockMean recipe.
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(256U, 256U, sdr_core::WindowType::Rectangular));
    auto input = make_cf32_block(tone(256U, 256'000.0, 20'000.0, 0.25),
                                0U, 256'000.0, 100'000'000.0);
    input.flags = sdr_core::QualityFlag::DcRemoved | sdr_core::QualityFlag::IqDropped;
    backend->push_iq(input);
    const auto output = backend->poll_spectrum(0U);
    expect(output.size() == 1U && output.front().dsp_processing_recipe &&
           output.front().dsp_processing_recipe->dc_removal() == sdr_core::DcRemovalMode::Off &&
           !output.front().dsp_processing_recipe->whole_frame_modified(),
           "ingress quality fabricated a processing recipe");
    expect(sdr_core::has_flag(output.front().quality_flags, sdr_core::QualityFlag::DcRemoved) &&
           sdr_core::has_flag(output.front().quality_flags, sdr_core::QualityFlag::IqDropped),
           "native recipe attachment lost existing input quality flags");
}

void test_precision_modes() {
    constexpr std::uint32_t n = 1024U;
    constexpr double rate = 1'024'000.0;
    constexpr double center = 100'000'000.0;
    const auto samples = tone(n, rate, 321'000.0, 1.0);
    const sdr_core::PrecisionMode modes[] = {
        sdr_core::PrecisionMode::ReferenceF64,
        sdr_core::PrecisionMode::AccurateF32F64Accum,
        sdr_core::PrecisionMode::FastF32,
    };
    double peaks[3] = {0.0, 0.0, 0.0};
    for (std::size_t mode = 0; mode < 3U; ++mode) {
        auto backend = sdr_core::make_cpu_dsp_backend({});
        backend->configure(make_config(
            n,
            n,
            sdr_core::WindowType::Hann,
            sdr_core::DetectorType::Sample,
            sdr_core::SpectrumUnit::DbfsBin,
            modes[mode]
        ));
        backend->push_iq(make_cf32_block(samples, 0U, rate, center));
        const auto frames = backend->poll_spectrum(0U);
        expect(frames.size() == 1U, "expected one frame per mode");
        peaks[mode] = static_cast<double>((*frames.front().values)[n / 2U + 321U]);
    }
    std::cout << "precision peaks dB: ref=" << peaks[0] << " accurate=" << peaks[1]
              << " fast=" << peaks[2] << '\n';
    expect(std::fabs(peaks[1] - peaks[0]) < 1e-4, "ACCURATE mode deviation too large");
    expect(std::fabs(peaks[2] - peaks[0]) < 5e-3, "FAST mode deviation too large");
}

void test_engine_continuous_dsp() {
    sdr_core::SyntheticEngine engine;
    sdr_core::EngineConfig config;
    config.block_size_samples = 1024U;
    config.blocks_per_second = 400U;  // paced: the consumer drains deterministically
    config.max_blocks = 32U;
    config.spectrum_queue_capacity = 64U;
    config.dsp = sdr_core::DspConfig{
        .fft_size = 256U,
        .hop_size = 256U,
        .window = sdr_core::WindowType::Rectangular,
        .precision_mode = sdr_core::PrecisionMode::ReferenceF64,
    };
    engine.configure(config);
    engine.start();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(60);
    while (engine.state() == sdr_core::EngineState::Running &&
           std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    engine.join();
    expect(engine.state() == sdr_core::EngineState::Stopped, "engine must stop");
    const auto metrics = engine.metrics();
    expect(metrics.iq_blocks_received == 32U, "all blocks produced");
    // 32768 samples / 256 hop with 256 FFT -> 128 frames.
    expect(metrics.fft_frames_computed == 128U, "FFT frame count mismatch");
    expect(metrics.analytical_fft_rate > 0.0, "analytical rate must be positive");
    const auto frames = engine.poll_spectrum_frames(0U);
    expect(!frames.empty(), "spectrum frames must reach the queue");
    sdr_core::validate(frames.back());
    expect_close(
        frames.back().frequencies_hz->at(128U),
        config.center_frequency_hz,
        1e-6,
        "center bin frequency"
    );
    expect(
        sdr_core::has_flag(frames.back().quality_flags, sdr_core::QualityFlag::TimestampEstimated),
        "host timestamps must be flagged as estimated"
    );
    expect(
        frames.back().fft_bin_width_hz == config.sample_rate_hz / 256.0,
        "bin width mismatch"
    );
}

void test_gap_rebases_without_stale_stitching() {
    constexpr std::uint32_t n = 256U;
    constexpr std::uint32_t hop = 128U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, hop, sdr_core::WindowType::Rectangular));

    // Pre-gap stream: strong tone at bin 10.
    const auto pre = tone(n + 44U, rate, 10'000.0, 1.0);
    backend->push_iq(make_cf32_block(pre, 0U, rate, center));
    static_cast<void>(backend->poll_spectrum(0U));

    // Gap: the next block restarts at index 1000 with a quiet DC level.
    const std::vector<std::complex<double>> post_block(
        hop,
        std::complex<double>(0.25, 0.0)
    );
    backend->push_iq(make_cf32_block(post_block, 1000U, rate, center));
    // Only hop fresh samples so far: no frame may be staged yet (stale ring
    // data from before the gap must not leak across).
    expect(
        backend->poll_spectrum(0U).empty(),
        "frame staged across a gap from stale ring data"
    );
    const std::vector<std::complex<double>> post_rest(
        n - hop,
        std::complex<double>(0.25, 0.0)
    );
    backend->push_iq(make_cf32_block(post_rest, 1000U + hop, rate, center));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "expected exactly one post-gap frame");
    const auto& frame = frames.front();
    expect(
        frame.first_sample_index == 1000U,
        "post-gap frame index must start at the new block, not inside the gap"
    );
    // DC bin: (0.25)^2 = 0.0625 linear.
    expect_close(
        static_cast<double>((*frame.values)[n / 2U]),
        10.0 * std::log10(0.0625),
        1e-3,
        "post-gap frame content"
    );
    // The pre-gap tone must not leak into the post-gap frame.
    expect(
        static_cast<double>((*frame.values)[n / 2U + 10U]) < -40.0,
        "pre-gap data stitched into the post-gap frame"
    );
}

void test_overlap_non_divisor_hop() {
    constexpr std::uint32_t n = 256U;
    constexpr std::uint32_t hop = 100U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, hop, sdr_core::WindowType::Rectangular));

    backend->push_iq(make_cf32_block(tone(n, rate, 10'000.0, 0.5), 0U, rate, center));
    auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "first non-divisor-hop frame missing");
    expect(frames.front().first_sample_index == 0U, "first frame must start at sample zero");

    backend->push_iq(make_cf32_block(tone(hop, rate, 10'000.0, 0.5), n, rate, center));
    frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "second non-divisor-hop frame missing");
    expect(frames.front().first_sample_index == hop, "non-divisor hop index drift");
}

void test_retune_flushes_old_center_samples() {
    constexpr std::uint32_t n = 256U;
    constexpr std::uint32_t hop = 100U;
    constexpr double rate = 256'000.0;
    constexpr double old_center = 50'000'000.0;
    constexpr double new_center = 60'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, hop, sdr_core::WindowType::Rectangular));

    backend->push_iq(make_cf32_block(tone(n, rate, 10'000.0, 1.0), 0U, rate, old_center));
    expect(backend->poll_spectrum(0U).size() == 1U, "pre-retune frame missing");

    backend->push_iq(make_cf32_block(tone(hop, rate, 20'000.0, 0.5), n, rate, new_center));
    expect(backend->poll_spectrum(0U).empty(), "retune stitched old-center samples");
    backend->push_iq(
        make_cf32_block(tone(n - hop, rate, 20'000.0, 0.5), n + hop, rate, new_center)
    );
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "first post-retune frame missing");
    expect(frames.front().first_sample_index == n, "post-retune frame index mismatch");
    expect(frames.front().center_frequency_hz == new_center, "post-retune center mismatch");
}

void test_gap_accounts_partial_averaging() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(
        n,
        n,
        sdr_core::WindowType::Rectangular,
        sdr_core::DetectorType::AveragePower,
        sdr_core::SpectrumUnit::DbfsBin,
        sdr_core::PrecisionMode::ReferenceF64,
        3U
    ));
    const auto samples = tone(n, rate, 10'000.0, 0.5);
    backend->push_iq(make_cf32_block(samples, 0U, rate, center));
    expect(backend->poll_spectrum(0U).empty(), "partial average emitted too early");
    backend->push_iq(make_cf32_block(samples, n, rate, center));
    expect(backend->poll_spectrum(0U).empty(), "partial average emitted too early");
    expect(backend->metrics().fft_frames_dropped == 0U, "pre-gap drops unexpected");

    backend->push_iq(make_cf32_block(samples, 1000U, rate, center));
    expect(backend->poll_spectrum(0U).empty(), "post-gap partial average emitted too early");
    expect(
        backend->metrics().fft_frames_dropped == 2U,
        "discarded averaging contributions must be counted"
    );
    backend->push_iq(make_cf32_block(samples, 1000U + n, rate, center));
    expect(backend->poll_spectrum(0U).empty(), "post-gap average emitted after two frames");
    backend->push_iq(make_cf32_block(samples, 1000U + 2U * n, rate, center));
    expect(backend->poll_spectrum(0U).size() == 1U, "post-gap average missing");
}

void test_averaging_quality_flags_are_or_reduced() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(
        n,
        n,
        sdr_core::WindowType::Rectangular,
        sdr_core::DetectorType::AveragePower,
        sdr_core::SpectrumUnit::DbfsBin,
        sdr_core::PrecisionMode::ReferenceF64,
        2U
    ));
    std::vector<std::pair<std::int16_t, std::int16_t>> clipped(n, {32767, 100});
    auto first = make_ci16_block(clipped, 0U, rate, center);
    first.flags = sdr_core::QualityFlag::IqDropped;
    backend->push_iq(first);
    expect(backend->poll_spectrum(0U).empty(), "averaged frame emitted after one contribution");

    const std::vector<std::pair<std::int16_t, std::int16_t>> clean(n, {100, 100});
    backend->push_iq(make_ci16_block(clean, n, rate, center));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "averaged frame missing");
    expect(
        sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::AdcOverload),
        "ADC overload from an earlier contribution was lost"
    );
    expect(
        sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::IqDropped),
        "input quality flag from an earlier contribution was lost"
    );
}

void test_partial_batch_spans_polls_without_flush() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    auto config = make_config(n, n, sdr_core::WindowType::Rectangular);
    config.batch_size = 4U;
    backend->configure(config);
    const auto samples = tone(n, rate, 10'000.0, 0.5);
    for (std::uint32_t block = 0U; block < 3U; ++block) {
        backend->push_iq(make_cf32_block(samples, block * n, rate, center));
        expect(
            backend->poll_spectrum(0U, false).empty(),
            "non-flushing poll prematurely executed a partial batch"
        );
    }
    backend->push_iq(make_cf32_block(samples, 3U * n, rate, center));
    expect(
        backend->poll_spectrum(0U, false).size() == 4U,
        "configured batch did not span I/Q block boundaries"
    );
}

void test_engine_latest_wins_loss_annotation() {
    sdr_core::SyntheticEngine engine;
    sdr_core::EngineConfig config;
    config.block_size_samples = 256U;
    config.blocks_per_second = 400U;
    config.max_blocks = 32U;
    config.acquisition_queue_capacity = 64U;
    config.dsp_queue_capacity = 64U;
    config.spectrum_queue_capacity = 1U;
    config.pool_block_count = 128U;
    config.dsp = sdr_core::DspConfig{
        .fft_size = 256U,
        .hop_size = 256U,
        .window = sdr_core::WindowType::Rectangular,
        .precision_mode = sdr_core::PrecisionMode::ReferenceF64,
        .batch_size = 4U,
    };
    engine.configure(config);
    engine.start();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(60);
    while (engine.state() == sdr_core::EngineState::Running &&
           std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    engine.join();
    const auto metrics = engine.metrics();
    const auto frames = engine.poll_spectrum_frames(0U);
    expect(metrics.fft_frames_computed == 32U, "engine FFT count mismatch");
    expect(frames.size() == 1U, "latest-wins queue must retain exactly one frame");
    expect(metrics.fft_frames_dropped == 31U, "engine FFT loss count mismatch");
    expect(
        frames.front().dropped_fft_frames_before == metrics.fft_frames_dropped,
        "retained frame does not carry exact boundary loss count"
    );
    expect(
        sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::FftDropped),
        "retained frame missing FFT_DROPPED quality flag"
    );
}

void test_pluto_int12_full_scale_and_clipping() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, n, sdr_core::WindowType::Rectangular));
    const auto source = tone(n, rate, 10'000.0, 0.5);
    std::vector<std::pair<std::int16_t, std::int16_t>> quantized(n);
    for (std::uint32_t index = 0U; index < n; ++index) {
        quantized[index] = {
            static_cast<std::int16_t>(std::llround(source[index].real() * 2048.0)),
            static_cast<std::int16_t>(std::llround(source[index].imag() * 2048.0)),
        };
    }
    backend->push_iq(make_ci16_block(
        quantized, 0U, rate, center, sdr_core::SampleFormat::ComplexInt12InInt16Le
    ));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "int12 input did not produce one FFT frame");
    const auto peak = *std::max_element(frames.front().values->begin(), frames.front().values->end());
    expect_close(peak, -6.0206, 0.08, "int12 full-scale normalization is wrong");

    backend->reset();
    const std::vector<std::pair<std::int16_t, std::int16_t>> clipped(
        n, {std::int16_t{2047}, std::int16_t{0}}
    );
    backend->push_iq(make_ci16_block(
        clipped, 0U, rate, center, sdr_core::SampleFormat::ComplexInt12InInt16Le
    ));
    const auto clipped_frames = backend->poll_spectrum(0U);
    expect(
        sdr_core::has_flag(clipped_frames.front().quality_flags, sdr_core::QualityFlag::AdcOverload),
        "int12 clipping threshold was not detected"
    );
}

void test_hackrf_ci8_normalization_clipping_and_length() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, n, sdr_core::WindowType::Rectangular));

    const std::vector<std::pair<std::int8_t, std::int8_t>> half_scale(
        n, {std::int8_t{64}, std::int8_t{0}}
    );
    backend->push_iq(make_ci8_block(half_scale, 0U, rate, center));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "CI8 input did not produce one FFT frame");
    expect(peak_bin(frames.front()) == n / 2U, "CI8 DC bin placement is wrong");
    const auto peak = *std::max_element(frames.front().values->begin(), frames.front().values->end());
    expect_close(peak, -6.0206, 0.02, "CI8 signed/128 normalization is wrong");
    expect(
        !sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::AdcOverload),
        "clean CI8 samples were marked overloaded"
    );

    backend->reset();
    const std::vector<std::pair<std::int8_t, std::int8_t>> rails(
        n, {std::int8_t{-128}, std::int8_t{127}}
    );
    backend->push_iq(make_ci8_block(rails, 0U, rate, center));
    const auto rail_frames = backend->poll_spectrum(0U);
    expect(
        sdr_core::has_flag(rail_frames.front().quality_flags, sdr_core::QualityFlag::AdcOverload),
        "CI8 signed rails were not marked overloaded"
    );

    backend->reset();
    auto malformed = make_ci8_block(half_scale, 0U, rate, center);
    auto short_bytes = std::make_shared<std::vector<std::uint8_t>>(*malformed.samples);
    short_bytes->pop_back();
    malformed.samples = std::move(short_bytes);
    const auto samples_before_rejection = backend->metrics().samples_processed;
    bool rejected = false;
    try {
        backend->push_iq(malformed);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "malformed CI8 byte length was accepted");
    expect(
        backend->metrics().samples_processed == samples_before_rejection,
        "rejected CI8 changed sample metrics"
    );
}

void test_clipping_flag_with_nonzero_base() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    backend->configure(make_config(n, n, sdr_core::WindowType::Rectangular));
    const std::vector<std::pair<std::int16_t, std::int16_t>> clipped(n, {32767, 100});
    backend->push_iq(make_ci16_block(clipped, 5000U, rate, center));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "expected one frame");
    expect(
        frames.front().first_sample_index == 5000U,
        "frame index must honor the block base"
    );
    expect(
        sdr_core::has_flag(frames.front().quality_flags, sdr_core::QualityFlag::AdcOverload),
        "clipping flag must work for non-zero stream base"
    );
}

void test_analytical_ready_pre_eviction_and_averaging() {
    constexpr std::uint32_t n = 256U;
    sdr_core::CpuDspOptions options;
    options.output_capacity = 1U;
    options.analytical_event_capacity = 64U;
    auto backend = sdr_core::make_cpu_dsp_backend(options);
    auto config = make_config(n, n, sdr_core::WindowType::Rectangular);
    config.batch_size = 1U;
    backend->configure(config);
    auto block = make_cf32_block(tone(n * 3U, 256000.0, 1000.0, 0.5), 0U,
                                256000.0, 100000000.0);
    const auto before = sdr_core::analytical_ready_clock_ns();
    backend->push_iq(block);
    const auto after = sdr_core::analytical_ready_clock_ns();
    const auto s = backend->analytical_ready_summary();
    expect(s.offered == 3U && s.producer_superseded == 2U && s.outstanding == 1U,
           "ready journal must observe pre-eviction A/B/C");
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U && frames[0].frame_sequence == 2U,
           "output capacity was altered by telemetry");
    const auto ref = *frames[0].analytical_ready;
    expect(ref.offer_sequence == 3U && ref.ready_native_ns >= before &&
           ref.ready_native_ns <= after && ref.config_generation == block.config_generation,
           "origin was stamped downstream at poll or lost generation");
    expect(frames[0].timestamp_ns == block.timestamp_ns + 2000000,
           "RF/sample timestamp was changed by host-ready telemetry");
    auto events = backend->poll_analytical_ready_events(0U);
    expect(events.size() == 6U && events.front().kind == sdr_core::AnalyticalReadyEventKind::Offered &&
           events.front().ref.offer_sequence == 1U &&
           events.back().ref == ref, "evicted detector result receipt was not retained");
    backend->configure(make_config(n, n, sdr_core::WindowType::Rectangular,
        sdr_core::DetectorType::AveragePower, sdr_core::SpectrumUnit::DbfsBin,
        sdr_core::PrecisionMode::ReferenceF64, 4U));
    backend->push_iq(make_cf32_block(tone(n * 8U, 256000.0, 1000.0, 0.5), 0U,
                                   256000.0, 100000000.0));
    const auto averaged = backend->poll_spectrum(0U);
    expect(averaged.size() == 1U && averaged[0].averaging_frames == 4U &&
           averaged[0].frame_sequence == 1U && averaged[0].analytical_ready->offer_sequence == 5U &&
           averaged[0].analytical_ready->producer_instance_id == ref.producer_instance_id,
           "configure aliased offer sequence or averaged FFT count into offer count");
    expect(backend->metrics().fft_frames_computed == 8U &&
           backend->analytical_ready_summary().offered == 5U,
           "FFT and detector offers were conflated");
}

void test_analytical_ready_reset_cancel_and_default_support() {
    constexpr std::uint32_t n = 256U;
    auto backend = sdr_core::make_cpu_dsp_backend({});
    auto config = make_config(n, n, sdr_core::WindowType::Rectangular);
    config.batch_size = 1U;
    backend->configure(config);
    auto block = make_cf32_block(tone(n, 256000.0, 1000.0, 0.5), 0U,
                                256000.0, 100000000.0);
    backend->push_iq(block);
    const auto id = backend->analytical_ready_summary().producer_instance_id;
    backend->reset();
    auto s = backend->analytical_ready_summary();
    expect(s.offered == 1U && s.producer_cancelled == 1U && s.outstanding == 0U &&
           s.events_lost == 2U && s.event_storage_bytes == 0U,
           "reset hid cancellation or default ring allocated memory");
    backend->push_iq(block);
    const auto frames = backend->poll_spectrum(0U);
    expect(frames[0].analytical_ready->producer_instance_id == id &&
           frames[0].analytical_ready->offer_sequence == 2U,
           "reset silently reused producer offer identity");
    s = backend->analytical_ready_summary();
    expect(s.offered == s.handed_off + s.producer_superseded + s.producer_cancelled + s.outstanding,
           "CPU producer conservation failed after reset/rearm");
    expect(!sdr_core::SpectrumFrame{}.analytical_ready.has_value(),
           "historical/unsupported frame fabricated ready clock");
}

}  // namespace

int main() {
    try {
        test_exact_bin_tone_all_windows();
        test_parseval_psd_integration();
        test_detectors_linear_domain();
        test_detectors_all_precision_modes();
        test_overlap_continuity();
        test_repeated_blocks_deterministic();
        test_fft_timestamps_follow_sample_offsets();
        test_reset();
        test_non_finite_block_dropped();
        test_stage_timing_contract();
        test_ci16_unpack_and_clipping_flag();
        test_dc_removal_block_mean();
        test_dc_removal_independent_oracle_matrix();
        test_dc_removal_overlap_group_flush_and_reset();
        test_dc_removal_large_fft_selected_bins_and_parseval();
        test_dc_removal_paired_independent_means();
        test_dc_off_default_parity();
        test_processing_recipe_input_and_historical_unknown();
        test_precision_modes();
        test_engine_continuous_dsp();
        test_gap_rebases_without_stale_stitching();
        test_overlap_non_divisor_hop();
        test_retune_flushes_old_center_samples();
        test_gap_accounts_partial_averaging();
        test_averaging_quality_flags_are_or_reduced();
        test_partial_batch_spans_polls_without_flush();
        test_engine_latest_wins_loss_annotation();
        test_pluto_int12_full_scale_and_clipping();
        test_hackrf_ci8_normalization_clipping_and_length();
        test_clipping_flag_with_nonzero_base();
        test_analytical_ready_pre_eviction_and_averaging();
        test_analytical_ready_reset_cancel_and_default_support();
        std::cout << "P05 CPU DSP backend OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
