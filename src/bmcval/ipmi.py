"""ipmitool wrapper for IPMI-over-LAN (RMCP+) tests."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class IpmiResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class IpmiClient:
    host: str
    port: int
    username: str
    password: str
    cipher_suite: int = 17  # AES-CBC-128 + HMAC-SHA256, the OpenBMC default
    timeout: float = 30.0

    @staticmethod
    def available() -> bool:
        return shutil.which("ipmitool") is not None

    def run(self, *args: str) -> IpmiResult:
        cmd = [
            "ipmitool", "-I", "lanplus", "-C", str(self.cipher_suite),
            "-H", self.host, "-p", str(self.port),
            "-U", self.username, "-P", self.password,
            "-N", "5", "-R", "3",
            *args,
        ]  # fmt: skip
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        return IpmiResult(proc.returncode, proc.stdout, proc.stderr)

    def raw(self, netfn: int, cmd: int, *data: int) -> IpmiResult:
        return self.run("raw", f"0x{netfn:02x}", f"0x{cmd:02x}", *(f"0x{b:02x}" for b in data))


def parse_kv(text: str) -> dict[str, str]:
    """Parse ipmitool's ``Key : Value`` output (``mc info``, ``chassis status``...)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            if key.strip():
                out[key.strip()] = value.strip()
    return out
