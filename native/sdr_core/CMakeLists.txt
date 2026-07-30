cmake_minimum_required(VERSION 3.25)

project(sdr_core VERSION 0.6.0 LANGUAGES CXX)

option(SDR_CORE_BUILD_PYTHON "Build the esw_dfl._sdr_native Python extension" ON)
option(SDR_CORE_BUILD_TESTS "Build native tests" ON)
option(SDR_CORE_ENABLE_PLUTO "Enable the Pluto/libiio backend" OFF)
option(SDR_CORE_ENABLE_CUDA "Enable the CUDA backend" OFF)
option(SDR_CORE_ENABLE_SIMD "Enable portable compiler SIMD paths when implemented" ON)
option(SDR_CORE_ENABLE_SANITIZERS "Enable supported native sanitizers" OFF)
option(SDR_CORE_ENABLE_PROFILING "Enable native profiling instrumentation" OFF)
set(SDR_CORE_PYTHON_OUTPUT_DIR "" CACHE PATH "Optional in-place output directory for _sdr_native")

if(SDR_CORE_ENABLE_PLUTO AND NOT WIN32)
    message(FATAL_ERROR "P06 dynamic libiio backend is currently verified on Windows only")
endif()
if(SDR_CORE_ENABLE_CUDA)
    if(NOT WIN32)
        message(FATAL_ERROR "P08 CUDA backend is currently verified on Windows only")
    endif()
    enable_language(CUDA)
    if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES OR CMAKE_CUDA_ARCHITECTURES STREQUAL "")
        # The local GPU defines the target arch on the dev preset; never
        # hardcode a desktop arch in common source (Jetson sets its own).
        set(CMAKE_CUDA_ARCHITECTURES native)
    endif()
    find_package(CUDAToolkit REQUIRED)
endif()

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(Dependencies)
include(Platform)
include(Sanitizers)

add_library(sdr_core STATIC
    src/core/api.cpp
    src/core/buffer_pool.cpp
    src/core/contracts.cpp
    src/core/persistence.cpp
    src/calibration/calibration.cpp
    src/core/engine.cpp
    src/core/synthetic_source.cpp
    src/dsp/cpu_dsp_backend.cpp
    src/dsp/dsp_backend_selector.cpp
    src/dsp/fft_provider.cpp
    src/dsp/window.cpp
)
if(NOT SDR_CORE_ENABLE_CUDA)
    target_sources(sdr_core PRIVATE src/dsp/cuda_backend_link_stub.cpp)
