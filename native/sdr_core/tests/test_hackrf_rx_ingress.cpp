#include "sdr_hackrf/hackrf_rx_ingress.hpp"

#include "sdr_core/errors.hpp"

#include <chrono>
#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <new>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace allocation_probe {
std::atomic<bool> enabled{};
std::atomic<std::uint64_t> count{};
}

void* operator new(const std::size_t size) {
    if (allocation_probe::enabled.load(std::memory_order_relaxed)) {
        allocation_probe::count.fetch_add(1U, std::memory_order_relaxed);
    }
    if (void* const memory = std::malloc(size)) {
        return memory;
    }
    throw std::bad_alloc{};
}

void operator delete(void* const memory) noexcept { std::free(memory); }
void operator delete(void* const memory, const std::size_t) noexcept { std::free(memory); }

void* operator new[](const std::size_t size) {
    return ::operator new(size);
}

void operator delete[](void* const memory) noexcept { ::operator delete(memory); }
void operator delete[](void* const memory, const std::size_t size) noexcept {
    ::operator delete(memory, size);
}

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

sdr_hackrf::HackrfRxIngressConfig config(
    const std::uint32_t slots = 3U,
    const std::uint32_t ready = 2U,
    const std::uint32_t bytes = 8U
) {
    return {
        .slot_count = slots,
        .slot_bytes = bytes,
        .ready_capacity = ready,
        .center_frequency_hz = 100'000'000.0,
        .sample_rate_hz = 10'000'000.0,
        .config_generation = 7U,
    };
}

std::vector<std::uint8_t> bytes(const std::uint32_t count, const std::uint8_t seed = 0U) {
    std::vector<std::uint8_t> result(count);
    for (std::uint32_t index = 0U; index < count; ++index) {
        result[index] = static_cast<std::uint8_t>(seed + index);
    }
    return result;
}

void test_config_fail_closed() {
    bool rejected = false;
    try {
        auto invalid = config();
        invalid.slot_bytes = 15U;
        const sdr_hackrf::HackrfRxIngress ingress(invalid);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "odd CI8 slot size must be rejected");

    rejected = false;
    try {
        auto invalid = config();
        invalid.slot_count = sdr_hackrf::hackrf_rx_max_slot_count + 1U;
        const sdr_hackrf::HackrfRxIngress ingress(invalid);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "excessive slot count must be rejected");

    rejected = false;
    try {
        auto invalid = config();
        invalid.slot_bytes = sdr_hackrf::hackrf_rx_max_slot_bytes + 2U;
        const sdr_hackrf::HackrfRxIngress ingress(invalid);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "excessive slot size must be rejected");

    rejected = false;
    try {
        auto invalid = config();
        invalid.ready_capacity = invalid.slot_count + 1U;
        const sdr_hackrf::HackrfRxIngress ingress(invalid);
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, "ready capacity above pool capacity must be rejected");
}

void test_ci8_copy_metadata_and_lease_lifetime() {
    sdr_hackrf::HackrfRxLease retained;
    sdr_core::IqBlock retained_iq;
    {
        sdr_hackrf::HackrfRxIngress ingress(config());
        const auto input = bytes(8U, 10U);
        expect(
            ingress.admit_callback(input, 1234) ==
                sdr_hackrf::HackrfRxAdmissionResult::Admitted,
            "valid callback was not admitted"
        );
        expect(ingress.try_pop(retained), "admitted callback was not poppable");
        expect(retained.samples().size() == input.size(), "lease byte count mismatch");
        expect(
            std::equal(retained.samples().begin(), retained.samples().end(), input.begin()),
            "callback copy mismatch"
        );
        const auto& metadata = retained.metadata();
        expect(metadata.source_sequence == 0U, "initial source sequence mismatch");
        expect(metadata.first_sample_index == 0U, "initial sample index mismatch");
        expect(metadata.sample_count == 4U, "CI8 sample count mismatch");
        expect(
            metadata.sample_format == sdr_core::SampleFormat::ComplexInt8Interleaved,
            "CI8 format mismatch"
        );
        expect(
            sdr_core::has_flag(metadata.quality_flags, sdr_core::QualityFlag::TimestampEstimated),
            "host timestamp must remain estimated"
        );
        expect(metadata.config_generation == 7U, "generation mismatch");
        retained_iq = retained.take_iq_block();
        sdr_core::validate(retained_iq);
        expect(!retained, "taking IqBlock did not invalidate the lease");
        expect(retained_iq.samples->size() == input.size(), "IqBlock byte count mismatch");
        expect(retained_iq.center_frequency_hz == 100'000'000.0, "IqBlock center mismatch");
        expect(retained_iq.sample_rate_hz == 10'000'000.0, "IqBlock sample rate mismatch");
    }
    // Lease owns the preallocated state and remains valid after the ingress
    // facade is destroyed; no SDK/device storage is referenced.
    expect(retained_iq.samples->size() == 8U, "IqBlock did not preserve state lifetime");
    retained_iq.samples.reset();
}

