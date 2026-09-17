#pragma once

#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/recording_writer.hpp"

#include <atomic>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>

namespace sdr_core {

// Lifecycle of one offline native I/Q reprocess operation.  The operation is
// deliberately pull-driven: callers choose a bounded number of input blocks
// per process() call, may observe progress between calls, and can request
// cancellation without waiting for a whole capture to be decoded.
enum class NativeIqReprocessState : std::uint8_t {
    Ready,
    Running,
    Completed,
    Cancelled,
    Failed,
};

// Scalar-only progress snapshot.  It never exposes I/Q payloads or retains a
// per-frame result list in native or Python memory.  Any completed output is a
// separate final native spectrum capture whose reader publishes through the
// normal replay path.
struct NativeIqReprocessProgress {
    NativeIqReprocessState state{NativeIqReprocessState::Ready};
    std::uint64_t total_input_blocks{};
    std::uint64_t processed_input_blocks{};
    std::uint64_t processed_input_samples{};
    std::uint64_t written_spectrum_frames{};
    std::uint64_t input_gap_boundaries{};
    std::uint64_t input_gap_samples{};
    std::uint64_t discarded_fft_frames{};
    ComputeBackendKind backend_requested{ComputeBackendKind::Cpu};
    ComputeBackendKind backend_active{ComputeBackendKind::Cpu};
    std::string output_uri;
    std::string message;
};

// Final-manifest-only, bounded native I/Q reprocess.  The reader has no Qt or
// Python dependency and does not make the raw I/Q capture observable across
// the binding.  It feeds the existing replaceable CPU/CUDA DspBackend contract
// and commits only a fully completed native SpectrumFrame recording.
class NativeIqRecordingReprocessor final {
public:
    NativeIqRecordingReprocessor(
        std::filesystem::path input_uri,
        std::filesystem::path output_uri,
        DspConfig dsp,
        DspBackendSelectionOptions selection,
        std::uint32_t max_samples_per_push = 262144U
    );
    ~NativeIqRecordingReprocessor() noexcept;

    NativeIqRecordingReprocessor(const NativeIqRecordingReprocessor&) = delete;
    NativeIqRecordingReprocessor& operator=(const NativeIqRecordingReprocessor&) = delete;
    NativeIqRecordingReprocessor(NativeIqRecordingReprocessor&&) = delete;
    NativeIqRecordingReprocessor& operator=(NativeIqRecordingReprocessor&&) = delete;

    // Processes at most max_blocks physical I/Q blocks from the append-only
    // index.  true means a terminal state (completed, cancelled or failed).
    // A zero max_blocks is rejected rather than becoming an accidental
    // unbounded call from a UI or worker loop.
    [[nodiscard]] bool process(std::uint32_t max_blocks = 8U);
    void request_cancel() noexcept;
    [[nodiscard]] NativeIqReprocessProgress progress() const;

private:
    struct CaptureMetadata {
        bool valid{};
        double center_frequency_hz{};
        double sample_rate_hz{};
        std::uint64_t config_generation{};
    };

    void process_block(const std::string& index_line);
    void flush_backend_output(bool flush_partial_batch);
    void finish_completed();
    void finish_cancelled() noexcept;
    void fail(std::string message) noexcept;

    std::filesystem::path input_base_path_;
    std::filesystem::path output_base_path_;
    DspConfig dsp_;
    DspBackendSelectionOptions selection_;
    std::uint32_t max_samples_per_push_{};
    std::ifstream index_;
    CaptureMetadata capture_{};
    std::uint64_t expected_next_sample_index_{};
    bool has_expected_next_sample_index_{};
    SourceDescriptor source_{};
    std::unique_ptr<DspBackend> backend_;
    std::unique_ptr<SpectrumFrameRecordingWriter> writer_;
    std::atomic_bool cancel_requested_{false};
    NativeIqReprocessProgress progress_{};
};

}  // namespace sdr_core
