#include "sdr_core/layer_ready.hpp"
#include "sdr_core/errors.hpp"

#include <algorithm>
#include <limits>

namespace sdr_core {

std::uint64_t LayerReadyJournal::reserved_bytes(const std::size_t capacity) {
    if (capacity > max_capacity) throw ConfigurationError("layer journal exceeds 4096 records");
    // Journal object + fixed ring + one serialized drain payload. Enclosing
    // frame refs and Python obligations require separate owner reservations.
    return sizeof(LayerReadyJournal) + 2U * capacity * sizeof(LayerReadyRef);
}

LayerReadyJournal::LayerReadyJournal(const std::size_t capacity, Clock clock)
    : clock_(clock ? clock : &analytical_ready_clock_ns) {
    static_cast<void>(reserved_bytes(capacity)); // refuse before allocation
    ring_.resize(capacity);
    if (ring_.capacity() > capacity) throw ConfigurationError("layer ring exceeded reservation");
    summary_.producer_instance_id = allocate_native_ready_producer_id();
    summary_.event_capacity = capacity;
}

LayerReadyRef LayerReadyJournal::sweep(const LayerReadyKind kind, const std::uint64_t epoch,
    const std::uint64_t sequence, const std::uint64_t revision) {
    if ((kind != LayerReadyKind::SweepProgress && kind != LayerReadyKind::SweepTerminal) ||
        (kind == LayerReadyKind::SweepProgress ? revision == 0U : revision != 0U)) {
        throw ConfigurationError("invalid Sweep layer creation identity");
    }
    return create({.kind = kind, .sweep_epoch = epoch,
        .line_sequence = sequence, .revision = revision});
}

LayerReadyRef LayerReadyJournal::density(const std::uint64_t generation, const std::uint64_t update,
    const std::uint64_t source_frame, const std::uint64_t accumulation) {
    if (generation == 0U || update == 0U || accumulation == 0U) {
        throw ConfigurationError("density layer requires actual generation/update/accumulation");
    }
    return create({.kind = LayerReadyKind::Density, .config_generation = generation,
        .update_sequence = update, .source_frame_sequence = source_frame,
        .accumulation_sequence = accumulation});
}

LayerReadyRef LayerReadyJournal::create(LayerReadyRef ref) {
    std::lock_guard lock(mutex_);
    if (summary_.created == std::numeric_limits<std::uint64_t>::max()) {
        throw ConfigurationError("layer creation identity space exhausted");
    }
    ref.ready_native_ns = clock_();
    if (clock_seen_ && ref.ready_native_ns < last_clock_) {
        ++summary_.clock_regressions;
        regressed_ = true;
    }
    last_clock_ = ref.ready_native_ns;
    clock_seen_ = true;
    ref.clock_state = regressed_ ? AnalyticalReadyClockState::Regressed
                                : AnalyticalReadyClockState::Monotonic;
    ref.producer_instance_id = summary_.producer_instance_id;
    ref.creation_sequence = ++summary_.created;
    if (pending_ == ring_.size()) {
        if (summary_.events_lost++ == 0U) summary_.first_lost_creation_sequence = ref.creation_sequence;
        summary_.last_lost_creation_sequence = ref.creation_sequence;
    } else {
        ring_[(head_ + pending_) % ring_.size()] = ref;
        ++pending_;
    }
    return ref;
}

LayerReadySummary LayerReadyJournal::summary() const noexcept {
    std::lock_guard lock(mutex_);
    auto result = summary_;
    result.events_pending = pending_;
    return result;
}

LayerReadyDrain LayerReadyJournal::drain(const std::size_t max_items) {
    if (max_items > max_capacity) throw ConfigurationError("layer drain exceeds 4096 records");
    std::lock_guard drain_lock(drain_mutex_);
    LayerReadyDrain result;
    result.creations.reserve(max_items == 0U ? ring_.size() : std::min(max_items, ring_.size()));
    std::lock_guard lock(mutex_);
    const auto count = max_items == 0U ? pending_ : std::min(max_items, pending_);
    for (std::size_t i = 0U; i < count; ++i) {
        result.creations.push_back(ring_[head_]);
        head_ = (head_ + 1U) % ring_.size();
        --pending_;
    }
    summary_.events_drained += count;
    result.summary = summary_;
    result.summary.events_pending = pending_;
    return result;
}

}  // namespace sdr_core
