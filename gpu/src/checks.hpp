#pragma once

#include <nvml.h>

#include <string>
#include <vector>

namespace gpuhealth {

enum class Status { Pass, Warn, Fail, Skip };

struct Check {
    std::string name;
    Status status;
    std::string message;
};

struct Thresholds {
    unsigned int max_temp_c = 85;          // sustained GPU core temperature
    unsigned long long max_uncorrected_ecc = 0;
    unsigned long long max_corrected_ecc_warn = 1000;
    double min_power_limit_ratio = 0.95;   // enforced limit vs. default: catches capped boards
};

struct GpuReport {
    unsigned int index = 0;
    std::string name;
    std::string uuid;
    std::string pci_bus_id;
    std::string serial;
    std::string vbios;
    std::vector<Check> checks;
};

// Runs every check against one device. Checks that the device or driver does not
// support are reported as Skip with the NVML reason, never silently dropped.
GpuReport inspect(unsigned int index, nvmlDevice_t dev, const Thresholds& th);

const char* to_string(Status s);

}  // namespace gpuhealth
