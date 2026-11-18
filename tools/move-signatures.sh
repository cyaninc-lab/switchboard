#!/usr/bin/env bash
# Move a release's .sig files off its releases page onto the `signatures` branch of cyaninc-lab/switchboard
# (signatures/README.md there says why). Installed apps don't need them: latest.json carries the signatures.
#   tools/move-signatures.sh <release repo> <release id> <version>     e.g. cyaninc-lab/switchboard-releases 123 0.1.14
# GH_TOKEN deletes the release's assets; HOME_TOKEN (default: GH_TOKEN) writes to cyaninc-lab/switchboard.
set -euo pipefail
REL_REPO=$1 RID=$2 V=$3 HOME_REPO=cyaninc-lab/switchboard
tmp=$(mktemp -d)
home() { GH_TOKEN=${HOME_TOKEN:-${GH_TOKEN:-}} gh "$@"; }  # an empty GH_TOKEN falls back to gh's own login
gh api "repos/$REL_REPO/releases/$RID/assets" --jq '.[] | select(.name | endswith(".sig")) | "\(.id) \(.name)"' | while read -r id name; do
  gh api -H "Accept: application/octet-stream" "repos/$REL_REPO/releases/assets/$id" > "$tmp/$name"
  path="v$V/$name"
  sha=$(home api "repos/$HOME_REPO/contents/$path?ref=signatures" --jq .sha 2>/dev/null || true)  # set on a re-run
  home api -X PUT "repos/$HOME_REPO/contents/$path" -f branch=signatures -f message="Signature: $path" \
    -f content="$(base64 -w0 < "$tmp/$name")" ${sha:+-f sha="$sha"} > /dev/null
  # only drop it from the release once it's safely stored
  home api "repos/$HOME_REPO/contents/$path?ref=signatures" --jq .size > /dev/null
  gh api -X DELETE "repos/$REL_REPO/releases/assets/$id"
  echo "moved $name -> signatures/$path"
done
