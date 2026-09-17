// R02 explicit synthetic CPU benchmark. The executable emits one JSON object
// to stdout and intentionally never opens a radio or stores I/Q data. It is
// built only with native tests so release applications carry no benchmark
// control path.

#include "sdr_core/dsp_backend.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <psapi.h>
#elif defined(__linux__)
#include <fstream>
#include <unistd.h>
#endif

namespace {

constexpr double two_pi = 6.28318530717958647692528676655900577;
constexpr std::size_t latency_reservoir_capacity = 8192U;

enum class InputFormat {
    ComplexFloat32Le,
    ComplexInt12InInt16Le,
};

enum class Backend {
    Cpu,
    Cuda,
};

enum class Precision {
    AccurateF32F64Accum,
    FastF32,
    ReferenceF64,
};

struct Arguments {
    std::uint32_t fft_size{4096U};
    std::uint32_t batch_size{1U};
    double sample_rate_hz{3'000'000.0};
    InputFormat input_format{InputFormat::ComplexFloat32Le};
    Backend backend{Backend::Cpu};
    Precision precision{Precision::AccurateF32F64Accum};
    double warmup_seconds{10.0};
    double measurement_seconds{60.0};
};

void require(const bool condition, const std::string_view message) {
    if (!condition) {
        throw std::runtime_error(std::string(message));
    }
}

[[nodiscard]] bool power_of_two(const std::uint32_t value) noexcept {
    return value >= 2U && (value & (value - 1U)) == 0U;
}

[[nodiscard]] std::string_view to_wire(const InputFormat value) noexcept {
    switch (value) {
    case InputFormat::ComplexFloat32Le: return "cf32_le";
    case InputFormat::ComplexInt12InInt16Le: return "ci12_in_i16_le";
    }
    return "unknown";
}

[[nodiscard]] sdr_core::SampleFormat to_sample_format(const InputFormat value) noexcept {
    switch (value) {
    case InputFormat::ComplexFloat32Le: return sdr_core::SampleFormat::ComplexFloat32Le;
    case InputFormat::ComplexInt12InInt16Le:
        return sdr_core::SampleFormat::ComplexInt12InInt16Le;
    }
    return sdr_core::SampleFormat::ComplexFloat32Le;
}

[[nodiscard]] std::string_view to_wire(const Backend value) noexcept {
    switch (value) {
    case Backend::Cpu: return "cpu";
    case Backend::Cuda: return "cuda";
    }
    return "unknown";
}

[[nodiscard]] sdr_core::ComputeBackendKind to_compute_backend(const Backend value) noexcept {
    switch (value) {
    case Backend::Cpu: return sdr_core::ComputeBackendKind::Cpu;
    case Backend::Cuda: return sdr_core::ComputeBackendKind::Cuda;
    }
    return sdr_core::ComputeBackendKind::Cpu;
}

[[nodiscard]] std::string_view to_wire(const Precision value) noexcept {
    switch (value) {
    case Precision::AccurateF32F64Accum: return "accurate_f32_f64_accum";
    case Precision::FastF32: return "fast_f32";
    case Precision::ReferenceF64: return "reference_f64";
    }
    return "unknown";
}

[[nodiscard]] sdr_core::PrecisionMode to_precision_mode(const Precision value) noexcept {
    switch (value) {
    case Precision::AccurateF32F64Accum:
        return sdr_core::PrecisionMode::AccurateF32F64Accum;
    case Precision::FastF32: return sdr_core::PrecisionMode::FastF32;
    case Precision::ReferenceF64: return sdr_core::PrecisionMode::ReferenceF64;
    }
    return sdr_core::PrecisionMode::AccurateF32F64Accum;
}

[[nodiscard]] Arguments parse_arguments(const int argc, char** argv) {
    Arguments result;
    for (int index = 1; index < argc; ++index) {
        const std::string_view argument(argv[index]);
        require(index + 1 < argc, "missing benchmark argument value");
        const std::string_view value(argv[++index]);
        if (argument == "--fft-size") {
            result.fft_size = static_cast<std::uint32_t>(std::stoul(std::string(value)));
        } else if (argument == "--batch-size") {
            result.batch_size = static_cast<std::uint32_t>(std::stoul(std::string(value)));
        } else if (argument == "--sample-rate-hz") {
            result.sample_rate_hz = std::stod(std::string(value));
        } else if (argument == "--input-format") {
            if (value == "cf32_le") {
                result.input_format = InputFormat::ComplexFloat32Le;
            } else if (value == "ci12_in_i16_le") {
                result.input_format = InputFormat::ComplexInt12InInt16Le;
            } else {
                throw std::runtime_error(
                    "--input-format must be cf32_le or ci12_in_i16_le"
                );
            }
        } else if (argument == "--backend") {
            if (value == "cpu") {
                result.backend = Backend::Cpu;
            } else if (value == "cuda") {
                result.backend = Backend::Cuda;
            } else {
                throw std::runtime_error("--backend must be cpu or cuda");
            }
        } else if (argument == "--precision") {
            if (value == "accurate_f32_f64_accum") {
                result.precision = Precision::AccurateF32F64Accum;
            } else if (value == "fast_f32") {
                result.precision = Precision::FastF32;
            } else if (value == "reference_f64") {
                result.precision = Precision::ReferenceF64;
            } else {
                throw std::runtime_error(
                    "--precision must be accurate_f32_f64_accum, fast_f32 or reference_f64"
                );
            }
        } else if (argument == "--warmup-seconds") {
            result.warmup_seconds = std::stod(std::string(value));
        } else if (argument == "--measurement-seconds") {
            result.measurement_seconds = std::stod(std::string(value));
        } else {
            throw std::runtime_error("unsupported argument: " + std::string(argument));
        }
    }
    require(power_of_two(result.fft_size), "--fft-size must be a power of two >= 2");
    require(result.batch_size > 0U && result.batch_size <= 1024U,
            "--batch-size must be in [1, 1024]");
    require(
        std::isfinite(result.sample_rate_hz) && result.sample_rate_hz > 0.0,
        "--sample-rate-hz must be finite and positive"
    );
    require(
        std::isfinite(result.warmup_seconds) && result.warmup_seconds >= 0.0,
        "--warmup-seconds must be finite and non-negative"
    );
    require(
        std::isfinite(result.measurement_seconds) && result.measurement_seconds > 0.0,
        "--measurement-seconds must be finite and positive"
    );
    return result;
}

[[nodiscard]] std::uint64_t working_set_bytes() noexcept {
#if defined(_WIN32)
    PROCESS_MEMORY_COUNTERS_EX counters{};
    counters.cb = sizeof(counters);
    if (GetProcessMemoryInfo(
        GetCurrentProcess(),
        reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&counters),
        sizeof(counters)
    ) == 0) {
        return 0U;
    }
    return static_cast<std::uint64_t>(counters.WorkingSetSize);
#elif defined(__linux__)
    std::ifstream statm("/proc/self/statm");
    std::uint64_t pages = 0U;
    std::uint64_t resident_pages = 0U;
    if (!(statm >> pages >> resident_pages)) {
        return 0U;
    }
    const long page_size = sysconf(_SC_PAGESIZE);
    return page_size > 0 ? resident_pages * static_cast<std::uint64_t>(page_size) : 0U;
#else
    return 0U;
#endif
}

