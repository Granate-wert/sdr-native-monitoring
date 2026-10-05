#include "sdr_pluto/fixed_band_engine.hpp"
#include "sdr_pluto/continuous_sweep_coordinator.hpp"
#include "sdr_core/dsp_backend.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/recording_reprocess.hpp"
#include "sdr_core/recording_writer.hpp"
#include "sdr_core/recording_path.hpp"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
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
void check_values(const sdr_core::SpectrumFrame& frame, const sdr_core::DspConfig& dsp, bool rx2) {
    // Compare against the known RX2 digital lanes, not merely a relabelled RX1 frame.
    auto bytes = std::make_shared<std::vector<std::uint8_t>>(dsp.fft_size * 4U);
    for (std::uint32_t k = 0; k < dsp.fft_size; ++k) {
        const auto index = frame.first_sample_index + k;
        const auto i = static_cast<std::int16_t>(rx2 ? 1000 + (index % 1024) : static_cast<int>(index % 4096) - 2048);
        const auto q = static_cast<std::int16_t>(rx2 ? -i : 2047 - static_cast<int>(index % 4096));
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

using Selection = sdr_pluto::ReceiverSelection;
// Bounded native-only observer; read non-atomic fields ONLY after engine.join.
// At most one retained pair, no UI queue or raw-IQ callback.
struct AnalyticalSink final : sdr_pluto::PairedSpectrumAnalyticalSink {
    std::uint64_t reservation{1024U * 32U};
    std::uint64_t calls{}, gaps{}, begins{}, finishes{}, generation{}, fail_at{};
    bool failed{}, fail_begin{}, fail_gap{};
    std::optional<sdr_core::DualRxSpectrumFrame> last;
    std::uint64_t payload_bytes() const noexcept override { return reservation; }
    void begin(const sdr_pluto::AppliedConfig& applied) override {
        ++begins;
        require(applied.receiver_selection == Selection::Both && applied.sample_rate_hz == 61'440'000.,
            "sink receives actual common owner readback");
        generation = applied.config_generation;
        calls = gaps = 0;
        last.reset();
        if (fail_begin) throw std::runtime_error("injected analytical sink begin failure");
    }
    void consume(const sdr_core::DualRxSpectrumFrame& pair) override {
        ++calls;
        require(pair.config_generation == generation &&
            pair.primary.config_generation == generation && pair.secondary.config_generation == generation &&
            pair.primary.source.source_id == "paired-rx1" && pair.secondary.source.source_id == "paired-rx2" &&
            pair.primary.first_sample_index == pair.secondary.first_sample_index &&
            pair.primary.timestamp_ns == pair.secondary.timestamp_ns,
            "analytical pair provenance differs from actual admitted common step");
        if (last) require(last->synchronization_epoch == pair.synchronization_epoch,
            "new synchronization epoch delivered before sink gap invalidation");
        if (fail_at && calls == fail_at) throw std::runtime_error("injected analytical sink consume failure");
        last = pair;
    }
    void shared_gap() override {
        ++gaps;
        last.reset();
        if (fail_gap) throw std::runtime_error("secondary analytical sink cleanup failure");
    }
    void finish(bool error) noexcept override { ++finishes; failed = error; }
};

sdr_pluto::PairedFixedBandConfig paired_config() {
    auto primary = config(Selection::Rx1);
    auto secondary = config(Selection::Rx2);
    primary.device.source_id = "paired-rx1";
    secondary.device.source_id = "paired-rx2";
    return {primary, secondary, 1U};
}
template<class Predicate> void wait_pair(sdr_pluto::FixedBandEngine& engine, Predicate predicate) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (std::chrono::steady_clock::now() < deadline) {
        auto m = engine.paired_metrics();
        require(!m.primary.has_error && !m.secondary.has_error, "paired owner error");
        if (predicate(m)) return;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    const auto m = engine.paired_metrics();
    throw std::runtime_error("paired mock data-plane deadline expired: formed=" +
        std::to_string(m.dsp.paired_frames_formed) + " gaps=" +
        std::to_string(m.dsp.shared_input_gaps) + " acquisition_drops=" +
        std::to_string(m.primary.acquisition_queue_blocks_dropped) + " lines=" +
        std::to_string(m.primary.completed_sweep_lines) + "/" +
        std::to_string(m.secondary.completed_sweep_lines) + " recording=" +
        std::to_string(m.primary.spectrum_writer_frames_written) + "/" +
        std::to_string(m.secondary.spectrum_writer_frames_written));
}
void test_pair_consumers_and_same_owner_restart(Hooks& hooks) {
    auto p = paired_config();
    p.primary.recorder_enabled = p.secondary.recorder_enabled = true;
    const auto created = hooks.created();
    sdr_pluto::FixedBandEngine engine("usb:mock");
    const auto first = engine.configure_paired(p);
    require(first.receiver_selection == Selection::Both && first.receiver_gains.size() == 2 &&
        hooks.contexts() == 1 && hooks.created() == created + 1 &&
        hooks.gain(1) == 43 && hooks.gain(2) == 43, "one-context common gain/readback");
    require(!engine.streaming() && engine.poll_paired_spectrum_frames(0).empty(),
        "Configure pair must not start acquisition");
    engine.start();
    wait_pair(engine, [](const auto& m) { return m.dsp.paired_frames_formed >= 32; });
    require(hooks.buffers() == 1, "one buffer while BOTH running");
    refused([&] { static_cast<void>(engine.poll_spectrum_frames(0)); }, "no one-Spectrum BOTH alias");
    engine.stop();
    auto m = engine.paired_metrics();
    require(m.dsp.primary.fft_frames_computed == m.dsp.secondary.fft_frames_computed &&
        m.dsp.paired_frames_formed == m.dsp.primary.fft_frames_computed &&
        m.dsp.primary.fft_frames_dropped == 0 && m.dsp.secondary.fft_frames_dropped == 0 &&
        m.dsp.paired_frames_published == 0 && m.paired_spectrum_queue.depth == 1 &&
        m.paired_snapshots_superseded > 0 && hooks.buffers() == 0, "analytical consumers before pair queue");
    require(m.primary.engine.persistence_updates == m.dsp.paired_frames_formed &&
        m.secondary.engine.persistence_updates == m.dsp.paired_frames_formed,
        "both densities must consume ALL analytical pairs");
    const auto pairs = engine.poll_paired_spectrum_frames(0);
    require(pairs.size() == 1, "bounded atomic pair output");
    const auto& pair = pairs.back();
    require(pair.primary.source.source_id == "paired-rx1" &&
        pair.secondary.source.source_id == "paired-rx2" &&
        pair.primary.source.metadata_json.at("receiver_selection") == "\"RX1\"" &&
        pair.secondary.source.metadata_json.at("receiver_selection") == "\"RX2\"" &&
        pair.primary.first_sample_index == pair.secondary.first_sample_index &&
        pair.config_generation == first.config_generation &&
        pair.primary.config_generation == pair.secondary.config_generation, "pair source/epoch provenance");
    check_values(pair.primary, p.primary.dsp, false);
    check_values(pair.secondary, p.secondary.dsp, true);
    for (const auto receiver : {Selection::Rx1, Selection::Rx2}) {
        const auto blocks = engine.poll_receiver_recorded_iq_blocks(receiver, 0);
        require(!blocks.empty(), "both native I/Q tees receive admitted epochs");
        require((*blocks[0].samples)[0] == (receiver == Selection::Rx1 ? 0 : 0xe8) &&
            (*blocks[0].samples)[1] == (receiver == Selection::Rx1 ? 0xf8 : 3),
            "tee must use actual selected digital bytes");
        const auto snapshots = engine.poll_receiver_persistence_snapshots(receiver, 0);
        require(!snapshots.empty() && snapshots.back().config_generation == first.config_generation,
            "per-chain persistence source/epoch");
    }
    p.primary.recorder_enabled = p.secondary.recorder_enabled = false;
    p.primary.device.center_frequency_hz += 1'000'000.;
    p.secondary.device.center_frequency_hz += 1'000'000.;
    const auto next = engine.configure_paired(p);
    require(next.config_generation > first.config_generation &&
        engine.poll_paired_spectrum_frames(0).empty() &&
        engine.poll_receiver_persistence_snapshots(Selection::Rx2, 0).empty() && !engine.streaming(),
        "stopped Apply resets both, no hidden Start");
    engine.start();
    wait_pair(engine, [](const auto& m) { return m.dsp.paired_frames_formed >= 8; });
    engine.stop();
    auto drain = engine.drain_latest_paired_spectrum_frame();
    require(drain.frame && drain.frame->config_generation == next.config_generation &&
        hooks.created() == created + 1, "explicit next Start same context/fresh epoch");
    engine.disconnect();
}
void test_pair_guards_before_rf(Hooks& hooks) {
    auto p = paired_config();
    sdr_pluto::FixedBandEngine engine("usb:mock");
    const auto writes = hooks.mutations();
    auto bad = p;
    bad.secondary.device.center_frequency_hz += 1;
    refused([&] { static_cast<void>(engine.configure_paired(bad)); }, "different LO refusal");
    bad = p; bad.secondary.device.source_id = p.primary.device.source_id;
    refused([&] { static_cast<void>(engine.configure_paired(bad)); }, "duplicate identity refusal");
    bad = p; bad.secondary.dsp.hop_size /= 2;
    refused([&] { static_cast<void>(engine.configure_paired(bad)); }, "different FFT geometry refusal");
    bad = p;
    bad.primary.device.buffer_samples = bad.secondary.device.buffer_samples = 262144;
    bad.primary.acquisition_queue_capacity = bad.secondary.acquisition_queue_capacity = 64;
    // EACH 67-MiB pool is admitted singly, but the combined pool exceeds 128 MiB.
    sdr_pluto::validate(bad.primary); sdr_pluto::validate(bad.secondary);
    refused([&] { static_cast<void>(engine.configure_paired(bad)); }, "aggregate budget refusal");
    CaptureDirectory capture;
    bad = p;
    bad.primary.recording = {.enabled = true, .output_uri = (capture.root / "same").string(),
        .record_iq = true, .stop_on_overflow = false};
    bad.secondary.recording = bad.primary.recording;
    bad.secondary.recording.output_uri += ".sigmf-meta.part";
    refused([&] { static_cast<void>(engine.configure_paired(bad)); }, "writer-normalized alias refusal");
    require(hooks.mutations() == writes && hooks.buffers() == 0 &&
        engine.state() == sdr_core::EngineState::Created, "host preflight must precede RF mutation");
    engine.disconnect();
}
std::string utf8_path(const std::filesystem::path& path) {
    const auto value = path.u8string();
    return {reinterpret_cast<const char*>(value.data()), value.size()};
}
void test_pair_unicode_alias_before_rf(Hooks& hooks) {
    CaptureDirectory capture;
    auto p = paired_config();
    // Explicit code points keep the test independent of source/CRT code pages.
    const auto upper = capture.root / L"\u0417\u0410\u041f\u0418\u0421\u042c_\u00c4";
    const auto lower = capture.root / L"\u0437\u0430\u043f\u0438\u0441\u044c_\u00e4";
    p.primary.recording = {.enabled = true, .output_uri = utf8_path(upper),
        .record_iq = true, .stop_on_overflow = false};
    p.secondary.recording = p.primary.recording;
    p.secondary.recording.output_uri = utf8_path(lower) + ".sigmf-meta.part";
    require(sdr_core::native_recording_base_path(p.primary.recording.output_uri) == upper,
        "writer path must preserve UTF-8 Unicode filename");
    sdr_pluto::FixedBandEngine engine("usb:mock");
    const auto writes = hooks.mutations();
    refused([&] { static_cast<void>(engine.configure_paired(p)); },
        "Windows ordinal Unicode case alias must refuse");
    p.secondary.recording.output_uri = utf8_path(lower) + ".part.part";
    refused([&] { static_cast<void>(engine.configure_paired(p)); },
        "Unicode bare repeated partial suffix alias must refuse");
    p.secondary.recording.output_uri = utf8_path(lower) + std::string(1, '\0') + "hidden";
    refused([&] { static_cast<void>(engine.configure_paired(p)); },
        "embedded NUL recording path must refuse before RF");
    p.secondary.recording.output_uri = utf8_path(capture.root) + "/bad_\xc3\x28";
    refused([&] { static_cast<void>(engine.configure_paired(p)); },
        "malformed UTF-8 recording path must refuse before RF");
    require(hooks.mutations() == writes && hooks.buffers() == 0 &&
        engine.state() == sdr_core::EngineState::Created &&
        std::filesystem::is_empty(capture.root), "Unicode alias preflight before RF or files");
    engine.disconnect();
}
void test_pair_lines_and_metric_polling() {
    auto p = paired_config();
    p.primary.continuous_sweep_line = sdr_pluto::ContinuousSweepLineConfig{
        .enabled = true, .epoch = 5, .display_start_hz = 2'440'000'000.,
        .display_stop_hz = 2'460'000'000., .usable_window_hz = 36'000'000.};
    p.secondary.continuous_sweep_line = p.primary.continuous_sweep_line;
    sdr_pluto::FixedBandEngine engine("usb:mock");
    static_cast<void>(engine.configure_paired(p));
    engine.start();
    std::atomic<bool> finished{}, failed{};
    std::thread observer([&] {
        try {
            while (!finished.load()) {
                const auto metrics = engine.paired_metrics();
                if (metrics.primary.receiver_selection != Selection::Rx1 ||
                    metrics.secondary.receiver_selection != Selection::Rx2) failed = true;
            }
        } catch (...) { failed = true; }
    });
    try {
        wait_pair(engine, [](const auto& m) {
            return m.primary.completed_sweep_lines >= 2 && m.secondary.completed_sweep_lines >= 2;
        });
        engine.stop();
    } catch (...) {
        finished = true; observer.join(); throw;
    }
    finished = true; observer.join();
    require(!failed, "concurrent pair metrics must use protected DSP snapshots");
    for (const auto receiver : {Selection::Rx1, Selection::Rx2}) {
        const auto lines = engine.poll_receiver_sweep_line_frames(receiver, 0);
        require(!lines.empty() && lines.back().epoch == 5 &&
            lines.back().source.source_id == (receiver == Selection::Rx1 ? "paired-rx1" : "paired-rx2"),
            "both native line consumers retain source/line epoch");
    }
    engine.disconnect();
}
void test_pair_durable_recordings(bool unicode = false) {
    CaptureDirectory capture;
    auto p = paired_config();
    const auto capture_path = [&](const auto* channel) {
        return unicode ? capture.root / L"\u0414\u0430\u043d\u043d\u044b\u0435_\u00c4" /
            (channel == &p.primary ? L"\u041f\u0440\u0438\u0451\u043c_RX1" : L"\u041f\u0440\u0438\u0451\u043c_RX2")
            : capture.root / channel->device.source_id;
    };
    for (auto* channel : {&p.primary, &p.secondary}) {
        channel->recording = {.enabled = true,
            .output_uri = utf8_path(capture_path(channel)),
            .record_iq = true, .record_spectrum = true, .chunk_samples = 4096,
            .queue_capacity = 32, .stop_on_overflow = false};
    }
    sdr_pluto::FixedBandEngine engine("usb:mock");
    const auto applied = engine.configure_paired(p);
    engine.start();
    wait_pair(engine, [](const auto& m) {
        return m.primary.spectrum_writer_frames_written >= 2 &&
               m.secondary.spectrum_writer_frames_written >= 2 &&
               m.primary.recorder_writer_blocks_written >= 2 &&
               m.secondary.recorder_writer_blocks_written >= 2;
    });
    engine.stop();
    const auto m = engine.paired_metrics();
    require(!m.primary.recorder_writer_failed && !m.secondary.recorder_writer_failed &&
        !m.primary.spectrum_writer_failed && !m.secondary.spectrum_writer_failed,
        "both channel writers normal-finalize after DSP join");
    for (const auto* channel : {&p.primary, &p.secondary}) {
        const auto base = capture_path(channel);
        const auto info = sdr_core::inspect_final_native_recording(base);
        require(info.iq_manifest_final && info.spectrum_manifest_final &&
            info.spectrum_frame_count > 0, "both independent native artifacts finalized");
        sdr_core::NativeSpectrumRecordingReader reader(base);
        const auto frame = reader.read_frame(0);
        require(frame.source.source_id == channel->device.source_id &&
            frame.source.metadata_json.at("receiver_selection") ==
                (channel == &p.primary ? "\"RX1\"" : "\"RX2\"") &&
            frame.config_generation == applied.config_generation, "replay per-chain provenance");
        if (unicode) {
            const auto output = base.parent_path() /
                (channel == &p.primary ? L"\u041e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0430_RX1" : L"\u041e\u0431\u0440\u0430\u0431\u043e\u0442\u043a\u0430_RX2");
            sdr_core::DspBackendSelectionOptions selection;
            selection.preference = sdr_core::ComputeBackendKind::Cpu;
            selection.allow_runtime_fallback = false;
            sdr_core::NativeIqRecordingReprocessor reprocessor(base, output, channel->dsp, selection);
            std::uint32_t turns = 0;
            while (!reprocessor.process(1)) require(++turns < 256, "bounded Unicode reprocess");
            require(reprocessor.progress().state == sdr_core::NativeIqReprocessState::Completed &&
                reprocessor.progress().output_uri == utf8_path(output), "Unicode reprocess output path");
            sdr_core::NativeSpectrumRecordingReader reprocessed(output);
            require(reprocessed.frame_count() > 0 &&
                reprocessed.read_frame(0).source.source_id == channel->device.source_id,
                "Unicode reprocess preserves channel provenance");
            const auto manifest = sdr_core::recording_path_with_suffix(base, ".sdr-spectrum.meta");
            require(read_text(manifest).find(utf8_path(base.filename())) != std::string::npos,
                "manifest artifact names are UTF-8");
        }
    }
    engine.disconnect();
}
void test_pair_cancel_refill(Hooks& hooks) {
    auto p = paired_config();
    _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "60");
    sdr_pluto::FixedBandEngine engine("usb:mock");
    static_cast<void>(engine.configure_paired(p));
    engine.start();
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    engine.request_stop(); engine.join();
    require(engine.state() == sdr_core::EngineState::Stopped && hooks.buffers() == 0,
        "cancel/join releases the ONLY paired IIO buffer");
    engine.disconnect();
    _putenv_s("SDR_MOCK_LIBIIO_REFILL_DELAY_MS", "1");
}
void test_pair_shared_queue_gap_and_terminal_flush() {
    auto p = paired_config();
    p.primary.acquisition_queue_capacity = p.secondary.acquisition_queue_capacity = 1;
    p.primary.dsp.batch_size = p.secondary.dsp.batch_size = 4;
    sdr_pluto::FixedBandEngine engine("usb:mock");
    static_cast<void>(engine.configure_paired(p));
    // Sleep(1) may be ~15.6 ms on Windows. A 10-ms DSP hook did not
    // force any queue loss on this host (1532 pairs, 0 gaps, 0 drops).
    // This is fault injection, not a product cadence/quality change.
    engine.set_dsp_delay_for_test(80);
    engine.start();
    wait_pair(engine, [](const auto& m) {
        return m.dsp.shared_input_gaps >= 2 && m.dsp.paired_frames_formed >= 24;
    });
    engine.stop();
    const auto m = engine.paired_metrics();
    const auto latest = engine.drain_latest_paired_spectrum_frame();
    require(latest.frame && latest.frame->shared_input_gaps_before == m.dsp.shared_input_gaps &&
        m.primary.acquisition_queue_blocks_dropped > 0 &&
        m.primary.acquisition_queue_blocks_dropped == m.secondary.acquisition_queue_blocks_dropped &&
        m.primary.engine.fft_frames_dropped == 0 && m.secondary.engine.fft_frames_dropped == 0,
        "shared queue loss distinct from analytical loss and no stale terminal epoch");
    require(sdr_core::has_flag(latest.frame->primary.quality_flags,
        sdr_core::QualityFlag::BackendDiscontinuity) &&
        sdr_core::has_flag(latest.frame->secondary.quality_flags,
        sdr_core::QualityFlag::BackendDiscontinuity), "both frames expose shared DSP discontinuity");
    require(m.dsp.primary.output_pending == 0 && m.dsp.secondary.output_pending == 0,
        "Stop flushes BOTH partial DSP batches");
    engine.disconnect();
}
void test_pair_absent_rx2(Hooks& hooks) {
    _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "");
    sdr_pluto::FixedBandEngine engine("usb:mock");
    const auto writes = hooks.mutations();
    refused([&] { static_cast<void>(engine.configure_paired(paired_config())); },
        "absent peer must refuse paired configuration");
    require(hooks.mutations() == writes && hooks.buffers() == 0, "absent peer no RF/buffer writes");
    engine.disconnect();
    _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "1");
}

