#include "sdr_pluto/pluto_backend.hpp"

#include <dlfcn.h>

#include <array>
#include <cstddef>
#include <cctype>
#include <cstdlib>
#include <initializer_list>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace sdr_pluto {
namespace {

struct iio_scan_context;
struct iio_context_info;
struct iio_context;
struct iio_device;
using ssize_type = std::ptrdiff_t;

struct Library final {
    Library() {
        std::vector<std::string> candidates;
        for (const char* variable : {"LIBIIO_SO_PATH", "LIBIIO_LIBRARY", "LIBIIO_DLL_PATH"}) {
            if (const char* value = std::getenv(variable); value != nullptr && value[0] != '\0') {
                candidates.emplace_back(value);
            }
        }
        candidates.emplace_back("libiio.so.0");
        candidates.emplace_back("libiio.so");
        candidates.emplace_back("libiio.so.1");
        std::string failures;
        for (const auto& candidate : candidates) {
            handle = dlopen(candidate.c_str(), RTLD_NOW | RTLD_LOCAL);
            if (handle != nullptr) {
                path = candidate;
                return;
            }
            if (const char* error = dlerror(); error != nullptr) {
                failures += candidate + ": " + error + "; ";
            }
        }
        throw std::runtime_error("libiio.so not found: " + failures);
    }
    ~Library() { if (handle != nullptr) dlclose(handle); }
    Library(const Library&) = delete;
    Library& operator=(const Library&) = delete;

    template <typename T>
    T symbol(const char* name) const {
        dlerror();
        void* address = dlsym(handle, name);
        if (address == nullptr) {
            throw std::runtime_error(std::string("libiio export missing: ") + name);
        }
        return reinterpret_cast<T>(address);
    }

    void* handle{};
    std::string path;
};

std::string safe(const char* value) { return value == nullptr ? std::string{} : std::string(value); }

}  // namespace

RuntimeInfo runtime_info() {
    RuntimeInfo result;
    try {
        Library library;
        result.available = true;
        result.library_path = library.path;
        using version_fn = void (*)(unsigned int*, unsigned int*, char[8]);
        using backend_count_fn = unsigned int (*)();
        using backend_fn = const char* (*)(unsigned int);
        const auto version = library.symbol<version_fn>("iio_library_get_version");
        std::array<char, 8> tag{};
        version(&result.major, &result.minor, tag.data());
        result.git_tag = tag.data();
        const auto count = library.symbol<backend_count_fn>("iio_get_backends_count")();
        const auto backend = library.symbol<backend_fn>("iio_get_backend");
        for (unsigned int index = 0; index < count; ++index) {
            result.backends.push_back(safe(backend(index)));
        }
    } catch (const std::exception& error) {
        result.error = error.what();
    }
    return result;
}

std::vector<ContextInfo> scan_contexts(const std::string& filter) {
    using create_fn = iio_scan_context* (*)(const char*, unsigned int);
    using destroy_fn = void (*)(iio_scan_context*);
    using list_fn = ssize_type (*)(iio_scan_context*, iio_context_info***);
    using free_fn = void (*)(iio_context_info**);
    using info_fn = const char* (*)(const iio_context_info*);
    Library library;
    const auto create = library.symbol<create_fn>("iio_create_scan_context");
    const auto destroy = library.symbol<destroy_fn>("iio_scan_context_destroy");
    const auto get_list = library.symbol<list_fn>("iio_scan_context_get_info_list");
    const auto free_list = library.symbol<free_fn>("iio_context_info_list_free");
    const auto get_description = library.symbol<info_fn>("iio_context_info_get_description");
    const auto get_uri = library.symbol<info_fn>("iio_context_info_get_uri");
    auto* scan = create(filter.empty() ? nullptr : filter.c_str(), 0U);
    if (scan == nullptr) throw std::runtime_error("iio_create_scan_context failed");
    std::unique_ptr<iio_scan_context, destroy_fn> scan_guard(scan, destroy);
    iio_context_info** raw_list = nullptr;
    const auto count = get_list(scan, &raw_list);
    if (count < 0) throw std::runtime_error("iio_scan_context_get_info_list failed: " + std::to_string(count));
    std::unique_ptr<iio_context_info*, free_fn> list_guard(raw_list, free_list);
    std::vector<ContextInfo> result;
    for (ssize_type index = 0; index < count; ++index) {
        const auto description = safe(get_description(raw_list[index]));
        const auto uri = safe(get_uri(raw_list[index]));
        const auto lower = [&description] {
            std::string value = description;
            for (auto& ch : value) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
            return value;
        }();
        if (lower.find("pluto") != std::string::npos || lower.find("ad936") != std::string::npos) {
            result.push_back({uri, description});
        }
    }
    return result;
}

