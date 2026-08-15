#include "sdr_hackrf/hackrf_rx_ingress.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

namespace sdr_hackrf {
namespace detail {

struct HackrfRxSlot {
    explicit HackrfRxSlot(const std::uint32_t bytes) : storage(bytes) {}

    std::vector<std::uint8_t> storage;
    std::uint32_t valid_bytes{};
    HackrfRxBlockMetadata metadata{};
};

struct HackrfRxIngressState {
    explicit HackrfRxIngressState(const HackrfRxIngressConfig& requested)
        : config(requested), ready_ring(requested.ready_capacity) {
        slots.reserve(config.slot_count);
        free_indices.resize(config.slot_count);
        for (std::uint32_t index = 0U; index < config.slot_count; ++index) {
            slots.emplace_back(config.slot_bytes);
            free_indices[index] = index;
        }
        free_count = config.slot_count;
    }

    const HackrfRxIngressConfig config;
    std::vector<HackrfRxSlot> slots;
    std::vector<std::uint32_t> free_indices;
    std::vector<std::uint32_t> ready_ring;
    mutable std::mutex mutex;
    std::condition_variable quiescent_cv;
    std::condition_variable ready_cv;
    std::uint32_t free_count{};
    std::uint32_t slots_high_water{};
    std::uint32_t ready_head{};
    std::uint32_t ready_tail{};
    std::uint32_t ready_count{};
    std::uint32_t ready_high_water{};

    std::atomic<bool> accepting{true};
    std::atomic_flag callback_gate = ATOMIC_FLAG_INIT;
    std::atomic<std::uint32_t> callbacks_active{};
    std::atomic<std::uint64_t> source_sequence{};
    std::atomic<std::uint64_t> sample_cursor{};
    // One-based marker for the last admitted callback. Access is serialized by
    // callback_gate. Any gap before the current source sequence is therefore
    // marked on exactly that next admitted block, including concurrent losers.
    std::uint64_t last_admitted_sequence_marker{};

    std::atomic<std::uint64_t> callbacks_total{};
    std::atomic<std::uint64_t> callback_bytes_total{};
    std::atomic<std::uint64_t> blocks_admitted{};
    std::atomic<std::uint64_t> bytes_admitted{};
    std::atomic<std::uint64_t> samples_admitted{};
    std::atomic<std::uint64_t> blocks_popped{};
    std::atomic<std::uint64_t> leases_released{};
    std::atomic<std::uint64_t> malformed_callbacks{};
    std::atomic<std::uint64_t> short_callbacks{};
    std::atomic<std::uint64_t> oversized_callbacks{};
    std::atomic<std::uint64_t> callbacks_after_stop{};
    std::atomic<std::uint64_t> lock_contention_drops{};
    std::atomic<std::uint64_t> pool_exhaustion_drops{};
    std::atomic<std::uint64_t> queue_full_drops{};
    std::atomic<std::uint64_t> abandoned_blocks{};
    std::atomic<std::uint64_t> dropped_bytes{};
    std::atomic<std::uint64_t> dropped_samples{};
    std::atomic<std::uint64_t> loss_events{};
};

}  // namespace detail
namespace {

class ActiveCallback final {
public:
    explicit ActiveCallback(const std::shared_ptr<detail::HackrfRxIngressState>& state)
        : state_(state) {
        state_->callbacks_active.fetch_add(1U, std::memory_order_acq_rel);
    }

    ~ActiveCallback() {
        if (state_->callbacks_active.fetch_sub(1U, std::memory_order_acq_rel) == 1U) {
            state_->quiescent_cv.notify_all();
            state_->ready_cv.notify_all();
        }
    }

    ActiveCallback(const ActiveCallback&) = delete;
    ActiveCallback& operator=(const ActiveCallback&) = delete;

private:
    std::shared_ptr<detail::HackrfRxIngressState> state_;
};

class CallbackGate final {
public:
    explicit CallbackGate(const std::shared_ptr<detail::HackrfRxIngressState>& state)
        : state_(state), owns_(!state_->callback_gate.test_and_set(std::memory_order_acquire)) {}

    ~CallbackGate() {
        if (owns_) {
            state_->callback_gate.clear(std::memory_order_release);
        }
    }

