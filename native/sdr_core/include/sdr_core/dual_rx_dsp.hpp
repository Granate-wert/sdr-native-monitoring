#pragma once

#include "sdr_core/bounded_queue.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/types.hpp"

#include <cstddef>
#include <cstdint>
#include <functional>
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
    // Admission bound for ONE push, not a render-queue size. Internal DSP
    // capacity is derived from this bound and the common hop/batch geometry.
    std::uint32_t max_input_samples_per_push{262'144U};
    DspBackendSelectionOptions backend{.preference = ComputeBackendKind::Cpu};
    std::uint32_t analytical_event_capacity{};
};

void validate(const DualRxDspConfig& value);

// Conservative combined host payload reservation for this synchronous bridge
// only. An acquisition owner must additionally account for its pools, tees,
// recordings and persistence under the SAME aggregate limits. This is not
// an RSS measurement or a bound on vendor GPU/FFT-plan workspace.
struct DualRxDspResourceBudget {
    std::uint32_t analytical_output_capacity{};
    std::uint64_t input_payload_bytes{};
    std::uint64_t dsp_working_bytes{};
    std::uint64_t spectrum_backlog_bytes{};
    std::uint64_t total_bytes{};
};

[[nodiscard]] DualRxDspResourceBudget dual_rx_dsp_resource_budget(const DualRxDspConfig& value);

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
    std::uint64_t paired_frames_formed{};
    std::uint64_t paired_frames_published{};
    std::uint64_t paired_frames_superseded{};
    std::uint64_t paired_frames_abandoned{};
    DualRxDspResourceBudget resource_budget;
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
    // Native owner only: every validated analytical pair is delivered before
    // final latest-wins reduction. Returning false transfers publication to
    // the owner; no raw payload or callback is exposed through Python.
    using AnalyticalConsumer = std::function<bool(
        DualRxSpectrumFrame&, const DspBackendMetrics&, const DspBackendMetrics&)>;
    void set_analytical_consumer(AnalyticalConsumer consumer);
    // Same native worker only; clears owner-side stale histories BEFORE a new
    // synchronization epoch can reach analytical consumers. Not Python-bound.
    void set_shared_gap_consumer(std::function<void()> consumer);
    void flush();
    void mark_shared_gap();
    void reset();

    [[nodiscard]] std::vector<DualRxSpectrumFrame> poll_spectrum_frames(
        std::size_t max_items
    );
    [[nodiscard]] LatestDualRxSpectrumFrameDrain drain_latest_spectrum_frame();
    [[nodiscard]] DualRxDspMetrics metrics() const;
    [[nodiscard]] AnalyticalReadyDrain drain_analytical_ready_events(bool primary, std::size_t max_items);
    // Native owning engine only; SAME per-chain journals, no second queue or callback.
    void enable_owner_presentation() noexcept;
    void record_owner_presentation(const DualRxSpectrumFrame&, OwnerPresentationDisposition) noexcept;

private:
    void reset_for_shared_gap();
    void publish_ready_frames(bool flush_partial_batch = false);

    DualRxDspConfig config_{};
    DualRxDspResourceBudget resource_budget_{};
    bool configured_{};
    std::uint64_t synchronization_epoch_{};
    std::uint64_t shared_input_gaps_{};
    std::uint64_t pairing_mismatches_{};
    std::uint64_t paired_frames_formed_{};
    std::uint64_t input_epochs_received_{};
    std::uint64_t paired_frames_published_{};
    std::uint64_t paired_frames_superseded_{};
    std::uint64_t paired_frames_abandoned_{};
    std::uint64_t last_source_sequence_{};
    std::uint64_t last_sample_end_{};
    std::uint64_t last_config_generation_{};
    double last_sample_rate_hz_{};
    double last_center_frequency_hz_{};
    SampleFormat last_sample_format_{SampleFormat::ComplexInt16Le};
    bool input_epoch_valid_{};
    std::unique_ptr<DspBackend> primary_backend_;
    std::unique_ptr<DspBackend> secondary_backend_;
    std::unique_ptr<BoundedQueue<DualRxSpectrumFrame>> output_queue_;
    AnalyticalConsumer analytical_consumer_;
    std::function<void()> shared_gap_consumer_;
};

}  // namespace sdr_core
