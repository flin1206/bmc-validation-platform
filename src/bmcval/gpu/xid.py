"""Scan kernel logs for NVIDIA Xid events and triage them into node actions.

An Xid is the driver's report of a GPU error. The raw number alone is not
actionable for an on-call engineer; what matters is *who is likely at fault*
(application, driver/firmware, hardware) and *what to do with the node*.
The mapping below follows NVIDIA's Xid catalog, simplified to three actions.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from ..checks import CheckSuite, Status


class Action(IntEnum):
    # Ordered by severity so max() picks the worst disposition.
    NONE = 0
    CHECK_APP = 1
    RESET_GPU = 2
    DRAIN_NODE = 3


@dataclass(frozen=True)
class XidInfo:
    title: str
    origin: str  # application | driver | hardware | informational
    action: Action


XID_CATALOG: dict[int, XidInfo] = {
    13: XidInfo("Graphics engine exception", "application", Action.CHECK_APP),
    31: XidInfo("GPU memory page fault", "application", Action.CHECK_APP),
    32: XidInfo("Invalid or corrupted push buffer stream", "driver", Action.RESET_GPU),
    43: XidInfo("GPU stopped processing", "application", Action.CHECK_APP),
    45: XidInfo("Preemptive cleanup after previous errors", "informational", Action.NONE),
    48: XidInfo("Double-bit ECC error", "hardware", Action.RESET_GPU),
    61: XidInfo("Internal micro-controller breakpoint/warning", "driver", Action.RESET_GPU),
    62: XidInfo("Internal micro-controller halt", "driver", Action.RESET_GPU),
    63: XidInfo("ECC page retirement / row remap recorded", "informational", Action.NONE),
    64: XidInfo("ECC page retirement / row remap recording failure", "hardware", Action.DRAIN_NODE),
    74: XidInfo("NVLink error", "hardware", Action.DRAIN_NODE),
    79: XidInfo("GPU has fallen off the bus", "hardware", Action.DRAIN_NODE),
    92: XidInfo("High single-bit ECC error rate", "hardware", Action.RESET_GPU),
    94: XidInfo("Contained ECC error", "hardware", Action.RESET_GPU),
    95: XidInfo("Uncontained ECC error", "hardware", Action.DRAIN_NODE),
    119: XidInfo("GSP RPC timeout", "driver", Action.RESET_GPU),
    120: XidInfo("GSP error", "driver", Action.RESET_GPU),
}
UNKNOWN = XidInfo("Unrecognised Xid", "unknown", Action.RESET_GPU)

# NVRM: Xid (PCI:0000:3b:00): 79, pid=0, name=..., GPU has fallen off the bus.
# NVRM: Xid (PCI:0000:01:00): 13, Graphics Exception: ...   (older drivers, no pid)
XID_RE = re.compile(
    r"NVRM: Xid \(PCI:(?P<bdf>[0-9A-Fa-f]{4}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}(?:\.\d)?)\):\s*"
    r"(?P<code>\d+),\s*(?:pid=(?P<pid>[^,\s]+),\s*)?(?:name=(?P<name>[^,]*),\s*)?(?P<msg>.*)$"
)


@dataclass(frozen=True)
class XidEvent:
    bdf: str
    code: int
    pid: str | None
    process: str | None
    message: str
    line_no: int

    @property
    def info(self) -> XidInfo:
        return XID_CATALOG.get(self.code, UNKNOWN)


def parse(lines) -> list[XidEvent]:
    events = []
    for no, line in enumerate(lines, 1):
        m = XID_RE.search(line)
        if m:
            events.append(
                XidEvent(
                    bdf=m["bdf"].lower(),
                    code=int(m["code"]),
                    pid=m["pid"],
                    process=(m["name"] or None),
                    message=m["msg"].strip(),
                    line_no=no,
                )
            )
    return events


def parse_file(path: Path) -> list[XidEvent]:
    with path.open(errors="replace") as fh:
        return parse(fh)


def triage(events: list[XidEvent]) -> dict[str, Action]:
    per_gpu: dict[str, Action] = defaultdict(lambda: Action.NONE)
    for e in events:
        per_gpu[e.bdf] = max(per_gpu[e.bdf], e.info.action)
    return dict(per_gpu)


def to_suite(events: list[XidEvent]) -> CheckSuite:
    suite = CheckSuite("gpu.xid")
    grouped: dict[tuple[str, int], list[XidEvent]] = defaultdict(list)
    for e in events:
        grouped[(e.bdf, e.code)].append(e)
    for (bdf, code), evs in sorted(grouped.items()):
        info = evs[0].info
        status = {
            Action.NONE: Status.PASS,
            Action.CHECK_APP: Status.WARN,
            Action.RESET_GPU: Status.FAIL,
            Action.DRAIN_NODE: Status.FAIL,
        }[info.action]
        check = suite.add(
            f"Xid {code} on {bdf}",
            status,
            f"{info.title} x{len(evs)} -> {info.action.name}",
            origin=info.origin,
            first_line=evs[0].line_no,
            sample=evs[0].message[:200],
        )
        check.classname = f"gpu.xid.{bdf}"
    dispositions = triage(events)
    suite.properties["node_action"] = max(dispositions.values(), default=Action.NONE).name
    if not events:
        suite.add("no Xid events", Status.PASS)
    return suite