    CallbackGate(const CallbackGate&) = delete;
    CallbackGate& operator=(const CallbackGate&) = delete;
    [[nodiscard]] bool owns() const noexcept { return owns_; }

private:
    std::shared_ptr<detail::HackrfRxIngressState> state_;
    bool owns_{};
};

void count_loss(
    const std::shared_ptr<detail::HackrfRxIngressState>& state,
    const std::uint64_t byte_count,
    const std::uint64_t sample_count
) noexcept {
    state->dropped_bytes.fetch_add(byte_count, std::memory_order_relaxed);
    state->dropped_samples.fetch_add(sample_count, std::memory_order_relaxed);
    state->loss_events.fetch_add(1U, std::memory_order_relaxed);
}

void release_slot(
    const std::shared_ptr<detail::HackrfRxIngressState>& state,
    const std::uint32_t slot_index
) noexcept {
    std::lock_guard lock(state->mutex);
    state->slots[slot_index].valid_bytes = 0U;
    state->free_indices[state->free_count++] = slot_index;
    state->leases_released.fetch_add(1U, std::memory_order_relaxed);
}

struct SharedSlotOwner {
    std::shared_ptr<detail::HackrfRxIngressState> state;
    std::uint32_t slot_index{};

    SharedSlotOwner(
        std::shared_ptr<detail::HackrfRxIngressState> owned_state,
        const std::uint32_t owned_slot_index
    ) noexcept : state(std::move(owned_state)), slot_index(owned_slot_index) {}

    SharedSlotOwner(const SharedSlotOwner&) = delete;
    SharedSlotOwner& operator=(const SharedSlotOwner&) = delete;

