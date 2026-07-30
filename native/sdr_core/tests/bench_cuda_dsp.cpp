#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_cuda/cuda_dsp_backend.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

constexpr double two_pi = 6.28318530717958647692528676655900577;
constexpr std::uint32_t benchmark_runs = 3U;
constexpr std::uint32_t samples_per_run = 10U;

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

std::size_t sample_width(const sdr_core::SampleFormat format) {
    return format == sdr_core::SampleFormat::ComplexInt12InInt16Le ? 4U : 8U;
}

std::shared_ptr<const std::vector<std::uint8_t>> make_signal(
    const std::uint32_t sample_count,
    const sdr_core::SampleFormat format,
    const double rate
) {
    const auto width = sample_width(format);
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(
        static_cast<std::size_t>(sample_count) * width
    );
    for (std::uint32_t k = 0U; k < sample_count; ++k) {
        const double phase = two_pi * 321'000.0 * static_cast<double>(k) / rate;
        if (format == sdr_core::SampleFormat::ComplexInt12InInt16Le) {
            const auto re = static_cast<std::int16_t>(std::llround(1536.0 * std::cos(phase)));
            const auto im = static_cast<std::int16_t>(std::llround(1536.0 * std::sin(phase)));
            std::memcpy(bytes->data() + k * width, &re, sizeof(re));
            std::memcpy(bytes->data() + k * width + 2U, &im, sizeof(im));
        } else {
            const float re = static_cast<float>(0.5 * std::cos(phase));
            const float im = static_cast<float>(0.5 * std::sin(phase));
            std::memcpy(bytes->data() + k * width, &re, sizeof(re));
            std::memcpy(bytes->data() + k * width + 4U, &im, sizeof(im));
        }
    }
    return std::const_pointer_cast<const std::vector<std::uint8_t>>(bytes);
}

sdr_core::IqBlock make_block(
    const std::shared_ptr<const std::vector<std::uint8_t>>& bytes,
    const std::uint32_t sample_count,
    const std::uint64_t first_sample_index,
    const double rate,
    const sdr_core::SampleFormat format
) {
    sdr_core::IqBlock block;
    block.first_sample_index = first_sample_index;
    block.timestamp_ns = 1;
    block.center_frequency_hz = 2'450'000'000.0;
    block.sample_rate_hz = rate;
    block.sample_format = format;
    block.sample_count = sample_count;
    block.samples = bytes;
    block.config_generation = 1U;
    return block;
}

double percentile(std::vector<double> values, const double quantile) {
    expect(!values.empty(), "benchmark has no timing samples");
    std::sort(values.begin(), values.end());
    const auto index = static_cast<std::size_t>(std::ceil(quantile * static_cast<double>(values.size() - 1U)));
    return values[std::min(index, values.size() - 1U)];
}

std::unique_ptr<sdr_core::DspBackend> make_backend(const sdr_core::ComputeBackendKind kind) {
    sdr_core::DspOptions options;
    options.output_capacity = 64U;
    if (kind == sdr_core::ComputeBackendKind::Cpu) {
        return sdr_core::make_cpu_dsp_backend(std::move(options));
    }
    return std::make_unique<sdr_cuda::CudaDspBackend>(std::move(options), -1, 8U);
}

