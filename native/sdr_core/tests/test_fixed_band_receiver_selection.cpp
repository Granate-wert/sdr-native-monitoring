#include "sdr_pluto/fixed_band_engine.hpp"
#include "sdr_pluto/continuous_sweep_coordinator.hpp"
#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/recording_reprocess.hpp"
#include "sdr_core/recording_writer.hpp"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
struct Hooks {
    static HMODULE load_mock() {
        const auto size = GetEnvironmentVariableW(L"LIBIIO_DLL_PATH", nullptr, 0);
        require(size != 0, "explicit mock path missing");
        std::wstring path(size, L'\0');
        const auto written = GetEnvironmentVariableW(L"LIBIIO_DLL_PATH", path.data(), size);
        require(written != 0 && written < size, "explicit mock path changed while reading");
        path.resize(written);
        return LoadLibraryW(path.c_str());
    }
    HMODULE module{load_mock()};
    using count_fn = int (*)();
    using gain_fn = double (*)(int);
    count_fn contexts{reinterpret_cast<count_fn>(GetProcAddress(module, "mock_iio_live_contexts"))};
    count_fn created{reinterpret_cast<count_fn>(GetProcAddress(module, "mock_iio_created_contexts"))};
    count_fn buffers{reinterpret_cast<count_fn>(GetProcAddress(module, "mock_iio_live_buffers"))};
    count_fn mutations{reinterpret_cast<count_fn>(GetProcAddress(module, "mock_iio_rf_mutation_calls"))};
    gain_fn gain{reinterpret_cast<gain_fn>(GetProcAddress(module, "mock_iio_gain"))};
    Hooks() { require(module && contexts && created && buffers && mutations && gain, "mock hooks missing"); }
    ~Hooks() { if (module) FreeLibrary(module); }
};
struct CaptureDirectory {
    std::filesystem::path root{std::filesystem::temp_directory_path() /
        ("sdr_rx2_engine_" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()))};
    CaptureDirectory() { require(std::filesystem::create_directory(root), "unique capture directory creation failed"); }
    ~CaptureDirectory() { std::error_code error; std::filesystem::remove_all(root, error); }
};
std::string read_text(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    require(static_cast<bool>(input), "recording artifact missing");
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}
sdr_pluto::FixedBandConfig config(sdr_pluto::ReceiverSelection receiver) {
    sdr_pluto::FixedBandConfig result;
    result.device = {.source_id = "fixed-rx2", .context_uri = "usb:mock",
        .center_frequency_hz = 2'450'000'000., .sample_rate_hz = 61'440'000.,
        .analog_bandwidth_hz = 56'000'000., .gain_mode = sdr_core::GainMode::Manual,
        .manual_gain_db = 43., .buffer_samples = 4096U};
    result.dsp = {.fft_size = 1024U, .hop_size = 512U,
        .precision_mode = sdr_core::PrecisionMode::ReferenceF64};
    result.backend = sdr_core::ComputeBackendKind::Cpu;
    result.allow_runtime_fallback = false;
    result.receiver_selection = receiver;
    result.discard_blocks_after_start = 0U;
    result.snapshot_rate_hz = 2000.;
    result.persistence.enabled = true;
    result.persistence.mode = sdr_core::PersistenceMode::RollingExact;
    result.persistence.window_frames = 8U;
    result.persistence.power_bins = 16U;
    result.persistence.snapshot_rate_hz = 30.;
    return result;
}
template<class Predicate> void wait(sdr_pluto::FixedBandEngine& engine, Predicate predicate) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (std::chrono::steady_clock::now() < deadline) {
        const auto metrics = engine.metrics();
        require(!metrics.has_error, "engine failed while waiting for selected-chain output");
        if (predicate(metrics)) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    throw std::runtime_error("selected-chain output deadline expired");
}
void check_rx2_values(const sdr_core::SpectrumFrame& frame, const sdr_core::DspConfig& dsp) {
    // Compare against the known RX2 digital lanes, not merely a relabelled RX1 frame.
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(dsp.fft_size * 4U);
    for (std::uint32_t k = 0; k < dsp.fft_size; ++k) {
        const auto i = static_cast<std::int16_t>(1000 + ((frame.first_sample_index + k) % 1024));
        const auto q = static_cast<std::int16_t>(-i);
        std::memcpy(bytes->data() + k * 4U, &i, 2U);
        std::memcpy(bytes->data() + k * 4U + 2U, &q, 2U);
    }
    sdr_core::DspOptions options;
    options.source = frame.source;
    auto reference = sdr_core::make_cpu_dsp_backend(options);
    reference->configure(dsp);
    reference->push_iq({.source_sequence = 0U, .first_sample_index = frame.first_sample_index,
        .timestamp_ns = frame.timestamp_ns, .center_frequency_hz = frame.center_frequency_hz,
        .sample_rate_hz = frame.sample_rate_hz,
        .sample_format = sdr_core::SampleFormat::ComplexInt12InInt16Le,
        .sample_count = dsp.fft_size, .samples = bytes, .config_generation = frame.config_generation});
    const auto expected = reference->poll_spectrum(0U, false);
    require(expected.size() == 1 && frame.values && expected[0].values &&
        frame.values->size() == expected[0].values->size(), "reference FFT shape mismatch");
    for (std::size_t k = 0; k < frame.values->size(); ++k) {
        require(std::abs((*frame.values)[k] - (*expected[0].values)[k]) < 1e-4f,
            "selected RX2 FFT contains wrong-chain values");
    }
}
template<class F> void refused(F call, const char* message) {
    bool failed = false;
    try { call(); } catch (const sdr_core::ConfigurationError&) { failed = true; }
    require(failed, message);
}
}