void test_queue_full_exact_loss_and_discontinuity() {
    sdr_hackrf::HackrfRxIngress ingress(config(3U, 1U));
    const auto first = bytes(8U, 1U);
    const auto dropped = bytes(8U, 20U);
    const auto third = bytes(8U, 40U);
    expect(
        ingress.admit_callback(first, 100) == sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "first callback failed"
    );
    expect(
        ingress.admit_callback(dropped, 200) == sdr_hackrf::HackrfRxAdmissionResult::QueueFull,
        "full queue did not reject newest"
    );
    sdr_hackrf::HackrfRxLease lease;
    expect(ingress.try_pop(lease), "first block missing");
    lease.reset();
    expect(
        ingress.admit_callback(third, 300) == sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "post-drop callback failed"
    );
    expect(ingress.try_pop(lease), "post-drop block missing");
    expect(lease.metadata().source_sequence == 2U, "dropped callback sequence was hidden");
    expect(lease.metadata().first_sample_index == 8U, "dropped sample range was hidden");
    expect(
        sdr_core::has_flag(lease.metadata().quality_flags, sdr_core::QualityFlag::IqDropped),
        "post-drop block lacks IqDropped"
    );
    lease.reset();
    const auto metrics = ingress.metrics();
    expect(metrics.queue_full_drops == 1U, "queue-full counter mismatch");
    expect(metrics.dropped_bytes == 8U, "dropped-byte counter mismatch");
    expect(metrics.dropped_samples == 4U, "dropped-sample counter mismatch");
    expect(metrics.loss_events == 1U, "loss-event counter mismatch");
    expect(metrics.blocks_admitted == 2U && metrics.blocks_popped == 2U, "flow counters mismatch");
}

void test_pool_exhaustion_from_retained_lease() {
    sdr_hackrf::HackrfRxIngress ingress(config(1U, 1U));
    const auto input = bytes(8U);
    expect(
        ingress.admit_callback(input, 100) == sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "first callback failed"
    );
    sdr_hackrf::HackrfRxLease lease;
    expect(ingress.try_pop(lease), "first block missing");
    expect(
        ingress.admit_callback(input, 200) ==
            sdr_hackrf::HackrfRxAdmissionResult::PoolExhausted,
        "retained lease did not exhaust the one-slot pool"
    );
    expect(ingress.metrics().pool_exhaustion_drops == 1U, "pool drop counter mismatch");
    lease.reset();
    expect(
        ingress.admit_callback(input, 300) == sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "released slot was not reusable"
    );
}

void test_callback_admission_allocates_nothing_after_construction() {
    sdr_hackrf::HackrfRxIngress ingress(config());
    const auto input = bytes(8U, 3U);
    allocation_probe::count.store(0U, std::memory_order_relaxed);
    allocation_probe::enabled.store(true, std::memory_order_release);
    const auto result = ingress.admit_callback(input, 100);
    allocation_probe::enabled.store(false, std::memory_order_release);
    expect(result == sdr_hackrf::HackrfRxAdmissionResult::Admitted, "allocation probe failed");
    expect(
        allocation_probe::count.load(std::memory_order_relaxed) == 0U,
        "callback admission allocated from the heap"
    );
}

void test_malformed_oversized_and_stop_accounting() {
    sdr_hackrf::HackrfRxIngress ingress(config());
    expect(
        ingress.admit_callback(bytes(3U), 100) ==
            sdr_hackrf::HackrfRxAdmissionResult::Malformed,
        "odd CI8 callback must be malformed"
    );
    expect(
        ingress.admit_callback(bytes(6U), 150) ==
            sdr_hackrf::HackrfRxAdmissionResult::Short,
        "short callback must be rejected"
    );
    expect(
        ingress.admit_callback(bytes(10U), 200) ==
            sdr_hackrf::HackrfRxAdmissionResult::Oversized,
        "oversized callback must be rejected"
    );
    expect(
        ingress.admit_callback(bytes(8U), 0) ==
            sdr_hackrf::HackrfRxAdmissionResult::Malformed,
        "invalid host timestamp must be rejected"
    );
    ingress.request_stop();
    expect(
        ingress.admit_callback(bytes(8U), 300) ==
            sdr_hackrf::HackrfRxAdmissionResult::Stopped,
        "post-stop callback must be rejected"
    );
    const auto metrics = ingress.metrics();
    expect(metrics.callbacks_total == 5U, "callback total mismatch");
    expect(metrics.malformed_callbacks == 2U, "malformed counter mismatch");
    expect(metrics.short_callbacks == 1U, "short counter mismatch");
    expect(metrics.oversized_callbacks == 1U, "oversized counter mismatch");
    expect(metrics.callbacks_after_stop == 1U, "after-stop counter mismatch");
    expect(metrics.dropped_bytes == 35U, "all rejected callback bytes were not counted");
    expect(!metrics.accepting_callbacks, "stop did not close admission");
}

