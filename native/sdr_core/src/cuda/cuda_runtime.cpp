#include "sdr_cuda/cuda_runtime.hpp"

#include <cstdlib>
#include <utility>

namespace sdr_cuda {

namespace {

[[nodiscard]] std::string error_name(const cudaError_t status) {
    const char* name = cudaGetErrorName(status);
    return name != nullptr ? name : "unknown";
}

[[nodiscard]] sdr_core::BackendErrorCode classify(const cudaError_t status) {
    switch (status) {
    case cudaErrorMemoryAllocation:
    case cudaErrorLaunchOutOfResources:
        return sdr_core::BackendErrorCode::AllocationFailed;
    case cudaErrorInvalidValue:
    case cudaErrorInvalidMemcpyDirection:
        return sdr_core::BackendErrorCode::CopyFailed;
    case cudaErrorNoDevice:
    case cudaErrorInsufficientDriver:
        return sdr_core::BackendErrorCode::NoDevice;
    case cudaErrorDevicesUnavailable:
        return sdr_core::BackendErrorCode::DeviceLost;
    case cudaErrorLaunchTimeout:
    case cudaErrorTimeout:
        return sdr_core::BackendErrorCode::TimeoutOrTdr;
    case cudaErrorUnknown:
    default:
        return sdr_core::BackendErrorCode::Unknown;
    }
}

}  // namespace

void throw_cuda_failure(
    const sdr_core::BackendErrorCode code,
    const std::string& what,
    const cudaError_t status
) {
    const auto effective = code == sdr_core::BackendErrorCode::Unknown ? classify(status) : code;
    throw sdr_core::DeviceError(what + std::string(": ") + error_name(status), effective);
}

void check_cuda(const sdr_core::BackendErrorCode code, const char* what, const cudaError_t status) {
    if (status != cudaSuccess) {
        throw_cuda_failure(code, what, status);
    }
}

DeviceGuard::DeviceGuard(const int device) {
    check_cuda(sdr_core::BackendErrorCode::NoDevice, "cudaGetDevice", cudaGetDevice(&previous_));
    if (device >= 0 && device != previous_) {
        check_cuda(sdr_core::BackendErrorCode::NoDevice, "cudaSetDevice", cudaSetDevice(device));
    } else {
        previous_ = -1;
    }
}

DeviceGuard::~DeviceGuard() noexcept {
    if (previous_ >= 0) {
        static_cast<void>(cudaSetDevice(previous_));
    }
}

Stream::Stream() {
    check_cuda(sdr_core::BackendErrorCode::Unknown, "cudaStreamCreate", cudaStreamCreate(&stream_));
}

Stream::~Stream() noexcept {
    if (stream_ != nullptr) {
        static_cast<void>(cudaStreamDestroy(stream_));
    }
}

Stream::Stream(Stream&& other) noexcept : stream_(std::exchange(other.stream_, nullptr)) {}

Stream& Stream::operator=(Stream&& other) noexcept {
    if (this != &other) {
        this->~Stream();
        stream_ = std::exchange(other.stream_, nullptr);
    }
    return *this;
}

Event::Event() {
    // Timing-enabled events: used both for sync boundaries and stage timing.
    check_cuda(
        sdr_core::BackendErrorCode::Unknown,
        "cudaEventCreate",
        cudaEventCreate(&event_)
    );
}

Event::~Event() noexcept {
    if (event_ != nullptr) {
        static_cast<void>(cudaEventDestroy(event_));
    }
}

Event::Event(Event&& other) noexcept : event_(std::exchange(other.event_, nullptr)) {}

Event& Event::operator=(Event&& other) noexcept {
    if (this != &other) {
        this->~Event();
        event_ = std::exchange(other.event_, nullptr);
    }
    return *this;
}

void Event::record(const Stream& stream) {
    check_cuda(sdr_core::BackendErrorCode::Unknown, "cudaEventRecord", cudaEventRecord(event_, stream.get()));
}

void Event::synchronize() {
    check_cuda(sdr_core::BackendErrorCode::Unknown, "cudaEventSynchronize", cudaEventSynchronize(event_));
}

float Event::elapsed_ms_since(const Event& earlier) const {
    float elapsed = 0.0F;
    check_cuda(
        sdr_core::BackendErrorCode::Unknown,
        "cudaEventElapsedTime",
        cudaEventElapsedTime(&elapsed, earlier.event_, event_)
    );
    return elapsed;
}

DeviceBuffer::DeviceBuffer(const std::size_t bytes) : bytes_(bytes) {
    if (bytes == 0U) {
        return;
    }
    check_cuda(sdr_core::BackendErrorCode::AllocationFailed, "cudaMalloc", cudaMalloc(&pointer_, bytes));
}

DeviceBuffer::~DeviceBuffer() noexcept {
    release();
}

DeviceBuffer::DeviceBuffer(DeviceBuffer&& other) noexcept
    : pointer_(std::exchange(other.pointer_, nullptr)),
      bytes_(std::exchange(other.bytes_, 0U)) {}

DeviceBuffer& DeviceBuffer::operator=(DeviceBuffer&& other) noexcept {
    if (this != &other) {
        release();
        pointer_ = std::exchange(other.pointer_, nullptr);
        bytes_ = std::exchange(other.bytes_, 0U);
    }
    return *this;
}

void DeviceBuffer::release() {
    if (pointer_ != nullptr) {
        static_cast<void>(cudaFree(pointer_));
        pointer_ = nullptr;
        bytes_ = 0U;
    }
}

PinnedBuffer::PinnedBuffer(const std::size_t bytes) : bytes_(bytes) {
    if (bytes == 0U) {
        return;
    }
    check_cuda(
        sdr_core::BackendErrorCode::AllocationFailed,
        "cudaHostAlloc",
        cudaHostAlloc(&pointer_, bytes, cudaHostAllocDefault)
    );
}

PinnedBuffer::~PinnedBuffer() noexcept {
    release();
}

PinnedBuffer::PinnedBuffer(PinnedBuffer&& other) noexcept
    : pointer_(std::exchange(other.pointer_, nullptr)),
      bytes_(std::exchange(other.bytes_, 0U)) {}

PinnedBuffer& PinnedBuffer::operator=(PinnedBuffer&& other) noexcept {
    if (this != &other) {
        release();
        pointer_ = std::exchange(other.pointer_, nullptr);
        bytes_ = std::exchange(other.bytes_, 0U);
    }
    return *this;
}

void PinnedBuffer::release() {
    if (pointer_ != nullptr) {
        static_cast<void>(cudaFreeHost(pointer_));
        pointer_ = nullptr;
        bytes_ = 0U;
    }
}

}  // namespace sdr_cuda
