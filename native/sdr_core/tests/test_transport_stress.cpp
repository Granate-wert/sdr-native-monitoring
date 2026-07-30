#include "sdr_core/engine.hpp"
#include "sdr_core/errors.hpp"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

#if defined(_WIN32)
#include <windows.h>

#include <psapi.h>
#endif

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

// Working set of the current process in bytes, or 0 when the platform does
// not expose it. Used only as supporting evidence; the authoritative bounds
// are pool/queue high-water marks.
std::uint64_t working_set_bytes() {
#if defined(_WIN32)
    PROCESS_MEMORY_COUNTERS_EX counters{};
    if (GetProcessMemoryInfo(
            GetCurrentProcess(),
            reinterpret_cast<PROCESS_MEMORY_COUNTERS*>(&counters),
            sizeof(counters)
        ) != 0) {
        return static_cast<std::uint64_t>(counters.WorkingSetSize);
    }
#endif
    return 0U;
}

}  // namespace

int main() {
    try {
        constexpr std::uint64_t total_blocks = 1'000'000U;
        sdr_core::SyntheticEngine engine;
        sdr_core::EngineConfig config;
        config.acquisition_queue_capacity = 64U;
        config.dsp_queue_capacity = 64U;
        config.recorder_queue_capacity = 8U;
        config.pool_block_count = 128U;
        config.block_size_samples = 1024U;
        config.snapshot_interval_blocks = 4096U;
        config.max_blocks = total_blocks;
        engine.configure(config);

        const std::uint64_t memory_before = working_set_bytes();
        const auto started = std::chrono::steady_clock::now();

        engine.start();

        // Concurrent metrics readers must not disturb the data plane.
        std::atomic<bool> readers_ok{true};
        std::vector<std::thread> readers;
        for (int index = 0; index < 2; ++index) {
            readers.emplace_back([&] {
                while (engine.state() == sdr_core::EngineState::Running) {
                    const auto metrics = engine.metrics();
                    if (metrics.acquisition_queue_depth > 64U || metrics.dsp_queue_depth > 64U ||
                        metrics.recorder_queue_depth > 8U) {
                        readers_ok.store(false);
                    }
                    static_cast<void>(engine.pool_stats());
                }
            });
        }

        const auto deadline = std::chrono::steady_clock::now() + std::chrono::minutes(8);
        while (engine.state() == sdr_core::EngineState::Running &&
               std::chrono::steady_clock::now() < deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        engine.join();
        for (auto& reader : readers) {
            reader.join();
        }

        const auto elapsed = std::chrono::steady_clock::now() - started;
        const double seconds = std::chrono::duration<double>(elapsed).count();
        const std::uint64_t memory_after = working_set_bytes();

        const auto metrics = engine.metrics();
        const auto pool = engine.pool_stats();
        const auto acquisition = engine.queue_stats(sdr_core::QueueId::Acquisition);
        const auto dsp = engine.queue_stats(sdr_core::QueueId::Dsp);

        std::cout << "P04 transport stress:\n"
                  << "  blocks received: " << metrics.iq_blocks_received << '\n'
                  << "  blocks dropped: " << metrics.iq_blocks_dropped << '\n'
                  << "  dsp consumed: " << dsp.popped << '\n'
                  << "  snapshots emitted: " << metrics.spectrum_snapshots_emitted << '\n'
                  << "  wall time s: " << seconds << '\n'
                  << "  throughput blocks/s: "
                  << static_cast<std::uint64_t>(total_blocks / seconds) << '\n'
                  << "  pool high_water: " << pool.high_water << "/" << pool.capacity << '\n'
                  << "  acquisition high_water: " << acquisition.high_water << "/"
                  << acquisition.capacity << '\n'
                  << "  dsp high_water: " << dsp.high_water << "/" << dsp.capacity << '\n'
                  << "  working set before/after bytes: " << memory_before << " / "
                  << memory_after << '\n';

        expect(readers_ok.load(), "queue depth exceeded capacity during stress");
        expect(engine.state() == sdr_core::EngineState::Stopped, "stress run did not stop");
        expect(metrics.iq_blocks_received == total_blocks, "not all blocks were produced");
        expect(
            metrics.iq_blocks_received == dsp.popped + metrics.iq_blocks_dropped,
            "loss accounting mismatch over 1M blocks"
        );
        expect(pool.high_water <= pool.capacity, "pool exceeded its capacity");
        expect(acquisition.high_water <= acquisition.capacity, "acquisition overflow bound");
        expect(dsp.high_water <= dsp.capacity, "dsp overflow bound");
        expect(pool.acquired == pool.returned, "pool blocks leaked");
        expect(pool.in_use == 0U, "pool blocks still in use after stop");
        if (memory_before != 0U && memory_after != 0U) {
            constexpr std::uint64_t growth_budget = 64ULL * 1024ULL * 1024ULL;
            expect(
                memory_after < memory_before + growth_budget,
                "working set grew beyond the bounded-memory budget"
            );
        }

        std::cout << "P04 transport stress OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
