"""Service root checks. Those marked smoke gate the rest of the functional run."""

import re

import pytest

pytestmark = [pytest.mark.requires("redfish")]
smoke = pytest.mark.smoke

REQUIRED_ROOT_LINKS = ["Systems", "Chassis", "Managers", "SessionService", "AccountService"]


@smoke
def test_service_root_is_public_and_versioned(anon):
    # DSP0266: the service root must be readable without authentication.
    resp = anon.get("/redfish/v1")
    assert resp.status_code == 200
    body = resp.json()
    assert re.fullmatch(r"\d+\.\d+\.\d+", body["RedfishVersion"])
    assert body["@odata.id"] == "/redfish/v1"
    assert "UUID" in body


@smoke
@pytest.mark.parametrize("link", REQUIRED_ROOT_LINKS)
def test_service_root_links_resolve(redfish, link):
    root = redfish.get_json("/redfish/v1")
    assert link in root, f"service root is missing {link}"
    resp = redfish.get(root[link]["@odata.id"])
    assert resp.status_code == 200


@smoke
def test_odata_metadata_document(anon):
    resp = anon.get("/redfish/v1/$metadata")
    assert resp.status_code == 200
    assert "xml" in resp.headers.get("Content-Type", "")


@smoke
def test_every_collection_member_is_reachable(redfish):
    """Walk one level below each top-level collection: no dangling @odata.id links."""
    for collection in ("/redfish/v1/Systems", "/redfish/v1/Chassis", "/redfish/v1/Managers"):
        body = redfish.get_json(collection)
        assert body["Members@odata.count"] == len(body["Members"])
        for member in body["Members"]:
            resp = redfish.get(member["@odata.id"])
            assert resp.status_code == 200, f"{member['@odata.id']} -> {resp.status_code}"
            assert resp.json()["@odata.id"] == member["@odata.id"]


# Not smoke: a missing version is a firmware defect, not a sign the run is invalid.
# Keeping it out of the smoke gate lets the rest of the suite still report.
@pytest.mark.firmware
def test_manager_reports_firmware_version(redfish, manager_uri):
    mgr = redfish.get_json(manager_uri)
    assert mgr.get("FirmwareVersion"), "manager has no FirmwareVersion"
    assert mgr["Status"]["State"] == "Enabled"
    assert mgr["Status"].get("Health", "OK") in ("OK", "Warning")
