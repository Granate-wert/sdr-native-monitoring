#include "sdr_core/errors.hpp"
#include "sdr_core/recording_reprocess.hpp"
#include "sdr_core/recording_writer.hpp"

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

void require(const bool condition, const char* message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

[[nodiscard]] sdr_core::SourceDescriptor source() {
    return {
        .source_type = sdr_core::SourceType::LiveIq,
        .source_id = "r08b-mock",
        .display_name = "R08-B mock source",
        .uri = "mock://r08b",
        .device_serial = {},
        .backend_id = "mock",
        .schema_version = sdr_core::contract_schema_version,
        .metadata_json = {},
    };
}

[[nodiscard]] sdr_core::IqBlock block(
    const std::uint64_t first_sample_index,
    const std::uint64_t sequence,
    const std::uint8_t seed
) {
    auto samples = std::vector<std::uint8_t>(12U);
    for (std::size_t index = 0U; index < samples.size(); ++index) {
        samples[index] = static_cast<std::uint8_t>(seed + index);
    }
    return {
        .source_sequence = sequence,
        .first_sample_index = first_sample_index,
        .timestamp_ns = static_cast<std::int64_t>(1'000'000U + first_sample_index),
        .center_frequency_hz = 2.45e9,
        .sample_rate_hz = 3.0e6,
        .sample_format = sdr_core::SampleFormat::ComplexInt12InInt16Le,
        .sample_count = 3U,
        .flags = sdr_core::QualityFlag::None,
        .samples = std::make_shared<const std::vector<std::uint8_t>>(std::move(samples)),
        .config_generation = 7U,
    };
}

[[nodiscard]] std::string read_text(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    return {
        std::istreambuf_iterator<char>(input),
        std::istreambuf_iterator<char>(),
    };
}

[[nodiscard]] sdr_core::RecordingConfig recording_config(
    const std::filesystem::path& base
) {
    return {
        .enabled = true,
        .output_uri = base.string(),
        .record_iq = true,
        .record_spectrum = false,
        .chunk_samples = 4U,
        .queue_capacity = 2U,
        .stop_on_overflow = false,
        .schema_version = sdr_core::contract_schema_version,
    };
}

[[nodiscard]] sdr_core::RecordingConfig spectrum_recording_config(
    const std::filesystem::path& base
) {
    auto result = recording_config(base);
    result.record_iq = false;
    result.record_spectrum = true;
    return result;
}

[[nodiscard]] sdr_core::SpectrumFrame spectrum_frame() {
    return {
        .source = source(),
        .frame_sequence = 11U,
        .first_sample_index = 512U,
        .timestamp_ns = 7'000'000,
        .config_generation = 7U,
        .center_frequency_hz = 2.45e9,
        .sample_rate_hz = 3.0e6,
        .analog_bandwidth_hz = 1.5e6,
        .fft_bin_width_hz = 750'000.0,
        .enbw_hz = 1.125e6,
        .nominal_rbw_hz = 1.125e6,
        .fft_size = 4U,
        .hop_size = 2U,
        .window = sdr_core::WindowType::Hann,
        .detector = sdr_core::DetectorType::Sample,
        .precision_mode = sdr_core::PrecisionMode::AccurateF32F64Accum,
        .unit = sdr_core::SpectrumUnit::DbfsBin,
        .frequencies_hz = std::make_shared<const std::vector<double>>(
            std::initializer_list<double>{2.449e9, 2.4495e9, 2.45e9, 2.4505e9}
        ),
        .values = std::make_shared<const std::vector<float>>(
            std::initializer_list<float>{-90.0F, -80.0F, -70.0F, -60.0F}
        ),
        .calibration_status = sdr_core::CalibrationStatus::Uncalibrated,
        .calibration_profile_id = {},
        .estimated_uncertainty_db = std::numeric_limits<double>::quiet_NaN(),
        .dropped_samples_before = 3U,
        .dropped_iq_blocks_before = 1U,
        .dropped_fft_frames_before = 2U,
        .quality_flags = sdr_core::QualityFlag::Uncalibrated,
    };
}

[[nodiscard]] sdr_core::IqBlock reprocess_block(
    const std::uint64_t first_sample_index,
    const std::uint64_t sequence
) {
    auto samples = std::vector<std::uint8_t>(2048U);
    for (std::size_t index = 0U; index < samples.size(); index += 4U) {
        const auto value = static_cast<std::int16_t>(128 + static_cast<std::int16_t>(index));
        samples[index] = static_cast<std::uint8_t>(value & 0xFF);
        samples[index + 1U] = static_cast<std::uint8_t>((value >> 8) & 0xFF);
        samples[index + 2U] = static_cast<std::uint8_t>(value & 0xFF);
        samples[index + 3U] = static_cast<std::uint8_t>((value >> 8) & 0xFF);
    }
    return {
        .source_sequence = sequence,
        .first_sample_index = first_sample_index,
        .timestamp_ns = static_cast<std::int64_t>(10'000'000U + first_sample_index),
        .center_frequency_hz = 2.45e9,
        .sample_rate_hz = 3.0e6,
        .sample_format = sdr_core::SampleFormat::ComplexInt16Le,
        .sample_count = 512U,
        .flags = sdr_core::QualityFlag::None,
        .samples = std::make_shared<const std::vector<std::uint8_t>>(std::move(samples)),
        .config_generation = 9U,
    };
}

[[nodiscard]] sdr_core::DspConfig reprocess_dsp_config() {
    return {
        .fft_size = 256U,
        .hop_size = 128U,
        .window = sdr_core::WindowType::Hann,
        .detector = sdr_core::DetectorType::Sample,
        .unit = sdr_core::SpectrumUnit::DbfsBin,
        .precision_mode = sdr_core::PrecisionMode::AccurateF32F64Accum,
        .batch_size = 1U,
        .averaging_frames = 1U,
        .kaiser_beta = 8.6,
        .calibration_status = sdr_core::CalibrationStatus::Uncalibrated,
        .calibration_profile_id = {},
        .schema_version = sdr_core::contract_schema_version,
    };
}

}  // namespace