endif()
add_library(sdr_core::sdr_core ALIAS sdr_core)
set_target_properties(sdr_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(sdr_core
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/third_party
)
target_compile_definitions(sdr_core
    PRIVATE
        SDR_CORE_COMPILER_ID="${CMAKE_CXX_COMPILER_ID}"
        SDR_CORE_COMPILER_VERSION="${CMAKE_CXX_COMPILER_VERSION}"
        SDR_CORE_PLATFORM="${CMAKE_SYSTEM_NAME}"
        SDR_CORE_ARCHITECTURE="${CMAKE_SYSTEM_PROCESSOR}"
        SDR_CORE_BUILD_TYPE="$<CONFIG>"
        SDR_CORE_CUDA_COMPILED=$<BOOL:${SDR_CORE_ENABLE_CUDA}>
        SDR_CORE_PLUTO_COMPILED=$<BOOL:${SDR_CORE_ENABLE_PLUTO}>
        SDR_CORE_SIMD_ENABLED=$<BOOL:${SDR_CORE_ENABLE_SIMD}>
        SDR_CORE_PROFILING_ENABLED=$<BOOL:${SDR_CORE_ENABLE_PROFILING}>
)
sdr_core_apply_platform_defaults(sdr_core)
sdr_core_apply_sanitizers(sdr_core)

if(SDR_CORE_ENABLE_CUDA)
    add_library(sdr_cuda STATIC
        src/cuda/cuda_backend_link.cpp
        src/cuda/cuda_dsp_backend.cpp
        src/cuda/cuda_kernels.cu
        src/cuda/cuda_runtime.cpp
        src/cuda/cufft_plan_cache.cpp
    )
    add_library(sdr_core::sdr_cuda ALIAS sdr_cuda)
    set_target_properties(sdr_cuda PROPERTIES
        POSITION_INDEPENDENT_CODE ON
        CUDA_SEPARABLE_COMPILATION OFF
        CUDA_PROPAGATE_HOST_FLAGS OFF
    )
    target_include_directories(sdr_cuda
        PUBLIC
            $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        PRIVATE
            ${CMAKE_CURRENT_SOURCE_DIR}/third_party
    )
    target_compile_features(sdr_cuda PUBLIC cxx_std_20)
    set_target_properties(sdr_cuda PROPERTIES CXX_EXTENSIONS OFF)
    # MSVC flags must not reach nvcc as bare inputs; wrap them per language.
    if(MSVC)
        target_compile_options(sdr_cuda PRIVATE
            $<$<COMPILE_LANGUAGE:CXX>:/W4>
            $<$<COMPILE_LANGUAGE:CXX>:/permissive->
            $<$<COMPILE_LANGUAGE:CXX>:/EHsc>
            $<$<COMPILE_LANGUAGE:CXX>:/utf-8>
            $<$<COMPILE_LANGUAGE:CUDA>:-Xcompiler=/W4,/permissive-,/EHsc,/utf-8>
        )
    endif()
    target_link_libraries(sdr_cuda PUBLIC sdr_core::sdr_core CUDA::cudart_static)
endif()

if(SDR_CORE_ENABLE_PLUTO)
    add_library(sdr_pluto STATIC
        src/pluto/pluto_backend_windows.cpp
        src/pluto/pluto_device_windows.cpp
        src/pluto/fixed_band_engine.cpp
    )
    target_include_directories(sdr_pluto PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
    target_compile_features(sdr_pluto PUBLIC cxx_std_20)
    target_link_libraries(sdr_pluto PUBLIC sdr_core::sdr_core)
    if(SDR_CORE_BUILD_TESTS)
        target_compile_definitions(sdr_pluto PUBLIC SDR_CORE_ENABLE_TEST_HOOKS=1)
    endif()
    sdr_core_apply_platform_defaults(sdr_pluto)
endif()

if(SDR_CORE_BUILD_PYTHON)
    pybind11_add_module(_sdr_native MODULE
        bindings/python_module.cpp
        bindings/contracts_binding.cpp
        bindings/calibration_binding.cpp
        bindings/aux_contracts_binding.cpp
        bindings/dsp_binding.cpp
        bindings/lifecycle_binding.cpp
        bindings/synthetic_binding.cpp
    )
    target_link_libraries(_sdr_native PRIVATE sdr_core::sdr_core)
    if(SDR_CORE_ENABLE_PLUTO)
        target_sources(_sdr_native PRIVATE bindings/pluto_binding.cpp)
        target_link_libraries(_sdr_native PRIVATE sdr_pluto)
        target_compile_definitions(_sdr_native PRIVATE SDR_CORE_PLUTO_COMPILED=1)
    endif()
    if(SDR_CORE_ENABLE_CUDA)
        target_link_libraries(_sdr_native PRIVATE sdr_core::sdr_cuda)
        target_compile_definitions(_sdr_native PRIVATE SDR_CORE_CUDA_COMPILED=1)
    endif()
    target_compile_features(_sdr_native PRIVATE cxx_std_20)
    sdr_core_apply_platform_defaults(_sdr_native)
    set_target_properties(_sdr_native PROPERTIES OUTPUT_NAME "_sdr_native")

    if(SDR_CORE_PYTHON_OUTPUT_DIR)
        set_target_properties(_sdr_native PROPERTIES
            LIBRARY_OUTPUT_DIRECTORY "${SDR_CORE_PYTHON_OUTPUT_DIR}"
            RUNTIME_OUTPUT_DIRECTORY "${SDR_CORE_PYTHON_OUTPUT_DIR}"
        )
        foreach(config Debug Release RelWithDebInfo MinSizeRel)
            string(TOUPPER "${config}" config_upper)
            set_target_properties(_sdr_native PROPERTIES
                LIBRARY_OUTPUT_DIRECTORY_${config_upper} "${SDR_CORE_PYTHON_OUTPUT_DIR}"
                RUNTIME_OUTPUT_DIRECTORY_${config_upper} "${SDR_CORE_PYTHON_OUTPUT_DIR}"
            )
        endforeach()
    endif()

    install(TARGETS _sdr_native
        LIBRARY DESTINATION esw_dfl
        RUNTIME DESTINATION esw_dfl
    )
endif()

if(SDR_CORE_BUILD_TESTS)
    include(CTest)
    add_executable(sdr_core_persistence_test tests/test_persistence.cpp)
    target_link_libraries(sdr_core_persistence_test PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_persistence_test)
    sdr_core_apply_sanitizers(sdr_core_persistence_test)
    add_test(NAME sdr_core.persistence COMMAND sdr_core_persistence_test)
    add_executable(sdr_core_calibration_test tests/test_calibration.cpp)
    target_link_libraries(sdr_core_calibration_test PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_calibration_test)
    sdr_core_apply_sanitizers(sdr_core_calibration_test)
    add_test(NAME sdr_core.calibration COMMAND sdr_core_calibration_test)
    add_executable(sdr_core_native_smoke tests/test_native_smoke.cpp)
    target_link_libraries(sdr_core_native_smoke PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_native_smoke)
    sdr_core_apply_sanitizers(sdr_core_native_smoke)
    add_test(NAME sdr_core.native_smoke COMMAND sdr_core_native_smoke)
    add_executable(sdr_core_contracts_test tests/test_contracts.cpp)
    target_link_libraries(sdr_core_contracts_test PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_contracts_test)
    sdr_core_apply_sanitizers(sdr_core_contracts_test)
    add_test(NAME sdr_core.contracts COMMAND sdr_core_contracts_test)
    add_executable(sdr_core_synthetic_source_test tests/test_synthetic_source.cpp)
    target_link_libraries(sdr_core_synthetic_source_test PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_synthetic_source_test)
    sdr_core_apply_sanitizers(sdr_core_synthetic_source_test)
    add_test(NAME sdr_core.synthetic_source COMMAND sdr_core_synthetic_source_test)
    add_executable(sdr_core_bounded_queue_test tests/test_bounded_queue.cpp)
    target_link_libraries(sdr_core_bounded_queue_test PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_bounded_queue_test)
    sdr_core_apply_sanitizers(sdr_core_bounded_queue_test)
    add_test(NAME sdr_core.bounded_queue COMMAND sdr_core_bounded_queue_test)
    set_tests_properties(sdr_core.bounded_queue PROPERTIES TIMEOUT 120)
    add_executable(sdr_core_buffer_pool_test tests/test_buffer_pool.cpp)
    target_link_libraries(sdr_core_buffer_pool_test PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_buffer_pool_test)
    sdr_core_apply_sanitizers(sdr_core_buffer_pool_test)
    add_test(NAME sdr_core.buffer_pool COMMAND sdr_core_buffer_pool_test)
    set_tests_properties(sdr_core.buffer_pool PROPERTIES TIMEOUT 120)
    add_executable(sdr_core_engine_lifecycle_test tests/test_engine_lifecycle.cpp)
    target_link_libraries(sdr_core_engine_lifecycle_test PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_engine_lifecycle_test)
    sdr_core_apply_sanitizers(sdr_core_engine_lifecycle_test)
    add_test(NAME sdr_core.engine_lifecycle COMMAND sdr_core_engine_lifecycle_test)
    set_tests_properties(sdr_core.engine_lifecycle PROPERTIES TIMEOUT 180)
    add_executable(sdr_core_transport_stress_test tests/test_transport_stress.cpp)
    target_link_libraries(sdr_core_transport_stress_test PRIVATE sdr_core::sdr_core)
    if(WIN32)
        target_link_libraries(sdr_core_transport_stress_test PRIVATE psapi)
    endif()
    sdr_core_apply_platform_defaults(sdr_core_transport_stress_test)
    sdr_core_apply_sanitizers(sdr_core_transport_stress_test)
    add_test(NAME sdr_core.transport_stress COMMAND sdr_core_transport_stress_test)
    set_tests_properties(sdr_core.transport_stress PROPERTIES TIMEOUT 600)
    add_executable(sdr_core_window_test tests/test_window.cpp)
    target_link_libraries(sdr_core_window_test PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_window_test)
    sdr_core_apply_sanitizers(sdr_core_window_test)
    add_test(NAME sdr_core.window COMMAND sdr_core_window_test)
    set_tests_properties(sdr_core.window PROPERTIES TIMEOUT 120)
    add_executable(sdr_core_cpu_dsp_backend_test tests/test_cpu_dsp_backend.cpp)
    target_link_libraries(sdr_core_cpu_dsp_backend_test PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_cpu_dsp_backend_test)
    sdr_core_apply_sanitizers(sdr_core_cpu_dsp_backend_test)
    add_test(NAME sdr_core.cpu_dsp_backend COMMAND sdr_core_cpu_dsp_backend_test)
    set_tests_properties(sdr_core.cpu_dsp_backend PROPERTIES TIMEOUT 300)
    add_executable(sdr_core_dsp_bench tests/bench_cpu_dsp.cpp)
    target_link_libraries(sdr_core_dsp_bench PRIVATE sdr_core::sdr_core)
    sdr_core_apply_platform_defaults(sdr_core_dsp_bench)
    sdr_core_apply_sanitizers(sdr_core_dsp_bench)
    add_test(NAME sdr_core.dsp_bench COMMAND sdr_core_dsp_bench)
    set_tests_properties(sdr_core.dsp_bench PROPERTIES TIMEOUT 600)

    if(SDR_CORE_ENABLE_CUDA)
        add_executable(sdr_core_cuda_dsp_backend_test tests/test_cuda_dsp_backend.cpp)
        target_link_libraries(sdr_core_cuda_dsp_backend_test PRIVATE sdr_core::sdr_cuda)
        sdr_core_apply_platform_defaults(sdr_core_cuda_dsp_backend_test)
        sdr_core_apply_sanitizers(sdr_core_cuda_dsp_backend_test)
        add_test(NAME sdr_core.cuda_dsp_backend COMMAND sdr_core_cuda_dsp_backend_test)
        set_tests_properties(sdr_core.cuda_dsp_backend PROPERTIES TIMEOUT 600)
        add_executable(sdr_core_dsp_backend_selector_test tests/test_dsp_backend_selector.cpp)
        target_link_libraries(sdr_core_dsp_backend_selector_test PRIVATE sdr_core::sdr_cuda)
        sdr_core_apply_platform_defaults(sdr_core_dsp_backend_selector_test)
        sdr_core_apply_sanitizers(sdr_core_dsp_backend_selector_test)
        add_test(NAME sdr_core.dsp_backend_selector COMMAND sdr_core_dsp_backend_selector_test)
        set_tests_properties(sdr_core.dsp_backend_selector PROPERTIES TIMEOUT 300)
        add_executable(sdr_core_cuda_bench tests/bench_cuda_dsp.cpp)
        target_link_libraries(sdr_core_cuda_bench PRIVATE sdr_core::sdr_cuda)
        sdr_core_apply_platform_defaults(sdr_core_cuda_bench)
        sdr_core_apply_sanitizers(sdr_core_cuda_bench)
        add_test(NAME sdr_core.cuda_bench COMMAND sdr_core_cuda_bench)
        set_tests_properties(sdr_core.cuda_bench PROPERTIES TIMEOUT 900)
    endif()

    if(SDR_CORE_ENABLE_PLUTO)
        add_library(sdr_core_mock_libiio SHARED tests/mock_libiio.cpp)
        set_target_properties(sdr_core_mock_libiio PROPERTIES OUTPUT_NAME "libiio")
        sdr_core_apply_platform_defaults(sdr_core_mock_libiio)
        add_executable(sdr_core_pluto_backend_test tests/test_pluto_backend.cpp)
        target_link_libraries(sdr_core_pluto_backend_test PRIVATE sdr_pluto sdr_core::sdr_core)
        if(SDR_CORE_ENABLE_CUDA)
            target_link_libraries(sdr_core_pluto_backend_test PRIVATE sdr_core::sdr_cuda)
        endif()
        sdr_core_apply_platform_defaults(sdr_core_pluto_backend_test)
        add_dependencies(sdr_core_pluto_backend_test sdr_core_mock_libiio)
        add_test(NAME sdr_core.pluto_backend COMMAND sdr_core_pluto_backend_test)
        set_tests_properties(sdr_core.pluto_backend PROPERTIES
            TIMEOUT 120
            ENVIRONMENT "LIBIIO_DLL_PATH=$<TARGET_FILE:sdr_core_mock_libiio>"
        )
        add_executable(sdr_core_fixed_band_engine_test tests/test_fixed_band_engine.cpp)
        target_link_libraries(sdr_core_fixed_band_engine_test PRIVATE sdr_pluto sdr_core::sdr_core)
        if(SDR_CORE_ENABLE_CUDA)
            target_link_libraries(sdr_core_fixed_band_engine_test PRIVATE sdr_core::sdr_cuda)
        endif()
        sdr_core_apply_platform_defaults(sdr_core_fixed_band_engine_test)
        add_dependencies(sdr_core_fixed_band_engine_test sdr_core_mock_libiio)
        add_test(NAME sdr_core.fixed_band_engine COMMAND sdr_core_fixed_band_engine_test)
        set_tests_properties(sdr_core.fixed_band_engine PROPERTIES
            TIMEOUT 180
            ENVIRONMENT "LIBIIO_DLL_PATH=$<TARGET_FILE:sdr_core_mock_libiio>;SDR_MOCK_LIBIIO_REFILL_DELAY_MS=1"
        )
    endif()
endif()

install(TARGETS sdr_core
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
    RUNTIME DESTINATION bin
)
install(DIRECTORY include/ DESTINATION include)
