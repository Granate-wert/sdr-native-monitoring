#include "sdr_pluto/pluto_backend.hpp"

#include "sdr_core/buffer_pool.hpp"
#include "sdr_core/errors.hpp"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#ifdef DeviceCapabilities
#undef DeviceCapabilities
#endif

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <initializer_list>
#include <memory>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

namespace sdr_pluto {
namespace {
struct iio_context;
struct iio_device;
struct iio_channel;
struct iio_buffer;
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
using ssize_type = std::ptrdiff_t;

std::string narrow(const std::wstring& value) {
    if (value.empty()) return {};
    const int size = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    if (size <= 0) return {};
    std::string result(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), size, nullptr, nullptr);
    return result;
}

class Library final {
public:
    Library() {
        std::vector<std::filesystem::path> candidates;
        std::array<wchar_t, 32768> env{};
        const DWORD explicit_len = GetEnvironmentVariableW(L"LIBIIO_DLL_PATH", env.data(), static_cast<DWORD>(env.size()));
        if (explicit_len > 0U && explicit_len < env.size()) candidates.emplace_back(env.data());
        const DWORD program_len = GetEnvironmentVariableW(L"ProgramFiles", env.data(), static_cast<DWORD>(env.size()));
        if (program_len > 0U && program_len < env.size()) {
            candidates.emplace_back(std::filesystem::path(env.data()) / L"IIO Oscilloscope" / L"bin" / L"libiio.dll");
        }
        candidates.emplace_back(L"libiio.dll");
        for (const auto& candidate : candidates) {
            module_ = LoadLibraryExW(candidate.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR);
            if (module_ != nullptr) return;
        }
        throw sdr_core::BackendUnavailableError("libiio.dll not found");
    }
    ~Library() { if (module_ != nullptr) FreeLibrary(module_); }
    Library(const Library&) = delete;
    Library& operator=(const Library&) = delete;
    template <typename T> T symbol(const char* name) const {
        const auto address = GetProcAddress(module_, name);
        if (address == nullptr) throw sdr_core::BackendUnavailableError(std::string("libiio export missing: ") + name);
        return reinterpret_cast<T>(address);
    }
private:
    HMODULE module_{};
};

struct Api final {
    using create_context_fn = iio_context* (*)(const char*);
    using destroy_context_fn = void (*)(iio_context*);
    using timeout_fn = int (*)(iio_context*, unsigned int);
    using devices_count_fn = unsigned int (*)(const iio_context*);
    using get_device_fn = iio_device* (*)(const iio_context*, unsigned int);
    using find_device_fn = iio_device* (*)(const iio_context*, const char*);
    using device_string_fn = const char* (*)(const iio_device*);
    using channels_count_fn = unsigned int (*)(const iio_device*);
    using get_channel_fn = iio_channel* (*)(const iio_device*, unsigned int);
    using find_channel_fn = iio_channel* (*)(const iio_device*, const char*, bool);
    using channel_string_fn = const char* (*)(const iio_channel*);
    using channel_output_fn = bool (*)(const iio_channel*);
    using channel_find_attr_fn = const char* (*)(const iio_channel*, const char*);
    using read_text_fn = ssize_type (*)(const iio_channel*, const char*, char*, std::size_t);
    using write_text_fn = ssize_type (*)(const iio_channel*, const char*, const char*);
    using read_ll_fn = int (*)(const iio_channel*, const char*, long long*);
    using write_ll_fn = int (*)(const iio_channel*, const char*, long long);
    using read_double_fn = int (*)(const iio_channel*, const char*, double*);
    using write_double_fn = int (*)(const iio_channel*, const char*, double);
    using toggle_fn = void (*)(iio_channel*);
    using format_fn = const iio_data_format* (*)(const iio_channel*);
    using sample_size_fn = ssize_type (*)(const iio_device*);
    using create_buffer_fn = iio_buffer* (*)(const iio_device*, std::size_t, bool);
    using destroy_buffer_fn = void (*)(iio_buffer*);
    using refill_fn = ssize_type (*)(iio_buffer*);
    using cancel_fn = void (*)(iio_buffer*);
    using first_fn = void* (*)(const iio_buffer*, const iio_channel*);
    using step_fn = std::ptrdiff_t (*)(const iio_buffer*);
    using end_fn = void* (*)(const iio_buffer*);
    using strerror_fn = void (*)(int, char*, std::size_t);

