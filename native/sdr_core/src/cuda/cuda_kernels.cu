// P08 CUDA DSP kernels. Semantics mirror the P05 CPU backend exactly:
// symmetric window, optional block-mean DC removal, unnormalized FFT output
// scaled by (N*CG)^2 or (Fs*sum_w^2), fftshift bin mapping, linear-domain
// detector accumulation (sum/max/min/last). No warp-size assumptions.

#include "sdr_cuda/cuda_kernels.hpp"

namespace {

constexpr int kBlockThreads = 256;

template <typename T, typename C>
__global__ void dc_window_kernel(
    const C* input,
    C* output,
    const T* coefficients,
    const int fft_size,
    const int block_mean
) {
    // One block per frame.
    const C* frame_in = input + static_cast<size_t>(blockIdx.x) * fft_size;
    C* frame_out = output + static_cast<size_t>(blockIdx.x) * fft_size;

    __shared__ T shared_re[kBlockThreads];
    __shared__ T shared_im[kBlockThreads];

    T mean_re = 0;
    T mean_im = 0;
    if (block_mean != 0) {
        T partial_re = 0;
        T partial_im = 0;
        for (int k = threadIdx.x; k < fft_size; k += kBlockThreads) {
            partial_re += frame_in[k].x;
            partial_im += frame_in[k].y;
        }
        shared_re[threadIdx.x] = partial_re;
        shared_im[threadIdx.x] = partial_im;
        __syncthreads();
        for (int stride = kBlockThreads / 2; stride > 0; stride >>= 1) {
            if (threadIdx.x < stride) {
                shared_re[threadIdx.x] += shared_re[threadIdx.x + stride];
                shared_im[threadIdx.x] += shared_im[threadIdx.x + stride];
            }
            __syncthreads();
        }
        mean_re = shared_re[0] / static_cast<T>(fft_size);
        mean_im = shared_im[0] / static_cast<T>(fft_size);
    }

    for (int k = threadIdx.x; k < fft_size; k += kBlockThreads) {
        const C value = frame_in[k];
        const T centered_re = value.x - mean_re;
        const T centered_im = value.y - mean_im;
        C result;
        result.x = centered_re * coefficients[k];
        result.y = centered_im * coefficients[k];
        frame_out[k] = result;
    }
}

template <typename C, typename P>
__global__ void power_stage_kernel(
    const C* spectrum,
    P* powers,
    const double denominator,
    const int fft_size
) {
    // 2D grid: y = frame index, x-blocks stride over bins.
    const int half = fft_size >> 1;
    const size_t frame_base = static_cast<size_t>(blockIdx.y) * fft_size;
    for (int k = blockIdx.x * blockDim.x + threadIdx.x; k < fft_size;
         k += blockDim.x * gridDim.x) {
        const C value = spectrum[frame_base + k];
        const double magnitude =
            static_cast<double>(value.x) * static_cast<double>(value.x) +
            static_cast<double>(value.y) * static_cast<double>(value.y);
        const int shifted = (k + half) % fft_size;
        powers[frame_base + shifted] = static_cast<P>(magnitude / denominator);
    }
}

template <typename P>
__global__ void accum_kernel(
    const P* powers,
    const int frames,
    const int fft_size,
    P* sum,
    P* maximum,
    P* minimum,
    P* last
) {
    // One thread per bin; serial over frames keeps SAMPLE-detector semantics
    // (last frame of the group wins deterministically).
    const int bin = blockIdx.x * blockDim.x + threadIdx.x;
    if (bin >= fft_size) {
        return;
    }
    P total = sum[bin];
    P peak = maximum[bin];
    P valley = minimum[bin];
    P latest = last[bin];
    for (int frame = 0; frame < frames; ++frame) {
        const P value = powers[static_cast<size_t>(frame) * fft_size + bin];
        total += value;
        peak = value > peak ? value : peak;
        valley = value < valley ? value : valley;
        latest = value;
    }
    sum[bin] = total;
    maximum[bin] = peak;
    minimum[bin] = valley;
    last[bin] = latest;
}

}  // namespace

namespace sdr_cuda {

void launch_dc_window_f64(
    const cufftDoubleComplex* input,
    cufftDoubleComplex* output,
    const double* coefficients,
    const int batch,
    const int fft_size,
    const bool block_mean,
    const cudaStream_t stream
) {
    dc_window_kernel<double, cufftDoubleComplex>
        <<<batch, kBlockThreads, 0, stream>>>(
            input,
            output,
            coefficients,
            fft_size,
            block_mean ? 1 : 0
        );
}

void launch_dc_window_f32(
    const cufftComplex* input,
    cufftComplex* output,
    const float* coefficients,
    const int batch,
    const int fft_size,
    const bool block_mean,
    const cudaStream_t stream
) {
    dc_window_kernel<float, cufftComplex>
        <<<batch, kBlockThreads, 0, stream>>>(
            input,
            output,
            coefficients,
            fft_size,
            block_mean ? 1 : 0
        );
}

void launch_power_stage_f64(
    const cufftDoubleComplex* spectrum,
    double* powers,
    const double denominator,
    const int batch,
    const int fft_size,
    const cudaStream_t stream
) {
    const dim3 grid(32, batch);
    power_stage_kernel<cufftDoubleComplex, double>
        <<<grid, kBlockThreads, 0, stream>>>(spectrum, powers, denominator, fft_size);
}

void launch_power_stage_f32(
    const cufftComplex* spectrum,
    float* powers,
    const double denominator,
    const int batch,
    const int fft_size,
    const cudaStream_t stream
) {
    const dim3 grid(32, batch);
    power_stage_kernel<cufftComplex, float>
        <<<grid, kBlockThreads, 0, stream>>>(spectrum, powers, denominator, fft_size);
}

void launch_power_stage_f32_to_f64(
    const cufftComplex* spectrum,
    double* powers,
    const double denominator,
    const int batch,
    const int fft_size,
    const cudaStream_t stream
) {
    const dim3 grid(32, batch);
    power_stage_kernel<cufftComplex, double>
        <<<grid, kBlockThreads, 0, stream>>>(spectrum, powers, denominator, fft_size);
}

void launch_accum_f64(
    const double* powers,
    const int frames,
    const int fft_size,
    double* sum,
    double* maximum,
    double* minimum,
    double* last,
    const cudaStream_t stream
) {
    const int blocks = (fft_size + kBlockThreads - 1) / kBlockThreads;
    accum_kernel<double>
        <<<blocks, kBlockThreads, 0, stream>>>(
            powers,
            frames,
            fft_size,
            sum,
            maximum,
            minimum,
            last
        );
}

void launch_accum_f32(
    const float* powers,
    const int frames,
    const int fft_size,
    float* sum,
    float* maximum,
    float* minimum,
    float* last,
    const cudaStream_t stream
) {
    const int blocks = (fft_size + kBlockThreads - 1) / kBlockThreads;
    accum_kernel<float>
        <<<blocks, kBlockThreads, 0, stream>>>(
            powers,
            frames,
            fft_size,
            sum,
            maximum,
            minimum,
            last
        );
}

}  // namespace sdr_cuda
