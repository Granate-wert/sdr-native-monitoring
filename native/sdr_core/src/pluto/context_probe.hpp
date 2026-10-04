#pragma once

#include "sdr_pluto/pluto_backend.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <initializer_list>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <string_view>

namespace sdr_pluto::detail {

// Reader is bound to the caller-owned context. Never infer a missing attribute
// from the caller URI, description, model or another temporary probe.
template <typename AttributeReader>
void inspect_connection_attributes(ContextProbe& result, const AttributeReader& read) {
    const auto observed = [&read](const char* key) -> std::optional<std::string> {
        const auto* value = read(key);
        return value == nullptr ? std::nullopt : std::optional<std::string>{value};
    };
    result.backend_uri = observed("uri");
    result.usb_vendor_id = observed("usb,idVendor");
    result.usb_product_id = observed("usb,idProduct");
    result.usb_serial = observed("usb,serial");
}

// Must match Python normalized_pluto_serial; ASCII only, no route inference.
inline std::optional<std::string> normalized_serial(const std::string& value) {
    constexpr std::string_view whitespace = " \t\n\r\v\f";
    const auto first = value.find_first_not_of(whitespace);
    if (first == std::string::npos) return std::nullopt;
    auto serial = value.substr(first, value.find_last_not_of(whitespace) - first + 1U);
    for (auto& character : serial) {
        const auto byte = static_cast<unsigned char>(character);
        if (byte < 33U || byte > 126U) return std::nullopt;
        if (character >= 'A' && character <= 'Z') character = static_cast<char>(character + ('a' - 'A'));
    }
    if (serial == "-" || serial == "unknown" || serial == "n/a" || serial == "none") return std::nullopt;
    return serial;
}

inline void validate_expected_serial(const std::optional<std::string>& expected) {
    if (expected.has_value() && !normalized_serial(*expected).has_value()) {
        throw std::invalid_argument("expected Pluto identity must be a known serial");
    }
}

inline void admit_context_identity(const ContextProbe& probe, const std::optional<std::string>& expected) {
    if (expected.has_value() && normalized_serial(probe.serial) != normalized_serial(*expected)) {
        throw std::runtime_error("Pluto receiver identity was not confirmed on the opened context");
    }
}

inline void validate_expected_usb_connection(
    const std::string& requested_uri,
    const std::optional<std::string>& expected_serial,
    const std::optional<ExpectedUsbConnection>& expected
) {
    if (!expected) return;
    const auto& value = *expected;
    if (!requested_uri.starts_with("usb:") || value.bus > 255U ||
        value.device_address == 0U || value.device_address > 127U ||
        value.interface_number > 255U || value.vendor_id == 0U || value.vendor_id > 65535U ||
        value.product_id == 0U || value.product_id > 65535U ||
        value.usb_serial.size() > 256U ||
        (!value.usb_serial.empty() && !normalized_serial(value.usb_serial))) {
        throw std::invalid_argument("expected Pluto USB connection is invalid");
    }
    if (expected_serial && normalized_serial(*expected_serial) != normalized_serial(value.usb_serial)) {
        throw std::invalid_argument("expected Pluto USB and hardware identities conflict");
    }
}

// Parse only the backend's complete bus.address.interface observation. Caller
// route aliases, signs, whitespace, trailing text and integer overflow refuse.
inline std::optional<std::array<std::uint32_t, 3>> observed_usb_route(const std::string& uri) {
    if (!uri.starts_with("usb:")) return std::nullopt;
    std::array<std::uint32_t, 3> result{};
    const char* cursor = uri.data() + 4U;
    const char* end = uri.data() + uri.size();
    for (std::size_t index = 0U; index < result.size(); ++index) {
        const auto* first = cursor;
        while (cursor != end && *cursor >= '0' && *cursor <= '9') ++cursor;
        if (cursor == first || (cursor - first > 1 && *first == '0')) return std::nullopt;
        const auto parsed = std::from_chars(first, cursor, result[index]);
        if (parsed.ec != std::errc{} || parsed.ptr != cursor) return std::nullopt;
        if (index + 1U != result.size()) {
            if (cursor == end || *cursor++ != '.') return std::nullopt;
        } else if (cursor != end) return std::nullopt;
    }
    if (result[0] > 255U || result[1] == 0U || result[1] > 127U || result[2] > 255U) return std::nullopt;
    return result;
}

inline std::optional<std::uint32_t> observed_usb_descriptor(const std::optional<std::string>& text) {
    if (!text || text->size() != 4U) return std::nullopt;
    for (const auto character : *text) {
        if (!((character >= '0' && character <= '9') ||
              (character >= 'a' && character <= 'f') || (character >= 'A' && character <= 'F'))) {
            return std::nullopt;
        }
    }
    std::uint32_t value{};
    const auto parsed = std::from_chars(text->data(), text->data() + text->size(), value, 16);
    if (parsed.ec != std::errc{} || value == 0U) return std::nullopt;
    return value;
}

inline void admit_context_usb_connection(
    const ContextProbe& probe, const std::optional<ExpectedUsbConnection>& expected
) {
    if (!expected) return;
    const auto route = probe.backend_uri ? observed_usb_route(*probe.backend_uri) : std::nullopt;
    // Genuinely empty serials are allowed only as a consistent observed pair.
    // Unknown placeholders/invalid serials cannot masquerade as empty identity.
    const bool valid_serials = probe.usb_serial.has_value() && probe.usb_serial->size() <= 256U &&
        probe.serial.size() <= 256U &&
        (probe.usb_serial->empty() || normalized_serial(*probe.usb_serial).has_value()) &&
        (probe.serial.empty() || normalized_serial(probe.serial).has_value());
    if (probe.context_name != "usb" || !route ||
        *route != std::array<std::uint32_t, 3>{expected->bus, expected->device_address, expected->interface_number} ||
        observed_usb_descriptor(probe.usb_vendor_id) != expected->vendor_id ||
        observed_usb_descriptor(probe.usb_product_id) != expected->product_id || !valid_serials ||
        normalized_serial(*probe.usb_serial) != normalized_serial(expected->usb_serial) ||
        normalized_serial(probe.serial) != normalized_serial(*probe.usb_serial)) {
        throw std::runtime_error("Pluto USB connection was not confirmed on the opened context");
    }
}

// Cooperative, process-local claims for explicitly asserted USB owners only.
// Interfaces of one bus/address are ONE resource. Known serials add an alias
// claim across addresses. This is not USB/IP discovery, cross-process exclusion
// or hot-swap/liveness proof, and it must not relax the product identity gate.
class UsbConnectionClaim final {
public:
    explicit UsbConnectionClaim(const ExpectedUsbConnection& expected)
        : bus_(expected.bus), address_(expected.device_address), serial_(normalized_serial(expected.usb_serial)) {
        auto& state = registry();
        std::lock_guard lock(state.mutex);
        for (const auto* prior : state.owners) {
            if ((prior->bus_ == bus_ && prior->address_ == address_) ||
                (serial_ && prior->serial_ == serial_)) {
                throw std::runtime_error("Pluto USB resource is already held by an asserted owner");
            }
        }
        state.owners.push_back(this);
    }
    ~UsbConnectionClaim() {
        auto& state = registry();
        std::lock_guard lock(state.mutex);
        std::erase(state.owners, this);
    }
    UsbConnectionClaim(const UsbConnectionClaim&) = delete;
    UsbConnectionClaim& operator=(const UsbConnectionClaim&) = delete;
private:
    struct Registry {
        std::mutex mutex;
        std::vector<const UsbConnectionClaim*> owners;
    };
    static Registry& registry() { static Registry value; return value; }
    std::uint32_t bus_;
    std::uint32_t address_;
    std::optional<std::string> serial_;
};

// Observe the caller-owned context through that owner's libiio function table.
// Never open, destroy, configure, enable a channel, or allocate an IIO buffer.
// Keeping this private and shared makes the Windows/Linux device constructors
// read identity from the same handle they subsequently configure for RX.
template <typename Api, typename Context>
ContextProbe inspect_open_context(const Api& api, const Context* context, const std::string& uri) {
    if (context == nullptr) throw std::invalid_argument("cannot inspect a null IIO context");
    const auto text = [](const char* value) { return value == nullptr ? std::string{} : std::string(value); };
    const auto lower = [](std::string value) {
        for (auto& character : value) {
            character = static_cast<char>(std::tolower(static_cast<unsigned char>(character)));
        }
        return value;
    };
    const auto first_attr = [&api, context](std::initializer_list<const char*> names) {
        for (const auto* name : names) {
            if (const auto* value = api.context_attr(context, name); value != nullptr && *value != '\0') {
                return std::string(value);
            }
        }
        return std::string{};
    };

    ContextProbe result;
    result.uri = uri;
    inspect_connection_attributes(result, [&api, context](const char* key) {
        return api.context_attr(context, key);
    });
    result.context_name = text(api.context_name(context));
    result.description = text(api.context_description(context));
    std::array<char, 8> tag{};
    if (api.context_version(context, &result.backend_major, &result.backend_minor, tag.data()) < 0) {
        throw std::runtime_error("iio_context_get_version failed");
    }
    result.backend_tag.assign(tag.begin(), std::find(tag.begin(), tag.end(), '\0'));
    result.model = first_attr({"hw_model", "model"});
    result.serial = first_attr({"hw_serial", "serial"});
    result.firmware = first_attr({"fw_version", "firmware", "local,kernel"});

    const auto count = api.devices_count(context);
    result.device_ids.reserve(count);
    for (unsigned int index = 0U; index < count; ++index) {
        const auto* device = api.get_device(context, index);
        if (device == nullptr) throw std::runtime_error("IIO context contains a null device");
        const auto id = text(api.device_id(device));
        const auto name = text(api.device_name(device));
        result.device_ids.push_back(id + (name.empty() ? "" : ":" + name));
        const auto identity = lower(id + " " + name);
        if (result.phy_device_id.empty() && identity.find("ad936") != std::string::npos) {
            result.phy_device_id = id;
        }
        if (!result.rx_stream_device_id.empty() ||
            (identity.find("cf-ad936") == std::string::npos && identity.find("axi-ad936") == std::string::npos)) {
            continue;
        }
        bool has_i = false;
        bool has_q = false;
        for (unsigned int channel_index = 0U; channel_index < api.channels_count(device); ++channel_index) {
            const auto* channel = api.get_channel(device, channel_index);
            if (channel == nullptr || api.channel_output(channel)) continue;
            const auto channel_identity = text(api.channel_id(channel));
            has_i = has_i || channel_identity == "voltage0";
            has_q = has_q || channel_identity == "voltage1";
        }
        if (has_i && has_q) result.rx_stream_device_id = id;
    }
    if (result.phy_device_id.empty()) throw std::runtime_error("AD936x PHY device not found in context");
    if (result.rx_stream_device_id.empty()) {
        throw std::runtime_error("AD936x RX streaming device with input voltage0/voltage1 channels not found in context");
    }
    return result;
}

// Copy scan-layout facts while the observed owner still holds this context.
// No second open, channel enable or buffer creation is performed.
template <typename Api, typename Context>
ReceiverTopologyProbe inspect_context_topology(const Api& api, const Context* context, const ContextProbe& probe) {
    if (context == nullptr) throw std::invalid_argument("cannot inspect a null IIO context");
    const auto text = [](const char* value) { return value == nullptr ? std::string{} : std::string(value); };
    ReceiverTopologyProbe result;
    result.context = probe;
    bool found_phy = false;
    bool found_stream = false;
    for (unsigned int index = 0U; index < api.devices_count(context); ++index) {
        const auto* device = api.get_device(context, index);
        if (device == nullptr) throw std::runtime_error("IIO topology contains a null device");
        const auto id = text(api.device_id(device));
        if (id != probe.phy_device_id && id != probe.rx_stream_device_id) continue;
        const bool is_phy = id == probe.phy_device_id;
        found_phy = found_phy || is_phy;
        found_stream = found_stream || !is_phy;
        for (unsigned int channel_index = 0U; channel_index < api.channels_count(device); ++channel_index) {
            const auto* channel = api.get_channel(device, channel_index);
            if (channel == nullptr || api.channel_output(channel)) continue;
            const auto channel_id = text(api.channel_id(channel));
            if (channel_id.empty()) continue;
            if (is_phy) {
                result.phy_rx_channel_ids.push_back(channel_id);
                continue;
            }
            const auto* format = api.format(channel);
            if (format == nullptr) continue;
            result.input_scan_elements.push_back({
                .id = channel_id,
                .device_channel_index = channel_index,
                .storage_bits = format->length,
                .significant_bits = format->bits,
                .shift = format->shift,
                .is_signed = format->is_signed,
                .is_big_endian = format->is_be,
                .repeat = format->repeat,
            });
        }
    }
    if (!found_phy || !found_stream) throw std::runtime_error("IIO topology cannot resolve the observed PHY/RX devices");
    return result;
}

}  // namespace sdr_pluto::detail
