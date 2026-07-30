#include "sdr_pluto/pluto_backend.hpp"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <array>
#include <cerrno>
#include <cctype>
#include <cstddef>
#include <initializer_list>
#include <filesystem>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace sdr_pluto {
namespace {

struct iio_context;
struct iio_device;
struct iio_context_info;
struct iio_scan_context;

using ssize_type = std::ptrdiff_t;

std::string narrow(const std::wstring& value) {
    if (value.empty()) return {};
    const int size = WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), nullptr, 0, nullptr, nullptr);
    std::string result(static_cast<std::size_t>(size), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.data(), static_cast<int>(value.size()), result.data(), size, nullptr, nullptr);
    return result;
}

std::string win_error(const DWORD code) {
    wchar_t* message = nullptr;
    const DWORD length = FormatMessageW(
        FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS,
        nullptr, code, 0, reinterpret_cast<wchar_t*>(&message), 0, nullptr
    );
    std::wstring text = length != 0U && message != nullptr ? std::wstring(message, length) : L"unknown Windows error";
    if (message != nullptr) LocalFree(message);
    return narrow(text);
}

class Library final {
public:
    Library() { load(); }
    ~Library() { if (module_ != nullptr) FreeLibrary(module_); }
    Library(const Library&) = delete;
    Library& operator=(const Library&) = delete;

    template <typename T>
    T symbol(const char* name) const {
        const auto address = GetProcAddress(module_, name);
        if (address == nullptr) throw std::runtime_error(std::string("libiio 0.x export missing: ") + name);
        return reinterpret_cast<T>(address);
    }

    [[nodiscard]] const std::string& path() const noexcept { return path_; }

private:
    void load() {
        std::vector<std::filesystem::path> candidates;
        std::array<wchar_t, 32768> env{};
        const DWORD explicit_len = GetEnvironmentVariableW(L"LIBIIO_DLL_PATH", env.data(), static_cast<DWORD>(env.size()));
        if (explicit_len > 0U && explicit_len < env.size()) candidates.emplace_back(env.data());
        const DWORD program_len = GetEnvironmentVariableW(L"ProgramFiles", env.data(), static_cast<DWORD>(env.size()));
        if (program_len > 0U && program_len < env.size()) {
            candidates.emplace_back(std::filesystem::path(env.data()) / L"IIO Oscilloscope" / L"bin" / L"libiio.dll");
        }
        candidates.emplace_back(L"libiio.dll");
        std::ostringstream failures;
        for (const auto& candidate : candidates) {
            module_ = LoadLibraryExW(candidate.c_str(), nullptr, LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR);
            if (module_ != nullptr) {
                std::array<wchar_t, 32768> resolved{};
                const DWORD length = GetModuleFileNameW(module_, resolved.data(), static_cast<DWORD>(resolved.size()));
                path_ = length != 0U ? narrow(std::wstring(resolved.data(), length)) : narrow(candidate.wstring());
                return;
            }
            failures << narrow(candidate.wstring()) << ": " << win_error(GetLastError()) << "; ";
        }
        throw std::runtime_error("libiio.dll not found: " + failures.str());
    }

    HMODULE module_{};
    std::string path_;
};

struct Api final {
    using create_scan_fn = iio_scan_context* (*)(const char*, unsigned int);
    using destroy_scan_fn = void (*)(iio_scan_context*);
    using scan_list_fn = ssize_type (*)(iio_scan_context*, iio_context_info***);
    using free_list_fn = void (*)(iio_context_info**);
    using info_string_fn = const char* (*)(const iio_context_info*);
    using library_version_fn = void (*)(unsigned int*, unsigned int*, char[8]);
    using backends_count_fn = unsigned int (*)();
    using backend_fn = const char* (*)(unsigned int);
    using create_context_fn = iio_context* (*)(const char*);
    using destroy_context_fn = void (*)(iio_context*);
    using context_string_fn = const char* (*)(const iio_context*);
    using context_version_fn = int (*)(const iio_context*, unsigned int*, unsigned int*, char[8]);
    using context_timeout_fn = int (*)(iio_context*, unsigned int);
    using context_attr_fn = const char* (*)(const iio_context*, const char*);
    using devices_count_fn = unsigned int (*)(const iio_context*);
    using get_device_fn = iio_device* (*)(const iio_context*, unsigned int);
    using device_string_fn = const char* (*)(const iio_device*);

    Library library;
    create_scan_fn create_scan{library.symbol<create_scan_fn>("iio_create_scan_context")};
    destroy_scan_fn destroy_scan{library.symbol<destroy_scan_fn>("iio_scan_context_destroy")};
    scan_list_fn scan_list{library.symbol<scan_list_fn>("iio_scan_context_get_info_list")};
    free_list_fn free_list{library.symbol<free_list_fn>("iio_context_info_list_free")};
    info_string_fn info_description{library.symbol<info_string_fn>("iio_context_info_get_description")};
    info_string_fn info_uri{library.symbol<info_string_fn>("iio_context_info_get_uri")};
    library_version_fn library_version{library.symbol<library_version_fn>("iio_library_get_version")};
    backends_count_fn backends_count{library.symbol<backends_count_fn>("iio_get_backends_count")};
    backend_fn backend{library.symbol<backend_fn>("iio_get_backend")};
    create_context_fn create_context{library.symbol<create_context_fn>("iio_create_context_from_uri")};
    destroy_context_fn destroy_context{library.symbol<destroy_context_fn>("iio_context_destroy")};
    context_string_fn context_name{library.symbol<context_string_fn>("iio_context_get_name")};
    context_string_fn context_description{library.symbol<context_string_fn>("iio_context_get_description")};
    context_version_fn context_version{library.symbol<context_version_fn>("iio_context_get_version")};
    context_timeout_fn context_timeout{library.symbol<context_timeout_fn>("iio_context_set_timeout")};
    context_attr_fn context_attr{library.symbol<context_attr_fn>("iio_context_get_attr_value")};
    devices_count_fn devices_count{library.symbol<devices_count_fn>("iio_context_get_devices_count")};
    get_device_fn get_device{library.symbol<get_device_fn>("iio_context_get_device")};
    device_string_fn device_id{library.symbol<device_string_fn>("iio_device_get_id")};
    device_string_fn device_name{library.symbol<device_string_fn>("iio_device_get_name")};
};

