#include "sdr_rtlsdr/rtl_official_port.hpp"
#include "sdr_core/errors.hpp"

#include <array>
#ifdef NDEBUG
#undef NDEBUG  // Release qualification must execute checks and hash side effects.
#endif
#include <cassert>
#include <chrono>
#include <cstdio>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
#include <bcrypt.h>

using namespace std::chrono_literals;

namespace {
[[nodiscard]] std::string sha256(const std::filesystem::path& file) {
    std::ifstream stream(file, std::ios::binary | std::ios::ate);
    assert(stream);
    const auto size = static_cast<std::size_t>(stream.tellg());
    assert(size > 0U && size < 64U * 1024U * 1024U);
    std::vector<std::uint8_t> bytes(size);
    stream.seekg(0);
    assert(stream.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(size)));
    BCRYPT_ALG_HANDLE algorithm{};
    assert(BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) >= 0);
    std::array<std::uint8_t, 32U> digest{};
    assert(BCryptHash(algorithm, nullptr, 0, bytes.data(), static_cast<ULONG>(bytes.size()),
                      digest.data(), static_cast<ULONG>(digest.size())) >= 0);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    constexpr char alphabet[] = "0123456789abcdef";
    std::string result;
    for (const auto byte : digest) {
        result.push_back(alphabet[byte >> 4U]);
        result.push_back(alphabet[byte & 15U]);
    }
    return result;
}

[[nodiscard]] std::string utf8_path(const std::filesystem::path& file) {
    const auto utf8 = file.u8string();
    return {reinterpret_cast<const char*>(utf8.data()), utf8.size()};
}

using Scenario = void (__cdecl *)(int);
using ResetCounters = void (__cdecl *)();
using Counter = int (__cdecl *)(int);
using CompletedCallbacks = int (__cdecl *)();

struct Cu8Capture {
    sdr_rtlsdr::RtlRuntimePort* port{};
    std::array<std::uint32_t, 2U> lengths{};
    std::size_t calls{};
    std::uint64_t samples{};
    bool bytes_match{true};
    bool cancellation_ok{true};
};

void capture_cu8(const std::span<const std::uint8_t> bytes, void* context) noexcept {
    auto& capture = *static_cast<Cu8Capture*>(context);
    if (capture.calls < capture.lengths.size()) {
        capture.lengths[capture.calls] = static_cast<std::uint32_t>(bytes.size());
    }
    ++capture.calls;
    capture.samples += bytes.size() / 2U;
    capture.bytes_match = capture.bytes_match && (bytes.size() & 1U) == 0U;
    for (std::size_t index = 0U; index < bytes.size(); ++index) {
        const auto expected = index % 16U == 0U ? 255U : 128U;
        capture.bytes_match = capture.bytes_match && bytes[index] == expected;
    }
    // The synthetic fixture delivers both transfers even after cancellation.
    // Direct-port verification has no runtime queue, admission or DSP thread.
    capture.cancellation_ok = capture.port->cancel_async() == 0 && capture.cancellation_ok;
}

[[nodiscard]] bool exact_cu8_transfers(const Cu8Capture& capture) {
    return capture.calls == 2U && capture.lengths == std::array<std::uint32_t, 2U>{16'384U, 32'768U} &&
           capture.samples == 24'576U && capture.bytes_match && capture.cancellation_ok;
}

[[nodiscard]] bool runtime_transfers_accounted(const sdr_rtlsdr::RtlMetrics& metrics) {
    return metrics.callbacks == 2U && metrics.blocks_admitted <= 2U &&
           metrics.host_input_blocks_dropped <= 2U && metrics.samples_admitted <= 24'576U &&
           metrics.host_input_samples_dropped <= 24'576U &&
           metrics.blocks_admitted + metrics.host_input_blocks_dropped == 2U &&
           metrics.samples_admitted + metrics.host_input_samples_dropped == 24'576U &&
           metrics.malformed_callbacks == 0U && !metrics.host_loss_cardinality_unknown &&
           metrics.callbacks_after_stop == 0U && metrics.worker_failures == 0U;
}

