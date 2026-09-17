// Explicit R10-D5 v2 CPU microbenchmark for final 36 MHz Sweep-line
// assembly. It deliberately starts after FFT: synthetic reduced spectrum
// frames are constructed once, no receiver/IIO context is opened, and no I/Q,
// Python or Qt object is touched. Its result is therefore an upper-bound
// host-cost witness for the assembler, never Sweep LPS or Spectrozir parity.

#include "sdr_core/sweep_line_assembler.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr double sample_rate_hz = 61'440'000.0;
constexpr double usable_window_hz = 36'000'000.0;
constexpr double overlap_hz = 2'000'000.0;
constexpr std::uint32_t max_fft_size = 262'144U;
constexpr std::size_t latency_reservoir_capacity = 8192U;

struct Arguments {
    std::uint32_t analysis_bins{1024U};
    std::uint32_t segments{1U};
    std::uint32_t warmup_lines{256U};
    std::uint32_t measurement_lines{2048U};
};

void require(const bool condition, const std::string_view message) {
    if (!condition) {
        throw std::runtime_error(std::string(message));
    }
}

[[nodiscard]] bool power_of_two(const std::uint32_t value) noexcept {
    return value != 0U && (value & (value - 1U)) == 0U;
}

[[nodiscard]] std::uint32_t next_power_of_two(const double value) {
    require(std::isfinite(value) && value > 0.0, "derived physical FFT size is invalid");
    std::uint32_t result = 1U;
    while (static_cast<double>(result) < value) {
        require(result <= max_fft_size / 2U, "derived physical FFT exceeds the native bound");
        result <<= 1U;
    }
    return result;
}

[[nodiscard]] Arguments parse_arguments(const int argc, char** argv) {
    Arguments result;
    for (int index = 1; index < argc; ++index) {
        const std::string_view argument(argv[index]);
        if (argument == "--help") {
            std::cout << "usage: sdr_core_sweep_line_assembly_bench "
                         "[--analysis-bins N] [--segments N] "
                         "[--warmup-lines N] [--measurement-lines N]\n";
            std::exit(0);
        }
        require(index + 1 < argc, "missing benchmark argument value");
        const auto value = static_cast<std::uint32_t>(std::stoul(argv[++index]));
        if (argument == "--analysis-bins") {
            result.analysis_bins = value;
        } else if (argument == "--segments") {
            result.segments = value;
        } else if (argument == "--warmup-lines") {
            result.warmup_lines = value;
        } else if (argument == "--measurement-lines") {
            result.measurement_lines = value;
        } else {
            throw std::runtime_error("unsupported argument: " + std::string(argument));
        }
    }
    require(
        result.analysis_bins >= 256U && result.analysis_bins <= max_fft_size &&
            power_of_two(result.analysis_bins),
        "--analysis-bins must be a power of two in [256, 262144]"
    );
    require(result.segments >= 1U && result.segments <= 64U, "--segments must be in [1, 64]");
    require(result.measurement_lines >= 1U && result.measurement_lines <= 1'000'000U,
            "--measurement-lines must be in [1, 1000000]");
    require(result.warmup_lines <= 1'000'000U, "--warmup-lines must be <= 1000000");
    return result;
}

[[nodiscard]] sdr_core::SourceDescriptor source() {
    return {
        .source_type = sdr_core::SourceType::Synthetic,
        .source_id = "r10d5-sweep-line-assembly-benchmark",
        .display_name = "R10-D5 synthetic Sweep line assembly benchmark",
        .uri = "synthetic:r10d5-sweep-line-assembly-benchmark",
        .backend_id = "cpu",
    };
}

