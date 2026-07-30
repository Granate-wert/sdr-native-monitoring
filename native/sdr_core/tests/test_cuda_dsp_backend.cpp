#include "sdr_core/cuda_backend_link.hpp"
#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/window.hpp"
#include "sdr_cuda/cuda_dsp_backend.hpp"

#include <cmath>
#include <complex>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
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
    block.first_sample_index = first_sample_index;
    block.timestamp_ns = 1;
    block.center_frequency_hz = center_frequency;
    block.sample_rate_hz = sample_rate;
    block.sample_format = sdr_core::SampleFormat::ComplexFloat32Le;
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
    const std::uint32_t averaging = 1U,
    const std::uint32_t batch = 1U
) {
    sdr_core::DspConfig config;
    config.fft_size = fft_size;
    config.hop_size = hop_size;
    config.window = window;
    config.detector = detector;
    config.unit = unit;
    config.precision_mode = precision;
    config.averaging_frames = averaging;
    config.batch_size = batch;
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

std::unique_ptr<sdr_core::DspBackend> make_cuda() {
    return sdr_core::cuda_link::make_backend({}, -1, 4U);
}

void test_availability_and_self_test() {
    const auto availability = sdr_core::cuda_link::availability(-1);
    expect(availability.compiled, "CUDA backend must be compiled in this preset");
    expect(availability.runtime_present, "CUDA runtime must be present");
    expect(availability.device_supported, "CUDA device must be supported");
    const auto self_test = sdr_core::cuda_link::self_test(-1);
    expect(
        self_test.self_test_passed,
        "CUDA self-test failed: " + self_test.details
    );
}

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
        auto backend = make_cuda();
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
        expect_close(
            static_cast<double>((*frames.front().values)[n / 2U + bin]),
            0.0,
            1e-3,
            std::string("exact-bin peak must be 0 dBFS/bin for window ") +
                std::string(sdr_core::to_wire(window))
        );
    }
}

void test_cpu_cuda_parity_on_tone() {
    constexpr std::uint32_t n = 1024U;
    constexpr double rate = 1'024'000.0;
    constexpr double center = 100'000'000.0;
    const auto samples = tone(n, rate, 321'000.0, 0.75);
    auto cpu = sdr_core::make_cpu_dsp_backend({});
    auto cuda = make_cuda();
    const auto config = make_config(n, n, sdr_core::WindowType::Hann);
    cpu->configure(config);
    cuda->configure(config);
    cpu->push_iq(make_cf32_block(samples, 0U, rate, center));
    cuda->push_iq(make_cf32_block(samples, 0U, rate, center));
    const auto cpu_frames = cpu->poll_spectrum(0U);
    const auto cuda_frames = cuda->poll_spectrum(0U);
    expect(cpu_frames.size() == 1U && cuda_frames.size() == 1U, "frame count mismatch");
    const auto& cpu_frame = cpu_frames.front();
    const auto& cuda_frame = cuda_frames.front();
    expect(peak_bin(cpu_frame) == peak_bin(cuda_frame), "peak bin parity");
    expect(
        cpu_frame.frequencies_hz->size() == cuda_frame.frequencies_hz->size(),
        "axis size parity"
    );
    expect(
        (*cpu_frame.frequencies_hz)[512] == (*cuda_frame.frequencies_hz)[512],
        "axis value parity"
    );
    expect(cpu_frame.frame_sequence == cuda_frame.frame_sequence, "sequence parity");
    expect(cpu_frame.first_sample_index == cuda_frame.first_sample_index, "index parity");
    expect(cpu_frame.timestamp_ns == cuda_frame.timestamp_ns, "timestamp parity");
    expect(cpu_frame.quality_flags == cuda_frame.quality_flags, "quality parity");
    double max_diff = 0.0;
    for (std::size_t k = 0; k < n; ++k) {
        const double cpu_value = (*cpu_frame.values)[k];
        const double cuda_value = (*cuda_frame.values)[k];
        if (std::isinf(cpu_value) || std::isinf(cuda_value)) {
            continue;
        }
        max_diff = std::max(max_diff, std::fabs(cpu_value - cuda_value));
    }
    std::cout << "cpu/cuda max bin diff dB (f64 path): " << max_diff << '\n';
    expect(max_diff < 1e-3, "CPU/CUDA PSD parity exceeded 1e-3 dB");
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
        auto backend = make_cuda();
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
            expected_power * 1e-3 + 1e-12,
            std::string("detector mismatch for ") + std::string(sdr_core::to_wire(detector))
        );
    }
}

