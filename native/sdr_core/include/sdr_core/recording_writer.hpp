#pragma once

#include "sdr_core/configuration.hpp"
#include "sdr_core/types.hpp"

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <vector>

namespace sdr_core {

// Bounded snapshot of a durable raw-I/Q writer.  Segment/block detail belongs
// to the append-only on-disk index; the writer retains only O(1) current
// segment/capture state regardless of recording duration.
struct NativeIqRecordingMetrics {
    bool active{};
    bool finalized{};
    bool failed{};
    std::uint64_t written_blocks{};
    std::uint64_t written_samples{};
    std::uint64_t written_bytes{};
    std::uint64_t gaps{};
    std::uint64_t gap_samples{};
    std::uint64_t segments{};
    std::uint64_t recorder_queue_dropped_blocks{};
    std::uint64_t recorder_queue_dropped_samples{};
};

// Bounded snapshot of the low-rate published-spectrum writer.  A spectrum
// frame retains shared immutable axes/values while queued; it never shares the
// high-rate I/Q writer or its loss counters.
struct NativeSpectrumRecordingMetrics {
    bool active{};
    bool finalized{};
    bool failed{};
    std::uint64_t written_frames{};
    std::uint64_t written_bytes{};
    std::uint64_t recorder_queue_dropped_frames{};
};

// Read-only recovery evidence for a recording that may still consist of .part
// artifacts.  The scanner never guesses I/Q samples or modifies user data; a
// caller can use the last complete sidecar/binary boundary to offer a separate
// repair action later.
struct NativeRecordingRecoveryScan {
    bool iq_manifest_final{};
    bool iq_manifest_partial{};
    bool spectrum_manifest_final{};
    bool spectrum_manifest_partial{};
    std::uint64_t iq_data_segments{};
    std::uint64_t iq_data_bytes{};
    std::uint64_t iq_index_complete_records{};
    std::uint64_t iq_index_complete_bytes{};
    std::uint64_t iq_index_trailing_bytes{};
    std::uint64_t iq_gap_complete_records{};
    std::uint64_t iq_gap_complete_bytes{};
    std::uint64_t iq_gap_trailing_bytes{};
    bool spectrum_binary_header_valid{};
    std::uint64_t spectrum_complete_records{};
    std::uint64_t spectrum_complete_bytes{};
    std::uint64_t spectrum_trailing_bytes{};
};

// Read-only admission evidence for a final native capture.  I/Q and spectrum
// writers intentionally commit independently; a caller must not infer a
// joined transaction from both booleans being true.  Lifecycle gaps describe
// host control transactions and are never re-labelled as physical sample loss.
struct NativeRecordingOpenInfo {
    bool iq_manifest_final{};
    bool spectrum_manifest_final{};
    std::uint64_t iq_gap_records{};
    std::uint64_t spectrum_frame_count{};
    std::uint64_t lifecycle_epochs{};
    std::uint64_t lifecycle_control_gaps{};
    std::uint64_t lifecycle_control_gap_duration_ns{};
};

struct NativeSpectrumReplayEntry {
    std::uint64_t ordinal{};
    std::uint64_t offset{};
    std::uint64_t record_bytes{};
    std::uint64_t frame_sequence{};
    std::int64_t timestamp_ns{};
};

// An immutable, on-demand decoded spectrum record.  This deliberately has no
// raw I/Q payload; canonical I/Q reprocess is an R09 responsibility.
struct NativeSpectrumReplayFrame {
    SourceDescriptor source;
    std::uint64_t frame_sequence{};
    std::uint64_t first_sample_index{};
    std::int64_t timestamp_ns{};
    std::uint64_t config_generation{};
    double center_frequency_hz{};
    double sample_rate_hz{};
    double analog_bandwidth_hz{};
    double fft_bin_width_hz{};
    double enbw_hz{};
    double nominal_rbw_hz{};
    std::uint32_t fft_size{};
    std::uint32_t hop_size{};
    SpectrumUnit unit{SpectrumUnit::DbfsBin};
    CalibrationStatus calibration_status{CalibrationStatus::Uncalibrated};
    std::string calibration_profile_id;
    std::uint32_t quality_flags{};
    std::uint64_t dropped_samples_before{};
    std::uint64_t dropped_iq_blocks_before{};
    std::uint64_t dropped_fft_frames_before{};
    SharedArray<double> frequencies_hz;
    SharedArray<float> values;
};

// A final-manifest-only reader with a fixed-size sparse index.  Open performs
// two streaming scans of the final JSONL index, retaining at most 4096 byte
// offsets; a seek scans no more than one sparse stride and reads one bounded
// self-delimiting spectrum record.  It never opens .part artifacts or writes
// to user data.
class NativeSpectrumRecordingReader final {
public:
    explicit NativeSpectrumRecordingReader(const std::filesystem::path& output_uri);

