"""Compare two Grype reports and enforce a release gate.

The question a firmware release owner asks is not "how many CVEs are in this
image?" (the answer is always "many") but "what did *this* change introduce,
and is any of it unacceptable?". So the gate is computed on the *delta*
between a baseline (last released firmware) and a candidate build:

- new     : present in candidate, absent in baseline  -> gated by policy
- fixed   : present in baseline, gone in candidate    -> reported as a win
- carried : present in both                           -> tracked, gated only
            if the policy says so (``gate_carried``)

Waivers must carry an owner, a reason and an expiry date. An expired waiver
stops suppressing its finding, so accepted risk is re-reviewed automatically
instead of living forever in a config file.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..checks import CheckSuite, Status

SEVERITY_ORDER = ["Unknown", "Negligible", "Low", "Medium", "High", "Critical"]


def severity_rank(sev: str) -> int:
    try:
        return SEVERITY_ORDER.index(sev.capitalize())
    except ValueError:
        return 0


@dataclass(frozen=True)
class Finding:
    vuln_id: str
    package: str
    version: str
    severity: str
    fixed_in: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        # Version is deliberately excluded: bumping a package from 1.2.3 to
        # 1.2.4 without fixing CVE-X should show as "carried", not "fixed + new".
        return (self.vuln_id, self.package)


@dataclass
class Waiver:
    vuln_id: str
    reason: str
    owner: str
    expires: dt.date
    package: str | None = None

    def matches(self, f: Finding) -> bool:
        return self.vuln_id == f.vuln_id and (self.package is None or self.package == f.package)

    def active(self, today: dt.date) -> bool:
        return today <= self.expires


@dataclass
class Policy:
    fail_on: str = "Critical"
    gate_carried: bool = False
    waivers: list[Waiver] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None) -> Policy:
        if path is None:
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        waivers = []
        for w in data.get("waivers", []):
            missing = {"id", "reason", "owner", "expires"} - set(w)
            if missing:
                raise ValueError(f"waiver {w.get('id', '?')} missing fields: {sorted(missing)}")
            expires = w["expires"]
            if isinstance(expires, str):
                expires = dt.date.fromisoformat(expires)
            waivers.append(Waiver(w["id"], w["reason"], w["owner"], expires, w.get("package")))
        return cls(
            fail_on=data.get("fail_on", "Critical"),
            gate_carried=bool(data.get("gate_carried", False)),
            waivers=waivers,
        )

    def blocking(self, f: Finding) -> bool:
        return severity_rank(f.severity) >= severity_rank(self.fail_on)


def load_grype(path: Path) -> dict[tuple[str, str], Finding]:
    """Parse ``grype -o json`` output into findings keyed by (vuln, package)."""
    data = json.loads(path.read_text())
    findings: dict[tuple[str, str], Finding] = {}
    for m in data.get("matches", []):
        vuln = m.get("vulnerability", {})
        art = m.get("artifact", {})
        f = Finding(
            vuln_id=vuln.get("id", "UNKNOWN"),
            package=art.get("name", "?"),
            version=art.get("version", "?"),
            severity=vuln.get("severity", "Unknown"),
            fixed_in=tuple(vuln.get("fix", {}).get("versions", []) or ()),
        )
        prev = findings.get(f.key)
        if prev is None or severity_rank(f.severity) > severity_rank(prev.severity):
            findings[f.key] = f
    return findings


def load_manifest(path: Path) -> set[str]:
    """Package names from a Yocto image manifest (``<pkg> <arch> <version>`` per line)."""
    return {line.split()[0] for line in path.read_text().splitlines() if line.strip()}


def shipped_only(
    findings: dict[tuple[str, str], Finding], shipped: set[str]
) -> tuple[dict[tuple[str, str], Finding], dict[str, int]]:
    """Drop findings for packages that are not installed in the image.

    Yocto's image SPDX also describes build-time dependencies (``*-native``
    host tools such as rsync-native or openssl-native) that never reach the
    flash. Matching those inflates the report with vulnerabilities the device
    cannot have. The image manifest is the authoritative list of what ships.
    Excluded packages are returned, not hidden, so a reviewer can audit them.
    """
    kept: dict[tuple[str, str], Finding] = {}
    excluded: dict[str, int] = {}
    for key, f in findings.items():
        if f.package in shipped:
            kept[key] = f
        else:
            excluded[f.package] = excluded.get(f.package, 0) + 1
    return kept, dict(sorted(excluded.items(), key=lambda kv: (-kv[1], kv[0])))


@dataclass
class DiffResult:
    new: list[Finding]
    fixed: list[Finding]
    carried: list[Finding]
    blocking: list[Finding]
    waived: list[tuple[Finding, Waiver]]
    expired_waivers: list[Waiver]
    excluded: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.blocking


def _sorted(findings) -> list[Finding]:
    return sorted(findings, key=lambda f: (-severity_rank(f.severity), f.vuln_id, f.package))


def diff(
    baseline: dict[tuple[str, str], Finding],
    candidate: dict[tuple[str, str], Finding],
    policy: Policy,
    today: dt.date | None = None,
) -> DiffResult:
    today = today or dt.date.today()
    new = _sorted(f for k, f in candidate.items() if k not in baseline)
    fixed = _sorted(f for k, f in baseline.items() if k not in candidate)
    carried = _sorted(f for k, f in candidate.items() if k in baseline)

    gated = new + (carried if policy.gate_carried else [])
    blocking: list[Finding] = []
    waived: list[tuple[Finding, Waiver]] = []
    for f in gated:
        if not policy.blocking(f):
            continue
        waiver = next((w for w in policy.waivers if w.matches(f) and w.active(today)), None)
        if waiver:
            waived.append((f, waiver))
        else:
            blocking.append(f)
    expired = [w for w in policy.waivers if not w.active(today)]
    return DiffResult(new, fixed, carried, _sorted(blocking), waived, expired)


def severity_counts(findings: list[Finding]) -> dict[str, int]:
    counts = {s: 0 for s in reversed(SEVERITY_ORDER)}
    for f in findings:
        counts[f.severity.capitalize() if f.severity.capitalize() in counts else "Unknown"] += 1
    return counts


def to_markdown(result: DiffResult, baseline_label: str, candidate_label: str) -> str:
    def table(findings: list[Finding], limit: int = 50) -> str:
        if not findings:
            return "_none_\n"
        rows = ["| Severity | ID | Package | Version | Fixed in |", "|---|---|---|---|---|"]
        for f in findings[:limit]:
            fix = ", ".join(f.fixed_in) or "—"
            rows.append(f"| {f.severity} | {f.vuln_id} | {f.package} | {f.version} | {fix} |")
        if len(findings) > limit:
            rows.append(f"| … | {len(findings) - limit} more | | | |")
        return "\n".join(rows) + "\n"

    verdict = "PASS" if result.passed else f"FAIL: {len(result.blocking)} blocking finding(s)"
    lines = [
        f"# Firmware CVE delta: `{baseline_label}` → `{candidate_label}`",
        "",
        f"**Gate: {verdict}**",
        "",
        "| | " + " | ".join(reversed(SEVERITY_ORDER)) + " |",
        "|---|" + "---|" * len(SEVERITY_ORDER),
    ]
    for label, group in (("New", result.new), ("Fixed", result.fixed), ("Carried", result.carried)):
        counts = severity_counts(group)
        lines.append(f"| {label} | " + " | ".join(str(counts[s]) for s in counts) + " |")
    lines += ["", "## Blocking", "", table(result.blocking)]
    if result.waived:
        lines += [
            "## Waived",
            "",
            "| ID | Package | Owner | Expires | Reason |",
            "|---|---|---|---|---|",
        ]
        for f, w in result.waived:
            lines.append(f"| {f.vuln_id} | {f.package} | {w.owner} | {w.expires} | {w.reason} |")
        lines.append("")
    if result.expired_waivers:
        lines += ["## Expired waivers (no longer suppressing)", ""]
        lines += [
            f"- {w.vuln_id} (owner {w.owner}, expired {w.expires})" for w in result.expired_waivers
        ]
        lines.append("")
    if result.excluded:
        total = sum(result.excluded.values())
        lines += [
            f"## Excluded: {total} finding(s) in packages not shipped in the image",
            "",
            ", ".join(f"`{pkg}` ({n})" for pkg, n in result.excluded.items()),
            "",
        ]
    lines += [
        "## New findings",
        "",
        table(result.new),
        "## Fixed since baseline",
        "",
        table(result.fixed),
    ]
    return "\n".join(lines)


def to_suite(result: DiffResult) -> CheckSuite:
    suite = CheckSuite("firmware.cve_gate")
    suite.properties.update(
        new=len(result.new), fixed=len(result.fixed), carried=len(result.carried)
    )
    blocking_keys = {f.key for f in result.blocking}
    waived_keys = {f.key for f, _ in result.waived}
    for f in result.new:
        name = f"{f.vuln_id} [{f.package} {f.version}]"
        details: dict[str, Any] = {"severity": f.severity, "fixed_in": list(f.fixed_in)}
        if f.key in blocking_keys:
            suite.add(name, Status.FAIL, f"new {f.severity} vulnerability", **details)
        elif f.key in waived_keys:
            suite.add(name, Status.WARN, f"new {f.severity} vulnerability (waived)", **details)
        else:
            suite.add(name, Status.PASS, f"new {f.severity} vulnerability below gate", **details)
    for w in result.expired_waivers:
        suite.add(
            f"waiver {w.vuln_id}", Status.WARN, f"waiver expired on {w.expires}", owner=w.owner
        )
    if not suite.checks:
        suite.add("no new vulnerabilities", Status.PASS)
    return suite
