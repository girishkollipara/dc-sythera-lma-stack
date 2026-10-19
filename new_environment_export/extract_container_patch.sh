#!/usr/bin/env bash
# Extract the VP container name-fix source files from the ECR images.
#
# WHY THIS EXISTS: `vp_fix_backups/container_patch/` is referenced by
# VP_CHANGES_APPLIED.md, permanent_patches/README.md and NEW_ENVIRONMENT_PLAYBOOK.md,
# but the directory does not exist in the repo. The patched files survive ONLY inside
# the ECR image `name-fix-v1`. This script recovers them.
#
# PREREQUISITE: Docker Desktop must be running.
#   Run:  ./extract_container_patch.sh
#
# Output: container_patch/  (patched files, .orig files, and a diff)

set -euo pipefail

REGION=us-east-1
ACCT=528757797189
REPO=lmasa-virtualparticipantstack-1iavizt1439r2-imagerepo-l3yrnekcbuxx
REGISTRY="${ACCT}.dkr.ecr.${REGION}.amazonaws.com"
# name-fix-v7 is what task definition :15 runs (2026-10-19). It contains the v1
# name-entry layer plus the auto-leave layer (details.js, scribe.js, teams.js).
PATCHED="${REGISTRY}/${REPO}:name-fix-v7"
ORIGINAL="${REGISTRY}/${REPO}:pre-name-fix-20260514"
OUT="$(cd "$(dirname "$0")" && pwd)/container_patch"

mkdir -p "$OUT"

echo "==> docker login to ECR"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

pull_and_copy () {
  local image="$1" suffix="$2"
  echo "==> pulling $image  (large - chromium base, expect several minutes)"
  docker pull "$image"
  local cid
  cid=$(docker create "$image")
  trap 'docker rm -f "$cid" >/dev/null 2>&1 || true' RETURN

  echo "==> locating handler files inside the image"
  # The VP app lives under /app in the LMA 0.3.2 container.
  docker export "$cid" | tar -tf - 2>/dev/null \
    | grep -E '^srv/dist/(teams|zoom|name-entry|details|scribe)\.js$' \
    | grep -v node_modules > "$OUT/paths${suffix}.txt" || true
  echo "    found $(wc -l < "$OUT/paths${suffix}.txt") candidate files"

  while read -r p; do
    [ -z "$p" ] && continue
    local dest="$OUT/$(basename "$p")${suffix}"
    docker cp "$cid:/$p" "$dest" 2>/dev/null && echo "    extracted $(basename "$p")${suffix}"
  done < "$OUT/paths${suffix}.txt"

  docker rm -f "$cid" >/dev/null
  trap - RETURN
}

pull_and_copy "$PATCHED"  ""
pull_and_copy "$ORIGINAL" ".orig"

echo "==> diffing patched vs original"
for f in teams.js zoom.js details.js scribe.js; do
  if [ -f "$OUT/$f" ] && [ -f "$OUT/$f.orig" ]; then
    diff -u "$OUT/$f.orig" "$OUT/$f" > "$OUT/$f.patch" || true
    echo "    $f.patch  ($(grep -c '^[+-]' "$OUT/$f.patch" || true) changed lines)"
  fi
done

echo
echo "Done. Files in: $OUT"
echo "Expected for name-fix-v7:"
echo "  zoom.js       name-entry import + call"
echo "  teams.js      name-entry import + call, badge hardening, no-humans/idle auto-leave"
echo "  details.js    BOT_NAME_* / NO_HUMANS_GRACE_SECONDS / IDLE_TIMEOUT_MINUTES / WATCHDOG_CHECK_SECONDS"
echo "  scribe.js     lastSpeechAt for the idle check"
echo "  name-entry.js new file (no .orig counterpart)"
