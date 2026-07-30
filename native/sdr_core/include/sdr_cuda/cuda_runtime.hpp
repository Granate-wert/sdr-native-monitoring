#pragma once

// CUDA-specific boundary header. Common code must include only sdr_core/*
// headers; everything CUDA lives under sdr_cuda/ (P08H-00 portability gate).

#include "sdr_core/backend_info.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/types.hpp"

#include <cuda_runtime.h>
#include <cufft.h>

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>

namespace sdr_cuda {

// Translates a CUDA runtime error into the vendor-neutral taxonomy and throws.
[[noreturn]] void throw_cuda_failure(
    sdr_core::BackendErrorCode code,
    const std::string& what,
    cudaError_t status
);

void check_cuda(sdr_core::BackendErrorCode code, const char* what, cudaError_t status);

class DeviceGuard final {
public:
    explicit DeviceGuard(int device);
    ~DeviceGuard() noexcept;
    DeviceGuard(const DeviceGuard&) = delete;
    DeviceGuard& operator=(const DeviceGuard&) = delete;

private:
    int previous_{-1};
};

class Stream final {
public:
    Stream();
    ~Stream() noexcept;
    Stream(const Stream&) = delete;
    Stream& operator=(const Stream&) = delete;
    Stream(Stream&& other) noexcept;
    Stream& operator=(Stream&& other) noexcept;

    [[nodiscard]] cudaStream_t get() const noexcept { return stream_; }

private:
    cudaStream_t stream_{};
};

class Event final {
public:
    Event();
    ~Event() noexcept;
    Event(const Event&) = delete;
    Event& operator=(const Event&) = delete;
    Event(Event&& other) noexcept;
    Event& operator=(Event&& other) noexcept;

    void record(const Stream& stream);
    void synchronize();
    [[nodiscard]] float elapsed_ms_since(const Event& earlier) const;
    [[nodiscard]] cudaEvent_t get() const noexcept { return event_; }

private:
    cudaEvent_t event_{};
};

class DeviceBuffer final {
public:
    explicit DeviceBuffer(std::size_t bytes);
    ~DeviceBuffer() noexcept;
    DeviceBuffer(const DeviceBuffer&) = delete;
    DeviceBuffer& operator=(const DeviceBuffer&) = delete;
    DeviceBuffer(DeviceBuffer&& other) noexcept;
    DeviceBuffer& operator=(DeviceBuffer&& other) noexcept;

    [[nodiscard]] void* get() noexcept { return pointer_; }
    [[nodiscard]] const void* get() const noexcept { return pointer_; }
    [[nodiscard]] std::size_t bytes() const noexcept { return bytes_; }
    void release();

private:
    void* pointer_{};
    std::size_t bytes_{};
};

class PinnedBuffer final {
public:
    explicit PinnedBuffer(std::size_t bytes);
    ~PinnedBuffer() noexcept;
    PinnedBuffer(const PinnedBuffer&) = delete;
    PinnedBuffer& operator=(const PinnedBuffer&) = delete;
    PinnedBuffer(PinnedBuffer&& other) noexcept;
    PinnedBuffer& operator=(PinnedBuffer&& other) noexcept;

    [[nodiscard]] void* get() noexcept { return pointer_; }
    [[nodiscard]] const void* get() const noexcept { return pointer_; }
    [[nodiscard]] std::size_t bytes() const noexcept { return bytes_; }
    void release();

private:
    void* pointer_{};
    std::size_t bytes_{};
};

// cuFFT is loaded dynamically (like the P06 libiio adapter) so that binaries
// launch on machines without CUDA DLLs; cudart is linked statically.
class CufftApi final {
public:
    using CreateFn = cufftResult (*)(cufftHandle*);
    using DestroyFn = cufftResult (*)(cufftHandle);
    using Plan1dFn = cufftResult (*)(cufftHandle*, int, cufftType, int);
    using SetStreamFn = cufftResult (*)(cufftHandle, cudaStream_t);
    using ExecZ2ZFn = cufftResult (*)(cufftHandle, cufftDoubleComplex*, cufftDoubleComplex*, int);
    using ExecC2CFn = cufftResult (*)(cufftHandle, cufftComplex*, cufftComplex*, int);
    using GetVersionFn = cufftResult (*)(int*);

    static const CufftApi& instance();

    CufftApi(const CufftApi&) = delete;
    CufftApi& operator=(const CufftApi&) = delete;

    CreateFn create{};
    DestroyFn destroy{};
    Plan1dFn plan1d{};
    SetStreamFn set_stream{};
    ExecZ2ZFn exec_z2z{};
    ExecC2CFn exec_c2c{};
    GetVersionFn get_version{};

    [[nodiscard]] int runtime_version() const;
    [[noreturn]] void throw_failure(sdr_core::BackendErrorCode code, const char* what, cufftResult status) const;

private:
    CufftApi();
    void* library_{};
};

sdr_core::BackendAvailability availability(int device_id);
sdr_core::BackendInfo device_info(int device_id, bool self_test_passed);

}  // namespace sdr_cuda
