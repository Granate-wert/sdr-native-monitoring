#pragma once

#include "sdr_core/errors.hpp"
#include "sdr_core/types.hpp"

#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <mutex>
#include <type_traits>
#include <utility>

namespace sdr_core {

struct QueueStats {
    std::uint32_t capacity{};
    std::uint32_t depth{};
    std::uint32_t high_water{};
    std::uint64_t pushed{};
    std::uint64_t popped{};
    std::uint64_t dropped{};
    std::uint64_t abandoned{};
    bool stop_requested{};
};

enum class PushResult : std::uint8_t {
    Pushed,   // accepted into the queue
    Full,     // try_push on a full queue with OverflowPolicy::Block
    Dropped,  // incoming item dropped (OverflowPolicy::DropNewest)
    Evicted,  // a queued item was dropped to make room (DropOldest / LatestWins)
    Stopped,  // stop was requested; item not accepted
};

enum class PopResult : std::uint8_t {
    Popped,
    Stopped,  // stop was requested and no pending items remain
};

// Bounded multi-producer/multi-consumer queue with explicit overflow policy.
// Capacity is fixed at construction; the queue never reallocates storage
// beyond the configured bound. All counters are exact and observed under the
// same mutex as the queue itself, so stats() is a consistent snapshot.
//
// Shutdown semantics are explicit:
// - request_stop() wakes every blocked producer/consumer; push() then fails
//   with Stopped, while pop()/try_pop() keep draining the remaining pending
//   items (Popped) until the queue is empty, then return Stopped;
// - pending items are never silently discarded; the owner either drains them
//   or discards them through abandon(), which counts them.
// Threads waiting in push()/pop() must be released with request_stop() (and
// joined) before the queue is destroyed.
template <typename T>
class BoundedQueue final {
public:
    BoundedQueue(const std::uint32_t capacity, const OverflowPolicy policy)
        : capacity_(capacity), policy_(policy) {
        if (capacity_ == 0U) {
            throw ConfigurationError("queue capacity must be positive");
        }
        static_cast<void>(to_wire(policy_));
    }

    BoundedQueue(const BoundedQueue&) = delete;
    BoundedQueue& operator=(const BoundedQueue&) = delete;

    // Blocking push. With OverflowPolicy::Block it waits for free space until
    // request_stop(); drop policies never block.
    PushResult push(T value) {
        std::unique_lock lock(mutex_);
        if (policy_ == OverflowPolicy::Block) {
            not_full_.wait(lock, [this] {
                return items_.size() < capacity_ || stop_;
            });
        }
        return push_locked(
            std::move(value),
            lock,
            [](T&, std::uint64_t) noexcept {},
            [](const T&) noexcept {}
        );
    }

    // Blocking push with exact eviction accounting. The callback runs under
    // the queue mutex only when a queued item is evicted by DropOldest or
    // LatestWins; DropNewest rejects the incoming item, which remains known
    // to the caller.
    template <typename OnEvicted>
    PushResult push_with_eviction(T value, OnEvicted&& on_evicted) {
        static_assert(
            std::is_nothrow_invocable_v<OnEvicted, const T&>,
            "queue eviction callback must be noexcept"
        );
        std::unique_lock lock(mutex_);
        if (policy_ == OverflowPolicy::Block) {
            not_full_.wait(lock, [this] {
                return items_.size() < capacity_ || stop_;
            });
        }
        return push_locked(
            std::move(value),
            lock,
            [](T&, std::uint64_t) noexcept {},
            std::forward<OnEvicted>(on_evicted)
        );
    }

    // Never blocks. With OverflowPolicy::Block a full queue returns Full and
    // the item is rejected without being counted as dropped (the caller owns
    // the backpressure decision and the accounting).
    PushResult try_push(T value) {
        std::unique_lock lock(mutex_);
        if (policy_ == OverflowPolicy::Block && items_.size() >= capacity_) {
            return stop_ ? PushResult::Stopped : PushResult::Full;
        }
        return push_locked(
            std::move(value),
            lock,
            [](T&, std::uint64_t) noexcept {},
            [](const T&) noexcept {}
        );
    }

    // Atomically annotates an accepted value after eviction accounting and
    // immediately before it becomes visible to consumers.
    template <typename Prepare>
    PushResult try_push_prepared(T value, Prepare&& prepare) {
        static_assert(
            std::is_nothrow_invocable_v<Prepare, T&, std::uint64_t>,
            "queue prepare callback must be noexcept"
        );
        std::unique_lock lock(mutex_);
        if (policy_ == OverflowPolicy::Block && items_.size() >= capacity_) {
            return stop_ ? PushResult::Stopped : PushResult::Full;
        }
        return push_locked(
            std::move(value),
            lock,
            std::forward<Prepare>(prepare),
            [](const T&) noexcept {}
        );
    }

