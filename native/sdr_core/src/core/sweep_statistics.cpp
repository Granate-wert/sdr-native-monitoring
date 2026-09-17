#include "sdr_core/sweep_statistics.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>
#include <utility>

namespace sdr_core {
namespace {
constexpr float missing = std::numeric_limits<float>::quiet_NaN();

std::size_t checked_add(std::size_t a, std::size_t b) {
    if (b > std::numeric_limits<std::size_t>::max() - a) {
        throw std::length_error("Sweep statistics payload overflow");
    }
    return a + b;
}

std::size_t checked_mul(std::size_t a, std::size_t b) {
    if (a != 0 && b > std::numeric_limits<std::size_t>::max() / a) {
        throw std::length_error("Sweep statistics payload overflow");
    }
    return a * b;
}

double linear_power(float value) noexcept {
    return std::pow(10.0, static_cast<double>(value) / 10.0);
}
}  // namespace

std::size_t SweepStatisticsAccumulator::required_payload_bytes(
    const SweepStatisticsConfig& config, std::size_t frequency_bins
) {
    if (config.window_passes == 0 || config.power_bins == 0 || frequency_bins == 0 ||
        !std::isfinite(config.power_min_db) || !std::isfinite(config.power_max_db) ||
        config.power_min_db >= config.power_max_db ||
        !std::isfinite(config.power_max_db - config.power_min_db)) {
        throw std::invalid_argument("Invalid Sweep statistics geometry/configuration");
    }
    const auto per_frequency = checked_add(
        checked_add(checked_mul(config.window_passes, sizeof(float)),
                    checked_mul(config.power_bins, 2 * sizeof(std::uint32_t))),
        sizeof(double) * 3 + sizeof(std::uint32_t) * 2 + sizeof(float)
    );
    const auto bytes = checked_add(checked_mul(frequency_bins, per_frequency),
                                   checked_mul(config.window_passes, sizeof(Pass)));
    if (bytes > config.max_payload_bytes) {
        throw std::length_error("Sweep statistics exceeds explicit payload budget");
    }
    return bytes;
}

SweepStatisticsAccumulator::SweepStatisticsAccumulator(
    SweepStatisticsConfig config, SourceDescriptor source, std::uint64_t epoch,
    SpectrumUnit unit, SharedArray<double> frequencies_hz
) : config_(config), source_(std::move(source)), epoch_(epoch), unit_(unit),
    frequencies_(std::move(frequencies_hz)) {
    if (!frequencies_ || source_.source_id.empty()) {
        throw std::invalid_argument("Sweep statistics requires source and frequency grid");
    }
    static_cast<void>(to_wire(unit_));
    const auto n = frequencies_->size();
    static_cast<void>(required_payload_bytes(config_, n));
    for (std::size_t i = 0; i < n; ++i) {
        if (!std::isfinite((*frequencies_)[i]) ||
            (i != 0 && (*frequencies_)[i] <= (*frequencies_)[i - 1])) {
            throw std::invalid_argument("Sweep statistics requires increasing finite frequencies");
        }
    }
    passes_.resize(config_.window_passes);
    values_.resize(n * config_.window_passes, missing);
    histogram_.resize(n * config_.power_bins);
    observations_.resize(n);
    power_sum_.resize(n);
    power_correction_.resize(n);
}

void SweepStatisticsAccumulator::reset() noexcept {
    std::fill(passes_.begin(), passes_.end(), Pass{});
    std::fill(values_.begin(), values_.end(), missing);
    std::fill(histogram_.begin(), histogram_.end(), 0U);
    std::fill(observations_.begin(), observations_.end(), 0U);
    std::fill(power_sum_.begin(), power_sum_.end(), 0.0);
    std::fill(power_correction_.begin(), power_correction_.end(), 0.0);
    next_slot_ = 0;
    retained_ = 0;
    newest_sequence_ = unique_passes_ = updates_ = 0;
}

std::size_t SweepStatisticsAccumulator::power_bin(float value) const noexcept {
    const double fraction = (static_cast<double>(value) - config_.power_min_db) /
                            (config_.power_max_db - config_.power_min_db);
    if (fraction <= 0.0) return 0;
    if (fraction >= 1.0) return config_.power_bins - 1U;
    return std::min(static_cast<std::size_t>(fraction * config_.power_bins),
                    static_cast<std::size_t>(config_.power_bins - 1U));
}

void SweepStatisticsAccumulator::add_power(std::size_t frequency, double value) noexcept {
    // Neumaier compensation preserves a weak observation when a much larger
    // old observation is replaced/evicted. Plain subtract/add loses it.
    auto& sum = power_sum_[frequency];
    const auto total = sum + value;
    power_correction_[frequency] += std::abs(sum) >= std::abs(value)
        ? (sum - total) + value : (value - total) + sum;
    sum = total;
}

bool SweepStatisticsAccumulator::update(const SweepProgressFrame& frame) {
    if (frame.revision == 0) {
        throw std::invalid_argument("Sweep statistics partial revision must be positive");
    }
    return admit(frame.source, frame.epoch, frame.unit, frame.frequencies_hz,
                 frame.values, frame.line_sequence, frame.revision, false);
}

bool SweepStatisticsAccumulator::update(const SweepLineFrame& frame) {
    if (frame.state != SweepLineState::Complete && frame.state != SweepLineState::Gap) {
        throw std::invalid_argument("Sweep statistics requires an explicit terminal state");
    }
    return admit(frame.source, frame.epoch, frame.unit, frame.frequencies_hz,
                 frame.values, frame.line_sequence, 0, true);
}

bool SweepStatisticsAccumulator::admit(
    const SourceDescriptor& source, std::uint64_t epoch, SpectrumUnit unit,
    const SharedArray<double>& frequencies, const SharedArray<float>& values,
    std::uint64_t sequence, std::uint64_t revision, bool terminal
) {
    const auto n = frequencies_->size();
    if (source.source_id != source_.source_id || source.source_type != source_.source_type ||
        epoch != epoch_ || unit != unit_ || !frequencies || !values || values->size() != n ||
        (frequencies != frequencies_ && *frequencies != *frequencies_)) {
        throw std::invalid_argument("Sweep statistics identity/grid mismatch; start a new accumulator");
    }
    auto existing = std::find_if(passes_.begin(), passes_.end(), [sequence](const Pass& pass) {
        return pass.occupied && pass.sequence == sequence;
    });
    const bool replacing = existing != passes_.end();
    if (replacing) {
        if (existing->terminal || (!terminal && revision <= existing->revision)) return false;
    } else if (retained_ != 0 && sequence <= newest_sequence_) {
        return false;  // An evicted or reordered unseen pass must not become newest.
    }
    if (updates_ == std::numeric_limits<std::uint64_t>::max() ||
        (!replacing && unique_passes_ == std::numeric_limits<std::uint64_t>::max())) {
        throw std::overflow_error("Sweep statistics sequence exhausted");
    }
    // Validate the complete replacement before touching any retained data.
    // Leave ample sum headroom for every retained pass and compensation.
    const double max_power = std::numeric_limits<double>::max() /
                             (4.0 * config_.window_passes);
    for (float value : *values) {
        if (std::isnan(value)) continue;
        const double power = linear_power(value);
        if (!std::isfinite(value) || !(power > 0.0) || power > max_power) {
            throw std::invalid_argument("Sweep statistics power is not representable");
        }
    }
    const std::size_t slot = replacing
        ? static_cast<std::size_t>(existing - passes_.begin()) : next_slot_;
    const auto offset = slot * n;
    for (std::size_t f = 0; f < n; ++f) {
        auto& old = values_[offset + f];
        const float value = (*values)[f];
        if (old == value || (std::isnan(old) && std::isnan(value))) continue;
        if (!std::isnan(old)) {
            --histogram_[power_bin(old) * n + f];
            --observations_[f];
            add_power(f, -linear_power(old));
            if (observations_[f] == 0) {
                power_sum_[f] = power_correction_[f] = 0.0;
            }
        }
        if (!std::isnan(value)) {
            ++histogram_[power_bin(value) * n + f];
            ++observations_[f];
            add_power(f, linear_power(value));
        }
        old = value;
    }
    passes_[slot] = Pass{sequence, revision, true, terminal};
    if (!replacing) {
        next_slot_ = (slot + 1) % passes_.size();
        if (retained_ < config_.window_passes) ++retained_;
        newest_sequence_ = sequence;
        ++unique_passes_;
    }
    ++updates_;
    return true;
}

SweepStatisticsSnapshot SweepStatisticsAccumulator::snapshot() const {
    auto averages = std::make_shared<std::vector<float>>(frequencies_->size(), missing);
    for (std::size_t f = 0; f < averages->size(); ++f) {
        if (observations_[f] == 0) continue;
        const double power = (power_sum_[f] + power_correction_[f]) / observations_[f];
        if (!(power > 0.0) || !std::isfinite(power)) {
            throw std::runtime_error("Sweep statistics numerical invariant failed");
        }
        (*averages)[f] = static_cast<float>(10.0 * std::log10(power));
    }
    return SweepStatisticsSnapshot{
        source_.source_type, source_.source_id,
        epoch_, updates_, newest_sequence_, unique_passes_, retained_, unit_,
        config_.power_min_db, config_.power_max_db, config_.power_bins, frequencies_,
        std::move(averages),
        std::make_shared<const std::vector<std::uint32_t>>(histogram_),
        std::make_shared<const std::vector<std::uint32_t>>(observations_)
    };
}

}  // namespace sdr_core
