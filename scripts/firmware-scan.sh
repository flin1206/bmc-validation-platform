#!/usr/bin/env bash
# Produce an SBOM and a Grype vulnerability report for one firmware build.
#
#   scripts/firmware-scan.sh build/images/romulus/1754 reports/security/candidate
#
# SBOM source preference:
#   1. Yocto's build-time SPDX (exact recipe names, versions, CPEs, patches applied)
#   2. Syft over the extracted SquashFS rootfs (fallback; OpenBMC ships no package
#      database, so Syft can only use binary classifiers and misses most packages)
set -euo pipefail

IMAGE_DIR=${1:?usage: $0 <image-dir> <out-dir>}
OUT=${2:?usage: $0 <image-dir> <out-dir>}
mkdir -p "$OUT"

spdx=$(find "$IMAGE_DIR" -maxdepth 1 -name '*.spdx.json' | head -n1 || true)
mtd=$(find "$IMAGE_DIR" -maxdepth 1 -name '*.static.mtd' | head -n1 || true)
manifest=$(find "$IMAGE_DIR" -maxdepth 1 -name '*.manifest' | head -n1 || true)

# The image manifest lists what is actually installed in the rootfs; cve-diff
# uses it to drop build-host (-native) packages that the SPDX also describes.
[[ -n "$manifest" ]] && cp "$manifest" "$OUT/image.manifest"

if [[ -n "$spdx" ]]; then
  echo "SBOM source: Yocto SPDX ($spdx)"
  cp "$spdx" "$OUT/sbom.spdx.json"
  echo yocto-spdx > "$OUT/sbom-source"
elif [[ -n "$mtd" ]]; then
  echo "SBOM source: syft over extracted rootfs (no SPDX available)"
  bmcval extract-rootfs "$mtd" --out "$OUT/rootfs"
  syft scan "dir:$OUT/rootfs" -o "spdx-json=$OUT/sbom.spdx.json" -q
  echo syft-rootfs > "$OUT/sbom-source"
else
  echo "no SPDX or .static.mtd in $IMAGE_DIR" >&2
  exit 2
fi

# Always also run syft on the rootfs when we have the image: its binary
# classifiers catch statically linked or vendored components that no recipe declares.
if [[ -n "$mtd" && ! -d "$OUT/rootfs" ]]; then
  bmcval extract-rootfs "$mtd" --out "$OUT/rootfs" >/dev/null
fi
if [[ -d "$OUT/rootfs" ]]; then
  syft scan "dir:$OUT/rootfs" -o "syft-json=$OUT/syft-rootfs.json" -q
fi

grype "sbom:$OUT/sbom.spdx.json" -o json --file "$OUT/grype.json" -q
python3 - "$OUT/grype.json" <<'EOF'
import collections, json, sys
m = json.load(open(sys.argv[1]))["matches"]
c = collections.Counter(x["vulnerability"]["severity"] for x in m)
pk = len({x["artifact"]["name"] for x in m})
print(f"grype: {len(m)} matches across {pk} packages: " + ", ".join(f"{k}={v}" for k, v in c.most_common()))
EOF
