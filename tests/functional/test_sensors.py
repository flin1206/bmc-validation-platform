"""Sensor inventory: discovered dynamically, validated against the Redfish Sensor schema rules."""

import math

import pytest

pytestmark = [pytest.mark.sensors, pytest.mark.requires("sensors")]

VALID_UNITS = {"Cel", "V", "A", "W", "RPM", "%", "Pa", "J", "kW.h", "Hz", "m3/min", "L/min"}


@pytest.fixture(scope="module")
def sensors(redfish, chassis_uris):
    found = []
    for chassis in chassis_uris:
        body = redfish.get_json(chassis)
        if "Sensors" in body:
            found.extend(redfish.members(body["Sensors"]["@odata.id"]))
    return found


def test_sensor_count_meets_profile_minimum(sensors, profile):
    minimum = profile.sensors.get("min_count", 1)
    assert len(sensors) >= minimum, f"found {len(sensors)} sensors, expected >= {minimum}"


def test_sensor_ids_unique(sensors):
    ids = [s["@odata.id"] for s in sensors]
    assert len(ids) == len(set(ids))


def test_sensor_readings_are_numbers_with_units(sensors):
    bad = []
    for s in sensors:
        if s.get("Status", {}).get("State") != "Enabled":
            continue
        reading = s.get("Reading")
        if reading is None or not isinstance(reading, (int, float)) or math.isnan(reading):
            bad.append(f"{s['Id']}: reading={reading!r}")
        elif s.get("ReadingUnits") not in VALID_UNITS:
            bad.append(f"{s['Id']}: units={s.get('ReadingUnits')!r}")
    assert not bad, "invalid sensor readings:\n" + "\n".join(bad)


def test_thresholds_are_ordered(sensors):
    """LowerFatal <= LowerCritical <= UpperCritical <= UpperFatal, when present."""
    order = [
        "LowerFatal",
        "LowerCritical",
        "LowerCaution",
        "UpperCaution",
        "UpperCritical",
        "UpperFatal",
    ]
    bad = []
    for s in sensors:
        th = s.get("Thresholds", {})
        values = [th[k]["Reading"] for k in order if th.get(k, {}).get("Reading") is not None]
        if values != sorted(values):
            bad.append(f"{s['Id']}: {th}")
    assert not bad, "mis-ordered thresholds:\n" + "\n".join(bad)


def test_readings_within_critical_thresholds(sensors):
    """A healthy idle system should not be sitting beyond a critical threshold."""
    over = []
    for s in sensors:
        r, th = s.get("Reading"), s.get("Thresholds", {})
        if not isinstance(r, (int, float)):
            continue
        lo = th.get("LowerCritical", {}).get("Reading")
        hi = th.get("UpperCritical", {}).get("Reading")
        if (lo is not None and r < lo) or (hi is not None and r > hi):
            over.append(f"{s['Id']}={r} (crit {lo}..{hi})")
    assert not over, "sensors beyond critical thresholds:\n" + "\n".join(over)


def test_sensor_health_rollup(sensors):
    critical = [s["Id"] for s in sensors if s.get("Status", {}).get("Health") == "Critical"]
    assert not critical, f"sensors reporting Critical health: {critical}"
