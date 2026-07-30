#define _CRT_SECURE_NO_WARNINGS

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

struct iio_context {};
struct iio_scan_context {};
struct iio_context_info { const char* uri; const char* description; };
struct iio_device { int kind; };
struct iio_channel { int kind; bool output; bool enabled; };
struct iio_data_format {
    unsigned int length;
    unsigned int bits;
    unsigned int shift;
    bool is_signed;
    bool is_fully_defined;
    bool is_be;
    bool with_scale;
    double scale;
    unsigned int repeat;
};
struct iio_buffer { std::size_t samples; bool canceled; std::vector<std::uint8_t> bytes; };

namespace {
iio_device phy{0};
iio_device rx{1};
iio_channel phy_rx{0, false, false};
iio_channel lo{1, true, false};
iio_channel rx_i{2, false, false};
iio_channel rx_q{3, false, false};
iio_channel wrong_phy_rx{4, false, false};
iio_data_format format{16U, 12U, 0U, true, true, false, false, 1.0, 1U};
long long frequency = 2'450'000'000LL;
long long sample_rate = 3'000'000LL;
long long bandwidth = 1'500'000LL;
double gain = 20.0;
std::string gain_mode = "manual";
std::atomic<bool> cancel_in_progress{};
std::atomic<bool> cancel_release{true};
std::atomic<bool> destroyed_during_cancel{};

void delay_from_env(const char* name) {
    if (const char* raw = std::getenv(name); raw != nullptr) {
        const int milliseconds = std::max(0, std::atoi(raw));
        std::this_thread::sleep_for(std::chrono::milliseconds(milliseconds));
    }
}

const char* channel_id(const iio_channel* channel) {
    switch (channel->kind) {
    case 0: return "voltage0";
    case 1: return "altvoltage0";
    case 2: return "voltage0";
    case 3: return "voltage1";
    default: return "voltage0";
    }
}
bool has_attr(const iio_channel* channel, const char* attr) {
    if (channel == &phy_rx) return std::strcmp(attr, "sampling_frequency") == 0 || std::strcmp(attr, "sampling_frequency_available") == 0 ||
        std::strcmp(attr, "rf_bandwidth") == 0 || std::strcmp(attr, "rf_bandwidth_available") == 0 ||
        std::strcmp(attr, "hardwaregain") == 0 || std::strcmp(attr, "hardwaregain_available") == 0 ||
        std::strcmp(attr, "gain_control_mode") == 0 || std::strcmp(attr, "gain_control_mode_available") == 0;
    return channel == &lo && (std::strcmp(attr, "frequency") == 0 || std::strcmp(attr, "frequency_available") == 0);
}
std::string value_text(const iio_channel* channel, const char* attr) {
    if (std::strcmp(attr, "sampling_frequency") == 0) return std::to_string(sample_rate);
    if (std::strcmp(attr, "sampling_frequency_available") == 0) return "[2083333 1 61440000]";
    if (std::strcmp(attr, "rf_bandwidth") == 0) return std::to_string(bandwidth);
    if (std::strcmp(attr, "rf_bandwidth_available") == 0) return "[200000 1 56000000]";
    if (std::strcmp(attr, "hardwaregain") == 0) return std::to_string(gain);
    if (std::strcmp(attr, "hardwaregain_available") == 0) return "[-3 1 71]";
    if (std::strcmp(attr, "gain_control_mode") == 0) return gain_mode;
    if (std::strcmp(attr, "gain_control_mode_available") == 0) return "manual slow_attack fast_attack hybrid";
    if (channel == &lo && std::strcmp(attr, "frequency") == 0) return std::to_string(frequency);
    if (channel == &lo && std::strcmp(attr, "frequency_available") == 0) return "[70000000 1 6000000000]";
    return {};
}
}

