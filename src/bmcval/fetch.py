"""Download firmware build artifacts from the upstream OpenBMC Jenkins.

Upstream publishes each machine's image together with its Yocto-generated
SPDX SBOM, package manifest and signed update tarball. Only the ``.static.mtd``
is published under a stable name; the rest are timestamped
(``obmc-phosphor-image-romulus-20260930025128.spdx.json``), and the stable
names are symlinks Jenkins will not serve. So we resolve real names through
the Jenkins JSON API instead of guessing URLs.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import requests

DEFAULT_JENKINS = "https://jenkins.openbmc.org/job/latest-master"


@dataclass
class Artifact:
    kind: str  # mtd | spdx | manifest | update
    relative_path: str

    @property
    def filename(self) -> str:
        return self.relative_path.rsplit("/", 1)[-1]


def _patterns(machine: str) -> dict[str, re.Pattern]:
    stem = re.escape(f"obmc-phosphor-image-{machine}")
    ts = r"(?P<ts>-\d{14})"
    return {
        # Some machines publish the flash image only under a stable name (romulus),
        # others only timestamped (gb200nvl-obmc, where the stable name is a symlink).
        "mtd": re.compile(rf"{stem}{ts}?\.static\.mtd$"),
        "spdx": re.compile(rf"{stem}{ts}\.spdx\.json$"),
        "manifest": re.compile(rf"{stem}{ts}\.manifest$"),
        "update": re.compile(rf"{stem}{ts}\.static\.mtd\.tar$"),
    }


def pick_artifacts(paths: list[str], machine: str) -> dict[str, Artifact]:
    """Choose one artifact per kind, preferring timestamped (real file) over stable names."""
    best: dict[str, tuple[bool, Artifact]] = {}
    for path in paths:
        for kind, pat in _patterns(machine).items():
            m = pat.search(path)
            if not m:
                continue
            timestamped = m.group("ts") is not None
            if kind not in best or (timestamped and not best[kind][0]):
                best[kind] = (timestamped, Artifact(kind, path))
    return {kind: art for kind, (_, art) in best.items()}


def job_url(jenkins: str, machine: str) -> str:
    return f"{jenkins.rstrip('/')}/label=docker-builder,target={machine}"


def resolve(jenkins: str, machine: str, build: str) -> tuple[int, list[Artifact]]:
    url = f"{job_url(jenkins, machine)}/{build}/api/json?tree=number,artifacts[relativePath]"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    found = pick_artifacts([a["relativePath"] for a in data.get("artifacts", [])], machine)
    if "mtd" not in found:
        raise FileNotFoundError(f"build {data.get('number')} of {machine} has no .static.mtd")
    return data["number"], list(found.values())


def download(jenkins: str, machine: str, build: str, dest: Path, kinds: set[str]) -> Path:
    number, artifacts = resolve(jenkins, machine, build)
    out = dest / machine / str(number)
    out.mkdir(parents=True, exist_ok=True)
    meta = {"machine": machine, "build": number, "source": job_url(jenkins, machine), "files": {}}
    for art in artifacts:
        if art.kind not in kinds:
            continue
        target = out / art.filename
        if not target.exists():
            url = f"{job_url(jenkins, machine)}/{number}/artifact/{art.relative_path}"
            with requests.get(url, stream=True, timeout=600) as r:
                r.raise_for_status()
                tmp = target.with_suffix(target.suffix + ".part")
                with tmp.open("wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
                tmp.rename(target)
        meta["files"][art.kind] = {"name": art.filename, "sha256": _sha256(target)}
    (out / "metadata.json").write_text(json.dumps(meta, indent=2))
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
