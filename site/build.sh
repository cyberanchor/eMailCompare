#!/usr/bin/env bash
# Build the eMailCompare site (Hugo + PaperMod, terminal design) into site/public/.
#
# The theme is not stored in the repository: build.sh fetches PaperMod into site/themes/ at the pinned
# commit below. Only Hugo extended (0.146 or newer) and git are needed; no Node.js.
#
#   ./build.sh            fetch the theme and build the site
#   ./build.sh --serve    live preview at http://localhost:1313/
#
# HUGO=/path/to/hugo ./build.sh uses another Hugo binary.
set -euo pipefail
cd "$(dirname "$0")"
HUGO=${HUGO:-hugo}
THEME_REPO=https://github.com/adityatelange/hugo-PaperMod
THEME_COMMIT=118119693d8b5ac211b939775678310973bcb232

dir=themes/PaperMod
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
  exec "$HUGO" server --disableFastRender --baseURL http://localhost:1313/
fi

"$HUGO" version
"$HUGO" --minify --gc --cleanDestinationDir --logLevel warn --quiet
echo "built: public/ ($(find public -name '*.html' | wc -l) pages)"
