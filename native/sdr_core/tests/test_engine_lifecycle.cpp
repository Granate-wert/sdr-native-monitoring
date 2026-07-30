#include "sdr_core/engine.hpp"
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

using sdr_core::EngineConfig;
using sdr_core::EngineState;
using sdr_core::SyntheticEngine;

EngineConfig small_config() {
    EngineConfig config;
    config.acquisition_queue_capacity = 16U;
    config.dsp_queue_capacity = 16U;
    config.recorder_queue_capacity = 8U;
    config.pool_block_count = 32U;
    config.block_size_samples = 256U;
    config.snapshot_interval_blocks = 16U;
    return config;
}

template <typename Fn>
void expect_invalid_transition(Fn&& fn, const std::string& message) {
    bool rejected = false;
    try {
        fn();
    } catch (const sdr_core::ConfigurationError&) {
        rejected = true;
    }
    expect(rejected, message);
}

void wait_for_state(
    const SyntheticEngine& engine,
    const EngineState wanted,
    const std::chrono::milliseconds timeout
) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (engine.state() != wanted && std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
}

void test_state_machine_transitions() {
    SyntheticEngine engine;
    expect(engine.state() == EngineState::Created, "initial state must be CREATED");
    expect_invalid_transition([&] { engine.start(); }, "start before configure must fail");
    expect_invalid_transition([&] { engine.request_stop(); }, "stop before start must fail");
    expect_invalid_transition([&] { engine.join(); }, "join before stop must fail");

    auto config = small_config();
    engine.configure(config);
    expect(engine.state() == EngineState::Configured, "configure must enter CONFIGURED");
    expect(engine.config_generation() == 1U, "generation must increment on configure");

    config.pool_block_count = 0U;
    expect_invalid_transition([&] { engine.configure(config); }, "invalid config must fail");
    expect(engine.state() == EngineState::Configured, "failed configure must keep state");

    config = small_config();
    engine.configure(config);
    expect(engine.config_generation() == 2U, "reconfigure must increment generation");

    engine.start();
    expect(engine.state() == EngineState::Running, "start must enter RUNNING");
    expect_invalid_transition([&] { engine.start(); }, "double start must fail");
    expect_invalid_transition([&] { engine.configure(config); }, "configure while running must fail");
    expect_invalid_transition([&] { engine.join(); }, "join while running must fail");

    engine.request_stop();
    expect(engine.state() == EngineState::Stopping, "request_stop must enter STOPPING");
    engine.request_stop();  // idempotent
    engine.join();
    expect(engine.state() == EngineState::Stopped, "join must enter STOPPED");
    engine.join();  // idempotent
}

void test_repeated_start_stop() {
    SyntheticEngine engine;
    for (std::uint64_t cycle = 1U; cycle <= 3U; ++cycle) {
        auto config = small_config();
        config.max_blocks = 0U;
        engine.configure(config);
        expect(engine.config_generation() == cycle, "generation mismatch across cycles");
        engine.start();
        std::this_thread::sleep_for(std::chrono::milliseconds(30));
        engine.stop();
        expect(engine.state() == EngineState::Stopped, "stop must end in STOPPED");
    }
}