    ~SharedSlotOwner() {
        if (state) {
            release_slot(state, slot_index);
        }
    }
};

}  // namespace

HackrfRxLease::HackrfRxLease(
    std::shared_ptr<detail::HackrfRxIngressState> state,
    const std::uint32_t slot_index,
    HackrfRxBlockMetadata metadata
) noexcept
    : state_(std::move(state)), slot_index_(slot_index), metadata_(metadata) {}

HackrfRxLease::~HackrfRxLease() { reset(); }

HackrfRxLease::HackrfRxLease(HackrfRxLease&& other) noexcept
    : state_(std::move(other.state_)),
      slot_index_(other.slot_index_),
      metadata_(other.metadata_) {}

HackrfRxLease& HackrfRxLease::operator=(HackrfRxLease&& other) noexcept {
    if (this != &other) {
        reset();
        state_ = std::move(other.state_);
        slot_index_ = other.slot_index_;
        metadata_ = other.metadata_;
    }
    return *this;
}

HackrfRxLease::operator bool() const noexcept { return state_ != nullptr; }

std::span<const std::uint8_t> HackrfRxLease::samples() const noexcept {
    if (!state_) {
        return {};
    }
    const auto& slot = state_->slots[slot_index_];
    return {slot.storage.data(), slot.storage.size()};
}

const HackrfRxBlockMetadata& HackrfRxLease::metadata() const noexcept { return metadata_; }

sdr_core::IqBlock HackrfRxLease::take_iq_block() {
    if (!state_) {
        throw sdr_core::SdrNativeError("HackRF RX lease is empty");
    }
    auto state = std::move(state_);
    std::shared_ptr<SharedSlotOwner> owner;
    try {
        owner = std::make_shared<SharedSlotOwner>(state, slot_index_);
    } catch (...) {
        release_slot(state, slot_index_);
        throw;
    }
    sdr_core::SharedBuffer samples(owner, &state->slots[slot_index_].storage);
    return {
        .source_sequence = metadata_.source_sequence,
        .first_sample_index = metadata_.first_sample_index,
        .timestamp_ns = metadata_.host_timestamp_ns,
        .center_frequency_hz = state->config.center_frequency_hz,
        .sample_rate_hz = state->config.sample_rate_hz,
        .sample_format = metadata_.sample_format,
        .sample_count = metadata_.sample_count,
        .flags = metadata_.quality_flags,
        .samples = std::move(samples),
        .config_generation = metadata_.config_generation,
    };
}

void HackrfRxLease::reset() noexcept {
    if (state_) {
        auto state = std::move(state_);
        release_slot(state, slot_index_);
    }
}

HackrfRxIngress::HackrfRxIngress(const HackrfRxIngressConfig config) {
    if (config.slot_count == 0U || config.slot_count > hackrf_rx_max_slot_count ||
        config.slot_bytes == 0U || config.slot_bytes > hackrf_rx_max_slot_bytes ||
        (config.slot_bytes % 2U) != 0U) {
        throw sdr_core::ConfigurationError(
            "HackRF ingress slot count/bytes exceed bounded CI8 limits"
        );
    }
    if (config.ready_capacity == 0U || config.ready_capacity > config.slot_count) {
        throw sdr_core::ConfigurationError(
            "HackRF ingress ready capacity must be in [1, slot_count]"
        );
    }
    if (!std::isfinite(config.center_frequency_hz) || config.center_frequency_hz <= 0.0 ||
        !std::isfinite(config.sample_rate_hz) || config.sample_rate_hz <= 0.0) {
        throw sdr_core::ConfigurationError(
            "HackRF ingress frequency and sample rate must be finite and positive"
        );
    }
    state_ = std::make_shared<detail::HackrfRxIngressState>(config);
}

HackrfRxIngress::~HackrfRxIngress() {
    request_stop();
    static_cast<void>(wait_callbacks_quiescent(std::chrono::seconds(5)));
    static_cast<void>(abandon_ready());
}

HackrfRxAdmissionResult HackrfRxIngress::admit_callback(
    const std::span<const std::uint8_t> interleaved_ci8,
    const std::int64_t host_timestamp_ns
) noexcept {
    auto state = state_;
    ActiveCallback active(state);
    state->callbacks_total.fetch_add(1U, std::memory_order_relaxed);
    state->callback_bytes_total.fetch_add(interleaved_ci8.size(), std::memory_order_relaxed);
    CallbackGate callback_gate(state);
    const auto source_sequence =
        state->source_sequence.fetch_add(1U, std::memory_order_relaxed);
    const auto sample_count = static_cast<std::uint64_t>(interleaved_ci8.size() / 2U);
    const auto first_sample_index =
        state->sample_cursor.fetch_add(sample_count, std::memory_order_relaxed);

    if (!callback_gate.owns()) {
        state->lock_contention_drops.fetch_add(1U, std::memory_order_relaxed);
        count_loss(state, interleaved_ci8.size(), sample_count);
        return HackrfRxAdmissionResult::LockContended;
    }

    if (!state->accepting.load(std::memory_order_acquire)) {
        state->callbacks_after_stop.fetch_add(1U, std::memory_order_relaxed);
        count_loss(state, interleaved_ci8.size(), sample_count);
        return HackrfRxAdmissionResult::Stopped;
    }
    if (interleaved_ci8.empty() || (interleaved_ci8.size() % 2U) != 0U ||
        host_timestamp_ns <= 0) {
        state->malformed_callbacks.fetch_add(1U, std::memory_order_relaxed);
        count_loss(state, interleaved_ci8.size(), sample_count);
        return HackrfRxAdmissionResult::Malformed;
    }
    if (interleaved_ci8.size() < state->config.slot_bytes) {
        state->short_callbacks.fetch_add(1U, std::memory_order_relaxed);
        count_loss(state, interleaved_ci8.size(), sample_count);
        return HackrfRxAdmissionResult::Short;
    }
    if (interleaved_ci8.size() > state->config.slot_bytes) {
        state->oversized_callbacks.fetch_add(1U, std::memory_order_relaxed);
        count_loss(state, interleaved_ci8.size(), sample_count);
        return HackrfRxAdmissionResult::Oversized;
    }

    std::unique_lock lock(state->mutex, std::try_to_lock);
    if (!lock.owns_lock()) {
        state->lock_contention_drops.fetch_add(1U, std::memory_order_relaxed);
        count_loss(state, interleaved_ci8.size(), sample_count);
        return HackrfRxAdmissionResult::LockContended;
    }
    if (!state->accepting.load(std::memory_order_acquire)) {
        lock.unlock();
        state->callbacks_after_stop.fetch_add(1U, std::memory_order_relaxed);
        count_loss(state, interleaved_ci8.size(), sample_count);
        return HackrfRxAdmissionResult::Stopped;
    }
    if (state->free_count == 0U) {
        lock.unlock();
        state->pool_exhaustion_drops.fetch_add(1U, std::memory_order_relaxed);
        count_loss(state, interleaved_ci8.size(), sample_count);
        return HackrfRxAdmissionResult::PoolExhausted;
    }
    if (state->ready_count == state->config.ready_capacity) {
        lock.unlock();
        state->queue_full_drops.fetch_add(1U, std::memory_order_relaxed);
        count_loss(state, interleaved_ci8.size(), sample_count);
        return HackrfRxAdmissionResult::QueueFull;
    }

    const auto slot_index = state->free_indices[--state->free_count];
    auto& slot = state->slots[slot_index];
    std::copy(interleaved_ci8.begin(), interleaved_ci8.end(), slot.storage.begin());
    slot.valid_bytes = static_cast<std::uint32_t>(interleaved_ci8.size());
    slot.metadata = {
        .source_sequence = source_sequence,
        .first_sample_index = first_sample_index,
        .host_timestamp_ns = host_timestamp_ns,
        .sample_count = static_cast<std::uint32_t>(sample_count),
        .sample_format = sdr_core::SampleFormat::ComplexInt8Interleaved,
        .quality_flags = sdr_core::QualityFlag::TimestampEstimated,
        .config_generation = state->config.config_generation,
    };
    if (source_sequence != state->last_admitted_sequence_marker) {
        slot.metadata.quality_flags =
            slot.metadata.quality_flags | sdr_core::QualityFlag::IqDropped;
    }
    state->last_admitted_sequence_marker = source_sequence + 1U;
    state->ready_ring[state->ready_tail] = slot_index;
    state->ready_tail = (state->ready_tail + 1U) % state->config.ready_capacity;
    ++state->ready_count;
    state->ready_high_water = std::max(state->ready_high_water, state->ready_count);
    const auto slots_in_use = state->config.slot_count - state->free_count;
    state->slots_high_water = std::max(state->slots_high_water, slots_in_use);
    lock.unlock();

    state->blocks_admitted.fetch_add(1U, std::memory_order_relaxed);
    state->bytes_admitted.fetch_add(interleaved_ci8.size(), std::memory_order_relaxed);
    state->samples_admitted.fetch_add(sample_count, std::memory_order_relaxed);
    state->ready_cv.notify_one();
    return HackrfRxAdmissionResult::Admitted;
}

HackrfRxPopResult HackrfRxIngress::pop(HackrfRxLease& output) noexcept {
    auto state = state_;
    output.reset();
    std::unique_lock lock(state->mutex);
    state->ready_cv.wait(lock, [&state] {
        return state->ready_count != 0U ||
               (!state->accepting.load(std::memory_order_acquire) &&
                state->callbacks_active.load(std::memory_order_acquire) == 0U);
    });
    if (state->ready_count == 0U) {
        return HackrfRxPopResult::Stopped;
    }
    const auto slot_index = state->ready_ring[state->ready_head];
    state->ready_head = (state->ready_head + 1U) % state->config.ready_capacity;
    --state->ready_count;
    auto metadata = state->slots[slot_index].metadata;
    output = HackrfRxLease(state, slot_index, metadata);
    state->blocks_popped.fetch_add(1U, std::memory_order_relaxed);
    return HackrfRxPopResult::Popped;
}

bool HackrfRxIngress::try_pop(HackrfRxLease& output) noexcept {
    auto state = state_;
    output.reset();
    std::lock_guard lock(state->mutex);
    if (state->ready_count == 0U) {
        return false;
    }
    const auto slot_index = state->ready_ring[state->ready_head];
    state->ready_head = (state->ready_head + 1U) % state->config.ready_capacity;
    --state->ready_count;
    auto metadata = state->slots[slot_index].metadata;
    output = HackrfRxLease(state, slot_index, metadata);
    state->blocks_popped.fetch_add(1U, std::memory_order_relaxed);
    return true;
}

void HackrfRxIngress::request_stop() noexcept {
    state_->accepting.store(false, std::memory_order_release);
    state_->ready_cv.notify_all();
}

bool HackrfRxIngress::wait_callbacks_quiescent(
    const std::chrono::milliseconds timeout
) noexcept {
    auto state = state_;
    std::unique_lock lock(state->mutex);
    return state->quiescent_cv.wait_for(lock, timeout, [&state] {
        return state->callbacks_active.load(std::memory_order_acquire) == 0U;
    });
}

std::uint64_t HackrfRxIngress::abandon_ready() noexcept {
    auto state = state_;
    std::lock_guard lock(state->mutex);
    const auto abandoned = static_cast<std::uint64_t>(state->ready_count);
    while (state->ready_count != 0U) {
        const auto slot_index = state->ready_ring[state->ready_head];
        state->ready_head = (state->ready_head + 1U) % state->config.ready_capacity;
        --state->ready_count;
        const auto samples = state->slots[slot_index].metadata.sample_count;
        const auto bytes = state->slots[slot_index].valid_bytes;
        state->slots[slot_index].valid_bytes = 0U;
        state->free_indices[state->free_count++] = slot_index;
        state->dropped_bytes.fetch_add(bytes, std::memory_order_relaxed);
        state->dropped_samples.fetch_add(samples, std::memory_order_relaxed);
        state->loss_events.fetch_add(1U, std::memory_order_relaxed);
    }
    state->abandoned_blocks.fetch_add(abandoned, std::memory_order_relaxed);
    return abandoned;
}

HackrfRxIngressMetrics HackrfRxIngress::metrics() const noexcept {
    auto state = state_;
    HackrfRxIngressMetrics result;
    {
        std::lock_guard lock(state->mutex);
        result.slot_capacity = state->config.slot_count;
        result.slot_bytes = state->config.slot_bytes;
        result.slots_in_use = state->config.slot_count - state->free_count;
        result.slots_high_water = state->slots_high_water;
        result.ready_capacity = state->config.ready_capacity;
        result.ready_depth = state->ready_count;
        result.ready_high_water = state->ready_high_water;
    }
    result.callbacks_active = state->callbacks_active.load(std::memory_order_relaxed);
    result.accepting_callbacks = state->accepting.load(std::memory_order_relaxed);
    result.callbacks_total = state->callbacks_total.load(std::memory_order_relaxed);
    result.callback_bytes_total = state->callback_bytes_total.load(std::memory_order_relaxed);
    result.blocks_admitted = state->blocks_admitted.load(std::memory_order_relaxed);
    result.bytes_admitted = state->bytes_admitted.load(std::memory_order_relaxed);
    result.samples_admitted = state->samples_admitted.load(std::memory_order_relaxed);
    result.blocks_popped = state->blocks_popped.load(std::memory_order_relaxed);
    result.leases_released = state->leases_released.load(std::memory_order_relaxed);
    result.malformed_callbacks = state->malformed_callbacks.load(std::memory_order_relaxed);
    result.short_callbacks = state->short_callbacks.load(std::memory_order_relaxed);
    result.oversized_callbacks = state->oversized_callbacks.load(std::memory_order_relaxed);
    result.callbacks_after_stop = state->callbacks_after_stop.load(std::memory_order_relaxed);
    result.lock_contention_drops = state->lock_contention_drops.load(std::memory_order_relaxed);
    result.pool_exhaustion_drops = state->pool_exhaustion_drops.load(std::memory_order_relaxed);
    result.queue_full_drops = state->queue_full_drops.load(std::memory_order_relaxed);
    result.abandoned_blocks = state->abandoned_blocks.load(std::memory_order_relaxed);
    result.dropped_bytes = state->dropped_bytes.load(std::memory_order_relaxed);
    result.dropped_samples = state->dropped_samples.load(std::memory_order_relaxed);
    result.loss_events = state->loss_events.load(std::memory_order_relaxed);
    result.device_overrun_counter_available = false;
    return result;
}

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
void HackrfRxIngress::test_hold_active_callback(
    std::atomic<bool>& entered,
    const std::atomic<bool>& release
) noexcept {
    auto state = state_;
    ActiveCallback active(state);
    entered.store(true, std::memory_order_release);
    while (!release.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }
}

void HackrfRxIngress::test_hold_callback_gate(
    std::atomic<bool>& entered,
    const std::atomic<bool>& release
) noexcept {
    auto state = state_;
    ActiveCallback active(state);
    CallbackGate gate(state);
    entered.store(gate.owns(), std::memory_order_release);
    while (!release.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }
}
#endif

bool HackrfRxShutdownResult::complete() const noexcept {
    return stop_rx_status == 0 && callbacks_quiescent && close_called &&
           close_status == 0 && exit_called && exit_status == 0;
}

HackrfRxShutdownResult shutdown_hackrf_rx(
    HackrfRxIngress& ingress,
    HackrfRxShutdownPort& port,
    const std::chrono::milliseconds callback_timeout
) noexcept {
    HackrfRxShutdownResult result;
    ingress.request_stop();
    result.stop_rx_status = port.stop_rx();
    result.callbacks_quiescent = ingress.wait_callbacks_quiescent(callback_timeout);
    if (!result.callbacks_quiescent || result.stop_rx_status != 0) {
        return result;
    }
    result.abandoned_blocks = ingress.abandon_ready();
    result.close_called = true;
    result.close_status = port.close_device();
    if (result.close_status != 0) {
        return result;
    }
    result.exit_called = true;
    result.exit_status = port.exit_library();
    return result;
}

}  // namespace sdr_hackrf
