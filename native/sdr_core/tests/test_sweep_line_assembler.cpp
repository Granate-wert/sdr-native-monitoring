#include "sdr_core/errors.hpp"
#include "sdr_core/sweep_line_assembler.hpp"

#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <vector>

namespace {

[[nodiscard]] sdr_core::SourceDescriptor source() {
    return {
        .source_type = sdr_core::SourceType::LiveIq,
        .source_id = "r10d-native-test",
        .display_name = "R10-D native test",
        .uri = "synthetic:r10d-native-test",
        .backend_id = "cpu",
    };
}

[[nodiscard]] sdr_core::SweepLineDefinition definition(
    const std::uint32_t capacity = 2U
) {
    return {
        .source = source(),
        .epoch = 7U,
        .start_frequency_hz = 100.0,
        .stop_frequency_hz = 108.0,
        .target_spacing_hz = 1.0,
        .unit = sdr_core::SpectrumUnit::DbfsBin,
        .max_inflight_lines = capacity,
        .segments = {
            {.segment_index = 0U, .config_generation = 11U, .usable_start_hz = 100.0, .usable_stop_hz = 104.0},
            {.segment_index = 1U, .config_generation = 12U, .usable_start_hz = 104.0, .usable_stop_hz = 108.0},
        },
    };
}

[[nodiscard]] sdr_core::SweepLineSegmentFrame segment(
    const std::uint32_t index,
    const std::uint64_t generation,
    const float offset = 0.0F
) {
    auto frequencies = std::make_shared<std::vector<double>>(
        index == 0U
            ? std::initializer_list<double>{100.0, 101.0, 102.0, 103.0, 104.0}
            : std::initializer_list<double>{104.0, 105.0, 106.0, 107.0, 108.0}
    );
    auto values = std::make_shared<std::vector<float>>(
        std::initializer_list<float>{-90.0F + offset, -89.0F + offset, -88.0F + offset,
                                     -87.0F + offset, -86.0F + offset}
    );
    return {
        .segment_index = index,
        .spectrum = {
            .source = source(),
            .frame_sequence = index,
            .timestamp_ns = 123,
            .config_generation = generation,
            .center_frequency_hz = index == 0U ? 102.0 : 106.0,
            .sample_rate_hz = 8.0,
            .analog_bandwidth_hz = 8.0,
            .fft_bin_width_hz = 1.0,
            .enbw_hz = 1.0,
            .nominal_rbw_hz = 1.0,
            .fft_size = 5U,
            .hop_size = 2U,
            .window = sdr_core::WindowType::Hann,
            .detector = sdr_core::DetectorType::Sample,
            .precision_mode = sdr_core::PrecisionMode::AccurateF32F64Accum,
            .unit = sdr_core::SpectrumUnit::DbfsBin,
            .frequencies_hz = frequencies,
            .values = values,
            .calibration_status = sdr_core::CalibrationStatus::Uncalibrated,
            .estimated_uncertainty_db = std::numeric_limits<double>::quiet_NaN(),
            .quality_flags = sdr_core::QualityFlag::Uncalibrated,
        },
    };
}

[[nodiscard]] bool rejects(auto&& action) {
    try {
        action();
    } catch (const sdr_core::ConfigurationError&) {
        return true;
    }
    return false;
}

}  // namespace