void test_analytical_sink_all_frames_flush_and_rearm(Hooks& hooks) {
    auto p = paired_config();
    p.primary.snapshot_rate_hz = p.secondary.snapshot_rate_hz = 1.;
    p.primary.dsp.batch_size = p.secondary.dsp.batch_size = 4;
    auto sink = std::make_shared<AnalyticalSink>();
    p.analytical_sink = sink;
    const auto created = hooks.created();
    sdr_pluto::FixedBandEngine engine("usb:mock");
    const auto first = engine.configure_paired(p);
    require(sink->begins == 0 && sink->calls == 0, "configured sink must not Start or consume");
    engine.start();
    wait_pair(engine, [](const auto& m) { return m.dsp.paired_frames_formed >= 32; });
    engine.stop();
    auto m = engine.paired_metrics();
    require(sink->calls == m.dsp.paired_frames_formed && sink->calls > m.paired_snapshots_emitted &&
        sink->finishes == 1 && !sink->failed && sink->last &&
        sink->generation == first.config_generation && sink->gaps == m.dsp.shared_input_gaps &&
        m.dsp.primary.output_pending == 0 && m.dsp.secondary.output_pending == 0,
        "all analytical pairs and terminal partial flush precede 1Hz render selection");
    require(m.primary.engine.persistence_updates == sink->calls &&
        m.secondary.engine.persistence_updates == sink->calls, "existing per-chain consumers remain fed");
    const auto first_generation = sink->generation;
    p.primary.device.center_frequency_hz += 1'000'000.;
    p.secondary.device.center_frequency_hz += 1'000'000.;
    const auto next = engine.configure_paired(p);
    require(sink->begins == 1 && !engine.streaming(), "reconfigure must not reenter sink or Start");
    engine.start();
    wait_pair(engine, [](const auto& metrics) { return metrics.dsp.paired_frames_formed >= 8; });
    engine.stop();
    m = engine.paired_metrics();
    require(sink->begins == 2 && sink->finishes == 2 && !sink->failed && sink->last &&
        sink->generation == next.config_generation && sink->generation > first_generation &&
        sink->calls == m.dsp.paired_frames_formed && hooks.created() == created + 1,
        "fresh analytical step uses SAME context, fresh generation, no stale pair");
    engine.disconnect();
}