void accounting_oracle() {
    sdr_rtlsdr::RtlMetrics metrics;
    metrics.callbacks = 1U;
    metrics.blocks_admitted = 1U;
    metrics.samples_admitted = 8'192U;
    metrics.dsp.fft_frames_computed = 3U;
    // A first FFT must never imply that the second transfer has completed.
    assert(!runtime_transfers_accounted(metrics));
    metrics.callbacks = 2U;
    metrics.host_input_blocks_dropped = 1U;
    metrics.host_input_samples_dropped = 16'384U;
    assert(runtime_transfers_accounted(metrics));  // reported bounded drop
    auto invalid = metrics;
    --invalid.host_input_samples_dropped;
    assert(!runtime_transfers_accounted(invalid));  // unreported sample
    invalid = metrics;
    invalid.host_loss_cardinality_unknown = true;
    assert(!runtime_transfers_accounted(invalid));
    invalid = metrics;
    invalid.worker_failures = 1U;
    assert(!runtime_transfers_accounted(invalid));
    invalid = metrics;
    invalid.malformed_callbacks = 1U;
    assert(!runtime_transfers_accounted(invalid));
    invalid = metrics;
    invalid.callbacks_after_stop = 1U;
    assert(!runtime_transfers_accounted(invalid));
    invalid = metrics;
    invalid.callbacks = 3U;
    assert(!runtime_transfers_accounted(invalid));
}

