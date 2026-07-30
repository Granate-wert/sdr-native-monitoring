#include "sdr_pluto/pluto_backend.hpp"
#include "sdr_core/errors.hpp"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <limits>
#include <string>
#include <thread>
#include <vector>

namespace {
sdr_core::DeviceConfig config(const std::string& uri, const double rate = 3'000'000.0) {
    return {
        .source_id = "p06-mock",
        .context_uri = uri,
        .center_frequency_hz = 2'450'000'000.0,
        .sample_rate_hz = rate,
        .analog_bandwidth_hz = 1'500'000.0,
        .gain_mode = sdr_core::GainMode::Manual,
        .manual_gain_db = 20.0,
        .channel_index = 0U,
        .buffer_samples = 1024U,
        .schema_version = sdr_core::contract_schema_version,
    };
}

struct MockHooks final {
    using void_fn = void (*)();
    using int_fn = int (*)();
    HMODULE module{};
    void_fn reset{};
    int_fn entered{};
    void_fn release{};
    int_fn destroyed{};

    MockHooks() {
        const char* path = std::getenv("LIBIIO_DLL_PATH");
        if (path == nullptr) throw std::runtime_error("LIBIIO_DLL_PATH is missing");
        module = LoadLibraryA(path);
        if (module == nullptr) throw std::runtime_error("mock libiio load failed");
        reset = reinterpret_cast<void_fn>(GetProcAddress(module, "mock_iio_reset_cancel_race"));
        entered = reinterpret_cast<int_fn>(GetProcAddress(module, "mock_iio_cancel_entered"));
        release = reinterpret_cast<void_fn>(GetProcAddress(module, "mock_iio_release_cancel"));
        destroyed = reinterpret_cast<int_fn>(GetProcAddress(module, "mock_iio_destroyed_during_cancel"));
        if (reset == nullptr || entered == nullptr || release == nullptr || destroyed == nullptr) {
            throw std::runtime_error("mock cancel-race hooks are missing");
        }
    }
    ~MockHooks() { if (module != nullptr) FreeLibrary(module); }
};
}