void test_analytical_sink_shared_gap() {
    auto p = paired_config();
    auto sink = std::make_shared<AnalyticalSink>();
    p.analytical_sink = sink;
    p.primary.acquisition_queue_capacity = p.secondary.acquisition_queue_capacity = 1;
    sdr_pluto::FixedBandEngine engine("usb:mock");
    static_cast<void>(engine.configure_paired(p));
    engine.set_dsp_delay_for_test(80); // Fault injection ONLY, product timing unchanged.
    engine.start();
    wait_pair(engine, [](const auto& m) { return m.dsp.shared_input_gaps >= 2; });
    engine.stop();
    const auto m = engine.paired_metrics();
    require(sink->gaps == m.dsp.shared_input_gaps && sink->gaps >= 2 &&
        sink->calls == m.dsp.paired_frames_formed && sink->last &&
        sink->last->shared_input_gaps_before == sink->gaps && !sink->failed && sink->finishes == 1,
        "shared gap must invalidate sink BEFORE next analytical pair, with terminal flush");
    engine.disconnect();
}

void test_analytical_sink_budget_before_rf(Hooks& hooks) {
    auto p = paired_config();
    auto sink = std::make_shared<AnalyticalSink>();
    sink->reservation = 128U * 1024U * 1024U + 1U;
    p.analytical_sink = sink;
    sdr_pluto::FixedBandEngine engine("usb:mock");
    const auto created = hooks.created();
    const auto mutations = hooks.mutations();
    refused([&] { static_cast<void>(engine.configure_paired(p)); }, "sink shared128MiB budget must refuse");
    sink->reservation = std::numeric_limits<std::uint64_t>::max();
    refused([&] { static_cast<void>(engine.configure_paired(p)); }, "sink checked sum must refuse overflow");
    require(hooks.created() == created && hooks.mutations() == mutations && sink->begins == 0,
        "sink memory refusal BEFORE context/RF/worker");
    engine.disconnect();
}

