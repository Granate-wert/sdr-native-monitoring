#pragma once

#include "sdr_rtlsdr/rtl_runtime.hpp"

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace sdr_rtlsdr {

struct RtlExternalFile {
    std::string absolute_utf8_path;
    std::string sha256_hex;
};

struct RtlExternalRuntime {
    RtlExternalFile library;
    std::vector<RtlExternalFile> dependencies;
};

struct RtlObservedCandidate {
    std::uint32_t enumeration_index{};
    std::string manufacturer;
    std::string product;
    std::string serial;
    std::uint32_t tuner_type{};  // zero until selected open/read-only probe
    bool direct_sampling{};
    bool offset_tuning{};
    std::vector<int> tuner_gains_tenth_db;  // selected owned-handle observation only
};

// All calls explicitly load a provisioned, hash-admitted, absolute DLL. The
// official SDK binary/header is neither linked nor bundled by this target.
// Discovery is explicit; constructing an application or Python module does
// not load the SDK or enumerate/open USB devices.
[[nodiscard]] std::vector<RtlObservedCandidate> enumerate_rtl_candidates(
    const RtlExternalRuntime& runtime);
[[nodiscard]] RtlObservedCandidate observe_single_rtl_candidate(
    const RtlExternalRuntime& runtime);
[[nodiscard]] std::unique_ptr<RtlRuntimePort> make_official_rtl_port(
    const RtlExternalRuntime& runtime);

}  // namespace sdr_rtlsdr
