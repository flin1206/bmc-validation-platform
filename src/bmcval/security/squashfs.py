"""Locate and carve SquashFS filesystems out of a raw flash image.

OpenBMC ``*.static.mtd`` images are a flat concatenation of U-Boot, kernel
FIT image, read-only rootfs (SquashFS) and a writable overlay (JFFS2/UBI).
Partition offsets differ per machine, so instead of hard-coding a layout we
scan for the SquashFS superblock and trust its own ``bytes_used`` field.

SquashFS 4.0 superblock (little endian), offsets used here:
    0   u32 magic        'hsqs'
    12  u32 block_size   power of two, 4 KiB .. 1 MiB
    20  u16 compression  1=gzip 2=lzma 3=lzo 4=xz 5=lz4 6=zstd
    22  u16 block_log    log2(block_size)
    28  u16 s_major      4
    40  u64 bytes_used   size of the filesystem image
"""

from __future__ import annotations

import mmap
import struct
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"hsqs"
SUPERBLOCK_SIZE = 96
COMPRESSION = {1: "gzip", 2: "lzma", 3: "lzo", 4: "xz", 5: "lz4", 6: "zstd"}


@dataclass(frozen=True)
class SquashfsHit:
    offset: int
    size: int
    block_size: int
    compression: str

    @property
    def end(self) -> int:
        return self.offset + self.size


def parse_superblock(buf: bytes | mmap.mmap, offset: int, total: int) -> SquashfsHit | None:
    """Validate a candidate superblock; returns None for false-positive magic matches."""
    if offset + SUPERBLOCK_SIZE > total:
        return None
    sb = bytes(buf[offset : offset + SUPERBLOCK_SIZE])
    if sb[:4] != MAGIC:
        return None
    block_size = struct.unpack_from("<I", sb, 12)[0]
    compression, block_log = struct.unpack_from("<HH", sb, 20)
    s_major = struct.unpack_from("<H", sb, 28)[0]
    bytes_used = struct.unpack_from("<Q", sb, 40)[0]
    if s_major != 4:
        return None
    if block_size < 4096 or block_size > 1 << 20 or block_size != 1 << block_log:
        return None
    if compression not in COMPRESSION:
        return None
    if bytes_used < SUPERBLOCK_SIZE or offset + bytes_used > total:
        return None
    return SquashfsHit(offset, bytes_used, block_size, COMPRESSION[compression])


def find_squashfs(image: Path) -> list[SquashfsHit]:
    hits: list[SquashfsHit] = []
    with image.open("rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        total = len(mm)
        pos = mm.find(MAGIC)
        while pos != -1:
            hit = parse_superblock(mm, pos, total)
            if hit:
                hits.append(hit)
                pos = mm.find(MAGIC, hit.end)  # skip over the filesystem body
            else:
                pos = mm.find(MAGIC, pos + 1)
    return hits


def carve(image: Path, hit: SquashfsHit, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with image.open("rb") as src, out.open("wb") as dst:
        src.seek(hit.offset)
        remaining = hit.size
        while remaining:
            chunk = src.read(min(remaining, 1 << 20))
            if not chunk:
                raise OSError(f"unexpected EOF while carving {image} at {hit.offset}")
            dst.write(chunk)
            remaining -= len(chunk)
    return out