void test_analytical_sink_failure(bool at_begin, bool compound = false) {
    auto p = paired_config();
    auto sink = std::make_shared<AnalyticalSink>();
    sink->fail_begin = at_begin;
    sink->fail_gap = compound;
    sink->fail_at = at_begin ? 0 : 3;
    p.analytical_sink = sink;
    sdr_pluto::FixedBandEngine engine("usb:mock");
    static_cast<void>(engine.configure_paired(p));
    engine.start();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (!engine.paired_metrics().primary.has_error && std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    require(engine.paired_metrics().primary.has_error, "sink exception must fail SAME common owner");
    engine.stop();
    require(sink->finishes == 1 && sink->failed && sink->begins == 1 &&
        sink->calls == (at_begin ? 0 : 3), "sink failure has one terminal acknowledgement and no retry");
    bool original = false;
    for (const auto& event : engine.poll_events(0))
        if (event.message.find(at_begin ? "injected analytical sink begin failure" :
            "injected analytical sink consume failure") != std::string::npos) original = true;
    require(original, "sink original failure cause preserved");
    if (compound) require(sink->gaps == 1, "failed sink cleanup must not be retried");
    engine.disconnect();
}
} // namespace

void test_pair_owner_journals(Hooks& hooks) {
    sdr_pluto::FixedBandEngine engine("usb:mock");
    auto requested = paired_config();
    requested.primary.analytical_event_capacity = 32U;
    requested.secondary.analytical_event_capacity = 32U;
    const auto mutations = hooks.mutations();
    auto invalid_capacity = requested;
    invalid_capacity.primary.analytical_event_capacity = 4097U;
    invalid_capacity.secondary.analytical_event_capacity = 4097U;
    refused([&] { static_cast<void>(engine.configure_paired(invalid_capacity)); },
            "unbounded pair journal accepted");
    require(hooks.mutations() == mutations, "journal refusal changed RF");
    auto resource = sdr_core::DualRxDspConfig{};
    // Existing combined budget must charge two rings AND two bounded drains.
    resource.primary.dsp = requested.primary.dsp;
    const auto before = sdr_core::dual_rx_dsp_resource_budget(resource);
    resource.analytical_event_capacity = 32U;
    const auto after = sdr_core::dual_rx_dsp_resource_budget(resource);
    require(after.total_bytes - before.total_bytes ==
        2U * (sdr_core::analytical_ready_reserved_bytes(32U) - sdr_core::analytical_ready_reserved_bytes(0U)),
        "paired journal reservation not aggregate");
    static_cast<void>(engine.configure_paired(requested));
    engine.start();
    wait_pair(engine, [](const auto& m) { return m.dsp.paired_frames_formed >= 8U; });
    const auto p = engine.drain_analytical_ready_events(Selection::Rx1, 0U);
    const auto q = engine.drain_analytical_ready_events(Selection::Rx2, 0U);
    require(p.summary.supported && q.summary.supported &&
        p.summary.producer_instance_id != q.summary.producer_instance_id &&
        p.summary.offered >= 8U && q.summary.offered >= 8U &&
        p.summary.event_capacity == 32U && q.summary.event_capacity == 32U,
        "paired owner does not expose separate admitted CPU producer journals");
    refused([&] { static_cast<void>(engine.drain_analytical_ready_events(Selection::Both, 0U)); },
        "BOTH aliases an individual journal");
    engine.stop();
    static_cast<void>(engine.drain_latest_paired_spectrum_frame());
    for (auto rx : {Selection::Rx1, Selection::Rx2}) {
        const auto final = engine.drain_analytical_ready_events(rx, 0U).summary;
        require(final.outstanding == 0U && final.events_generated ==
            final.events_drained + final.events_pending + final.events_lost,
            "terminal paired journal conservation failed");
        const auto& presentation = final.presentation;
        require(presentation.supported && presentation.accounting_failures == 0U && final.handed_off ==
            presentation.forwarded + presentation.superseded + presentation.coalesced +
                presentation.cancelled + presentation.cadence_suppressed,
            "paired AD native owner lost per-chain presentation/cadence decisions");
    }
    engine.disconnect();
}
void test_pair_terminal_discard() {
    sdr_pluto::FixedBandEngine engine("usb:mock");
    auto p = paired_config();
    p.primary.analytical_event_capacity = p.secondary.analytical_event_capacity = 64U;
    static_cast<void>(engine.configure_paired(p));
    refused([&] { static_cast<void>(engine.discard_terminal_spectrum_frames()); },
        "configured paired terminal release accepted");
    engine.start();
    refused([&] { static_cast<void>(engine.discard_terminal_spectrum_frames()); },
        "running paired terminal release accepted");
    wait_pair(engine, [](const auto& m) { return m.dsp.paired_frames_formed >= 8U; });
    engine.stop();
    const auto pending = engine.paired_metrics().paired_spectrum_queue.depth;
    require(pending > 0 && engine.discard_terminal_spectrum_frames() == pending &&
        engine.discard_terminal_spectrum_frames() == 0 && !engine.drain_latest_paired_spectrum_frame().frame,
        "terminal paired discard exact/idempotent");
    for (auto rx : {Selection::Rx1, Selection::Rx2}) {
        const auto s = engine.drain_analytical_ready_events(rx, 0U).summary;
        const auto& d = s.presentation;
        require(d.cancelled == pending && d.accounting_failures == 0 && s.handed_off ==
            d.forwarded + d.superseded + d.coalesced + d.cancelled + d.cadence_suppressed,
            "each paired chain must account its own cancelled terminal offers");
    }
    engine.disconnect();
}