    NativeSpectrumRecordingReader(const NativeSpectrumRecordingReader&) = delete;
    NativeSpectrumRecordingReader& operator=(const NativeSpectrumRecordingReader&) = delete;

    [[nodiscard]] const NativeRecordingOpenInfo& info() const noexcept;
    [[nodiscard]] std::uint64_t frame_count() const noexcept;
    [[nodiscard]] std::int64_t first_timestamp_ns() const noexcept;
    [[nodiscard]] std::int64_t last_timestamp_ns() const noexcept;
    [[nodiscard]] std::uint64_t source_size_bytes() const noexcept;
    [[nodiscard]] NativeSpectrumReplayEntry entry_at(std::uint64_t ordinal) const;
    [[nodiscard]] NativeSpectrumReplayFrame read_frame(std::uint64_t ordinal) const;

private:
    struct SparseIndexEntry {
        std::uint64_t ordinal{};
        std::uint64_t offset{};
    };

    [[nodiscard]] std::string index_line_at(std::uint64_t ordinal) const;

    std::filesystem::path base_path_;
    std::filesystem::path data_path_;
    std::filesystem::path index_path_;
    NativeRecordingOpenInfo info_{};
    std::uint64_t frame_count_{};
    std::uint64_t sparse_stride_{1U};
    std::int64_t first_timestamp_ns_{};
    std::int64_t last_timestamp_ns_{};
    std::uint64_t source_size_bytes_{};
    std::vector<SparseIndexEntry> sparse_index_;
};

[[nodiscard]] NativeRecordingOpenInfo inspect_final_native_recording(
    const std::filesystem::path& output_uri
);

// Portable segmented raw-I/Q writer.  Segment payloads are canonical sample
// bytes; an append-only JSONL index/gap stream carries provenance and recovery
// boundaries.  Nothing in this class owns a queue, device, Python object or Qt
// resource.  The FixedBand engine controls the bounded recorder queue and
// invokes this writer from its native recorder thread.
class SegmentedIqRecordingWriter final {
public:
    SegmentedIqRecordingWriter(RecordingConfig config, SourceDescriptor source);
    ~SegmentedIqRecordingWriter() noexcept;

    SegmentedIqRecordingWriter(const SegmentedIqRecordingWriter&) = delete;
    SegmentedIqRecordingWriter& operator=(const SegmentedIqRecordingWriter&) = delete;
    SegmentedIqRecordingWriter(SegmentedIqRecordingWriter&&) = delete;
    SegmentedIqRecordingWriter& operator=(SegmentedIqRecordingWriter&&) = delete;

    // Creates only .part artifacts after confirming no final/partial target
    // exists.  A finalized manifest is the reader-visible commit marker.
    void start();
    void write_block(const IqBlock& block);
    // FixedBand calls this after its recorder producer is stopped, before the
    // manifest commit.  It preserves a visible tail-loss summary even when no
    // later admitted block exists from which a sample-index gap can be inferred.
    void set_recorder_queue_loss(std::uint64_t blocks, std::uint64_t samples);
    void finalize();
    void abort(std::string reason) noexcept;

