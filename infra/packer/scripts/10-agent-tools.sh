#!/usr/bin/env bash
# Everything a 'qemu' + 'security' Jenkins agent needs.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
  openjdk-17-jre-headless git curl ca-certificates \
  python3 python3-venv python3-pip \
  qemu-system-arm ipmitool squashfs-tools \
  openscap-scanner lynis

SYFT_VERSION=${SYFT_VERSION:-1.52.0}
GRYPE_VERSION=${GRYPE_VERSION:-0.119.0}
curl -sSfL "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_amd64.tar.gz" \
  | tar -xz -C /usr/local/bin syft
curl -sSfL "https://github.com/anchore/grype/releases/download/v${GRYPE_VERSION}/grype_${GRYPE_VERSION}_linux_amd64.tar.gz" \
  | tar -xz -C /usr/local/bin grype

# SCAP content: Ubuntu's archive ships none for 24.04, so take the upstream
# ComplianceAsCode release that includes the ubuntu2404 datastream.
SSG_VERSION=${SSG_VERSION:-0.1.76}
mkdir -p /usr/share/xml/scap/ssg/content
curl -sSfL "https://github.com/ComplianceAsCode/content/releases/download/v${SSG_VERSION}/scap-security-guide-${SSG_VERSION}.zip" \
  -o /tmp/ssg.zip
python3 - <<'EOF'
import zipfile
with zipfile.ZipFile("/tmp/ssg.zip") as z:
    for n in z.namelist():
        if n.endswith("ssg-ubuntu2404-ds.xml"):
            open("/usr/share/xml/scap/ssg/content/ssg-ubuntu2404-ds.xml", "wb").write(z.read(n))
EOF
rm /tmp/ssg.zip

useradd --system --create-home --shell /bin/bash jenkins
