#include "sdr_cuda/cufft_plan_cache.hpp"

#include <algorithm>
#include <cstdlib>
#include <utility>
#include <vector>

#ifndef _WIN32
#error "P08 CUDA backend is currently verified on Windows only"
#endif

#include <windows.h>

namespace sdr_cuda {

namespace {

[[noreturn]] void throw_unavailable(const std::string& details) {
    throw sdr_core::BackendUnavailableError(
        "cuFFT runtime is unavailable: " + details
    );
}

[[nodiscard]] void* load_cufft_library() {
    if (const char* override_path = std::getenv("CUFFT_DLL_PATH")) {
        if (override_path[0] != '\0') {
            const std::wstring wide(override_path, override_path + std::strlen(override_path));
            HMODULE module = LoadLibraryExW(
                wide.c_str(),
                nullptr,
                LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
            );
            if (module != nullptr) {
                return module;
            }
            throw_unavailable(std::string("CUFFT_DLL_PATH load failed: ") + override_path);
        }
    }
    // cuFFT keeps the ABI-12 soname in CUDA 13.x; prefer exact, then generic.
    static const wchar_t* candidates[] = {
        L"cufft64_12.dll",
        L"cufft64_13.dll",
        L"cufft64_11.dll",
        L"cufft64_10.dll",
        L"cufft.dll",
    };
    // Plain LoadLibraryW searches PATH (safe-search ExW variants do not).
    for (const auto* candidate : candidates) {
        HMODULE module = LoadLibraryW(candidate);
        if (module != nullptr) {
            return module;
        }
    }
    // The standard CUDA installer exports CUDA_PATH; probe its bin\x64 dir.
    for (const char* variable : {"CUDA_PATH", "CUDAToolkit_ROOT"}) {
        const char* root = std::getenv(variable);
        if (root == nullptr || root[0] == '\0') {
            continue;
        }
        const std::string base(root);
        for (const auto* candidate : candidates) {
            const std::string full = base + "\\bin\\x64\\" +
                                     std::string(candidate, candidate + std::wcslen(candidate));
            const std::wstring wide(full.begin(), full.end());
            HMODULE module = LoadLibraryExW(
                wide.c_str(),
                nullptr,
                LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
            );
            if (module != nullptr) {
                return module;
            }
        }
    }
    // Final fallback: probe the official default toolkit layout
    // (C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*\\bin\x64).
    {
        const std::wstring root = L"C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\";
        WIN32_FIND_DATAW data{};
        HANDLE search = FindFirstFileW((root + L"v*").c_str(), &data);
        if (search != INVALID_HANDLE_VALUE) {
            std::vector<std::wstring> versions;
            do {
                if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0U &&
                    data.cFileName[0] != L'.') {
                    versions.emplace_back(data.cFileName);
                }
            } while (FindNextFileW(search, &data) != 0);
            FindClose(search);
            std::sort(versions.rbegin(), versions.rend());
            for (const auto& version : versions) {
                for (const auto* candidate : candidates) {
                    const std::wstring full =
                        root + version + L"\\bin\\x64\\" + candidate;
                    HMODULE module = LoadLibraryExW(
                        full.c_str(),
                        nullptr,
                        LOAD_LIBRARY_SEARCH_DEFAULT_DIRS | LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR
                    );
                    if (module != nullptr) {
                        return module;
                    }
                }
            }
        }
    }
    throw_unavailable("cufft64_12.dll/cufft.dll not found in the search path");
}

[[nodiscard]] void* resolve(void* library, const char* name) {
    void* symbol = reinterpret_cast<void*>(GetProcAddress(static_cast<HMODULE>(library), name));
    if (symbol == nullptr) {
        throw_unavailable(std::string("missing cuFFT export: ") + name);
    }
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
