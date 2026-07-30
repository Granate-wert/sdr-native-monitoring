#pragma once

#include "sdr_core/backend_info.hpp"
#include "sdr_core/dsp_backend.hpp"

#include <cstdint>
#include <memory>
#include <string>

// Link boundary between the portable core and the optional CUDA target.
// This header contains no CUDA SDK types and is safe for CPU-only builds.
// The optional sdr_cuda target provides the real implementation; when
// SDR_CORE_ENABLE_CUDA=OFF the core links a controlled stub instead.
namespace sdr_core::cuda_link {

[[nodiscard]] bool compiled() noexcept;
[[nodiscard]] BackendAvailability availability(int device_id);
[[nodiscard]] BackendAvailability self_test(int device_id);
[[nodiscard]] std::string self_test_cache_key(int device_id);
[[nodiscard]] std::unique_ptr<DspBackend> make_backend(
    DspOptions options,
    int device_id,
    std::uint32_t plan_cache_capacity
);

}  // namespace sdr_core::cuda_link