template <typename T, typename Destroy>
using unique_handle = std::unique_ptr<T, Destroy>;

std::string safe(const char* value) { return value == nullptr ? std::string{} : std::string(value); }

bool contains_ci(const std::string& value, const std::string_view needle) {
    std::string lower = value;
    for (auto& ch : lower) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
    return lower.find(needle) != std::string::npos;
}

std::string first_attr(const Api& api, const iio_context* context, std::initializer_list<const char*> names) {
    for (const char* name : names) {
        if (const char* value = api.context_attr(context, name); value != nullptr && *value != '\0') return value;
    }
    return {};
}

}  // namespace

RuntimeInfo runtime_info() {
    RuntimeInfo result;
    try {
        Api api;
        result.available = true;
        result.library_path = api.library.path();
        std::array<char, 8> tag{};
        api.library_version(&result.major, &result.minor, tag.data());
        result.git_tag = tag.data();
        const auto count = api.backends_count();
        result.backends.reserve(count);
        for (unsigned int index = 0U; index < count; ++index) result.backends.push_back(safe(api.backend(index)));
    } catch (const std::exception& error) {
        result.error = error.what();
    }
    return result;
}

std::vector<ContextInfo> scan_contexts(const std::string& filter) {
    Api api;
    auto* raw_scan = api.create_scan(filter.empty() ? nullptr : filter.c_str(), 0U);
    if (raw_scan == nullptr) throw std::runtime_error("iio_create_scan_context failed");
    unique_handle<iio_scan_context, Api::destroy_scan_fn> scan(raw_scan, api.destroy_scan);
    iio_context_info** raw_list = nullptr;
    const auto count = api.scan_list(scan.get(), &raw_list);
    if (count < 0) throw std::runtime_error("iio_scan_context_get_info_list failed: " + std::to_string(count));
    unique_handle<iio_context_info*, Api::free_list_fn> list(raw_list, api.free_list);
    std::vector<ContextInfo> result;
    result.reserve(static_cast<std::size_t>(count));
    for (ssize_type index = 0; index < count; ++index) {
        ContextInfo info{safe(api.info_uri(raw_list[index])), safe(api.info_description(raw_list[index]))};
        if (contains_ci(info.description, "pluto") || contains_ci(info.description, "ad936")) result.push_back(std::move(info));
    }
    return result;
}

ContextProbe probe_context(const std::string& uri, const std::uint32_t timeout_ms) {
    if (!(uri.starts_with("usb:") || uri.starts_with("ip:"))) throw std::invalid_argument("Pluto URI must start with usb: or ip:");
    Api api;
    auto* raw_context = api.create_context(uri.c_str());
    if (raw_context == nullptr) throw std::runtime_error("iio_create_context_from_uri failed for " + uri);
    unique_handle<iio_context, Api::destroy_context_fn> context(raw_context, api.destroy_context);
    const int timeout_result = api.context_timeout(context.get(), timeout_ms);
    if (timeout_result < 0) throw std::runtime_error("iio_context_set_timeout failed: " + std::to_string(timeout_result));

    ContextProbe result;
    result.uri = uri;
    result.context_name = safe(api.context_name(context.get()));
    result.description = safe(api.context_description(context.get()));
    std::array<char, 8> tag{};
    const int version_result = api.context_version(context.get(), &result.backend_major, &result.backend_minor, tag.data());
    if (version_result < 0) throw std::runtime_error("iio_context_get_version failed: " + std::to_string(version_result));
    result.backend_tag = tag.data();
    result.model = first_attr(api, context.get(), {"hw_model", "model"});
    result.serial = first_attr(api, context.get(), {"hw_serial", "serial"});
    result.firmware = first_attr(api, context.get(), {"fw_version", "firmware", "local,kernel"});

    const auto count = api.devices_count(context.get());
    result.device_ids.reserve(count);
    for (unsigned int index = 0U; index < count; ++index) {
        const auto* device = api.get_device(context.get(), index);
        const auto id = safe(api.device_id(device));
        const auto name = safe(api.device_name(device));
        result.device_ids.push_back(id + (name.empty() ? "" : ":" + name));
        if (result.phy_device_id.empty() && (contains_ci(id, "ad936") || contains_ci(name, "ad936"))) result.phy_device_id = id;
        if (result.rx_stream_device_id.empty() &&
            (contains_ci(id, "cf-ad9361-lpc") || contains_ci(name, "cf-ad9361-lpc") || contains_ci(id, "axi-ad9361-rx"))) {
            result.rx_stream_device_id = id;
        }
    }
    if (result.phy_device_id.empty()) throw std::runtime_error("AD936x PHY device not found in context");
    if (result.rx_stream_device_id.empty()) throw std::runtime_error("AD936x RX streaming device not found in context");
    return result;
}

}  // namespace sdr_pluto
