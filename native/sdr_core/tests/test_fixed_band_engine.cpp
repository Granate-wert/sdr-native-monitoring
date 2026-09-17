#include "sdr_pluto/fixed_band_engine.hpp"

#include "sdr_core/errors.hpp"
#include "sdr_core/recording_writer.hpp"

#include <algorithm>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <thread>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <psapi.h>
#include <tlhelp32.h>
#endif

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

[[nodiscard]] bool wait_for_sweep_lines(
    sdr_pluto::FixedBandEngine& engine,
    const std::uint64_t minimum,
    const std::chrono::milliseconds timeout = std::chrono::seconds(3)
) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (engine.metrics().completed_sweep_lines >= minimum) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return false;
}

[[nodiscard]] std::uint64_t working_set_bytes() {
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

[[nodiscard]] std::uint32_t current_process_thread_count() {
#if defined(_WIN32)
    const HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0U);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return 0U;
    }
    THREADENTRY32 entry{};
    entry.dwSize = sizeof(entry);
    std::uint32_t count = 0U;
    if (Thread32First(snapshot, &entry) != 0) {
        do {
            if (entry.th32OwnerProcessID == GetCurrentProcessId()) {
                ++count;
            }
        } while (Thread32Next(snapshot, &entry) != 0);
    }
    CloseHandle(snapshot);
    return count;
#else
    return 0U;
#endif
}

[[nodiscard]] bool is_expected_lifecycle_race(
    const std::exception_ptr& failure
) {
    if (!failure) {
        return true;
    }
    try {
        std::rethrow_exception(failure);
    } catch (const sdr_core::ConfigurationError&) {
        return true;
    } catch (...) {
        return false;
    }
}

}  // namespace