    Library library;
    create_context_fn create_context{library.symbol<create_context_fn>("iio_create_context_from_uri")};
    destroy_context_fn destroy_context{library.symbol<destroy_context_fn>("iio_context_destroy")};
    timeout_fn set_timeout{library.symbol<timeout_fn>("iio_context_set_timeout")};
    devices_count_fn devices_count{library.symbol<devices_count_fn>("iio_context_get_devices_count")};
    get_device_fn get_device{library.symbol<get_device_fn>("iio_context_get_device")};
    find_device_fn find_device{library.symbol<find_device_fn>("iio_context_find_device")};
    device_string_fn device_id{library.symbol<device_string_fn>("iio_device_get_id")};
    device_string_fn device_name{library.symbol<device_string_fn>("iio_device_get_name")};
    channels_count_fn channels_count{library.symbol<channels_count_fn>("iio_device_get_channels_count")};
    get_channel_fn get_channel{library.symbol<get_channel_fn>("iio_device_get_channel")};
    find_channel_fn find_channel{library.symbol<find_channel_fn>("iio_device_find_channel")};
    channel_string_fn channel_id{library.symbol<channel_string_fn>("iio_channel_get_id")};
    channel_string_fn channel_name{library.symbol<channel_string_fn>("iio_channel_get_name")};
    channel_output_fn channel_output{library.symbol<channel_output_fn>("iio_channel_is_output")};
    channel_find_attr_fn find_attr{library.symbol<channel_find_attr_fn>("iio_channel_find_attr")};
    read_text_fn read_text{library.symbol<read_text_fn>("iio_channel_attr_read")};
    write_text_fn write_text{library.symbol<write_text_fn>("iio_channel_attr_write")};
    read_ll_fn read_ll{library.symbol<read_ll_fn>("iio_channel_attr_read_longlong")};
    write_ll_fn write_ll{library.symbol<write_ll_fn>("iio_channel_attr_write_longlong")};
    read_double_fn read_double{library.symbol<read_double_fn>("iio_channel_attr_read_double")};
    write_double_fn write_double{library.symbol<write_double_fn>("iio_channel_attr_write_double")};
    toggle_fn enable{library.symbol<toggle_fn>("iio_channel_enable")};
    toggle_fn disable{library.symbol<toggle_fn>("iio_channel_disable")};
    format_fn format{library.symbol<format_fn>("iio_channel_get_data_format")};
    sample_size_fn sample_size{library.symbol<sample_size_fn>("iio_device_get_sample_size")};
    create_buffer_fn create_buffer{library.symbol<create_buffer_fn>("iio_device_create_buffer")};
    destroy_buffer_fn destroy_buffer{library.symbol<destroy_buffer_fn>("iio_buffer_destroy")};
    refill_fn refill{library.symbol<refill_fn>("iio_buffer_refill")};
    cancel_fn cancel{library.symbol<cancel_fn>("iio_buffer_cancel")};
    first_fn first{library.symbol<first_fn>("iio_buffer_first")};
    step_fn step{library.symbol<step_fn>("iio_buffer_step")};
    end_fn end{library.symbol<end_fn>("iio_buffer_end")};
    strerror_fn strerror_value{library.symbol<strerror_fn>("iio_strerror")};
    [[nodiscard]] std::string error(const int code) const {
        std::array<char, 256> text{};
        strerror_value(code < 0 ? -code : code, text.data(), text.size());
        return std::string(text.data()) + " (" + std::to_string(code) + ")";
    }
};

std::string safe(const char* value) { return value == nullptr ? std::string{} : std::string(value); }
std::string lower_copy(std::string value) {
    for (auto& ch : value) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    return value;
}
bool contains_ci(const std::string& value, const std::string_view needle) { return lower_copy(value).find(needle) != std::string::npos; }

iio_device* find_device(const Api& api, const iio_context* context, std::initializer_list<const char*> exact, std::initializer_list<std::string_view> fragments) {
    for (const char* name : exact) if (auto* value = api.find_device(context, name); value != nullptr) return value;
    for (unsigned int index = 0U; index < api.devices_count(context); ++index) {
        auto* value = api.get_device(context, index);
        const auto identity = safe(api.device_id(value)) + " " + safe(api.device_name(value));
        for (const auto fragment : fragments) if (contains_ci(identity, fragment)) return value;
    }
    return nullptr;
}

iio_channel* find_channel(const Api& api, const iio_device* device, std::initializer_list<const char*> exact, const bool output, std::initializer_list<std::string_view> fragments, const char* required_attr = nullptr) {
    for (const char* name : exact) {
        if (auto* value = api.find_channel(device, name, output); value != nullptr) {
            if (required_attr == nullptr || api.find_attr(value, required_attr) != nullptr) return value;
        }
    }
    for (unsigned int index = 0U; index < api.channels_count(device); ++index) {
        auto* value = api.get_channel(device, index);
        if (api.channel_output(value) != output) continue;
        if (required_attr != nullptr && api.find_attr(value, required_attr) == nullptr) continue;
        const auto identity = safe(api.channel_id(value)) + " " + safe(api.channel_name(value));
        for (const auto fragment : fragments) if (contains_ci(identity, fragment)) return value;
    }
    return nullptr;
}
std::string read_text(const Api& api, const iio_channel* channel, const char* attr) {
    std::array<char, 1024> buffer{};
    const auto result = api.read_text(channel, attr, buffer.data(), buffer.size() - 1U);
    if (result < 0) return {};
    const auto length = std::min<std::size_t>(static_cast<std::size_t>(result), buffer.size() - 1U);
    std::string value(buffer.data(), length);
    while (!value.empty() && (value.back() == '\0' || value.back() == ' ' || value.back() == '\t' ||
                              value.back() == '\r' || value.back() == '\n')) {
        value.pop_back();
    }
    return value;
}
std::string read_text_required(const Api& api, const iio_channel* channel, const char* attr) {
    const auto value = read_text(api, channel, attr);
    if (value.empty()) throw std::runtime_error(std::string("required read ") + attr + " failed or returned empty");
    return value;
}
long long read_ll(const Api& api, const iio_channel* channel, const char* attr) {
    long long value{};
    const int result = api.read_ll(channel, attr, &value);
    if (result < 0) throw std::runtime_error(std::string("read ") + attr + " failed: " + api.error(result));
    return value;
}
double read_double(const Api& api, const iio_channel* channel, const char* attr) {
    double value{};
    const int result = api.read_double(channel, attr, &value);
    if (result < 0) throw std::runtime_error(std::string("read ") + attr + " failed: " + api.error(result));
    return value;
}
void write_ll(const Api& api, const iio_channel* channel, const char* attr, const long long value) {
    const int result = api.write_ll(channel, attr, value);
    if (result < 0) throw sdr_core::ConfigurationError(std::string("write ") + attr + " failed: " + api.error(result));
}
void write_double(const Api& api, const iio_channel* channel, const char* attr, const double value) {
    const int result = api.write_double(channel, attr, value);
    if (result < 0) throw sdr_core::ConfigurationError(std::string("write ") + attr + " failed: " + api.error(result));
}
void write_text(const Api& api, const iio_channel* channel, const char* attr, const std::string& value) {
    const auto result = api.write_text(channel, attr, value.c_str());
    if (result < 0) throw sdr_core::ConfigurationError(std::string("write ") + attr + " failed: " + api.error(static_cast<int>(result)));
}
std::vector<double> parse_numbers(const std::string& text) {
    std::vector<double> values;
    const char* cursor = text.c_str();
    while (*cursor != '\0') {
        char* end = nullptr;
        const double value = std::strtod(cursor, &end);
        if (end != cursor) {
            if (std::isfinite(value)) values.push_back(value);
            cursor = end;
        } else {
            ++cursor;
        }
    }
    return values;
}
sdr_core::NumericRange range_from_available(const std::string& text, const double current) {
    const auto values = parse_numbers(text);
    if (values.empty()) return {current, current, std::nullopt};
    if (values.size() == 3U && values[1] > 0.0 && values[2] >= values[0]) {
        return {values[0], values[2], values[1]};
    }
    const auto bounds = std::minmax_element(values.begin(), values.end());
    return {*bounds.first, *bounds.second, std::nullopt};
}
std::string mode_wire(const sdr_core::GainMode mode) {
    switch (mode) {
    case sdr_core::GainMode::Manual: return "manual";
    case sdr_core::GainMode::SlowAttack: return "slow_attack";
    case sdr_core::GainMode::FastAttack: return "fast_attack";
    case sdr_core::GainMode::Hybrid: return "hybrid";
    }
    throw sdr_core::ConfigurationError("unknown gain mode");
}
sdr_core::GainMode mode_from_text(const std::string& value) {
    const auto lower = lower_copy(value);
    if (lower == "manual") return sdr_core::GainMode::Manual;
    if (lower == "slow_attack") return sdr_core::GainMode::SlowAttack;
    if (lower == "fast_attack") return sdr_core::GainMode::FastAttack;
    if (lower == "hybrid") return sdr_core::GainMode::Hybrid;
    throw sdr_core::ConfigurationError("unknown or empty gain mode readback: " + value);
}
std::vector<sdr_core::GainMode> modes_from_available(const std::string& text) {
    std::vector<sdr_core::GainMode> result{sdr_core::GainMode::Manual};
    const auto lower = lower_copy(text);
    const auto add = [&result](const sdr_core::GainMode mode) {
        if (std::find(result.begin(), result.end(), mode) == result.end()) result.push_back(mode);
    };
    if (lower.find("slow_attack") != std::string::npos) add(sdr_core::GainMode::SlowAttack);
    if (lower.find("fast_attack") != std::string::npos) add(sdr_core::GainMode::FastAttack);
    if (lower.find("hybrid") != std::string::npos) add(sdr_core::GainMode::Hybrid);
    return result;
}
std::int16_t normalize_sample(const std::uint8_t* bytes, const iio_data_format& format) {
    std::uint16_t raw = format.is_be
        ? static_cast<std::uint16_t>((static_cast<std::uint16_t>(bytes[0]) << 8U) | bytes[1])
        : static_cast<std::uint16_t>(bytes[0] | (static_cast<std::uint16_t>(bytes[1]) << 8U));
    raw = static_cast<std::uint16_t>(raw >> format.shift);
    const auto bits = std::min(format.bits, 16U);
    const std::uint32_t mask = bits == 16U ? 0xFFFFU : ((1U << bits) - 1U);
    std::uint32_t value = static_cast<std::uint32_t>(raw) & mask;
    if (format.is_signed && bits > 0U && (value & (1U << (bits - 1U))) != 0U) value |= ~mask;
    return static_cast<std::int16_t>(static_cast<std::int32_t>(value));
}
void store_le_i16(std::uint8_t* destination, const std::int16_t value) {
    const auto raw = static_cast<std::uint16_t>(value);
    destination[0] = static_cast<std::uint8_t>(raw & 0xFFU);
    destination[1] = static_cast<std::uint8_t>((raw >> 8U) & 0xFFU);
}
}  // namespace