void test_pair_layer_journals(Hooks& hooks) {
    auto requested = paired_config();
    requested.primary.continuous_sweep_line = sdr_pluto::ContinuousSweepLineConfig{
        .enabled = true, .epoch = 23U, .display_start_hz = 2'440'000'000.,
        .display_stop_hz = 2'460'000'000., .usable_window_hz = 36'000'000.};
    requested.secondary.continuous_sweep_line = requested.primary.continuous_sweep_line;
    // Deliberately different diagnostic capacities: NOT an RF-common setting.
    requested.primary.layer_event_capacity = 1U;
    requested.secondary.layer_event_capacity = 32U;
    sdr_pluto::FixedBandEngine engine("usb:mock");
    const auto mutations = hooks.mutations();
    auto invalid = requested;
    invalid.secondary.layer_event_capacity = 4097U;
    refused([&] { static_cast<void>(engine.configure_paired(invalid)); }, "unbounded RX2 layer ring accepted");
    // Exact existing aggregate Sweep ceiling: two actual relay reservations
    // plus one sink. Enabling even a one-record journal must NOT get a second
    // per-chain ceiling or allocate outside the admitted common resource.
    auto ceiling = requested;
    ceiling.primary.layer_event_capacity = ceiling.secondary.layer_event_capacity = 0U;
    const std::uint64_t relay = std::max<std::uint64_t>(
        ceiling.primary.continuous_sweep_line->output_queue_capacity,
        ceiling.primary.device.buffer_samples / ceiling.primary.dsp.hop_size + ceiling.primary.dsp.batch_size + 2U);
    const auto bins = static_cast<std::uint64_t>(std::floor(20'000'000. /
        (ceiling.primary.device.sample_rate_hz / ceiling.primary.dsp.fft_size)) + 1.);
    const auto channel_bytes = bins * 20U * (relay + 2U) + (relay + 3U) *
        sizeof(std::optional<sdr_core::LayerReadyRef>) + sizeof(std::shared_ptr<sdr_core::LayerReadyJournal>);
    auto sink = std::make_shared<AnalyticalSink>();
    sink->reservation = 128ULL * 1024U * 1024U - 2U * channel_bytes;
    ceiling.analytical_sink = sink;
    sdr_pluto::validate(ceiling);
    ceiling.secondary.layer_event_capacity = 1U;
    refused([&] { static_cast<void>(engine.configure_paired(ceiling)); },
        "layer ring/drain escaped aggregate Sweep ceiling");
    require(hooks.mutations() == mutations, "layer capacity refusal changed RF");
    static_cast<void>(engine.configure_paired(requested));
    require(hooks.contexts() == 1 && hooks.buffers() == 0, "layer journal opened second context/buffer");
    engine.start();
    wait_pair(engine, [](const auto& m) {
        return m.primary.completed_sweep_lines >= 3U && m.secondary.completed_sweep_lines >= 3U;
    });
    require(hooks.buffers() == 1, "layer journaling split shared IIO buffer");
    engine.stop();
    std::uint64_t ids[4]{};
    unsigned index = 0U;
    for (const auto rx : {Selection::Rx1, Selection::Rx2}) {
        const auto d = engine.drain_density_layer_ready_events(rx, 0U);
        const auto s = engine.drain_sweep_layer_ready_events(rx, 0U);
        const auto density = engine.poll_receiver_persistence_snapshots(rx, 0U);
        const auto lines = engine.poll_receiver_sweep_line_frames(rx, 0U);
        require(!density.empty() && !lines.empty() && density.back().layer_ready && lines.back().layer_ready,
            "paired actual density/line lacks original receipt");
        require(d.summary.created > 0 && s.summary.created >= 3U &&
            density.back().layer_ready->producer_instance_id == d.summary.producer_instance_id &&
            lines.back().layer_ready->producer_instance_id == s.summary.producer_instance_id,
            "paired layer receipts do not match same channel journals");
        for (const auto* summary : {&d.summary, &s.summary}) {
            ids[index++] = summary->producer_instance_id;
            require(summary->created == summary->events_drained + summary->events_pending + summary->events_lost,
                "paired Stop layer conservation failed");
        }
        if (rx == Selection::Rx1) require(s.summary.events_lost > 0, "bounded layer loss disappeared");
    }
    for (unsigned i = 0; i < 4; ++i) for (unsigned j = i + 1; j < 4; ++j)
        require(ids[i] != ids[j], "paired chains/layers share creation producer");
    refused([&] { static_cast<void>(engine.drain_sweep_layer_ready_events(Selection::Both, 0U)); },
        "BOTH aliases one layer journal");
    refused([&] { static_cast<void>(engine.drain_density_layer_ready_events(Selection::Rx1, 4097U)); },
        "unbounded layer drain accepted");
    engine.disconnect();
    require(hooks.contexts() == 0 && hooks.buffers() == 0, "layer owner cleanup leaked");
}
int main() {
    try {
        Hooks hooks;
        _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "1");
        test_pair_owner_journals(hooks);
        test_pair_layer_journals(hooks);
        test_pair_terminal_discard();
        test_pair_consumers_and_same_owner_restart(hooks);
        test_pair_guards_before_rf(hooks);
        test_pair_unicode_alias_before_rf(hooks);
        test_pair_lines_and_metric_polling();
        test_pair_durable_recordings();
        test_pair_durable_recordings(true);
        test_pair_cancel_refill(hooks);
        std::cout << "shared queue gap/flush case\n";
        test_pair_shared_queue_gap_and_terminal_flush();
        test_pair_absent_rx2(hooks);
        test_analytical_sink_all_frames_flush_and_rearm(hooks);
        test_analytical_sink_shared_gap();
        test_analytical_sink_budget_before_rf(hooks);
        test_analytical_sink_failure(false);
        test_analytical_sink_failure(true);
        test_analytical_sink_failure(false, true);
        require(hooks.contexts() == 0 && hooks.buffers() == 0, "paired ownership leaks");
        _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "");
        std::cout << "paired same-owner data plane 18 cases PASS (mock ONLY)\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
