#include "sdr_pluto/fixed_band_engine.hpp"

#include <algorithm>

#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <thread>

namespace {

[[nodiscard]] sdr_pluto::FixedBandConfig config(
    const double center_hz = 2'450'000'000.0,
    const std::uint32_t queue_capacity = 16U
) {
    sdr_pluto::FixedBandConfig result;
    result.device = {
        .source_id = "p07-mock",
        .context_uri = "usb:mock",
        .center_frequency_hz = center_hz,
        .sample_rate_hz = 3'000'000.0,
        .analog_bandwidth_hz = 1'500'000.0,
        .gain_mode = sdr_core::GainMode::Manual,
        .manual_gain_db = 20.0,
        .channel_index = 0U,
        .buffer_samples = 4096U,
    };
    result.dsp = {
        .fft_size = 1024U,
        .hop_size = 512U,
        .window = sdr_core::WindowType::Hann,
        .detector = sdr_core::DetectorType::Sample,
        .unit = sdr_core::SpectrumUnit::DbfsBin,
        .precision_mode = sdr_core::PrecisionMode::AccurateF32F64Accum,
        .batch_size = 4U,
        .averaging_frames = 1U,
    };
    result.acquisition_queue_capacity = queue_capacity;
    result.spectrum_queue_capacity = 2U;
    result.snapshot_rate_hz = 60.0;
    result.discard_blocks_after_start = 2U;
    return result;
}

[[nodiscard]] bool wait_for_frames(
    sdr_pluto::FixedBandEngine& engine,
    const std::uint64_t minimum,
    const std::chrono::milliseconds timeout = std::chrono::seconds(3)
) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (engine.metrics().engine.fft_frames_computed >= minimum) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return false;
}

}  // namespace