int main() {
    const auto unique = std::to_string(
        std::chrono::steady_clock::now().time_since_epoch().count()
    );
    const auto root = std::filesystem::temp_directory_path() /
                      ("sdr_core_recording_writer_" + unique);
    try {
        std::filesystem::create_directories(root);
        const auto base = root / "capture";
        sdr_core::SegmentedIqRecordingWriter writer(recording_config(base), source());
        writer.start();
        writer.write_block(block(0U, 1U, 1U));
        writer.write_block(block(6U, 2U, 21U));
        writer.set_recorder_queue_loss(2U, 6U);
        writer.finalize();

        const auto metrics = writer.metrics();
        require(!metrics.active && metrics.finalized && !metrics.failed,
                "writer final state is incorrect");
        require(metrics.written_blocks == 2U && metrics.written_samples == 6U &&
                    metrics.written_bytes == 24U && metrics.segments == 2U,
                "writer block/segment accounting is incorrect");
        require(metrics.gaps == 1U && metrics.gap_samples == 3U,
                "writer did not retain explicit sample-index gap accounting");
        require(metrics.recorder_queue_dropped_blocks == 2U &&
                    metrics.recorder_queue_dropped_samples == 6U,
                "writer did not retain tail recorder-queue loss summary");

        const auto manifest = root / "capture.sigmf-meta";
        const auto index = root / "capture.sigmf-index.jsonl";
        const auto gaps = root / "capture.sigmf-gaps.jsonl";
        const auto first_data = root / "capture.000000.sigmf-data";
        const auto second_data = root / "capture.000001.sigmf-data";
        require(std::filesystem::exists(manifest) && std::filesystem::exists(index) &&
                    std::filesystem::exists(gaps) && std::filesystem::exists(first_data) &&
                    std::filesystem::exists(second_data),
                "finalized recording artifact set is incomplete");
        require(!std::filesystem::exists(root / "capture.sigmf-meta.part") &&
                    !std::filesystem::exists(root / "capture.000000.sigmf-data.part"),
                "finalized recording left partial artifacts");
        require(std::filesystem::file_size(first_data) == 12U &&
                    std::filesystem::file_size(second_data) == 12U,
                "segment payload byte count is incorrect");
        const auto manifest_text = read_text(manifest);
        const auto index_text = read_text(index);
        const auto gaps_text = read_text(gaps);
        require(manifest_text.find("\"completed\":true") != std::string::npos &&
                    manifest_text.find("\"core:datatype\":\"ci12_le\"") != std::string::npos &&
                    manifest_text.find("\"segment_count\":2") != std::string::npos &&
                    manifest_text.find("\"recorder_queue_dropped_blocks\":2") !=
                        std::string::npos,
                "final manifest is missing SigMF/provenance summary");
        require(index_text.find("\"type\":\"segment_start\"") != std::string::npos &&
                    index_text.find("\"first_sample_index\":6") != std::string::npos,
                "append-only index is missing segment/block provenance");
        require(gaps_text.find("\"reason\":\"sample_index_gap\"") != std::string::npos &&
                    gaps_text.find("\"sample_count\":3") != std::string::npos,
                "gap sidecar is missing explicit loss record");

        bool duplicate_rejected = false;
        try {
            sdr_core::SegmentedIqRecordingWriter duplicate(recording_config(base), source());
            duplicate.start();
        } catch (const sdr_core::ConfigurationError&) {
            duplicate_rejected = true;
        }
        require(duplicate_rejected, "writer accepted an existing finalized target");

        const auto partial_base = root / "partial";
        sdr_core::SegmentedIqRecordingWriter partial(
            recording_config(partial_base), source()
        );
        partial.start();
        partial.write_block(block(0U, 1U, 42U));
        partial.abort("test_abort");
        require(std::filesystem::exists(root / "partial.sigmf-meta.part") &&
                    std::filesystem::exists(root / "partial.000000.sigmf-data.part"),
                "aborted writer did not retain recoverable partial artifacts");
        require(read_text(root / "partial.sigmf-meta.part").find(
                    "\"completed\":false"
                ) != std::string::npos,
                "partial manifest does not identify incomplete recording");

        {
            std::ofstream tail(
                root / "partial.sigmf-index.jsonl.part",
                std::ios::binary | std::ios::out | std::ios::app
            );
            tail << '{';
        }
        const auto iq_recovery = sdr_core::scan_native_recording_prefix(partial_base);
        require(iq_recovery.iq_manifest_partial && !iq_recovery.iq_manifest_final &&
                    iq_recovery.iq_data_segments == 1U &&
                    iq_recovery.iq_index_complete_records >= 3U &&
                    iq_recovery.iq_index_trailing_bytes == 1U,
                "I/Q partial scanner did not retain the complete index prefix boundary");

        const auto spectrum_base = root / "spectrum";
        sdr_core::SpectrumFrameRecordingWriter spectrum_writer(
            spectrum_recording_config(spectrum_base), source()
        );
        spectrum_writer.start();
        spectrum_writer.write_frame(spectrum_frame());
        auto second_spectrum = spectrum_frame();
        second_spectrum.frame_sequence = 12U;
        second_spectrum.timestamp_ns = 7'500'000;
        second_spectrum.config_generation = 8U;
        spectrum_writer.write_frame(second_spectrum);
        for (std::uint64_t ordinal = 0U; ordinal < 4096U; ++ordinal) {
            auto sparse_spectrum = spectrum_frame();
            sparse_spectrum.frame_sequence = 13U + ordinal;
            sparse_spectrum.timestamp_ns = 8'000'000 + static_cast<std::int64_t>(ordinal);
            sparse_spectrum.config_generation = 9U;
            spectrum_writer.write_frame(sparse_spectrum);
        }
        spectrum_writer.set_recorder_queue_loss(2U);
        spectrum_writer.finalize();
        const auto spectrum_metrics = spectrum_writer.metrics();
        const auto spectrum_manifest = root / "spectrum.sdr-spectrum.meta";
        const auto spectrum_data = root / "spectrum.sdr-spectrum.bin";
        const auto spectrum_index = root / "spectrum.sdr-spectrum-index.jsonl";
        require(!spectrum_metrics.active && spectrum_metrics.finalized &&
                    spectrum_metrics.written_frames == 4098U &&
                    spectrum_metrics.recorder_queue_dropped_frames == 2U &&
                    std::filesystem::exists(spectrum_manifest) &&
                    std::filesystem::exists(spectrum_data) &&
                    std::filesystem::exists(spectrum_index),
                "finalized spectrum writer artifact set is incomplete");
        require(read_text(spectrum_manifest).find(
                    "\"recording_type\":\"native_spectrum_frames\""
                ) != std::string::npos &&
                    read_text(spectrum_index).find("\"bin_count\":4") !=
                        std::string::npos,
                "spectrum writer did not preserve frame metadata");
        const auto spectrum_recovery = sdr_core::scan_native_recording_prefix(spectrum_base);
        require(spectrum_recovery.spectrum_manifest_final &&
                    spectrum_recovery.spectrum_binary_header_valid &&
                    spectrum_recovery.spectrum_complete_records == 4098U &&
                    spectrum_recovery.spectrum_trailing_bytes == 0U,
                "finalized spectrum scanner result is incorrect");

        {
            std::ofstream lifecycle(root / "spectrum.sdr-lifecycle.jsonl", std::ios::binary);
            lifecycle << "{\"type\":\"epoch_start\",\"epoch\":1}\n";
            lifecycle << "{\"type\":\"gap\",\"gap_scope\":\"live_control_transaction\","
                      << "\"gap_duration_ns\":2500000}\n";
        }
        const auto final_info = sdr_core::inspect_final_native_recording(spectrum_base);
        require(!final_info.iq_manifest_final && final_info.spectrum_manifest_final &&
                    final_info.spectrum_frame_count == 4098U &&
                    final_info.lifecycle_epochs == 1U &&
                    final_info.lifecycle_control_gaps == 1U &&
                    final_info.lifecycle_control_gap_duration_ns == 2'500'000U,
                "final native recording inspection lost independent lifecycle evidence");
        sdr_core::NativeSpectrumRecordingReader spectrum_reader(spectrum_base);
        require(spectrum_reader.frame_count() == 4098U &&
                    spectrum_reader.info().spectrum_frame_count == 4098U &&
                    spectrum_reader.info().lifecycle_control_gaps == 1U,
                "bounded native spectrum reader did not admit final capture metadata");
        const auto replay_entry = spectrum_reader.entry_at(4097U);
        const auto replay_frame = spectrum_reader.read_frame(4097U);
        require(replay_entry.ordinal == 4097U && replay_entry.offset > 8U &&
                    replay_entry.record_bytes > 8U && replay_entry.frame_sequence == 4108U &&
                    replay_entry.timestamp_ns == 8'004'095 &&
                    replay_frame.source.source_id == "r08b-mock" &&
                    replay_frame.config_generation == 9U && replay_frame.values &&
                    replay_frame.values->size() == 4U && replay_frame.values->at(3U) == -60.0F,
                "native spectrum reader did not preserve physical index/frame provenance");

        const auto partial_spectrum_base = root / "partial-spectrum";
        sdr_core::SpectrumFrameRecordingWriter partial_spectrum(
            spectrum_recording_config(partial_spectrum_base), source()
        );
        partial_spectrum.start();
        partial_spectrum.write_frame(spectrum_frame());
        partial_spectrum.abort("test_abort");
        {
            std::ofstream tail(
                root / "partial-spectrum.sdr-spectrum.bin.part",
                std::ios::binary | std::ios::out | std::ios::app
            );
            tail << '\x01';
        }
        const auto partial_spectrum_recovery =
            sdr_core::scan_native_recording_prefix(partial_spectrum_base);
        require(partial_spectrum_recovery.spectrum_manifest_partial &&
                    partial_spectrum_recovery.spectrum_binary_header_valid &&
                    partial_spectrum_recovery.spectrum_complete_records == 1U &&
                    partial_spectrum_recovery.spectrum_trailing_bytes == 1U,
                "spectrum partial scanner did not retain the complete record boundary");
        bool partial_reader_rejected = false;
        try {
            [[maybe_unused]] sdr_core::NativeSpectrumRecordingReader partial_reader(
                partial_spectrum_base
            );
        } catch (const sdr_core::ConfigurationError&) {
            partial_reader_rejected = true;
        }
        require(partial_reader_rejected,
                "native spectrum reader accepted a partial capture instead of scan-only state");

        const auto reprocess_input_base = root / "reprocess-input";
        auto reprocess_input_config = recording_config(reprocess_input_base);
        reprocess_input_config.chunk_samples = 512U;
        sdr_core::SegmentedIqRecordingWriter reprocess_input(
            reprocess_input_config, source()
        );
        reprocess_input.start();
        reprocess_input.write_block(reprocess_block(0U, 31U));
        reprocess_input.write_block(reprocess_block(512U, 32U));
        reprocess_input.write_block(reprocess_block(1'280U, 33U));
        reprocess_input.finalize();

        sdr_core::DspBackendSelectionOptions cpu_selection;
        cpu_selection.preference = sdr_core::ComputeBackendKind::Cpu;
        const auto reprocess_output_base = root / "reprocess-output";
        sdr_core::NativeIqRecordingReprocessor reprocessor(
            reprocess_input_base,
            reprocess_output_base,
            reprocess_dsp_config(),
            cpu_selection,
            64U
        );
        require(!reprocessor.process(1U) && !reprocessor.process(1U) &&
                    !reprocessor.process(1U) && reprocessor.process(1U),
                "native I/Q reprocessor did not obey bounded input processing");
        const auto reprocess_progress = reprocessor.progress();
        require(reprocess_progress.state == sdr_core::NativeIqReprocessState::Completed &&
                    reprocess_progress.total_input_blocks == 3U &&
                    reprocess_progress.processed_input_blocks == 3U &&
                    reprocess_progress.processed_input_samples == 1'536U &&
                    reprocess_progress.written_spectrum_frames > 0U &&
                    reprocess_progress.input_gap_boundaries == 1U &&
                    reprocess_progress.input_gap_samples == 256U &&
                    reprocess_progress.discarded_fft_frames == 0U &&
                    reprocess_progress.backend_requested == sdr_core::ComputeBackendKind::Cpu &&
                    reprocess_progress.backend_active == sdr_core::ComputeBackendKind::Cpu,
                "native I/Q reprocessor lost bounded progress, gap or backend provenance");
        {
            sdr_core::NativeSpectrumRecordingReader reprocessed_reader(reprocess_output_base);
            require(reprocessed_reader.frame_count() > 0U &&
                        reprocessed_reader.read_frame(0U).source.source_id == "r08b-mock" &&
                        std::filesystem::exists(root / "reprocess-output.sdr-spectrum.meta"),
                    "native I/Q reprocessor did not atomically publish recorded-I/Q spectrum output");
        }

        const auto cancelled_output_base = root / "reprocess-cancelled";
        sdr_core::NativeIqRecordingReprocessor cancelled_reprocessor(
            reprocess_input_base,
            cancelled_output_base,
            reprocess_dsp_config(),
            cpu_selection,
            64U
        );
        cancelled_reprocessor.request_cancel();
        require(cancelled_reprocessor.process(1U) &&
                    cancelled_reprocessor.progress().state ==
                        sdr_core::NativeIqReprocessState::Cancelled &&
                    std::filesystem::exists(root / "reprocess-cancelled.sdr-spectrum.meta.part") &&
                    !std::filesystem::exists(root / "reprocess-cancelled.sdr-spectrum.meta"),
                "cancelled native I/Q reprocess published a final output or lost recovery evidence");

        std::filesystem::remove_all(root);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        std::error_code ignored;
        std::filesystem::remove_all(root, ignored);
        return 1;
    }
}
