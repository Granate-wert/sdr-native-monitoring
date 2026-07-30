#include "sdr_core/buffer_pool.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/types.hpp"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void test_basic_acquire_release() {
    sdr_core::BufferPool pool(4U, 1024U);
    {
        auto block = pool.acquire();
        expect(block != nullptr, "acquire failed on a fresh pool");
        expect(block->size() == 1024U, "block size mismatch");
        expect(pool.stats().in_use == 1U, "in_use mismatch");
    }
    const auto stats = pool.stats();
    expect(stats.in_use == 0U, "slot not released");
    expect(stats.acquired == 1U && stats.returned == 1U, "acquire/return counters mismatch");
    expect(stats.capacity == 4U && stats.high_water == 1U, "capacity/high_water mismatch");
}

void test_invalid_pool_rejected() {
    bool rejected = false;
    try {
        const sdr_core::BufferPool invalid(0U, 16U);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "zero block count must be rejected");
    rejected = false;
    try {
        const sdr_core::BufferPool invalid(1U, 0U);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "zero block size must be rejected");
}

void test_exhaustion_is_bounded() {
    sdr_core::BufferPool pool(2U, 64U);
    auto first = pool.acquire();
    auto second = pool.acquire();
    expect(first != nullptr && second != nullptr, "initial acquires failed");
    expect(pool.try_acquire() == nullptr, "exhausted pool must return nullptr");
    expect(pool.stats().exhausted == 1U, "exhaustion counter mismatch");
    expect(pool.stats().in_use == 2U && pool.stats().high_water == 2U, "in_use bound violated");
    first.reset();
    auto third = pool.try_acquire();
    expect(third != nullptr, "released slot must be reusable");
    expect(pool.stats().exhausted == 1U, "exhausted must not change after reuse");
}

void test_slot_returned_after_last_owner() {
    sdr_core::BufferPool pool(1U, 64U);
    auto block = pool.acquire();
    sdr_core::SharedBuffer shared_view = block;  // contract-compatible const view
    expect(pool.stats().in_use == 1U, "in_use mismatch");
    block.reset();
    expect(pool.stats().in_use == 1U, "slot released before last owner");
    expect(pool.stats().returned == 0U, "return counter mismatch");
    shared_view.reset();
    expect(pool.stats().in_use == 0U, "slot not returned after last owner");
    expect(pool.stats().returned == 1U, "return counter mismatch");
}

void test_blocked_acquire_wakeup_on_stop() {
    sdr_core::BufferPool pool(1U, 64U);
    auto held = pool.acquire();
    std::atomic<bool> finished{false};
    std::atomic<bool> got_null{false};
    std::thread waiter([&] {
        auto block = pool.acquire();
        got_null.store(block == nullptr);
        finished.store(true);
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    pool.request_stop();
    waiter.join();
    expect(finished.load(), "blocked acquire did not wake up");
    expect(got_null.load(), "stopped acquire must return nullptr");
    held.reset();
}

void test_blocked_acquire_wakeup_on_release() {
    sdr_core::BufferPool pool(1U, 64U);
    auto held = pool.acquire();
    std::atomic<bool> acquired{false};
    std::thread waiter([&] {
        auto block = pool.acquire();
        acquired.store(block != nullptr);
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    held.reset();
    waiter.join();
    expect(acquired.load(), "release must unblock a waiting acquire");
}

void test_pool_destruction_with_outstanding_blocks() {
    std::shared_ptr<std::vector<std::uint8_t>> outstanding;
    {
        sdr_core::BufferPool pool(1U, 64U);
        outstanding = pool.acquire();
        expect(outstanding != nullptr, "acquire failed");
    }  // pool destroyed while a block is still owned
    expect(outstanding != nullptr, "outstanding block must stay valid");
    (*outstanding)[0] = 0xABU;
    expect((*outstanding)[0] == 0xABU, "outstanding block contents corrupted");
    outstanding.reset();  // slot returned to the orphaned state, no crash
}

void test_concurrent_stress_bounded() {
    sdr_core::BufferPool pool(8U, 256U);
    std::atomic<bool> stop{false};
    std::atomic<std::uint64_t> cycles{0U};
    std::vector<std::thread> workers;
    for (int index = 0; index < 4; ++index) {
        workers.emplace_back([&] {
            while (!stop.load()) {
                auto block = pool.try_acquire();
                if (block != nullptr) {
                    ++cycles;
                } else {
                    std::this_thread::yield();
                }
            }
        });
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    stop.store(true);
    for (auto& worker : workers) {
        worker.join();
    }
    const auto stats = pool.stats();
    expect(cycles.load() > 0U, "no acquire/release cycles executed");
    expect(stats.in_use == 0U, "all blocks must return to the pool");
    expect(stats.acquired == stats.returned, "acquired != returned");
    expect(stats.high_water <= stats.capacity, "high_water exceeded capacity");
}

}  // namespace

int main() {
    try {
        test_basic_acquire_release();
        test_invalid_pool_rejected();
        test_exhaustion_is_bounded();
        test_slot_returned_after_last_owner();
        test_blocked_acquire_wakeup_on_stop();
        test_blocked_acquire_wakeup_on_release();
        test_pool_destruction_with_outstanding_blocks();
        test_concurrent_stress_bounded();
        std::cout << "P04 buffer pool OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
