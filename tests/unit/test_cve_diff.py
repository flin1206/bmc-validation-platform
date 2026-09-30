import datetime as dt
import json

import pytest

from bmcval.checks import Status
from bmcval.security import cve_diff


def grype(tmp_path, name, matches):
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "matches": [
                    {
                        "vulnerability": {"id": vid, "severity": sev, "fix": {"versions": fix}},
                        "artifact": {"name": pkg, "version": ver},
                    }
                    for vid, pkg, ver, sev, fix in matches
                ]
            }
        )
    )
    return cve_diff.load_grype(path)


@pytest.fixture
def reports(tmp_path):
    baseline = grype(
        tmp_path,
        "base",
        [
            ("CVE-2023-0001", "openssl", "3.0.1", "High", ["3.0.8"]),
            ("CVE-2023-0002", "busybox", "1.35", "Medium", []),
        ],
    )
    candidate = grype(
        tmp_path,
        "cand",
        [
            ("CVE-2023-0002", "busybox", "1.36", "Medium", []),  # carried (version bump, not fixed)
            ("CVE-2024-9999", "dropbear", "2022.83", "Critical", ["2024.84"]),  # new
            ("CVE-2024-1234", "zlib", "1.2.13", "Low", []),  # new, below gate
        ],
    )
    return baseline, candidate


def test_classifies_new_fixed_carried(reports):
    result = cve_diff.diff(*reports, cve_diff.Policy())
    assert [f.vuln_id for f in result.new] == ["CVE-2024-9999", "CVE-2024-1234"]
    assert [f.vuln_id for f in result.fixed] == ["CVE-2023-0001"]
    assert [f.vuln_id for f in result.carried] == ["CVE-2023-0002"]


def test_new_critical_blocks(reports):
    result = cve_diff.diff(*reports, cve_diff.Policy(fail_on="Critical"))
    assert not result.passed
    assert [f.vuln_id for f in result.blocking] == ["CVE-2024-9999"]


def test_lower_threshold_catches_more(reports):
    result = cve_diff.diff(*reports, cve_diff.Policy(fail_on="Low"))
    assert {f.vuln_id for f in result.blocking} == {"CVE-2024-9999", "CVE-2024-1234"}


def test_active_waiver_suppresses(reports):
    waiver = cve_diff.Waiver(
        "CVE-2024-9999", "not reachable: dropbear disabled", "flin", dt.date(2030, 1, 1)
    )
    result = cve_diff.diff(*reports, cve_diff.Policy(waivers=[waiver]), today=dt.date(2026, 1, 1))
    assert result.passed
    assert result.waived[0][0].vuln_id == "CVE-2024-9999"


def test_expired_waiver_stops_suppressing(reports):
    waiver = cve_diff.Waiver("CVE-2024-9999", "temp", "flin", dt.date(2025, 1, 1))
    result = cve_diff.diff(*reports, cve_diff.Policy(waivers=[waiver]), today=dt.date(2026, 1, 1))
    assert not result.passed
    assert result.expired_waivers == [waiver]


def test_waiver_scoped_to_package(reports):
    waiver = cve_diff.Waiver("CVE-2024-9999", "x", "flin", dt.date(2030, 1, 1), package="other")
    result = cve_diff.diff(*reports, cve_diff.Policy(waivers=[waiver]), today=dt.date(2026, 1, 1))
    assert not result.passed


def test_gate_carried(reports):
    policy = cve_diff.Policy(fail_on="Medium", gate_carried=True)
    ids = {f.vuln_id for f in cve_diff.diff(*reports, policy).blocking}
    assert "CVE-2023-0002" in ids


def test_policy_requires_waiver_fields(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("waivers:\n  - id: CVE-1\n    reason: x\n")
    with pytest.raises(ValueError, match="owner"):
        cve_diff.Policy.load(p)


def test_policy_loads_dates(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "fail_on: High\nwaivers:\n  - {id: CVE-1, reason: r, owner: o, expires: 2030-01-31}\n"
    )
    policy = cve_diff.Policy.load(p)
    assert policy.fail_on == "High"
    assert policy.waivers[0].expires == dt.date(2030, 1, 31)


def test_duplicate_matches_keep_highest_severity(tmp_path):
    findings = grype(
        tmp_path,
        "dup",
        [
            ("CVE-1", "openssl", "3.0", "Medium", []),
            ("CVE-1", "openssl", "3.0", "High", []),
        ],
    )
    assert findings[("CVE-1", "openssl")].severity == "High"


def test_suite_and_markdown(reports):
    result = cve_diff.diff(*reports, cve_diff.Policy())
    suite = cve_diff.to_suite(result)
    statuses = {c.name.split()[0]: c.status for c in suite.checks}
    assert statuses["CVE-2024-9999"] is Status.FAIL
    assert statuses["CVE-2024-1234"] is Status.PASS
    md = cve_diff.to_markdown(result, "v2.14", "v2.16")
    assert "FAIL: 1 blocking" in md
    assert "CVE-2024-9999" in md


def test_shipped_only_drops_build_host_packages(tmp_path):
    findings = grype(
        tmp_path,
        "spdx",
        [
            ("CVE-1", "openssl-native", "3.5", "Critical", []),  # build host tool
            ("CVE-1", "libssl3", "3.5", "Critical", []),  # shipped library
            ("CVE-2", "rsync-native", "3.4", "High", []),
            ("CVE-3", "rsync-native", "3.4", "High", []),
        ],
    )
    manifest = tmp_path / "image.manifest"
    manifest.write_text("libssl3 arm1176jzs 3.5-r0\nbusybox arm1176jzs 1.36-r0\n")
    kept, excluded = cve_diff.shipped_only(findings, cve_diff.load_manifest(manifest))
    assert set(kept) == {("CVE-1", "libssl3")}
    assert excluded == {"rsync-native": 2, "openssl-native": 1}


def test_excluded_packages_are_listed_in_markdown(reports):
    result = cve_diff.diff(*reports, cve_diff.Policy())
    result.excluded = {"rsync-native": 29}
    assert "`rsync-native` (29)" in cve_diff.to_markdown(result, "a", "b")
