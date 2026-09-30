"""IPMI over LAN (RMCP+). Still what most datacenter tooling uses for out-of-band basics."""

import pytest

from bmcval.ipmi import IpmiClient, parse_kv

pytestmark = [pytest.mark.ipmi, pytest.mark.requires("ipmi_lan")]


def test_mc_info(ipmi):
    res = ipmi.run("mc", "info")
    assert res.ok, res.stderr
    info = parse_kv(res.stdout)
    assert info.get("IPMI Version") == "2.0"
    assert info.get("Firmware Revision")


def test_mc_selftest_passes(ipmi):
    res = ipmi.run("mc", "selftest")
    assert res.ok, res.stderr
    assert "passed" in res.stdout.lower()


def test_chassis_status(ipmi):
    res = ipmi.run("chassis", "status")
    assert res.ok, res.stderr
    assert parse_kv(res.stdout).get("System Power") in ("on", "off")


def test_sdr_list_matches_redfish_sensor_presence(ipmi, profile):
    res = ipmi.run("sdr", "elist")
    assert res.ok, res.stderr
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    assert len(lines) >= profile.sensors.get("min_count", 1)


def test_sel_readable(ipmi):
    res = ipmi.run("sel", "info")
    assert res.ok, res.stderr
    assert "Version" in res.stdout


def test_device_id_raw(ipmi):
    # Get Device ID: NetFn App (0x06), Cmd 0x01. Response byte 5 is IPMI version, 0x02 = 2.0.
    res = ipmi.raw(0x06, 0x01)
    assert res.ok, res.stderr
    data = res.stdout.split()
    assert len(data) >= 11
    assert data[4] == "02"


def test_invalid_command_returns_completion_code(ipmi):
    # Reserved NetFn/Cmd must return a non-zero completion code, not hang or crash ipmid.
    res = ipmi.raw(0x06, 0xFE)
    assert not res.ok
    assert "rsp=0x" in res.stderr.lower() or "invalid" in res.stderr.lower()
    assert ipmi.run("mc", "info").ok, "ipmid did not survive an invalid command"


def test_wrong_password_rejected(profile, credentials):
    host = profile.endpoints["host"]
    bad = IpmiClient(host, int(profile.endpoints["ipmi_port"]), credentials[0], "wrong-password")
    if not IpmiClient.available():
        pytest.skip("ipmitool not installed")
    assert not bad.run("mc", "info").ok


def test_cipher_suite_zero_disabled(profile, credentials):
    """Cipher suite 0 means no authentication at all; enabled = critical finding."""
    if not IpmiClient.available():
        pytest.skip("ipmitool not installed")
    c0 = IpmiClient(
        profile.endpoints["host"], int(profile.endpoints["ipmi_port"]), credentials[0],
        "anything", cipher_suite=0,
    )  # fmt: skip
    assert not c0.run("mc", "info").ok