struct FakeShutdownPort final : sdr_hackrf::HackrfRxShutdownPort {
    std::vector<std::string> calls;
    int stop_status{};
    int close_status{};
    int exit_status{};

    int stop_rx() noexcept override {
        calls.emplace_back("stop_rx");
        return stop_status;
    }
    int close_device() noexcept override {
        calls.emplace_back("close");
        return close_status;
    }
    int exit_library() noexcept override {
        calls.emplace_back("exit");
        return exit_status;
    }
};

void test_shutdown_order_and_explicit_abandon() {
    sdr_hackrf::HackrfRxIngress ingress(config());
    expect(
        ingress.admit_callback(bytes(8U), 100) ==
            sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "setup callback failed"
    );
    FakeShutdownPort port;
    const auto result = sdr_hackrf::shutdown_hackrf_rx(
        ingress, port, std::chrono::milliseconds(100)
    );
    expect(result.complete(), "clean shutdown must be complete");
    expect(result.abandoned_blocks == 1U, "pending block was not explicitly abandoned");
    expect(
        port.calls == std::vector<std::string>({"stop_rx", "close", "exit"}),
        "shutdown call order mismatch"
    );
    const auto metrics = ingress.metrics();
    expect(metrics.abandoned_blocks == 1U, "abandon counter mismatch");
    expect(metrics.ready_depth == 0U && metrics.slots_in_use == 0U, "shutdown leaked slots");
}

void test_shutdown_reports_vendor_failures_without_skipping_cleanup() {
    sdr_hackrf::HackrfRxIngress ingress(config());
    FakeShutdownPort port;
    port.stop_status = -1;
    const auto result = sdr_hackrf::shutdown_hackrf_rx(
        ingress, port, std::chrono::milliseconds(100)
    );
    expect(!result.complete(), "vendor failures must not report complete");
    expect(!result.close_called && !result.exit_called, "unsafe cleanup followed failed stop_rx");

    sdr_hackrf::HackrfRxIngress close_failure_ingress(config());
    FakeShutdownPort close_failure_port;
    close_failure_port.close_status = -2;
    close_failure_port.exit_status = -3;
    const auto close_failure = sdr_hackrf::shutdown_hackrf_rx(
        close_failure_ingress, close_failure_port, std::chrono::milliseconds(100)
    );
    expect(close_failure.close_called, "close was not attempted after clean stop");
    expect(!close_failure.exit_called, "library exit followed a failed device close");
    expect(close_failure.close_status == -2, "close status was not preserved");
}

void test_shutdown_timeout_refuses_close_and_exit() {
    sdr_hackrf::HackrfRxIngress ingress(config());
    std::atomic<bool> entered{};
    std::atomic<bool> release{};
    std::thread callback([&] {
        ingress.test_hold_active_callback(entered, release);
    });
    while (!entered.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }

    FakeShutdownPort port;
    const auto result = sdr_hackrf::shutdown_hackrf_rx(
        ingress, port, std::chrono::milliseconds(0)
    );
    expect(!result.complete(), "active callback timeout reported complete");
    expect(!result.callbacks_quiescent, "active callback incorrectly reported quiescent");
    expect(!result.close_called && !result.exit_called, "timeout allowed unsafe close/exit");
    expect(port.calls == std::vector<std::string>({"stop_rx"}), "timeout order mismatch");

    release.store(true, std::memory_order_release);
    callback.join();
    expect(
        ingress.wait_callbacks_quiescent(std::chrono::milliseconds(100)),
        "released callback did not become quiescent"
    );
}

void test_concurrent_loss_marks_only_the_next_later_sequence() {
    sdr_hackrf::HackrfRxIngress ingress(config());
    std::atomic<bool> entered{};
    std::atomic<bool> release{};
    std::thread gate_holder([&] {
        ingress.test_hold_callback_gate(entered, release);
    });
    while (!entered.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }

    const auto input = bytes(8U, 12U);
    expect(
        ingress.admit_callback(input, 100) ==
            sdr_hackrf::HackrfRxAdmissionResult::LockContended,
        "occupied callback gate did not reject the concurrent callback"
    );
    release.store(true, std::memory_order_release);
    gate_holder.join();

    expect(
        ingress.admit_callback(input, 200) ==
            sdr_hackrf::HackrfRxAdmissionResult::Admitted,
        "post-contention callback was not admitted"
    );
    sdr_hackrf::HackrfRxLease lease;
    expect(ingress.try_pop(lease), "post-contention block was not available");
    expect(lease.metadata().source_sequence == 1U, "contention sequence was hidden");
    expect(
        sdr_core::has_flag(lease.metadata().quality_flags, sdr_core::QualityFlag::IqDropped),
        "next later sequence did not carry IqDropped"
    );
}