    [[nodiscard]] NativeIqRecordingMetrics metrics() const noexcept;
    [[nodiscard]] std::filesystem::path manifest_path() const;

private:
    [[nodiscard]] std::filesystem::path data_path(std::uint64_t segment) const;
    [[nodiscard]] std::filesystem::path part_path(const std::filesystem::path& path) const;
    void open_segment(const IqBlock& first_block);
    void close_segment();
    void write_gap(
        std::uint64_t first_sample_index,
        std::uint64_t sample_count,
        std::int64_t timestamp_ns,
        std::string_view reason
    );
    void write_manifest(bool completed, std::string_view abort_reason);
    void write_index_line(const std::string& line);
    void require_active() const;

    RecordingConfig config_;
    SourceDescriptor source_;
    std::filesystem::path base_path_;
    std::filesystem::path manifest_path_;
    std::filesystem::path index_path_;
    std::filesystem::path gaps_path_;
    std::ofstream data_;
    std::ofstream index_;
    std::ofstream gaps_;
    bool active_{};
    bool finalized_{};
    bool failed_{};
    std::uint64_t current_segment_{};
    std::uint64_t current_segment_samples_{};
    std::uint64_t current_segment_bytes_{};
    std::uint64_t expected_sample_index_{};
    bool has_expected_sample_index_{};
    SampleFormat sample_format_{SampleFormat::ComplexInt16Le};
    bool has_sample_format_{};
    double sample_rate_hz_{};
    double center_frequency_hz_{};
    std::uint64_t config_generation_{};
    NativeIqRecordingMetrics metrics_{};
    mutable std::mutex mutex_;
};

// Portable writer for the bounded stream of published SpectrumFrame objects.
// Each binary record is length-prefixed and self-delimiting; its JSONL index
// carries the reader-facing wire metadata.  The FixedBand engine owns the
// native queue and invokes this writer only from its dedicated writer thread.
class SpectrumFrameRecordingWriter final {
public:
    SpectrumFrameRecordingWriter(RecordingConfig config, SourceDescriptor source);
    ~SpectrumFrameRecordingWriter() noexcept;

    SpectrumFrameRecordingWriter(const SpectrumFrameRecordingWriter&) = delete;
    SpectrumFrameRecordingWriter& operator=(const SpectrumFrameRecordingWriter&) = delete;
    SpectrumFrameRecordingWriter(SpectrumFrameRecordingWriter&&) = delete;
    SpectrumFrameRecordingWriter& operator=(SpectrumFrameRecordingWriter&&) = delete;

    void start();
    void write_frame(const SpectrumFrame& frame);
    void set_recorder_queue_loss(std::uint64_t frames);
    void finalize();
    void abort(std::string reason) noexcept;

    [[nodiscard]] NativeSpectrumRecordingMetrics metrics() const noexcept;
    [[nodiscard]] std::filesystem::path manifest_path() const;

private:
    [[nodiscard]] std::filesystem::path part_path(const std::filesystem::path& path) const;
    void write_manifest(bool completed, std::string_view abort_reason);
    void write_index_line(const std::string& line);
    void require_active() const;

    RecordingConfig config_;
    SourceDescriptor source_;
    std::filesystem::path base_path_;
    std::filesystem::path manifest_path_;
    std::filesystem::path data_path_;
    std::filesystem::path index_path_;
    std::ofstream data_;
    std::ofstream index_;
    bool active_{};
    bool finalized_{};
    bool failed_{};
    std::uint64_t data_offset_{};
    NativeSpectrumRecordingMetrics metrics_{};
    mutable std::mutex mutex_;
};

[[nodiscard]] NativeRecordingRecoveryScan scan_native_recording_prefix(
    const std::filesystem::path& output_uri
);

}  // namespace sdr_core
