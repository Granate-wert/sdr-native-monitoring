#include "sdr_core/bounded_queue.hpp"
#include "sdr_core/errors.hpp"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

using IntQueue = sdr_core::BoundedQueue<std::uint64_t>;

void test_order_and_capacity() {
    IntQueue queue(4U, sdr_core::OverflowPolicy::Block);
    for (std::uint64_t value = 0U; value < 4U; ++value) {
        expect(queue.push(value) == sdr_core::PushResult::Pushed, "push rejected below capacity");
    }
    expect(queue.try_push(99U) == sdr_core::PushResult::Full, "try_push must report Full");
    std::uint64_t out = 0U;
    for (std::uint64_t expected = 0U; expected < 4U; ++expected) {
        expect(queue.pop(out) == sdr_core::PopResult::Popped, "pop failed");
        expect(out == expected, "FIFO order violated");
    }
    const auto stats = queue.stats();
    expect(stats.capacity == 4U, "capacity mismatch");
    expect(stats.pushed == 4U && stats.popped == 4U, "push/pop counters mismatch");
    expect(stats.high_water == 4U && stats.depth == 0U, "depth/high_water mismatch");
}

void test_zero_capacity_rejected() {
    bool rejected = false;
    try {
        const IntQueue invalid(0U, sdr_core::OverflowPolicy::Block);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "zero capacity must be rejected");
}

void test_drop_newest() {
    IntQueue queue(2U, sdr_core::OverflowPolicy::DropNewest);
    expect(queue.push(1U) == sdr_core::PushResult::Pushed, "initial push failed");
    expect(queue.push(2U) == sdr_core::PushResult::Pushed, "initial push failed");
    expect(queue.push(3U) == sdr_core::PushResult::Dropped, "incoming item must be dropped");
    expect(queue.stats().dropped == 1U, "exact drop counter mismatch");
    std::uint64_t out = 0U;
    expect(queue.pop(out) == sdr_core::PopResult::Popped && out == 1U, "queued order changed");
    expect(queue.pop(out) == sdr_core::PopResult::Popped && out == 2U, "queued order changed");
}

void test_drop_oldest() {
    IntQueue queue(2U, sdr_core::OverflowPolicy::DropOldest);
    expect(queue.push(1U) == sdr_core::PushResult::Pushed, "initial push failed");
    expect(queue.push(2U) == sdr_core::PushResult::Pushed, "initial push failed");
    expect(queue.push(3U) == sdr_core::PushResult::Evicted, "oldest item must be evicted");
    expect(queue.stats().dropped == 1U, "exact drop counter mismatch");
    std::uint64_t out = 0U;
    expect(queue.pop(out) == sdr_core::PopResult::Popped && out == 2U, "oldest was not evicted");
    expect(queue.pop(out) == sdr_core::PopResult::Popped && out == 3U, "order violated");
}

void test_latest_wins() {
    IntQueue queue(2U, sdr_core::OverflowPolicy::LatestWins);
    expect(queue.push(1U) == sdr_core::PushResult::Pushed, "initial push failed");
    expect(queue.push(2U) == sdr_core::PushResult::Pushed, "initial push failed");
    expect(queue.push(3U) == sdr_core::PushResult::Evicted, "newest pending item must be replaced");
    expect(queue.stats().dropped == 1U, "exact drop counter mismatch");
    std::uint64_t out = 0U;
    expect(queue.pop(out) == sdr_core::PopResult::Popped && out == 1U, "oldest item must survive");
    expect(queue.pop(out) == sdr_core::PopResult::Popped && out == 3U, "latest must win");
}

void test_eviction_callback_reports_actual_item() {
    IntQueue oldest(1U, sdr_core::OverflowPolicy::DropOldest);
    expect(oldest.push(11U) == sdr_core::PushResult::Pushed, "oldest seed failed");
    std::uint64_t evicted = 0U;
    expect(
        oldest.push_with_eviction(
            22U,
            [&evicted](const std::uint64_t& value) noexcept { evicted = value; }
        ) == sdr_core::PushResult::Evicted,
        "DropOldest must report eviction"
    );
    expect(evicted == 11U, "DropOldest callback received incoming, not evicted item");

    IntQueue latest(2U, sdr_core::OverflowPolicy::LatestWins);
    expect(latest.push(31U) == sdr_core::PushResult::Pushed, "latest seed failed");
    expect(latest.push(32U) == sdr_core::PushResult::Pushed, "latest seed failed");
    evicted = 0U;
    expect(
        latest.push_with_eviction(
            33U,
            [&evicted](const std::uint64_t& value) noexcept { evicted = value; }
        ) == sdr_core::PushResult::Evicted,
        "LatestWins must report eviction"
    );
    expect(evicted == 32U, "LatestWins callback received wrong item");
}

