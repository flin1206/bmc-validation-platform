# GPU node diagnostics

**English** · [繁體中文](../zh-TW/gpu-diagnostics.md) · [← README](../../README.md)

The BMC stage asks "is the management firmware correct?". This stage asks "is the machine it manages healthy?". Three tools answer at different depths:

| Tool | Depth | Cost |
|---|---|---|
| `gpu-health` (C++/NVML) | Point-in-time health: seconds, safe on a production node | ~1 s |
| `bmcval xid` | What the driver has already complained about | log parse |
| `dcgmi diag -r 2/3` | Active stress: memory, PCIe bandwidth, targeted power | minutes, node must be idle |

## `gpu-health`

C++17, built with CMake against `CUDA::nvml`. CI builds it inside `nvidia/cuda:*-devel` and links against the NVML stub, so no GPU is needed to compile it.

| Check | Fail | Warn | Why it matters |
|---|---|---|---|
| `temperature` | > 85 °C | | sustained heat shortens life and triggers slowdown |
| `pcie_width` | current < max | | a GPU that trained at x8 instead of x16 is badly seated or has a riser/cable fault, and loses half its host bandwidth |
| `pcie_gen` | | current < max | **only a warning**: links drop speed at idle for power saving (ASPM), which is normal. Confirm under load with DCGM |
| `ecc_uncorrected` | > 0 volatile | | data corruption already happened |
| `ecc_corrected` | | > 1000 volatile | early sign of failing memory |
| `row_remap` | remap **failure** | remap pending | Ampere+: failure means the bank is out of spare rows, so pull the board; pending needs a GPU reset |
| `retired_pages_pending` | | pending | pre-Ampere equivalent |
| `clock_events` | HW slowdown, HW thermal, power brake | SW thermal, SW power cap | hardware slowdown means a platform problem (cooling, PSU); software caps are policy |
| `power_limit` | | enforced < 95% of default | a board capped below spec will fail performance acceptance |
| `xid_event` (`--watch-xid N`) | any critical Xid while watching | | catches errors *under load* when run next to a burn-in |

Checks the device or driver does not support are reported as **skip with the NVML reason**, never dropped. Exit codes: `0` healthy, `1` a check failed, `2` NVML unusable. A broken driver is therefore not confused with a broken GPU.

**A portability detail found by compiling against the real header:** CUDA 12.2+ renamed only the *software* clock reasons to `nvmlClocksEvent*`, while the hardware slowdown constants remain `nvmlClocksThrottleReason*`. The code uses the `Throttle` spellings, which exist in every header version.

## Xid triage

The driver logs `NVRM: Xid (PCI:0000:3b:00): 79, pid=..., GPU has fallen off the bus.`. The number alone does not tell an on-call engineer what to do, so `bmcval xid` maps each Xid to its likely origin and an action:

| Action | Example Xids | Meaning |
|---|---|---|
| `NONE` | 45, 63 | follow-on or informational |
| `CHECK_APP` | 13, 31, 43 | usually the workload (bad kernel, illegal address) |
| `RESET_GPU` | 48, 92, 94, 119, 120 | ECC events, GSP errors: recoverable with a reset |
| `DRAIN_NODE` | 64, 74, 79, 95 | row-remap failure, NVLink, off the bus, uncontained ECC: pull the node |

The parser handles both the older format (no `pid=`) and the newer one (`pid=`, `name=`). It normalises PCI addresses and takes the worst action **per GPU**. The suite property `node_action` is the one field a scheduler would read.

## DCGM

`dcgmi diag -j` output has changed layout across DCGM major versions. Instead of binding to one schema, the parser walks the JSON for any object with a test name and a status. Unit tests pin that behaviour with a nested fixture.

## Limits

- DCGM is **not supported on WSL2**, and WSL2 exposes no real kernel log, so the Xid path cannot be exercised there. That is why the stage runs only on agents labelled `gpu` and is off by default.
