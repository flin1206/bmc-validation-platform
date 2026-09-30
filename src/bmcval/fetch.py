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
    return {
        "mtd": re.compile(rf"{stem}\.static\.mtd$"),
        "spdx": re.compile(rf"{stem}-\d{{14}}\.spdx\.json$"),
        "manifest": re.compile(rf"{stem}-\d{{14}}\.manifest$"),
        "update": re.compile(rf"{stem}-\d{{14}}\.static\.mtd\.tar$"),
    }


def job_url(jenkins: str, machine: str) -> str:
    return f"{jenkins.rstrip('/')}/label=docker-builder,target={machine}"


def resolve(jenkins: str, machine: str, build: str) -> tuple[int, list[Artifact]]:
    url = f"{job_url(jenkins, machine)}/{build}/api/json?tree=number,artifacts[relativePath]"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    found: dict[str, Artifact] = {}
    for art in data.get("artifacts", []):
        for kind, pat in _patterns(machine).items():
            if pat.search(art["relativePath"]):
                found[kind] = Artifact(kind, art["relativePath"])
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
