#pragma once

#include "sdr_core/bounded_queue.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/types.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <vector>

namespace sdr_core {

// The two channel configurations deliberately retain independent source and
// calibration provenance.  `validate(DualRxDspConfig)` requires the numerical
// DSP parameters to be equal, because a synchronized publication cannot pair
// frames with different transform timing or grids.
struct DualRxChannelDspConfig {
    SourceDescriptor source;
    DspConfig dsp;
};

struct DualRxDspConfig {
    DualRxChannelDspConfig primary;
    DualRxChannelDspConfig secondary;
    bool dc_removal_block_mean{};
    std::uint32_t output_queue_capacity{4U};
};

void validate(const DualRxDspConfig& value);

// One immutable reduced result for a single common acquisition epoch.  Raw
// I/Q is intentionally absent.  The constituent SpectrumFrames preserve
// separate source/calibration and per-channel DSP-loss identity, while the
// pair fields make a transport/control discontinuity visible as shared.
struct DualRxSpectrumFrame {
    std::uint64_t synchronization_epoch{};
    std::uint64_t first_sample_index{};
    std::int64_t timestamp_ns{};
    std::uint64_t config_generation{};
    std::uint64_t shared_input_gaps_before{};
    SpectrumFrame primary;
    SpectrumFrame secondary;
};

struct DualRxDspMetrics {
    std::uint64_t input_epochs_received{};
    std::uint64_t shared_input_gaps{};
    std::uint64_t pairing_mismatches{};
    std::uint64_t paired_frames_published{};
    std::uint64_t paired_frames_superseded{};
    QueueStats output_queue;
    DspBackendMetrics primary;
    DspBackendMetrics secondary;
};

struct LatestDualRxSpectrumFrameDrain {
    std::optional<DualRxSpectrumFrame> frame;
    std::uint32_t coalesced_frames{};
};

// Synchronous native-only bridge from two already-acquired canonical I/Q
// blocks to bounded paired spectra.  An acquisition adapter (Pluto E1 today,
// a future HackRF/ADRV9009 backend tomorrow) owns transport and calls `push`.
// The public Python binding exposes configuration, metrics and reduced output
// only; it deliberately has no raw-I/Q `push` method.
class DualRxDspPublisher final {
public:
    DualRxDspPublisher() = default;
    ~DualRxDspPublisher() = default;

    DualRxDspPublisher(const DualRxDspPublisher&) = delete;
    DualRxDspPublisher& operator=(const DualRxDspPublisher&) = delete;

    void configure(const DualRxDspConfig& config);
    // Both blocks must describe the same physical capture epoch.  A mismatch
    // resets both DSP histories and becomes an explicit shared gap; neither
    // channel is processed independently past a missing peer.
    void push(const IqBlock& primary, const IqBlock& secondary);
    void mark_shared_gap();
    void reset();

    [[nodiscard]] std::vector<DualRxSpectrumFrame> poll_spectrum_frames(
        std::size_t max_items
    );
    [[nodiscard]] LatestDualRxSpectrumFrameDrain drain_latest_spectrum_frame();
    [[nodiscard]] DualRxDspMetrics metrics() const;

private:
    void reset_for_shared_gap();
    void publish_ready_frames();

    DualRxDspConfig config_{};
    bool configured_{};
    std::uint64_t synchronization_epoch_{};
    std::uint64_t shared_input_gaps_{};
    std::uint64_t pairing_mismatches_{};
    std::uint64_t input_epochs_received_{};
    std::uint64_t paired_frames_published_{};
    std::uint64_t paired_frames_superseded_{};
    std::uint64_t last_source_sequence_{};
    std::uint64_t last_sample_end_{};
    std::uint64_t last_config_generation_{};
    bool input_epoch_valid_{};
    std::unique_ptr<DspBackend> primary_backend_;
    std::unique_ptr<DspBackend> secondary_backend_;
    std::unique_ptr<BoundedQueue<DualRxSpectrumFrame>> output_queue_;
};

}  // namespace sdr_core