class LatencyReservoir {
public:
    void add(const double value_ms) {
        ++seen_;
        if (values_.size() < latency_reservoir_capacity) {
            values_.push_back(value_ms);
            return;
        }
        // Deterministic reservoir sampling keeps memory bounded even at the
        // FFT1024 rate. It is adequate for percentile evidence, while exact
        // frame/block counts remain separate counters.
        state_ ^= state_ << 7U;
        state_ ^= state_ >> 9U;
        const auto replacement = state_ % seen_;
        if (replacement < values_.size()) {
            values_[static_cast<std::size_t>(replacement)] = value_ms;
        }
    }

    [[nodiscard]] std::size_t size() const noexcept { return values_.size(); }

    [[nodiscard]] double percentile(const double quantile) const {
        require(!values_.empty(), "benchmark did not produce latency samples");
        auto ordered = values_;
        std::sort(ordered.begin(), ordered.end());
        const auto rank = static_cast<std::size_t>(
            std::max(1.0, std::ceil(quantile * static_cast<double>(ordered.size())))
        );
        return ordered[std::min(rank - 1U, ordered.size() - 1U)];
    }

private:
    std::vector<double> values_{};
    std::uint64_t seen_{};
    std::uint64_t state_{0x9E3779B97F4A7C15ULL};
};