class PlutoDevice::Impl final {
public:
    Impl(std::string uri, const std::uint32_t timeout_ms)
        : api_(std::make_unique<Api>()), uri_(std::move(uri)) {
        if (!(uri_.starts_with("usb:") || uri_.starts_with("ip:"))) throw std::invalid_argument("Pluto URI must start with usb: or ip:");
        probe_ = probe_context(uri_, timeout_ms);
        context_ = api_->create_context(uri_.c_str());
        if (context_ == nullptr) throw std::runtime_error("iio_create_context_from_uri failed for " + uri_);
        try {
            const int timeout_result = api_->set_timeout(context_, timeout_ms);
            if (timeout_result < 0) throw std::runtime_error("iio_context_set_timeout failed: " + api_->error(timeout_result));
            discover_locked();
            capabilities_ = read_capabilities_locked();
            connected_.store(true, std::memory_order_release);
        } catch (...) {
            api_->destroy_context(context_);
            context_ = nullptr;
            throw;
        }
    }
    ~Impl() { disconnect(); }

    bool connected() const noexcept {
        return connected_.load(std::memory_order_acquire);
    }
    std::string uri() const { std::scoped_lock lock(mutex_); return uri_; }
    ContextProbe probe() const { std::scoped_lock lock(mutex_); require_connected_locked(); return probe_; }
    sdr_core::DeviceCapabilities capabilities() const { std::scoped_lock lock(mutex_); require_connected_locked(); return capabilities_; }

