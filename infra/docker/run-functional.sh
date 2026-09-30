#!/usr/bin/env bash
# Boot the newest image under /images, run the functional suite, always stop QEMU.
# Reports go to /reports (mount a host directory there to keep them).
set -uo pipefail

PROFILE=${PROFILE:-qemu-romulus}
mkdir -p /reports

image=$(find /images -name '*.static.mtd' -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)
[[ -n "$image" ]] || { echo "no .static.mtd under /images (run: make fetch)"; exit 2; }
echo "image: $image"
# A signed update tarball next to the image enables the positive update test.
update=$(find "$(dirname "$image")" -name '*.static.mtd.tar' | head -1)
[[ -n "$update" ]] && export UPDATE_IMAGE="$update"

bmcval boot --profile "$PROFILE" --image "$image" --workdir /reports/qemu || exit 2
trap 'bmcval stop --workdir /reports/qemu' EXIT

pytest tests/functional -m smoke --profile "$PROFILE" \
  --junitxml=/reports/bmc-smoke.xml -o junit_suite_name=bmc.smoke "$@" || exit 1

pytest tests/functional -m 'not smoke' --profile "$PROFILE" --run-destructive \
  --junitxml=/reports/bmc-functional.xml -o junit_suite_name=bmc.functional "$@"