void test_concurrent_callback_accounting_remains_exact() {
    sdr_hackrf::HackrfRxIngress ingress(config(8U, 6U, 32U));
    constexpr std::uint32_t thread_count = 4U;
    constexpr std::uint32_t callbacks_per_thread = 2'000U;
    std::atomic<std::uint64_t> admitted{};
    std::atomic<std::uint64_t> contended{};
    std::atomic<std::uint64_t> pool_exhausted{};
    std::atomic<std::uint64_t> queue_full{};
    std::atomic<bool> producers_done{};
    std::thread consumer([&] {
        sdr_hackrf::HackrfRxLease lease;
        while (!producers_done.load(std::memory_order_acquire) ||
               ingress.metrics().ready_depth != 0U) {
            if (ingress.try_pop(lease)) {
                lease.reset();
            } else {
                std::this_thread::yield();
            }
        }
    });
    std::vector<std::thread> producers;
    for (std::uint32_t thread_index = 0U; thread_index < thread_count; ++thread_index) {
        producers.emplace_back([&, thread_index] {
            const auto input = bytes(32U, static_cast<std::uint8_t>(thread_index));
            for (std::uint32_t index = 0U; index < callbacks_per_thread; ++index) {
                switch (ingress.admit_callback(
                    input,
                    1'000 + static_cast<std::int64_t>(index)
                )) {
                case sdr_hackrf::HackrfRxAdmissionResult::Admitted:
                    admitted.fetch_add(1U, std::memory_order_relaxed);
                    break;
                case sdr_hackrf::HackrfRxAdmissionResult::LockContended:
                    contended.fetch_add(1U, std::memory_order_relaxed);
                    break;
                case sdr_hackrf::HackrfRxAdmissionResult::PoolExhausted:
                    pool_exhausted.fetch_add(1U, std::memory_order_relaxed);
                    break;
                case sdr_hackrf::HackrfRxAdmissionResult::QueueFull:
                    queue_full.fetch_add(1U, std::memory_order_relaxed);
                    break;
                default:
                    throw std::runtime_error("valid concurrent callback returned invalid status");
                }
            }
        });
    }
    for (auto& producer : producers) {
        producer.join();
    }
    producers_done.store(true, std::memory_order_release);
    consumer.join();

    const auto total = static_cast<std::uint64_t>(thread_count) * callbacks_per_thread;
    const auto metrics = ingress.metrics();
    expect(metrics.callbacks_total == total, "concurrent callback total mismatch");
    expect(
        admitted.load() + contended.load() + pool_exhausted.load() + queue_full.load() == total,
        "concurrent result partition is not exact"
    );
    expect(metrics.blocks_admitted == admitted.load(), "concurrent admitted mismatch");
    expect(metrics.lock_contention_drops == contended.load(), "contention mismatch");
    expect(metrics.pool_exhaustion_drops == pool_exhausted.load(), "pool mismatch");
    expect(metrics.queue_full_drops == queue_full.load(), "queue mismatch");
    expect(
        metrics.loss_events == contended.load() + pool_exhausted.load() + queue_full.load(),
        "concurrent loss partition mismatch"
    );
    expect(metrics.ready_depth == 0U && metrics.slots_in_use == 0U, "stress leaked slots");
    expect(metrics.slots_high_water <= metrics.slot_capacity, "slot bound exceeded");
    expect(metrics.ready_high_water <= metrics.ready_capacity, "queue bound exceeded");
}

}  // namespace

int main() {
    try {
        test_config_fail_closed();
        test_ci8_copy_metadata_and_lease_lifetime();
        test_queue_full_exact_loss_and_discontinuity();
        test_pool_exhaustion_from_retained_lease();
        test_callback_admission_allocates_nothing_after_construction();
        test_malformed_oversized_and_stop_accounting();
        test_shutdown_order_and_explicit_abandon();
        test_shutdown_reports_vendor_failures_without_skipping_cleanup();
        test_shutdown_timeout_refuses_close_and_exit();
        test_concurrent_loss_marks_only_the_next_later_sequence();
        test_concurrent_callback_accounting_remains_exact();
        std::cout << "R11-G HackRF RX ingress OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
