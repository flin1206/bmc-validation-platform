"""Platform profiles: one YAML per target (QEMU machine or real board).

A profile declares *what the platform can do* so the same test suite runs on
an emulator and on hardware; tests call ``profile.require("host_power")`` and
are skipped with a clear reason instead of failing on missing features.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Profile:
    name: str
    description: str = ""
    qemu: dict[str, Any] = field(default_factory=dict)
    endpoints: dict[str, Any] = field(default_factory=dict)
    credentials: dict[str, str] = field(default_factory=dict)
    capabilities: set[str] = field(default_factory=set)
    timeouts: dict[str, float] = field(default_factory=dict)
    sensors: dict[str, Any] = field(default_factory=dict)

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def timeout(self, key: str, default: float) -> float:
        return float(self.timeouts.get(key, default))


def available_profiles() -> list[str]:
    root = resources.files("bmcval") / "data"
    return sorted(p.name.removesuffix(".yaml") for p in root.iterdir() if p.name.endswith(".yaml"))


def load_profile(name_or_path: str) -> Profile:
    path = Path(name_or_path)
    if path.suffix in (".yaml", ".yml") and path.exists():
        text = path.read_text()
    else:
        res = resources.files("bmcval") / "data" / f"{name_or_path}.yaml"
        if not res.is_file():
            raise ValueError(
                f"unknown profile {name_or_path!r}; available: {', '.join(available_profiles())}"
            )
        text = res.read_text()
    data = yaml.safe_load(text) or {}
    unknown = set(data) - set(Profile.__dataclass_fields__)
    if unknown:
        raise ValueError(f"profile {name_or_path!r} has unknown keys: {sorted(unknown)}")
    data["capabilities"] = set(data.get("capabilities", []))
    return Profile(**data)