    AppliedConfig configure(const sdr_core::DeviceConfig& config, const std::uint32_t output_pool_blocks) {
        sdr_core::validate(config);
        if (output_pool_blocks == 0U) {
            throw sdr_core::ConfigurationError(
                "Pluto output_pool_blocks must be positive"
            );
        }
        if (config.buffer_samples > std::numeric_limits<std::uint32_t>::max() / 4U) {
            throw sdr_core::ConfigurationError("Pluto buffer_samples exceeds byte-buffer capacity");
        }
        if (config.context_uri != uri_) throw sdr_core::ConfigurationError("DeviceConfig context_uri differs from open Pluto URI");
        std::scoped_lock lock(mutex_);
        require_connected_locked();
        stop_stream_locked();
        const auto old_rate = read_ll(*api_, phy_rx_, "sampling_frequency");
        const auto old_bandwidth = read_ll(*api_, phy_rx_, "rf_bandwidth");
        const auto old_frequency = read_ll(*api_, lo_, "frequency");
        const auto old_mode = read_text_required(*api_, phy_rx_, "gain_control_mode");
        const auto old_mode_value = mode_from_text(old_mode);
        const auto old_gain = read_double(*api_, phy_rx_, "hardwaregain");
        try {
            write_ll(*api_, phy_rx_, "sampling_frequency", static_cast<long long>(std::llround(config.sample_rate_hz)));
            write_ll(*api_, phy_rx_, "rf_bandwidth", static_cast<long long>(std::llround(config.analog_bandwidth_hz)));
            write_ll(*api_, lo_, "frequency", static_cast<long long>(std::llround(config.center_frequency_hz)));
            write_text(*api_, phy_rx_, "gain_control_mode", mode_wire(config.gain_mode));
            if (config.gain_mode == sdr_core::GainMode::Manual) write_double(*api_, phy_rx_, "hardwaregain", config.manual_gain_db);
            AppliedConfig candidate{
                .requested = config,
                .center_frequency_hz = static_cast<double>(read_ll(*api_, lo_, "frequency")),
                .sample_rate_hz = static_cast<double>(read_ll(*api_, phy_rx_, "sampling_frequency")),
                .analog_bandwidth_hz = static_cast<double>(read_ll(*api_, phy_rx_, "rf_bandwidth")),
                .gain_mode = mode_from_text(read_text_required(*api_, phy_rx_, "gain_control_mode")),
                .manual_gain_db = read_double(*api_, phy_rx_, "hardwaregain"),
                .config_generation = generation_ + 1U,
                .sample_layout = sample_layout_locked(),
            };
            if (candidate.gain_mode != config.gain_mode) throw sdr_core::ConfigurationError("gain mode readback differs from requested mode");
            auto candidate_pool = std::make_unique<sdr_core::BufferPool>(
                output_pool_blocks, config.buffer_samples * 4U
            );
            applied_ = std::move(candidate);
            pool_ = std::move(candidate_pool);
            generation_ = applied_.config_generation;
            sequence_ = 0U;
            first_sample_index_ = 0U;
            {
                std::scoped_lock metrics_lock(metrics_mutex_);
                metrics_ = {};
            }
            configured_ = true;
            return applied_;
        } catch (...) {
            const auto original = std::current_exception();
            bool rollback_ok = true;
            const auto rollback = [&rollback_ok](auto&& operation) {
                try { operation(); } catch (...) { rollback_ok = false; }
            };
            rollback([&] { write_ll(*api_, phy_rx_, "sampling_frequency", old_rate); });
            rollback([&] { write_ll(*api_, phy_rx_, "rf_bandwidth", old_bandwidth); });
            rollback([&] { write_ll(*api_, lo_, "frequency", old_frequency); });
            rollback([&] { write_text(*api_, phy_rx_, "gain_control_mode", old_mode); });
            if (old_mode_value == sdr_core::GainMode::Manual) {
                rollback([&] { write_double(*api_, phy_rx_, "hardwaregain", old_gain); });
            }
            rollback([&] {
                rollback_ok = rollback_ok && read_ll(*api_, phy_rx_, "sampling_frequency") == old_rate;
                rollback_ok = rollback_ok && read_ll(*api_, phy_rx_, "rf_bandwidth") == old_bandwidth;
                rollback_ok = rollback_ok && read_ll(*api_, lo_, "frequency") == old_frequency;
                rollback_ok = rollback_ok && mode_from_text(read_text_required(*api_, phy_rx_, "gain_control_mode")) == old_mode_value;
                if (old_mode_value == sdr_core::GainMode::Manual) {
                    rollback_ok = rollback_ok && std::abs(read_double(*api_, phy_rx_, "hardwaregain") - old_gain) < 1.0e-9;
                }
            });
            if (!rollback_ok) {
                configured_ = false;
                applied_ = {};
                if (pool_) pool_->request_stop();
                pool_.reset();
                ++generation_;
                throw sdr_core::ConfigurationError(
                    "Pluto configuration failed and hardware rollback could not be verified; successful reconfigure required"
                );
            }
            std::rethrow_exception(original);
        }
    }
    AppliedConfig applied_config() const {
        std::scoped_lock lock(mutex_);
        require_configured_locked();
        return applied_;
    }
    void start_stream() {
        std::scoped_lock lock(mutex_);
        require_configured_locked();
        if (buffer_ != nullptr) return;
        api_->enable(rx_i_);
        api_->enable(rx_q_);
        const auto stride = api_->sample_size(rx_stream_);
        if (stride != 4) {
            api_->disable(rx_i_);
            api_->disable(rx_q_);
            throw std::runtime_error("AD936x RX sample stride must be 4 bytes, got " + std::to_string(stride));
        }
        buffer_ = api_->create_buffer(rx_stream_, applied_.requested.buffer_samples, false);
        if (buffer_ == nullptr) {
            api_->disable(rx_i_);
            api_->disable(rx_q_);
            throw std::runtime_error("iio_device_create_buffer failed for non-cyclic RX");
        }
        try {
            applied_.sample_layout = sample_layout_locked();
        } catch (...) {
            api_->destroy_buffer(buffer_);
            buffer_ = nullptr;
            api_->disable(rx_i_);
            api_->disable(rx_q_);
            throw;
        }
        {
            std::scoped_lock cancel_lock(cancel_mutex_);
            cancel_buffer_ = buffer_;
        }
        streaming_.store(true, std::memory_order_release);
    }
    sdr_core::IqBlock refill() {
        std::scoped_lock lock(mutex_);
        require_configured_locked();
        if (buffer_ == nullptr) throw std::runtime_error("Pluto RX stream is not started");
        const auto received_bytes = api_->refill(buffer_);
        if (received_bytes < 0) {
            {
                std::scoped_lock metrics_lock(metrics_mutex_);
                ++metrics_.refill_errors;
            }
            throw std::runtime_error("iio_buffer_refill failed: " + api_->error(static_cast<int>(received_bytes)));
        }
        const auto stride = api_->step(buffer_);
        if (stride <= 0) throw std::runtime_error("iio_buffer_step returned a non-positive stride");
        const auto* format_i = api_->format(rx_i_);
        const auto* format_q = api_->format(rx_q_);
        if (format_i == nullptr || format_q == nullptr) throw std::runtime_error("RX channel data format is unavailable");
        const auto valid_format = [](const iio_data_format& format) {
            return format.length == 16U && format.bits == 12U && format.shift <= format.length &&
                   format.bits <= format.length - format.shift &&
                   format.is_signed && format.repeat == 1U;
        };
        if (!valid_format(*format_i) || !valid_format(*format_q)) {
            {
                std::scoped_lock metrics_lock(metrics_mutex_);
                ++metrics_.refill_errors;
            }
            throw std::runtime_error("Unsupported Pluto RX format during refill; expected signed 12-bit in 16-bit storage");
        }
        auto* first_i = static_cast<std::uint8_t*>(api_->first(buffer_, rx_i_));
        auto* first_q = static_cast<std::uint8_t*>(api_->first(buffer_, rx_q_));
        auto* end = static_cast<std::uint8_t*>(api_->end(buffer_));
        if (first_i == nullptr || first_q == nullptr || end == nullptr || first_i >= end || first_q >= end) {
            {
                std::scoped_lock metrics_lock(metrics_mutex_);
                ++metrics_.refill_errors;
            }
            throw std::runtime_error("libiio returned an invalid RX sample layout");
        }
        const auto count_available = [end, stride](const std::uint8_t* first, const std::size_t storage_bytes) {
            const auto available = static_cast<std::size_t>(end - first);
            if (available < storage_bytes) return std::uint64_t{0};
            return std::uint64_t{1} + static_cast<std::uint64_t>(
                (available - storage_bytes) / static_cast<std::size_t>(stride)
            );
        };
        constexpr std::size_t storage_bytes = 2U;
        const auto count_i = count_available(first_i, storage_bytes);
        const auto count_q = count_available(first_q, storage_bytes);
        const auto complete_scans = static_cast<std::uint64_t>(received_bytes) /
                                    static_cast<std::uint64_t>(stride);
        const auto actual_count = static_cast<std::uint32_t>(std::min<std::uint64_t>(
            std::min({count_i, count_q, complete_scans}), applied_.requested.buffer_samples
        ));
        if (actual_count == 0U) throw std::runtime_error("libiio RX refill produced zero complete I/Q samples");
        sdr_core::QualityFlag flags = sdr_core::QualityFlag::TimestampEstimated;
        if (actual_count < applied_.requested.buffer_samples) {
            {
                std::scoped_lock metrics_lock(metrics_mutex_);
                ++metrics_.short_reads;
                metrics_.estimated_dropped_samples +=
                    applied_.requested.buffer_samples - actual_count;
            }
            flags = flags | sdr_core::QualityFlag::IqDropped;
        }
        {
            std::scoped_lock metrics_lock(metrics_mutex_);
            ++metrics_.blocks_received;
            metrics_.samples_received += actual_count;
        }
        const auto block_sequence = sequence_++;
        const auto block_first_sample_index = first_sample_index_;
        first_sample_index_ += actual_count;
        auto storage = pool_->try_acquire();
        if (!storage) {
            {
                std::scoped_lock metrics_lock(metrics_mutex_);
                ++metrics_.output_pool_exhaustions;
                ++metrics_.output_blocks_dropped;
                metrics_.estimated_dropped_samples += actual_count;
            }
            throw std::runtime_error("Pluto RX output pool exhausted; release retained IqBlock objects");
        }
        storage->resize(static_cast<std::size_t>(actual_count) * 4U);
        for (std::uint32_t index = 0U; index < actual_count; ++index) {
            const auto offset = static_cast<std::ptrdiff_t>(index) * stride;
            store_le_i16(storage->data() + static_cast<std::size_t>(index) * 4U, normalize_sample(first_i + offset, *format_i));
            store_le_i16(storage->data() + static_cast<std::size_t>(index) * 4U + 2U, normalize_sample(first_q + offset, *format_q));
        }

        const auto completed_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
        const auto duration_ns = static_cast<std::int64_t>(std::llround(static_cast<double>(actual_count) * 1.0e9 / applied_.sample_rate_hz));
        sdr_core::IqBlock block{
            .source_sequence = block_sequence,
            .first_sample_index = block_first_sample_index,
            .timestamp_ns = completed_ns - duration_ns,
            .center_frequency_hz = applied_.center_frequency_hz,
            .sample_rate_hz = applied_.sample_rate_hz,
            .sample_format = sdr_core::SampleFormat::ComplexInt12InInt16Le,
            .sample_count = actual_count,
            .flags = flags,
            .samples = storage,
            .config_generation = generation_,
        };
        sdr_core::validate(block);
        return block;
    }
    void cancel() noexcept {
        std::scoped_lock lock(cancel_mutex_);
        if (api_ != nullptr && cancel_buffer_ != nullptr) api_->cancel(cancel_buffer_);
    }
    void stop_stream() noexcept {
        cancel();
        std::scoped_lock lock(mutex_);
        stop_stream_locked();
    }
    void disconnect() noexcept {
        cancel();
        std::scoped_lock lock(mutex_);
        stop_stream_locked();
        if (pool_) pool_->request_stop();
        pool_.reset();
        if (context_ != nullptr && api_ != nullptr) api_->destroy_context(context_);
        context_ = nullptr;
        connected_.store(false, std::memory_order_release);
        phy_ = nullptr;
        phy_rx_ = nullptr;
        lo_ = nullptr;
        rx_stream_ = nullptr;
        rx_i_ = nullptr;
        rx_q_ = nullptr;
        configured_ = false;
    }
    bool streaming() const noexcept {
        return streaming_.load(std::memory_order_acquire);
    }
    StreamMetrics metrics() const noexcept { std::scoped_lock lock(metrics_mutex_); return metrics_; }

private:
    void require_connected_locked() const { if (context_ == nullptr) throw std::runtime_error("Pluto device is disconnected"); }
    void require_configured_locked() const {
        require_connected_locked();
        if (!configured_) throw sdr_core::ConfigurationError("Pluto device is not configured");
    }
    void discover_locked() {
        phy_ = find_device(*api_, context_, {"ad9361-phy", "ad9364-phy"}, {"ad936"});
        rx_stream_ = find_device(*api_, context_, {"cf-ad9361-lpc", "axi-ad9361-rx"}, {"cf-ad936", "ad9361-rx"});
        if (phy_ == nullptr || rx_stream_ == nullptr) throw std::runtime_error("Pluto AD936x PHY/RX devices not found");
        phy_rx_ = find_channel(*api_, phy_, {"voltage0"}, false, {"voltage", "rx"}, "sampling_frequency");
        lo_ = find_channel(*api_, phy_, {"altvoltage0", "RX_LO"}, true, {"altvoltage", "rx_lo"}, "frequency");
        rx_i_ = find_channel(*api_, rx_stream_, {"voltage0"}, false, {"voltage0", " i"});
        rx_q_ = find_channel(*api_, rx_stream_, {"voltage1"}, false, {"voltage1", " q"});
        if (phy_rx_ == nullptr || lo_ == nullptr || rx_i_ == nullptr || rx_q_ == nullptr || rx_i_ == rx_q_) {
            throw std::runtime_error("Pluto RX configuration/LO/I/Q channels not found");
        }
    }
    sdr_core::DeviceCapabilities read_capabilities_locked() const {
        const double frequency = static_cast<double>(read_ll(*api_, lo_, "frequency"));
        const double rate = static_cast<double>(read_ll(*api_, phy_rx_, "sampling_frequency"));
        const double bandwidth = static_cast<double>(read_ll(*api_, phy_rx_, "rf_bandwidth"));
        const double gain = read_double(*api_, phy_rx_, "hardwaregain");
        sdr_core::DeviceCapabilities result{
            .backend_id = "pluto-libiio",
            .device_id = safe(api_->device_id(phy_)),
            .serial = probe_.serial,
            .model = probe_.model.empty() ? "AD936x / PlutoSDR" : probe_.model,
            .firmware = probe_.firmware,
            .tuning_range_hz = range_from_available(read_text(*api_, lo_, "frequency_available"), frequency),
            .sample_rate_ranges_hz = {range_from_available(read_text(*api_, phy_rx_, "sampling_frequency_available"), rate)},
            .analog_bandwidth_ranges_hz = {range_from_available(read_text(*api_, phy_rx_, "rf_bandwidth_available"), bandwidth)},
            .gain_range_db = range_from_available(read_text(*api_, phy_rx_, "hardwaregain_available"), gain),
            .gain_modes = modes_from_available(read_text(*api_, phy_rx_, "gain_control_mode_available")),
            .sample_formats = {sdr_core::SampleFormat::ComplexInt12InInt16Le},
            .supports_hardware_timestamps = false,
            .supports_fastlock = false,
            .supports_temperature = false,
            .supports_overflow_counter = false,
            .supports_continuous_iq = true,
            .schema_version = sdr_core::contract_schema_version,
        };
        sdr_core::validate(result);
        return result;
    }
    SampleLayout sample_layout_locked() const {
        const auto* left = api_->format(rx_i_);
        const auto* right = api_->format(rx_q_);
        if (left == nullptr || right == nullptr) throw std::runtime_error("RX channel data format is unavailable");
        const bool same = left->length == right->length && left->bits == right->bits && left->shift == right->shift &&
                          left->is_signed == right->is_signed && left->is_be == right->is_be && left->repeat == right->repeat;
        if (!same) throw std::runtime_error("Pluto I/Q channel formats differ");
        if (left->length != 16U || left->bits != 12U || left->shift > left->length ||
            left->bits > left->length - left->shift ||
            !left->is_signed || left->repeat != 1U) {
            throw std::runtime_error("Unsupported Pluto RX format; expected signed 12-bit values in 16-bit storage");
        }
        return {
            .storage_bits = left->length,
            .significant_bits = left->bits,
            .shift = left->shift,
            .is_signed = left->is_signed,
            .is_big_endian = left->is_be,
            .repeat = left->repeat,
            .stride_bytes = buffer_ == nullptr ? 4 : api_->step(buffer_),
            .output_format = sdr_core::SampleFormat::ComplexInt12InInt16Le,
        };
    }
    void stop_stream_locked() noexcept {
        streaming_.store(false, std::memory_order_release);
        std::scoped_lock cancel_lock(cancel_mutex_);
        cancel_buffer_ = nullptr;
        if (buffer_ != nullptr && api_ != nullptr) api_->destroy_buffer(buffer_);
        buffer_ = nullptr;
        if (rx_i_ != nullptr && api_ != nullptr) api_->disable(rx_i_);
        if (rx_q_ != nullptr && api_ != nullptr) api_->disable(rx_q_);
    }

