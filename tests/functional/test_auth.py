"""Authentication and session handling: the BMC is a remote root shell on the server, so these are security tests."""

import pytest

from bmcval.redfish import RedfishClient

pytestmark = [pytest.mark.negative, pytest.mark.requires("redfish_sessions")]

PROTECTED = [
    "/redfish/v1/Systems",
    "/redfish/v1/Managers",
    "/redfish/v1/AccountService/Accounts",
    "/redfish/v1/UpdateService",
]


@pytest.mark.parametrize("path", PROTECTED)
def test_protected_resources_require_auth(anon, path):
    assert anon.get(path).status_code == 401


@pytest.mark.parametrize("path", PROTECTED)
def test_invalid_token_is_rejected(anon, path):
    resp = anon.get(path, headers={"X-Auth-Token": "0" * 20})
    assert resp.status_code == 401


def test_wrong_password_rejected(bmc_url, credentials):
    client = RedfishClient(bmc_url, credentials[0], credentials[1] + "x")
    client.use_basic_auth()
    assert client.get("/redfish/v1/Systems").status_code == 401


def test_session_lifecycle(bmc_url, credentials):
    client = RedfishClient(bmc_url, *credentials)
    client.login()
    token = client._token
    assert client.get("/redfish/v1/Systems").status_code == 200

    client.logout()
    # A deleted session's token must be dead immediately, not at timeout.
    replay = RedfishClient(bmc_url, "", "")
    assert replay.get("/redfish/v1/Systems", headers={"X-Auth-Token": token}).status_code == 401


def test_session_not_created_for_bad_credentials(anon):
    resp = anon.post(
        "/redfish/v1/SessionService/Sessions",
        json={"UserName": "root", "Password": "definitely-wrong"},
    )
    assert resp.status_code == 401
    assert "X-Auth-Token" not in resp.headers


def test_session_service_timeout_is_bounded(redfish):
    svc = redfish.get_json("/redfish/v1/SessionService")
    assert svc["ServiceEnabled"] is True
    # Idle admin sessions that never expire are a finding in any BMC security review.
    assert 30 <= svc["SessionTimeout"] <= 86400
