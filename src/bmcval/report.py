"""Aggregate every JUnit file of a build into one summary + a static HTML dashboard.

``summary.json`` is also appended to a history directory, so the dashboard can
draw a per-suite trend across builds without needing a database.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class SuiteSummary:
    name: str
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    time: float = 0.0

    @property
    def passed(self) -> int:
        return self.tests - self.failures - self.errors - self.skipped

    @property
    def ok(self) -> bool:
        return self.failures == 0 and self.errors == 0


def read_junit(path: Path) -> list[SuiteSummary]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    out = []
    for s in suites:
        out.append(
            SuiteSummary(
                name=s.get("name") or path.stem,
                tests=int(s.get("tests", 0)),
                failures=int(s.get("failures", 0)),
                errors=int(s.get("errors", 0)),
                skipped=int(s.get("skipped", 0)),
                time=float(s.get("time", 0) or 0),
            )
        )
    return out


def collect(reports: Path) -> list[SuiteSummary]:
    merged: dict[str, SuiteSummary] = {}
    for path in sorted(reports.rglob("*.xml")):
        try:
            suites = read_junit(path)
        except ET.ParseError:
            continue
        for s in suites:
            if s.name == "pytest":  # pytest's default suite name; use file stem instead
                s.name = path.stem
            m = merged.setdefault(s.name, SuiteSummary(s.name))
            m.tests += s.tests
            m.failures += s.failures
            m.errors += s.errors
            m.skipped += s.skipped
            m.time += s.time
    return sorted(merged.values(), key=lambda s: s.name)


def build_summary(reports: Path, build_id: str, git_sha: str, firmware: str) -> dict:
    suites = collect(reports)
    return {
        "build": build_id,
        "git_sha": git_sha,
        "firmware": firmware,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ok": all(s.ok for s in suites),
        "suites": [asdict(s) | {"passed": s.passed} for s in suites],
    }


def load_history(history: Path, limit: int = 30) -> list[dict]:
    if not history.exists():
        return []
    items = []
    for p in history.glob("*.json"):
        try:
            items.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    items.sort(key=lambda d: d.get("timestamp", ""))
    return items[-limit:]


def _sparkline(values: list[float], width: int = 120, height: int = 24) -> str:
    if len(values) < 2:
        return ""
    step = width / (len(values) - 1)
    pts = " ".join(f"{i * step:.1f},{height - v * height:.1f}" for i, v in enumerate(values))
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" aria-hidden="true">'
        f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>'
    )


def render_html(summary: dict, history: list[dict]) -> str:
    def rate(s: dict) -> float:
        run = s["tests"] - s["skipped"]
        return s["passed"] / run if run else 1.0

    trend: dict[str, list[float]] = {}
    for h in history:
        for s in h.get("suites", []):
            trend.setdefault(s["name"], []).append(rate(s))

    rows = []
    for s in summary["suites"]:
        state = "ok" if s["failures"] == 0 and s["errors"] == 0 else "bad"
        rows.append(
            f'<tr class="{state}"><td>{html.escape(s["name"])}</td>'
            f"<td>{s['passed']}</td><td>{s['failures'] + s['errors']}</td><td>{s['skipped']}</td>"
            f"<td>{rate(s) * 100:.1f}%</td><td>{_sparkline(trend.get(s['name'], []))}</td></tr>"
        )
    verdict = "PASS" if summary["ok"] else "FAIL"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Validation report · build {html.escape(str(summary["build"]))}</title>
<style>
:root {{ --bg:#fff; --fg:#1b1f23; --muted:#57606a; --ok:#1a7f37; --bad:#cf222e; --line:#d0d7de; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0d1117; --fg:#e6edf3; --muted:#8d96a0; --ok:#3fb950; --bad:#f85149;
    --line:#30363d; }}
}}
body {{ background:var(--bg); color:var(--fg); font:14px/1.5 system-ui,sans-serif;
  margin:0 auto; padding:24px 16px; max-width:960px; }}
h1 {{ font-size:20px; margin:0 0 4px; }}
.meta {{ color:var(--muted); margin-bottom:16px; }}
.verdict {{ font-weight:700; color:var(--{"ok" if summary["ok"] else "bad"}); }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; }}
th,td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line);
  white-space:nowrap; }}
td:nth-child(n+2):nth-child(-n+5) {{ font-variant-numeric:tabular-nums; }}
tr.bad td:first-child {{ color:var(--bad); font-weight:600; }}
tr.ok svg {{ color:var(--ok); }} tr.bad svg {{ color:var(--bad); }}
</style></head><body>
<h1>Firmware validation · <span class="verdict">{verdict}</span></h1>
<div class="meta">build {html.escape(str(summary["build"]))} · firmware
{html.escape(summary["firmware"])} · commit {html.escape(summary["git_sha"][:10])} ·
{html.escape(summary["timestamp"])}</div>
<div class="wrap"><table>
<thead><tr><th>Suite</th><th>Passed</th><th>Failed</th><th>Skipped</th><th>Pass rate</th>
<th>Trend (last {len(history)})</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>
</body></html>
"""