    std::unique_ptr<Api> api_;
    std::string uri_;
    mutable std::mutex mutex_;
    std::atomic<bool> connected_{false};
    std::atomic<bool> streaming_{false};
    iio_context* context_{};
    iio_device* phy_{};
    iio_channel* phy_rx_{};
    iio_channel* lo_{};
    iio_device* rx_stream_{};
    iio_channel* rx_i_{};
    iio_channel* rx_q_{};
    iio_buffer* buffer_{};
    mutable std::mutex cancel_mutex_;
    iio_buffer* cancel_buffer_{};
    ContextProbe probe_;
    sdr_core::DeviceCapabilities capabilities_;
    AppliedConfig applied_;
    bool configured_{};
    std::uint64_t generation_{};
    std::uint64_t sequence_{};
    std::uint64_t first_sample_index_{};
    mutable std::mutex metrics_mutex_;
    StreamMetrics metrics_;
    std::unique_ptr<sdr_core::BufferPool> pool_;
};
PlutoDevice::PlutoDevice(std::string uri, const std::uint32_t timeout_ms)
    : impl_(std::make_unique<Impl>(std::move(uri), timeout_ms)) {}
PlutoDevice::~PlutoDevice() = default;
PlutoDevice::PlutoDevice(PlutoDevice&&) noexcept = default;
PlutoDevice& PlutoDevice::operator=(PlutoDevice&&) noexcept = default;
bool PlutoDevice::connected() const noexcept { return impl_ != nullptr && impl_->connected(); }
std::string PlutoDevice::uri() const { return impl_->uri(); }
ContextProbe PlutoDevice::probe() const { return impl_->probe(); }
sdr_core::DeviceCapabilities PlutoDevice::capabilities() const { return impl_->capabilities(); }
AppliedConfig PlutoDevice::configure(const sdr_core::DeviceConfig& config) { return impl_->configure(config, 8U); }
AppliedConfig PlutoDevice::configure(const sdr_core::DeviceConfig& config, const std::uint32_t output_pool_blocks) { return impl_->configure(config, output_pool_blocks); }
AppliedConfig PlutoDevice::applied_config() const { return impl_->applied_config(); }
void PlutoDevice::start_stream() { impl_->start_stream(); }
sdr_core::IqBlock PlutoDevice::refill() { return impl_->refill(); }
void PlutoDevice::cancel() noexcept { if (impl_) impl_->cancel(); }
void PlutoDevice::stop_stream() noexcept { if (impl_) impl_->stop_stream(); }
void PlutoDevice::disconnect() noexcept { if (impl_) impl_->disconnect(); }
bool PlutoDevice::streaming() const noexcept { return impl_ != nullptr && impl_->streaming(); }
StreamMetrics PlutoDevice::metrics() const noexcept { return impl_ == nullptr ? StreamMetrics{} : impl_->metrics(); }
}  // namespace sdr_pluto