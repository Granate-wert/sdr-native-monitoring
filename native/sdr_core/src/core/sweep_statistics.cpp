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
    const SweepStatisticsConfig& config, std::size_t frequency_bins,
    std::size_t retained_snapshot_slots
) {
    if (config.window_passes == 0 || config.power_bins == 0 || frequency_bins == 0 ||
        retained_snapshot_slots == 0 ||
        !std::isfinite(config.power_min_db) || !std::isfinite(config.power_max_db) ||
        config.power_min_db >= config.power_max_db ||
        !std::isfinite(config.power_max_db - config.power_min_db)) {
        throw std::invalid_argument("Invalid Sweep statistics geometry/configuration");
    }
    if (frequency_bins > 2'000'000) throw std::length_error("Sweep statistics frequency bound exceeded");
    const auto columns = config.density_columns == 0 ? frequency_bins
        : std::min<std::size_t>(frequency_bins, config.density_columns);
    const auto per_cell = (frequency_bins + columns - 1) / columns;
    if (checked_mul(per_cell, config.window_passes) > std::numeric_limits<std::uint32_t>::max()) {
        throw std::length_error("Sweep density observation counter would overflow");
    }
    const auto cells = checked_mul(columns, config.power_bins);
    const auto state = checked_add(
        checked_add(checked_mul(frequency_bins, checked_add(
            checked_mul(config.window_passes, sizeof(float)), 3 * sizeof(double) + sizeof(std::uint32_t))),
            checked_mul(config.window_passes, sizeof(Pass))),
        checked_add(checked_mul(cells, sizeof(std::uint32_t)),
            checked_add(checked_mul(columns, sizeof(std::uint32_t)),
                        checked_mul(columns + 1, sizeof(double)))));
    const auto one_snapshot = checked_add(
        checked_mul(frequency_bins, sizeof(float) + sizeof(std::uint32_t)),
        checked_add(checked_mul(cells, sizeof(float) + sizeof(std::uint32_t)),
                    checked_mul(columns, sizeof(std::uint32_t))));
    const auto bytes = checked_add(state, checked_mul(retained_snapshot_slots, one_snapshot));
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
    static_cast<void>(to_wire(source_.source_type));
    const auto n = frequencies_->size();
    static_cast<void>(required_payload_bytes(config_, n));
    for (std::size_t i = 0; i < n; ++i) {
        if (!std::isfinite((*frequencies_)[i]) ||
            (i != 0 && (*frequencies_)[i] <= (*frequencies_)[i - 1])) {
            throw std::invalid_argument("Sweep statistics requires increasing finite frequencies");
        }
    }
    const auto columns = config_.density_columns == 0 ? n : std::min<std::size_t>(n, config_.density_columns);
    const double spacing = n > 1 ? (frequencies_->back() - frequencies_->front()) /
        static_cast<double>(n - 1) : 1.0;
    for (std::size_t i = 0; i < n; ++i) {
        if (std::abs((*frequencies_)[i] - (frequencies_->front() + spacing * static_cast<double>(i))) >
            std::max(spacing * 1e-7, std::abs(frequencies_->front()) * 2e-15)) {
            throw std::invalid_argument("Sweep density requires a regular physical grid");
        }
    }
    passes_.resize(config_.window_passes);
    values_.resize(n * config_.window_passes, missing);
    auto edges = std::make_shared<std::vector<double>>(columns + 1);
    for (std::size_t c = 0; c <= columns; ++c) {
        (*edges)[c] = frequencies_->front() - spacing / 2 + spacing * static_cast<double>(n) *
            static_cast<double>(c) / static_cast<double>(columns);
    }
    density_edges_ = std::move(edges);
    histogram_.resize(columns * config_.power_bins);
    density_observations_.resize(columns);
    observations_.resize(n);
    power_sum_.resize(n);
    power_correction_.resize(n);
}