struct Geometry {
    std::uint32_t physical_fft_size{};
    double analysis_spacing_hz{};
    double physical_spacing_hz{};
    double display_start_hz{2'400'000'000.0};
    double display_stop_hz{};
    std::size_t final_bins{};
};

[[nodiscard]] Geometry geometry_for(const Arguments& arguments) {
    Geometry result;
    result.analysis_spacing_hz = usable_window_hz / static_cast<double>(arguments.analysis_bins);
    result.physical_fft_size = next_power_of_two(sample_rate_hz / result.analysis_spacing_hz);
    result.physical_spacing_hz = sample_rate_hz / static_cast<double>(result.physical_fft_size);
    const auto stride_hz = usable_window_hz - overlap_hz;
    result.display_stop_hz = result.display_start_hz + usable_window_hz +
        static_cast<double>(arguments.segments - 1U) * stride_hz;
    const auto span_hz = result.display_stop_hz - result.display_start_hz;
    const auto bins = std::ceil(span_hz / result.analysis_spacing_hz - 1e-12);
    require(std::isfinite(bins) && bins >= 2.0 && bins <= 2'000'000.0,
            "final analysis grid exceeds the bounded native line capacity");
    result.final_bins = static_cast<std::size_t>(bins);
    return result;
}

[[nodiscard]] sdr_core::SweepLineDefinition definition_for(
    const Arguments& arguments,
    const Geometry& geometry
) {
    std::vector<sdr_core::SweepLineSegmentDefinition> segments;
    segments.reserve(arguments.segments);
    const auto stride_hz = usable_window_hz - overlap_hz;
    for (std::uint32_t index = 0U; index < arguments.segments; ++index) {
        const auto usable_start_hz = geometry.display_start_hz + static_cast<double>(index) * stride_hz;
        segments.push_back({
            .segment_index = index,
            .config_generation = static_cast<std::uint64_t>(index) + 1U,
            .usable_start_hz = usable_start_hz,
            .usable_stop_hz = usable_start_hz + usable_window_hz,
        });
    }
    return {
        .source = source(),
        .epoch = 1U,
        .start_frequency_hz = geometry.display_start_hz,
        .stop_frequency_hz = geometry.display_stop_hz,
        .target_spacing_hz = geometry.analysis_spacing_hz,
        .analysis_window_hz = usable_window_hz,
        .analysis_bins_per_usable_window = arguments.analysis_bins,
        .physical_fft_bin_width_hz = geometry.physical_spacing_hz,
        .physical_fft_size = geometry.physical_fft_size,
        .unit = sdr_core::SpectrumUnit::DbfsBin,
        .max_inflight_lines = 1U,
        .segments = std::move(segments),
    };
}

[[nodiscard]] std::vector<sdr_core::SweepLineSegmentFrame> input_frames_for(
    const sdr_core::SweepLineDefinition& definition,
    const Geometry& geometry
) {
    std::vector<sdr_core::SweepLineSegmentFrame> result;
    result.reserve(definition.segments.size());
    for (const auto& segment : definition.segments) {
        auto frequencies = std::make_shared<std::vector<double>>(geometry.physical_fft_size);
        auto values = std::make_shared<std::vector<float>>(geometry.physical_fft_size);
        // The physical window is deliberately larger than the usable 36 MHz
        // range. This mirrors the full-rate FFT followed by edge clipping and
        // gives lower_bound/interpolation the real source-grid shape.
        const auto physical_start_hz = segment.usable_start_hz -
            (sample_rate_hz - usable_window_hz) / 2.0;
        for (std::uint32_t bin = 0U; bin < geometry.physical_fft_size; ++bin) {
            (*frequencies)[bin] = physical_start_hz +
                static_cast<double>(bin) * geometry.physical_spacing_hz;
            const auto phase = static_cast<double>(bin) * 0.013 +
                static_cast<double>(segment.segment_index) * 0.17;
            (*values)[bin] = static_cast<float>(-98.0 + 6.0 * std::sin(phase));
        }
        result.push_back({
            .segment_index = segment.segment_index,
            .spectrum = {
                .source = definition.source,
                .frame_sequence = static_cast<std::uint64_t>(segment.segment_index) + 1U,
                .timestamp_ns = 1,
                .config_generation = segment.config_generation,
                .center_frequency_hz = (segment.usable_start_hz + segment.usable_stop_hz) / 2.0,
                .sample_rate_hz = sample_rate_hz,
                .analog_bandwidth_hz = 56'000'000.0,
                .fft_bin_width_hz = geometry.physical_spacing_hz,
                .enbw_hz = geometry.physical_spacing_hz * 1.5,
                .nominal_rbw_hz = geometry.physical_spacing_hz,
                .fft_size = geometry.physical_fft_size,
                .hop_size = geometry.physical_fft_size / 2U,
                .window = sdr_core::WindowType::Hann,
                .detector = sdr_core::DetectorType::Sample,
                .precision_mode = sdr_core::PrecisionMode::AccurateF32F64Accum,
                .unit = sdr_core::SpectrumUnit::DbfsBin,
                .frequencies_hz = std::const_pointer_cast<const std::vector<double>>(frequencies),
                .values = std::const_pointer_cast<const std::vector<float>>(values),
                .calibration_status = sdr_core::CalibrationStatus::Uncalibrated,
                .estimated_uncertainty_db = std::numeric_limits<double>::quiet_NaN(),
                .quality_flags = sdr_core::QualityFlag::Uncalibrated,
            },
        });
    }
    return result;
}

[[nodiscard]] sdr_core::SweepLineFrame assemble_one(
    sdr_core::ContinuousSweepLineAssembler& assembler,
    const std::vector<sdr_core::SweepLineSegmentFrame>& frames,
    const std::uint64_t sequence
) {
    std::vector<sdr_core::SweepLineFrame> emitted;
    for (const auto& frame : frames) {
        emitted = assembler.admit(sequence, static_cast<std::int64_t>(sequence), frame);
    }
    require(emitted.size() == 1U && emitted.front().state == sdr_core::SweepLineState::Complete,
            "synthetic Sweep line did not complete exactly once");
    return std::move(emitted.front());
}

[[nodiscard]] double percentile(std::vector<double> values, const double fraction) {
    require(!values.empty(), "benchmark collected no latency samples");
    std::sort(values.begin(), values.end());
    const auto rank = static_cast<std::size_t>(std::ceil(fraction * static_cast<double>(values.size())));
    return values[std::min(rank == 0U ? 0U : rank - 1U, values.size() - 1U)];
}

int run(const Arguments& arguments) {
    const auto geometry = geometry_for(arguments);
    const auto definition = definition_for(arguments, geometry);
    const auto frames = input_frames_for(definition, geometry);

    {
        sdr_core::ContinuousSweepLineAssembler warmup(definition);
        for (std::uint32_t line = 0U; line < arguments.warmup_lines; ++line) {
            static_cast<void>(assemble_one(warmup, frames, static_cast<std::uint64_t>(line) + 1U));
        }
    }

    sdr_core::ContinuousSweepLineAssembler measurement(definition);
    std::vector<double> latencies_ms;
    latencies_ms.reserve(std::min<std::size_t>(arguments.measurement_lines, latency_reservoir_capacity));
    volatile double checksum = 0.0;
    const auto started = std::chrono::steady_clock::now();
    for (std::uint32_t line = 0U; line < arguments.measurement_lines; ++line) {
        const auto line_started = std::chrono::steady_clock::now();
        const auto completed = assemble_one(measurement, frames, static_cast<std::uint64_t>(line) + 1U);
        const auto elapsed = std::chrono::steady_clock::now() - line_started;
        if (latencies_ms.size() < latency_reservoir_capacity) {
            latencies_ms.push_back(std::chrono::duration<double, std::milli>(elapsed).count());
        }
        checksum += static_cast<double>((*completed.values)[line % completed.values->size()]);
    }
    const auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    const auto metrics = measurement.metrics();
    require(metrics.completed_lines == arguments.measurement_lines && metrics.gapped_lines == 0U,
            "benchmark produced an incomplete or gapped synthetic Sweep line");
    require(std::isfinite(checksum), "benchmark checksum is not finite");

    std::cout << std::setprecision(17)
              << "{\"schema\":\"sdr-native-r10d5-sweep-line-assembly-bench-v1\""
              << ",\"source_kind\":\"synthetic_spectrum_no_radio\""
              << ",\"config\":{\"sample_rate_hz\":" << sample_rate_hz
              << ",\"usable_window_hz\":" << usable_window_hz
              << ",\"overlap_hz\":" << overlap_hz
              << ",\"segments\":" << arguments.segments
              << ",\"analysis_bins_per_usable_window\":" << arguments.analysis_bins
              << ",\"analysis_bin_spacing_hz\":" << geometry.analysis_spacing_hz
              << ",\"physical_fft_size\":" << geometry.physical_fft_size
              << ",\"physical_fft_bin_width_hz\":" << geometry.physical_spacing_hz
              << ",\"final_line_bins\":" << geometry.final_bins << "}"
              << ",\"measurement\":{\"warmup_lines\":" << arguments.warmup_lines
              << ",\"completed_lines\":" << metrics.completed_lines
              << ",\"elapsed_seconds\":" << elapsed
              << ",\"completed_line_lps\":" << static_cast<double>(metrics.completed_lines) / elapsed
              << ",\"latency_ms\":{\"sample_count\":" << latencies_ms.size()
              << ",\"p50_ms\":" << percentile(latencies_ms, 0.50)
              << ",\"p95_ms\":" << percentile(latencies_ms, 0.95)
              << ",\"p99_ms\":" << percentile(latencies_ms, 0.99) << "}}"
              << ",\"quality\":{\"gapped_lines\":0,\"checksum\":" << checksum << "}"
              << ",\"not_verified\":[\"No FFT execution, I/Q acquisition, retune, USB, Ethernet, iiod, Python or Qt path was measured.\",\"This result is not physical Sweep LPS and cannot establish Spectrozir parity.\"]"
              << "}\n";
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        return run(parse_arguments(argc, argv));
    } catch (const std::exception& error) {
        std::cerr << "sdr_core_sweep_line_assembly_bench: " << error.what() << '\n';
        return 1;
    }
}
