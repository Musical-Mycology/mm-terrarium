#!/usr/bin/env bash
# Build the terrarium-dev image from docker/pins.env (docker/README.md).
#   docker/build.sh                 -> ghcr.io/musical-mycology/terrarium-dev:local
#   docker/build.sh --tag sha-XXXX  -> a named tag (Phase 2 CI)
#   docker/build.sh --dry-run       -> print the build command only
# Keep this Bash 3.2 compatible.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

TD_IMAGE_REPO="${TD_IMAGE_REPO:-ghcr.io/musical-mycology/terrarium-dev}"
TD_PINS_FILE="${TD_PINS_FILE:-docker/pins.env}"
die() { echo "build.sh: $*" >&2; exit 1; }

tag=local; dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tag)     [ $# -ge 2 ] || die "--tag needs a value"; tag="$2"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ -f "$TD_PINS_FILE" ] || die "no pins file at $TD_PINS_FILE"
set -a
# shellcheck disable=SC1090
. "$TD_PINS_FILE"
set +a

cmd=(docker buildx build --platform linux/amd64 --load
     -f docker/Dockerfile -t "$TD_IMAGE_REPO:$tag")
for key in BASE_IMAGE ARCO_REPO ARCO_SHA O2_REPO O2_SHA LUXAETERNA_REPO LUXAETERNA_SHA; do
  [ -n "${!key:-}" ] || die "$TD_PINS_FILE is missing $key"
  cmd+=(--build-arg "$key=${!key}")
done
cmd+=(.)

if [ "$dry" -eq 1 ]; then
  printf '%q ' "${cmd[@]}"
  echo
  exit 0
fi
exec "${cmd[@]}"
