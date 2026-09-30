"""Fixtures for tests that talk to a live BMC (QEMU or hardware).

The BMC is expected to be up already (``bmcval boot`` in CI, or a lab board).
Keeping boot out of pytest means one boot serves several parallel pytest
invocations, and a boot failure is reported as an infrastructure error in its
own Jenkins stage instead of as hundreds of test errors.
"""

from __future__ import annotations

import os
import secrets
import string

import pytest
import requests

from bmcval.ipmi import IpmiClient
from bmcval.profiles import Profile, load_profile
from bmcval.redfish import RedfishClient


@pytest.fixture(scope="session")
def profile(pytestconfig) -> Profile:
    return load_profile(pytestconfig.getoption("--profile"))


@pytest.fixture(autouse=True)
def _gate_by_profile(request, profile, pytestconfig):
    marker = request.node.get_closest_marker("requires")
    if marker:
        missing = [c for c in marker.args if not profile.has(c)]
        if missing:
            pytest.skip(f"{profile.name} lacks capability: {', '.join(missing)}")
    if request.node.get_closest_marker("destructive") and not pytestconfig.getoption(
        "--run-destructive"
    ):
        pytest.skip("destructive test; pass --run-destructive to enable")


@pytest.fixture(scope="session")
def bmc_url(profile) -> str:
    host = os.environ.get("BMC_HOST", profile.endpoints["host"])
    port = os.environ.get("BMC_HTTPS_PORT", profile.endpoints["https_port"])
    return f"https://{host}:{port}"


@pytest.fixture(scope="session")
def credentials(profile) -> tuple[str, str]:
    return (
        os.environ.get("BMC_USERNAME", profile.credentials["username"]),
        os.environ.get("BMC_PASSWORD", profile.credentials["password"]),
    )


@pytest.fixture(scope="session")
def _reachable(bmc_url):
    try:
        requests.get(f"{bmc_url}/redfish/v1", verify=False, timeout=15)
    except requests.RequestException as exc:
        pytest.exit(f"BMC not reachable at {bmc_url}: {exc}. Run `bmcval boot` first.", 2)


@pytest.fixture(scope="session")
def redfish(bmc_url, credentials, _reachable) -> RedfishClient:
    """Administrator session shared across the whole run."""
    client = RedfishClient(bmc_url, *credentials)
    client.login()
    yield client
    client.logout()


@pytest.fixture
def anon(bmc_url, _reachable) -> RedfishClient:
    """Unauthenticated client for access-control tests."""
    return RedfishClient(bmc_url, username="", password="")


@pytest.fixture(scope="session")
def ipmi(profile, credentials) -> IpmiClient:
    if not IpmiClient.available():
        pytest.skip("ipmitool not installed")
    host = os.environ.get("BMC_HOST", profile.endpoints["host"])
    port = int(os.environ.get("BMC_IPMI_PORT", profile.endpoints["ipmi_port"]))
    return IpmiClient(host, port, *credentials)


@pytest.fixture(scope="session")
def system_uri(redfish) -> str:
    return redfish.get_json("/redfish/v1/Systems")["Members"][0]["@odata.id"]


@pytest.fixture(scope="session")
def manager_uri(redfish) -> str:
    return redfish.get_json("/redfish/v1/Managers")["Members"][0]["@odata.id"]


@pytest.fixture(scope="session")
def chassis_uris(redfish) -> list[str]:
    return [m["@odata.id"] for m in redfish.get_json("/redfish/v1/Chassis")["Members"]]


def strong_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#%^*_-"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(not c.isalnum() for c in pw)
        ):
            return pw


@pytest.fixture
def temp_account(redfish):
    """Factory creating Redfish accounts that are always deleted afterwards."""
    created: list[str] = []

    def make(role: str = "ReadOnly") -> tuple[str, str]:
        username = f"t{secrets.token_hex(4)}"
        password = strong_password()
        resp = redfish.post(
            "/redfish/v1/AccountService/Accounts",
            json={"UserName": username, "Password": password, "RoleId": role, "Enabled": True},
        )
        assert resp.status_code in (200, 201), f"account create failed: {resp.text}"
        created.append(username)
        return username, password

    yield make
    for username in created:
        redfish.delete(f"/redfish/v1/AccountService/Accounts/{username}")
