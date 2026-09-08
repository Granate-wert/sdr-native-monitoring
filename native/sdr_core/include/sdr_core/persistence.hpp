#pragma once

#include "sdr_core/configuration.hpp"
#include "sdr_core/types.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <vector>

namespace sdr_core {

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
};

class PersistenceAccumulator final {
public:
    explicit PersistenceAccumulator(PersistenceConfig config);

    void configure(PersistenceConfig config);
    void reset();

    [[nodiscard]] std::optional<PersistenceSnapshot> update(const SpectrumFrame& frame);
    [[nodiscard]] const PersistenceConfig& config() const noexcept { return config_; }
    [[nodiscard]] std::uint64_t processed_frames() const noexcept {
        return processed_frames_;
    }

private:
    [[nodiscard]] std::uint32_t bin_for(float value) const noexcept;
    [[nodiscard]] PersistenceSnapshot make_snapshot(const SpectrumFrame& frame) const;

    PersistenceConfig config_{};
    std::optional<SourceDescriptor> source_;
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
    std::uint64_t processed_frames_{};
    std::uint64_t update_sequence_{};
    std::int64_t last_timestamp_ns_{};
    std::int64_t last_snapshot_timestamp_ns_{};
};

}  // namespace sdr_core
