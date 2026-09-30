"""Firmware inventory consistency and update-path robustness.

The negative update tests are the important ones: a BMC that bricks itself on
a truncated or tampered image is a field-return, so every rejection path must
leave the *running* firmware untouched and the BMC responsive.
"""

import os
import secrets

import pytest

pytestmark = [pytest.mark.firmware, pytest.mark.requires("firmware_inventory")]


def bmc_images(redfish, manager_uri) -> list[dict]:
    """Inventory items that declare the BMC manager as their RelatedItem.

    Found independently of the Manager's own links, so an inventory problem and a
    Manager-linking problem show up as separate failures.
    """
    return [
        item
        for item in redfish.members("/redfish/v1/UpdateService/FirmwareInventory")
        if any(r.get("@odata.id") == manager_uri for r in item.get("RelatedItem", []))
    ]


def test_inventory_lists_bmc_image(redfish, manager_uri):
    images = bmc_images(redfish, manager_uri)
    assert images, "no FirmwareInventory item is related to the BMC manager"
    assert any(i["Status"]["State"] == "Enabled" and i.get("Version") for i in images)


def test_manager_links_active_image(redfish, manager_uri):
    links = redfish.get_json(manager_uri).get("Links", {})
    assert "ActiveSoftwareImage" in links, "Manager.Links.ActiveSoftwareImage missing"


def test_manager_version_matches_active_image(redfish, manager_uri):
    mgr = redfish.get_json(manager_uri)
    versions = {i["Version"] for i in bmc_images(redfish, manager_uri)}
    assert mgr.get("FirmwareVersion") in versions, (
        f"Manager.FirmwareVersion={mgr.get('FirmwareVersion')!r}, inventory has {versions}"
    )


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


def _outcome(redfish, resp, profile) -> str:
    """'rejected' (4xx), 'server_error' (5xx), or the terminal TaskState of an accepted push."""
    if resp.status_code == 202:
        task_uri = resp.json().get("@odata.id") or resp.headers["Location"]
        return redfish.wait_task(task_uri, timeout=profile.timeout("firmware_task", 900))[
            "TaskState"
        ]
    if 400 <= resp.status_code < 500:
        return "rejected"
    if resp.status_code >= 500:
        return "server_error"
    return f"accepted({resp.status_code})"


CORRUPT = pytest.mark.parametrize(
    "payload",
    [
        secrets.token_bytes(64 * 1024),
        b"",
        b"ustar\x00" + b"\x00" * 506,
    ],
    ids=["random_bytes", "empty", "truncated_tar"],
)


@pytest.mark.destructive
@pytest.mark.requires("firmware_update_push")
@CORRUPT
def test_corrupt_image_never_applied(redfish, manager_uri, profile, payload):
    """Safety: a bad image must never replace the running firmware or take the BMC down."""
    before = {i["Version"] for i in bmc_images(redfish, manager_uri)}
    outcome = _outcome(redfish, _push(redfish, payload), profile)
    assert outcome != "Completed" and not outcome.startswith("accepted"), outcome
    after = {i["Version"] for i in bmc_images(redfish, manager_uri)}
    assert after == before, f"firmware inventory changed after a bad image: {before} -> {after}"
    assert redfish.get("/redfish/v1").status_code == 200


@pytest.mark.destructive
@pytest.mark.requires("firmware_update_push")
@CORRUPT
def test_corrupt_image_gets_client_error(redfish, profile, payload):
    """Protocol: a malformed upload is the client's fault, so 4xx or a failed Task, never 500."""
    outcome = _outcome(redfish, _push(redfish, payload), profile)
    assert outcome in ("rejected", "Exception", "Killed", "Cancelled"), outcome


@pytest.mark.destructive
@pytest.mark.applies_firmware
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
