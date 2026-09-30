"""Account management and role-based privilege enforcement."""

import pytest

from bmcval.redfish import RedfishClient

pytestmark = [pytest.mark.accounts, pytest.mark.requires("account_service")]


def test_password_policy_rejects_weak_password(redfish):
    svc = redfish.get_json("/redfish/v1/AccountService")
    min_len = svc.get("MinPasswordLength", 8)
    resp = redfish.post(
        "/redfish/v1/AccountService/Accounts",
        json={"UserName": "weakpw", "Password": "a" * (min_len - 1), "RoleId": "ReadOnly"},
    )
    assert resp.status_code == 400
    assert redfish.get("/redfish/v1/AccountService/Accounts/weakpw").status_code == 404


def test_duplicate_username_rejected(redfish, temp_account):
    username, password = temp_account()
    resp = redfish.post(
        "/redfish/v1/AccountService/Accounts",
        json={"UserName": username, "Password": password, "RoleId": "ReadOnly"},
    )
    assert resp.status_code in (400, 409)


def test_readonly_user_can_read(bmc_url, temp_account):
    client = RedfishClient(bmc_url, *temp_account("ReadOnly"))
    client.login()
    try:
        assert client.get("/redfish/v1/Systems").status_code == 200
    finally:
        client.logout()


def test_readonly_user_cannot_write(bmc_url, temp_account, manager_uri):
    client = RedfishClient(bmc_url, *temp_account("ReadOnly"))
    client.login()
    try:
        assert (
            client.patch("/redfish/v1/SessionService", json={"SessionTimeout": 600}).status_code
            == 403
        )
        assert (
            client.post(
                "/redfish/v1/AccountService/Accounts",
                json={
                    "UserName": "escalate",
                    "Password": "Aa1!aaaaaaaa",
                    "RoleId": "Administrator",
                },
            ).status_code
            == 403
        )
    finally:
        client.logout()


def test_readonly_user_cannot_escalate_own_role(redfish, bmc_url, temp_account):
    """The security property is "the role did not change", not a specific status code.

    bmcweb answers 400 PropertyUnknown here (a ReadOnly user may only PATCH its own
    Password, so RoleId is not a valid property *for this caller*); other stacks answer
    403. Both are refusals. What must never happen is a 2xx or a changed role.
    """
    username, password = temp_account("ReadOnly")
    client = RedfishClient(bmc_url, username, password)
    client.login()
    try:
        resp = client.patch(
            f"/redfish/v1/AccountService/Accounts/{username}", json={"RoleId": "Administrator"}
        )
        assert resp.status_code in (400, 403), resp.text
    finally:
        client.logout()
    role = redfish.get_json(f"/redfish/v1/AccountService/Accounts/{username}")["RoleId"]
    assert role == "ReadOnly", f"privilege escalation: role is now {role}"


def test_readonly_user_cannot_change_other_password(bmc_url, temp_account, credentials):
    client = RedfishClient(bmc_url, *temp_account("ReadOnly"))
    client.login()
    try:
        resp = client.patch(
            f"/redfish/v1/AccountService/Accounts/{credentials[0]}",
            json={"Password": "Hacked!12345678"},
        )
        assert resp.status_code == 403
    finally:
        client.logout()


@pytest.mark.destructive
def test_password_change_takes_effect(redfish, bmc_url, temp_account, new_password):
    """After a password change the new one works and the old one is dead immediately."""
    username, old = temp_account("ReadOnly")
    new = new_password()
    resp = redfish.patch(f"/redfish/v1/AccountService/Accounts/{username}", json={"Password": new})
    assert resp.status_code in (200, 204), resp.text

    fresh = RedfishClient(bmc_url, username, new)
    fresh.use_basic_auth()
    stale = RedfishClient(bmc_url, username, old)
    stale.use_basic_auth()
    assert fresh.get("/redfish/v1/Systems").status_code == 200
    assert stale.get("/redfish/v1/Systems").status_code == 401


def test_deleted_account_cannot_log_in(redfish, bmc_url, temp_account):
    username, password = temp_account("Operator")
    assert redfish.delete(f"/redfish/v1/AccountService/Accounts/{username}").status_code in (
        200,
        204,
    )
    client = RedfishClient(bmc_url, username, password)
    client.use_basic_auth()
    assert client.get("/redfish/v1/Systems").status_code == 401
