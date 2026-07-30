#include "sdr_core/dsp_backend.hpp"
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

}  // namespace

int main() {
    try {
        test_exact_bin_tone_all_windows();
        test_parseval_psd_integration();
        test_detectors_linear_domain();
        test_overlap_continuity();
        test_repeated_blocks_deterministic();
        test_fft_timestamps_follow_sample_offsets();
        test_reset();
        test_non_finite_block_dropped();
        test_ci16_unpack_and_clipping_flag();
        test_dc_removal_block_mean();
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
        test_clipping_flag_with_nonzero_base();
        std::cout << "P05 CPU DSP backend OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
