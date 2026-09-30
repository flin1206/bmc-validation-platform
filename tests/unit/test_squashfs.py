import struct

from bmcval.security import squashfs


def superblock(bytes_used: int, block_log: int = 17, compression: int = 4, major: int = 4) -> bytes:
    sb = bytearray(96)
    sb[0:4] = b"hsqs"
    struct.pack_into("<I", sb, 12, 1 << block_log)
    struct.pack_into("<HH", sb, 20, compression, block_log)
    struct.pack_into("<H", sb, 28, major)
    struct.pack_into("<Q", sb, 40, bytes_used)
    return bytes(sb)


def build_image(tmp_path, parts):
    path = tmp_path / "flash.mtd"
    path.write_bytes(b"".join(parts))
    return path


def test_finds_rootfs_at_offset(tmp_path):
    fs = superblock(4096) + b"\xaa" * (4096 - 96)
    image = build_image(tmp_path, [b"\xff" * 0x1000, fs, b"\xff" * 0x800])
    hits = squashfs.find_squashfs(image)
    assert len(hits) == 1
    assert hits[0].offset == 0x1000
    assert hits[0].size == 4096
    assert hits[0].compression == "xz"


def test_rejects_false_positive_magic(tmp_path):
    # "hsqs" appearing inside kernel data with nonsense fields must be ignored.
    junk = b"hsqs" + b"\x00" * 200
    bad_version = superblock(4096, major=3) + b"\x00" * 4000
    image = build_image(tmp_path, [junk, bad_version])
    assert squashfs.find_squashfs(image) == []


def test_rejects_size_past_end_of_image(tmp_path):
    image = build_image(tmp_path, [superblock(1 << 30) + b"\x00" * 100])
    assert squashfs.find_squashfs(image) == []


def test_multiple_filesystems_and_carve(tmp_path):
    small = superblock(512) + b"\x01" * (512 - 96)
    big = superblock(8192) + b"\x02" * (8192 - 96)
    image = build_image(tmp_path, [b"\x00" * 64, small, b"\x00" * 64, big])
    hits = squashfs.find_squashfs(image)
    assert [h.size for h in hits] == [512, 8192]
    out = squashfs.carve(image, hits[1], tmp_path / "rootfs.squashfs")
    data = out.read_bytes()
    assert len(data) == 8192
    assert data[:4] == b"hsqs"
    assert data[-1:] == b"\x02"