int main() {
    try {
        {
            auto oversized = config();
            oversized.dsp.fft_size = 262144U;
            oversized.dsp.hop_size = 131072U;
            oversized.persistence = {
                .enabled = true,
                .mode = sdr_core::PersistenceMode::ExponentialDecay,
                .window_frames = 500U,
                .half_life_seconds = 1.0,
                .power_min_db = -140.0,
                .power_max_db = 20.0,
                .power_bins = 4096U,
                .snapshot_rate_hz = 30.0,
            };
            bool rejected = false;
            try {
                sdr_pluto::validate(oversized);
            } catch (const sdr_core::ConfigurationError&) {
                rejected = true;
            }
            if (!rejected) {
                return 21;
            }
        }
        {
            auto blocking_recorder = config();
            blocking_recorder.recorder_enabled = true;
            blocking_recorder.recorder_overflow = sdr_core::OverflowPolicy::Block;
            bool rejected = false;
            try {
                sdr_pluto::validate(blocking_recorder);
            } catch (const sdr_core::ConfigurationError&) {
                rejected = true;
            }
            if (!rejected) {
                return 26;
            }
        }
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

        // R10-D1A: a display span that fits in its declared usable receiver
        // window is emitted as a native completed line per analytical frame.
        // It is independent from the 60 Hz SpectrumFrame snapshot boundary.
        auto continuous = config();
        continuous.continuous_sweep_line = {
            .enabled = true,
            .epoch = 42U,
            .display_start_hz = 2'449'500'000.0,
            .display_stop_hz = 2'450'500'000.0,
            .usable_window_hz = 1'500'000.0,
            .output_queue_capacity = 2U,
        };
        static_cast<void>(engine.configure(continuous));
        engine.start();
        if (!wait_for_sweep_lines(engine, 8U)) {
            return 33;
        }
        const auto continuous_metrics = engine.metrics();
        const auto continuous_lines = engine.poll_sweep_line_frames(0U);
        if (continuous_lines.empty() ||
            continuous_lines.back().state != sdr_core::SweepLineState::Complete ||
            continuous_lines.back().epoch != 42U ||
            continuous_lines.back().frequencies_hz->size() < 2U ||
            continuous_metrics.sweep_line_queue.capacity < 14U ||
            continuous_metrics.completed_sweep_lines < 8U ||
            continuous_metrics.gapped_sweep_lines != 0U) {
            return 34;
        }
        engine.stop();

        auto invalid_continuous_recording = continuous;
        invalid_continuous_recording.recording = {
            .enabled = true,
            .output_uri = "ignored-by-validation",
            .record_iq = true,
            .record_spectrum = false,
            .chunk_samples = 4096U,
            .queue_capacity = 1U,
            .stop_on_overflow = false,
            .schema_version = sdr_core::contract_schema_version,
        };
        bool continuous_recording_rejected = false;
        try {
            sdr_pluto::validate(invalid_continuous_recording);
        } catch (const sdr_core::ConfigurationError&) {
            continuous_recording_rejected = true;
        }
        if (!continuous_recording_rejected) {
            return 35;
        }

        // R08-A: the recorder tee receives native I/Q before DSP, preserves
        // the original buffer/identity and reports bounded recorder-only
        // loss.  No consumer drains this staging queue while streaming;
        // analytical FFTs must still progress.
        auto recorder = config();
        recorder.recorder_enabled = true;
        recorder.recorder_queue_capacity = 1U;
        recorder.recorder_overflow = sdr_core::OverflowPolicy::DropNewest;
        const auto recorder_applied = engine.configure(recorder);
        engine.start();
        if (!wait_for_frames(engine, 16U)) {
            return 27;
        }
        const auto recorder_deadline =
            std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (engine.metrics().recorder_queue_blocks_dropped == 0U &&
               std::chrono::steady_clock::now() < recorder_deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        const auto tee_metrics = engine.metrics();
        engine.stop();
        const auto tee_stopped_metrics = engine.metrics();
        bool reconfigure_rejected = false;
        try {
            static_cast<void>(engine.reconfigure(config(2'450'500'000.0)));
        } catch (const sdr_core::ConfigurationError&) {
            reconfigure_rejected = true;
        }
        const auto retained_iq = engine.poll_recorded_iq_blocks(0U);
        if (tee_metrics.engine.fft_frames_computed <= 16U ||
            tee_metrics.engine.iq_blocks_received < 2U ||
            tee_metrics.source_sequence_discontinuities != 0U ||
            tee_metrics.source_sample_index_discontinuities != 0U ||
            tee_metrics.source_timestamp_regressions != 0U ||
            tee_metrics.source_estimated_timestamp_blocks == 0U ||
            tee_metrics.hardware_overflow_counter_available ||
            tee_metrics.recorder_queue.capacity != 1U ||
            tee_metrics.recorder_queue.depth > tee_metrics.recorder_queue.capacity ||
            tee_metrics.recorder_queue_blocks_dropped == 0U ||
            tee_metrics.recorder_queue_blocks_dropped != tee_metrics.recorder_queue.dropped ||
            tee_stopped_metrics.recorder_queue.depth != 1U ||
            !reconfigure_rejected ||
            retained_iq.size() != 1U ||
            retained_iq.front().samples == nullptr ||
            retained_iq.front().sample_count == 0U ||
            retained_iq.front().config_generation != recorder_applied.config_generation) {
            return 28;
        }

        // R08-B: the native recorder thread drains the R08-A queue into a
        // segmented raw-I/Q artifact.  Final manifest publication happens
        // only after acquisition/DSP/recorder threads have joined.
        const auto recording_root = std::filesystem::temp_directory_path() /
            ("sdr_core_fixed_recording_" + std::to_string(
                std::chrono::steady_clock::now().time_since_epoch().count()
            ));
        std::filesystem::create_directories(recording_root);
        const auto recording_base = recording_root / "capture";
        auto durable_recording = config();
        durable_recording.recording = {
            .enabled = true,
            .output_uri = recording_base.string(),
            .record_iq = true,
            .record_spectrum = false,
            .chunk_samples = 4096U,
            .queue_capacity = 4U,
            .stop_on_overflow = false,
            .schema_version = sdr_core::contract_schema_version,
        };
        static_cast<void>(engine.configure(durable_recording));
        engine.start();
        if (!wait_for_frames(engine, 16U)) {
            return 29;
        }
        engine.stop();
        const auto durable_metrics = engine.metrics();
        const auto durable_manifest = recording_root / "capture.sigmf-meta";
        std::ifstream durable_manifest_input(durable_manifest, std::ios::binary);
        const std::string durable_manifest_text{
            std::istreambuf_iterator<char>(durable_manifest_input),
            std::istreambuf_iterator<char>(),
        };
        const bool durable_ok =
            durable_metrics.recorder_writer_blocks_written != 0U &&
            durable_metrics.recorder_writer_samples_written != 0U &&
            durable_metrics.recorder_writer_bytes_written != 0U &&
            !durable_metrics.recorder_writer_failed &&
            std::filesystem::exists(durable_manifest) &&
            !std::filesystem::exists(recording_root / "capture.sigmf-meta.part") &&
            durable_manifest_text.find("\"completed\":true") != std::string::npos &&
            durable_manifest_text.find("\"recording_type\":\"native_iq_segmented\"") !=
                std::string::npos;
        durable_manifest_input.close();
        if (!durable_ok) {
            std::cerr << "durable recording failed: blocks="
                      << durable_metrics.recorder_writer_blocks_written
                      << " samples=" << durable_metrics.recorder_writer_samples_written
                      << " bytes=" << durable_metrics.recorder_writer_bytes_written
                      << " writer_failed=" << durable_metrics.recorder_writer_failed
                      << " manifest=" << std::filesystem::exists(durable_manifest)
                      << " manifest_text=" << durable_manifest_text << '\n';
            std::filesystem::remove_all(recording_root);
            return 30;
        }
        std::filesystem::remove_all(recording_root);

        // R08-B2: only frames admitted to the normal published-spectrum queue
        // are copied by shared ownership into a separate non-blocking native
        // writer queue.  Disk work therefore cannot enter the DSP thread.
        const auto spectrum_recording_root = std::filesystem::temp_directory_path() /
            ("sdr_core_fixed_spectrum_recording_" + std::to_string(
                std::chrono::steady_clock::now().time_since_epoch().count()
            ));
        std::filesystem::create_directories(spectrum_recording_root);
        const auto spectrum_recording_base = spectrum_recording_root / "capture";
        auto spectrum_recording = config();
        spectrum_recording.recording = {
            .enabled = true,
            .output_uri = spectrum_recording_base.string(),
            .record_iq = true,
            .record_spectrum = true,
            .chunk_samples = 4096U,
            .queue_capacity = 4U,
            .stop_on_overflow = false,
            .schema_version = sdr_core::contract_schema_version,
        };
        static_cast<void>(engine.configure(spectrum_recording));
        engine.start();
        if (!wait_for_frames(engine, 16U)) {
            std::filesystem::remove_all(spectrum_recording_root);
            return 31;
        }
        engine.stop();
        const auto spectrum_recording_metrics = engine.metrics();
        const auto spectrum_manifest = spectrum_recording_root / "capture.sdr-spectrum.meta";
        const auto spectrum_data = spectrum_recording_root / "capture.sdr-spectrum.bin";
        std::ifstream spectrum_manifest_input(spectrum_manifest, std::ios::binary);
        const std::string spectrum_manifest_text{
            std::istreambuf_iterator<char>(spectrum_manifest_input),
            std::istreambuf_iterator<char>(),
        };
        spectrum_manifest_input.close();
        const auto spectrum_recovery =
            sdr_core::scan_native_recording_prefix(spectrum_recording_base);
        const bool spectrum_recording_ok =
            spectrum_recording_metrics.engine.fft_frames_computed >= 16U &&
            spectrum_recording_metrics.spectrum_recorder_queue.capacity == 4U &&
            spectrum_recording_metrics.spectrum_recorder_queue.depth == 0U &&
            spectrum_recording_metrics.spectrum_writer_frames_written != 0U &&
            spectrum_recording_metrics.spectrum_writer_bytes_written != 0U &&
            !spectrum_recording_metrics.spectrum_writer_failed &&
            std::filesystem::exists(spectrum_manifest) &&
            std::filesystem::exists(spectrum_data) &&
            !std::filesystem::exists(
                spectrum_recording_root / "capture.sdr-spectrum.meta.part"
            ) &&
            spectrum_manifest_text.find(
                "\"recording_type\":\"native_spectrum_frames\""
            ) != std::string::npos &&
            spectrum_recovery.spectrum_manifest_final &&
            spectrum_recovery.spectrum_binary_header_valid &&
            spectrum_recovery.spectrum_complete_records != 0U &&
            spectrum_recovery.spectrum_trailing_bytes == 0U;
        if (!spectrum_recording_ok) {
            std::cerr << "spectrum recording failed: frames="
                      << spectrum_recording_metrics.spectrum_writer_frames_written
                      << " bytes="
                      << spectrum_recording_metrics.spectrum_writer_bytes_written
                      << " writer_failed="
                      << spectrum_recording_metrics.spectrum_writer_failed
                      << " queue_depth="
                      << spectrum_recording_metrics.spectrum_recorder_queue.depth
                      << " recovery_records=" << spectrum_recovery.spectrum_complete_records
                      << " recovery_tail=" << spectrum_recovery.spectrum_trailing_bytes
                      << " manifest_text=" << spectrum_manifest_text << '\n';
            std::filesystem::remove_all(spectrum_recording_root);
            return 32;
        }
        std::filesystem::remove_all(spectrum_recording_root);

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
        const auto lifecycle_memory_before = working_set_bytes();
        const auto lifecycle_threads_before = current_process_thread_count();
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
        const auto lifecycle_memory_after = working_set_bytes();
        const auto lifecycle_threads_after = current_process_thread_count();
        if (lifecycle_threads_before != 0U && lifecycle_threads_after > lifecycle_threads_before + 1U) {
            return 21;
        }
        if (lifecycle_memory_before != 0U && lifecycle_memory_after != 0U &&
            lifecycle_memory_after > lifecycle_memory_before + 64U * 1024U * 1024U) {
            return 22;
        }

        // metrics() must use the lock-free/short-lock snapshot path, not wait
        // behind a deliberately long libiio refill. Multiple probes cover one
        // real acquisition call rather than merely an idle engine.
#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "150");
#else
        setenv("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "150", 1);
#endif
        static_cast<void>(engine.configure(config()));
        engine.start();
        std::chrono::milliseconds max_metrics_latency{0};
        for (int probe = 0; probe < 10; ++probe) {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
            const auto metrics_started = std::chrono::steady_clock::now();
            static_cast<void>(engine.metrics());
            const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - metrics_started
            );
            max_metrics_latency = std::max(max_metrics_latency, elapsed);
        }
        engine.stop();
#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "1");
#else
        setenv("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "1", 1);
#endif
        if (max_metrics_latency >= std::chrono::milliseconds(50)) {
            return 23;
        }

        // R10-D6: one 65536-sample I/Q refill at 3 MS/s contains many
        // overlapping FFT1024/hop512 outputs. Spectrum publication is paced
        // by canonical FFT timestamps, not by the one post-refill wall-clock
        // instant, so a 720-Hz request must publish several reduced
        // SpectrumFrames from one I/Q block. The public queue is still
        // capacity two/latest-wins; this test observes scalar counters rather
        // than retaining an unbounded frame history.
#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "30");
#else
        setenv("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "30", 1);
#endif
        auto timestamp_paced = config();
        timestamp_paced.device.buffer_samples = 65536U;
        timestamp_paced.snapshot_rate_hz = 720.0;
        static_cast<void>(engine.configure(timestamp_paced));
        engine.start();
        const auto paced_deadline =
            std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (engine.metrics().engine.spectrum_snapshots_emitted < 8U &&
               std::chrono::steady_clock::now() < paced_deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        const auto paced_metrics = engine.metrics();
        engine.stop();
#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "1");
#else
        setenv("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "1", 1);
#endif
        if (paced_metrics.engine.spectrum_snapshots_emitted < 8U ||
            paced_metrics.engine.spectrum_snapshots_emitted <=
                paced_metrics.device.blocks_received ||
            paced_metrics.engine.fft_frames_computed <
                paced_metrics.engine.spectrum_snapshots_emitted ||
            paced_metrics.spectrum_queue.depth > paced_metrics.spectrum_queue.capacity) {
            return 36;
        }

        // R10-D6 bridge follow-up: the Python-facing path takes at most the
        // latest ready SpectrumFrame. It must atomically drain the existing
        // capacity-two/latest-wins queue, expose the exact native coalescing
        // count, preserve the newest frame metadata, and leave no hidden
        // presentation backlog. This is not an analytical FFT loss path.
        auto latest_drain = config();
        latest_drain.snapshot_rate_hz = 720.0;
        latest_drain.spectrum_queue_capacity = 2U;
        static_cast<void>(engine.configure(latest_drain));
        engine.start();
        const auto latest_drain_deadline =
            std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (engine.metrics().engine.spectrum_snapshots_emitted < 3U &&
               std::chrono::steady_clock::now() < latest_drain_deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        const auto before_latest_drain = engine.metrics();
        const auto latest = engine.drain_latest_spectrum_frame();
        const auto after_latest_drain = engine.metrics();
        if (!latest.frame.has_value() || latest.coalesced_frames + 1U !=
                before_latest_drain.spectrum_queue.depth ||
            latest.frame->config_generation != engine.config_generation() ||
            after_latest_drain.spectrum_queue.depth != 0U ||
            after_latest_drain.spectrum_queue.popped !=
                before_latest_drain.spectrum_queue.popped +
                    latest.coalesced_frames + 1U ||
            after_latest_drain.engine.fft_frames_dropped !=
                before_latest_drain.engine.fft_frames_dropped) {
            engine.stop();
            return 37;
        }
        const auto empty_latest = engine.drain_latest_spectrum_frame();
        if (empty_latest.frame.has_value() || empty_latest.coalesced_frames != 0U) {
            engine.stop();
            return 38;
        }
        engine.stop();

#if defined(SDR_CORE_ENABLE_CUDA)
        // The bridge is backend-neutral. Inject one CUDA batch failure through
        // the complete fixed-band engine, require the documented CPU failover,
        // then consume through the same latest-only API. This complements the
        // selector's frame-level parity test without depending on the
        // one-time fallback flag still being the newest capacity-two frame.
#if defined(_WIN32)
        _putenv_s("SDR_CUDA_FAIL_ON_BATCH", "2");
#else
        setenv("SDR_CUDA_FAIL_ON_BATCH", "2", 1);
#endif
        auto cuda_fallback = config();
        cuda_fallback.backend = sdr_core::ComputeBackendKind::Cuda;
        cuda_fallback.allow_runtime_fallback = true;
        cuda_fallback.snapshot_rate_hz = 720.0;
        cuda_fallback.spectrum_queue_capacity = 2U;
        static_cast<void>(engine.configure(cuda_fallback));
        engine.start();
        const auto fallback_deadline =
            std::chrono::steady_clock::now() + std::chrono::seconds(5);
        while (std::chrono::steady_clock::now() < fallback_deadline) {
            const auto observed = engine.metrics();
            if (observed.backend_fallback_count == 1U &&
                observed.active_backend == sdr_core::ComputeBackendKind::Cpu &&
                observed.spectrum_queue.depth > 0U) {
                break;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        const auto fallback_metrics = engine.metrics();
        const auto fallback_latest = engine.drain_latest_spectrum_frame();
        engine.stop();
#if defined(_WIN32)
        _putenv_s("SDR_CUDA_FAIL_ON_BATCH", "");
#else
        unsetenv("SDR_CUDA_FAIL_ON_BATCH");
#endif
        if (fallback_metrics.backend_fallback_count != 1U ||
            fallback_metrics.backend_switch_count != 1U ||
            fallback_metrics.active_backend != sdr_core::ComputeBackendKind::Cpu ||
            fallback_metrics.last_backend_error != sdr_core::BackendErrorCode::FftExecutionFailed ||
            !fallback_latest.frame.has_value() ||
            fallback_latest.frame->config_generation != engine.config_generation()) {
            return 39;
        }
#endif

        // Lifecycle calls may arrive from different control-plane tasks. A
        // start/disconnect or reconfigure/disconnect collision may be
        // rejected by the documented state machine, but must never leave a
        // worker, stream or configured engine behind.
        for (int cycle = 0; cycle < 25; ++cycle) {
            sdr_pluto::FixedBandEngine start_disconnect("usb:mock");
            static_cast<void>(start_disconnect.configure(config()));
            std::atomic<bool> release{false};
            std::exception_ptr start_failure;
            std::thread starter([&] {
                while (!release.load(std::memory_order_acquire)) {
                    std::this_thread::yield();
                }
                try {
                    start_disconnect.start();
                } catch (...) {
                    start_failure = std::current_exception();
                }
            });
            std::thread disconnector([&] {
                while (!release.load(std::memory_order_acquire)) {
                    std::this_thread::yield();
                }
                start_disconnect.disconnect();
            });
            release.store(true, std::memory_order_release);
            starter.join();
            disconnector.join();
            if (!is_expected_lifecycle_race(start_failure) ||
                start_disconnect.connected() ||
                start_disconnect.state() != sdr_core::EngineState::Stopped) {
                return 24;
            }

            sdr_pluto::FixedBandEngine reconfigure_disconnect("usb:mock");
            static_cast<void>(reconfigure_disconnect.configure(config()));
            reconfigure_disconnect.start();
            release.store(false, std::memory_order_release);
            std::exception_ptr reconfigure_failure;
            std::thread reconfigurer([&] {
                while (!release.load(std::memory_order_acquire)) {
                    std::this_thread::yield();
                }
                try {
                    static_cast<void>(reconfigure_disconnect.reconfigure(
                        config(2'452'000'000.0 + static_cast<double>(cycle))
                    ));
                } catch (...) {
                    reconfigure_failure = std::current_exception();
                }
            });
            std::thread reconfigure_disconnector([&] {
                while (!release.load(std::memory_order_acquire)) {
                    std::this_thread::yield();
                }
                reconfigure_disconnect.disconnect();
            });
            release.store(true, std::memory_order_release);
            reconfigurer.join();
            reconfigure_disconnector.join();
            if (!is_expected_lifecycle_race(reconfigure_failure) ||
                reconfigure_disconnect.connected() ||
                reconfigure_disconnect.state() != sdr_core::EngineState::Stopped) {
                return 25;
            }
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
        // Backpressure is a transport contract, not a race against the
        // accelerator: pin the DSP stage to CPU so the scenario stays
        // deterministic when a faster CUDA backend is available.
        overflow.backend = sdr_core::ComputeBackendKind::Cpu;
        static_cast<void>(engine.configure(overflow));
#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
        engine.set_dsp_delay_for_test(100U);
#endif
        engine.start();
        const auto overflow_deadline =
            std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (engine.metrics().engine.iq_blocks_dropped == 0U &&
               std::chrono::steady_clock::now() < overflow_deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        engine.stop();
#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
        engine.set_dsp_delay_for_test(0U);
#endif
#if defined(_WIN32)
        _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "1");
#else
        setenv("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "1", 1);
#endif
        const auto overflow_metrics = engine.metrics();
        if (overflow_metrics.engine.iq_blocks_dropped == 0U ||
            overflow_metrics.acquisition_queue.depth >
                overflow_metrics.acquisition_queue.capacity ||
            overflow_metrics.acquisition_queue_blocks_dropped == 0U ||
            overflow_metrics.acquisition_queue_blocks_dropped !=
                overflow_metrics.acquisition_queue.dropped ||
            overflow_metrics.engine.iq_blocks_dropped !=
                overflow_metrics.acquisition_queue_blocks_dropped +
                    overflow_metrics.shutdown_blocks_discarded ||
            overflow_metrics.engine.iq_samples_dropped !=
                overflow_metrics.acquisition_queue_samples_dropped +
                    overflow_metrics.shutdown_samples_discarded) {
            std::cerr << "overflow accounting invariant failed: engine_blocks="
                      << overflow_metrics.engine.iq_blocks_dropped
                      << " acquisition_blocks="
                      << overflow_metrics.acquisition_queue_blocks_dropped
                      << " queue_blocks=" << overflow_metrics.acquisition_queue.dropped
                      << " shutdown_blocks=" << overflow_metrics.shutdown_blocks_discarded
                      << " engine_samples=" << overflow_metrics.engine.iq_samples_dropped
                      << " acquisition_samples="
                      << overflow_metrics.acquisition_queue_samples_dropped
                      << " shutdown_samples=" << overflow_metrics.shutdown_samples_discarded
                      << " queue_depth=" << overflow_metrics.acquisition_queue.depth
                      << " queue_capacity=" << overflow_metrics.acquisition_queue.capacity
                      << " state=" << static_cast<int>(overflow_metrics.state)
                      << " has_error=" << overflow_metrics.has_error
                      << " refill_errors=" << overflow_metrics.device.refill_errors
                      << " output_pool_exhaustions="
                      << overflow_metrics.device.output_pool_exhaustions
                      << " expected_cancellations=" << overflow_metrics.expected_cancellations
                      << '\n';
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
