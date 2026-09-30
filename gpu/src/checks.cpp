#include "checks.hpp"

#include <sstream>

namespace gpuhealth {
namespace {

std::string err(nvmlReturn_t rc) { return nvmlErrorString(rc); }

bool unsupported(nvmlReturn_t rc) {
    return rc == NVML_ERROR_NOT_SUPPORTED || rc == NVML_ERROR_FUNCTION_NOT_FOUND;
}

void add(GpuReport& r, std::string name, Status s, std::string msg) {
    r.checks.push_back({std::move(name), s, std::move(msg)});
}

void skip_or_fail(GpuReport& r, const std::string& name, nvmlReturn_t rc) {
    add(r, name, unsupported(rc) ? Status::Skip : Status::Fail, "NVML: " + err(rc));
}

void check_temperature(GpuReport& r, nvmlDevice_t dev, const Thresholds& th) {
    unsigned int t = 0;
    nvmlReturn_t rc = nvmlDeviceGetTemperature(dev, NVML_TEMPERATURE_GPU, &t);
    if (rc != NVML_SUCCESS) return skip_or_fail(r, "temperature", rc);
    std::ostringstream m;
    m << t << " C (limit " << th.max_temp_c << " C)";
    add(r, "temperature", t <= th.max_temp_c ? Status::Pass : Status::Fail, m.str());
}

// A GPU that trained its PCIe link at reduced *width* is a hardware/seating
// problem. Reduced *generation* at idle is normal power management (ASPM),
// so generation is only a warning; stress tests confirm it under load.
void check_pcie_link(GpuReport& r, nvmlDevice_t dev) {
    unsigned int cw = 0, mw = 0, cg = 0, mg = 0;
    nvmlReturn_t rc1 = nvmlDeviceGetCurrPcieLinkWidth(dev, &cw);
    nvmlReturn_t rc2 = nvmlDeviceGetMaxPcieLinkWidth(dev, &mw);
    if (rc1 != NVML_SUCCESS || rc2 != NVML_SUCCESS)
        return skip_or_fail(r, "pcie_width", rc1 != NVML_SUCCESS ? rc1 : rc2);
    std::ostringstream w;
    w << "x" << cw << " of x" << mw;
    add(r, "pcie_width", cw == mw ? Status::Pass : Status::Fail, w.str());

    rc1 = nvmlDeviceGetCurrPcieLinkGeneration(dev, &cg);
    rc2 = nvmlDeviceGetMaxPcieLinkGeneration(dev, &mg);
    if (rc1 != NVML_SUCCESS || rc2 != NVML_SUCCESS)
        return skip_or_fail(r, "pcie_gen", rc1 != NVML_SUCCESS ? rc1 : rc2);
    std::ostringstream g;
    g << "Gen" << cg << " of Gen" << mg << (cg < mg ? " (may be idle downshift)" : "");
    add(r, "pcie_gen", cg == mg ? Status::Pass : Status::Warn, g.str());
}

void check_ecc(GpuReport& r, nvmlDevice_t dev, const Thresholds& th) {
    nvmlEnableState_t cur, pend;
    nvmlReturn_t rc = nvmlDeviceGetEccMode(dev, &cur, &pend);
    if (rc != NVML_SUCCESS) return skip_or_fail(r, "ecc_mode", rc);
    if (cur != NVML_FEATURE_ENABLED) {
        add(r, "ecc_mode", Status::Warn, "ECC disabled");
        return;
    }
    add(r, "ecc_mode", Status::Pass, pend == cur ? "enabled" : "enabled (change pending reboot)");

    unsigned long long unc = 0, corr = 0;
    rc = nvmlDeviceGetTotalEccErrors(dev, NVML_MEMORY_ERROR_TYPE_UNCORRECTED, NVML_VOLATILE_ECC, &unc);
    if (rc != NVML_SUCCESS) return skip_or_fail(r, "ecc_uncorrected", rc);
    add(r, "ecc_uncorrected", unc <= th.max_uncorrected_ecc ? Status::Pass : Status::Fail,
        std::to_string(unc) + " volatile uncorrected");

    rc = nvmlDeviceGetTotalEccErrors(dev, NVML_MEMORY_ERROR_TYPE_CORRECTED, NVML_VOLATILE_ECC, &corr);
    if (rc != NVML_SUCCESS) return skip_or_fail(r, "ecc_corrected", rc);
    add(r, "ecc_corrected", corr <= th.max_corrected_ecc_warn ? Status::Pass : Status::Warn,
        std::to_string(corr) + " volatile corrected");
}

// Ampere and later replace page retirement with row remapping. A pending remap
// needs a GPU reset to take effect; a remap *failure* means the bank is out of
// spare rows and the board should be pulled.
void check_row_remap(GpuReport& r, nvmlDevice_t dev) {
    unsigned int corr = 0, unc = 0, pending = 0, failed = 0;
    nvmlReturn_t rc = nvmlDeviceGetRemappedRows(dev, &corr, &unc, &pending, &failed);
    if (rc != NVML_SUCCESS) return skip_or_fail(r, "row_remap", rc);
    std::ostringstream m;
    m << "correctable=" << corr << " uncorrectable=" << unc << " pending=" << pending
      << " failure=" << failed;
    Status s = failed ? Status::Fail : pending ? Status::Warn : Status::Pass;
    add(r, "row_remap", s, m.str());
}

void check_retired_pages(GpuReport& r, nvmlDevice_t dev) {
    nvmlEnableState_t pending;
    nvmlReturn_t rc = nvmlDeviceGetRetiredPagesPendingStatus(dev, &pending);
    if (rc != NVML_SUCCESS) return skip_or_fail(r, "retired_pages_pending", rc);
    add(r, "retired_pages_pending",
        pending == NVML_FEATURE_ENABLED ? Status::Warn : Status::Pass,
        pending == NVML_FEATURE_ENABLED ? "retirement pending; reset GPU" : "none pending");
}

// The *Throttle* names are used deliberately: CUDA 12.2+ renamed only some of
// them to *Event*, while the Throttle spellings exist in every NVML header.
void check_throttle(GpuReport& r, nvmlDevice_t dev) {
    unsigned long long reasons = 0;
    nvmlReturn_t rc = nvmlDeviceGetCurrentClocksEventReasons(dev, &reasons);
    if (rc != NVML_SUCCESS) return skip_or_fail(r, "clock_events", rc);
    struct { unsigned long long bit; const char* name; bool bad; } table[] = {
        {nvmlClocksThrottleReasonHwSlowdown, "HW slowdown", true},
        {nvmlClocksThrottleReasonHwThermalSlowdown, "HW thermal slowdown", true},
        {nvmlClocksThrottleReasonHwPowerBrakeSlowdown, "HW power brake", true},
        {nvmlClocksThrottleReasonSwThermalSlowdown, "SW thermal slowdown", false},
        {nvmlClocksThrottleReasonSwPowerCap, "SW power cap", false},
    };
    std::string msg;
    Status s = Status::Pass;
    for (const auto& e : table) {
        if (reasons & e.bit) {
            msg += (msg.empty() ? "" : ", ") + std::string(e.name);
            if (e.bad) s = Status::Fail;
            else if (s == Status::Pass) s = Status::Warn;
        }
    }
    add(r, "clock_events", s, msg.empty() ? "none" : msg);
}

void check_power_limit(GpuReport& r, nvmlDevice_t dev, const Thresholds& th) {
    unsigned int enforced = 0, def = 0;
    nvmlReturn_t rc1 = nvmlDeviceGetEnforcedPowerLimit(dev, &enforced);
    nvmlReturn_t rc2 = nvmlDeviceGetPowerManagementDefaultLimit(dev, &def);
    if (rc1 != NVML_SUCCESS || rc2 != NVML_SUCCESS)
        return skip_or_fail(r, "power_limit", rc1 != NVML_SUCCESS ? rc1 : rc2);
    std::ostringstream m;
    m << enforced / 1000 << " W enforced, " << def / 1000 << " W default";
    bool ok = def == 0 || static_cast<double>(enforced) / def >= th.min_power_limit_ratio;
    add(r, "power_limit", ok ? Status::Pass : Status::Warn, m.str());
}

std::string device_string(nvmlReturn_t (*fn)(nvmlDevice_t, char*, unsigned int), nvmlDevice_t dev) {
    char buf[96] = {};
    return fn(dev, buf, sizeof(buf)) == NVML_SUCCESS ? buf : "";
}

}  // namespace

const char* to_string(Status s) {
    switch (s) {
        case Status::Pass: return "pass";
        case Status::Warn: return "warn";
        case Status::Fail: return "fail";
        case Status::Skip: return "skip";
    }
    return "error";
}

GpuReport inspect(unsigned int index, nvmlDevice_t dev, const Thresholds& th) {
    GpuReport r;
    r.index = index;
    r.name = device_string(nvmlDeviceGetName, dev);
    r.uuid = device_string(nvmlDeviceGetUUID, dev);
    r.serial = device_string(nvmlDeviceGetSerial, dev);
    r.vbios = device_string(nvmlDeviceGetVbiosVersion, dev);
    nvmlPciInfo_t pci{};
    if (nvmlDeviceGetPciInfo_v3(dev, &pci) == NVML_SUCCESS) r.pci_bus_id = pci.busId;

    check_temperature(r, dev, th);
    check_pcie_link(r, dev);
    check_ecc(r, dev, th);
    check_row_remap(r, dev);
    check_retired_pages(r, dev);
    check_throttle(r, dev);
    check_power_limit(r, dev, th);
    return r;
}

}  // namespace gpuhealth
