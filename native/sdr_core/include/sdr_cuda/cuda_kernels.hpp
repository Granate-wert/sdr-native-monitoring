#pragma once

#include <cufft.h>

namespace sdr_cuda {

void launch_dc_window_f64(
    const cufftDoubleComplex* input,
    cufftDoubleComplex* output,
    const double* coefficients,
    int batch,
    int fft_size,
    bool block_mean,
    cudaStream_t stream
);

void launch_dc_window_f32(
    const cufftComplex* input,
    cufftComplex* output,
    const float* coefficients,
    int batch,
    int fft_size,
    bool block_mean,
    cudaStream_t stream
);

void launch_power_stage_f64(
    const cufftDoubleComplex* spectrum,
    double* powers,
    double denominator,
    int batch,
    int fft_size,
    cudaStream_t stream
);

void launch_power_stage_f32(
    const cufftComplex* spectrum,
    float* powers,
    double denominator,
    int batch,
    int fft_size,
    cudaStream_t stream
);

void launch_power_stage_f32_to_f64(
    const cufftComplex* spectrum,
    double* powers,
    double denominator,
    int batch,
    int fft_size,
    cudaStream_t stream
);

void launch_accum_f64(
    const double* powers,
    int frames,
    int fft_size,
    double* sum,
    double* maximum,
    double* minimum,
    double* last,
    cudaStream_t stream
);

void launch_accum_f32(
    const float* powers,
    int frames,
    int fft_size,
    float* sum,
    float* maximum,
    float* minimum,
    float* last,
    cudaStream_t stream
);

}  // namespace sdr_cuda
