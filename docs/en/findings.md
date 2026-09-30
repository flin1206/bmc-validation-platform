# Findings from live runs

**English** · [繁體中文](../zh-TW/findings.md) · [← README](../../README.md)

Every item below came out of running this repository's test suite against **upstream OpenBMC reference builds** booted in QEMU 11.1.2. Each one is written the way it would be filed upstream: what happens, how to reproduce it, the evidence, and what is still unknown.

> **Scope note.** These are the OpenBMC project's own nightly reference images for each machine (`jenkins.openbmc.org/job/latest-master`). They are **not** any vendor's production firmware. In particular, the gb200nvl-obmc findings say nothing about NVIDIA's shipping BMC firmware.

## Summary

| # | Machine | Builds | What breaks | Severity | Tests that caught it |
|---|---|---|---|---|---|
| F1 | romulus | #1752, #1753, #1754 | BMC firmware cannot be updated over Redfish; Manager doesn't report its running version | High | 6 |
| F2 | gb200nvl-obmc | #1754 | No password can be set: accounts can't be created and the **default root password can't be changed** | High (security) | 7 |
| F3 | gb200nvl-obmc | #1754 | A malformed firmware upload returns 500 instead of a client error | Low | 2 |

## Results per run

| Machine | Build | Passed | Failed | Skipped | Failures explained by |
|---|---|---|---|---|---|
| romulus | #1752 | 50 | 6 | 11 | F1 |
| romulus | #1753 | 50 | 6 | 11 | F1 |
| romulus | #1754 | 54 | 6 | 11 | F1 |
| gb200nvl-obmc | #1754 | 40 | 9 | 22 | F2 (7), F3 (2) |

The #1754 romulus run includes four tests that were added later (the safety/protocol split and the password-change test). Every failure maps to one of three root causes. Skips are features the emulated machine doesn't have (no host CPU, no sensor hardware, no FRU EEPROM, no IPMI on gb200nvl). Each skip is declared in the [profile](../../src/bmcval/data) with the observation that justifies it.

---

## F1 · romulus: firmware update path is broken

**What happens**

- `GET /redfish/v1/Managers/bmc` has no `FirmwareVersion` and no `Links.ActiveSoftwareImage`.
- The inventory does list the image (`FirmwareInventory/07d40fbb`, `Version: 3.1.0-dev-1341-g16d23c4743`), but with `Updateable: false`.
- Every firmware push returns **500 InternalError**. That includes the signed `.static.mtd.tar` from the same build, and both `HttpPushUri` and `MultipartHttpPushUri`.
- The state persists: it is unchanged 5 minutes after boot, so it isn't a startup race.

**Reproduce**

```bash
bmcval fetch --machine romulus --build 1754
bmcval boot --profile qemu-romulus --image-dir build/images/romulus/1754
curl -sk -u root:0penBmc https://127.0.0.1:2443/redfish/v1/Managers/bmc | jq .FirmwareVersion   # null
```

**Evidence (BMC journal, read through Redfish `LogServices/Journal`)**

```
mapperx: Found invalid association on path /xyz/openbmc_project/software/07d40fbb
phosphor-image-updater: Error in mapper GetSubTreePath: ... ResourceNotFound
openpower-update-manager: Error version is empty
bmcweb: [update_service.hpp:971] Found 0 MultipartUpdate objects, expected exactly 1
bmcweb: [update_service.hpp:860] error_code = Invalid request descriptor
```

**Why it matters:** a BMC that can't be updated over its management API has to be reflashed by hand.

**Control:** the gb200nvl-obmc image built from the **same OpenBMC revision** reports `FirmwareVersion` and `ActiveSoftwareImage` correctly. The defect is therefore specific to the romulus machine configuration, not to bmcweb in general.

**Hypothesis (not yet confirmed):** romulus is an OpenPOWER machine and also runs `openpower-update-manager` for the host firmware. Its failure at startup, together with the invalid association on the BMC software object, suggests the two update managers conflict. QEMU may also contribute, for example through the emulated flash layout. This needs to be confirmed on hardware or with upstream.

---

## F2 · gb200nvl-obmc: PAM references modules the image doesn't ship

**What happens**

- `POST /AccountService/Accounts` with a password that satisfies the advertised policy returns (five tested, 12 to 14 characters, `MinPasswordLength` 8 / `MaxPasswordLength` 20) **400 PropertyValueFormatError**. The user is created and then rolled back (`userdel` appears in the journal).
- `PATCH /AccountService/Accounts/root {"Password": ...}` returns **500**. Afterwards the default password `0penBmc` **still works** and the new one does not.

**Root cause, confirmed statically and at runtime**

```
# rootfs /etc/pam.d/common-password
17: password [success=ok default=die]  pam_ipmicheck.so spec_grp_name=ipmi use_authtok
20: password [success=1  default=die]  pam_ipmisave.so  spec_grp_name=ipmi ...
```

- `pam_ipmicheck.so` and `pam_ipmisave.so` are **not present** in `/usr/lib/security/`, and `pam-ipmi` is **not in the image manifest**.
- Because the lines use `default=die`, a module that fails to load aborts every password change.
- The journal confirms it: `PAM unable to dlopen(/usr/lib/security/pam_ipmicheck.so)` followed by `pamUpdatePassword Failed`.

**Control:** romulus ships `pam-ipmi`, has both modules, and passes `test_password_change_takes_effect`.

**Why it matters:** the well-known default credential can't be rotated through Redfish on this build, and no other user can be created.

**Likely fix:** either include `pam-ipmi` in the image, or leave the IPMI PAM lines out when the image is built without IPMI-over-LAN (this image has no `phosphor-ipmi-net`, which also explains why RMCP+ is unavailable).

---

## F3 · gb200nvl-obmc: malformed firmware upload returns 500

Pushing random bytes or a truncated tar returns **500 InternalError**. The journal shows `pldmd: No devices discovered, cannot process the PLDM fw update package` and `bmcweb: error_code = Input/output error`.

**Safety held:** the separate `test_corrupt_image_never_applied` tests pass. The running firmware is unchanged and the BMC stays responsive. Only the error class is wrong: a bad upload is the client's fault and should get a 4xx or a failed Task.

---

## How the suite kept these findings clean

- **Safety and protocol are separate tests.** "The bad image wasn't applied" and "the right error code came back" fail independently, so F3 reads as low severity instead of looking like a bricking risk.
- **Each defect fails a small, specific set of tests.** The firmware inventory is located through `RelatedItem` and not through the Manager's links, so F1 doesn't cascade into unrelated failures.
- **Test bugs were fixed, not waived.** The first live run failed `test_readonly_user_cannot_escalate_own_role`, because bmcweb refuses with 400 where the test expected 403. The test was rewritten to assert the actual security property (the role is unchanged), and the escalation is still refused.
- **Environment limits are recorded as profile data**, each with the observation behind it, so they don't pile up as permanent red tests.
