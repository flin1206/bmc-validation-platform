from pathlib import Path

import pytest

from bmcval.profiles import available_profiles, load_profile
from bmcval.qemu import QemuBmc


def test_bundled_profiles_load():
    names = available_profiles()
    assert "qemu-romulus" in names
    for name in names:
        profile = load_profile(name)
        assert profile.name == name
        assert profile.has("redfish")


def test_unknown_profile_key_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\ncapabilites: [redfish]\n")  # typo must not be silently ignored
    with pytest.raises(ValueError, match="capabilites"):
        load_profile(str(bad))


def test_unknown_profile_name():
    with pytest.raises(ValueError, match="available"):
        load_profile("no-such-board")


def test_qemu_command_uses_working_copy_and_loopback_forwards(tmp_path):
    bmc = QemuBmc(load_profile("qemu-romulus"), Path("golden.mtd"), tmp_path)
    cmd = bmc.command()
    assert cmd[:3] == ["qemu-system-arm", "-M", "romulus-bmc"]
    drive = cmd[cmd.index("-drive") + 1]
    assert str(tmp_path / "flash.mtd") in drive and "golden" not in drive
    netdev = cmd[-1]
    # Forwards bind to loopback only: an emulated BMC with default creds must not face the LAN.
    assert "hostfwd=tcp:127.0.0.1:2443-:443" in netdev
    assert "hostfwd=udp:127.0.0.1:2623-:623" in netdev


def test_stop_without_pidfile_is_noop(tmp_path):
    QemuBmc(load_profile("qemu-romulus"), Path("x"), tmp_path).stop()