void SweepStatisticsAccumulator::reset() noexcept {
    std::fill(passes_.begin(), passes_.end(), Pass{});
    std::fill(values_.begin(), values_.end(), missing);
    std::fill(histogram_.begin(), histogram_.end(), 0U);
    std::fill(observations_.begin(), observations_.end(), 0U);
    std::fill(density_observations_.begin(), density_observations_.end(), 0U);
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

std::size_t SweepStatisticsAccumulator::density_column(std::size_t frequency) const noexcept {
    return ((2 * frequency + 1) * density_observations_.size()) / (2 * frequencies_->size());
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
        const auto column = density_column(f);
        const auto columns = density_observations_.size();
        if (old == value || (std::isnan(old) && std::isnan(value))) continue;
        if (!std::isnan(old)) {
            --histogram_[power_bin(old) * columns + column];
            --density_observations_[column];
            --observations_[f];
            add_power(f, -linear_power(old));
            if (observations_[f] == 0) {
                power_sum_[f] = power_correction_[f] = 0.0;
            }
        }
        if (!std::isnan(value)) {
            ++histogram_[power_bin(value) * columns + column];
            ++density_observations_[column];
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
    auto probability = std::make_shared<std::vector<float>>(histogram_.size(), missing);
    const auto columns = density_observations_.size();
    for (std::size_t index = 0; index < histogram_.size(); ++index) {
        const auto count = density_observations_[index % columns];
        if (count != 0) (*probability)[index] = static_cast<float>(
            static_cast<double>(histogram_[index]) / count);
    }
    return SweepStatisticsSnapshot{
        source_.source_type, source_.source_id,
        epoch_, updates_, newest_sequence_, unique_passes_, retained_, unit_,
        config_.power_min_db, config_.power_max_db, config_.power_bins, frequencies_,
        std::move(averages),
        std::make_shared<const std::vector<std::uint32_t>>(histogram_),
        std::make_shared<const std::vector<std::uint32_t>>(observations_),
        density_edges_, std::make_shared<const std::vector<std::uint32_t>>(density_observations_),
        std::move(probability)
    };
}

SweepStatisticsPublisher::SweepStatisticsPublisher(
    SweepStatisticsConfig config, SourceDescriptor source, std::uint64_t epoch,
    SpectrumUnit unit, SharedArray<double> frequencies, double snapshot_rate_hz,
    std::size_t retained_snapshot_slots
) : payload_bytes_([&] {
        if (!std::isfinite(snapshot_rate_hz) || snapshot_rate_hz < 1.0 || snapshot_rate_hz > 60.0) {
            throw std::invalid_argument("Sweep statistics publication rate must be in [1, 60] Hz");
        }
        return SweepStatisticsAccumulator::required_payload_bytes(
            config, frequencies ? frequencies->size() : 0, retained_snapshot_slots);
    }()),
    accumulator_(config, std::move(source), epoch, unit, std::move(frequencies)) {
    period_ns_ = static_cast<std::int64_t>(std::ceil(1e9 / snapshot_rate_hz));
}

void SweepStatisticsPublisher::refresh(std::int64_t steady_ns, bool force) {
    if (steady_ns < 0 || (latest_ && steady_ns < last_snapshot_ns_)) {
        throw std::invalid_argument("Sweep statistics publication clock regressed");
    }
    if (force || !latest_ || steady_ns - last_snapshot_ns_ >= period_ns_) {
        latest_ = std::make_shared<const SweepStatisticsSnapshot>(accumulator_.snapshot());
        last_snapshot_ns_ = steady_ns;
    }
}

void SweepStatisticsPublisher::consume(SweepProgressFrame& frame, std::int64_t steady_ns) {
    std::lock_guard lock(mutex_);
    if (steady_ns < 0 || (latest_ && steady_ns < last_snapshot_ns_)) {
        throw std::invalid_argument("Sweep statistics publication clock regressed");
    }
    if (!accumulator_.update(frame)) throw std::invalid_argument("Duplicate/stale Sweep statistics input");
    newest_sequence_ = std::max(newest_sequence_, frame.line_sequence);
    refresh(steady_ns, false);
    frame.statistics = latest_;
}

void SweepStatisticsPublisher::consume(SweepLineFrame& frame, std::int64_t steady_ns, bool force_snapshot) {
    std::lock_guard lock(mutex_);
    if (steady_ns < 0 || (latest_ && steady_ns < last_snapshot_ns_)) {
        throw std::invalid_argument("Sweep statistics publication clock regressed");
    }
    if (!accumulator_.update(frame)) throw std::invalid_argument("Duplicate/stale Sweep statistics input");
    newest_sequence_ = std::max(newest_sequence_, frame.line_sequence);
    refresh(steady_ns, force_snapshot);
    frame.statistics = latest_;
}

std::uint64_t SweepStatisticsPublisher::newest_sequence() const {
    std::lock_guard lock(mutex_);
    return newest_sequence_;
}

}  // namespace sdr_core
