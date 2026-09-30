#!/usr/bin/env bash
# Boot one fetched firmware build under QEMU, run the full functional suite
# (including destructive tests), write JUnit, and always stop QEMU.
#
#   scripts/validate-build.sh build/images/romulus/1754 [pytest args...]
#
# Running this over several consecutive builds is how a regression is bisected
# to the build that introduced it.
set -uo pipefail

DIR=${1:?usage: $0 <image-dir> [pytest args...]}
shift
PROFILE=${PROFILE:-qemu-romulus}
label=$(basename "$(dirname "$DIR")")-$(basename "$DIR")
out=reports/builds/$label
mkdir -p "$out"

image=$(find "$DIR" -maxdepth 1 -name '*.static.mtd' | head -1)
update=$(find "$DIR" -maxdepth 1 -name '*.static.mtd.tar' | head -1)
[[ -n "$image" ]] || { echo "no .static.mtd in $DIR"; exit 2; }

bmcval boot --profile "$PROFILE" --image "$image" --workdir "$out/qemu" || exit 2
trap 'bmcval stop --workdir "$out/qemu"' EXIT

UPDATE_IMAGE="$update" pytest tests/functional --profile "$PROFILE" --run-destructive \
  -p no:cacheprovider -q -W ignore::urllib3.exceptions.InsecureRequestWarning \
  --junitxml="$out/functional.xml" -o junit_suite_name="bmc.$label" "$@"
rc=$?
echo "$label: pytest exit $rc"
exit $rc
