"""A single result model shared by every non-pytest producer.

CIS audits, CVE gates, DCGM diagnostics and the NVML health tool all emit
``CheckSuite`` objects, so Jenkins renders all of them in the same JUnit test
view and the dashboard aggregates them without per-tool special cases.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Status(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


@dataclass
class Check:
    name: str
    status: Status
    message: str = ""
    classname: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckSuite:
    name: str
    checks: list[Check] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)

    def add(self, name: str, status: Status | str, message: str = "", **details: Any) -> Check:
        check = Check(name=name, status=Status(status), message=message, details=details)
        self.checks.append(check)
        return check

    def count(self, status: Status) -> int:
        return sum(1 for c in self.checks if c.status is status)

    @property
    def failed(self) -> bool:
        return any(c.status in (Status.FAIL, Status.ERROR) for c in self.checks)

    # ------------------------------------------------------------ serialisers
    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.name,
            "properties": self.properties,
            "summary": {s.value: self.count(s) for s in Status},
            "checks": [{**asdict(c), "status": c.status.value} for c in self.checks],
        }

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

    def to_junit(self) -> ET.Element:
        suite = ET.Element(
            "testsuite",
            name=self.name,
            tests=str(len(self.checks)),
            failures=str(self.count(Status.FAIL)),
            errors=str(self.count(Status.ERROR)),
            skipped=str(self.count(Status.SKIP)),
        )
        if self.properties:
            props = ET.SubElement(suite, "properties")
            for key, value in self.properties.items():
                ET.SubElement(props, "property", name=str(key), value=str(value))
        for check in self.checks:
            case = ET.SubElement(
                suite, "testcase", name=check.name, classname=check.classname or self.name
            )
            if check.status is Status.FAIL:
                ET.SubElement(case, "failure", message=check.message).text = _fmt(check)
            elif check.status is Status.ERROR:
                ET.SubElement(case, "error", message=check.message).text = _fmt(check)
            elif check.status is Status.SKIP:
                ET.SubElement(case, "skipped", message=check.message)
            elif check.status is Status.WARN:
                # JUnit has no "warning"; keep it visible without failing the build.
                ET.SubElement(case, "system-out").text = f"WARNING: {check.message}"
        return suite

    def write_junit(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tree = ET.ElementTree(self.to_junit())
        ET.indent(tree)
        tree.write(path, encoding="utf-8", xml_declaration=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckSuite:
        suite = cls(name=data["suite"], properties=data.get("properties", {}))
        for c in data.get("checks", []):
            suite.checks.append(
                Check(
                    name=c["name"],
                    status=Status(c["status"]),
                    message=c.get("message", ""),
                    classname=c.get("classname", ""),
                    details=c.get("details", {}),
                )
            )
        return suite


def _fmt(check: Check) -> str:
    if not check.details:
        return check.message
    return f"{check.message}\n{json.dumps(check.details, indent=2, default=str)}"