void test_overlap_gap_and_indices() {
    constexpr std::uint32_t n = 256U;
    constexpr std::uint32_t hop = 128U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = make_cuda();
    backend->configure(make_config(n, hop, sdr_core::WindowType::Rectangular));
    const auto samples = tone(2U * n, rate, 10'000.0, 1.0);
    backend->push_iq(make_cf32_block(samples, 0U, rate, center));
    auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 3U, "overlap must emit 3 frames");
    expect(frames[0].first_sample_index == 0U, "frame 0 index");
    expect(frames[1].first_sample_index == hop, "frame 1 index");
    expect(frames[2].first_sample_index == 2U * hop, "frame 2 index");

    // Gap: rebase must produce frames from fresh samples only.
    backend->push_iq(make_cf32_block(tone(hop, rate, 40'000.0, 0.5), 5000U, rate, center));
    expect(backend->poll_spectrum(0U).empty(), "stale stitching across gap");
    backend->push_iq(make_cf32_block(tone(hop, rate, 40'000.0, 0.5), 5000U + hop, rate, center));
    frames = backend->poll_spectrum(0U);
    expect(frames.size() == 1U, "expected post-gap frame");
    expect(frames.front().first_sample_index == 5000U, "post-gap index must not enter the gap");
}

void test_partial_batch_flush() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = make_cuda();
    backend->configure(make_config(n, n, sdr_core::WindowType::Rectangular,
                                   sdr_core::DetectorType::Sample,
                                   sdr_core::SpectrumUnit::DbfsBin,
                                   sdr_core::PrecisionMode::ReferenceF64, 1U, 4U));
    backend->push_iq(make_cf32_block(tone(n, rate, 32'000.0, 0.75), 0U, rate, center));
    backend->push_iq(make_cf32_block(tone(n, rate, 32'000.0, 0.75), n, rate, center));
    // Two staged frames (< batch_size 4) must flush on poll.
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 2U, "partial batch must flush on poll");
}

void test_plan_cache_reuse_and_eviction() {
    constexpr std::uint32_t n = 1024U;
    constexpr double rate = 1'024'000.0;
    constexpr double center = 100'000'000.0;
    sdr_cuda::CudaDspBackend backend({}, -1, 2U);
    backend.configure(make_config(n, n, sdr_core::WindowType::Hann,
                                  sdr_core::DetectorType::Sample,
                                  sdr_core::SpectrumUnit::DbfsBin,
                                  sdr_core::PrecisionMode::ReferenceF64, 1U, 4U));
    for (std::uint32_t block = 0U; block < 2U; ++block) {
        backend.push_iq(make_cf32_block(tone(4U * n, rate, 321'000.0, 1.0), block * 4U * n, rate, center));
    }
    auto perf = backend.perf_snapshot();
    expect(perf.batches_processed == 2U, "two batches expected");
    expect(perf.plan_cache.misses == 1U, "same key must create the plan once");
    expect(perf.plan_cache.hits == 1U, "same key must hit the cache");

    // Second config with a different FFT size: capacity 2 forces eviction
    // when a third distinct key appears.
    backend.configure(make_config(512U, 512U, sdr_core::WindowType::Hann,
                                  sdr_core::DetectorType::Sample,
                                  sdr_core::SpectrumUnit::DbfsBin,
                                  sdr_core::PrecisionMode::ReferenceF64, 1U, 4U));
    backend.push_iq(make_cf32_block(tone(4U * 512U, rate, 321'000.0, 1.0), 0U, rate, center));
    backend.configure(make_config(256U, 256U, sdr_core::WindowType::Hann,
                                  sdr_core::DetectorType::Sample,
                                  sdr_core::SpectrumUnit::DbfsBin,
                                  sdr_core::PrecisionMode::ReferenceF64, 1U, 4U));
    backend.push_iq(make_cf32_block(tone(4U * 256U, rate, 321'000.0, 1.0), 0U, rate, center));
    perf = backend.perf_snapshot();
    expect(perf.plan_cache.capacity == 2U, "plan cache capacity");
    expect(perf.plan_cache.evictions >= 1U, "bounded cache must evict at capacity");
    expect(perf.plan_cache.size <= 2U, "plan cache must stay bounded");
}

void test_repeated_configure_resource_stability() {
    constexpr std::uint32_t n = 1024U;
    constexpr double rate = 1'024'000.0;
    constexpr double center = 100'000'000.0;
    sdr_cuda::CudaDspBackend backend({}, -1, 4U);
    std::uint64_t device_bytes = 0U;
    for (std::uint32_t iteration = 0U; iteration < 3U; ++iteration) {
        backend.configure(make_config(n, n, sdr_core::WindowType::Hann,
                                      sdr_core::DetectorType::Sample,
                                      sdr_core::SpectrumUnit::DbfsBin,
                                      sdr_core::PrecisionMode::ReferenceF64, 1U, 4U));
        backend.push_iq(make_cf32_block(tone(4U * n, rate, 321'000.0, 1.0), 0U, rate, center));
        const auto perf = backend.perf_snapshot();
        if (iteration == 0U) {
            device_bytes = perf.device_bytes;
        } else {
            expect(perf.device_bytes == device_bytes, "device memory must not grow on reconfigure");
        }
        const auto frames = backend.poll_spectrum(0U);
        expect(!frames.empty(), "configure must leave a working backend");
    }
}

void test_non_finite_and_reset() {
    constexpr std::uint32_t n = 256U;
    constexpr double rate = 256'000.0;
    constexpr double center = 50'000'000.0;
    auto backend = make_cuda();
    backend->configure(make_config(n, n, sdr_core::WindowType::Hann));
    auto samples = tone(n, rate, 32'000.0, 0.75);
    samples[42] = std::complex<double>(std::numeric_limits<double>::quiet_NaN(), 0.0);
    backend->push_iq(make_cf32_block(samples, 0U, rate, center));
    expect(backend->poll_spectrum(0U).empty(), "non-finite block must be dropped");
    expect(backend->metrics().fft_frames_dropped == 1U, "drop must be counted");
    backend->reset();
    backend->push_iq(make_cf32_block(tone(n, rate, 32'000.0, 0.75), 0U, rate, center));
    expect(backend->poll_spectrum(0U).size() == 1U, "reset must restore processing");
}

}  // namespace

int main() {
    try {
        test_availability_and_self_test();
        test_exact_bin_tone_all_windows();
        test_cpu_cuda_parity_on_tone();
        test_detectors_linear_domain();
        test_overlap_gap_and_indices();
        test_partial_batch_flush();
        test_plan_cache_reuse_and_eviction();
        test_repeated_configure_resource_stability();
        test_non_finite_and_reset();
        std::cout << "P08 CUDA DSP backend OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