// P08-010: every timed sample pushes one block containing the complete
// configured batch. Three runs of ten samples provide median/p95 instead of
// timing a single FFT or timing an under-filled batch.
void bench_matrix() {
    const std::uint32_t sizes[] = {1024U, 4096U, 16384U, 65536U};
    const std::uint32_t batches[] = {1U, 4U, 8U, 16U, 32U};
    const sdr_core::PrecisionMode modes[] = {
        sdr_core::PrecisionMode::AccurateF32F64Accum,
        sdr_core::PrecisionMode::ReferenceF64,
    };
    const sdr_core::SampleFormat formats[] = {
        sdr_core::SampleFormat::ComplexFloat32Le,
        sdr_core::SampleFormat::ComplexInt12InInt16Le,
    };
    const sdr_core::ComputeBackendKind backends[] = {
        sdr_core::ComputeBackendKind::Cpu,
        sdr_core::ComputeBackendKind::Cuda,
    };
    constexpr double rate = 8'192'000.0;
    std::cout << "backend,format,fft_size,batch,precision,runs,samples,median_ms,p95_ms,frames,gpu_ms,h2d_ms,d2h_ms\n";

    for (const auto backend_kind : backends) {
        for (const auto format : formats) {
            for (const auto mode : modes) {
                for (const auto fft_size : sizes) {
                    for (const auto batch : batches) {
                        sdr_core::DspConfig config;
                        config.fft_size = fft_size;
                        config.hop_size = fft_size;
                        config.window = sdr_core::WindowType::Hann;
                        config.precision_mode = mode;
                        config.batch_size = batch;
                        config.averaging_frames = 1U;

                        auto backend = make_backend(backend_kind);
                        backend->configure(config);
                        const auto sample_count = fft_size * batch;
                        const auto signal = make_signal(sample_count, format, rate);
                        backend->push_iq(make_block(signal, sample_count, 0U, rate, format));
                        static_cast<void>(backend->poll_spectrum(0U));
                        backend->reset();

                        std::vector<double> timings_ms;
                        timings_ms.reserve(benchmark_runs * samples_per_run);
                        std::uint64_t frames = 0U;
                        for (std::uint32_t run = 0U; run < benchmark_runs; ++run) {
                            backend->reset();
                            for (std::uint32_t sample = 0U; sample < samples_per_run; ++sample) {
                                const auto started = std::chrono::steady_clock::now();
                                backend->push_iq(make_block(
                                    signal,
                                    sample_count,
                                    static_cast<std::uint64_t>(sample) * sample_count,
                                    rate,
                                    format
                                ));
                                const auto output = backend->poll_spectrum(0U, false);
                                const auto elapsed = std::chrono::steady_clock::now() - started;
                                timings_ms.push_back(
                                    std::chrono::duration<double, std::milli>(elapsed).count()
                                );
                                frames += output.size();
                            }
                        }

                        const auto metrics = backend->metrics();
                        const auto expected_frames = static_cast<std::uint64_t>(benchmark_runs) *
                            samples_per_run * batch;
                        expect(frames == expected_frames, "benchmark did not emit a full configured batch");
                        expect(metrics.fft_frames_dropped == 0U, "benchmark dropped FFT frames");

                        double gpu_ms = 0.0;
                        double h2d_ms = 0.0;
                        double d2h_ms = 0.0;
                        if (const auto* cuda = dynamic_cast<const sdr_cuda::CudaDspBackend*>(backend.get())) {
                            const auto perf = cuda->perf_snapshot();
                            gpu_ms = static_cast<double>(perf.preprocess_ns + perf.fft_ns + perf.detector_ns) / 1.0e6;
                            h2d_ms = static_cast<double>(perf.h2d_ns) / 1.0e6;
                            d2h_ms = static_cast<double>(perf.d2h_ns) / 1.0e6;
                        }
                        std::cout << (backend_kind == sdr_core::ComputeBackendKind::Cpu ? "cpu" : "cuda") << ','
                                  << (format == sdr_core::SampleFormat::ComplexFloat32Le ? "f32" : "int12") << ','
                                  << fft_size << ',' << batch << ','
                                  << (mode == sdr_core::PrecisionMode::ReferenceF64 ? "f64" : "accurate") << ','
                                  << benchmark_runs << ',' << timings_ms.size() << ','
                                  << percentile(timings_ms, 0.50) << ','
                                  << percentile(timings_ms, 0.95) << ',' << frames << ','
                                  << gpu_ms << ',' << h2d_ms << ',' << d2h_ms << '\n';
                    }
                }
            }
        }
    }
}

// Memory plateau: repeated runs must not grow device/pinned bytes.
void bench_memory_plateau() {
    sdr_core::DspConfig config;
    config.fft_size = 16384U;
    config.hop_size = 8192U;
    config.window = sdr_core::WindowType::Hann;
    config.precision_mode = sdr_core::PrecisionMode::AccurateF32F64Accum;
    config.batch_size = 8U;
    sdr_cuda::CudaDspBackend backend({}, -1, 8U);
    backend.configure(config);
    const auto initial = backend.perf_snapshot();
    const auto bytes = make_signal(config.fft_size * config.batch_size, sdr_core::SampleFormat::ComplexFloat32Le, 8'192'000.0);
    for (std::uint32_t rep = 0U; rep < 20U; ++rep) {
        backend.push_iq(make_block(
            bytes,
            config.fft_size * config.batch_size,
            static_cast<std::uint64_t>(rep) * config.fft_size,
            8'192'000.0,
            sdr_core::SampleFormat::ComplexFloat32Le
        ));
        static_cast<void>(backend.poll_spectrum(0U));
    }
    const auto steady = backend.perf_snapshot();
    expect(steady.device_bytes == initial.device_bytes, "device bytes grew");
    expect(steady.pinned_host_bytes == initial.pinned_host_bytes, "pinned bytes grew");
    expect(steady.plan_cache.evictions == 0U, "plan evictions on repeated key");
    std::cout << "memory plateau: device=" << steady.device_bytes
              << " pinned=" << steady.pinned_host_bytes
              << " plan_hits=" << steady.plan_cache.hits << '\n';
}

}  // namespace

int main() {
    try {
        bench_matrix();
        bench_memory_plateau();
        std::cout << "P08 CUDA benchmark OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}