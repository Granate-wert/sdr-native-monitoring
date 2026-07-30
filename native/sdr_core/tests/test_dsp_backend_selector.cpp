#include "sdr_core/cuda_backend_link.hpp"
#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"

#include <cmath>
#include <complex>
#include <cstdlib>
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

void arm_fail_on_batch(const unsigned long long batch) {
    const std::string value = "SDR_CUDA_FAIL_ON_BATCH=" + std::to_string(batch);
    if (_putenv(value.c_str()) != 0) {
        throw std::runtime_error("failed to arm the failure hook");
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

sdr_core::IqBlock make_block(
    const std::vector<std::complex<double>>& samples,
    const std::uint64_t first_sample_index
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
    block.center_frequency_hz = 100'000'000.0;
    block.sample_rate_hz = 1'024'000.0;
    block.sample_format = sdr_core::SampleFormat::ComplexFloat32Le;
    block.sample_count = static_cast<std::uint32_t>(samples.size());
    block.samples = std::move(bytes);
    block.config_generation = 7U;
    return block;
}

sdr_core::DspConfig make_config(
    const std::uint32_t fft_size,
    const std::uint32_t batch
) {
    sdr_core::DspConfig config;
    config.fft_size = fft_size;
    config.hop_size = fft_size;
    config.window = sdr_core::WindowType::Rectangular;
    config.precision_mode = sdr_core::PrecisionMode::ReferenceF64;
    config.batch_size = batch;
    return config;
}

sdr_core::DspBackendSelectionOptions selection(
    const sdr_core::ComputeBackendKind preference,
    const bool fallback = true,
    const int device_id = -1
) {
    sdr_core::DspBackendSelectionOptions result;
    result.preference = preference;
    result.allow_runtime_fallback = fallback;
    result.device_id = device_id;
    return result;
}

void test_cpu_preference_never_touches_cuda() {
    auto backend = sdr_core::make_dsp_backend(selection(sdr_core::ComputeBackendKind::Cpu), {});
    expect(backend->info().kind == sdr_core::ComputeBackendKind::Cpu, "Cpu preference must give CPU");
    const auto metrics = backend->metrics();
    expect(metrics.requested_preference == sdr_core::ComputeBackendKind::Cpu, "requested metric");
    expect(metrics.backend_fallback_count == 0U, "no fallback on CPU");
}

void test_hip_is_reported_not_implemented() {
    bool rejected = false;
    try {
        static_cast<void>(sdr_core::make_dsp_backend(selection(sdr_core::ComputeBackendKind::Hip), {}));
    } catch (const sdr_core::BackendUnavailableError& error) {
        rejected = std::string(error.what()).find("P08H") != std::string::npos;
    }
    expect(rejected, "HIP preference must fail as not-implemented, not silently degrade");
    const auto availability = sdr_core::backend_availability(sdr_core::ComputeBackendKind::Hip);
    expect(!availability.compiled, "HIP must report compiled=false");
}

void test_forced_cuda_works_on_this_machine() {
    auto backend = sdr_core::make_dsp_backend(selection(sdr_core::ComputeBackendKind::Cuda), {});
    backend->configure(make_config(1024U, 4U));
    backend->push_iq(make_block(tone(4U * 1024U, 1'024'000.0, 321'000.0, 1.0), 0U));
    const auto frames = backend->poll_spectrum(0U);
    expect(frames.size() == 4U, "forced CUDA must produce frames");
    expect(backend->info().kind == sdr_core::ComputeBackendKind::Cuda, "active must be CUDA");
}

void test_forced_cuda_unavailable_fails_typed() {
    bool rejected = false;
    try {
        static_cast<void>(
            sdr_core::make_dsp_backend(selection(sdr_core::ComputeBackendKind::Cuda, true, 999), {})
        );
    } catch (const sdr_core::BackendUnavailableError&) {
        rejected = true;
    }
    expect(rejected, "forced CUDA on an invalid device must fail with BackendUnavailableError");
}

void test_auto_stays_cpu_without_committed_crossover() {
    auto backend = sdr_core::make_dsp_backend(selection(sdr_core::ComputeBackendKind::Auto), {});
    backend->configure(make_config(4096U, 16U));  // 65536 >= crossover
    expect(
        backend->metrics().active_backend == sdr_core::ComputeBackendKind::Cpu,
        "AUTO must stay CPU without a committed crossover"
    );
}

void test_auto_uses_cpu_below_crossover() {
    auto backend = sdr_core::make_dsp_backend(selection(sdr_core::ComputeBackendKind::Auto), {});
    backend->configure(make_config(256U, 1U));  // below crossover
    expect(
        backend->metrics().active_backend == sdr_core::ComputeBackendKind::Cpu,
        "AUTO must keep small workloads on CPU"
    );
    backend->push_iq(make_block(tone(256U, 1'024'000.0, 32'000.0, 1.0), 0U));
    expect(backend->poll_spectrum(0U).size() == 1U, "CPU path must keep working");
}

void test_fallback_once_and_flagged() {
    auto backend = sdr_core::make_dsp_backend(selection(sdr_core::ComputeBackendKind::Cuda, true), {});
    arm_fail_on_batch(2U);
    backend->configure(make_config(256U, 2U));
    // Batch 1 commits on CUDA; batch 2 is injected to fail.
    backend->push_iq(make_block(tone(2U * 256U, 1'024'000.0, 32'000.0, 1.0), 0U));
    const auto before = backend->poll_spectrum(0U);
    expect(before.size() == 2U, "first batch must commit on CUDA");
    expect(
        !sdr_core::has_flag(before.front().quality_flags, sdr_core::QualityFlag::BackendFallback),
        "no fallback flag before the failure"
    );
    backend->push_iq(make_block(tone(2U * 256U, 1'024'000.0, 32'000.0, 1.0), 512U));
    const auto after = backend->poll_spectrum(0U);
    const auto metrics = backend->metrics();
    expect(metrics.backend_fallback_count == 1U, "exactly one fallback");
    expect(metrics.backend_switch_count == 1U, "exactly one switch");
    expect(
        metrics.last_backend_error == sdr_core::BackendErrorCode::FftExecutionFailed,
        "typed failure code"
    );
    expect(metrics.active_backend == sdr_core::ComputeBackendKind::Cpu, "CPU after fallback");
    expect(!after.empty(), "replayed block must produce CPU frames");
    expect(
        sdr_core::has_flag(after.front().quality_flags, sdr_core::QualityFlag::BackendFallback),
        "first CPU frame must carry CUDA_FALLBACK"
    );
    expect(after.front().config_generation == 7U, "generation must be preserved");
    // Steady state: further frames are plain CPU frames.
    backend->push_iq(make_block(tone(2U * 256U, 1'024'000.0, 32'000.0, 1.0), 1024U));
    const auto steady = backend->poll_spectrum(0U);
    expect(!steady.empty(), "CPU steady state must produce frames");
    expect(
        !sdr_core::has_flag(steady.front().quality_flags, sdr_core::QualityFlag::BackendFallback),
        "fallback flag is a one-time marker"
    );
}

void test_fallback_disabled_enters_error() {
    auto backend = sdr_core::make_dsp_backend(selection(sdr_core::ComputeBackendKind::Cuda, false), {});
    arm_fail_on_batch(2U);
    backend->configure(make_config(256U, 2U));
    backend->push_iq(make_block(tone(2U * 256U, 1'024'000.0, 32'000.0, 1.0), 0U));
    static_cast<void>(backend->poll_spectrum(0U));
    bool failed = false;
    try {
        backend->push_iq(make_block(tone(2U * 256U, 1'024'000.0, 32'000.0, 1.0), 512U));
    } catch (const sdr_core::DeviceError& error) {
        failed = error.code() == sdr_core::BackendErrorCode::FftExecutionFailed;
    }
    expect(failed, "fallback disabled must surface the typed error");
}

void test_reconfigure_retries_cuda() {
    auto backend = sdr_core::make_dsp_backend(selection(sdr_core::ComputeBackendKind::Cuda, true), {});
    arm_fail_on_batch(2U);
    backend->configure(make_config(256U, 2U));
    backend->push_iq(make_block(tone(2U * 256U, 1'024'000.0, 32'000.0, 1.0), 0U));
    static_cast<void>(backend->poll_spectrum(0U));
    backend->push_iq(make_block(tone(2U * 256U, 1'024'000.0, 32'000.0, 1.0), 512U));
    expect(
        backend->metrics().active_backend == sdr_core::ComputeBackendKind::Cpu,
        "must be on CPU after the injected failure"
    );
    // The injection hook re-arms per configure but triggers only at batch 2;
    // the retry pushes a single batch, so the recreated primary is used.
    backend->configure(make_config(512U, 2U));
    expect(
        backend->metrics().active_backend == sdr_core::ComputeBackendKind::Cuda,
        "reconfigure must retry CUDA"
    );
    backend->push_iq(make_block(tone(2U * 512U, 1'024'000.0, 32'000.0, 1.0), 0U));
    expect(backend->poll_spectrum(0U).size() == 2U, "retried CUDA must produce frames");
}

}  // namespace

int main() {
    struct Step {
        const char* name;
        void (*fn)();
    };
    const Step steps[] = {
        {"cpu_preference", test_cpu_preference_never_touches_cuda},
        {"hip_rejected", test_hip_is_reported_not_implemented},
        {"forced_cuda", test_forced_cuda_works_on_this_machine},
        {"forced_cuda_unavailable", test_forced_cuda_unavailable_fails_typed},
        {"auto_without_table", test_auto_stays_cpu_without_committed_crossover},
        {"auto_below", test_auto_uses_cpu_below_crossover},
        {"fallback_once", test_fallback_once_and_flagged},
        {"fallback_disabled", test_fallback_disabled_enters_error},
        {"reconfigure_retry", test_reconfigure_retries_cuda},
    };
    try {
        for (const auto& step : steps) {
            std::cerr << "[step] " << step.name << '\n';
            step.fn();
        }
        std::cout << "P08 DSP backend selector OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
