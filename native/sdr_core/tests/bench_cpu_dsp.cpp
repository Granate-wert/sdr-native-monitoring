#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/fft_provider.hpp"

#include <chrono>
#include <cmath>
#include <complex>
#include <cstring>
#include <iostream>
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

// Provider-level benchmark: FFT size x batch, double precision.
void bench_provider() {
    const std::uint32_t sizes[] = {1024U, 4096U, 16384U, 65536U};
    const std::size_t batches[] = {1U, 16U, 64U, 256U};
    std::cout << "fft_size,batch,total_ms,ffts_per_second,ns_per_fft\n";
    for (const auto size : sizes) {
        for (const auto batch : batches) {
            auto provider = sdr_core::make_pocketfft_provider();
            provider->configure(size);
            std::vector<std::complex<double>> input(batch * size);
            std::vector<std::complex<double>> output(batch * size);
            for (std::size_t k = 0; k < input.size(); ++k) {
                const double phase = two_pi * 0.01 * static_cast<double>(k % size);
                input[k] = {std::cos(phase), std::sin(phase)};
            }
            // Warmup.
            provider->execute_batch(input.data(), output.data(), batch);
            const auto started = std::chrono::steady_clock::now();
            constexpr std::uint32_t repetitions = 3U;
            for (std::uint32_t rep = 0U; rep < repetitions; ++rep) {
                provider->execute_batch(input.data(), output.data(), batch);
            }
            const auto elapsed = std::chrono::steady_clock::now() - started;
            const double seconds = std::chrono::duration<double>(elapsed).count() / repetitions;
            const double fft_count = static_cast<double>(batch);
            const double per_second = fft_count / seconds;
            std::cout << size << ',' << batch << ',' << seconds * 1e3 << ',' << per_second
                      << ',' << seconds * 1e9 / fft_count << '\n';
            expect(std::isfinite(output[123 % output.size()].real()), "FFT output not finite");
            expect(per_second > 0.0, "rate must be positive");
        }
    }
}

// Backend-level throughput: continuous blocks through the full pipeline.
void bench_backend_pipeline() {
    constexpr std::uint32_t fft_size = 4096U;
    constexpr std::uint32_t hop = 2048U;
    constexpr std::uint32_t block_samples = 8192U;
    constexpr std::uint32_t blocks = 512U;
    constexpr double rate = 8'192'000.0;

    sdr_core::DspConfig config;
    config.fft_size = fft_size;
    config.hop_size = hop;
    config.window = sdr_core::WindowType::Hann;
    config.precision_mode = sdr_core::PrecisionMode::AccurateF32F64Accum;
    config.batch_size = 16U;
    sdr_core::CpuDspOptions options;
    options.output_capacity = 64U;  // bursts of batch_size=16 frames
    auto backend = sdr_core::make_cpu_dsp_backend(options);
    backend->configure(config);

    std::vector<std::complex<double>> samples(block_samples);
    for (std::uint32_t k = 0U; k < block_samples; ++k) {
        const double phase = two_pi * 1'000'000.0 * static_cast<double>(k) / rate;
        samples[k] = {0.5 * std::cos(phase), 0.5 * std::sin(phase)};
    }
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(samples.size() * 8U);
    for (std::size_t index = 0; index < samples.size(); ++index) {
        const float re = static_cast<float>(samples[index].real());
        const float im = static_cast<float>(samples[index].imag());
        std::memcpy(bytes->data() + index * 8U, &re, sizeof(re));
        std::memcpy(bytes->data() + index * 8U + 4U, &im, sizeof(im));
    }

    const auto started = std::chrono::steady_clock::now();
    std::uint64_t emitted = 0U;
    for (std::uint32_t block_index = 0U; block_index < blocks; ++block_index) {
        sdr_core::IqBlock block;
        block.first_sample_index = block_index * block_samples;
        block.timestamp_ns = 1;
        block.center_frequency_hz = 100'000'000.0;
        block.sample_rate_hz = rate;
        block.sample_format = sdr_core::SampleFormat::ComplexFloat32Le;
        block.sample_count = block_samples;
        block.samples = bytes;
        block.config_generation = 1U;
        backend->push_iq(block);
        emitted += backend->poll_spectrum(0U).size();
    }
    const auto elapsed = std::chrono::steady_clock::now() - started;
    const double seconds = std::chrono::duration<double>(elapsed).count();
    const auto total_samples = static_cast<double>(blocks) * block_samples;
    const auto metrics = backend->metrics();
    std::cout << "pipeline: samples=" << static_cast<std::uint64_t>(total_samples)
              << " fft_frames=" << metrics.fft_frames_computed
              << " emitted=" << emitted
              << " seconds=" << seconds
              << " MSPS=" << total_samples / seconds / 1e6
              << " fft_per_second=" << metrics.fft_frames_computed / seconds << '\n';
    expect(metrics.fft_frames_computed > 0U, "pipeline produced no frames");
    expect(emitted > 0U, "pipeline emitted no frames");
    expect(metrics.fft_frames_dropped == 0U, "unexpected drops with fast consumer");
}

}  // namespace

int main() {
    try {
        bench_provider();
        bench_backend_pipeline();
        std::cout << "P05 DSP benchmark OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
