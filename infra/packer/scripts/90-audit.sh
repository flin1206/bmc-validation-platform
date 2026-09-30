#!/usr/bin/env bash
# Audit the image we just built. Evidence lands in /var/log/bmcval-audit and is
# downloaded by Packer into reports/baseos/, where `bmcval cis` turns it into JUnit.
set -uxo pipefail
OUT=/var/log/bmcval-audit
mkdir -p "$OUT"

DS=/usr/share/xml/scap/ssg/content/ssg-ubuntu2404-ds.xml
PROFILE=xccdf_org.ssgproject.content_profile_cis_level1_server

# oscap exits 2 when any rule fails; that is a result, not an error.
oscap xccdf eval --profile "$PROFILE" \
  --results "$OUT/cis-results.xml" --report "$OUT/cis-report.html" "$DS"
rc=$?
[[ $rc -eq 0 || $rc -eq 2 ]] || { echo "oscap failed to run (exit $rc)"; exit 1; }

lynis audit system --quick --no-colors --report-file "$OUT/lynis-report.dat" > "$OUT/lynis.log"

index=$(grep -oP '^hardening_index=\K\d+' "$OUT/lynis-report.dat")
echo "Lynis hardening index: $index (minimum ${MIN_HARDENING_INDEX})"
if (( index < MIN_HARDENING_INDEX )); then
  echo "hardening index below minimum; refusing to produce this image"
  exit 1
fi
chmod -R a+r "$OUT"