    // Blocking pop. Waits for an item until request_stop().
    PopResult pop(T& out) {
        std::unique_lock lock(mutex_);
        not_empty_.wait(lock, [this] {
            return !items_.empty() || stop_;
        });
        if (items_.empty()) {
            return PopResult::Stopped;
        }
        out = std::move(items_.front());
        items_.pop_front();
        ++popped_;
        lock.unlock();
        not_full_.notify_one();
        return PopResult::Popped;
    }

    bool try_pop(T& out) {
        std::unique_lock lock(mutex_);
        if (items_.empty()) {
            return false;
        }
        out = std::move(items_.front());
        items_.pop_front();
        ++popped_;
        lock.unlock();
        not_full_.notify_one();
        return true;
    }

    // Wakes every blocked producer and consumer. Pending items stay queued
    // for explicit drain or abandon(); nothing is silently lost.
    void request_stop() noexcept {
        {
            std::lock_guard lock(mutex_);
            stop_ = true;
        }
        not_empty_.notify_all();
        not_full_.notify_all();
    }

    // Visits and discards pending items after stop, counting them as
    // abandoned. The noexcept visitor runs under the queue mutex and may only
    // perform bounded bookkeeping.
    template <typename Visitor>
    std::uint64_t abandon_with(Visitor&& visitor) {
        static_assert(
            std::is_nothrow_invocable_v<Visitor, const T&>,
            "queue abandon callback must be noexcept"
        );
        std::uint64_t discarded = 0U;
        {
            std::lock_guard lock(mutex_);
            discarded = static_cast<std::uint64_t>(items_.size());
            for (const auto& item : items_) {
                std::forward<Visitor>(visitor)(item);
            }
            items_.clear();
            abandoned_ += discarded;
        }
        not_full_.notify_all();
        return discarded;
    }

    // Discards pending items after stop and counts them as abandoned.
    // Returns the number of discarded items.
    std::uint64_t abandon() {
        return abandon_with([](const T&) noexcept {});
    }

    [[nodiscard]] QueueStats stats() const {
        std::lock_guard lock(mutex_);
        QueueStats result;
        result.capacity = capacity_;
        result.depth = static_cast<std::uint32_t>(items_.size());
        result.high_water = high_water_;
        result.pushed = pushed_;
        result.popped = popped_;
        result.dropped = dropped_;
        result.abandoned = abandoned_;
        result.stop_requested = stop_;
        return result;
    }

    [[nodiscard]] std::uint32_t depth() const {
        std::lock_guard lock(mutex_);
        return static_cast<std::uint32_t>(items_.size());
    }

    [[nodiscard]] std::uint32_t capacity() const noexcept {
        return capacity_;
    }

    [[nodiscard]] bool stop_requested() const {
        std::lock_guard lock(mutex_);
        return stop_;
    }

private:
    template <typename Prepare, typename OnEvicted>
    PushResult push_locked(
        T value,
        std::unique_lock<std::mutex>& lock,
        Prepare&& prepare,
        OnEvicted&& on_evicted
    ) {
        if (stop_) {
            return PushResult::Stopped;
        }
        PushResult result = PushResult::Pushed;
        if (items_.size() >= capacity_) {
            switch (policy_) {
            case OverflowPolicy::Block:
                // Unreachable: blocking push waited for space, try_push
                // returned Full before calling us.
                return PushResult::Full;
            case OverflowPolicy::DropNewest:
                ++dropped_;
                return PushResult::Dropped;
            case OverflowPolicy::DropOldest:
                std::forward<OnEvicted>(on_evicted)(items_.front());
                items_.pop_front();
                ++dropped_;
                result = PushResult::Evicted;
                break;
            case OverflowPolicy::LatestWins:
                // Replace the newest pending item: the consumer never sees
                // superseded snapshots.
                std::forward<OnEvicted>(on_evicted)(items_.back());
                items_.pop_back();
                ++dropped_;
                result = PushResult::Evicted;
                break;
            }
        }
        std::forward<Prepare>(prepare)(value, dropped_);
        items_.push_back(std::move(value));
        ++pushed_;
        if (items_.size() > high_water_) {
            high_water_ = static_cast<std::uint32_t>(items_.size());
        }
        lock.unlock();
        not_empty_.notify_one();
        return result;
    }

    const std::uint32_t capacity_;
    const OverflowPolicy policy_;
    mutable std::mutex mutex_;
    std::condition_variable not_empty_;
    std::condition_variable not_full_;
    std::deque<T> items_;
    std::uint32_t high_water_{};
    std::uint64_t pushed_{};
    std::uint64_t popped_{};
    std::uint64_t dropped_{};
    std::uint64_t abandoned_{};
    bool stop_{};
};

}  // namespace sdr_core
