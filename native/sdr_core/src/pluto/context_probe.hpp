#pragma once

#include "sdr_pluto/pluto_backend.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <initializer_list>
#include <stdexcept>
#include <string>
#include <string_view>

namespace sdr_pluto::detail {

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

}  // namespace sdr_pluto::detail
