#include "sdr_core/cuda_backend_link.hpp"

#include "sdr_core/errors.hpp"

// Controlled stub linked into CPU-only builds: the CUDA backend answers
// "compiled=false" through the vendor-neutral availability contract instead
// of failing to link or launch.
namespace sdr_core::cuda_link {

bool compiled() noexcept {
    return false;
}

BackendAvailability availability(int /*device_id*/) {
    BackendAvailability result;
    result.compiled = false;
    result.reason_code = std::string(to_wire(BackendErrorCode::RuntimeNotFound));
    result.details = "CUDA backend is not compiled (SDR_CORE_ENABLE_CUDA=OFF)";
    return result;
}

BackendAvailability self_test(int /*device_id*/) {
    return availability(-1);
}

std::string self_test_cache_key(int /*device_id*/) {
    return "cuda-unavailable";
}

std::unique_ptr<DspBackend> make_backend(
    DspOptions /*options*/,
    int /*device_id*/,
    std::uint32_t /*plan_cache_capacity*/
) {
    throw BackendUnavailableError("CUDA backend is not compiled (SDR_CORE_ENABLE_CUDA=OFF)");
}

}  // namespace sdr_core::cuda_link
