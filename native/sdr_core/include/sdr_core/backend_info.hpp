#pragma once

#include "sdr_core/types.hpp"

#include <cstdint>
#include <string>

namespace sdr_core {

// P08/P08H-00 vendor-neutral description of a compute backend implementation.
// Contains no vendor handles, pointers or SDK types.
struct BackendInfo {
    ComputeBackendKind kind{ComputeBackendKind::Cpu};
    std::string backend_id;
    std::string vendor;
    std::string device_uuid;
    std::string device_name;
    std::string architecture;
    std::string driver_version;
    std::string runtime_version;
    std::string fft_library;
    std::string fft_library_version;
    std::uint64_t total_memory_bytes{};
    bool supports_fp64{};
    bool supports_pinned_host{};
    bool supports_async_copy{};
    bool supports_managed_memory{};
    bool validated{};
};

// P08/P08H-00 availability ladder: compiled -> runtime_present -> devices ->
// device_supported -> self_test_passed. A missing level never implies the
// next one; `reason_code` carries a stable BackendErrorCode wire value when
// something is unavailable.
struct BackendAvailability {
    bool compiled{};
    bool runtime_present{};
    std::uint32_t device_count{};
    bool device_supported{};
    bool self_test_passed{};
    std::string reason_code;
    std::string details;
};

void validate(const BackendInfo& value);
void validate(const BackendAvailability& value);

}  // namespace sdr_core
