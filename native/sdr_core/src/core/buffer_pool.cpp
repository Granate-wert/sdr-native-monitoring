#include "sdr_core/buffer_pool.hpp"

#include <cstddef>
#include <utility>

namespace sdr_core {

struct BufferPool::State {
    State(const std::uint32_t block_count, const std::uint32_t block_size)
        : capacity(block_count), block_size(block_size) {
        slots.reserve(block_count);
        free_slots.resize(block_count);
        for (std::uint32_t index = 0; index < block_count; ++index) {
            slots.push_back(std::make_unique<std::vector<std::uint8_t>>(block_size));
            free_slots[index] = slots.back().get();
        }
        free_count = block_count;
    }

    void return_slot(std::vector<std::uint8_t>* slot) noexcept {
        {
            std::lock_guard lock(mutex);
            free_slots[free_count++] = slot;
            --in_use;
            ++returned;
        }
        cv.notify_one();
    }

    const std::uint32_t capacity;
    const std::uint32_t block_size;
    std::vector<std::unique_ptr<std::vector<std::uint8_t>>> slots;
    std::mutex mutex;
    std::condition_variable cv;
    // Fixed-capacity stack: shared_ptr deleters never allocate or throw.
    std::vector<std::vector<std::uint8_t>*> free_slots;
    std::uint32_t free_count{};
    std::uint32_t in_use{};
    std::uint32_t high_water{};
    std::uint64_t acquired{};
    std::uint64_t returned{};
    std::uint64_t exhausted{};
    bool stop{};
};

BufferPool::BufferPool(const std::uint32_t block_count, const std::uint32_t block_size) {
    if (block_count == 0U || block_size == 0U) {
        throw ConfigurationError("buffer pool block count/size must be positive");
    }
    state_ = std::make_shared<State>(block_count, block_size);
}

BufferPool::Block BufferPool::acquire() {
    auto state = state_;
    std::unique_lock lock(state->mutex);
    state->cv.wait(lock, [&state] {
        return state->free_count != 0U || state->stop;
    });
    if (state->stop) {
        return nullptr;
    }
    auto* slot = state->free_slots[--state->free_count];
    try {
        Block block(slot, [state](std::vector<std::uint8_t>* released) {
            state->return_slot(released);
        });
        ++state->in_use;
        ++state->acquired;
        if (state->in_use > state->high_water) {
            state->high_water = state->in_use;
        }
        return block;
    } catch (...) {
        // Control-block allocation failed: roll the slot back so the pool
        // accounting stays exact.
        state->free_slots[state->free_count++] = slot;
        throw;
    }
}

BufferPool::Block BufferPool::try_acquire() {
    auto state = state_;
    std::unique_lock lock(state->mutex);
    if (state->stop) {
        return nullptr;
    }
    if (state->free_count == 0U) {
        ++state->exhausted;
        return nullptr;
    }
    auto* slot = state->free_slots[--state->free_count];
    try {
        Block block(slot, [state](std::vector<std::uint8_t>* released) {
            state->return_slot(released);
        });
        ++state->in_use;
        ++state->acquired;
        if (state->in_use > state->high_water) {
            state->high_water = state->in_use;
        }
        return block;
    } catch (...) {
        state->free_slots[state->free_count++] = slot;
        throw;
    }
}

void BufferPool::request_stop() noexcept {
    auto state = state_;
    {
        std::lock_guard lock(state->mutex);
        state->stop = true;
    }
    state->cv.notify_all();
}

PoolStats BufferPool::stats() const {
    auto state = state_;
    std::lock_guard lock(state->mutex);
    PoolStats result;
    result.capacity = state->capacity;
    result.block_size = state->block_size;
    result.in_use = state->in_use;
    result.high_water = state->high_water;
    result.acquired = state->acquired;
    result.returned = state->returned;
    result.exhausted = state->exhausted;
    result.stop_requested = state->stop;
    return result;
}

}  // namespace sdr_core
