// gpu-health: point-in-time GPU node health check over NVML.
//
//   gpu-health [--json FILE] [--watch-xid SECONDS] [--max-temp C]
//
// Exit codes: 0 all pass/warn, 1 at least one check failed, 2 NVML unusable.
// --watch-xid additionally listens for critical Xid events for N seconds,
// which is how a burn-in wrapper catches errors that occur *under load*.

#include <nvml.h>

#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "checks.hpp"

using namespace gpuhealth;

namespace {

std::string json_escape(const std::string& s) {
    std::string out;
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out;
}

std::string to_json(const std::string& driver, const std::string& nvml,
                    const std::vector<GpuReport>& gpus) {
    std::ostringstream o;
    o << "{\n  \"driver_version\": \"" << json_escape(driver) << "\",\n"
      << "  \"nvml_version\": \"" << json_escape(nvml) << "\",\n  \"gpus\": [";
    for (size_t i = 0; i < gpus.size(); ++i) {
        const auto& g = gpus[i];
        o << (i ? "," : "") << "\n    {\"index\": " << g.index << ", \"name\": \""
          << json_escape(g.name) << "\", \"uuid\": \"" << json_escape(g.uuid)
          << "\", \"pci_bus_id\": \"" << json_escape(g.pci_bus_id) << "\", \"serial\": \""
          << json_escape(g.serial) << "\", \"vbios\": \"" << json_escape(g.vbios)
          << "\",\n     \"checks\": [";
        for (size_t j = 0; j < g.checks.size(); ++j) {
            const auto& c = g.checks[j];
            o << (j ? ", " : "") << "\n       {\"name\": \"" << json_escape(c.name)
              << "\", \"status\": \"" << to_string(c.status) << "\", \"message\": \""
              << json_escape(c.message) << "\"}";
        }
        o << "]}";
    }
    o << "\n  ]\n}\n";
    return o.str();
}

// Returns the number of critical Xid events seen, appending them as checks.
int watch_xid(std::vector<nvmlDevice_t>& devs, std::vector<GpuReport>& reports, unsigned seconds) {
    nvmlEventSet_t set;
    if (nvmlEventSetCreate(&set) != NVML_SUCCESS) return 0;
    for (auto d : devs) nvmlDeviceRegisterEvents(d, nvmlEventTypeXidCriticalError, set);
    int seen = 0;
    for (unsigned waited = 0; waited < seconds; ++waited) {
        nvmlEventData_t ev{};
        if (nvmlEventSetWait_v2(set, &ev, 1000) != NVML_SUCCESS) continue;
        for (size_t i = 0; i < devs.size(); ++i) {
            if (devs[i] == ev.device) {
                reports[i].checks.push_back(
                    {"xid_event", Status::Fail, "Xid " + std::to_string(ev.eventData)});
                ++seen;
            }
        }
    }
    nvmlEventSetFree(set);
    return seen;
}

}  // namespace

int main(int argc, char** argv) {
    std::string json_path;
    unsigned watch_seconds = 0;
    Thresholds th;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--json") && i + 1 < argc) json_path = argv[++i];
        else if (!std::strcmp(argv[i], "--watch-xid") && i + 1 < argc) watch_seconds = std::stoul(argv[++i]);
        else if (!std::strcmp(argv[i], "--max-temp") && i + 1 < argc) th.max_temp_c = std::stoul(argv[++i]);
        else {
            std::cerr << "usage: gpu-health [--json FILE] [--watch-xid SECONDS] [--max-temp C]\n";
            return 2;
        }
    }

    nvmlReturn_t rc = nvmlInit_v2();
    if (rc != NVML_SUCCESS) {
        std::cerr << "nvmlInit failed: " << nvmlErrorString(rc) << "\n";
        return 2;
    }

    char driver[NVML_SYSTEM_DRIVER_VERSION_BUFFER_SIZE] = {};
    char nvml[NVML_SYSTEM_NVML_VERSION_BUFFER_SIZE] = {};
    nvmlSystemGetDriverVersion(driver, sizeof(driver));
    nvmlSystemGetNVMLVersion(nvml, sizeof(nvml));

    unsigned int count = 0;
    nvmlDeviceGetCount_v2(&count);
    std::vector<nvmlDevice_t> devs;
    std::vector<GpuReport> reports;
    for (unsigned int i = 0; i < count; ++i) {
        nvmlDevice_t dev;
        if (nvmlDeviceGetHandleByIndex_v2(i, &dev) != NVML_SUCCESS) continue;
        devs.push_back(dev);
        reports.push_back(inspect(i, dev, th));
    }
    if (watch_seconds) watch_xid(devs, reports, watch_seconds);

    bool failed = count == 0;
    for (const auto& g : reports) {
        std::cout << "GPU" << g.index << " " << g.name << " [" << g.pci_bus_id << "]\n";
        for (const auto& c : g.checks) {
            std::cout << "  " << to_string(c.status) << "  " << c.name << ": " << c.message << "\n";
            failed |= c.status == Status::Fail;
        }
    }
    if (!json_path.empty()) std::ofstream(json_path) << to_json(driver, nvml, reports);

    nvmlShutdown();
    return failed ? 1 : 0;
}
