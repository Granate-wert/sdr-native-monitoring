#include "sdr_core/persistence.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

namespace sdr_core {

namespace {
constexpr std::uint32_t invalid_bin = std::numeric_limits<std::uint32_t>::max();
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
    frequency_bins_ = 0U;
    density_.clear();
    exact_ring_.clear();
    ring_position_ = 0U;
    ring_count_ = 0U;
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
    if (frequency_bins_ != bins) {
        frequency_bins_ = bins;
        density_.assign(
            static_cast<std::size_t>(config_.power_bins) * frequency_bins_,
            0.0
        );
        exact_ring_.assign(
            config_.mode == PersistenceMode::RollingExact
                ? static_cast<std::size_t>(config_.window_frames) * frequency_bins_
                : 0U,
            invalid_bin
        );
        ring_position_ = 0U;
        ring_count_ = 0U;
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
        for (double& value : density_) {
            value *= factor;
        }
    }

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
                    cell = std::max(0.0, cell - 1.0);
                }
            }
        }
        for (std::uint32_t column = 0U; column < frequency_bins_; ++column) {
            const auto row = bin_for((*frame.values)[column]);
            exact_ring_[frame_offset + column] = row;
            if (row != invalid_bin) {
                density_[
                    static_cast<std::size_t>(row) * frequency_bins_ + column
                ] += 1.0;
            }
        }
        ring_position_ = (ring_position_ + 1U) % config_.window_frames;
        ring_count_ = std::min<std::uint64_t>(
            ring_count_ + 1U, config_.window_frames
        );
    } else {
        for (std::uint32_t column = 0U; column < frequency_bins_; ++column) {
            const auto row = bin_for((*frame.values)[column]);
            if (row != invalid_bin) {
                density_[
                    static_cast<std::size_t>(row) * frequency_bins_ + column
                ] += 1.0;
            }
        }
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
    auto frequencies = std::make_shared<std::vector<double>>(
        *frame.frequencies_hz
    );
    auto density = std::make_shared<std::vector<float>>(density_.size());
    std::transform(
        density_.begin(),
        density_.end(),
        density->begin(),
        [](const double value) { return static_cast<float>(value); }
    );

    PersistenceSnapshot result;
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
    result.frequencies_hz = std::move(frequencies);
    result.density = std::move(density);
    return result;
}

}  // namespace sdr_core