int main() {
    try {
        sdr_pluto::FixedBandEngine engine("usb:mock");
        const auto applied = engine.configure(config());
        if (applied.center_frequency_hz != 2'450'000'000.0 ||
            applied.sample_rate_hz != 3'000'000.0) {
            return 1;
        }
        engine.start();
        if (!wait_for_frames(engine, 8U)) {
            return 2;
        }
        // Slow/absent snapshot polling must not stop analytical FFTs.
        const auto before = engine.metrics().engine.fft_frames_computed;
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        const auto after = engine.metrics();
        if (after.engine.fft_frames_computed <= before ||
            after.spectrum_queue.depth > after.spectrum_queue.capacity ||
            after.engine.fft_frames_dropped != 0U) {
            return 3;
        }
        const auto frames = engine.poll_spectrum_frames(0U);
        if (frames.empty() || frames.back().fft_size != 1024U ||
            frames.back().unit != sdr_core::SpectrumUnit::DbfsBin ||
            frames.back().frequencies_hz->size() != 1024U ||
            frames.back().values->size() != 1024U) {
            return 4;
        }
        engine.stop();
        if (engine.state() != sdr_core::EngineState::Stopped ||
            engine.streaming()) {
            return 5;
        }

        const auto next = config(2'451'000'000.0);
        const auto reapplied = engine.reconfigure(next);
        if (reapplied.center_frequency_hz != 2'451'000'000.0 ||
            reapplied.config_generation <= applied.config_generation) {
            return 6;
        }
        engine.start();
        if (!wait_for_frames(engine, 4U)) {
            return 7;
        }
        const auto post_reconfigure = engine.poll_spectrum_frames(0U);
        if (post_reconfigure.empty() ||
            post_reconfigure.back().config_generation !=
                reapplied.config_generation ||
            post_reconfigure.back().center_frequency_hz !=
                reapplied.center_frequency_hz) {
            return 8;
        }
        engine.stop();

        // Lifecycle repetition catches stale queue stop tokens/thread reuse.
        for (int cycle = 0; cycle < 100; ++cycle) {
            static_cast<void>(engine.configure(config(
                2'450'000'000.0 + static_cast<double>(cycle)
            )));
            engine.start();
            if (!wait_for_frames(engine, 1U)) {
                return 9;
            }
            engine.stop();
        }

#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_SHORT_READ", "1");
#else
        setenv("SDR_MOCK_LIBIIO_SHORT_READ", "1", 1);
#endif
        static_cast<void>(engine.configure(config()));
        engine.start();
        if (!wait_for_frames(engine, 1U)) {
            return 10;
        }
        engine.stop();
#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_SHORT_READ", "");
#else
        unsetenv("SDR_MOCK_LIBIIO_SHORT_READ");
#endif
        const auto loss = engine.metrics();
        if (loss.device.short_reads == 0U ||
            loss.device.estimated_dropped_samples == 0U) {
            return 11;
        }

        // Unpaced producer + tiny queue: loss must be bounded and visible,
        // never converted into unbounded backlog or silent FFT omission.
#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "");
#else
        unsetenv("SDR_MOCK_LIBIIO_REFILL_DELAY_MS");
#endif
        auto overflow = config(2'450'000'000.0, 1U);
        overflow.dsp.fft_size = 32768U;
        overflow.dsp.hop_size = 32768U;
        overflow.dsp.batch_size = 1U;
        static_cast<void>(engine.configure(overflow));
        engine.start();
        const auto overflow_deadline =
            std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (engine.metrics().engine.iq_blocks_dropped == 0U &&
               std::chrono::steady_clock::now() < overflow_deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        engine.stop();
#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "1");
#else
        setenv("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "1", 1);
#endif
        const auto overflow_metrics = engine.metrics();
        if (overflow_metrics.engine.iq_blocks_dropped == 0U ||
            overflow_metrics.acquisition_queue.depth >
                overflow_metrics.acquisition_queue.capacity) {
            return 12;
        }
        const auto overflow_events = engine.poll_events(0U);
        bool saw_overflow = false;
        for (const auto& event : overflow_events) {
            if (event.code == "acquisition_overflow") {
                saw_overflow = true;
            }
        }
        if (!saw_overflow) {
            return 13;
        }

#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_FAIL", "1");
#else
        setenv("SDR_MOCK_LIBIIO_REFILL_FAIL", "1", 1);
#endif
        auto failure = config();
        failure.event_queue_capacity = 1U;
        static_cast<void>(engine.configure(failure));
        engine.start();
        const auto failure_deadline =
            std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (engine.state() != sdr_core::EngineState::Error &&
               std::chrono::steady_clock::now() < failure_deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        const auto failed_metrics = engine.metrics();
        if (failed_metrics.state != sdr_core::EngineState::Error) {
            return 14;
        }
        engine.join();
        if (!engine.metrics().has_error) {
            return 15;
        }
#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_FAIL", "");
#else
        unsetenv("SDR_MOCK_LIBIIO_REFILL_FAIL");
#endif
        const auto failure_events = engine.poll_events(0U);
        bool saw_failure = false;
        for (const auto& event : failure_events) {
            if (event.code == "acquisition_failure" &&
                event.severity == sdr_core::EventSeverity::Critical) {
                saw_failure = true;
            }
        }
        if (!saw_failure) {
            return 16;
        }

        static_cast<void>(engine.configure(config()));
        engine.disconnect();
        if (engine.connected() ||
            engine.state() != sdr_core::EngineState::Stopped) {
            return 17;
        }
        bool config_invalidated = false;
        try {
            static_cast<void>(engine.config());
        } catch (const sdr_core::ConfigurationError&) {
            config_invalidated = true;
        }
        if (!config_invalidated) {
            return 18;
        }
#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
        {
            sdr_pluto::FixedBandEngine roomy_events("usb:mock");
            auto roomy_config = config();
            roomy_config.event_queue_capacity = 4U;
            static_cast<void>(roomy_events.configure(roomy_config));
            roomy_events.emit_diagnostic_for_test(
                sdr_core::EventSeverity::Critical,
                "critical_one",
                "first critical event"
            );
            roomy_events.emit_diagnostic_for_test(
                sdr_core::EventSeverity::Critical,
                "critical_two",
                "second critical event"
            );
            const auto roomy_metrics = roomy_events.metrics();
            const auto roomy_result = roomy_events.poll_events(0U);
            const auto critical_count = std::count_if(
                roomy_result.begin(),
                roomy_result.end(),
                [](const sdr_core::DiagnosticEvent& event) {
                    return event.code == "critical_one" ||
                           event.code == "critical_two";
                }
            );
            if (roomy_metrics.diagnostic_events_lost != 0U ||
                critical_count != 2) {
                return 19;
            }
            roomy_events.disconnect();
        }
        {
            sdr_pluto::FixedBandEngine saturated_events("usb:mock");
            auto saturated_config = config();
            saturated_config.event_queue_capacity = 1U;
            static_cast<void>(saturated_events.configure(saturated_config));
            saturated_events.emit_diagnostic_for_test(
                sdr_core::EventSeverity::Critical,
                "critical_evicted",
                "critical event that must be counted as lost"
            );
            saturated_events.emit_diagnostic_for_test(
                sdr_core::EventSeverity::Critical,
                "critical_preserved",
                "latest critical event must survive"
            );
            const auto saturated_metrics = saturated_events.metrics();
            const auto saturated_result = saturated_events.poll_events(0U);
            const bool saw_evicted = std::any_of(
                saturated_result.begin(),
                saturated_result.end(),
                [](const sdr_core::DiagnosticEvent& event) {
                    return event.code == "critical_evicted";
                }
            );
            const bool saw_preserved = std::any_of(
                saturated_result.begin(),
                saturated_result.end(),
                [](const sdr_core::DiagnosticEvent& event) {
                    return event.code == "critical_preserved";
                }
            );
            if (saturated_metrics.diagnostic_events_lost != 1U ||
                saw_evicted || !saw_preserved) {
                return 20;
            }
            saturated_events.disconnect();
        }
#endif

        std::cout << "P07 fixed-band native pipeline passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 99;
    }
}
