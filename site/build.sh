#!/usr/bin/env bash
# Build the eMailCompare site (Hugo + PaperMod, terminal design) into site/public/.
#
# Sources: config/ (settings), content/ (home text; one page per row of ../emails.csv), layouts/ (templates),
# assets/ (emc.css, emc.js, favicons/<slug>.png, screenshots/<slug>.webp), static/ (site icon).
# Data: ../emails.csv and the status marks of the ../README.md tables. Rebuild after editing them and commit public/.
#
# The theme and Hugo's caches stay outside the repository: PaperMod is fetched at the pinned commit below into
# $EMC_CACHE (default ~/.cache/emailcompare-site). Only Hugo extended (0.146 or newer) and git are needed; no Node.js.
#
#   ./build.sh            fetch the theme and build the site into public/
#   ./build.sh --serve    live preview at http://localhost:1313/
#
# HUGO=/path/to/hugo ./build.sh uses another Hugo binary.
set -euo pipefail
cd "$(dirname "$0")"
HUGO=${HUGO:-hugo}
THEME_REPO=https://github.com/adityatelange/hugo-PaperMod
THEME_COMMIT=118119693d8b5ac211b939775678310973bcb232

CACHE=${EMC_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/emailcompare-site}
export HUGO_RESOURCEDIR="$CACHE/resources"
dir=$CACHE/themes/PaperMod
if ! [ -d "$dir/.git" ] || [ "$(git -C "$dir" rev-parse HEAD)" != "$THEME_COMMIT" ]; then
  echo "fetching PaperMod ($THEME_COMMIT)"
  rm -rf "$dir"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$THEME_REPO"
  git -C "$dir" fetch -q --depth 1 origin "$THEME_COMMIT"
  git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
fi

if [ "${1:-}" = "--serve" ]; then
  exec "$HUGO" server --themesDir "$CACHE/themes" --noBuildLock --disableFastRender --baseURL http://localhost:1313/
fi

"$HUGO" version
"$HUGO" --themesDir "$CACHE/themes" --noBuildLock --minify --gc --cleanDestinationDir --logLevel warn --quiet
echo "built: public/ ($(find public -name '*.html' | wc -l) pages)"
