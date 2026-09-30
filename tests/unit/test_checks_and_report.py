import json
import xml.etree.ElementTree as ET

from bmcval import report
from bmcval.checks import CheckSuite, Status
from bmcval.gpu import health


def test_junit_roundtrip(tmp_path):
    suite = CheckSuite("demo")
    suite.add("a", Status.PASS)
    suite.add("b", Status.FAIL, "boom", value=3)
    suite.add("c", Status.SKIP, "n/a")
    suite.add("d", Status.WARN, "meh")
    path = tmp_path / "demo.xml"
    suite.write_junit(path)

    root = ET.parse(path).getroot()
    assert root.get("tests") == "4"
    assert root.get("failures") == "1"
    assert root.get("skipped") == "1"
    failure = root.find("testcase[@name='b']/failure")
    assert failure is not None and '"value": 3' in failure.text

    again = CheckSuite.from_dict(suite.to_dict())
    assert [c.status for c in again.checks] == [c.status for c in suite.checks]


def test_report_aggregates_and_trends(tmp_path):
    reports = tmp_path / "reports"
    ok = CheckSuite("security")
    ok.add("x", Status.PASS)
    ok.write_junit(reports / "security.xml")
    bad = CheckSuite("functional")
    bad.add("y", Status.PASS)
    bad.add("z", Status.FAIL)
    bad.write_junit(reports / "functional.xml")

    history = tmp_path / "history"
    history.mkdir()
    for n in range(3):
        s = report.build_summary(reports, str(n), "abc", "fw")
        s["timestamp"] = f"2026-01-0{n + 1}T00:00:00+00:00"
        (history / f"{n}.json").write_text(json.dumps(s))

    summary = report.build_summary(reports, "4", "abcdef1234", "2.16.0")
    assert not summary["ok"]
    names = {s["name"]: s for s in summary["suites"]}
    assert names["functional"]["failures"] == 1
    assert names["security"]["passed"] == 1

    html = report.render_html(summary, report.load_history(history))
    assert "FAIL" in html and "<polyline" in html


def test_nvml_health_conversion(tmp_path):
    path = tmp_path / "health.json"
    path.write_text(
        json.dumps(
            {
                "driver_version": "550.54",
                "gpus": [
                    {
                        "index": 0,
                        "name": "NVIDIA H100",
                        "pci_bus_id": "00000000:3B:00.0",
                        "checks": [
                            {"name": "temperature", "status": "pass", "message": "41 C"},
                            {"name": "pcie_width", "status": "fail", "message": "x8 of x16"},
                        ],
                    }
                ],
            }
        )
    )
    suite = health.nvml_health(path)
    assert suite.failed
    assert suite.checks[1].name == "gpu0 NVIDIA H100: pcie_width"


def test_dcgm_parser_is_schema_tolerant(tmp_path):
    path = tmp_path / "dcgm.json"
    path.write_text(
        json.dumps(
            {
                "DCGM GPU Diagnostic": {
                    "test_categories": [
                        {
                            "category": "Deployment",
                            "tests": [
                                {"name": "Denylist", "results": [{"status": "Pass"}]},
                                {
                                    "test_name": "PCIe",
                                    "status": "Fail",
                                    "gpu_id": 0,
                                    "warnings": [{"message": "bandwidth below threshold"}],
                                },
                            ],
                        }
                    ]
                }
            }
        )
    )
    suite = health.dcgm_diag(path)
    by_name = {c.name: c for c in suite.checks}
    assert by_name["PCIe (gpu 0)"].status is Status.FAIL
    assert "bandwidth" in by_name["PCIe (gpu 0)"].message
