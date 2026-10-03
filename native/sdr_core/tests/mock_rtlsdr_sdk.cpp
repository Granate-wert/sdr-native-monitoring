// Synthetic ABI fixture only. This is not an Osmocom SDK binary, header, or
// emulation of hardware RF behavior; it must never be installed or packaged.
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <thread>

namespace {
struct MockDevice final {
    std::uint32_t rate{};
    std::uint32_t center{};
    int gain{};
    bool manual{};
};
MockDevice selected{};
std::atomic<int> scenario{};
std::atomic<bool> opened{};
std::atomic<bool> reading{};
std::atomic<bool> cancelled{};
std::atomic<int> owned_descriptor_reads{};
std::atomic<int> open_calls{}, close_calls{}, read_calls{}, manual_calls{}, gain_calls{};

void strings(char* manufacturer, char* product, char* serial) {
    std::memcpy(manufacturer, "Mock", sizeof("Mock"));
    std::memcpy(product, "RTL tuner", sizeof("RTL tuner"));
    std::memcpy(serial, "00000001", sizeof("00000001"));
}
}  // namespace

extern "C" {
__declspec(dllexport) void __cdecl mock_rtl_set_scenario(int value) { scenario.store(value); }
__declspec(dllexport) void __cdecl mock_rtl_reset_counters() {
    open_calls.store(0); close_calls.store(0); read_calls.store(0);
    manual_calls.store(0); gain_calls.store(0);
}
__declspec(dllexport) int __cdecl mock_rtl_counter(int field) {
    switch (field) {
    case 0: return open_calls.load();
    case 1: return close_calls.load();
    case 2: return read_calls.load();
    case 3: return manual_calls.load();
    case 4: return gain_calls.load();
    default: return -1;
    }
}
__declspec(dllexport) std::uint32_t __cdecl rtlsdr_get_device_count() {
    return scenario.load() == 1 && opened.load() ? 2U : 1U;
}
__declspec(dllexport) int __cdecl rtlsdr_get_device_usb_strings(std::uint32_t index,
                                                               char* manufacturer, char* product, char* serial) {
    if (index != 0U) return -1;
    // On Windows the index-based descriptor helper opens a SECOND USB handle.
    // WinUSB may refuse it while the acquisition handle owns the interface.
    if (scenario.load() == 3 && opened.load()) return -13;
    strings(manufacturer, product, serial);
    return 0;
}
__declspec(dllexport) int __cdecl rtlsdr_open(void** device, std::uint32_t index) {
    open_calls.fetch_add(1);
    if (index != 0U || device == nullptr) return -2;
    selected = {};
    owned_descriptor_reads.store(0);
    opened.store(true);
    cancelled.store(false);
    *device = &selected;
    return 0;
}
__declspec(dllexport) int __cdecl rtlsdr_close(void* device) {
    close_calls.fetch_add(1);
    if (device != &selected) return -3;
    if (scenario.load() == 2) return -4;
    opened.store(false);
    return 0;
}
__declspec(dllexport) int __cdecl rtlsdr_get_usb_strings(void* device,
                                                         char* manufacturer, char* product, char* serial) {
    if (device != &selected) return -5;
    const auto subsequent = owned_descriptor_reads.fetch_add(1) > 0;
    if (scenario.load() == 5 && subsequent) return -14;
    strings(manufacturer, product, serial);
    if (scenario.load() == 4 && subsequent) {
        std::memcpy(product, "Changed tuner", sizeof("Changed tuner"));
    }
    return 0;
}
__declspec(dllexport) int __cdecl rtlsdr_get_tuner_type(void* device) {
    return device == &selected ? 5 : 0;
}
__declspec(dllexport) int __cdecl rtlsdr_get_direct_sampling(void* device) {
    return device == &selected ? 0 : -1;
}
__declspec(dllexport) int __cdecl rtlsdr_get_offset_tuning(void* device) {
    return device == &selected ? 0 : -1;
}
__declspec(dllexport) int __cdecl rtlsdr_set_sample_rate(void* device, std::uint32_t value) {
    if (device != &selected) return -6;
    selected.rate = value;
    return 0;
}
__declspec(dllexport) std::uint32_t __cdecl rtlsdr_get_sample_rate(void* device) {
    return device == &selected ? selected.rate : 0U;
}
__declspec(dllexport) int __cdecl rtlsdr_set_center_freq(void* device, std::uint32_t value) {
    if (device != &selected) return -7;
    selected.center = value;
    return 0;
}
__declspec(dllexport) std::uint32_t __cdecl rtlsdr_get_center_freq(void* device) {
    return device == &selected ? selected.center : 0U;
}
__declspec(dllexport) int __cdecl rtlsdr_set_tuner_gain_mode(void* device, int automatic) {
    if (automatic == 1) manual_calls.fetch_add(1);
    if (device != &selected || (automatic != 0 && automatic != 1)) return -8;
    selected.manual = automatic == 1;
    return scenario.load() == 9 ? -18 : 0;
}
#ifndef MOCK_RTL_NO_MANUAL_GAIN
__declspec(dllexport) int __cdecl rtlsdr_get_tuner_gains(void* device, int* gains) {
    if (device != &selected) return -1;
    if (scenario.load() == 6) return 257;
    int table[] = {-99, 0, 144, 496};
    if (scenario.load() == 11) table[2] = 0;  // duplicate
    if (scenario.load() == 12) table[3] = 1001;  // unbounded value
    if (scenario.load() == 13) table[0] = 9;  // unsorted
    if (gains) std::memcpy(gains, table, sizeof(table));
    return scenario.load() == 7 && gains ? 3 : 4;
}
__declspec(dllexport) int __cdecl rtlsdr_set_tuner_gain(void* device, int gain) {
    gain_calls.fetch_add(1);
    if (device != &selected || !selected.manual) return -1;
    if (scenario.load() == 10) return -19;
    selected.gain = gain;
    return 0;
}
__declspec(dllexport) int __cdecl rtlsdr_get_tuner_gain(void* device) {
    return device == &selected ? selected.gain + (scenario.load() == 8 ? 1 : 0) : 0;
}
#endif
__declspec(dllexport) int __cdecl rtlsdr_reset_buffer(void* device) {
    return device == &selected ? 0 : -9;
}
__declspec(dllexport) int __cdecl rtlsdr_read_async(
    void* device, void (__cdecl *callback)(unsigned char*, std::uint32_t, void*),
    void* context, std::uint32_t, std::uint32_t buffer_bytes) {
    read_calls.fetch_add(1);
    if (device != &selected || callback == nullptr || buffer_bytes != 32'768U) return -10;
    reading.store(true);
    unsigned char samples[32'768]{};
    for (std::size_t index = 0U; index < sizeof(samples); index += 2U) {
        samples[index] = index % 16U == 0U ? 255U : 128U;
        samples[index + 1U] = 128U;
    }
    callback(samples, 16'384U, context);  // valid short then full transfer
    callback(samples, 32'768U, context);
    for (int trial = 0; trial < 5000 && !cancelled.load(); ++trial) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    reading.store(false);
    return cancelled.load() ? 0 : -11;
}
__declspec(dllexport) int __cdecl rtlsdr_cancel_async(void* device) {
    if (device != &selected) return -12;
    if (!reading.load()) return -2;
    cancelled.store(true);
    return 0;
}
}  // extern "C"