[[nodiscard]] std::shared_ptr<const std::vector<std::uint8_t>> make_signal(
    const std::uint32_t count,
    const double sample_rate_hz,
    const InputFormat input_format
) {
    const auto bytes_per_sample =
        input_format == InputFormat::ComplexFloat32Le ? 8U : 4U;
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(
        static_cast<std::size_t>(count) * bytes_per_sample
    );
    const double tone_hz = std::min(321'000.0, sample_rate_hz / 8.0);
    for (std::uint32_t index = 0U; index < count; ++index) {
        const double phase = two_pi * tone_hz * static_cast<double>(index) / sample_rate_hz;
        if (input_format == InputFormat::ComplexFloat32Le) {
            const float re = static_cast<float>(0.5 * std::cos(phase));
            const float im = static_cast<float>(0.5 * std::sin(phase));
            std::memcpy(bytes->data() + index * 8U, &re, sizeof(re));
            std::memcpy(bytes->data() + index * 8U + 4U, &im, sizeof(im));
            continue;
        }
        // AD936x CI12 arrives as signed 12-bit values stored in 16-bit LE
        // lanes.  Keep the synthetic amplitude away from the 12-bit endpoints
        // so the CPU benchmark measures normal unpack/DSP work, not clipping.
        const auto re = static_cast<std::int16_t>(std::lround(1024.0 * std::cos(phase)));
        const auto im = static_cast<std::int16_t>(std::lround(1024.0 * std::sin(phase)));
        const auto write_i16_le = [&](const std::size_t offset, const std::int16_t value) {
            const auto raw = static_cast<std::uint16_t>(value);
            (*bytes)[offset] = static_cast<std::uint8_t>(raw & 0xFFU);
            (*bytes)[offset + 1U] = static_cast<std::uint8_t>(raw >> 8U);
        };
        write_i16_le(static_cast<std::size_t>(index) * 4U, re);
        write_i16_le(static_cast<std::size_t>(index) * 4U + 2U, im);
    }
    return std::const_pointer_cast<const std::vector<std::uint8_t>>(bytes);
}

[[nodiscard]] sdr_core::IqBlock make_block(
    const std::shared_ptr<const std::vector<std::uint8_t>>& bytes,
    const std::uint32_t count,
    const std::uint64_t first_sample_index,
    const double sample_rate_hz,
    const InputFormat input_format
) {
    sdr_core::IqBlock block;
    block.first_sample_index = first_sample_index;
    block.timestamp_ns = 1;
    block.center_frequency_hz = 100'000'000.0;
    block.sample_rate_hz = sample_rate_hz;
    block.sample_format = to_sample_format(input_format);
    block.sample_count = count;
    block.samples = bytes;
    block.config_generation = 1U;
    return block;
}

struct PhaseResult {
    double elapsed_seconds{};
    std::uint64_t frames{};
    LatencyReservoir latencies{};
    std::uint64_t memory_peak_bytes{};
};

PhaseResult run_phase(
    sdr_core::DspBackend& backend,
    const std::shared_ptr<const std::vector<std::uint8_t>>& signal,
    const std::uint32_t block_samples,
    const double sample_rate_hz,
    const InputFormat input_format,
    const double target_seconds,
    const bool collect_latency
) {
    PhaseResult result;
    if (target_seconds == 0.0) {
        return result;
    }
    const auto started = std::chrono::steady_clock::now();
    std::uint64_t first_sample_index = 0U;
    while (true) {
        const auto block_started = std::chrono::steady_clock::now();
        backend.push_iq(make_block(
            signal,
            block_samples,
            first_sample_index,
            sample_rate_hz,
            input_format
        ));
        result.frames += backend.poll_spectrum(0U, false).size();
        const auto block_elapsed = std::chrono::steady_clock::now() - block_started;
        if (collect_latency) {
            result.latencies.add(std::chrono::duration<double, std::milli>(block_elapsed).count());
        }
        first_sample_index += block_samples;
        if ((first_sample_index / block_samples) % 32U == 0U) {
            result.memory_peak_bytes = std::max(result.memory_peak_bytes, working_set_bytes());
        }
        result.elapsed_seconds = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - started
        ).count();
        if (result.elapsed_seconds >= target_seconds) {
            return result;
        }
    }
}

