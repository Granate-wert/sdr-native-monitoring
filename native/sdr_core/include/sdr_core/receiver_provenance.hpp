#pragma once

#include "sdr_core/errors.hpp"
#include "sdr_core/types.hpp"

#include <string>
#include <string_view>

namespace sdr_core {

// Optional digital-chain provenance only. It conveys no RF-path/topology or
// independent tuning proof. Missing legacy metadata remains unspecified.
inline void restore_receiver_selection(SourceDescriptor& source, const std::string_view selection) {
    if (selection != "RX1" && selection != "RX2" && selection != "BOTH") {
        throw ConfigurationError("recorded receiver_selection must be RX1, RX2 or BOTH");
    }
    source.metadata_json["receiver_selection"] = "\"" + std::string(selection) + "\"";
}

[[nodiscard]] inline std::string receiver_selection_json_suffix(const SourceDescriptor& source) {
    const auto entry = source.metadata_json.find("receiver_selection");
    if (entry == source.metadata_json.end()) return {};
    const auto& json = entry->second;
    if (json != "\"RX1\"" && json != "\"RX2\"" && json != "\"BOTH\"") {
        throw ConfigurationError("source receiver_selection is not a canonical digital selection");
    }
    return ",\"receiver_selection\":" + json;
}

}  // namespace sdr_core
