#pragma once

#include "sdr_core/dsp_backend.hpp"
#include "sdr_cuda/cuda_runtime.hpp"

#include <cstdint>
#include <list>
#include <unordered_map>

namespace sdr_cuda {

using CufftPlanKey = sdr_core::FftPlanKey;

struct CufftPlanKeyHash {
    [[nodiscard]] std::size_t operator()(const CufftPlanKey& key) const noexcept {
        std::uint64_t value = static_cast<std::uint32_t>(key.backend_kind);
        value = value * 1000003ULL + static_cast<std::uint32_t>(key.device_id);
        value = value * 1000033ULL + key.fft_size;
        value = value * 1000039ULL + key.batch_size;
        value = value * 1000081ULL + static_cast<std::uint32_t>(key.precision);
        value = value * 1000099ULL + static_cast<std::uint32_t>(key.transform);
        value = value * 1000117ULL + static_cast<std::uint32_t>(key.input_layout);
        value = value * 1000129ULL + static_cast<std::uint32_t>(key.output_layout);
        value = value * 1000141ULL + key.input_stride;
        value = value * 1000153ULL + key.output_stride;
        return static_cast<std::size_t>(value);
    }
};
struct CufftPlanCacheStats {
    std::uint64_t hits{};
    std::uint64_t misses{};
    std::uint64_t evictions{};
    std::uint32_t size{};
    std::uint32_t capacity{};
};

// Bounded cuFFT plan cache with deterministic LRU eviction (P08 §9.6).
class CufftPlanCache final {
public:
    explicit CufftPlanCache(std::uint32_t capacity);
    ~CufftPlanCache() noexcept;

    CufftPlanCache(const CufftPlanCache&) = delete;
    CufftPlanCache& operator=(const CufftPlanCache&) = delete;

    // Returns a ready plan bound to the stream, creating (and possibly
    // evicting) as needed. Throws CudaFailure on plan errors.
    [[nodiscard]] cufftHandle acquire(const CufftPlanKey& key, cudaStream_t stream);

    [[nodiscard]] CufftPlanCacheStats stats() const;

private:
    struct Entry {
        CufftPlanKey key;
        cufftHandle plan{};
    };

    const CufftApi* api_{};
    std::uint32_t capacity_;
    std::list<Entry> lru_;
    std::unordered_map<CufftPlanKey, std::list<Entry>::iterator, CufftPlanKeyHash> index_;
    std::uint64_t hits_{};
    std::uint64_t misses_{};
    std::uint64_t evictions_{};
};

}  // namespace sdr_cuda
