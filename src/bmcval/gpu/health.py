"""Turn the JSON emitted by ``gpu-health`` (C++/NVML) and ``dcgmi diag -j`` into CheckSuites."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..checks import CheckSuite, Status


def nvml_health(path: Path) -> CheckSuite:
    """``gpu-health --json`` already evaluates thresholds; this just re-keys it per GPU."""
    data = json.loads(path.read_text())
    suite = CheckSuite("gpu.health")
    suite.properties["driver"] = data.get("driver_version", "?")
    suite.properties["nvml"] = data.get("nvml_version", "?")
    for gpu in data.get("gpus", []):
        prefix = f"gpu{gpu.get('index')} {gpu.get('name', '')}".strip()
        for c in gpu.get("checks", []):
            check = suite.add(f"{prefix}: {c['name']}", c["status"], c.get("message", ""))
            check.classname = f"gpu.health.{gpu.get('pci_bus_id', gpu.get('index'))}"
    if not suite.checks:
        suite.add("gpus detected", Status.ERROR, "gpu-health reported no GPUs")
    return suite


_DCGM_STATUS = {
    "pass": Status.PASS,
    "fail": Status.FAIL,
    "warn": Status.WARN,
    "warning": Status.WARN,
    "skip": Status.SKIP,
    "not run": Status.SKIP,
    "error": Status.ERROR,
}


def _walk(node: Any, path: tuple[str, ...] = ()):
    """Yield (path, dict) for every dict in a nested JSON structure."""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, (*path, str(key)))
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, path)


def dcgm_diag(path: Path) -> CheckSuite:
    """Parse ``dcgmi diag -r N -j``.

    The JSON layout has changed across DCGM major versions, so rather than bind
    to one schema we look for any object that has a test name and a status.
    """
    data = json.loads(path.read_text())
    suite = CheckSuite("gpu.dcgm_diag")
    seen: set[str] = set()
    for _, obj in _walk(data):
        name = obj.get("test_name") or obj.get("name")
        raw = obj.get("status") or obj.get("result")
        if not isinstance(name, str) or not isinstance(raw, str):
            continue
        status = _DCGM_STATUS.get(raw.strip().lower())
        if status is None:
            continue
        gpu = obj.get("gpu_id", obj.get("entity_id"))
        label = f"{name} (gpu {gpu})" if gpu is not None else name
        if label in seen:
            continue
        seen.add(label)
        info = obj.get("info") or obj.get("warnings") or obj.get("error") or ""
        if isinstance(info, list):
            info = "; ".join(
                str(i.get("message", i)) if isinstance(i, dict) else str(i) for i in info
            )
        suite.add(label, status, str(info)[:500])
    if not suite.checks:
        suite.add("dcgm diag output", Status.ERROR, "no test results found in DCGM JSON")
    return suite
