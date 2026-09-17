#pragma once

#include "sdr_core/bounded_queue.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/types.hpp"
#include "sdr_hackrf/hackrf_rx_ingress.hpp"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <vector>

namespace sdr_hackrf {

inline constexpr std::uint32_t hackrf_fixed_band_max_dsp_output_capacity = 4096U;
// A larger opt-in relay bounds a deliberately slow presentation consumer
// without moving the default (four frames) or making the queue unbounded.
inline constexpr std::uint32_t hackrf_fixed_band_max_presentation_capacity = 256U;
// This final, reduced-frame-only HackRF boundary deliberately preserves the
// freshest bounded window.  The legacy LatestWins policy remains unchanged
// for existing producer/consumer pipelines elsewhere in the core.
inline constexpr auto hackrf_fixed_band_presentation_overflow_policy =
    sdr_core::OverflowPolicy::DropOldest;

struct HackrfFixedBandDspConfig {
    sdr_core::DspConfig dsp;
    sdr_core::DcRemovalMode dc_removal{sdr_core::DcRemovalMode::Off};
    sdr_core::SourceDescriptor source;
    std::uint32_t dsp_output_capacity{256U};
    std::uint32_t presentation_capacity{4U};
};

struct HackrfFixedBandDspMetrics {
    std::uint64_t iq_blocks_processed{};
    std::uint64_t iq_samples_processed{};
    std::uint64_t source_sequence_discontinuities{};
    std::uint64_t source_sample_index_discontinuities{};
    std::uint64_t source_blocks_missing{};
    std::uint64_t source_samples_missing{};
    std::uint64_t source_timestamp_regressions{};
    std::uint64_t source_estimated_timestamp_blocks{};
    sdr_core::DspBackendMetrics dsp;
    // These are delivery-only counters for the final bounded fresh-window
    // presentation queue.  They never describe an input or analytical FFT
    // loss and must not be folded into SpectrumFrame::dropped_fft_frames_before.
    std::uint64_t presentation_frames_superseded{};
    std::uint64_t presentation_frames_abandoned{};
    sdr_core::QueueStats presentation;
};

// A scalar-only assessment of the two intentionally independent boundaries
// below HackRF CI8 acquisition.  `analytical_pipeline_clean` says that every
// admitted cursor reached the CPU FFT without a source-cursor or CPU-DSP drop;
// it says nothing about device/USB continuity before admission.  Presentation
// delivery retains a fresh bounded window by design and is reported separately so a UI
// coalescing event can never be relabelled as an analytical FFT loss.
struct HackrfFixedBandDspDeliveryAssessment {
    bool source_cursor_continuity_clean{};
    bool cpu_fft_loss_free{};
    bool analytical_pipeline_clean{};
    bool presentation_delivery_clean{};
    std::uint64_t analytical_fft_frames_computed{};
    std::uint64_t analytical_fft_frames_dropped{};
    std::uint64_t presentation_frames_superseded{};
    std::uint64_t presentation_frames_abandoned{};
    std::uint32_t presentation_capacity{};
    std::uint32_t presentation_high_water{};
};

[[nodiscard]] HackrfFixedBandDspDeliveryAssessment
assess_hackrf_fixed_band_dsp_delivery(const HackrfFixedBandDspMetrics& metrics) noexcept;

// Shared fail-closed validation boundary used by the synchronous adapter and
// higher-level composition before any injected runtime is initialized.
void validate_hackrf_fixed_band_dsp_config(const HackrfFixedBandDspConfig& config);

// Synchronous, runtime-free R11-I bridge from the bounded R11-G lease to the
// canonical CPU DSP. It owns no device and performs no HackRF lifecycle call.
class HackrfFixedBandDsp final {
public:
    explicit HackrfFixedBandDsp(HackrfFixedBandDspConfig config);
    ~HackrfFixedBandDsp();

    HackrfFixedBandDsp(const HackrfFixedBandDsp&) = delete;
    HackrfFixedBandDsp& operator=(const HackrfFixedBandDsp&) = delete;

    void push(HackrfRxLease lease);
    [[nodiscard]] std::vector<sdr_core::SpectrumFrame> poll_spectrum_frames(
        std::size_t max_items = 0U
    );
    [[nodiscard]] HackrfFixedBandDspMetrics metrics() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_hackrf