int main() {
    try {
        using Selection = sdr_pluto::ReceiverSelection;
        Hooks hooks;
        _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "1");
        auto profile = config(Selection::Rx2);
        profile.recorder_enabled = true;  // native-only staging tee, no Python I/Q.
        const auto initial_rx1_gain = hooks.gain(1);
        const auto created_before = hooks.created();
        {
            sdr_pluto::FixedBandEngine engine("usb:mock");
            const auto first = engine.configure(profile);
            require(first.receiver_selection == Selection::Rx2 && first.manual_gain_db == 43 &&
                hooks.gain(1) == initial_rx1_gain && hooks.contexts() == 1 &&
                hooks.created() == created_before + 1, "selected RX2 configure must use one owner and isolate gain");
            engine.start();
            wait(engine, [](const auto& m) { return m.engine.fft_frames_computed >= 16; });
            const auto retained = engine.poll_recorded_iq_blocks(1);
            require(retained.size() == 1 && retained[0].samples && (*retained[0].samples)[0] == 0xe8 &&
                (*retained[0].samples)[1] == 3 && retained[0].config_generation == first.config_generation,
                "native recorder tee must contain RX2 bytes/epoch");
            auto both = profile;
            both.receiver_selection = Selection::Both;
            const auto mutations = hooks.mutations();
            refused([&] { static_cast<void>(engine.reconfigure(both)); }, "single-producer engine cannot admit BOTH");
            require(engine.state() == sdr_core::EngineState::Running && hooks.buffers() == 1 &&
                hooks.mutations() == mutations && engine.config_generation() == first.config_generation,
                "invalid BOTH reconfigure cannot stop or mutate a running RX2");
            engine.stop();
            // Existing recorder drain guard must remain in force at reconfigure.
            static_cast<void>(engine.poll_recorded_iq_blocks(0));
            const auto frames = engine.poll_spectrum_frames(0);
            const auto densities = engine.poll_persistence_snapshots(0);
            require(!frames.empty() && !densities.empty(), "RX2 must reach Spectrum and persistence");
            for (const auto& frame : frames) {
                require(frame.source.source_id == "fixed-rx2" &&
                    frame.source.metadata_json.at("receiver_selection") == "\"RX2\"" &&
                    frame.config_generation == first.config_generation && frame.sample_rate_hz == 61'440'000.,
                    "RX2 frame producer/selection/epoch/Fs provenance mismatch");
                check_rx2_values(frame, profile.dsp);
            }
            require(densities.back().source.source_id == "fixed-rx2" &&
                densities.back().config_generation == first.config_generation &&
                engine.metrics().receiver_selection == Selection::Rx2 &&
                engine.metrics().engine.fft_frames_dropped == 0 && hooks.buffers() == 0,
                "RX2 persistence/metrics/analytical-loss/Stop mismatch");
            // Same context, explicit stopped configure then Start, fresh queues/epoch.
            profile.recorder_enabled = false;
            profile.device.center_frequency_hz += 1'000'000.;
            const auto next = engine.configure(profile);
            require(next.config_generation > first.config_generation && engine.poll_spectrum_frames(0).empty() &&
                engine.poll_persistence_snapshots(0).empty(), "stopped RX2 configure must reset histories");
            engine.start();
            wait(engine, [](const auto& m) { return m.engine.fft_frames_computed >= 8; });
            engine.stop();
            const auto restarted = engine.poll_spectrum_frames(0);
            require(!restarted.empty() && restarted.back().config_generation == next.config_generation &&
                restarted.back().source.source_id == "fixed-rx2" && hooks.created() == created_before + 1,
                "RX2 next Start must preserve one context and fresh producer epoch");
            engine.disconnect();
        }
        {
            auto sweep = config(Selection::Rx2);
            sweep.continuous_sweep_line = sdr_pluto::ContinuousSweepLineConfig{
                .enabled = true, .epoch = 9U, .display_start_hz = 2'440'000'000.,
                .display_stop_hz = 2'460'000'000., .usable_window_hz = 36'000'000.};
            sdr_pluto::FixedBandEngine engine("usb:mock");
            const auto applied = engine.configure(sweep);
            engine.start();
            wait(engine, [](const auto& m) { return m.completed_sweep_lines >= 1; });
            engine.stop();
            const auto lines = engine.poll_sweep_line_frames(0);
            require(!lines.empty() && lines.back().source.source_id == "fixed-rx2" &&
                lines.back().source.metadata_json.at("receiver_selection") == "\"RX2\"" &&
                applied.receiver_selection == Selection::Rx2, "RX2 must use the common reduced Sweep-line path");
            engine.disconnect();
        }
        {
            auto left = config(Selection::Rx2);
            auto right = left;
            left.persistence = right.persistence = {};
            right.device.center_frequency_hz = 2'470'000'000.;
            sdr_pluto::ContinuousSweepCoordinatorConfig sweep{
                .epoch = 19U, .display_start_hz = 2'440'000'000., .display_stop_hz = 2'480'000'000.,
                .usable_window_hz = 36'000'000., .output_queue_capacity = 8U,
                .segments = {{left, 2'440'000'000., 2'461'000'000.},
                    {right, 2'459'000'000., 2'480'000'000.}}};
            auto mixed = sweep;
            mixed.segments.back().fixed_band.receiver_selection = Selection::Rx1;
            refused([&] { sdr_pluto::validate(mixed); }, "one sweep cannot silently switch selected receiver");
            sdr_pluto::ContinuousSweepCoordinator coordinator("usb:mock");
            const auto created = hooks.created();
            coordinator.configure(sweep);
            coordinator.start();
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
            while (coordinator.metrics().completed_lines < 2U) {
                require(!coordinator.metrics().has_error && std::chrono::steady_clock::now() < deadline,
                    "RX2 same-owner retuning sweep deadline/error");
                std::this_thread::sleep_for(std::chrono::milliseconds(1));
            }
            coordinator.stop();
            const auto lines = coordinator.poll_lines(0);
            require(!lines.empty() && hooks.created() == created && hooks.contexts() == 1 && hooks.buffers() == 0,
                "retuning RX2 Sweep must keep one existing context and release buffers at Stop");
            bool complete = false;
            for (const auto& line : lines) {
                require(line.source.source_id == "fixed-rx2" &&
                    line.source.metadata_json.at("receiver_selection") == "\"RX2\"" && line.epoch == 19U,
                    "retuning RX2 Sweep publication selection/source/epoch mismatch");
                complete = complete || line.state == sdr_core::SweepLineState::Complete;
            }
            require(complete, "RX2 retuning Sweep must publish a real completed line");
            coordinator.disconnect();
        }
        {
            CaptureDirectory capture;
            auto durable = config(Selection::Rx2);
            durable.recording = {.enabled = true, .output_uri = (capture.root / "live").string(),
                .record_iq = true, .record_spectrum = true, .chunk_samples = 4096U,
                .queue_capacity = 4U, .stop_on_overflow = false};
            sdr_pluto::FixedBandEngine engine("usb:mock");
            const auto applied = engine.configure(durable);
            engine.start();
            wait(engine, [](const auto& m) { return m.recorder_writer_blocks_written >= 2 &&
                m.spectrum_writer_frames_written >= 1; });
            engine.stop();
            const auto metrics = engine.metrics();
            require(!metrics.recorder_writer_failed && !metrics.spectrum_writer_failed &&
                metrics.recorder_queue.depth == 0 && metrics.spectrum_recorder_queue.depth == 0,
                "RX2 writers must finalize after Stop without queued work");
            for (const auto& name : {"live.sigmf-meta", "live.sdr-spectrum.meta"}) {
                const auto text = read_text(capture.root / name);
                require(text.find("\"completed\":true") != std::string::npos &&
                    text.find("\"receiver_selection\":\"RX2\"") != std::string::npos,
                    "RX2 recording manifest must retain exact selected-chain provenance");
            }
            const auto raw = read_text(capture.root / "live.000000.sigmf-data");
            require(raw.size() >= 4 && static_cast<unsigned char>(raw[0]) == 0xe8 && raw[1] == 3,
                "durable I/Q file must contain selected RX2 digital lanes");
            sdr_core::NativeSpectrumRecordingReader reader(capture.root / "live");
            const auto replay = reader.read_frame(0);
            require(replay.source.metadata_json.at("receiver_selection") == "\"RX2\"" &&
                replay.config_generation == applied.config_generation,
                "Spectrum replay must retain RX2/epoch, not infer RX1");
            // Reprocessing selected I/Q keeps provenance through the existing
            // bounded native replay DSP/writer path; no new raw Python loop.
            sdr_core::DspBackendSelectionOptions cpu;
            cpu.preference = sdr_core::ComputeBackendKind::Cpu;
            cpu.allow_runtime_fallback = false;
            sdr_core::NativeIqRecordingReprocessor reprocess(capture.root / "live",
                capture.root / "reprocessed", durable.dsp, cpu);
            const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
            while (!reprocess.process(8U)) {
                require(std::chrono::steady_clock::now() < deadline, "bounded RX2 reprocess deadline expired");
            }
            require(reprocess.progress().state == sdr_core::NativeIqReprocessState::Completed,
                "selected I/Q native reprocess failed");
            sdr_core::NativeSpectrumRecordingReader rebuilt(capture.root / "reprocessed");
            require(rebuilt.read_frame(0).source.metadata_json.at("receiver_selection") == "\"RX2\"",
                "I/Q reprocess must preserve RX2 provenance");
            engine.disconnect();
        }
        _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "");
        {
            sdr_pluto::FixedBandEngine engine("usb:mock");
            const auto mutations = hooks.mutations();
            refused([&] { static_cast<void>(engine.configure(config(Selection::Rx2))); }, "absent RX2 must refuse before RX");
            require(hooks.mutations() == mutations && hooks.buffers() == 0 &&
                engine.state() == sdr_core::EngineState::Created, "absent RX2 cannot partially configure an engine");
            auto ordinary = config(Selection::Rx1);
            const auto applied = engine.configure(ordinary);
            engine.start();
            wait(engine, [](const auto& m) { return m.engine.fft_frames_computed >= 1; });
            engine.stop();
            const auto frames = engine.poll_spectrum_frames(0);
            require(applied.receiver_selection == Selection::Rx1 && !frames.empty() &&
                frames.back().source.metadata_json.empty(), "legacy RX1 remains available and unchanged");
            engine.disconnect();
        }
        require(hooks.buffers() == 0 && hooks.contexts() == 0, "selected-chain tests leaked native ownership");
        std::cout << "RX2 same-engine FFT/tee/persistence/Sweep-line/restart/guards PASS (mock only)\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
