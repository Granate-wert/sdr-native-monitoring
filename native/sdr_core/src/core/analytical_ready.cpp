#include "sdr_core/analytical_ready.hpp"
#include "sdr_core/errors.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <limits>

namespace sdr_core {
namespace {
std::atomic<std::uint64_t> next_producer_id{1U};
std::uint64_t allocate_producer_id() {
    auto candidate = next_producer_id.load(std::memory_order_relaxed);
    for (;;) {
        if (candidate == std::numeric_limits<std::uint64_t>::max()) {
            throw ConfigurationError("analytical producer identity space exhausted");
        }
        if (next_producer_id.compare_exchange_weak(
                candidate, candidate + 1U, std::memory_order_relaxed)) {
            return candidate;
        }
    }
}
}  // namespace

std::int64_t analytical_ready_clock_ns() noexcept {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

AnalyticalReadyJournal::AnalyticalReadyJournal(const std::size_t capacity, Clock clock)
    : clock_(clock ? clock : &analytical_ready_clock_ns) {
    if (capacity > max_event_capacity) {
        throw ConfigurationError("analytical event capacity exceeds 4096 scalar records");
    }
    // Bound checked BEFORE allocation. Product owner admission must charge any
    // enabled ring explicitly; existing owners keep capacity zero until wired.
    events_.resize(capacity);
    if (events_.capacity() > capacity) {
        throw ConfigurationError("analytical event allocator exceeded admitted payload reservation");
    }
    summary_.supported = true;
    summary_.producer_instance_id = allocate_producer_id();
    summary_.event_capacity = capacity;
    summary_.event_storage_bytes = events_.capacity() * sizeof(AnalyticalReadyEvent);
}

AnalyticalReadyRef AnalyticalReadyJournal::offer(const std::uint64_t generation) {
    std::lock_guard lock(mutex_);
    // An offer generates at most one later retirement; reserve counter space
    // before publication rather than wrap IDs or cumulative event accounting.
    if (summary_.offered >= std::numeric_limits<std::uint64_t>::max() / 2U) {
        throw ConfigurationError("analytical offer identity space exhausted");
    }
    const auto ready = clock_();
    if (clock_seen_ && ready < last_clock_ns_) {
        ++summary_.clock_regressions;
        clock_regressed_ = true;
    }
    last_clock_ns_ = ready;
    clock_seen_ = true;
    AnalyticalReadyRef ref{summary_.producer_instance_id, ++summary_.offered,
                           generation, ready, AnalyticalReadyClock::NativeSteady,
                           clock_regressed_ ? AnalyticalReadyClockState::Regressed
                                            : AnalyticalReadyClockState::Monotonic};
    ++summary_.outstanding;
    append(ref, AnalyticalReadyEventKind::Offered);
    return ref;
}

void AnalyticalReadyJournal::retire(
    const AnalyticalReadyRef& ref, const AnalyticalReadyEventKind kind
) {
    std::lock_guard lock(mutex_);
    if (ref.producer_instance_id != summary_.producer_instance_id ||
        ref.offer_sequence != summary_.handed_off + summary_.producer_superseded +
                              summary_.producer_cancelled + 1U ||
        summary_.outstanding == 0U) {
        throw ConfigurationError("analytical retirement must match the oldest pending producer offer");
    }
    switch (kind) {
    case AnalyticalReadyEventKind::HandedOff: ++summary_.handed_off; break;
    case AnalyticalReadyEventKind::ProducerSuperseded: ++summary_.producer_superseded; break;
    case AnalyticalReadyEventKind::ProducerCancelled: ++summary_.producer_cancelled; break;
    default: throw ConfigurationError("analytical offer is not a retirement disposition");
    }
    --summary_.outstanding;
    append(ref, kind);
}

void AnalyticalReadyJournal::append(
    const AnalyticalReadyRef& ref, const AnalyticalReadyEventKind kind
) noexcept {
    const auto sequence = ++summary_.events_generated;
    if (pending_ == events_.size()) {
        if (summary_.events_lost++ == 0U) {
            summary_.first_lost_event_sequence = sequence;
        }
        // Bounds, NOT a claim that every intervening event was lost. Drains can
        // split loss intervals. events_lost is the exact conservative loss count.
        summary_.last_lost_event_sequence = sequence;
        return;
    }
    events_[(head_ + pending_) % events_.size()] = {sequence, ref, kind};
    ++pending_;
}

AnalyticalReadySummary AnalyticalReadyJournal::summary() const noexcept {
    std::lock_guard lock(mutex_);
    auto result = summary_;
    result.events_pending = pending_;
    return result;
}

std::vector<AnalyticalReadyEvent> AnalyticalReadyJournal::poll_events(std::size_t max_items) {
    return drain(max_items).events;
}

AnalyticalReadyDrain AnalyticalReadyJournal::drain(std::size_t max_items) {
    std::lock_guard drain_lock(drain_mutex_);
    // Reserve outside the critical section so a concurrent owner drain does
    // not allocate while holding the producer's scalar journal lock.
    if (max_items > max_event_capacity) {
        throw ConfigurationError("analytical drain exceeds bounded event capacity");
    }
    AnalyticalReadyDrain drained;
    drained.events.reserve(max_items == 0U ? events_.size() : std::min(max_items, events_.size()));
    std::lock_guard lock(mutex_);
    const auto count = max_items == 0U ? pending_ : std::min(max_items, pending_);
    for (std::size_t index = 0U; index < count; ++index) {
        drained.events.push_back(events_[head_]);
        head_ = (head_ + 1U) % events_.size();
        --pending_;
    }
    summary_.events_drained += count;
    drained.summary = summary_;
    drained.summary.events_pending = pending_;
    return drained;
}

std::uint64_t analytical_ready_reserved_bytes(const std::uint32_t capacity) {
    if (capacity > AnalyticalReadyJournal::max_event_capacity) {
        throw ConfigurationError("analytical event capacity exceeds 4096 scalar records");
    }
    // Ring plus one fully bounded native drain vector. Not Python-object/RSS
    // or downstream obligation-ledger memory; callers must budget those too.
    return 2U * static_cast<std::uint64_t>(capacity) * sizeof(AnalyticalReadyEvent);
}
}  // namespace sdr_core