void test_exact_counters_and_auto_stop() {
    constexpr std::uint64_t total = 20'000U;
    SyntheticEngine engine;
    auto config = small_config();
    config.max_blocks = total;
    engine.configure(config);
    engine.start();
    wait_for_state(engine, EngineState::Stopping, std::chrono::milliseconds(60'000));
    engine.join();
    expect(engine.state() == EngineState::Stopped, "auto-stop must end in STOPPED");

    const auto metrics = engine.metrics();
    expect(metrics.iq_blocks_received == total, "received counter mismatch");
    expect(
        metrics.iq_samples_received == total * config.block_size_samples,
        "sample counter mismatch"
    );
    const auto dsp = engine.queue_stats(sdr_core::QueueId::Dsp);
    const auto acquisition = engine.queue_stats(sdr_core::QueueId::Acquisition);
    expect(
        metrics.iq_blocks_received == dsp.popped + metrics.iq_blocks_dropped,
        "received != consumed + dropped: loss accounting mismatch"
    );
    // Exact decomposition is intentionally an inequality: blocks that were
    // in-flight inside the mover/producer at shutdown are counted in
    // iq_blocks_dropped but do not belong to any single queue's counters.
    const std::uint64_t queued_drops = acquisition.dropped + dsp.dropped;
    const std::uint64_t abandoned = acquisition.abandoned + dsp.abandoned;
    expect(
        metrics.iq_blocks_dropped >= queued_drops + abandoned,
        "drop counters must cover queue drops plus abandoned blocks"
    );
    expect(metrics.acquisition_queue_depth == 0U, "acquisition queue not drained at stop");
    expect(metrics.dsp_queue_depth == 0U, "dsp queue not drained at stop");
}

void test_recorder_tee_exact_accounting() {
    constexpr std::uint64_t total = 50'000U;
    SyntheticEngine engine;
    auto config = small_config();
    config.recorder_enabled = true;
    config.recorder_queue_capacity = 2U;
    config.acquisition_queue_capacity = 64U;
    config.dsp_queue_capacity = 64U;
    config.pool_block_count = 128U;
    config.max_blocks = total;
    engine.configure(config);
    engine.start();
    wait_for_state(engine, EngineState::Stopping, std::chrono::milliseconds(60'000));
    engine.join();
    expect(engine.state() == EngineState::Stopped, "recorder run must stop cleanly");

    const auto metrics = engine.metrics();
    const auto acquisition = engine.queue_stats(sdr_core::QueueId::Acquisition);
    const auto dsp = engine.queue_stats(sdr_core::QueueId::Dsp);
    const auto recorder = engine.queue_stats(sdr_core::QueueId::Recorder);
    // Main path: every produced block is consumed or exactly counted.
    expect(
        metrics.iq_blocks_received == dsp.popped + metrics.iq_blocks_dropped,
        "main-path accounting mismatch with recorder tee"
    );
    // Recorder tee: every block popped from acquisition was offered to the
    // recorder queue exactly once (pushed or dropped). At most one block may
    // be in-flight inside the mover at shutdown (offered to neither side).
    const std::uint64_t tee_attempts = recorder.pushed + recorder.dropped;
    expect(
        acquisition.popped == tee_attempts || acquisition.popped == tee_attempts + 1U,
        "recorder tee accounting mismatch"
    );
    expect(
        recorder.pushed == recorder.popped + recorder.abandoned,
        "recorder queue conservation mismatch"
    );
    // Recorder-stream drops are observable through queue stats and events,
    // and must NOT pollute the main-path iq_blocks_dropped.
    if (recorder.dropped > 0U) {
        const auto events = engine.poll_events(0U);
        bool saw_recorder_overflow = false;
        for (const auto& event : events) {
            saw_recorder_overflow =
                saw_recorder_overflow || event.code == "recorder_overflow";
        }
        expect(saw_recorder_overflow, "recorder overflow must be observable");
    }
}

void test_recorder_block_policy_backpressures_without_unaccounted_loss() {
    constexpr std::uint64_t total = 50'000U;
    SyntheticEngine engine;
    auto config = small_config();
    config.recorder_enabled = true;
    config.recorder_overflow = sdr_core::OverflowPolicy::Block;
    config.recorder_stop_on_overflow = false;
    config.recorder_queue_capacity = 1U;
    config.acquisition_queue_capacity = 64U;
    config.dsp_queue_capacity = 64U;
    config.pool_block_count = 128U;
    config.max_blocks = total;
    config.blocks_per_second = 0U;
    engine.configure(config);
    engine.start();
    wait_for_state(engine, EngineState::Stopping, std::chrono::milliseconds(60'000));
    engine.join();

    const auto acquisition = engine.queue_stats(sdr_core::QueueId::Acquisition);
    const auto recorder = engine.queue_stats(sdr_core::QueueId::Recorder);
    expect(recorder.dropped == 0U, "BLOCK recorder policy must not drop on Full");
    expect(
        acquisition.popped == recorder.pushed || acquisition.popped == recorder.pushed + 1U,
        "BLOCK recorder path contains unaccounted acquisition blocks"
    );
    expect(
        recorder.pushed == recorder.popped + recorder.abandoned,
        "BLOCK recorder queue conservation mismatch"
    );
}

void test_recorder_stop_on_overflow() {
    SyntheticEngine engine;
    auto config = small_config();
    config.recorder_enabled = true;
    config.recorder_overflow = sdr_core::OverflowPolicy::Block;
    config.recorder_stop_on_overflow = true;
    config.recorder_queue_capacity = 1U;
    config.acquisition_queue_capacity = 64U;
    config.dsp_queue_capacity = 64U;
    config.pool_block_count = 128U;
    config.blocks_per_second = 0U;  // unlimited: the recorder queue will fill
    engine.configure(config);
    engine.start();
    wait_for_state(engine, EngineState::Stopping, std::chrono::milliseconds(60'000));
    engine.join();
    expect(engine.state() == EngineState::Stopped, "stop_on_overflow must stop the engine");

    const auto events = engine.poll_events(0U);
    bool saw_recorder_error = false;
    for (const auto& event : events) {
        if (event.code == "recorder_overflow" &&
            event.severity == sdr_core::EventSeverity::Error) {
            saw_recorder_error = true;
        }
    }
    expect(saw_recorder_error, "stop_on_overflow must emit an Error event");
}

void test_stop_while_consumer_blocked() {
    SyntheticEngine engine;
    auto config = small_config();
    config.blocks_per_second = 2U;  // consumer waits in pop almost all the time
    engine.configure(config);
    engine.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    const auto started = std::chrono::steady_clock::now();
    engine.stop();
    const auto elapsed = std::chrono::steady_clock::now() - started;
    expect(engine.state() == EngineState::Stopped, "stop must end in STOPPED");
    expect(
        elapsed < std::chrono::seconds(5),
        "stop while consumer blocked took too long (missed wakeup)"
    );
}

void test_stop_while_system_saturated() {
    SyntheticEngine engine;
    auto config = small_config();
    config.acquisition_queue_capacity = 4U;
    config.dsp_queue_capacity = 4U;
    config.pool_block_count = 8U;
    config.acquisition_overflow = sdr_core::OverflowPolicy::Block;
    config.dsp_overflow = sdr_core::OverflowPolicy::Block;
    config.blocks_per_second = 0U;  // unlimited producer: queues and pool stay full
    engine.configure(config);
    engine.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    const auto started = std::chrono::steady_clock::now();
    engine.stop();
    const auto elapsed = std::chrono::steady_clock::now() - started;
    expect(engine.state() == EngineState::Stopped, "stop must end in STOPPED");
    expect(elapsed < std::chrono::seconds(5), "stop under saturation took too long");
    const auto metrics = engine.metrics();
    expect(
        metrics.iq_blocks_received ==
            engine.queue_stats(sdr_core::QueueId::Dsp).popped + metrics.iq_blocks_dropped,
        "loss accounting mismatch after saturated stop"
    );
}

void test_destructor_during_active_run() {
    {
        SyntheticEngine engine;
        engine.configure(small_config());
        engine.start();
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }  // destructor must stop and join without hanging
}

void test_events_and_snapshots() {
    SyntheticEngine engine;
    auto config = small_config();
    config.acquisition_queue_capacity = 2U;
    config.dsp_queue_capacity = 2U;
    config.pool_block_count = 4U;
    config.max_blocks = 50'000U;
    engine.configure(config);
    engine.start();
    wait_for_state(engine, EngineState::Stopping, std::chrono::milliseconds(60'000));
    engine.join();

    const auto events = engine.poll_events(0U);
    bool saw_configured = false;
    bool saw_started = false;
    bool saw_stopped = false;
    bool saw_overflow = false;
    for (const auto& event : events) {
        saw_configured = saw_configured || event.code == "engine_configured";
        saw_started = saw_started || event.code == "engine_started";
        saw_stopped = saw_stopped || event.code == "engine_stopped";
        saw_overflow = saw_overflow || event.code.find("overflow") != std::string::npos;
    }
    expect(saw_configured, "configured event missing");
    expect(saw_started, "started event missing");
    expect(saw_stopped, "stopped event missing");
    expect(saw_overflow, "overflow must be observable as an event");

    const auto snapshots = engine.poll_snapshots(0U);
    expect(!snapshots.empty(), "periodic metrics snapshots missing");
    expect(
        snapshots.size() <= engine.config().snapshot_queue_capacity,
        "snapshot queue must stay bounded (latest-wins)"
    );
    expect(
        engine.metrics().spectrum_snapshots_emitted >= snapshots.size(),
        "emitted snapshot counter mismatch"
    );
}

void test_concurrent_metrics_reads() {
    SyntheticEngine engine;
    auto config = small_config();
    config.max_blocks = 100'000U;
    engine.configure(config);
    engine.start();
    std::atomic<bool> ok{true};
    std::vector<std::thread> readers;
    for (int index = 0; index < 4; ++index) {
        readers.emplace_back([&] {
            while (engine.state() == EngineState::Running) {
                const auto metrics = engine.metrics();
                const auto stats = engine.queue_stats(sdr_core::QueueId::Acquisition);
                if (metrics.acquisition_queue_depth > engine.config().acquisition_queue_capacity ||
                    stats.high_water > stats.capacity) {
                    ok.store(false);
                }
                sdr_core::validate(metrics);
            }
        });
    }
    wait_for_state(engine, EngineState::Stopping, std::chrono::milliseconds(60'000));
    engine.join();
    for (auto& reader : readers) {
        reader.join();
    }
    expect(ok.load(), "metrics snapshot violated bounds under concurrent read");
    expect(engine.state() == EngineState::Stopped, "final state mismatch");
}

}  // namespace

int main() {
    try {
        test_state_machine_transitions();
        test_repeated_start_stop();
        test_exact_counters_and_auto_stop();
        test_recorder_tee_exact_accounting();
        test_recorder_block_policy_backpressures_without_unaccounted_loss();
        test_recorder_stop_on_overflow();
        test_stop_while_consumer_blocked();
        test_stop_while_system_saturated();
        test_destructor_during_active_run();
        test_events_and_snapshots();
        test_concurrent_metrics_reads();
        std::cout << "P04 engine lifecycle OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
