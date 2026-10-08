#pragma once

#include "sdr_core/configuration.hpp"
#include "sdr_core/types.hpp"

#include <cstdint>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace sdr_core {

inline constexpr std::uint32_t persistence_processing_metadata_version = 1U;
inline constexpr std::size_t persistence_metadata_string_max_bytes = 256U;

// Actual contributing numerical signature, not a hardware readback or an
// admitted owner receipt. UNKNOWN recipe/normalization remain absent. Strings
// are bounded; copied only on identity change or snapshot, not every FFT.
struct PersistenceProcessingMetadataV1 {
    std::optional<DspProcessingRecipeV1> dsp_processing_recipe;
    double center_frequency_hz{};
    double sample_rate_hz{};
    double analog_bandwidth_hz{};
    double fft_bin_width_hz{};
    double enbw_hz{};
    double nominal_rbw_hz{};
    std::uint32_t fft_size{};
    std::uint32_t hop_size{};
    WindowType window{WindowType::Hann};
    DetectorType detector{DetectorType::Sample};
    PrecisionMode precision_mode{PrecisionMode::AccurateF32F64Accum};
    std::uint32_t averaging_frames{};
    CalibrationStatus calibration_status{CalibrationStatus::Uncalibrated};
    std::string calibration_profile_id;
    double estimated_uncertainty_db{std::numeric_limits<double>::quiet_NaN()};
    std::optional<std::string> window_normalization_version;

    [[nodiscard]] static PersistenceProcessingMetadataV1 from_frame(const SpectrumFrame& frame);
    [[nodiscard]] bool matches(const SpectrumFrame& frame) const noexcept;
};

struct PersistenceSnapshot {
    SourceDescriptor source;
    std::uint64_t config_generation{};
    SpectrumUnit unit{SpectrumUnit::DbfsBin};
    std::uint64_t update_sequence{};
    std::int64_t timestamp_ns{};
    std::uint64_t source_frame_sequence{};
    double power_min_db{};
    double power_max_db{};
    std::uint32_t power_bins{};
    std::uint32_t frequency_bins{};
    std::uint64_t processed_frames{};
    bool exponential_decay{};
    // ``density`` is an immutable, renderer-ready row-major image with shape
    // [power_bins, frequency_bins]. Its displayed probability is
    // density * probability_scale; its decayed hit-count estimate is
    // density * count_scale. Keeping scales separate avoids a full-grid decay
    // or normalization on every analytical FFT.
    double probability_scale{1.0};
    double count_scale{1.0};
    SharedArray<double> frequencies_hz;
    std::shared_ptr<const std::vector<float>> density;
    // Latest contributing detector frame, not an aggregate RF-duty assertion.
    QualityFlag quality_flags{QualityFlag::None};
    std::optional<LayerReadyRef> layer_ready;
    // Native contributing-frame evidence only; never reconstructed from a
    // request, quality bit, or the lossy latest spectrum queue.
    std::optional<PersistenceProcessingMetadataV1> processing_metadata;
    std::uint64_t first_sample_index{};
};

// One accumulator signature plus five retained/in-construction snapshot slots.
// Covers inline padding and maximum string payloads; NOT an RSS assertion.
inline constexpr std::uint64_t persistence_processing_reserved_bytes = 8192U;
static_assert(6U * (
    sizeof(std::optional<PersistenceProcessingMetadataV1>) +
    alignof(PersistenceSnapshot) + 2U * persistence_metadata_string_max_bytes +
    sizeof(std::uint64_t)) <= persistence_processing_reserved_bytes);

struct PersistenceProfilingTiming {
    bool available{};
    // Cumulative successful-update work. Snapshot construction is separately
    // timed and does not overlap histogram_update_ns.
    std::uint64_t histogram_update_ns{};
    std::uint64_t snapshot_build_ns{};
    std::uint64_t snapshot_count{};
};

class PersistenceAccumulator final {
public:
    explicit PersistenceAccumulator(PersistenceConfig config,
        std::shared_ptr<LayerReadyJournal> layer_ready = nullptr);

    void configure(PersistenceConfig config);
    void reset();

    [[nodiscard]] std::optional<PersistenceSnapshot> update(const SpectrumFrame& frame);
    [[nodiscard]] const PersistenceConfig& config() const noexcept { return config_; }
    [[nodiscard]] std::uint64_t processed_frames() const noexcept {
        return processed_frames_;
    }
    [[nodiscard]] PersistenceProfilingTiming profiling_timing() const noexcept;

private:
    [[nodiscard]] std::uint32_t bin_for(float value) const noexcept;
    [[nodiscard]] PersistenceSnapshot make_snapshot(const SpectrumFrame& frame) const;

    PersistenceConfig config_{};
    std::shared_ptr<LayerReadyJournal> layer_ready_;
    std::uint64_t accumulation_sequence_{};
    std::optional<SourceDescriptor> source_;
    std::optional<PersistenceProcessingMetadataV1> processing_metadata_;
    std::uint64_t config_generation_{};
    SpectrumUnit unit_{SpectrumUnit::DbfsBin};
    SharedArray<double> frequencies_;
    std::uint32_t frequency_bins_{};
    std::vector<float> density_;
    std::vector<std::uint32_t> exact_ring_;
    std::uint64_t ring_position_{};
    std::uint64_t ring_count_{};
    double raw_weight_{};
    double decay_scale_{1.0};
    // Exponential cells only increase between rare full-grid rebases. Track
    // their maximum in the existing update pass, not a second snapshot scan.
    float max_raw_density_{};
    std::uint64_t processed_frames_{};
    std::uint64_t update_sequence_{};
    std::int64_t last_timestamp_ns_{};
    std::int64_t last_snapshot_timestamp_ns_{};
    std::uint64_t histogram_update_ns_{};
    std::uint64_t snapshot_build_ns_{};
    std::uint64_t snapshot_count_{};
};

}  // namespace sdr_core
