#include "sdr_rtlsdr/rtl_official_port.hpp"
#include "sdr_core/errors.hpp"

#include <array>
#ifdef NDEBUG
#undef NDEBUG  // Release qualification must execute checks and hash side effects.
#endif
#include <cassert>
#include <chrono>
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

void selected_route_and_cu8(const sdr_rtlsdr::RtlExternalRuntime& external) {
    const auto observed = sdr_rtlsdr::observe_single_rtl_candidate(external);
    assert(observed.enumeration_index == 0U && observed.serial == "00000001");
    assert(observed.tuner_type == 5U && !observed.direct_sampling && !observed.offset_tuning);
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
    for (int trial = 0; trial < 200 && owner->metrics().dsp.fft_frames_computed == 0U; ++trial) {
        std::this_thread::sleep_for(2ms);
    }
    assert(owner->metrics().samples_admitted == 24'576U);
    assert(owner->metrics().dsp.fft_frames_computed > 0U);
    const auto latest = owner->drain_latest_spectrum_frame();
    assert(latest.frame && latest.frame->source.backend_id == "native.librtlsdr.unbundled.cpu.v1");
    bool probe_refused{};
    try {
        static_cast<void>(sdr_rtlsdr::observe_single_rtl_candidate(external));
    } catch (const sdr_core::DeviceError&) { probe_refused = true; }
    assert(probe_refused);  // selected probe cannot open over this live owner
    assert(owner->stop(2000ms).complete());
    assert(!owner->cleanup_required());
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
}  // namespace

int main(int argc, char** argv) {
    assert(argc == 2);
    const auto library = std::filesystem::weakly_canonical(std::filesystem::path(argv[1]));
    const sdr_rtlsdr::RtlExternalRuntime external{
        {utf8_path(library), sha256(library)}, {}};
    const auto module = LoadLibraryExW(library.c_str(), nullptr,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    assert(module != nullptr);
    const auto scenario = reinterpret_cast<Scenario>(GetProcAddress(module, "mock_rtl_set_scenario"));
    assert(scenario != nullptr);
    scenario(0);
    selected_route_and_cu8(external);
    scenario(3);
    selected_route_and_cu8(external);  // exact owned-handle identity, no second USB opener
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
    FreeLibrary(module);
    return 0;
}
