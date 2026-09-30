"""Convert OpenSCAP XCCDF results and Lynis reports into CheckSuites."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..checks import CheckSuite, Status

XCCDF_NS = {"x": "http://checklists.nist.gov/xccdf/1.2"}

XCCDF_STATUS = {
    "pass": Status.PASS,
    "fixed": Status.PASS,
    "fail": Status.FAIL,
    "error": Status.ERROR,
    "unknown": Status.ERROR,
    "notapplicable": Status.SKIP,
    "notchecked": Status.SKIP,
    "notselected": None,  # not part of the profile; omit entirely
    "informational": Status.WARN,
}


def xccdf_results(path: Path, severities: set[str] | None = None) -> CheckSuite:
    """Parse ``oscap xccdf eval --results`` output.

    ``severities`` restricts which failing rules count as FAIL; lower-severity
    failures become WARN so a new low-severity rule upstream doesn't break CI.
    """
    root = ET.parse(path).getroot()
    result = root.find(".//x:TestResult", XCCDF_NS)
    if result is None:
        raise ValueError(f"{path} contains no XCCDF TestResult")

    titles = {
        r.get("id"): (r.findtext("x:title", default="", namespaces=XCCDF_NS) or "").strip()
        for r in root.iter(f"{{{XCCDF_NS['x']}}}Rule")
    }
    suite = CheckSuite("baseos.cis")
    profile = result.find("x:profile", XCCDF_NS)
    if profile is not None:
        suite.properties["profile"] = profile.get("idref", "")
    score = result.find("x:score", XCCDF_NS)
    if score is not None and score.text:
        suite.properties["score"] = round(float(score.text), 2)

    for rr in result.findall("x:rule-result", XCCDF_NS):
        outcome = (rr.findtext("x:result", default="unknown", namespaces=XCCDF_NS) or "").strip()
        status = XCCDF_STATUS.get(outcome, Status.ERROR)
        if status is None:
            continue
        rule_id = rr.get("idref", "?")
        severity = rr.get("severity", "unknown")
        if status is Status.FAIL and severities and severity not in severities:
            status = Status.WARN
        short = rule_id.removeprefix("xccdf_org.ssgproject.content_rule_")
        check = suite.add(short, status, titles.get(rule_id, ""), severity=severity)
        check.classname = f"cis.{severity}"
    return suite


def lynis_report(path: Path, min_hardening_index: int = 0) -> CheckSuite:
    """Parse ``/var/log/lynis-report.dat`` (key=value, repeatable ``key[]=``)."""
    suite = CheckSuite("baseos.lynis")
    warnings: list[str] = []
    suggestions: list[str] = []
    index = None
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key == "hardening_index":
            index = int(value)
        elif key == "warning[]":
            warnings.append(value)
        elif key == "suggestion[]":
            suggestions.append(value)

    if index is None:
        suite.add("hardening_index", Status.ERROR, "hardening_index missing from report")
    else:
        suite.properties["hardening_index"] = index
        ok = index >= min_hardening_index
        suite.add(
            "hardening_index",
            Status.PASS if ok else Status.FAIL,
            f"hardening index {index} (minimum {min_hardening_index})",
        )
    # Lynis warnings are surfaced but not gated: the hardening index is the
    # gate, and CIS rules (OpenSCAP) are the authoritative pass/fail source.
    for w in warnings:
        test_id, _, text = w.partition("|")
        suite.add(f"warning {test_id}", Status.WARN, text.split("|")[0])
    suite.properties["suggestions"] = len(suggestions)
    return suite
