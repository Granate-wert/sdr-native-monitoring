#include "sdr_cuda/cufft_plan_cache.hpp"

#include <cstdlib>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace sdr_cuda {

namespace {

[[noreturn]] void throw_unavailable(const std::string& details) {
    throw sdr_core::BackendUnavailableError(
        "cuFFT runtime is unavailable: " + details
    );
}

[[nodiscard]] void* load_cufft_library() {
    if (const char* override_path = std::getenv("CUFFT_LIBRARY");
        override_path != nullptr && override_path[0] != '\0') {
#ifdef _WIN32
        if (HMODULE module = LoadLibraryA(override_path); module != nullptr) return module;
#else
        if (void* module = dlopen(override_path, RTLD_NOW | RTLD_LOCAL); module != nullptr) return module;
#endif
        throw_unavailable(std::string("CUFFT_LIBRARY load failed: ") + override_path);
    }
    if (const char* override_path = std::getenv("CUFFT_DLL_PATH");
        override_path != nullptr && override_path[0] != '\0') {
#ifdef _WIN32
        if (HMODULE module = LoadLibraryA(override_path); module != nullptr) return module;
#else
        if (void* module = dlopen(override_path, RTLD_NOW | RTLD_LOCAL); module != nullptr) return module;
#endif
        throw_unavailable(std::string("CUFFT_DLL_PATH load failed: ") + override_path);
    }
#ifdef _WIN32
    const std::vector<std::string> candidates{
        "cufft64_12.dll", "cufft64_13.dll", "cufft64_11.dll", "cufft64_10.dll", "cufft.dll"};
#else
    const std::vector<std::string> candidates{
        "libcufft.so.12", "libcufft.so.11", "libcufft.so.10", "libcufft.so"};
#endif
    for (const auto& candidate : candidates) {
#ifdef _WIN32
        if (HMODULE module = LoadLibraryA(candidate.c_str()); module != nullptr) return module;
#else
        if (void* module = dlopen(candidate.c_str(), RTLD_NOW | RTLD_LOCAL); module != nullptr) return module;
#endif
    }
    for (const char* root_variable : {"CUDA_PATH", "CUDAToolkit_ROOT", "CUDA_HOME"}) {
        const char* root = std::getenv(root_variable);
        if (root == nullptr || root[0] == '\0') continue;
#ifdef _WIN32
        const std::vector<std::string> directories{std::string(root) + "\\bin\\x64\\"};
#else
        const std::vector<std::string> directories{
            std::string(root) + "/lib64/", std::string(root) + "/targets/aarch64-linux/lib/"};
#endif
        for (const auto& directory : directories) {
            for (const auto& candidate : candidates) {
#ifdef _WIN32
                if (HMODULE module = LoadLibraryA((directory + candidate).c_str()); module != nullptr) return module;
#else
                if (void* module = dlopen((directory + candidate).c_str(), RTLD_NOW | RTLD_LOCAL); module != nullptr) return module;
#endif
            }
        }
    }
#ifdef _WIN32
    throw_unavailable("cufft64_12.dll/cufft.dll not found in PATH or CUDA_PATH");
#else
    throw_unavailable("libcufft.so.12/libcufft.so not found in the loader path or CUDA_HOME");
#endif
}

[[nodiscard]] void* resolve(void* library, const char* name) {
#ifdef _WIN32
    void* symbol = reinterpret_cast<void*>(GetProcAddress(static_cast<HMODULE>(library), name));
#else
    dlerror();
    void* symbol = dlsym(library, name);
#endif
    if (symbol == nullptr) throw_unavailable(std::string("missing cuFFT export: ") + name);
    return symbol;
}

}  // namespace
const CufftApi& CufftApi::instance() {
    static const CufftApi api;
    return api;
}

