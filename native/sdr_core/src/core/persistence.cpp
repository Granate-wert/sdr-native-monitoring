#include "sdr_core/persistence.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

namespace sdr_core {

namespace {
constexpr std::uint32_t invalid_bin = std::numeric_limits<std::uint32_t>::max();
constexpr double decay_rebase_threshold = 1.0e-6;
}

PersistenceAccumulator::PersistenceAccumulator(PersistenceConfig config) {
    configure(config);
}

void PersistenceAccumulator::configure(PersistenceConfig config) {
    validate(config);
    config_ = config;
    reset();
}

void PersistenceAccumulator::reset() {
    source_.reset();
    frequencies_.reset();
    frequency_bins_ = 0U;
    density_.clear();
    exact_ring_.clear();
    ring_position_ = 0U;
    ring_count_ = 0U;
    raw_weight_ = 0.0;
    decay_scale_ = 1.0;
    processed_frames_ = 0U;
    update_sequence_ = 0U;
    last_timestamp_ns_ = 0;
    last_snapshot_timestamp_ns_ = 0;
}

std::uint32_t PersistenceAccumulator::bin_for(const float value) const noexcept {
    if (!std::isfinite(value)) {
        return invalid_bin;
    }
    const double span = config_.power_max_db - config_.power_min_db;
    const double normalized =
        (static_cast<double>(value) - config_.power_min_db) / span;
    const auto raw = static_cast<std::int64_t>(
        std::floor(normalized * static_cast<double>(config_.power_bins))
    );
    return static_cast<std::uint32_t>(std::clamp<std::int64_t>(
        raw,
        0,
        static_cast<std::int64_t>(config_.power_bins) - 1
    ));
}

std::optional<PersistenceSnapshot> PersistenceAccumulator::update(
    const SpectrumFrame& frame
) {
    if (!config_.enabled || config_.mode == PersistenceMode::Disabled ||
        !frame.values || !frame.frequencies_hz) {
        return std::nullopt;
    }
    const auto bins = static_cast<std::uint32_t>(frame.values->size());
    if (bins == 0U || frame.frequencies_hz->size() != bins) {
        return std::nullopt;
    }
    const bool changed_identity = source_.has_value() && (
        source_->source_id != frame.source.source_id ||
        source_->source_type != frame.source.source_type ||
        config_generation_ != frame.config_generation || unit_ != frame.unit ||
        !frequencies_ || *frequencies_ != *frame.frequencies_hz
    );
    if (changed_identity) {
        reset();
    }
    source_ = frame.source;
    config_generation_ = frame.config_generation;
    unit_ = frame.unit;
    frequencies_ = frame.frequencies_hz;
    if (frequency_bins_ != bins) {
        frequency_bins_ = bins;
        density_.assign(
            static_cast<std::size_t>(config_.power_bins) * frequency_bins_,
            0.0F
        );
        exact_ring_.assign(
            config_.mode == PersistenceMode::RollingExact
                ? static_cast<std::size_t>(config_.window_frames) * frequency_bins_
                : 0U,
            invalid_bin
        );
        ring_position_ = 0U;
        ring_count_ = 0U;
        raw_weight_ = 0.0;
        decay_scale_ = 1.0;
        processed_frames_ = 0U;
        update_sequence_ = 0U;
        last_timestamp_ns_ = 0;
        last_snapshot_timestamp_ns_ = 0;
    }

    if (config_.mode == PersistenceMode::ExponentialDecay &&
        last_timestamp_ns_ > 0 && frame.timestamp_ns > last_timestamp_ns_) {
        const double elapsed_s =
            static_cast<double>(frame.timestamp_ns - last_timestamp_ns_) / 1.0e9;
        const double factor = std::exp(
            -std::log(2.0) * elapsed_s / config_.half_life_seconds
        );
        decay_scale_ *= factor;
        // Rebase only after many half lives. Normal updates are O(frequency
        // bins), not O(power bins * frequency bins); this rare full pass
        // keeps the raw increment finite and preserves the visible epoch.
        if (!std::isfinite(decay_scale_) || decay_scale_ < decay_rebase_threshold) {
            for (float& value : density_) {
                value = static_cast<float>(static_cast<double>(value) * decay_scale_);
            }
            raw_weight_ *= decay_scale_;
            decay_scale_ = 1.0;
        }
    }

    const float increment = config_.mode == PersistenceMode::ExponentialDecay
                                ? static_cast<float>(1.0 / decay_scale_)
                                : 1.0F;

    if (config_.mode == PersistenceMode::RollingExact) {
        const auto frame_offset =
            static_cast<std::size_t>(ring_position_) * frequency_bins_;
        if (ring_count_ == config_.window_frames) {
            const auto* old = exact_ring_.data() + frame_offset;
            for (std::uint32_t column = 0U; column < frequency_bins_; ++column) {
                const auto row = old[column];
                if (row != invalid_bin) {
                    auto& cell = density_[
                        static_cast<std::size_t>(row) * frequency_bins_ + column
                    ];
                    cell = std::max(0.0F, cell - 1.0F);
                }
            }
        }
        for (std::uint32_t column = 0U; column < frequency_bins_; ++column) {
            const auto row = bin_for((*frame.values)[column]);
            exact_ring_[frame_offset + column] = row;
            if (row != invalid_bin) {
                density_[
                    static_cast<std::size_t>(row) * frequency_bins_ + column
                ] += increment;
            }
        }
        ring_position_ = (ring_position_ + 1U) % config_.window_frames;
        ring_count_ = std::min<std::uint64_t>(
            ring_count_ + 1U, config_.window_frames
        );
        raw_weight_ = static_cast<double>(ring_count_);
    } else {
        for (std::uint32_t column = 0U; column < frequency_bins_; ++column) {
            const auto row = bin_for((*frame.values)[column]);
            if (row != invalid_bin) {
                density_[
                    static_cast<std::size_t>(row) * frequency_bins_ + column
                ] += increment;
            }
        }
        raw_weight_ += static_cast<double>(increment);
    }

    ++processed_frames_;
    ++update_sequence_;
    last_timestamp_ns_ = frame.timestamp_ns;
    const auto period_ns = static_cast<std::int64_t>(
        std::max(1.0, 1.0e9 / config_.snapshot_rate_hz)
    );
    if (last_snapshot_timestamp_ns_ != 0 &&
        frame.timestamp_ns > 0 &&
        frame.timestamp_ns < last_snapshot_timestamp_ns_ + period_ns) {
        return std::nullopt;
    }
    last_snapshot_timestamp_ns_ = frame.timestamp_ns;
    return make_snapshot(frame);
}

PersistenceSnapshot PersistenceAccumulator::make_snapshot(
    const SpectrumFrame& frame
) const {
    // Snapshot copy is bounded and preserves immutable latest-wins ownership.
    // It intentionally happens only at snapshot_rate_hz, never once per FFT.
    auto density = std::make_shared<std::vector<float>>(density_);

    PersistenceSnapshot result;
    result.source = frame.source;
    result.config_generation = frame.config_generation;
    result.unit = frame.unit;
    result.update_sequence = update_sequence_;
    result.timestamp_ns = frame.timestamp_ns;
    result.source_frame_sequence = frame.frame_sequence;
    result.power_min_db = config_.power_min_db;
    result.power_max_db = config_.power_max_db;
    result.power_bins = config_.power_bins;
    result.frequency_bins = frequency_bins_;
    result.processed_frames = processed_frames_;
    result.exponential_decay =
        config_.mode == PersistenceMode::ExponentialDecay;
    result.probability_scale = raw_weight_ > 0.0 ? 1.0 / raw_weight_ : 0.0;
    result.count_scale = result.exponential_decay ? decay_scale_ : 1.0;
    // Frequency axes are immutable SpectrumFrame data and remain valid through
    // the shared owner, so publication need not copy 4096 doubles per update.
    result.frequencies_hz = frame.frequencies_hz;
    result.density = std::move(density);
    return result;
}

}  // namespace sdr_core