ContextProbe probe_context(const std::string& uri, const std::uint32_t timeout_ms) {
    if (!(uri.starts_with("usb:") || uri.starts_with("ip:"))) {
        throw std::invalid_argument("Pluto URI must start with usb: or ip:");
    }
    using create_fn = iio_context* (*)(const char*);
    using destroy_fn = void (*)(iio_context*);
    using timeout_fn = int (*)(iio_context*, unsigned int);
    using string_fn = const char* (*)(const iio_context*);
    using version_fn = int (*)(const iio_context*, unsigned int*, unsigned int*, char[8]);
    using attr_fn = const char* (*)(const iio_context*, const char*);
    using count_fn = unsigned int (*)(const iio_context*);
    using device_fn = iio_device* (*)(const iio_context*, unsigned int);
    using device_string_fn = const char* (*)(const iio_device*);
    Library library;
    const auto create = library.symbol<create_fn>("iio_create_context_from_uri");
    const auto destroy = library.symbol<destroy_fn>("iio_context_destroy");
    const auto set_timeout = library.symbol<timeout_fn>("iio_context_set_timeout");
    const auto context_name = library.symbol<string_fn>("iio_context_get_name");
    const auto context_description = library.symbol<string_fn>("iio_context_get_description");
    const auto context_version = library.symbol<version_fn>("iio_context_get_version");
    const auto context_attr = library.symbol<attr_fn>("iio_context_get_attr_value");
    const auto devices_count = library.symbol<count_fn>("iio_context_get_devices_count");
    const auto get_device = library.symbol<device_fn>("iio_context_get_device");
    const auto device_id = library.symbol<device_string_fn>("iio_device_get_id");
    const auto device_name = library.symbol<device_string_fn>("iio_device_get_name");
    auto* raw_context = create(uri.c_str());
    if (raw_context == nullptr) throw std::runtime_error("iio_create_context_from_uri failed for " + uri);
    std::unique_ptr<iio_context, destroy_fn> context(raw_context, destroy);
    const int timeout_result = set_timeout(context.get(), timeout_ms);
    if (timeout_result < 0) throw std::runtime_error("iio_context_set_timeout failed: " + std::to_string(timeout_result));
    ContextProbe result;
    result.uri = uri;
    result.context_name = safe(context_name(context.get()));
    result.description = safe(context_description(context.get()));
    std::array<char, 8> tag{};
    if (context_version(context.get(), &result.backend_major, &result.backend_minor, tag.data()) < 0) {
        throw std::runtime_error("iio_context_get_version failed");
    }
    result.backend_tag = tag.data();
    for (const char* attribute : {"hw_model", "model"}) {
        if (const char* value = context_attr(context.get(), attribute); value != nullptr && value[0] != '\0') {
            result.model = value;
            break;
        }
    }
    for (const char* attribute : {"hw_serial", "serial"}) {
        if (const char* value = context_attr(context.get(), attribute); value != nullptr && value[0] != '\0') {
            result.serial = value;
            break;
        }
    }
    for (const char* attribute : {"fw_version", "firmware", "local,kernel"}) {
        if (const char* value = context_attr(context.get(), attribute); value != nullptr && value[0] != '\0') {
            result.firmware = value;
            break;
        }
    }
    const auto count = devices_count(context.get());
    result.device_ids.reserve(count);
    for (unsigned int index = 0; index < count; ++index) {
        const auto* device = get_device(context.get(), index);
        const auto id = safe(device_id(device));
        const auto name = safe(device_name(device));
        result.device_ids.push_back(id + (name.empty() ? std::string{} : ":" + name));
        std::string identity = id + " " + name;
        for (auto& ch : identity) ch = static_cast<char>(std::tolower(static_cast<unsigned char>(ch)));
        if (result.phy_device_id.empty() && identity.find("ad936") != std::string::npos) result.phy_device_id = id;
        if (result.rx_stream_device_id.empty() &&
            (identity.find("cf-ad9361-lpc") != std::string::npos || identity.find("axi-ad9361-rx") != std::string::npos)) {
            result.rx_stream_device_id = id;
        }
    }
    if (result.phy_device_id.empty()) throw std::runtime_error("AD936x PHY device not found in context");
    if (result.rx_stream_device_id.empty()) throw std::runtime_error("AD936x RX streaming device not found in context");
    return result;
}

}  // namespace sdr_pluto
