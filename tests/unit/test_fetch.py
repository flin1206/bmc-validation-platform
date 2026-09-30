from bmcval.fetch import pick_artifacts

D = "openbmc/build/tmp/deploy/images"


def test_romulus_stable_mtd_only():
    paths = [
        f"{D}/romulus/obmc-phosphor-image-romulus.static.mtd",
        f"{D}/romulus/obmc-phosphor-image-romulus-20260930025128.spdx.json",
        f"{D}/romulus/obmc-phosphor-image-romulus.spdx.json",  # symlink, not served
        f"{D}/romulus/obmc-phosphor-image-romulus-20260930025128.static.mtd.tar",
        f"{D}/romulus/obmc-phosphor-debug-tarball-romulus-20260930025128.spdx.json",
    ]
    got = pick_artifacts(paths, "romulus")
    assert got["mtd"].filename == "obmc-phosphor-image-romulus.static.mtd"
    assert got["spdx"].filename == "obmc-phosphor-image-romulus-20260930025128.spdx.json"
    assert got["update"].filename.endswith("-20260930025128.static.mtd.tar")


def test_timestamped_mtd_preferred_over_stable_symlink():
    paths = [
        f"{D}/gb200nvl-obmc/obmc-phosphor-image-gb200nvl-obmc.static.mtd",
        f"{D}/gb200nvl-obmc/obmc-phosphor-image-gb200nvl-obmc-20260930025112.static.mtd",
        f"{D}/gb200nvl-obmc/obmc-phosphor-image-gb200nvl-obmc-20260930025112.static.mtd.all.tar",
    ]
    got = pick_artifacts(paths, "gb200nvl-obmc")
    assert got["mtd"].filename == "obmc-phosphor-image-gb200nvl-obmc-20260930025112.static.mtd"
    assert "update" not in got  # .all.tar is not the update tarball