CufftApi::CufftApi() {
    library_ = load_cufft_library();
    create = reinterpret_cast<CreateFn>(resolve(library_, "cufftCreate"));
    destroy = reinterpret_cast<DestroyFn>(resolve(library_, "cufftDestroy"));
    plan1d = reinterpret_cast<Plan1dFn>(resolve(library_, "cufftPlan1d"));
    set_stream = reinterpret_cast<SetStreamFn>(resolve(library_, "cufftSetStream"));
    exec_z2z = reinterpret_cast<ExecZ2ZFn>(resolve(library_, "cufftExecZ2Z"));
    exec_c2c = reinterpret_cast<ExecC2CFn>(resolve(library_, "cufftExecC2C"));
    get_version = reinterpret_cast<GetVersionFn>(resolve(library_, "cufftGetVersion"));
}

int CufftApi::runtime_version() const {
    int version = 0;
    if (get_version(&version) != CUFFT_SUCCESS) {
        return 0;
    }
    return version;
}

void CufftApi::throw_failure(
    const sdr_core::BackendErrorCode code,
    const char* what,
    const cufftResult status
) const {
    throw sdr_core::DeviceError(
        std::string(what) + ": cuFFT error " + std::to_string(static_cast<int>(status)),
        code
    );
}

CufftPlanCache::CufftPlanCache(const std::uint32_t capacity) : capacity_(capacity) {
    if (capacity == 0U) {
        throw sdr_core::ConfigurationError("cuFFT plan cache capacity must be positive");
    }
}

CufftPlanCache::~CufftPlanCache() noexcept {
    if (api_ != nullptr) {
        for (auto& entry : lru_) {
            static_cast<void>(api_->destroy(entry.plan));
        }
    }
}

cufftHandle CufftPlanCache::acquire(const CufftPlanKey& key, const cudaStream_t stream) {
    if (api_ == nullptr) {
        api_ = &CufftApi::instance();
    }
    const auto found = index_.find(key);
    if (found != index_.end()) {
        ++hits_;
        lru_.splice(lru_.begin(), lru_, found->second);
        if (api_->set_stream(found->second->plan, stream) != CUFFT_SUCCESS) {
            api_->throw_failure(sdr_core::BackendErrorCode::FftPlanFailed, "cufftSetStream", CUFFT_INVALID_PLAN);
        }
        return found->second->plan;
    }

    ++misses_;
    Entry entry;
    entry.key = key;
    if (api_->create(&entry.plan) != CUFFT_SUCCESS) {
        api_->throw_failure(sdr_core::BackendErrorCode::FftPlanFailed, "cufftCreate", CUFFT_ALLOC_FAILED);
    }
    const auto plan_status = api_->plan1d(
        &entry.plan,
        key.fft_size,
        static_cast<cufftType>(key.precision == sdr_core::PrecisionMode::ReferenceF64 ? CUFFT_Z2Z : CUFFT_C2C),
        key.batch_size
    );
    if (plan_status != CUFFT_SUCCESS) {
        static_cast<void>(api_->destroy(entry.plan));
        api_->throw_failure(sdr_core::BackendErrorCode::FftPlanFailed, "cufftPlan1d", plan_status);
    }
    if (api_->set_stream(entry.plan, stream) != CUFFT_SUCCESS) {
        static_cast<void>(api_->destroy(entry.plan));
        api_->throw_failure(sdr_core::BackendErrorCode::FftPlanFailed, "cufftSetStream", CUFFT_INVALID_PLAN);
    }

    if (index_.size() >= capacity_) {
        const auto& oldest = lru_.back();
        static_cast<void>(api_->destroy(oldest.plan));
        index_.erase(oldest.key);
        lru_.pop_back();
        ++evictions_;
    }
    lru_.push_front(entry);
    index_.emplace(key, lru_.begin());
    return lru_.front().plan;
}

CufftPlanCacheStats CufftPlanCache::stats() const {
    CufftPlanCacheStats result;
    result.hits = hits_;
    result.misses = misses_;
    result.evictions = evictions_;
    result.size = static_cast<std::uint32_t>(index_.size());
    result.capacity = capacity_;
    return result;
}

