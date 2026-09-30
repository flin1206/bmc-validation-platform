"""Boot, supervise and tear down an OpenBMC image under QEMU.

The emulated flash is writable, so every boot starts from a fresh copy of the
pristine ("golden") image. Otherwise a test that creates a user or flashes
firmware would leak state into the next run and make failures non-reproducible.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .profiles import Profile

READY_MARKERS = (b"login:",)
PANIC_MARKERS = (b"Kernel panic", b"Unable to mount root fs", b"U-Boot SPL fail")


class BootError(RuntimeError):
    pass


def find_image(image_dir: Path, pattern: str) -> Path:
    matches = sorted(image_dir.rglob(pattern), key=lambda p: p.stat().st_mtime)
    if not matches:
        raise FileNotFoundError(f"no image matching {pattern!r} in {image_dir}")
    return matches[-1]


@dataclass
class QemuBmc:
    profile: Profile
    image: Path
    workdir: Path

    @property
    def pidfile(self) -> Path:
        return self.workdir / "qemu.pid"

    @property
    def console_log(self) -> Path:
        return self.workdir / "console.log"

    @property
    def flash(self) -> Path:
        return self.workdir / "flash.mtd"

    def command(self) -> list[str]:
        q = self.profile.qemu
        fwd = q.get("hostfwd", {})
        netdev = ",".join(
            [
                "user",
                f"hostfwd=tcp:127.0.0.1:{fwd.get('ssh', 2222)}-:22",
                f"hostfwd=tcp:127.0.0.1:{fwd.get('https', 2443)}-:443",
                f"hostfwd=udp:127.0.0.1:{fwd.get('ipmi_udp', 2623)}-:623",
                "hostname=qemu",
            ]
        )
        return [
            q.get("binary", "qemu-system-arm"),
            "-M", q["machine"],
            "-m", str(q.get("memory_mb", 256)),
            "-nographic",
            "-drive", f"file={self.flash},format=raw,if=mtd",
            "-net", "nic",
            "-net", netdev,
        ]  # fmt: skip

    def is_running(self) -> bool:
        pid = self._read_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def start(self) -> int:
        if self.is_running():
            raise BootError(f"QEMU already running (pid {self._read_pid()})")
        self.workdir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.image, self.flash)
        log = self.console_log.open("wb")
        proc = subprocess.Popen(
            self.command(),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # survive the Jenkins step that launched it
        )
        self.pidfile.write_text(str(proc.pid))
        return proc.pid

    def wait_for_console(self, timeout: float) -> float:
        """Block until the serial console shows a login prompt. Returns seconds taken."""
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            data = self.console_log.read_bytes() if self.console_log.exists() else b""
            for marker in PANIC_MARKERS:
                if marker in data:
                    raise BootError(f"boot failure detected on console: {marker.decode()}")
            if any(m in data for m in READY_MARKERS):
                return time.monotonic() - start
            if not self.is_running():
                raise BootError("QEMU exited before reaching the login prompt")
            time.sleep(2)
        raise BootError(f"no login prompt after {timeout}s; see {self.console_log}")

    def stop(self, grace: float = 10) -> None:
        pid = self._read_pid()
        if pid is None:
            return
        try:
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                os.kill(pid, 0)
                time.sleep(0.5)
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        finally:
            self.pidfile.unlink(missing_ok=True)

    def _read_pid(self) -> int | None:
        try:
            return int(self.pidfile.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None
