# Test strategy

**English** · [繁體中文](../zh-TW/test-strategy.md) · [← README](../../README.md)

## Levels

| Level | Where it runs | Gate |
|---|---|---|
| **Unit** (35) | every push, GitHub Actions, no hardware | merge |
| **Smoke** (8, `-m smoke`) | right after boot | the full functional run starts only if smoke passes |
| **Functional** (71 total) | QEMU nightly; hardware via profile | build goes *unstable* on failure |
| **Destructive** (11, `--run-destructive`) | QEMU by default (safe, because of the golden-image copy); opt-in on hardware | same |
| **Security gate** | every build | build *fails* on new vulnerabilities at or above policy |

## Coverage matrix

| Area | File | Tests | What is verified |
|---|---|---|---|
| Service root | `test_service_root.py` | 9 | Public root per DSP0266, version format, required links resolve, `$metadata`, no dangling `@odata.id` one level down, manager reports firmware version |
| Auth & sessions | `test_auth.py` | 12 | 401 on protected resources without auth or with a forged token, wrong password, **session token dead immediately after logout**, bad credentials do not create a session, session timeout bounded |
| Malformed input | `test_negative_input.py` | 11 | Malformed JSON, unknown property, wrong type, out-of-range value **not partially applied**, invalid ResetType, 405, path traversal (`../`, `%2e%2e`), 10 MiB body, wrong Content-Type |
| Sensors | `test_sensors.py` | 6 | Discovered dynamically, minimum count per profile, unique IDs, numeric readings with valid units, threshold ordering, nothing beyond critical at idle, no Critical health |
| Power | `test_power.py` | 5 | Power state reported, ResetType discovery via inline values or ActionInfo; *(host_power)* ForceOff→On, idempotent On, **transition leaves an event-log entry** |
| Accounts & RBAC | `test_accounts.py` | 8 | Password policy, duplicate user, ReadOnly can read, **ReadOnly cannot write or create an Administrator**, **cannot escalate its own role**, **a changed password takes effect and the old one is dead**, ReadOnly cannot change another user's password, deleted account cannot log in |
| Firmware | `test_firmware.py` | 11 | Active image in inventory, Manager version matches it, pinned expected version; bad images (random, empty, truncated) are tested twice: **never applied, BMC alive** (safety) and **answered with 4xx or a failed Task** (protocol); positive update with a real signed tarball |
| IPMI | `test_ipmi.py` | 9 | `mc info`, selftest, chassis status, SDR, SEL, raw Get Device ID, invalid command returns a completion code and **ipmid survives**, wrong password, **cipher suite 0 disabled** |

## How the negative tests assert

A negative test that checks only `status == 400` misses the bugs that matter. Each case in `test_negative_input.py` checks three things:

1. **The right status** (400 / 404 / 405 / 413).
2. **A spec-compliant error body**: a Redfish `error` object with `@Message.ExtendedInfo`, and where the spec defines one, the exact `MessageId` (`MalformedJSON`, `PropertyUnknown`, `PropertyValueTypeError`).
3. **The BMC is still healthy afterwards.** An autouse fixture requests the service root after each test. A 400 that leaves bmcweb crashing in a loop is still a failure.

The firmware update tests use the same idea. A rejected image is only a pass if the **running firmware version is unchanged** and the BMC still answers. If the push is accepted asynchronously (202 + Task), the Task must end in `Exception`, `Killed` or `Cancelled`.

## Results from live runs

See [findings.md](findings.md): romulus #1752 to #1754 and gb200nvl-obmc #1754, with every failure traced to one of three root causes.

## Isolation

- Accounts are created through the `temp_account` factory fixture, which always deletes them. Usernames are random, so parallel runs cannot collide.
- Destructive tests are skipped unless `--run-destructive` is passed. On QEMU they are safe because of the golden-image copy (see [architecture D2](architecture.md#d2-every-boot-starts-from-a-copy-of-the-golden-image)).

## Known gaps (next work)

- **DMTF Redfish Service Validator**: schema conformance of every resource. This is the natural next Jenkins stage.
- **Event subscriptions / SSE**: `EventService` delivery after power and sensor events.
- **Fuzzing**: grammar-based fuzzing of Redfish PATCH bodies and IPMI raw commands, with the same "still alive" oracle.
- **In-band IPMI (KCS)** and **host power** need a hardware profile.
- **Performance**: Redfish latency percentiles under concurrent sessions, tracked in the trend dashboard.