int main() {
    try {
        const auto runtime = sdr_pluto::runtime_info();
        if (!runtime.available || runtime.major != 0U || runtime.minor != 26U) return 1;
        const auto contexts = sdr_pluto::scan_contexts();
        if (contexts.size() != 1U || contexts.front().uri != "usb:mock") return 2;
        const auto probe = sdr_pluto::probe_context("usb:mock");
        if (probe.phy_device_id.empty() || probe.rx_stream_device_id.empty()) return 3;

        // Exact-name candidate without the required control attribute must fall back structurally.
        _putenv_s("SDR_MOCK_LIBIIO_EXACT_WRONG", "1");
        sdr_pluto::PlutoDevice fallback_device("usb:mock");
        static_cast<void>(fallback_device.configure(config("usb:mock")));
        fallback_device.disconnect();
        _putenv_s("SDR_MOCK_LIBIIO_EXACT_WRONG", "");

        sdr_pluto::PlutoDevice device("usb:mock");
        const auto capabilities = device.capabilities();
        if (capabilities.tuning_range_hz.minimum != 70'000'000.0 ||
            capabilities.sample_rate_ranges_hz.front().minimum != 2'083'333.0 ||
            capabilities.analog_bandwidth_ranges_hz.front().minimum != 200'000.0) return 4;
        const auto applied = device.configure(config("usb:mock"));
        if (applied.center_frequency_hz != 2'450'000'000.0 || applied.sample_rate_hz != 3'000'000.0 ||
            applied.analog_bandwidth_hz != 1'500'000.0 || applied.sample_layout.significant_bits != 12U) return 5;
        device.start_stream();
        const auto first = device.refill();
        const auto second = device.refill();
        if (first.sample_format != sdr_core::SampleFormat::ComplexInt12InInt16Le || first.sample_count != 1024U ||
            first.samples->size() != 4096U || second.source_sequence != first.source_sequence + 1U ||
            second.first_sample_index != first.first_sample_index + first.sample_count) return 6;
        _putenv_s("SDR_MOCK_LIBIIO_SHORT_READ", "1");
        const auto short_block = device.refill();
        _putenv_s("SDR_MOCK_LIBIIO_SHORT_READ", "");
        if (short_block.sample_count != 1023U || short_block.samples->size() != 4092U ||
            !sdr_core::has_flag(short_block.flags, sdr_core::QualityFlag::IqDropped)) return 7;
        // Reported channel extent ends one byte into the last Q sample: never read beyond it.
        _putenv_s("SDR_MOCK_LIBIIO_TRUNCATED_END", "1");
        const auto truncated_block = device.refill();
        _putenv_s("SDR_MOCK_LIBIIO_TRUNCATED_END", "");
        if (truncated_block.sample_count != 1023U || truncated_block.samples->size() != 4092U ||
            !sdr_core::has_flag(truncated_block.flags, sdr_core::QualityFlag::IqDropped)) return 22;
        const auto metrics = device.metrics();
        if (metrics.blocks_received != 4U || metrics.samples_received != 4094U || metrics.refill_errors != 0U ||
            metrics.short_reads != 2U || metrics.estimated_dropped_samples != 2U) return 8;
        device.stop_stream();
        if (device.streaming()) return 8;
        device.disconnect();
        if (device.connected()) return 9;

        sdr_pluto::PlutoDevice pool_device("usb:mock");
        auto pool_config = config("usb:mock");
        pool_config.buffer_samples = 32U;
        static_cast<void>(pool_device.configure(pool_config));
        pool_device.start_stream();
        std::vector<sdr_core::IqBlock> retained;
        for (std::uint32_t index = 0U; index < 8U; ++index) {
            retained.push_back(pool_device.refill());
        }
        bool pool_exhausted = false;
        try { static_cast<void>(pool_device.refill()); }
        catch (const std::runtime_error&) { pool_exhausted = true; }
        const auto exhausted_metrics = pool_device.metrics();
        if (!pool_exhausted || exhausted_metrics.blocks_received != 9U ||
            exhausted_metrics.samples_received != 288U ||
            exhausted_metrics.output_pool_exhaustions != 1U ||
            exhausted_metrics.output_blocks_dropped != 1U ||
            exhausted_metrics.estimated_dropped_samples != 32U) return 20;
        retained.clear();
        const auto after_gap = pool_device.refill();
        if (after_gap.source_sequence != 9U || after_gap.first_sample_index != 288U) return 21;
        pool_device.stop_stream();
        pool_device.disconnect();

        sdr_pluto::PlutoDevice reconnected("usb:mock");
        bool rejected = false;
        try { static_cast<void>(reconnected.configure(config("usb:mock", 1'000'000.0))); }
        catch (const sdr_core::ConfigurationError&) { rejected = true; }
        if (!rejected) return 10;
        auto oversized = config("usb:mock");
        oversized.buffer_samples = std::numeric_limits<std::uint32_t>::max();
        bool oversized_rejected = false;
        try { static_cast<void>(reconnected.configure(oversized)); }
        catch (const sdr_core::ConfigurationError&) { oversized_rejected = true; }
        if (!oversized_rejected) return 11;
        const auto stable = reconnected.configure(config("usb:mock"));

        // Required mode readback may neither disappear nor silently mismatch.
        auto agc = config("usb:mock");
        agc.gain_mode = sdr_core::GainMode::SlowAttack;
        _putenv_s("SDR_MOCK_LIBIIO_GAIN_MODE_READ_FAIL", "1");
        bool readback_failed = false;
        try { static_cast<void>(reconnected.configure(agc)); }
        catch (const std::exception&) { readback_failed = true; }
        _putenv_s("SDR_MOCK_LIBIIO_GAIN_MODE_READ_FAIL", "");
        if (!readback_failed || reconnected.applied_config().config_generation != stable.config_generation) return 23;
        _putenv_s("SDR_MOCK_LIBIIO_GAIN_MODE_MISMATCH", "1");
        bool mismatch_failed = false;
        try { static_cast<void>(reconnected.configure(agc)); }
        catch (const std::exception&) { mismatch_failed = true; }
        _putenv_s("SDR_MOCK_LIBIIO_GAIN_MODE_MISMATCH", "");
        if (!mismatch_failed || reconnected.applied_config().config_generation != stable.config_generation) return 24;

        // P06 output contract is strictly signed int12-in-int16, never mislabeled int16.
        _putenv_s("SDR_MOCK_LIBIIO_FORMAT_16", "1");
        bool int16_rejected = false;
        try { static_cast<void>(reconnected.configure(config("usb:mock"))); }
        catch (const std::exception&) { int16_rejected = true; }
        _putenv_s("SDR_MOCK_LIBIIO_FORMAT_16", "");
        if (!int16_rejected) return 25;
        _putenv_s("SDR_MOCK_LIBIIO_INVALID_SHIFT", "1");
        bool shift_rejected = false;
        try { static_cast<void>(reconnected.configure(config("usb:mock"))); }
        catch (const std::exception&) { shift_rejected = true; }
        _putenv_s("SDR_MOCK_LIBIIO_INVALID_SHIFT", "");
        if (!shift_rejected) return 26;
        _putenv_s("SDR_MOCK_LIBIIO_OVERFLOW_SHIFT", "1");
        bool overflow_shift_rejected = false;
        try { static_cast<void>(reconnected.configure(config("usb:mock"))); }
        catch (const std::exception&) { overflow_shift_rejected = true; }
        _putenv_s("SDR_MOCK_LIBIIO_OVERFLOW_SHIFT", "");
        if (!overflow_shift_rejected) return 31;

        auto failing = config("usb:mock", 4'000'000.0);
        failing.manual_gain_db = 100.0;
        bool rolled_back = false;
        try { static_cast<void>(reconnected.configure(failing)); }
        catch (const sdr_core::ConfigurationError&) {
            const auto still_applied = reconnected.applied_config();
            rolled_back = still_applied.config_generation == stable.config_generation &&
                          still_applied.sample_rate_hz == stable.sample_rate_hz;
        }
        if (!rolled_back) return 11;

        // If any rollback write/readback fails, published configuration is invalidated.
        _putenv_s("SDR_MOCK_LIBIIO_ROLLBACK_FAIL", "1");
        bool rollback_invalidated = false;
        try { static_cast<void>(reconnected.configure(failing)); }
        catch (const sdr_core::ConfigurationError&) {
            try { static_cast<void>(reconnected.applied_config()); }
            catch (const sdr_core::ConfigurationError&) { rollback_invalidated = true; }
        }
        _putenv_s("SDR_MOCK_LIBIIO_ROLLBACK_FAIL", "");
        if (!rollback_invalidated) return 27;
        bool start_rejected = false;
        try { reconnected.start_stream(); }
        catch (const sdr_core::ConfigurationError&) { start_rejected = true; }
        if (!start_rejected) return 28;
        static_cast<void>(reconnected.configure(config("usb:mock")));
        reconnected.start_stream();
        reconnected.cancel();
        bool canceled = false;
        try { static_cast<void>(reconnected.refill()); }
        catch (const std::runtime_error&) { canceled = true; }
        if (!canceled || reconnected.metrics().refill_errors != 1U) return 11;
        reconnected.stop_stream();

        // cancel() and stop_stream() synchronize iio_buffer lifetime, not only pointer value.
        MockHooks hooks;
        sdr_pluto::PlutoDevice race_device("usb:mock");
        static_cast<void>(race_device.configure(config("usb:mock")));
        race_device.start_stream();
        _putenv_s("SDR_MOCK_LIBIIO_BLOCK_CANCEL", "1");
        hooks.reset();
        std::thread cancel_thread([&] { race_device.cancel(); });
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (hooks.entered() == 0 && std::chrono::steady_clock::now() < deadline) std::this_thread::yield();
        if (hooks.entered() == 0) { hooks.release(); cancel_thread.join(); return 29; }
        std::thread stop_thread([&] { race_device.stop_stream(); });
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
        const bool destroyed_early = hooks.destroyed() != 0;
        hooks.release();
        cancel_thread.join();
        stop_thread.join();
        _putenv_s("SDR_MOCK_LIBIIO_BLOCK_CANCEL", "");
        if (destroyed_early || hooks.destroyed() != 0) return 30;
        race_device.disconnect();

        bool invalid_uri = false;
        try { sdr_pluto::PlutoDevice invalid("serial:mock"); }
        catch (const std::invalid_argument&) { invalid_uri = true; }
        if (!invalid_uri) return 12;
        std::cout << "P06 mock libiio backend passed" << std::endl;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 100;
    }
}