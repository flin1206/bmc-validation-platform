"""Power control: action discovery on every platform, real transitions only where a host exists."""

import pytest

pytestmark = pytest.mark.power


def allowable_reset_types(redfish, system_uri) -> list[str]:
    action = redfish.get_json(system_uri)["Actions"]["#ComputerSystem.Reset"]
    if "ResetType@Redfish.AllowableValues" in action:
        return action["ResetType@Redfish.AllowableValues"]
    info = redfish.get_json(action["@Redfish.ActionInfo"])
    param = next(p for p in info["Parameters"] if p["Name"] == "ResetType")
    return param["AllowableValues"]


@pytest.mark.requires("chassis_power_state")
def test_power_state_is_reported(redfish, system_uri):
    assert redfish.get_json(system_uri)["PowerState"] in ("On", "Off", "PoweringOn", "PoweringOff")


@pytest.mark.requires("chassis_power_state")
def test_reset_action_advertises_standard_types(redfish, system_uri):
    types = set(allowable_reset_types(redfish, system_uri))
    assert {"On", "ForceOff"} <= types, f"missing basic reset types: {types}"


def _wait_power(redfish, system_uri, want: str, timeout: float):
    return redfish.wait_until(
        lambda: redfish.get_json(system_uri)["PowerState"] == want,
        timeout=timeout,
        poll=3,
        what=f"PowerState == {want}",
    )


@pytest.mark.destructive
@pytest.mark.requires("host_power")
def test_force_off_then_on(redfish, system_uri, profile):
    timeout = profile.timeout("power_transition", 120)
    reset = f"{system_uri}/Actions/ComputerSystem.Reset"

    assert redfish.post(reset, json={"ResetType": "ForceOff"}).status_code in (200, 202, 204)
    _wait_power(redfish, system_uri, "Off", timeout)

    assert redfish.post(reset, json={"ResetType": "On"}).status_code in (200, 202, 204)
    _wait_power(redfish, system_uri, "On", timeout)


@pytest.mark.destructive
@pytest.mark.requires("host_power")
def test_power_on_when_already_on_is_idempotent(redfish, system_uri, profile):
    reset = f"{system_uri}/Actions/ComputerSystem.Reset"
    redfish.post(reset, json={"ResetType": "On"})
    _wait_power(redfish, system_uri, "On", profile.timeout("power_transition", 120))
    resp = redfish.post(reset, json={"ResetType": "On"})
    # Either accepted as a no-op or rejected with a clear error; never a 5xx.
    assert resp.status_code < 500
    assert redfish.get_json(system_uri)["PowerState"] == "On"


@pytest.mark.destructive
@pytest.mark.requires("host_power")
def test_power_events_logged(redfish, system_uri, manager_uri, profile):
    """A power transition must leave an audit trail in the event log."""
    logs = redfish.get_json(f"{system_uri}/LogServices")
    event_log = next(m["@odata.id"] for m in logs["Members"] if m["@odata.id"].endswith("EventLog"))
    before = redfish.get_json(f"{event_log}/Entries")["Members@odata.count"]

    reset = f"{system_uri}/Actions/ComputerSystem.Reset"
    redfish.post(reset, json={"ResetType": "ForceRestart"})
    redfish.wait_until(
        lambda: redfish.get_json(f"{event_log}/Entries")["Members@odata.count"] > before,
        timeout=profile.timeout("power_transition", 120),
        what="new event log entry",
    )
