#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>

namespace sdr_core {

// Cooperative cancellation state shared between engine threads.
// request_stop() is idempotent, sets the flag and wakes every waiter.
// The state is held through std::shared_ptr so a token never dangles.
class StopState final {
public:
    void request_stop() noexcept {
        {
            std::lock_guard lock(mutex_);
            stop_.store(true, std::memory_order_release);
        }
        cv_.notify_all();
    }

    [[nodiscard]] bool stop_requested() const noexcept {
        return stop_.load(std::memory_order_acquire);
    }

    // Waits until stop is requested or the deadline passes.
    // Returns true when stop was requested.
    template <typename Rep, typename Period>
    bool wait_for(const std::chrono::duration<Rep, Period>& duration) {
        std::unique_lock lock(mutex_);
        return cv_.wait_for(lock, duration, [this] {
            return stop_.load(std::memory_order_acquire);
        });
    }

private:
    std::atomic<bool> stop_{false};
    std::mutex mutex_;
    std::condition_variable cv_;
};

using StopToken = std::shared_ptr<StopState>;

[[nodiscard]] inline StopToken make_stop_token() {
    return std::make_shared<StopState>();
}

}  // namespace sdr_core