void official_cu8_transfers(const sdr_rtlsdr::RtlExternalRuntime& external) {
    auto port = sdr_rtlsdr::make_official_rtl_port(external);
    assert(port->open_exact_unique_serial("00000001") == 0);
    Cu8Capture capture;
    capture.port = port.get();
    assert(port->read_async(capture_cu8, &capture, 32'768U) == 0);
    assert(exact_cu8_transfers(capture));
    assert(port->close() == 0);
    // Oracle must reject lost, duplicate, truncated or corrupted delivery.
    auto invalid = capture;
    invalid.calls = 1U;
    assert(!exact_cu8_transfers(invalid));
    invalid = capture;
    invalid.calls = 3U;
    assert(!exact_cu8_transfers(invalid));
    invalid = capture;
    --invalid.lengths[1];
    assert(!exact_cu8_transfers(invalid));
    invalid = capture;
    invalid.bytes_match = false;
    assert(!exact_cu8_transfers(invalid));
    invalid = capture;
    --invalid.samples;
    assert(!exact_cu8_transfers(invalid));
}

void selected_route_and_cu8(const sdr_rtlsdr::RtlExternalRuntime& external,
                          CompletedCallbacks completed_callbacks,
                          bool has_gain_table = true, std::uint32_t tuner_type = 5U) {
    const auto observed = sdr_rtlsdr::observe_single_rtl_candidate(external);
    assert(observed.enumeration_index == 0U && observed.serial == "00000001");
    assert(observed.tuner_type == tuner_type && !observed.direct_sampling && !observed.offset_tuning);
    assert(observed.tuner_gains_tenth_db == (has_gain_table ? std::vector<int>({-99, 0, 144, 496}) : std::vector<int>{}));
    sdr_rtlsdr::RtlProfile profile;
    profile.center_hz = 150'000'000U;
    profile.sample_rate_hz = 2'400'000U;
    profile.source_id = "rtl-mock-cabi";
    profile.session_route = sdr_rtlsdr::RtlSessionRoute{
        observed.manufacturer, observed.product, observed.serial, observed.tuner_type, 1U};
    profile.official_unbundled_runtime = true;
    auto owner = sdr_rtlsdr::RtlRuntimeSession::start(
        sdr_rtlsdr::make_official_rtl_port(external), profile);
    const auto readback = owner->readback();
    assert(readback.session_epoch > 0U);
    assert(readback.actual_center_hz == profile.center_hz);
    assert(readback.actual_sample_rate_hz == profile.sample_rate_hz);
    assert(!readback.tuner_gain_readback_known && !readback.cached_tuner_gain_tenth_db);
    // First FFT is not a completion fence for a second callback. Observe the
    // test fixture's return fence first, without locking the live slot pool.
    for (int trial = 0; trial < 200 && completed_callbacks() != 2; ++trial) {
        std::this_thread::sleep_for(2ms);
    }
    assert(completed_callbacks() == 2);
    for (int trial = 0; trial < 200 && owner->metrics().dsp.fft_frames_computed == 0U; ++trial) {
        std::this_thread::sleep_for(2ms);
    }
    const auto diagnostic = owner->metrics();
    std::fprintf(stderr, "RTL fixture observation callbacks=%llu admitted=%llu dropped=%llu fft=%llu\n",
        static_cast<unsigned long long>(diagnostic.callbacks),
        static_cast<unsigned long long>(diagnostic.samples_admitted),
        static_cast<unsigned long long>(diagnostic.host_input_samples_dropped),
        static_cast<unsigned long long>(diagnostic.dsp.fft_frames_computed));
    // Bounded runtime admission may drop on slot contention. Exact SDK bytes
    // are verified separately; here every transfer must be truthfully counted.
    assert(runtime_transfers_accounted(diagnostic));
    assert(diagnostic.dsp.fft_frames_computed > 0U);
    const auto latest = owner->drain_latest_spectrum_frame();
    assert(latest.frame && latest.frame->source.backend_id == "native.librtlsdr.unbundled.cpu.v1");
    bool probe_refused{};
    try {
        static_cast<void>(sdr_rtlsdr::observe_single_rtl_candidate(external));
    } catch (const sdr_core::DeviceError&) { probe_refused = true; }
    assert(probe_refused);  // selected probe cannot open over this live owner
    assert(owner->stop(2000ms).complete());
    assert(!owner->cleanup_required());
    const auto terminal = owner->metrics();
    assert(runtime_transfers_accounted(terminal));
    assert(terminal.reader_returned && terminal.reader_return_status == 0);
    assert(!terminal.reader_returned_without_stop);
    assert(terminal.ready_depth == 0U && terminal.slots_in_use == 0U);
}

void count_changed_after_open(const sdr_rtlsdr::RtlExternalRuntime& external) {
    sdr_rtlsdr::RtlProfile profile;
    profile.center_hz = 150'000'000U;
    profile.sample_rate_hz = 2'400'000U;
    profile.session_route = sdr_rtlsdr::RtlSessionRoute{"Mock", "RTL tuner", "00000001", 5U, 1U};
    bool refused{};
    try {
        static_cast<void>(sdr_rtlsdr::RtlRuntimeSession::start(
            sdr_rtlsdr::make_official_rtl_port(external), profile));
    } catch (const sdr_core::DeviceError&) { refused = true; }
    assert(refused);  // post-open count/descriptor recheck, before RX
}

void manual_gain(const sdr_rtlsdr::RtlExternalRuntime& external, const int gain, const bool success,
                 ResetCounters reset_counters, Counter counter, bool before_gain_setters = false,
                 std::uint32_t tuner_type = 5U) {
    reset_counters();
    sdr_rtlsdr::RtlProfile profile;
    profile.center_hz = 150'000'000U;
    profile.sample_rate_hz = 2'400'000U;
    profile.session_route = sdr_rtlsdr::RtlSessionRoute{"Mock", "RTL tuner", "00000001", tuner_type, 1U};
    profile.manual_tuner_gain_tenth_db = gain;
    bool refused{};
    try {
        auto owner = sdr_rtlsdr::RtlRuntimeSession::start(sdr_rtlsdr::make_official_rtl_port(external), profile);
        const auto readback = owner->readback();
        assert(readback.tuner_gain_readback_known && readback.cached_tuner_gain_tenth_db == gain);
        assert(owner->stop(2000ms).complete());
    } catch (const sdr_core::DeviceError&) { refused = true; }
    assert(refused != success);
    assert(counter(0) == 1 && counter(1) == 1);
    if (!success) assert(counter(2) == 0);  // no reader, not merely no crash
    if (before_gain_setters) assert(counter(3) == 0 && counter(4) == 0);
    assert(!sdr_rtlsdr::rtl_process_quarantined());
    // A failed configuration must have closed the exact owner before RX.
    auto port = sdr_rtlsdr::make_official_rtl_port(external);
    assert(port->open_exact_unique_serial("00000001") == 0);
    assert(port->close() == 0);
}
}  // namespace

int main(int argc, char** argv) {
    assert(argc == 3);
    accounting_oracle();
    const auto library = std::filesystem::weakly_canonical(std::filesystem::path(argv[1]));
    const sdr_rtlsdr::RtlExternalRuntime external{
        {utf8_path(library), sha256(library)}, {}};
    const auto module = LoadLibraryExW(library.c_str(), nullptr,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    assert(module != nullptr);
    const auto scenario = reinterpret_cast<Scenario>(GetProcAddress(module, "mock_rtl_set_scenario"));
    const auto reset_counters = reinterpret_cast<ResetCounters>(GetProcAddress(module, "mock_rtl_reset_counters"));
    const auto counter = reinterpret_cast<Counter>(GetProcAddress(module, "mock_rtl_counter"));
    const auto completed_callbacks = reinterpret_cast<CompletedCallbacks>(
        GetProcAddress(module, "mock_rtl_completed_callbacks"));
    assert(scenario && reset_counters && counter && completed_callbacks);
    scenario(0);
    official_cu8_transfers(external);
    selected_route_and_cu8(external, completed_callbacks);
    scenario(3);
    selected_route_and_cu8(external, completed_callbacks);  // exact owned-handle identity, no second USB opener
    {
        auto port = sdr_rtlsdr::make_official_rtl_port(external);
        assert(port->open_exact_unique_serial("00000001") == 0);
        assert(port->close() == 0);
    }
    scenario(1);
    count_changed_after_open(external);
    scenario(4);
    count_changed_after_open(external);  // owned-handle identity change still refuses
    scenario(5);
    count_changed_after_open(external);  // owned descriptor read failure still refuses
    scenario(0);
    manual_gain(external, -99, true, reset_counters, counter);
    manual_gain(external, 0, true, reset_counters, counter);
    manual_gain(external, 144, true, reset_counters, counter);
    manual_gain(external, 145, false, reset_counters, counter, true);  // no rounding
    for (const int failure : {6, 7, 8, 9, 10, 11, 12, 13}) {
        scenario(failure);
        manual_gain(external, 144, false, reset_counters, counter,
                    failure == 6 || failure == 7 || failure >= 11);
    }
    for (const int malformed_optional_table : {6, 7, 11, 12, 13}) {
        scenario(malformed_optional_table);
        selected_route_and_cu8(external, completed_callbacks, false);  // auto path still works
    }
    scenario(14);
    selected_route_and_cu8(external, completed_callbacks, false, 4U);  // FC2580 automatic remains valid
    manual_gain(external, 0, false, reset_counters, counter, true, 4U);
    scenario(0);
    FreeLibrary(module);
    // A legacy DLL with no optional gain exports retains automatic RX, while
    // manual requests fail before mode/value setters and the async reader.
    const auto legacy_library = std::filesystem::weakly_canonical(std::filesystem::path(argv[2]));
    const sdr_rtlsdr::RtlExternalRuntime legacy{{utf8_path(legacy_library), sha256(legacy_library)}, {}};
    const auto legacy_module = LoadLibraryExW(legacy_library.c_str(), nullptr,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    assert(legacy_module);
    assert(!GetProcAddress(legacy_module, "rtlsdr_get_tuner_gains"));
    const auto legacy_reset = reinterpret_cast<ResetCounters>(GetProcAddress(legacy_module, "mock_rtl_reset_counters"));
    const auto legacy_counter = reinterpret_cast<Counter>(GetProcAddress(legacy_module, "mock_rtl_counter"));
    const auto legacy_completed = reinterpret_cast<CompletedCallbacks>(
        GetProcAddress(legacy_module, "mock_rtl_completed_callbacks"));
    assert(legacy_reset && legacy_counter && legacy_completed);
    official_cu8_transfers(legacy);
    selected_route_and_cu8(legacy, legacy_completed, false);
    manual_gain(legacy, 0, false, legacy_reset, legacy_counter, true);
    FreeLibrary(legacy_module);
    return 0;
}