void print_json(
    const Arguments& arguments,
    const PhaseResult& measurement,
    const std::uint64_t memory_baseline_bytes,
    const std::uint64_t memory_warmup_bytes,
    const std::uint64_t memory_final_bytes,
    const sdr_core::DspBackendMetrics& metrics
) {
    const auto latency_p50 = measurement.latencies.percentile(0.50);
    const auto latency_p95 = measurement.latencies.percentile(0.95);
    const auto latency_p99 = measurement.latencies.percentile(0.99);
    const auto peak_bytes = std::max({
        memory_baseline_bytes,
        memory_warmup_bytes,
        measurement.memory_peak_bytes,
        memory_final_bytes,
    });
    const auto stage_mask = metrics.stage_timing_mask;
    std::cout << std::setprecision(17)
              << "{\"schema\":\"sdr-native-performance-run-v1\""
              << ",\"backend\":\"" << to_wire(arguments.backend) << "\""
              << ",\"source_kind\":\"synthetic\""
              << ",\"config\":{\"fft_size\":" << arguments.fft_size
              << ",\"hop_size\":" << arguments.fft_size / 2U
              << ",\"batch_size\":" << arguments.batch_size
              << ",\"sample_rate_hz\":" << arguments.sample_rate_hz
              << ",\"input_format\":\"" << to_wire(arguments.input_format) << "\""
              << ",\"precision\":\"" << to_wire(arguments.precision) << "\""
              << ",\"window\":\"hann\",\"detector\":\"sample\"}"
              << ",\"elapsed_seconds\":" << measurement.elapsed_seconds
              << ",\"fft_frames_computed\":" << metrics.fft_frames_computed
              << ",\"spectrum_frames_emitted\":" << measurement.frames
              << ",\"analytical_fft_rate_hz\":"
              << static_cast<double>(metrics.fft_frames_computed) / measurement.elapsed_seconds
              << ",\"input_complex_sample_rate_capacity_hz\":"
              << static_cast<double>(metrics.samples_processed) / measurement.elapsed_seconds
              << ",\"realtime_dsp_fft_rate_required_hz\":"
              << arguments.sample_rate_hz / static_cast<double>(arguments.fft_size / 2U)
              << ",\"realtime_dsp_input_capacity_ratio\":"
              << (static_cast<double>(metrics.samples_processed) / measurement.elapsed_seconds) /
                     arguments.sample_rate_hz
              << ",\"latency_ms\":{\"kind\":\"cpu_backend_block_wall\",\"sample_count\":"
              << measurement.latencies.size() << ",\"p50_ms\":" << latency_p50
              << ",\"p95_ms\":" << latency_p95 << ",\"p99_ms\":" << latency_p99 << "}"
              << ",\"memory\":{\"kind\":\"process_working_set\",\"baseline_bytes\":"
              << memory_baseline_bytes << ",\"warmup_bytes\":" << memory_warmup_bytes
              << ",\"peak_bytes\":" << peak_bytes << ",\"final_bytes\":" << memory_final_bytes
              << "}"
              << ",\"losses\":{\"source_samples\":0,\"device_blocks\":0"
              << ",\"acquisition_queue_blocks\":0,\"dsp_frames\":" << metrics.fft_frames_dropped
              << ",\"spectrum_publications\":0,\"persistence_snapshots\":0}"
              << ",\"stage_totals_ns\":{\"availability_mask\":" << stage_mask
              << ",\"input_unpack_ns\":" << metrics.input_unpack_ns
              << ",\"window_ns\":" << metrics.window_ns
              << ",\"fft_ns\":" << metrics.fft_ns
              << ",\"detector_ns\":" << metrics.detector_ns << "}"
              << "}\n";
}

