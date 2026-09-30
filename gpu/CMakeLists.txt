cmake_minimum_required(VERSION 3.18)
project(gpu_health LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# NVML ships with the driver (libnvidia-ml.so.1); the header and a link stub
# ship with the CUDA toolkit. Linking against the stub lets CI build without a GPU.
find_package(CUDAToolkit REQUIRED)

add_executable(gpu-health src/main.cpp src/checks.cpp)
target_include_directories(gpu-health PRIVATE src)
target_link_libraries(gpu-health PRIVATE CUDA::nvml)
target_compile_options(gpu-health PRIVATE -Wall -Wextra -Wpedantic)
