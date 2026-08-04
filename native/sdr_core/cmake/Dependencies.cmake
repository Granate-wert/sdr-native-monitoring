if(SDR_CORE_BUILD_PYTHON)
    find_package(Python 3.13 REQUIRED COMPONENTS Interpreter Development.Module)
    find_package(pybind11 2.13 CONFIG REQUIRED)
endif()