int main() {
    try {
        sdr_core::ContinuousSweepLineAssembler assembler(definition());
        auto unresolved = definition();
        for (auto& item : unresolved.segments) item.config_generation = 0;
        sdr_core::ContinuousSweepLineAssembler bound(unresolved);
        if (!rejects([&] { static_cast<void>(bound.admit(1, 1, segment(0, 11))); }) ||
            !rejects([&] { bound.bind_segment_generation(0, 0); }) ||
            !rejects([&] { bound.bind_segment_generation(99, 11); })) {
            std::cerr << "unresolved generation accepted without readback" << std::endl;
            return 24;
        }
        bound.bind_segment_generation(0, 11);
        static_cast<void>(bound.admit(1, 1, segment(0, 11)));
        if (!rejects([&] { bound.bind_segment_generation(0, 12); })) {
            std::cerr << "bound generation changed after admission" << std::endl;
            return 25;
        }
        bound.bind_segment_generation(1, 12);
        if (bound.admit(1, 2, segment(1, 12)).size() != 1) {
            std::cerr << "readback-bound plan did not complete" << std::endl;
            return 26;
        }
        if (!assembler.admit(10U, 1000, segment(1U, 12U)).empty()) {
            std::cerr << "partial line was emitted" << std::endl;
            return 1;
        }
        const auto preview = assembler.preview(10U);
        if (!preview || preview->segment_acquisition.size() != 1 ||
            preview->segment_acquisition[0].segment_index != 1 ||
            preview->segment_acquisition[0].timestamp_ns != 123 ||
            preview->segment_acquisition[0].config_generation != 12 ||
            preview->segment_acquisition[0].frame_sequence != 1 ||
            preview->segment_acquisition[0].fft_size != 5 ||
            preview->segment_acquisition[0].sample_rate_hz != 8.0) {
            std::cerr << "preview lost segment acquisition metadata" << std::endl;
            return 27;
        }
        if (!preview || preview->revision != 1 || preview->acquired_segments.size() != 1 ||
            preview->acquired_segments[0].config_generation != 12 ||
            preview->pending_segment_indices != std::vector<std::uint32_t>{0} ||
            !std::isnan((*preview->values)[0]) || !std::isfinite((*preview->values)[8]) ||
            assembler.metrics().completed_lines != 0 || assembler.metrics().gapped_lines != 0 ||
            assembler.preview(10U)->revision != 1 || assembler.preview(999U).has_value()) {
            std::cerr << "progress preview lost provenance or changed terminal state" << std::endl;
            return 22;
        }
        const auto complete = assembler.admit(10U, 1001, segment(0U, 11U));
        if (complete.size() != 1 || complete[0].acquired_segments.size() != 2 ||
            complete[0].acquired_segments[0].segment_index != 0 ||
            complete[0].acquired_segments[1].timestamp_ns != 123 ||
            complete[0].completed_ns != 1001) {
            std::cerr << "terminal acquisition times confused with completion" << std::endl;
            return 28;
        }
        if (assembler.preview(10U).has_value() || !std::isnan((*preview->values)[0])) {
            std::cerr << "completed line retained preview or mutated published data" << std::endl;
            return 23;
        }
        if (complete.size() != 1U || complete[0].state != sdr_core::SweepLineState::Complete ||
            complete[0].frequencies_hz->size() != 9U || complete[0].line_sequence != 10U ||
            complete[0].segment_generations.size() != 2U ||
            !complete[0].gap_reasons.empty()) {
            std::cerr << "complete line contract mismatch" << std::endl;
            return 2;
        }
        const auto overlap_expected = static_cast<float>(10.0 * std::log10(
            (std::pow(10.0, -86.0 / 10.0) + std::pow(10.0, -90.0 / 10.0)) / 2.0
        ));
        if ((*complete[0].source_segment_indices)[4] != 0 ||
            std::abs((*complete[0].values)[4] - overlap_expected) > 0.001F ||
            !sdr_core::has_flag(
                static_cast<sdr_core::QualityFlag>((*complete[0].quality_flags_per_bin)[4]),
                sdr_core::QualityFlag::StitchOverlap
            )) {
            std::cerr << "overlap stitch did not retain linear-power semantics" << std::endl;
            return 3;
        }
        // APP-01/F02: source quality survives stitching and is unioned only
        // Compare forward/reverse arrivals without promising bitwise sums.
        sdr_core::ContinuousSweepLineAssembler forward(definition());
        static_cast<void>(forward.admit(10U, 1000, segment(0U, 11U)));
        const auto ordered = forward.admit(10U, 1001, segment(1U, 12U));
        if (ordered.size() != 1U ||
            *ordered[0].source_segment_indices != *complete[0].source_segment_indices ||
            *ordered[0].quality_flags_per_bin != *complete[0].quality_flags_per_bin ||
            *ordered[0].frequencies_hz != *complete[0].frequencies_hz) {
            std::cerr << "arrival order changed sweep provenance" << std::endl;
            return 20;
        }
        for (std::size_t bin = 0; bin < complete[0].values->size(); ++bin) {
            if (std::abs((*ordered[0].values)[bin] - (*complete[0].values)[bin]) > 1e-5F) {
                std::cerr << "arrival order changed sweep power beyond tolerance" << std::endl;
                return 21;
            }
        }
        // APP-01/F02: source quality survives stitching and is unioned only
        // where those segments actually contribute.
        auto flagged_left = segment(0U, 11U);
        auto flagged_right = segment(1U, 12U);
        const auto left_flags = sdr_core::QualityFlag::AdcOverload |
            sdr_core::QualityFlag::Uncalibrated;
        const auto right_flags = sdr_core::QualityFlag::IqDropped |
            sdr_core::QualityFlag::TimestampEstimated;
        flagged_left.spectrum.quality_flags = left_flags;
        flagged_right.spectrum.quality_flags = right_flags;
        sdr_core::ContinuousSweepLineAssembler quality_assembler(definition());
        static_cast<void>(quality_assembler.admit(12U, 1000, flagged_left));
        const auto quality_line = quality_assembler.admit(12U, 1001, flagged_right);
        const auto& quality_bins = *quality_line.at(0).quality_flags_per_bin;
        if (quality_bins[0] != static_cast<std::uint32_t>(left_flags) ||
            quality_bins[8] != static_cast<std::uint32_t>(right_flags) ||
            quality_bins[4] != static_cast<std::uint32_t>(
                left_flags | right_flags | sdr_core::QualityFlag::StitchOverlap)) {
            std::cerr << "source quality lost or leaked across sweep coverage" << std::endl;
            return 21;
        }
        // The hot 36 MHz assembly path advances through monotonic FFT and
        // target grids. Preserve the original linear-power interpolation
        // semantics for target frequencies between physical FFT bins.
        auto interpolated_definition = definition();
        interpolated_definition.target_spacing_hz = 0.5;
        sdr_core::ContinuousSweepLineAssembler interpolated(interpolated_definition);
        static_cast<void>(interpolated.admit(11U, 1000, segment(0U, 11U)));
        const auto interpolated_complete = interpolated.admit(11U, 1001, segment(1U, 12U));
        const auto half_bin_expected = static_cast<float>(10.0 * std::log10(
            (std::pow(10.0, -90.0 / 10.0) + std::pow(10.0, -89.0 / 10.0)) / 2.0
        ));
        if (interpolated_complete.size() != 1U ||
            std::abs((*interpolated_complete[0].values)[1] - half_bin_expected) > 0.001F) {
            std::cerr << "monotonic stitch path changed linear-power interpolation" << std::endl;
            return 20;
        }
        const auto complete_metrics = assembler.metrics();
        if ((*interpolated_complete[0].quality_flags_per_bin)[1] !=
            static_cast<std::uint32_t>(sdr_core::QualityFlag::Uncalibrated)) {
            std::cerr << "interpolation lost source quality" << std::endl;
            return 22;
        }
        auto invalid_values = segment(0U, 11U);
        invalid_values.spectrum.values = std::make_shared<std::vector<float>>(
            5U, std::numeric_limits<float>::quiet_NaN());
        sdr_core::ContinuousSweepLineAssembler invalid_assembler(definition());
        if (!rejects([&] { static_cast<void>(invalid_assembler.admit(15U, 1000, invalid_values)); })) {
            std::cerr << "NaN spectrum admitted as measurement" << std::endl;
            return 23;
        }
        // Infinite source values are non-contributors under the existing
        // stitch policy. They must not leak their flags into a finite neighbor.
        auto unusable = segment(0U, 11U);
        unusable.spectrum.quality_flags = sdr_core::QualityFlag::AdcOverload;
        unusable.spectrum.values = std::make_shared<std::vector<float>>(
            5U, -std::numeric_limits<float>::infinity());
        sdr_core::ContinuousSweepLineAssembler gapped(definition());
        static_cast<void>(gapped.admit(16U, 1000, unusable));
        const auto gap_line = gapped.admit(16U, 1001, segment(1U, 12U));
        if (gap_line.at(0).state != sdr_core::SweepLineState::Gap ||
            !std::isnan((*gap_line[0].values)[0]) ||
            (*gap_line[0].quality_flags_per_bin)[0] !=
                static_cast<std::uint32_t>(sdr_core::QualityFlag::MissingSegment) ||
            (*gap_line[0].quality_flags_per_bin)[4] !=
                static_cast<std::uint32_t>(sdr_core::QualityFlag::Uncalibrated)) {
            std::cerr << "non-contributing spectrum leaked quality or hid gap" << std::endl;
            return 24;
        }
        if (complete_metrics.completed_lines != 1U || complete_metrics.gapped_lines != 0U ||
            complete_metrics.pending_lines != 0U) {
            std::cerr << "complete metrics mismatch" << std::endl;
            return 4;
        }

        sdr_core::ContinuousSweepLineAssembler capacity_assembler(definition(1U));
        static_cast<void>(capacity_assembler.admit(1U, 10, segment(0U, 11U)));
        const auto evicted = capacity_assembler.admit(2U, 20, segment(0U, 11U));
        if (evicted.size() != 1U || evicted[0].state != sdr_core::SweepLineState::Gap ||
            evicted[0].gap_reasons != std::vector<sdr_core::SweepLineGapReason>{
                sdr_core::SweepLineGapReason::Capacity
            } ||
            evicted[0].missing_segment_indices != std::vector<std::uint32_t>{1U}) {
            std::cerr << "capacity eviction did not publish an explicit gap" << std::endl;
            return 5;
        }
        const auto flushed = capacity_assembler.flush(sdr_core::SweepLineGapReason::Cancellation);
        if (flushed.size() != 1U || flushed[0].gap_reasons !=
            std::vector<sdr_core::SweepLineGapReason>{
                sdr_core::SweepLineGapReason::Cancellation
            }) {
            std::cerr << "flush did not preserve the control-gap reason" << std::endl;
            return 6;
        }
        const auto explicit_gap = capacity_assembler.emit_gap(
            3U, 30, sdr_core::SweepLineGapReason::Reconfigure
        );
        if (explicit_gap.state != sdr_core::SweepLineState::Gap ||
            explicit_gap.missing_segment_indices.size() != 2U ||
            explicit_gap.gap_reasons != std::vector<sdr_core::SweepLineGapReason>{
                sdr_core::SweepLineGapReason::Reconfigure
            }) {
            std::cerr << "terminal control gap was not explicit" << std::endl;
            return 17;
        }
        const auto gaps = capacity_assembler.metrics();
        if (gaps.gapped_lines != 3U || gaps.capacity_evicted_lines != 1U || gaps.pending_lines != 0U) {
            std::cerr << "gap metrics mismatch" << std::endl;
            return 7;
        }

        // In R10-D5, the user-visible N belongs to one 36 MHz usable window,
        // not to the full-rate physical transform.  A 30 kHz physical FFT is
        // intentionally denser than the 36 MHz / 1024 = 35.15625 kHz final
        // grid, so the native assembler may reduce but must never upsample.
        auto analysis_grid = definition();
        analysis_grid.start_frequency_hz = 2'400'000'000.0;
        analysis_grid.stop_frequency_hz = 2'436'000'000.0;
        analysis_grid.target_spacing_hz = 36'000'000.0 / 1024.0;
        analysis_grid.analysis_window_hz = 36'000'000.0;
        analysis_grid.analysis_bins_per_usable_window = 1024U;
        analysis_grid.physical_fft_bin_width_hz = 30'000.0;
        analysis_grid.physical_fft_size = 2048U;
        analysis_grid.segments = {{
            .segment_index = 0U,
            .config_generation = 13U,
            .usable_start_hz = 2'400'000'000.0,
            .usable_stop_hz = 2'436'000'000.0,
        }};
        sdr_core::ContinuousSweepLineAssembler analysis_assembler(analysis_grid);
        const auto analysis_gap = analysis_assembler.emit_gap(
            99U, 99, sdr_core::SweepLineGapReason::Cancellation
        );
        if (analysis_gap.frequencies_hz->size() != 1024U ||
            std::abs((*analysis_gap.frequencies_hz)[1] - (*analysis_gap.frequencies_hz)[0] -
                     (36'000'000.0 / 1024.0)) > 1e-6 ||
            analysis_gap.analysis_window_hz != 36'000'000.0 ||
            analysis_gap.analysis_bins_per_usable_window != 1024U ||
            analysis_gap.physical_fft_bin_width_hz != 30'000.0 ||
            analysis_gap.physical_fft_size != 2048U) {
            std::cerr << "analysis-grid geometry/provenance mismatch" << std::endl;
            return 18;
        }
        auto invalid_analysis_grid = analysis_grid;
        invalid_analysis_grid.physical_fft_bin_width_hz = 40'000.0;
        if (!rejects([&invalid_analysis_grid] {
                sdr_core::ContinuousSweepLineAssembler rejected(invalid_analysis_grid);
            })) {
            std::cerr << "analysis grid accepted resolution finer than physical FFT" << std::endl;
            return 19;
        }

        if (!rejects([&assembler] { static_cast<void>(assembler.admit(11U, 1, segment(0U, 99U))); }) ||
            !rejects([&assembler] {
                static_cast<void>(assembler.admit(12U, 1, segment(0U, 11U)));
                static_cast<void>(assembler.admit(12U, 99, segment(0U, 11U)));
            })) {
            std::cerr << "invalid generation or duplicate segment was accepted" << std::endl;
            return 8;
        }
        const auto atomic_duplicate = assembler.admit(12U, 2, segment(1U, 12U));
        if (atomic_duplicate.size() != 1U || atomic_duplicate[0].completed_ns != 2) {
            std::cerr << "duplicate admission mutated an existing pending line" << std::endl;
            return 9;
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 10;
    }
    return 0;
}
