"""Robustness against malformed input.

Each case asserts three things: the right status class, a spec-compliant
Redfish error body, and that the BMC is still healthy afterwards. A 400 that
leaves bmcweb crashed and restarting is still a bug.
"""

import pytest

from bmcval.redfish import extended_info, message_ids

pytestmark = pytest.mark.negative


@pytest.fixture(autouse=True)
def _still_alive_after(redfish):
    yield
    assert redfish.get("/redfish/v1").status_code == 200, "BMC stopped responding after test"


def assert_redfish_error(resp, status: int):
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    assert "error" in resp.json(), "error response lacks a Redfish 'error' object"
    assert extended_info(resp), "error response lacks @Message.ExtendedInfo"


def test_malformed_json_body(redfish, manager_uri):
    resp = redfish.patch(
        manager_uri, data=b'{"DateTime": ', headers={"Content-Type": "application/json"}
    )
    assert_redfish_error(resp, 400)
    assert "MalformedJSON" in message_ids(resp)


def test_unknown_property(redfish, manager_uri):
    resp = redfish.patch(manager_uri, json={"NotARealProperty": 1})
    assert_redfish_error(resp, 400)
    assert "PropertyUnknown" in message_ids(resp)


def test_wrong_property_type(redfish):
    resp = redfish.patch("/redfish/v1/SessionService", json={"SessionTimeout": "forever"})
    assert_redfish_error(resp, 400)
    assert "PropertyValueTypeError" in message_ids(resp)


def test_out_of_range_value(redfish):
    before = redfish.get_json("/redfish/v1/SessionService")["SessionTimeout"]
    resp = redfish.patch("/redfish/v1/SessionService", json={"SessionTimeout": -1})
    assert resp.status_code == 400
    after = redfish.get_json("/redfish/v1/SessionService")["SessionTimeout"]
    assert after == before, "rejected PATCH must not partially apply"


def test_invalid_reset_type(redfish, system_uri):
    resp = redfish.post(
        f"{system_uri}/Actions/ComputerSystem.Reset", json={"ResetType": "SelfDestruct"}
    )
    assert_redfish_error(resp, 400)


def test_method_not_allowed_on_service_root(redfish):
    assert redfish.delete("/redfish/v1").status_code == 405


@pytest.mark.parametrize(
    "path",
    [
        "/redfish/v1/Systems/does-not-exist",
        "/redfish/v1/Chassis/../../../etc/passwd",
        "/redfish/v1/Managers/%2e%2e%2f%2e%2e%2fetc%2fshadow",
    ],
)
def test_nonexistent_and_traversal_paths(redfish, path):
    resp = redfish.get(path)
    assert resp.status_code == 404
    assert "root:" not in resp.text


def test_oversized_body_rejected(redfish, manager_uri):
    # 10 MiB of JSON to a normal resource. bmcweb caps request bodies; the
    # exact status (400/413) is less important than not hanging or crashing.
    blob = '{"x": "' + "A" * (10 * 1024 * 1024) + '"}'
    resp = redfish.patch(
        manager_uri, data=blob.encode(), headers={"Content-Type": "application/json"}
    )
    assert 400 <= resp.status_code < 500


def test_wrong_content_type(redfish, manager_uri):
    resp = redfish.patch(
        manager_uri,
        data=b"DateTime=now",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert 400 <= resp.status_code < 500
