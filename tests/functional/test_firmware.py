"""Firmware inventory consistency and update-path robustness.

The negative update tests are the important ones: a BMC that bricks itself on
a truncated or tampered image is a field-return, so every rejection path must
leave the *running* firmware untouched and the BMC responsive.
"""

import os
import secrets

import pytest

pytestmark = [pytest.mark.firmware, pytest.mark.requires("firmware_inventory")]


def active_bmc_image(redfish, manager_uri) -> dict:
    mgr = redfish.get_json(manager_uri)
    return redfish.get_json(mgr["Links"]["ActiveSoftwareImage"]["@odata.id"])


def test_inventory_lists_active_bmc_image(redfish, manager_uri):
    inv = redfish.get_json("/redfish/v1/UpdateService/FirmwareInventory")
    assert inv["Members@odata.count"] >= 1
    image = active_bmc_image(redfish, manager_uri)
    assert image["Status"]["State"] == "Enabled"
    assert image["Version"]


def test_manager_version_matches_active_image(redfish, manager_uri):
    mgr = redfish.get_json(manager_uri)
    assert mgr["FirmwareVersion"] == active_bmc_image(redfish, manager_uri)["Version"]


def test_expected_version_if_pinned(redfish, manager_uri):
    """CI sets EXPECTED_FW_VERSION so a mis-flashed image cannot pass as the candidate."""
    expected = os.environ.get("EXPECTED_FW_VERSION")
    if not expected:
        pytest.skip("EXPECTED_FW_VERSION not set")
    assert redfish.get_json(manager_uri)["FirmwareVersion"] == expected


def _push_uri(redfish) -> tuple[str, bool]:
    svc = redfish.get_json("/redfish/v1/UpdateService")
    if "MultipartHttpPushUri" in svc:
        return svc["MultipartHttpPushUri"], True
    return svc["HttpPushUri"], False


def _push(redfish, payload: bytes):
    uri, multipart = _push_uri(redfish)
    if multipart:
        return redfish.post(
            uri,
            files={
                "UpdateParameters": (None, '{"Targets":[]}', "application/json"),
                "UpdateFile": ("image.bin", payload, "application/octet-stream"),
            },
            timeout=300,
        )
    return redfish.post(
        uri, data=payload, headers={"Content-Type": "application/octet-stream"}, timeout=300
    )


def _assert_rejected_and_unchanged(redfish, manager_uri, resp, before: dict, profile):
    if resp.status_code == 202:
        # Accepted for async processing: the task must end in failure.
        task_uri = resp.json().get("@odata.id") or resp.headers["Location"]
        task = redfish.wait_task(task_uri, timeout=profile.timeout("firmware_task", 900))
        assert task["TaskState"] in ("Exception", "Killed", "Cancelled"), task
    else:
        assert 400 <= resp.status_code < 500, f"{resp.status_code}: {resp.text[:300]}"
    after = active_bmc_image(redfish, manager_uri)
    assert after["Version"] == before["Version"], "running firmware changed after a bad image"
    assert redfish.get("/redfish/v1").status_code == 200


@pytest.mark.destructive
@pytest.mark.requires("firmware_update_push")
@pytest.mark.parametrize(
    "label,payload",
    [
        ("random_bytes", secrets.token_bytes(64 * 1024)),
        ("empty", b""),
        ("truncated_tar", b"ustar\x00" + b"\x00" * 506),
    ],
)
def test_corrupt_image_is_rejected(redfish, manager_uri, profile, label, payload):
    before = active_bmc_image(redfish, manager_uri)
    resp = _push(redfish, payload)
    _assert_rejected_and_unchanged(redfish, manager_uri, resp, before, profile)


@pytest.mark.destructive
@pytest.mark.requires("firmware_update_push")
def test_valid_image_update(redfish, manager_uri, profile):
    """Positive path: needs UPDATE_IMAGE pointing to a signed image tarball for this machine."""
    path = os.environ.get("UPDATE_IMAGE")
    if not path:
        pytest.skip("UPDATE_IMAGE not set")
    with open(path, "rb") as fh:
        resp = _push(redfish, fh.read())
    assert resp.status_code == 202
    task_uri = resp.json().get("@odata.id") or resp.headers["Location"]
    task = redfish.wait_task(task_uri, timeout=profile.timeout("firmware_task", 900))
    assert task["TaskState"] == "Completed", task