void test_blocked_pop_wakeup() {
    IntQueue queue(4U, sdr_core::OverflowPolicy::Block);
    std::atomic<bool> returned{false};
    std::atomic<sdr_core::PopResult> result{sdr_core::PopResult::Popped};
    std::thread consumer([&] {
        std::uint64_t out = 0U;
        result.store(queue.pop(out));
        returned.store(true);
    });
    while (!returned.load() && queue.stats().depth == 0U) {
        // give the consumer a chance to block
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
        if (returned.load()) {
            break;
        }
        queue.request_stop();
    }
    consumer.join();
    expect(result.load() == sdr_core::PopResult::Stopped, "blocked pop must return Stopped");
}

void test_blocked_push_wakeup() {
    IntQueue queue(2U, sdr_core::OverflowPolicy::Block);
    expect(queue.push(1U) == sdr_core::PushResult::Pushed, "fill failed");
    expect(queue.push(2U) == sdr_core::PushResult::Pushed, "fill failed");
    std::atomic<sdr_core::PushResult> result{sdr_core::PushResult::Pushed};
    std::thread producer([&] {
        result.store(queue.push(3U));
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    queue.request_stop();
    producer.join();
    expect(result.load() == sdr_core::PushResult::Stopped, "blocked push must return Stopped");
    // Pending items survive stop for explicit drain.
    expect(queue.stats().depth == 2U, "stop must not silently discard pending items");
    std::uint64_t abandoned_sum = 0U;
    expect(
        queue.abandon_with([&abandoned_sum](const std::uint64_t& value) noexcept {
            abandoned_sum += value;
        }) == 2U,
        "abandon_with must count discarded items"
    );
    expect(abandoned_sum == 3U, "abandon_with must visit every pending item");
    expect(queue.stats().abandoned == 2U, "abandoned counter mismatch");
}

void test_fast_producer_exact_accounting() {
    constexpr std::uint64_t total = 10'000U;
    IntQueue queue(16U, sdr_core::OverflowPolicy::DropNewest);
    std::atomic<std::uint64_t> consumed{0U};
    std::atomic<bool> done{false};
    std::thread consumer([&] {
        std::uint64_t out = 0U;
        while (!done.load()) {
            if (queue.try_pop(out)) {
                ++consumed;
            } else {
                std::this_thread::yield();
            }
        }
        while (queue.try_pop(out)) {
            ++consumed;
        }
    });
    for (std::uint64_t value = 0U; value < total; ++value) {
        static_cast<void>(queue.push(value));
    }
    done.store(true);
    consumer.join();
    const auto stats = queue.stats();
    expect(stats.pushed + stats.dropped == total, "every item must be accounted exactly");
    expect(stats.pushed == stats.popped + stats.depth, "pushed != popped + depth");
    expect(stats.depth <= stats.capacity && stats.high_water <= stats.capacity, "bound violated");
    expect(consumed.load() == stats.popped, "consumer count disagrees with pop counter");
}

void test_multi_producer_multi_consumer() {
    constexpr std::uint64_t per_producer = 5'000U;
    IntQueue queue(32U, sdr_core::OverflowPolicy::Block);
    std::atomic<std::uint64_t> consumed{0U};
    std::atomic<std::uint64_t> producers_done{0U};
    std::vector<std::thread> threads;
    for (std::uint64_t producer = 0U; producer < 2U; ++producer) {
        threads.emplace_back([&, producer] {
            for (std::uint64_t value = 0U; value < per_producer; ++value) {
                static_cast<void>(queue.push(producer * per_producer + value));
            }
            ++producers_done;
        });
    }
    for (int consumer = 0; consumer < 2; ++consumer) {
        threads.emplace_back([&] {
            std::uint64_t out = 0U;
            while (true) {
                if (queue.pop(out) == sdr_core::PopResult::Stopped) {
                    break;
                }
                ++consumed;
            }
        });
    }
    while (producers_done.load() < 2U) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    // Wait until the queue drains, then stop consumers.
    while (queue.stats().depth != 0U) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    queue.request_stop();
    for (auto& thread : threads) {
        thread.join();
    }
    const auto stats = queue.stats();
    expect(stats.pushed == 2U * per_producer, "push counter mismatch");
    expect(consumed.load() == 2U * per_producer, "consumed mismatch");
    expect(stats.popped == consumed.load(), "pop counter mismatch");
    expect(stats.dropped == 0U, "Block policy must not drop");
}

}  // namespace

int main() {
    try {
        test_order_and_capacity();
        test_zero_capacity_rejected();
        test_drop_newest();
        test_drop_oldest();
        test_latest_wins();
        test_eviction_callback_reports_actual_item();
        test_blocked_pop_wakeup();
        test_blocked_push_wakeup();
        test_fast_producer_exact_accounting();
        test_multi_producer_multi_consumer();
        std::cout << "P04 bounded queue OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
