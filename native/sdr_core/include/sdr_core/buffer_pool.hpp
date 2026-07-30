#pragma once

#include "sdr_core/errors.hpp"

#include <condition_variable>
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <vector>

namespace sdr_core {

struct PoolStats {
    std::uint32_t capacity{};
    std::uint32_t block_size{};
    std::uint32_t in_use{};
    std::uint32_t high_water{};
    std::uint64_t acquired{};
    std::uint64_t returned{};
    std::uint64_t exhausted{};
    bool stop_requested{};
};

// Preallocated pool of fixed-size byte blocks for I/Q transport.
//
// All sample storage is allocated once at construction; acquire() only
// allocates one shared_ptr control block per outstanding block and never
// reallocates sample storage. The returned shared_ptr has a custom deleter
// that returns the slot to the pool when the last owner releases it. The
// shared_ptr<std::vector<uint8_t>> converts implicitly to SharedBuffer
// (shared_ptr<const vector<uint8_t>>) used by IqBlock, so pool blocks cross
// the existing P02 contract boundary without copying.
//
// Lifetime: the internal state is shared between the pool and every
// outstanding block deleter, so destroying the pool while blocks are alive
// is safe; slots are reclaimed when the last block owner goes away.
// Threads blocked in acquire() must be released with request_stop() (and
// joined) before the pool is destroyed.
class BufferPool final {
public:
    using Block = std::shared_ptr<std::vector<std::uint8_t>>;

    BufferPool(std::uint32_t block_count, std::uint32_t block_size);

    BufferPool(const BufferPool&) = delete;
    BufferPool& operator=(const BufferPool&) = delete;

    // Blocking acquire. Returns nullptr only after request_stop().
    [[nodiscard]] Block acquire();

    // Never blocks. Returns nullptr when the pool is exhausted (counted in
    // PoolStats::exhausted) or stopped.
    [[nodiscard]] Block try_acquire();

    // Wakes every blocked acquire(); outstanding blocks remain valid and
    // return their slots normally.
    void request_stop() noexcept;

    [[nodiscard]] PoolStats stats() const;

private:
    struct State;
    std::shared_ptr<State> state_;
};

}  // namespace sdr_core
