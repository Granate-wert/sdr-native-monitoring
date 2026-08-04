set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(SDR_AARCH64_C_COMPILER "aarch64-linux-gnu-gcc" CACHE FILEPATH "AArch64 C compiler")
set(SDR_AARCH64_CXX_COMPILER "aarch64-linux-gnu-g++" CACHE FILEPATH "AArch64 C++ compiler")
set(CMAKE_C_COMPILER "${SDR_AARCH64_C_COMPILER}")
set(CMAKE_CXX_COMPILER "${SDR_AARCH64_CXX_COMPILER}")

if(DEFINED ENV{SDR_AARCH64_SYSROOT} AND NOT "$ENV{SDR_AARCH64_SYSROOT}" STREQUAL "")
    set(CMAKE_SYSROOT "$ENV{SDR_AARCH64_SYSROOT}")
    set(CMAKE_FIND_ROOT_PATH "$ENV{SDR_AARCH64_SYSROOT}")
    set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
    set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
    set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
    set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
endif()

# Optional target-side Python ABI hints.  Keep Python discovery disabled for
# the default cross build; when enabled, point CMake at the Jetson aarch64
# interpreter/sysroot rather than accidentally selecting the host interpreter.
set(SDR_AARCH64_PYTHON_EXECUTABLE "" CACHE FILEPATH "Target aarch64 Python executable")
set(SDR_AARCH64_PYTHON_ROOT "" CACHE PATH "Target aarch64 Python root")
if(SDR_AARCH64_PYTHON_EXECUTABLE)
    set(Python_EXECUTABLE "${SDR_AARCH64_PYTHON_EXECUTABLE}" CACHE FILEPATH "" FORCE)
endif()
if(SDR_AARCH64_PYTHON_ROOT)
    set(Python_ROOT_DIR "${SDR_AARCH64_PYTHON_ROOT}" CACHE PATH "" FORCE)
endif()