sdr_core::BackendAvailability availability(const int device_id) {
    sdr_core::BackendAvailability result;
    result.compiled = true;
    int count = 0;
    const auto status = cudaGetDeviceCount(&count);
    if (status == cudaErrorNoDevice || status == cudaErrorInsufficientDriver) {
        result.reason_code = std::string(to_wire(sdr_core::BackendErrorCode::NoDevice));
        result.details = cudaGetErrorString(status);
        return result;
    }
    if (status != cudaSuccess) {
        result.reason_code = std::string(to_wire(sdr_core::BackendErrorCode::RuntimeIncompatible));
        result.details = cudaGetErrorString(status);
        return result;
    }
    result.runtime_present = true;
    result.device_count = static_cast<std::uint32_t>(count);
    if (count == 0) {
        result.reason_code = std::string(to_wire(sdr_core::BackendErrorCode::NoDevice));
        result.details = "no CUDA devices";
        return result;
    }
    const int device = device_id >= 0 ? device_id : 0;
    if (device >= count) {
        result.reason_code = std::string(to_wire(sdr_core::BackendErrorCode::NoDevice));
        result.details = "requested device id is out of range";
        return result;
    }
    cudaDeviceProp properties{};
    if (cudaGetDeviceProperties(&properties, device) != cudaSuccess) {
        result.reason_code = std::string(to_wire(sdr_core::BackendErrorCode::UnsupportedDevice));
        result.details = "cudaGetDeviceProperties failed";
        return result;
    }
    result.device_supported = true;
    // cuFFT DLL presence is part of availability (runtime layer).
    try {
        static_cast<void>(CufftApi::instance());
    } catch (const sdr_core::BackendUnavailableError& error) {
        result.device_supported = false;
        result.reason_code = std::string(to_wire(sdr_core::BackendErrorCode::RuntimeNotFound));
        result.details = error.what();
        return result;
    }
    return result;
}

sdr_core::BackendInfo device_info(const int device_id, const bool self_test_passed) {
    sdr_core::BackendInfo result;
    result.kind = sdr_core::ComputeBackendKind::Cuda;
    result.backend_id = "cuda-cufft";
    result.vendor = "NVIDIA";
    const int device = device_id >= 0 ? device_id : 0;
    cudaDeviceProp properties{};
    if (cudaGetDeviceProperties(&properties, device) == cudaSuccess) {
        result.device_name = properties.name;
        result.device_uuid = "pci:" + std::to_string(properties.pciDomainID) + ":" +
                             std::to_string(properties.pciBusID) + ":" +
                             std::to_string(properties.pciDeviceID);
        result.architecture =
            "sm_" + std::to_string(properties.major) + std::to_string(properties.minor);
        result.total_memory_bytes = static_cast<std::uint64_t>(properties.totalGlobalMem);
        result.supports_fp64 = true;
        result.supports_pinned_host = true;
        result.supports_async_copy = properties.asyncEngineCount > 0;
        int managed_memory = 0;
        if (cudaDeviceGetAttribute(&managed_memory, cudaDevAttrManagedMemory, device) == cudaSuccess) {
            result.supports_managed_memory = managed_memory != 0;
        }
    }
    int driver_version = 0;
    if (cudaDriverGetVersion(&driver_version) == cudaSuccess) {
        result.driver_version = std::to_string(driver_version);
    }
    int runtime_version = 0;
    if (cudaRuntimeGetVersion(&runtime_version) == cudaSuccess) {
        result.runtime_version = std::to_string(runtime_version);
    }
    result.fft_library = "cuFFT(dynamic)";
    try {
        result.fft_library_version = std::to_string(CufftApi::instance().runtime_version());
    } catch (const sdr_core::BackendUnavailableError&) {
        result.fft_library_version = "unavailable";
    }
    result.validated = self_test_passed;
    return result;
}

}  // namespace sdr_cuda
