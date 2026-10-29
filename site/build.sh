#!/usr/bin/env bash
# Build the Hugo prototypes in site/<theme>/ into site/<theme>/public/.
#
# Themes are not stored in the repository: each one is fetched into site/<theme>/themes/ at the pinned
# commit below. Only Hugo extended (0.146 or newer) and git are needed; no Node.js.
#
#   ./build.sh                 fetch the themes and build every prototype
#   ./build.sh hextra congo    build only these
#   ./build.sh --serve hextra  live preview of one prototype at http://localhost:1313/
#
# HUGO=/path/to/hugo ./build.sh uses another Hugo binary.
set -euo pipefail
cd "$(dirname "$0")"
HUGO=${HUGO:-hugo}

# site directory | theme directory | repository | commit (tag)
THEMES=(
  "hugo-book hugo-book https://github.com/alex-shpak/hugo-book 027be3349d20785efeb93f6773872a35497d7258 v0.15.0"
  "hextra hextra https://github.com/imfing/hextra adf732f8d97cb8e149d4aba232a449d41cd9e38c v0.13.0"
  "papermod PaperMod https://github.com/adityatelange/hugo-PaperMod 118119693d8b5ac211b939775678310973bcb232 master"
  "congo congo https://github.com/jpanther/congo 87dd58ce29e71f210d57dd356055b7b97f528ec3 v2.14.0"
)

fetch_theme() { # site theme repo commit
  local dir="$1/themes/$2"
  if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD)" = "$4" ]; then
    return
  fi
  echo "fetching $2 ($4) into $dir"
  rm -rf "$dir"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$3"
  git -C "$dir" fetch -q --depth 1 origin "$4"
  git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
}

serve=""
if [ "${1:-}" = "--serve" ]; then
  serve=1
  shift
fi
wanted=("$@")

"$HUGO" version
for entry in "${THEMES[@]}"; do
  read -r site theme repo commit tag <<<"$entry"
  if [ ${#wanted[@]} -gt 0 ] && [[ ! " ${wanted[*]} " == *" $site "* ]]; then
    continue
  fi
  fetch_theme "$site" "$theme" "$repo" "$commit"
  if [ -n "$serve" ]; then
    echo "serving $site ($theme $tag)"
    (cd "$site" && exec "$HUGO" server --disableFastRender --baseURL http://localhost:1313/)
    exit
  fi
  echo "building $site ($theme $tag)"
  (cd "$site" && "$HUGO" --minify --gc --cleanDestinationDir --logLevel warn)
done