extern "C" {
__declspec(dllexport) iio_scan_context* iio_create_scan_context(const char*, unsigned int) { return new iio_scan_context; }
__declspec(dllexport) void iio_scan_context_destroy(iio_scan_context* value) { delete value; }
__declspec(dllexport) std::ptrdiff_t iio_scan_context_get_info_list(iio_scan_context*, iio_context_info*** output) {
    auto** list = new iio_context_info*[1];
    static iio_context_info info{
        "usb:mock",
        "Analog Devices PlutoSDR mock, serial: TOP-SECRET-P07, transport USB"
    };
    list[0] = &info;
    *output = list;
    return 1;
}
__declspec(dllexport) void iio_context_info_list_free(iio_context_info** value) { delete[] value; }
__declspec(dllexport) const char* iio_context_info_get_description(const iio_context_info* value) { return value->description; }
__declspec(dllexport) const char* iio_context_info_get_uri(const iio_context_info* value) { return value->uri; }
__declspec(dllexport) void iio_library_get_version(unsigned int* major, unsigned int* minor, char tag[8]) {
    if (major) *major = 0U;
    if (minor) *minor = 26U;
    if (tag) strcpy_s(tag, 8U, "mock");
}
__declspec(dllexport) void iio_strerror(int error, char* dst, std::size_t length) { std::snprintf(dst, length, "mock error %d", error); }
__declspec(dllexport) unsigned int iio_get_backends_count() { return 2U; }
__declspec(dllexport) const char* iio_get_backend(unsigned int index) { return index == 0U ? "usb" : "ip"; }
__declspec(dllexport) iio_context* iio_create_context_from_uri(const char* uri) {
    delay_from_env("SDR_MOCK_LIBIIO_CONSTRUCTOR_DELAY_MS");
    return uri != nullptr && (std::strncmp(uri, "usb:", 4) == 0 || std::strncmp(uri, "ip:", 3) == 0) ? new iio_context : nullptr;
}
__declspec(dllexport) void iio_context_destroy(iio_context* value) { delete value; }
__declspec(dllexport) const char* iio_context_get_name(const iio_context*) { return "mock"; }
__declspec(dllexport) const char* iio_context_get_description(const iio_context*) { return "mock Pluto context"; }
__declspec(dllexport) int iio_context_get_version(const iio_context*, unsigned int* major, unsigned int* minor, char tag[8]) {
    *major = 0U;
    *minor = 25U;
    strcpy_s(tag, 8U, "mock");
    return 0;
}
__declspec(dllexport) int iio_context_set_timeout(iio_context*, unsigned int) { return 0; }
__declspec(dllexport) const char* iio_context_get_attr_value(const iio_context*, const char* attr) {
    if (std::strcmp(attr, "hw_model") == 0) return "PlutoSDR mock";
    if (std::strcmp(attr, "hw_serial") == 0) return "MOCK";
    if (std::strcmp(attr, "fw_version") == 0) return "mock-fw";
    return nullptr;
}
__declspec(dllexport) void mock_iio_reset_cancel_race() { cancel_in_progress = false; cancel_release = false; destroyed_during_cancel = false; }
__declspec(dllexport) int mock_iio_cancel_entered() { return cancel_in_progress.load() ? 1 : 0; }
__declspec(dllexport) void mock_iio_release_cancel() { cancel_release = true; }
__declspec(dllexport) int mock_iio_destroyed_during_cancel() { return destroyed_during_cancel.load() ? 1 : 0; }
__declspec(dllexport) unsigned int iio_context_get_devices_count(const iio_context*) { return 2U; }
__declspec(dllexport) iio_device* iio_context_get_device(const iio_context*, unsigned int index) { return index == 0U ? &phy : &rx; }
__declspec(dllexport) iio_device* iio_context_find_device(const iio_context*, const char* name) {
    if (std::strcmp(name, "ad9361-phy") == 0 || std::strcmp(name, "ad9364-phy") == 0) return &phy;
    if (std::strcmp(name, "cf-ad9361-lpc") == 0 || std::strcmp(name, "axi-ad9361-rx") == 0) return &rx;
    return nullptr;
}
__declspec(dllexport) const char* iio_device_get_id(const iio_device* value) { return value == &phy ? "iio:device0" : "iio:device1"; }
__declspec(dllexport) const char* iio_device_get_name(const iio_device* value) { return value == &phy ? "ad9361-phy" : "cf-ad9361-lpc"; }
__declspec(dllexport) unsigned int iio_device_get_channels_count(const iio_device* value) { return value == &phy && std::getenv("SDR_MOCK_LIBIIO_EXACT_WRONG") != nullptr ? 3U : 2U; }
__declspec(dllexport) iio_channel* iio_device_get_channel(const iio_device* value, unsigned int index) {
    if (value == &phy && std::getenv("SDR_MOCK_LIBIIO_EXACT_WRONG") != nullptr) {
        if (index == 0U) return &wrong_phy_rx;
        return index == 1U ? &phy_rx : &lo;
    }
    if (value == &phy) return index == 0U ? &phy_rx : &lo;
    return index == 0U ? &rx_i : &rx_q;
}
__declspec(dllexport) iio_channel* iio_device_find_channel(const iio_device* value, const char* name, bool output) {
    if (value == &phy && !output && std::strcmp(name, "voltage0") == 0) return std::getenv("SDR_MOCK_LIBIIO_EXACT_WRONG") != nullptr ? &wrong_phy_rx : &phy_rx;
    if (value == &phy && output && (std::strcmp(name, "altvoltage0") == 0 || std::strcmp(name, "RX_LO") == 0)) return &lo;
    if (value == &rx && !output && std::strcmp(name, "voltage0") == 0) return &rx_i;
    if (value == &rx && !output && std::strcmp(name, "voltage1") == 0) return &rx_q;
    return nullptr;
}
__declspec(dllexport) const char* iio_channel_get_id(const iio_channel* value) { return channel_id(value); }
__declspec(dllexport) const char* iio_channel_get_name(const iio_channel* value) { return channel_id(value); }
__declspec(dllexport) bool iio_channel_is_output(const iio_channel* value) { return value->output; }
__declspec(dllexport) const char* iio_channel_find_attr(const iio_channel* value, const char* attr) { return has_attr(value, attr) ? attr : nullptr; }
__declspec(dllexport) std::ptrdiff_t iio_channel_attr_read(const iio_channel* channel, const char* attr, char* dst, std::size_t length) {
    if (channel == &phy_rx && std::strcmp(attr, "gain_control_mode") == 0 && std::getenv("SDR_MOCK_LIBIIO_GAIN_MODE_READ_FAIL") != nullptr && gain_mode != "manual") return -EIO;
    const auto text = value_text(channel, attr); if (text.empty()) return -ENOENT;
    const auto count = std::min(length, text.size()); std::memcpy(dst, text.data(), count); return static_cast<std::ptrdiff_t>(count);
}
__declspec(dllexport) std::ptrdiff_t iio_channel_attr_write(const iio_channel* channel, const char* attr, const char* value) {
    if (channel != &phy_rx || std::strcmp(attr, "gain_control_mode") != 0) return -EINVAL;
    const std::string next(value); if (next != "manual" && next != "slow_attack" && next != "fast_attack" && next != "hybrid") return -EINVAL;
    if (std::getenv("SDR_MOCK_LIBIIO_GAIN_MODE_MISMATCH") == nullptr) gain_mode = next;
    return static_cast<std::ptrdiff_t>(next.size());
}
__declspec(dllexport) int iio_channel_attr_read_longlong(const iio_channel* channel, const char* attr, long long* value) {
    if (channel == &phy_rx && std::strcmp(attr, "sampling_frequency") == 0) *value = sample_rate;
    else if (channel == &phy_rx && std::strcmp(attr, "rf_bandwidth") == 0) *value = bandwidth;
    else if (channel == &lo && std::strcmp(attr, "frequency") == 0) *value = frequency;
    else return -EINVAL;
    return 0;
}
__declspec(dllexport) int iio_channel_attr_write_longlong(const iio_channel* channel, const char* attr, long long value) {
    if (channel == &phy_rx && std::strcmp(attr, "sampling_frequency") == 0) {
        delay_from_env("SDR_MOCK_LIBIIO_CONFIG_DELAY_MS");
        if (value < 2'083'333LL || value > 61'440'000LL) return -EINVAL;
        if (std::getenv("SDR_MOCK_LIBIIO_ROLLBACK_FAIL") != nullptr && value == 3'000'000LL) return -EIO;
        sample_rate = value; return 0;
    }
    if (channel == &phy_rx && std::strcmp(attr, "rf_bandwidth") == 0) {
        if (value < 200'000LL || value > 56'000'000LL) return -EINVAL; bandwidth = value; return 0;
    }
    if (channel == &lo && std::strcmp(attr, "frequency") == 0) {
        if (value < 70'000'000LL || value > 6'000'000'000LL) return -EINVAL; frequency = value; return 0;
    }
    return -EINVAL;
}
__declspec(dllexport) int iio_channel_attr_read_double(const iio_channel* channel, const char* attr, double* value) {
    if (channel != &phy_rx || std::strcmp(attr, "hardwaregain") != 0) return -EINVAL; *value = gain; return 0;
}
__declspec(dllexport) int iio_channel_attr_write_double(const iio_channel* channel, const char* attr, double value) {
    if (channel != &phy_rx || std::strcmp(attr, "hardwaregain") != 0 || value < -3.0 || value > 71.0) return -EINVAL; gain = value; return 0;
}
__declspec(dllexport) void iio_channel_enable(iio_channel* value) { value->enabled = true; }
__declspec(dllexport) void iio_channel_disable(iio_channel* value) { value->enabled = false; }
__declspec(dllexport) const iio_data_format* iio_channel_get_data_format(const iio_channel*) {
    format.bits = std::getenv("SDR_MOCK_LIBIIO_FORMAT_16") != nullptr ? 16U : 12U;
    format.shift = std::getenv("SDR_MOCK_LIBIIO_OVERFLOW_SHIFT") != nullptr
        ? ~0U
        : (std::getenv("SDR_MOCK_LIBIIO_INVALID_SHIFT") != nullptr ? 8U : 0U);
    return &format;
}
__declspec(dllexport) std::ptrdiff_t iio_device_get_sample_size(const iio_device* value) { return value == &rx && rx_i.enabled && rx_q.enabled ? 4 : -EINVAL; }
__declspec(dllexport) iio_buffer* iio_device_create_buffer(const iio_device* value, std::size_t count, bool cyclic) {
    if (value != &rx || cyclic || !rx_i.enabled || !rx_q.enabled || count == 0U) return nullptr;
    auto* buffer = new iio_buffer{count, false, std::vector<std::uint8_t>(count * 4U)};
    return buffer;
}
__declspec(dllexport) void iio_buffer_destroy(iio_buffer* value) { if (cancel_in_progress.load()) destroyed_during_cancel = true; delete value; }
__declspec(dllexport) std::ptrdiff_t iio_buffer_refill(iio_buffer* value) {
    delay_from_env("SDR_MOCK_LIBIIO_REFILL_DELAY_MS");
    if (value->canceled) return -ECANCELED;
    if (std::getenv("SDR_MOCK_LIBIIO_REFILL_FAIL") != nullptr) return -EIO;
    for (std::size_t index = 0U; index < value->samples; ++index) {
        const auto i = static_cast<std::int16_t>((static_cast<int>(index) % 4096) - 2048);
        const auto q = static_cast<std::int16_t>(2047 - (static_cast<int>(index) % 4096));
        std::memcpy(value->bytes.data() + index * 4U, &i, 2U);
        std::memcpy(value->bytes.data() + index * 4U + 2U, &q, 2U);
    }
    const bool short_read = std::getenv("SDR_MOCK_LIBIIO_SHORT_READ") != nullptr;
    const auto returned = short_read && value->samples > 1U
        ? (value->samples - 1U) * 4U
        : value->bytes.size();
    return static_cast<std::ptrdiff_t>(returned);
}
__declspec(dllexport) void iio_buffer_cancel(iio_buffer* value) {
    value->canceled = true;
    if (std::getenv("SDR_MOCK_LIBIIO_BLOCK_CANCEL") == nullptr) return;
    bool expected = false;
    if (cancel_in_progress.compare_exchange_strong(expected, true)) {
        while (!cancel_release.load()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        cancel_in_progress = false;
    }
}
__declspec(dllexport) void* iio_buffer_first(const iio_buffer* value, const iio_channel* channel) {
    return const_cast<std::uint8_t*>(value->bytes.data()) + (channel == &rx_q ? 2U : 0U);
}
__declspec(dllexport) std::ptrdiff_t iio_buffer_step(const iio_buffer*) { return 4; }
__declspec(dllexport) void* iio_buffer_end(const iio_buffer* value) {
    const auto trim = std::getenv("SDR_MOCK_LIBIIO_TRUNCATED_END") != nullptr ? 1U : 0U;
    return const_cast<std::uint8_t*>(value->bytes.data()) + value->bytes.size() - trim;
}
}