int run(const Arguments& arguments) {
    // Keep the original R02 input geometry: a fixed physical block of one
    // FFT.  This preserves the default CPU benchmark comparability.  A
    // configured batch is assembled across successive contiguous blocks;
    // there is no synthetic discontinuity at a block boundary.
    const auto block_samples = arguments.fft_size;
    sdr_core::DspConfig config;
    config.fft_size = arguments.fft_size;
    config.hop_size = arguments.fft_size / 2U;
    config.window = sdr_core::WindowType::Hann;
    config.detector = sdr_core::DetectorType::Sample;
    config.precision_mode = to_precision_mode(arguments.precision);
    config.batch_size = arguments.batch_size;
    config.averaging_frames = 1U;
    sdr_core::DspOptions options;
    // A full configured batch is emitted together. Retain that finite burst
    // plus two slots so this benchmark measures DSP capacity rather than
    // deliberately triggering the production latest-wins output bound.
    options.output_capacity = arguments.batch_size + 2U;
    sdr_core::DspBackendSelectionOptions selection;
    selection.preference = to_compute_backend(arguments.backend);
    // Capacity evidence must not fall through to CPU after an unavailable or
    // failed CUDA path: the requested backend is part of the JSON identity.
    selection.allow_runtime_fallback = false;
    auto backend = sdr_core::make_dsp_backend(selection, std::move(options));
    backend->configure(config);
    const auto signal = make_signal(
        block_samples,
        arguments.sample_rate_hz,
        arguments.input_format
    );
    const auto memory_baseline_bytes = working_set_bytes();
    static_cast<void>(run_phase(
        *backend,
        signal,
        block_samples,
        arguments.sample_rate_hz,
        arguments.input_format,
        arguments.warmup_seconds,
        false
    ));
    const auto memory_warmup_bytes = working_set_bytes();
    // The warm-up result is deliberately excluded from the evidence counters.
    backend->configure(config);
    const auto measurement = run_phase(
        *backend,
        signal,
        block_samples,
        arguments.sample_rate_hz,
        arguments.input_format,
        arguments.measurement_seconds,
        true
    );
    const auto metrics = backend->metrics();
    const auto memory_final_bytes = working_set_bytes();
    require(
        metrics.active_backend == to_compute_backend(arguments.backend),
        "benchmark backend identity differs from the requested backend"
    );
    require(metrics.fft_frames_computed > 0U, "benchmark produced no FFT frames");
    require(measurement.frames > 0U, "benchmark emitted no spectrum frames");
    require(
        metrics.fft_frames_dropped == 0U,
        "benchmark unexpectedly dropped FFT frames: " +
            std::to_string(metrics.fft_frames_dropped) +
            " (computed=" + std::to_string(metrics.fft_frames_computed) +
            ", emitted=" + std::to_string(measurement.frames) + ")"
    );
    print_json(
        arguments,
        measurement,
        memory_baseline_bytes,
        memory_warmup_bytes,
        memory_final_bytes,
        metrics
    );
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        return run(parse_arguments(argc, argv));
    } catch (const std::exception& error) {
        std::cerr << "sdr_core_performance_bench: " << error.what() << '\n';
        return 1;
    }
}
